"""!
@file debugnet_server.py
@brief Live log server: listens for plain-text UDP log datagrams sent by a
       "debugnet"-style remote logging library running on the PS Vita, prints
       them color-coded by severity, and saves every line to a timestamped
       session log file.

@details
No VitaShell FTP round-trip needed to see what a running game is doing --
useful for a freeze/hang that never gets far enough to flush a log file to
disk before the console needs a hard reset. See `docs/dev-notes/debugnet_server.md`
for the wire-format assumption this makes and why.
"""

import re
import socket
import time
from pathlib import Path

from . import i18n
from .i18n import t
from . import tui
from .tui import C

STRINGS = {
    "debugnet.menu_title": {
        "es": "Servidor de Logs en Vivo",
        "en": "Live Log Server",
        "pt": "Servidor de Logs em Tempo Real",
    },
    "debugnet.breadcrumb": {
        "es": "{game_name} › Logs en vivo",
        "en": "{game_name} › Live logs",
        "pt": "{game_name} › Logs em tempo real",
    },
    "debugnet.port_prompt": {
        "es": "Puerto UDP a escuchar [{default}]: ",
        "en": "UDP port to listen on [{default}]: ",
        "pt": "Porta UDP para escutar [{default}]: ",
    },
    "debugnet.filter_prompt": {
        "es": "Filtrar por regex (Enter = mostrar todo): ",
        "en": "Filter by regex (Enter = show everything): ",
        "pt": "Filtrar por regex (Enter = mostrar tudo): ",
    },
    "debugnet.invalid_filter": {
        "es": "[-] Regex inválida: {error} -- se omite el filtro.",
        "en": "[-] Invalid regex: {error} -- ignoring the filter.",
        "pt": "[-] Regex inválida: {error} -- ignorando o filtro.",
    },
    "debugnet.bind_failed": {
        "es": "[-] No se pudo escuchar en el puerto UDP {port}: {error}",
        "en": "[-] Couldn't listen on UDP port {port}: {error}",
        "pt": "[-] Não foi possível escutar na porta UDP {port}: {error}",
    },
    "debugnet.listening": {
        "es": "[*] Escuchando logs UDP en el puerto {port} -- guardando en {log_path}",
        "en": "[*] Listening for UDP logs on port {port} -- saving to {log_path}",
        "pt": "[*] Escutando logs UDP na porta {port} -- salvando em {log_path}",
    },
    "debugnet.stop_hint": {
        "es": "    Ctrl+C para detener.",
        "en": "    Ctrl+C to stop.",
        "pt": "    Ctrl+C para parar.",
    },
    "debugnet.session_ended": {
        "es": "[+] Sesión terminada -- {count} línea(s) guardada(s) en {log_path}",
        "en": "[+] Session ended -- {count} line(s) saved to {log_path}",
        "pt": "[+] Sessão encerrada -- {count} linha(s) salva(s) em {log_path}",
    },
}
i18n.register(STRINGS)

DEFAULT_PORT = 9999

# Matches a bracketed severity marker anywhere in the line, e.g. "[ERROR]",
# "[WARN]" -- the convention this plan item asked for. debugnet-style
# libraries don't enforce this themselves (it's just part of the format
# string the game/loader code passes in); lines without one aren't
# miscategorized, just shown uncolored.
_LEVEL_RE = re.compile(r'\[(FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\]', re.IGNORECASE)

_LEVEL_COLORS = {
    "FATAL": C.RED, "ERROR": C.RED,
    "WARN": C.YELLOW, "WARNING": C.YELLOW,
    "INFO": C.CYAN, "DEBUG": C.DIM,
}


def _detect_level(line):
    """!
    @brief Find a bracketed severity marker in a log line.
    @param line One decoded log line.
    @return The uppercased level name (e.g. `"ERROR"`), or `None` if none found.
    """
    m = _LEVEL_RE.search(line)
    return m.group(1).upper() if m else None


def _session_log_path(project_cfg):
    """!
    @brief Build this session's timestamped log path, creating `logs/` if needed.
    @param project_cfg Per-project config dict.
    @return `Path` to `<project_dir>/logs/live_session_YYYYMMDD_HHMMSS.log`.
    """
    logs_dir = Path(project_cfg["_project_dir"]) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return logs_dir / f"live_session_{stamp}.log"


def run_live_log_server(project_cfg, port=DEFAULT_PORT, tag_filter=None, on_line=None):
    """!
    @brief Listen for UDP log datagrams and print/save them until interrupted.
    @param project_cfg Per-project config dict.
    @param port UDP port to bind to (all interfaces).
    @param tag_filter Optional compiled regex (`re.Pattern`); only lines
           matching it are shown and saved. `None` shows/saves everything.
    @param on_line Optional `(timestamp, level, text)` callback invoked for
           every accepted line, in addition to the usual print/save --
           `dashboard.py` uses this to also push the line to any connected
           browser tab, without this module needing to know the dashboard
           exists. Exceptions from the callback are swallowed: a broken
           consumer on the other side of that boundary shouldn't take down
           the log listener itself.
    @note Wire format assumed: one UTF-8 text line per UDP datagram, no
          binary framing -- see `docs/dev-notes/debugnet_server.md` for why,
          and what happens if a specific project's logging library frames
          packets differently.
    """
    log_path = _session_log_path(project_cfg)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"{C.RED}{t('debugnet.bind_failed', port=port, error=e)}{C.RESET}")
        return

    print(t("debugnet.listening", port=port, log_path=log_path))
    print(f"{C.DIM}{t('debugnet.stop_hint')}{C.RESET}")

    count = 0
    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            while True:
                data, addr = sock.recvfrom(65536)
                text = data.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    continue
                if tag_filter and not tag_filter.search(text):
                    continue
                count += 1
                timestamp = time.strftime("%H:%M:%S")
                level = _detect_level(text)
                color = _LEVEL_COLORS.get(level, "")
                print(f"{C.DIM}[{timestamp}]{C.RESET} {color}{text}{C.RESET}")
                logf.write(f"[{timestamp}] {addr[0]}: {text}\n")
                logf.flush()
                if on_line:
                    try:
                        on_line(timestamp, level, text)
                    except Exception:  # noqa: BLE001 -- see @param on_line above
                        pass
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        print(f"\n{C.GREEN}{t('debugnet.session_ended', count=count, log_path=log_path)}{C.RESET}")


def live_log_menu(project_cfg):
    """!
    @brief TUI-facing wrapper: prompt for port/filter, then run the server.
    @param project_cfg Per-project config dict.
    """
    port_raw = input(f"{C.BOLD}{t('debugnet.port_prompt', default=DEFAULT_PORT)}{C.RESET}").strip()
    port = int(port_raw) if port_raw.isdigit() else DEFAULT_PORT

    filter_raw = input(f"{C.BOLD}{t('debugnet.filter_prompt')}{C.RESET}").strip()
    tag_filter = None
    if filter_raw:
        try:
            tag_filter = re.compile(filter_raw)
        except re.error as e:
            print(f"{C.RED}{t('debugnet.invalid_filter', error=e)}{C.RESET}")

    run_live_log_server(project_cfg, port=port, tag_filter=tag_filter)
