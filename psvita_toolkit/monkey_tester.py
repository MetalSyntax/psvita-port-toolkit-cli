"""!
@file monkey_tester.py
@brief Unattended soak-testing on the REAL PS Vita: a heartbeat over UDP so
       a hang (not just a crash dump) gets noticed during a long unattended
       run, plus generated C hooks for an optional randomized-input driver.

@details
This toolkit can't press physical buttons on a console, and it doesn't own
the game's own input-polling code (same limitation documented in
`so_patcher.py`/`mem_profiler.py` for why this project never claims to patch
or drive a binary it doesn't control). What it CAN do honestly: generate a
`monkey_test_poll_input()` helper the porter calls INSTEAD of
`sceCtrlPeekBufferPositive()` when a compile-time flag is set, substituting
randomized button presses -- and, independent of whether that's wired in at
all, a heartbeat the host listens for so a silent hang (no crash dump, the
process just froze) gets flagged instead of the toolkit having to guess from
console silence alone.

`run_soak_test()` (host side) never stops on a detected hang -- the porter
may power-cycle and restart mid-run, and honestly reporting the total
elapsed time AND every hang incident is more useful than aborting on the
first one. Pair this with `mem_profiler.py` running in parallel for the
plan's "Leak Sentinel" -- that's an existing, dedicated tool for exactly
that, not duplicated here. See `docs/dev-notes/monkey_tester.md`.
"""

import socket
import time
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "monkey_tester.menu_title": {
        "es": "Monkey Testing / Soak Test (consola real)",
        "en": "Monkey Testing / Soak Test (real console)",
        "pt": "Monkey Testing / Soak Test (console real)",
    },
    "monkey_tester.menu_listen": {
        "es": "Correr soak test (escuchar heartbeat hasta Ctrl+C)",
        "en": "Run soak test (listen for heartbeat until Ctrl+C)",
        "pt": "Rodar soak test (escutar heartbeat até Ctrl+C)",
    },
    "monkey_tester.menu_gen_hooks": {
        "es": "Generar monkey_test_hooks.c/.h (heartbeat + entrada aleatoria opcional)",
        "en": "Generate monkey_test_hooks.c/.h (heartbeat + optional random input)",
        "pt": "Gerar monkey_test_hooks.c/.h (heartbeat + entrada aleatória opcional)",
    },
    "monkey_tester.port_prompt": {
        "es": "Puerto UDP a escuchar [{default}]: ",
        "en": "UDP port to listen on [{default}]: ",
        "pt": "Porta UDP para escutar [{default}]: ",
    },
    "monkey_tester.hang_timeout_prompt": {
        "es": "Segundos sin heartbeat para considerar un hang [{default}]: ",
        "en": "Seconds without a heartbeat to flag a hang [{default}]: ",
        "pt": "Segundos sem heartbeat para considerar um hang [{default}]: ",
    },
    "monkey_tester.bind_failed": {
        "es": "[-] No se pudo escuchar en el puerto UDP {port}: {error}",
        "en": "[-] Couldn't listen on UDP port {port}: {error}",
        "pt": "[-] Não foi possível escutar na porta UDP {port}: {error}",
    },
    "monkey_tester.listening": {
        "es": "[*] Soak test corriendo -- puerto {port}, hang a los {hang_timeout}s sin heartbeat.",
        "en": "[*] Soak test running -- port {port}, hang flagged after {hang_timeout}s without a heartbeat.",
        "pt": "[*] Soak test rodando -- porta {port}, hang detectado após {hang_timeout}s sem heartbeat.",
    },
    "monkey_tester.stop_hint": {
        "es": "    Ctrl+C para detener y ver el resumen.",
        "en": "    Ctrl+C to stop and see the summary.",
        "pt": "    Ctrl+C para parar e ver o resumo.",
    },
    "monkey_tester.hang_detected": {
        "es": "[!] Posible hang/crash -- {gap:.0f}s sin heartbeat.",
        "en": "[!] Possible hang/crash -- {gap:.0f}s without a heartbeat.",
        "pt": "[!] Possível hang/crash -- {gap:.0f}s sem heartbeat.",
    },
    "monkey_tester.summary": {
        "es": "[+] Soak test terminado -- {elapsed} corrido, {incidents} incidente(s) de hang, {heartbeats} heartbeat(s).",
        "en": "[+] Soak test ended -- {elapsed} elapsed, {incidents} hang incident(s), {heartbeats} heartbeat(s).",
        "pt": "[+] Soak test encerrado -- {elapsed} decorrido, {incidents} incidente(s) de hang, {heartbeats} heartbeat(s).",
    },
    "monkey_tester.badge": {
        "es": "[+] Certificación: Tested: 0 incidentes en {elapsed} de soak test automatizado.",
        "en": "[+] Certification: Tested: 0 incidents across {elapsed} of automated soak testing.",
        "pt": "[+] Certificação: Tested: 0 incidentes em {elapsed} de soak test automatizado.",
    },
    "monkey_tester.hooks_generated": {
        "es": "[+] Hooks de monkey testing generados en {header}/{source}",
        "en": "[+] Monkey-testing hooks generated at {header}/{source}",
        "pt": "[+] Hooks de monkey testing gerados em {header}/{source}",
    },
}
i18n.register(STRINGS)

DEFAULT_PORT = 9995
DEFAULT_HANG_TIMEOUT = 30


def _format_elapsed(seconds):
    """!
    @brief Human-readable `HhMMmSSs`-ish elapsed-time formatter.
    @param seconds Elapsed seconds.
    @return e.g. `"2h 14m"` for readability in reports/badges.
    """
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _session_log_path(project_cfg):
    """!
    @brief Build this session's timestamped log path, creating `logs/` if needed.
    @param project_cfg Per-project config dict.
    @return `Path` to `<project_dir>/logs/soak_test_YYYYMMDD_HHMMSS.log`.
    """
    logs_dir = Path(project_cfg["_project_dir"]) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"soak_test_{stamp}.log"


def run_soak_test(project_cfg, port=DEFAULT_PORT, hang_timeout=DEFAULT_HANG_TIMEOUT):
    """!
    @brief Listen for `HEARTBEAT,<tick>` UDP datagrams and flag any gap
           longer than `hang_timeout` seconds, until interrupted.
    @param project_cfg Per-project config dict.
    @param port UDP port to bind to (all interfaces).
    @param hang_timeout Seconds without a heartbeat before flagging a
           possible hang/crash. Listening continues afterward -- see the
           module docstring for why this never auto-stops.
    @note At the end, if zero hang incidents were recorded, appends a
          "Tested: N hours crash-free" line to `PORTING_PLAN.md` -- the
          plan's requested certification, written only when actually true.
    """
    log_path = _session_log_path(project_cfg)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"{C.RED}{t('monkey_tester.bind_failed', port=port, error=e)}{C.RESET}")
        return

    print(t("monkey_tester.listening", port=port, hang_timeout=hang_timeout))
    print(f"{C.DIM}{t('monkey_tester.stop_hint')}{C.RESET}")

    start = time.monotonic()
    last_heartbeat = start
    heartbeats = 0
    incidents = 0
    hang_active = False

    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            while True:
                try:
                    data, addr = sock.recvfrom(65536)
                except socket.timeout:
                    gap = time.monotonic() - last_heartbeat
                    if gap > hang_timeout and not hang_active:
                        hang_active = True
                        incidents += 1
                        print(f"{C.RED}{t('monkey_tester.hang_detected', gap=gap)}{C.RESET}")
                        logf.write(f"[{time.strftime('%H:%M:%S')}] HANG gap={gap:.0f}s\n")
                        logf.flush()
                    continue
                text = data.decode("utf-8", errors="replace").strip()
                if not text.startswith("HEARTBEAT"):
                    continue
                heartbeats += 1
                last_heartbeat = time.monotonic()
                hang_active = False
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        elapsed = _format_elapsed(time.monotonic() - start)
        print(f"{C.GREEN}{t('monkey_tester.summary', elapsed=elapsed, incidents=incidents, heartbeats=heartbeats)}{C.RESET}")
        if incidents == 0 and heartbeats > 0:
            print(f"{C.GREEN}{t('monkey_tester.badge', elapsed=elapsed)}{C.RESET}")
            plan_path = Path(project_cfg["_project_dir"]) / "PORTING_PLAN.md"
            if plan_path.exists():
                with open(plan_path, "a", encoding="utf-8") as f:
                    f.write(f"\n## Soak test (psvita-toolkit)\n\n"
                            f"Tested: 0 hang/crash incidents across {elapsed} of automated soak "
                            f"testing on the real console ({heartbeats} heartbeats received).\n")


# ---------------------------------------------------------------------------
# monkey_test_hooks.c / .h generation
# ---------------------------------------------------------------------------

def _hooks_header_lines():
    """!
    @brief Shared header comment block for both generated files.
    @return list of comment lines (no trailing newline).
    """
    return [
        "/* Auto-generated by psvita-toolkit -- soak-test heartbeat + optional random input. */",
        "/* monkey_test_heartbeat() is always safe to call every frame/tick.            */",
        "/* monkey_test_poll_input() is an OPTIONAL example: call it INSTEAD of         */",
        "/* sceCtrlPeekBufferPositive() only behind your own build flag, and only for   */",
        "/* unattended soak-test builds -- never in a build you'd ship.                 */",
        "/* See docs/dev-notes/monkey_tester.md.                                        */",
    ]


def generate_monkey_hooks(project_cfg, host_ip=None, port=DEFAULT_PORT, out_dir=None):
    """!
    @brief Generate `monkey_test_hooks.c` + `.h`.
    @param project_cfg Per-project config dict.
    @param host_ip Dev machine IP to send heartbeats to; defaults to a
           placeholder the porter must edit (same reasoning as
           `mem_profiler.generate_profiler_hooks()`).
    @param port UDP port `run_soak_test()` listens on.
    @param out_dir Directory to write the two files into; defaults to
           `<project_dir>/source` if it exists, else the project root.
    """
    project_dir = Path(project_cfg["_project_dir"])
    host_ip = host_ip or "192.168.1.2"

    header_lines = _hooks_header_lines() + [
        "",
        "#pragma once",
        "#include <psp2/ctrl.h>",
        "",
        "void monkey_test_init(const char *host_ip, unsigned short port);",
        "void monkey_test_heartbeat(void);",
        "void monkey_test_poll_input(SceCtrlData *pad); /* optional, see header comment above */",
        "",
    ]

    source_lines = _hooks_header_lines() + [
        "",
        '#include "monkey_test_hooks.h"',
        "#include <psp2/kernel/processmgr.h>",
        "#include <stdlib.h>",
        "#include <string.h>",
        "#include <sys/socket.h>",
        "#include <netinet/in.h>",
        "#include <arpa/inet.h>",
        "",
        "static int s_sock = -1;",
        "static struct sockaddr_in s_dest;",
        "static unsigned int s_tick = 0;",
        "",
        "void monkey_test_init(const char *host_ip, unsigned short port) {",
        "    s_sock = socket(AF_INET, SOCK_DGRAM, 0);",
        "    if (s_sock < 0) return;",
        "    memset(&s_dest, 0, sizeof(s_dest));",
        "    s_dest.sin_family = AF_INET;",
        "    s_dest.sin_port = htons(port);",
        "    inet_pton(AF_INET, host_ip, &s_dest.sin_addr);",
        "    srand((unsigned int)sceKernelGetProcessTimeWide());",
        "}",
        "",
        "/* Sent roughly once a second at 60 ticks/s -- adjust the modulo if your loop rate differs. */",
        "void monkey_test_heartbeat(void) {",
        "    s_tick++;",
        "    if (s_sock < 0 || (s_tick % 60) != 0) return;",
        "    char line[32];",
        "    snprintf(line, sizeof(line), \"HEARTBEAT,%u\", s_tick);",
        "    sendto(s_sock, line, strlen(line), 0, (struct sockaddr *)&s_dest, sizeof(s_dest));",
        "}",
        "",
        "/* Example only: presses a random subset of buttons for a few ticks, then releases.",
        " * Adapt the button set / hold duration to what your game's menus/gameplay actually",
        " * expect -- this can't know what's \"reasonable\" input for your specific game. */",
        "void monkey_test_poll_input(SceCtrlData *pad) {",
        "    memset(pad, 0, sizeof(*pad));",
        "    static const unsigned int buttons[] = {",
        "        SCE_CTRL_UP, SCE_CTRL_DOWN, SCE_CTRL_LEFT, SCE_CTRL_RIGHT,",
        "        SCE_CTRL_CROSS, SCE_CTRL_CIRCLE, SCE_CTRL_SQUARE, SCE_CTRL_TRIANGLE,",
        "    };",
        "    if ((rand() % 100) < 60) { /* ~60% of ticks: hold one random button */",
        "        pad->buttons = buttons[rand() % (sizeof(buttons) / sizeof(buttons[0]))];",
        "    }",
        "    pad->lx = pad->ly = pad->rx = pad->ry = 128; /* sticks centered -- adjust if you want stick fuzzing too */",
        "}",
        "",
    ]

    dest = Path(out_dir) if out_dir else (project_dir / "source" if (project_dir / "source").is_dir() else project_dir)
    dest.mkdir(parents=True, exist_ok=True)
    header_path = dest / "monkey_test_hooks.h"
    source_path = dest / "monkey_test_hooks.c"
    header_path.write_text("\n".join(header_lines) + "\n", encoding="utf-8")
    source_path.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    print(t("monkey_tester.hooks_generated", header=header_path.name, source=source_path.name))


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

def monkey_tester_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: run the soak-test listener, or generate the C-side hooks.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (accepted for a uniform menu-item
           call signature; unused today).
    """
    def _listen():
        port_raw = input(f"{C.BOLD}{t('monkey_tester.port_prompt', default=DEFAULT_PORT)}{C.RESET}").strip()
        port = int(port_raw) if port_raw.isdigit() else DEFAULT_PORT
        timeout_raw = input(f"{C.BOLD}{t('monkey_tester.hang_timeout_prompt', default=DEFAULT_HANG_TIMEOUT)}{C.RESET}").strip()
        hang_timeout = int(timeout_raw) if timeout_raw.isdigit() else DEFAULT_HANG_TIMEOUT
        run_soak_test(project_cfg, port=port, hang_timeout=hang_timeout)

    tui.run_menu(
        t("monkey_tester.menu_title"),
        [
            (t("monkey_tester.menu_listen"), _listen),
            (t("monkey_tester.menu_gen_hooks"), lambda: generate_monkey_hooks(project_cfg)),
        ],
    )
