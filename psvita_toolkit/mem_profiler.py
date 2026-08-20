"""!
@file mem_profiler.py
@brief Live heap profiler and leak detector for a game running on the REAL
       PS Vita -- generates the C-side allocation-tracking wrappers the
       porter wires into their own loader's libc import table, and a
       host-side UDP listener (same wire technique as `debugnet_server.py`)
       that aggregates the resulting alloc/free stream into a live heap
       summary and flags allocations that outlive a "checkpoint" marker.

@details
PS Vita gives an app roughly 420 MB of usable RAM; a soloader-based port
that slowly leaks memory across level transitions or loading screens can run
fine for the first few minutes and then hard-crash from `sceKernelAllocMemBlock`
starvation an hour in -- exactly the kind of bug that's nearly impossible to
catch by staring at the game, and that Vita3K (removed from this toolkit,
see `build_deploy.py`) couldn't have caught either way, since real allocator
behavior and real memory pressure are properties of the real console.

Two independent things, mirroring the honest split `so_patcher.py` already
established between "generate reviewable source" and "this toolkit doesn't
own the target binary's linking":
1. `generate_profiler_hooks()` -- writes `mem_profiler_hooks.c`/`.h`: tracked
   `mp_malloc`/`mp_calloc`/`mp_realloc`/`mp_free` wrappers around the real
   vitasdk allocator, plus a UDP sender. This toolkit has no ELF
   loader/relocator of its own (same limitation `so_patcher.py` documents),
   so it can't hook the game's OWN calls to `malloc`/`free` automatically --
   the porter registers these wrapper functions in place of the real
   `malloc`/`free`/`calloc`/`realloc` entries in whatever import-resolution
   table their soloader already uses to satisfy the game `.so`'s libc
   imports (the same mechanism that already lets a soloader intercept JNI
   calls). See `docs/dev-notes/mem_profiler.md`.
2. `run_memory_profiler()` -- a UDP listener (default port `9998`, one
   plain-text CSV line per datagram, same "no binary framing" posture as
   `debugnet_server.py` and for the same reason: no single ground-truth wire
   format exists across soloader projects) that keeps a live table of
   outstanding allocations, prints a periodic heap summary, and reports
   "still alive after the last checkpoint" candidates as likely leaks.
"""

import socket
import time
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "mem_profiler.menu_title": {
        "es": "Profiler de Memoria en Vivo (Heap Inspector)",
        "en": "Live Memory Profiler (Heap Inspector)",
        "pt": "Profiler de Memória em Tempo Real (Heap Inspector)",
    },
    "mem_profiler.menu_listen": {
        "es": "Escuchar métricas de heap en vivo (UDP, consola real)",
        "en": "Listen for live heap metrics (UDP, real console)",
        "pt": "Escutar métricas de heap em tempo real (UDP, console real)",
    },
    "mem_profiler.menu_gen_hooks": {
        "es": "Generar mem_profiler_hooks.c/.h (wrappers de malloc/free instrumentados)",
        "en": "Generate mem_profiler_hooks.c/.h (instrumented malloc/free wrappers)",
        "pt": "Gerar mem_profiler_hooks.c/.h (wrappers instrumentados de malloc/free)",
    },
    "mem_profiler.port_prompt": {
        "es": "Puerto UDP a escuchar [{default}]: ",
        "en": "UDP port to listen on [{default}]: ",
        "pt": "Porta UDP para escutar [{default}]: ",
    },
    "mem_profiler.bind_failed": {
        "es": "[-] No se pudo escuchar en el puerto UDP {port}: {error}",
        "en": "[-] Couldn't listen on UDP port {port}: {error}",
        "pt": "[-] Não foi possível escutar na porta UDP {port}: {error}",
    },
    "mem_profiler.listening": {
        "es": "[*] Escuchando métricas de heap UDP en el puerto {port} -- guardando en {log_path}",
        "en": "[*] Listening for UDP heap metrics on port {port} -- saving to {log_path}",
        "pt": "[*] Escutando métricas de heap UDP na porta {port} -- salvando em {log_path}",
    },
    "mem_profiler.stop_hint": {
        "es": "    Ctrl+C para detener y ver el resumen final.",
        "en": "    Ctrl+C to stop and see the final summary.",
        "pt": "    Ctrl+C para parar e ver o resumo final.",
    },
    "mem_profiler.summary_header": {
        "es": "[*] {live} bloque(s) vivo(s) -- {bytes_live} bytes en uso -- pico: {bytes_peak} bytes",
        "en": "[*] {live} live block(s) -- {bytes_live} bytes in use -- peak: {bytes_peak} bytes",
        "pt": "[*] {live} bloco(s) vivo(s) -- {bytes_live} bytes em uso -- pico: {bytes_peak} bytes",
    },
    "mem_profiler.leak_header": {
        "es": "[!] {count} bloque(s) sospechoso(s) de fuga (vivos desde antes del último checkpoint '{tag}'):",
        "en": "[!] {count} block(s) suspected of leaking (alive since before the last '{tag}' checkpoint):",
        "pt": "[!] {count} bloco(s) suspeito(s) de fuga (vivo(s) desde antes do último checkpoint '{tag}'):",
    },
    "mem_profiler.session_ended": {
        "es": "[+] Sesión terminada -- {count} evento(s) guardado(s) en {log_path}",
        "en": "[+] Session ended -- {count} event(s) saved to {log_path}",
        "pt": "[+] Sessão encerrada -- {count} evento(s) salvo(s) em {log_path}",
    },
    "mem_profiler.hooks_generated": {
        "es": "[+] Hooks de memoria generados en {header}/{source} -- registralos en la tabla de imports de tu soloader.",
        "en": "[+] Memory hooks generated at {header}/{source} -- register them in your soloader's import table.",
        "pt": "[+] Hooks de memória gerados em {header}/{source} -- registre-os na tabela de imports do seu soloader.",
    },
}
i18n.register(STRINGS)

DEFAULT_PORT = 9998

# Wire format: "<event>,<ptr>,<size>,<tag>" per UDP datagram --
#   ALLOC,0x1a2b3c4,128,levelload
#   FREE,0x1a2b3c4,0,
#   CHECKPOINT,0,0,level2_start
# `size`/`tag` are unused (0/"") for FREE and CHECKPOINT respectively; kept
# as fixed 4 fields rather than a variable-length line so a malformed/partial
# datagram is easy to detect and skip instead of silently misparsing.
_FIELDS = 4


def _parse_event(text):
    """!
    @brief Parse one wire-format line into its fields.
    @param text Decoded UDP datagram text (already stripped).
    @return `(event, ptr, size, tag)` with `ptr`/`size` as `int`, or `None`
            if the line doesn't have exactly 4 comma-separated fields or
            `ptr`/`size` aren't valid integers (accepts `0x...` and decimal).
    """
    parts = text.split(",", _FIELDS - 1)
    if len(parts) != _FIELDS:
        return None
    event, ptr_s, size_s, tag = parts
    try:
        ptr = int(ptr_s, 0)
        size = int(size_s, 0)
    except ValueError:
        return None
    return event.strip().upper(), ptr, size, tag.strip()


def _session_log_path(project_cfg):
    """!
    @brief Build this session's timestamped log path, creating `logs/` if needed.
    @param project_cfg Per-project config dict.
    @return `Path` to `<project_dir>/logs/mem_profile_YYYYMMDD_HHMMSS.log`.
    """
    logs_dir = Path(project_cfg["_project_dir"]) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"mem_profile_{stamp}.log"


def run_memory_profiler(project_cfg, port=DEFAULT_PORT, summary_every=50, stop_event=None):
    """!
    @brief Listen for UDP alloc/free/checkpoint events and maintain a live
           heap summary until interrupted.
    @param project_cfg Per-project config dict.
    @param port UDP port to bind to (all interfaces).
    @param summary_every Print a heap summary line every this many processed
           events (in addition to the final one on exit).
    @param stop_event Optional `threading.Event` -- when set, the loop exits
           cleanly (same as Ctrl+C) on its next ~1s poll. Lets
           `monkey_tester.run_combined_soak_session()` run this on a
           background thread and still shut it down from the main thread
           (background threads never receive `KeyboardInterrupt` directly).
           `None` (default) preserves the original Ctrl+C-only behavior.
    @note Leak heuristic: every live allocation remembers the tag of the most
          recent `CHECKPOINT` event that had already happened when it was
          allocated. On exit (or when asked), anything still live whose
          remembered checkpoint is OLDER than the current one is reported --
          i.e. "this outlived at least one level/scene transition without
          being freed". A game that never sends `CHECKPOINT` events still
          gets accurate live-byte/live-block counts, just no leak-vs-just-
          -still-in-use distinction -- see `docs/dev-notes/mem_profiler.md`.
    """
    log_path = _session_log_path(project_cfg)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"{C.RED}{t('mem_profiler.bind_failed', port=port, error=e)}{C.RESET}")
        return

    print(t("mem_profiler.listening", port=port, log_path=log_path))
    print(f"{C.DIM}{t('mem_profiler.stop_hint')}{C.RESET}")

    live = {}          # ptr -> (size, checkpoint_tag_at_alloc_time)
    current_checkpoint = ""
    bytes_live = 0
    bytes_peak = 0
    count = 0

    def _print_summary():
        print(f"{C.CYAN}{t('mem_profiler.summary_header', live=len(live), bytes_live=bytes_live, bytes_peak=bytes_peak)}{C.RESET}")

    def _print_leak_report():
        stale = [(ptr, size, tag) for ptr, (size, tag) in live.items() if tag != current_checkpoint]
        if not stale:
            return
        print(f"{C.YELLOW}{t('mem_profiler.leak_header', count=len(stale), tag=current_checkpoint)}{C.RESET}")
        for ptr, size, tag in sorted(stale, key=lambda x: -x[1])[:15]:
            print(f"    0x{ptr:x}  {size} bytes  (allocated during '{tag}')")
        if len(stale) > 15:
            print(f"    ... {len(stale) - 15} more")

    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            while not (stop_event and stop_event.is_set()):
                try:
                    data, addr = sock.recvfrom(65536)
                except socket.timeout:
                    continue
                text = data.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                parsed = _parse_event(text)
                if not parsed:
                    continue
                event, ptr, size, tag = parsed
                count += 1
                timestamp = time.strftime("%H:%M:%S")
                logf.write(f"[{timestamp}] {addr[0]}: {text}\n")
                logf.flush()

                if event == "ALLOC":
                    live[ptr] = (size, current_checkpoint)
                    bytes_live += size
                    bytes_peak = max(bytes_peak, bytes_live)
                elif event == "FREE":
                    entry = live.pop(ptr, None)
                    if entry:
                        bytes_live -= entry[0]
                elif event == "CHECKPOINT":
                    current_checkpoint = tag

                if count % summary_every == 0:
                    _print_summary()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        _print_summary()
        _print_leak_report()
        print(f"{C.GREEN}{t('mem_profiler.session_ended', count=count, log_path=log_path)}{C.RESET}")


# ---------------------------------------------------------------------------
# mem_profiler_hooks.c / .h generation
# ---------------------------------------------------------------------------

def _hooks_header_lines():
    """!
    @brief Shared header comment block for both generated files.
    @return list of comment lines (no trailing newline).
    """
    return [
        "/* Auto-generated by psvita-toolkit -- instrumented heap allocator wrappers. */",
        "/* This toolkit has no ELF loader/relocator of its own, so it can't hook the  */",
        "/* game .so's malloc/free calls automatically (same limitation documented in  */",
        "/* so_patcher.py). Register mp_malloc/mp_calloc/mp_realloc/mp_free below in    */",
        "/* place of the real malloc/calloc/realloc/free entries in your soloader's own */",
        "/* libc import-resolution table. See docs/dev-notes/mem_profiler.md.           */",
    ]


def generate_profiler_hooks(project_cfg, host_ip=None, port=DEFAULT_PORT, out_dir=None):
    """!
    @brief Generate `mem_profiler_hooks.c` + `.h`: tracked malloc/calloc/
           realloc/free wrappers around the real vitasdk allocator, streaming
           one UDP event per call to the host running `run_memory_profiler()`.
    @param project_cfg Per-project config dict.
    @param host_ip Dev machine IP the Vita should send metrics to; defaults
           to `project_cfg["vita_ip"]`'s subnet guess is NOT attempted --
           this is the reverse direction of every other network config in
           this toolkit, so it's asked for explicitly (falls back to
           `"192.168.1.2"` as an obviously-placeholder value the porter must
           edit if not passed).
    @param port UDP port `run_memory_profiler()` listens on.
    @param out_dir Directory to write the two files into; defaults to
           `<project_dir>/source` if it exists, else the project root (same
           convention as `jni_analyzer.generate_jni_stubs()`).
    """
    project_dir = Path(project_cfg["_project_dir"])
    host_ip = host_ip or "192.168.1.2"

    header_lines = _hooks_header_lines() + [
        "",
        "#pragma once",
        "#include <stddef.h>",
        "",
        "void mem_profiler_init(const char *host_ip, unsigned short port);",
        "void mem_profiler_checkpoint(const char *tag);",
        "void *mp_malloc(size_t size);",
        "void *mp_calloc(size_t count, size_t size);",
        "void *mp_realloc(void *ptr, size_t size);",
        "void mp_free(void *ptr);",
        "",
    ]

    source_lines = _hooks_header_lines() + [
        "",
        '#include "mem_profiler_hooks.h"',
        "#include <stdio.h>",
        "#include <stdlib.h>",
        "#include <string.h>",
        "#include <sys/socket.h>",
        "#include <netinet/in.h>",
        "#include <arpa/inet.h>",
        "",
        "static int s_sock = -1;",
        "static struct sockaddr_in s_dest;",
        "",
        "void mem_profiler_init(const char *host_ip, unsigned short port) {",
        "    s_sock = socket(AF_INET, SOCK_DGRAM, 0);",
        "    if (s_sock < 0) return;",
        "    memset(&s_dest, 0, sizeof(s_dest));",
        "    s_dest.sin_family = AF_INET;",
        "    s_dest.sin_port = htons(port);",
        "    inet_pton(AF_INET, host_ip, &s_dest.sin_addr);",
        "}",
        "",
        "static void mp_send(const char *event, void *ptr, size_t size, const char *tag) {",
        "    if (s_sock < 0) return; /* mem_profiler_init() not called -- wrappers still work, just silent */",
        "    char line[160];",
        "    snprintf(line, sizeof(line), \"%s,%p,%zu,%s\", event, ptr, size, tag ? tag : \"\");",
        "    sendto(s_sock, line, strlen(line), 0, (struct sockaddr *)&s_dest, sizeof(s_dest));",
        "}",
        "",
        "void mem_profiler_checkpoint(const char *tag) {",
        "    mp_send(\"CHECKPOINT\", NULL, 0, tag);",
        "}",
        "",
        "void *mp_malloc(size_t size) {",
        "    void *p = malloc(size);",
        "    if (p) mp_send(\"ALLOC\", p, size, NULL);",
        "    return p;",
        "}",
        "",
        "void *mp_calloc(size_t count, size_t size) {",
        "    void *p = calloc(count, size);",
        "    if (p) mp_send(\"ALLOC\", p, count * size, NULL);",
        "    return p;",
        "}",
        "",
        "void *mp_realloc(void *ptr, size_t size) {",
        "    if (ptr) mp_send(\"FREE\", ptr, 0, NULL);",
        "    void *p = realloc(ptr, size);",
        "    if (p) mp_send(\"ALLOC\", p, size, NULL);",
        "    return p;",
        "}",
        "",
        "void mp_free(void *ptr) {",
        "    if (ptr) mp_send(\"FREE\", ptr, 0, NULL);",
        "    free(ptr);",
        "}",
        "",
    ]

    dest = Path(out_dir) if out_dir else (project_dir / "source" if (project_dir / "source").is_dir() else project_dir)
    dest.mkdir(parents=True, exist_ok=True)
    header_path = dest / "mem_profiler_hooks.h"
    source_path = dest / "mem_profiler_hooks.c"
    header_path.write_text("\n".join(header_lines) + "\n", encoding="utf-8")
    source_path.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    print(t("mem_profiler.hooks_generated", header=header_path.name, source=source_path.name))


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

def profiler_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: listen for live metrics, or generate the C-side hooks.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (accepted for a uniform menu-item
           call signature; unused today).
    """
    def _listen():
        port_raw = input(f"{C.BOLD}{t('mem_profiler.port_prompt', default=DEFAULT_PORT)}{C.RESET}").strip()
        port = int(port_raw) if port_raw.isdigit() else DEFAULT_PORT
        run_memory_profiler(project_cfg, port=port)

    tui.run_menu(
        t("mem_profiler.menu_title"),
        [
            (t("mem_profiler.menu_listen"), _listen),
            (t("mem_profiler.menu_gen_hooks"), lambda: generate_profiler_hooks(project_cfg)),
        ],
    )
