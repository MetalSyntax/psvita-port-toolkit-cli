"""!
@file perf_telemetry.py
@brief Live frame-pacing (and best-effort per-core occupancy) telemetry from
       a game running on the REAL PS Vita, streamed over UDP to the host and
       from there to the dashboard's Performance tab.

@details
The plan item this responds to also asked for GPU (PowerVR SGX543MP4+)
counters -- vertex counts, rasterization rate. Those aren't exposed to
homebrew through any public vitasdk API this toolkit could find, so this
does NOT fabricate a GPU metric; claiming one without a real counter behind
it would be exactly the kind of dishonest "drop-in" this project's other
modules (`so_patcher.py`, `mem_profiler.py`) deliberately avoid. What IS
real and, for "get to a stable 60 FPS" purposes, arguably more directly
useful anyway: **frame time** itself. A GPU-bound frame shows up as a long
frame time regardless of whether there's a dedicated GPU counter behind it,
so frame-pacing analysis (jitter, dropped-frame detection) stands on its own
as a genuinely actionable metric.

Two independent signals, same UDP-line wire convention as
`debugnet_server.py`/`mem_profiler.py`:
1. `FRAME,<microseconds>` -- one per frame, timed with `sceKernelGetProcessTimeWide()`
   around the porter's own render+present call (`generate_perf_hooks()`'s
   `perf_telemetry_frame_begin()`/`_end()` pair). Always reliable -- this is
   just a wall-clock delta.
2. `CORES,<t0>,<t1>,<t2>,<t3>` -- which thread ID is currently scheduled on
   each of the Cortex-A9's 4 cores, sampled via `sceKernelGetThreadRunStatus()`.
   This is a coarse, best-effort SAMPLE (like a classic sampling profiler),
   not an exact per-core load percentage -- see
   `docs/dev-notes/perf_telemetry.md` for why, and why the generated hook
   for it is explicitly marked "verify against your vitasdk headers" rather
   than asserted correct.
"""

import socket
import time
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "perf_telemetry.menu_title": {
        "es": "Telemetría de Rendimiento (Frame-Pacing / CPU)",
        "en": "Performance Telemetry (Frame-Pacing / CPU)",
        "pt": "Telemetria de Desempenho (Frame-Pacing / CPU)",
    },
    "perf_telemetry.menu_listen": {
        "es": "Escuchar telemetría en vivo (UDP, consola real)",
        "en": "Listen for live telemetry (UDP, real console)",
        "pt": "Escutar telemetria em tempo real (UDP, console real)",
    },
    "perf_telemetry.menu_gen_hooks": {
        "es": "Generar perf_telemetry_hooks.c/.h",
        "en": "Generate perf_telemetry_hooks.c/.h",
        "pt": "Gerar perf_telemetry_hooks.c/.h",
    },
    "perf_telemetry.port_prompt": {
        "es": "Puerto UDP a escuchar [{default}]: ",
        "en": "UDP port to listen on [{default}]: ",
        "pt": "Porta UDP para escutar [{default}]: ",
    },
    "perf_telemetry.bind_failed": {
        "es": "[-] No se pudo escuchar en el puerto UDP {port}: {error}",
        "en": "[-] Couldn't listen on UDP port {port}: {error}",
        "pt": "[-] Não foi possível escutar na porta UDP {port}: {error}",
    },
    "perf_telemetry.listening": {
        "es": "[*] Escuchando telemetría UDP en el puerto {port} -- guardando en {log_path}",
        "en": "[*] Listening for UDP telemetry on port {port} -- saving to {log_path}",
        "pt": "[*] Escutando telemetria UDP na porta {port} -- salvando em {log_path}",
    },
    "perf_telemetry.stop_hint": {
        "es": "    Ctrl+C para detener.",
        "en": "    Ctrl+C to stop.",
        "pt": "    Ctrl+C para parar.",
    },
    "perf_telemetry.summary": {
        "es": "[*] {fps:.1f} FPS (avg) -- frame p95: {p95:.1f} ms -- stutters (>2x avg): {stutters}",
        "en": "[*] {fps:.1f} FPS (avg) -- frame p95: {p95:.1f} ms -- stutters (>2x avg): {stutters}",
        "pt": "[*] {fps:.1f} FPS (média) -- frame p95: {p95:.1f} ms -- engasgos (>2x média): {stutters}",
    },
    "perf_telemetry.session_ended": {
        "es": "[+] Sesión terminada -- {count} muestra(s) guardada(s) en {log_path}",
        "en": "[+] Session ended -- {count} sample(s) saved to {log_path}",
        "pt": "[+] Sessão encerrada -- {count} amostra(s) salva(s) em {log_path}",
    },
    "perf_telemetry.hooks_generated": {
        "es": "[+] Hooks de telemetría generados en {header}/{source}",
        "en": "[+] Telemetry hooks generated at {header}/{source}",
        "pt": "[+] Hooks de telemetria gerados em {header}/{source}",
    },
}
i18n.register(STRINGS)

DEFAULT_PORT = 9996


def _parse_sample(text):
    """!
    @brief Parse one wire-format line.
    @param text Decoded UDP datagram text (already stripped).
    @return `("FRAME", frame_time_us)`, `("CORES", (t0, t1, t2, t3))`, or
            `None` if the line doesn't match either shape.
    """
    parts = text.split(",")
    if not parts:
        return None
    kind = parts[0].strip().upper()
    try:
        if kind == "FRAME" and len(parts) == 2:
            return kind, int(parts[1])
        if kind == "CORES" and len(parts) == 5:
            return kind, tuple(int(p) for p in parts[1:])
    except ValueError:
        return None
    return None


def _session_log_path(project_cfg):
    """!
    @brief Build this session's timestamped log path, creating `logs/` if needed.
    @param project_cfg Per-project config dict.
    @return `Path` to `<project_dir>/logs/perf_telemetry_YYYYMMDD_HHMMSS.log`.
    """
    logs_dir = Path(project_cfg["_project_dir"]) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"perf_telemetry_{stamp}.log"


def run_perf_telemetry(project_cfg, port=DEFAULT_PORT, summary_every=120, on_sample=None):
    """!
    @brief Listen for UDP frame/core-occupancy samples and print a rolling
           frame-pacing summary until interrupted.
    @param project_cfg Per-project config dict.
    @param port UDP port to bind to (all interfaces).
    @param summary_every Print a summary line every this many `FRAME` samples.
    @param on_sample Optional `(kind, value)` callback invoked for every
           accepted sample (`kind` is `"FRAME"`/`"CORES"`) -- `dashboard.py`
           uses this to fan samples out to the Performance tab. Exceptions
           are swallowed, same boundary reasoning as `debugnet_server.py`'s
           `on_line`.
    """
    log_path = _session_log_path(project_cfg)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"{C.RED}{t('perf_telemetry.bind_failed', port=port, error=e)}{C.RESET}")
        return

    print(t("perf_telemetry.listening", port=port, log_path=log_path))
    print(f"{C.DIM}{t('perf_telemetry.stop_hint')}{C.RESET}")

    frame_times_us = []
    count = 0

    def _print_summary():
        if not frame_times_us:
            return
        window = frame_times_us[-600:]
        avg_us = sum(window) / len(window)
        fps = 1_000_000 / avg_us if avg_us else 0.0
        p95_ms = sorted(window)[int(len(window) * 0.95)] / 1000
        stutters = sum(1 for v in window if v > avg_us * 2)
        print(f"{C.CYAN}{t('perf_telemetry.summary', fps=fps, p95=p95_ms, stutters=stutters)}{C.RESET}")

    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            while True:
                data, addr = sock.recvfrom(65536)
                text = data.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                parsed = _parse_sample(text)
                if not parsed:
                    continue
                kind, value = parsed
                count += 1
                timestamp = time.strftime("%H:%M:%S")
                logf.write(f"[{timestamp}] {addr[0]}: {text}\n")
                logf.flush()

                if kind == "FRAME":
                    frame_times_us.append(value)
                    if count % summary_every == 0:
                        _print_summary()

                if on_sample:
                    try:
                        on_sample(kind, value)
                    except Exception:  # noqa: BLE001 -- see @param on_sample above
                        pass
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        _print_summary()
        print(f"{C.GREEN}{t('perf_telemetry.session_ended', count=count, log_path=log_path)}{C.RESET}")


# ---------------------------------------------------------------------------
# perf_telemetry_hooks.c / .h generation
# ---------------------------------------------------------------------------

def _hooks_header_lines():
    """!
    @brief Shared header comment block for both generated files.
    @return list of comment lines (no trailing newline).
    """
    return [
        "/* Auto-generated by psvita-toolkit -- frame-pacing + coarse core telemetry. */",
        "/* perf_telemetry_frame_begin()/_end() are reliable wall-clock timing (call    */",
        "/* them around your own render+present code). perf_telemetry_sample_cores() is */",
        "/* a BEST-EFFORT sampler using sceKernelGetThreadRunStatus() -- verify its      */",
        "/* struct fields against YOUR vitasdk headers version before relying on it; if  */",
        "/* it doesn't compile as-is, delete it -- frame timing works independently.     */",
        "/* See docs/dev-notes/perf_telemetry.md.                                        */",
    ]


def generate_perf_hooks(project_cfg, host_ip=None, port=DEFAULT_PORT, out_dir=None):
    """!
    @brief Generate `perf_telemetry_hooks.c` + `.h`: frame-time timing helpers
           and a best-effort per-core occupancy sampler, streaming UDP events
           to the host running `run_perf_telemetry()`.
    @param project_cfg Per-project config dict.
    @param host_ip Dev machine IP the Vita should send telemetry to;
           defaults to a placeholder the porter must edit (same reasoning
           as `mem_profiler.generate_profiler_hooks()`).
    @param port UDP port `run_perf_telemetry()` listens on.
    @param out_dir Directory to write the two files into; defaults to
           `<project_dir>/source` if it exists, else the project root.
    """
    project_dir = Path(project_cfg["_project_dir"])
    host_ip = host_ip or "192.168.1.2"

    header_lines = _hooks_header_lines() + [
        "",
        "#pragma once",
        "",
        "void perf_telemetry_init(const char *host_ip, unsigned short port);",
        "void perf_telemetry_frame_begin(void);",
        "void perf_telemetry_frame_end(void);",
        "void perf_telemetry_sample_cores(void); /* best-effort, see header comment above */",
        "",
    ]

    source_lines = _hooks_header_lines() + [
        "",
        '#include "perf_telemetry_hooks.h"',
        "#include <psp2/kernel/processmgr.h>",
        "#include <psp2/kernel/threadmgr.h>",
        "#include <stdio.h>",
        "#include <string.h>",
        "#include <sys/socket.h>",
        "#include <netinet/in.h>",
        "#include <arpa/inet.h>",
        "",
        "static int s_sock = -1;",
        "static struct sockaddr_in s_dest;",
        "static SceKernelSysClock s_frame_start;",
        "",
        "void perf_telemetry_init(const char *host_ip, unsigned short port) {",
        "    s_sock = socket(AF_INET, SOCK_DGRAM, 0);",
        "    if (s_sock < 0) return;",
        "    memset(&s_dest, 0, sizeof(s_dest));",
        "    s_dest.sin_family = AF_INET;",
        "    s_dest.sin_port = htons(port);",
        "    inet_pton(AF_INET, host_ip, &s_dest.sin_addr);",
        "}",
        "",
        "static void pt_send(const char *line) {",
        "    if (s_sock < 0) return; /* perf_telemetry_init() not called -- helpers still work, just silent */",
        "    sendto(s_sock, line, strlen(line), 0, (struct sockaddr *)&s_dest, sizeof(s_dest));",
        "}",
        "",
        "void perf_telemetry_frame_begin(void) {",
        "    sceKernelGetProcessTimeWide(&s_frame_start);",
        "}",
        "",
        "void perf_telemetry_frame_end(void) {",
        "    SceKernelSysClock now;",
        "    sceKernelGetProcessTimeWide(&now);",
        "    char line[64];",
        "    snprintf(line, sizeof(line), \"FRAME,%llu\", (unsigned long long)(now - s_frame_start));",
        "    pt_send(line);",
        "}",
        "",
        "/* Best-effort: which thread ID is currently running on each of the 4 cores,",
        " * per sceKernelGetThreadRunStatus(). Field names below match the vitasdk",
        " * headers as of this writing -- confirm SceKernelThreadRunStatus's layout in",
        " * <psp2/kernel/threadmgr.h> for your SDK version before trusting this blindly. */",
        "void perf_telemetry_sample_cores(void) {",
        "    SceKernelThreadRunStatus status;",
        "    status.size = sizeof(status);",
        "    if (sceKernelGetThreadRunStatus(&status) < 0) return;",
        "    char line[64];",
        "    snprintf(line, sizeof(line), \"CORES,%d,%d,%d,%d\",",
        "             status.cpuInfo[0].currentThreadId, status.cpuInfo[1].currentThreadId,",
        "             status.cpuInfo[2].currentThreadId, status.cpuInfo[3].currentThreadId);",
        "    pt_send(line);",
        "}",
        "",
    ]

    dest = Path(out_dir) if out_dir else (project_dir / "source" if (project_dir / "source").is_dir() else project_dir)
    dest.mkdir(parents=True, exist_ok=True)
    header_path = dest / "perf_telemetry_hooks.h"
    source_path = dest / "perf_telemetry_hooks.c"
    header_path.write_text("\n".join(header_lines) + "\n", encoding="utf-8")
    source_path.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    print(t("perf_telemetry.hooks_generated", header=header_path.name, source=source_path.name))


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

def perf_telemetry_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: listen for live telemetry, or generate the C-side hooks.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (accepted for a uniform menu-item
           call signature; unused today).
    """
    def _listen():
        port_raw = input(f"{C.BOLD}{t('perf_telemetry.port_prompt', default=DEFAULT_PORT)}{C.RESET}").strip()
        port = int(port_raw) if port_raw.isdigit() else DEFAULT_PORT
        run_perf_telemetry(project_cfg, port=port)

    tui.run_menu(
        t("perf_telemetry.menu_title"),
        [
            (t("perf_telemetry.menu_listen"), _listen),
            (t("perf_telemetry.menu_gen_hooks"), lambda: generate_perf_hooks(project_cfg)),
        ],
    )
