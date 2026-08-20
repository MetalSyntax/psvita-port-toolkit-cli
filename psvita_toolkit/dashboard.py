"""!
@file dashboard.py
@brief Local web dashboard for a REAL PS Vita dev session: live log stream,
       hardware connection status, crash-dump viewer, LiveArea asset
       inspector, and a browser-based Touch-to-Pad visual mapper -- all
       served from a single dependency-free local HTTP server.

@details
The plan this responds to specified FastAPI + WebSockets. This toolkit's
`requirements.txt` has stayed deliberately minimal (`Pillow` only, everything
else opt-in and lazily imported -- see `docs/dev-notes/dashboard.md`), so
this implements the same real-time-push shape -- a hand-rolled RFC 6455
WebSocket handshake/frame codec over `http.server` -- rather than adding a
dependency for it. Nothing here needs Vita3K or any emulator: every data
source is either the real console over the network (live UDP logs via
`debugnet_server`, a TCP reachability probe against `vita_ip:vita_port`) or
this project's own local files (crash dumps, LiveArea assets, generated
touch-map C code).

Four independent things, each its own tab in the single-page dashboard:
1. Live logs -- reuses `debugnet_server.run_live_log_server()` unchanged
   (via its `on_line` callback) in a background thread, fanning every line
   out to every connected browser tab over `/ws/logs`.
2. Status -- `/api/status`: TCP-connect probe against the project's
   configured `vita_ip`/`vita_port` (VitaShell's FTP port makes a fine
   reachability check without a real FTP login) plus local build/VPK count.
3. Crashes -- `/api/crashes`: lists locally downloaded dumps
   (`ftp_ops.list_local_history(kind="dumps")`) and, if `crash_analyzer` already
   wrote a `<dump>.triage_summary.md` for one, serves it as-is.
4. Assets -- `/api/assets`: `livearea.validate_livearea_dir()`'s results.
5. Touch mapper -- `/api/touch-map/*`: a canvas-based visual calibrator over
   a screenshot the porter drops at `extras/touch_map/screenshot.png`,
   exporting a reviewable `touch_bindings.c`/`.h` pair in raw front-panel
   touch-panel units (`sceTouchPeek`'s actual 1920x1088 coordinate space,
   NOT screen-pixel 960x544 -- see `_export_touch_map()`).
"""

import base64
import hashlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import debugnet_server
from . import ftp_ops
from . import i18n
from . import livearea
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "dashboard.menu_title": {
        "es": "Web Dashboard Local",
        "en": "Local Web Dashboard",
        "pt": "Web Dashboard Local",
    },
    "dashboard.menu_start": {
        "es": "Iniciar el dashboard (logs, estado, crashes, assets, mapeador táctil)",
        "en": "Start the dashboard (logs, status, crashes, assets, touch mapper)",
        "pt": "Iniciar o dashboard (logs, status, crashes, assets, mapeador de toque)",
    },
    "dashboard.port_prompt": {
        "es": "Puerto HTTP local [{default}]: ",
        "en": "Local HTTP port [{default}]: ",
        "pt": "Porta HTTP local [{default}]: ",
    },
    "dashboard.bind_failed": {
        "es": "[-] No se pudo escuchar en el puerto {port}: {error}",
        "en": "[-] Couldn't listen on port {port}: {error}",
        "pt": "[-] Não foi possível escutar na porta {port}: {error}",
    },
    "dashboard.listening": {
        "es": "[*] Dashboard corriendo en http://127.0.0.1:{port} -- abrilo en el navegador.",
        "en": "[*] Dashboard running at http://127.0.0.1:{port} -- open it in your browser.",
        "pt": "[*] Dashboard rodando em http://127.0.0.1:{port} -- abra no navegador.",
    },
    "dashboard.stop_hint": {
        "es": "    Ctrl+C para detener.",
        "en": "    Ctrl+C to stop.",
        "pt": "    Ctrl+C para parar.",
    },
    "dashboard.stopped": {
        "es": "[+] Dashboard detenido.",
        "en": "[+] Dashboard stopped.",
        "pt": "[+] Dashboard parado.",
    },
}
i18n.register(STRINGS)

DEFAULT_HTTP_PORT = 8080

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Real front-panel touch-digitizer coordinate space `sceTouchPeek`/`sceTouchRead`
# actually reports on Vita hardware -- independent of the 960x544 screen
# resolution. The touch mapper scales click coordinates (taken against
# whatever pixel size the screenshot happens to be) into this space, since
# that's what game/loader code polling the real touch panel receives.
_TOUCH_PANEL_W = 1920
_TOUCH_PANEL_H = 1088

# Discrete SCE_CTRL_* buttons offered in the mapper UI. Analog-stick/gesture
# zones intentionally have no bitmask entry -- see _export_touch_map()'s note.
_SCE_CTRL_BUTTONS = (
    "SCE_CTRL_CROSS", "SCE_CTRL_CIRCLE", "SCE_CTRL_SQUARE", "SCE_CTRL_TRIANGLE",
    "SCE_CTRL_UP", "SCE_CTRL_DOWN", "SCE_CTRL_LEFT", "SCE_CTRL_RIGHT",
    "SCE_CTRL_LTRIGGER", "SCE_CTRL_RTRIGGER", "SCE_CTRL_START", "SCE_CTRL_SELECT",
)


# ---------------------------------------------------------------------------
# Hand-rolled RFC 6455 WebSocket (handshake + framing only, no fragmentation
# support needed: this is a one-way server->client text push).
# ---------------------------------------------------------------------------

def _ws_accept_key(request_key):
    """!
    @brief Compute the `Sec-WebSocket-Accept` header value for a handshake.
    @param request_key The client's `Sec-WebSocket-Key` header value.
    @return Base64-encoded SHA-1 digest, per RFC 6455 section 4.2.2.
    """
    digest = hashlib.sha1((request_key + _WS_MAGIC).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_encode_text_frame(text):
    """!
    @brief Encode one unmasked, unfragmented text frame (server -> client
           frames are never masked, per RFC 6455 section 5.1).
    @param text Text to send.
    @return Raw frame bytes.
    """
    payload = text.encode("utf-8")
    length = len(payload)
    header = bytearray([0x81])  # FIN=1, opcode=0x1 (text)
    if length <= 125:
        header.append(length)
    elif length <= 65535:
        header.append(126)
        header += length.to_bytes(2, "big")
    else:
        header.append(127)
        header += length.to_bytes(8, "big")
    return bytes(header) + payload


def _recv_exact(sock, n):
    """!
    @brief Read exactly `n` bytes from a socket, or `None` on EOF/short read.
    @param sock Raw socket.
    @param n Number of bytes to read.
    @return `bytes` of length `n`, or `None`.
    """
    chunks = bytearray()
    while len(chunks) < n:
        chunk = sock.recv(n - len(chunks))
        if not chunk:
            return None
        chunks += chunk
    return bytes(chunks)


def _ws_read_frame(sock):
    """!
    @brief Read and decode one client->server frame (always masked).
    @param sock Raw socket.
    @return `(opcode, payload)`, or `(None, None)` on EOF/short read -- the
            caller treats either as "connection closed".
    """
    header = _recv_exact(sock, 2)
    if not header:
        return None, None
    b1, b2 = header
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        ext = _recv_exact(sock, 2)
        if ext is None:
            return None, None
        length = int.from_bytes(ext, "big")
    elif length == 127:
        ext = _recv_exact(sock, 8)
        if ext is None:
            return None, None
        length = int.from_bytes(ext, "big")
    mask_key = _recv_exact(sock, 4) if masked else b""
    if masked and mask_key is None:
        return None, None
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None, None
    if masked and payload:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


class _LogBroadcaster:
    """!
    @brief Thread-safe registry of connected `/ws/logs` sockets, fanning out
           one text frame per accepted debugnet log line to all of them.
    """

    def __init__(self):
        """!
        @brief Set up an empty, lock-protected set of connected raw sockets.
        """
        self._lock = threading.Lock()
        self._conns = set()

    def add(self, sock):
        """!
        @brief Register a newly upgraded WebSocket connection.
        @param sock Raw socket (post-handshake).
        """
        with self._lock:
            self._conns.add(sock)

    def remove(self, sock):
        """!
        @brief Drop a connection (closed, or a broadcast send to it failed).
        @param sock Raw socket to remove.
        """
        with self._lock:
            self._conns.discard(sock)

    def broadcast(self, payload_dict):
        """!
        @brief Send one JSON-encoded text frame to every connected socket,
               dropping any that fail to receive it.
        @param payload_dict JSON-serializable dict to send.
        """
        frame = _ws_encode_text_frame(json.dumps(payload_dict))
        with self._lock:
            dead = []
            for sock in self._conns:
                try:
                    sock.sendall(frame)
                except OSError:
                    dead.append(sock)
            for sock in dead:
                self._conns.discard(sock)


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

def _touch_map_dir(project_cfg):
    """!
    @brief Resolve (and create) the touch-mapper's screenshot/output directory.
    @param project_cfg Per-project config dict.
    @return `Path` to `<project_dir>/extras/touch_map/`.
    """
    d = Path(project_cfg["_project_dir"]) / "extras" / "touch_map"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _probe_status(project_cfg):
    """!
    @brief TCP-reachability probe against the project's configured Vita, plus
           local build count -- the "hardware monitor" the plan asked for,
           against the real console (no VPN/emulator involved).
    @param project_cfg Per-project config dict.
    @return dict ready to serialize as `/api/status`'s JSON body.
    """
    vita_ip = project_cfg.get("vita_ip", "")
    vita_port = project_cfg.get("vita_port", 1337)
    reachable = False
    latency_ms = None
    if vita_ip:
        start = time.monotonic()
        try:
            with socket.create_connection((vita_ip, int(vita_port)), timeout=1.5):
                reachable = True
        except OSError:
            reachable = False
        latency_ms = round((time.monotonic() - start) * 1000, 1)

    project_dir = Path(project_cfg["_project_dir"])
    vpks = ftp_ops.list_local_vpks(project_dir, project_cfg.get("build_dir", "build"))
    return {
        "game_name": project_cfg.get("game_name", ""),
        "titleid": project_cfg.get("titleid", ""),
        "vita_ip": vita_ip,
        "vita_port": vita_port,
        "reachable": reachable,
        "latency_ms": latency_ms,
        "build_count": len(vpks),
        "latest_build": vpks[0].name if vpks else None,
    }


def _list_crashes(project_cfg):
    """!
    @brief List locally downloaded crash dumps and whether each already has
           a `crash_analyzer`-written triage summary alongside it.
    @param project_cfg Per-project config dict.
    @return list of dicts ready to serialize as `/api/crashes`'s JSON body.
    """
    out = []
    for dump_path in ftp_ops.list_local_history(project_cfg, kind="dumps"):
        summary_path = dump_path.with_suffix(dump_path.suffix + ".triage_summary.md")
        summary_text = None
        if summary_path.exists():
            try:
                summary_text = summary_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                summary_text = None
        stat = dump_path.stat()
        out.append({
            "name": dump_path.name,
            "size_bytes": stat.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "has_summary": summary_text is not None,
            "summary": summary_text,
        })
    return out


def _list_assets(project_cfg):
    """!
    @brief Wrap `livearea.validate_livearea_dir()`'s per-asset checks for JSON.
    @param project_cfg Per-project config dict.
    @return list of `{name, ok, detail}` dicts.
    """
    dest_dir = Path(project_cfg["_project_dir"]) / "extras" / "livearea"
    checks = livearea.validate_livearea_dir(dest_dir)
    return [{"name": name, "ok": ok, "detail": detail} for name, ok, detail in checks]


def _export_touch_map(project_cfg, bindings, screenshot_w, screenshot_h):
    """!
    @brief Write `touch_bindings.c`/`.h`: a reviewable `touch_map_t
           touch_bindings[]` array, coordinates scaled from screenshot pixel
           space into the real front-panel touch digitizer's own
           1920x1088 unit space (`sceTouchPeek`), not screen-pixel 960x544.
    @param project_cfg Per-project config dict.
    @param bindings list of dicts from the browser:
           `{x, y, w, h, label, button, event}` in screenshot pixel space --
           `button` is one of `_SCE_CTRL_BUTTONS` or `""` (analog/gesture
           zone, no discrete button), `event` is `"down"`/`"move"`/`"up"`.
    @param screenshot_w Screenshot pixel width the click coordinates were
           taken against.
    @param screenshot_h Screenshot pixel height.
    @return Path to the written `.c` file.
    """
    project_dir = Path(project_cfg["_project_dir"])
    out_dir = project_dir / "source" if (project_dir / "source").is_dir() else project_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    scale_x = _TOUCH_PANEL_W / screenshot_w if screenshot_w else 1.0
    scale_y = _TOUCH_PANEL_H / screenshot_h if screenshot_h else 1.0
    event_enum = {"down": "TOUCH_DOWN", "move": "TOUCH_MOVE", "up": "TOUCH_UP"}

    header_lines = [
        "/* Auto-generated by psvita-toolkit (Touch-to-Pad visual mapper). */",
        "/* Rectangles are in the REAL front-panel touch digitizer's own coordinate  */",
        "/* space (sceTouchPeek/sceTouchRead: 0-1919 x 0-1087), not screen pixels --  */",
        "/* scale any UI/screen-space rect the same way before comparing.            */",
        "#pragma once",
        "",
        "typedef enum { TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP } touch_event_t;",
        "",
        "typedef struct {",
        "    int x, y, w, h;      /* touch-panel units */",
        "    const char *label;",
        "    int vita_button;     /* SCE_CTRL_* bitmask, or 0 for an analog/gesture zone */",
        "    touch_event_t event;",
        "} touch_map_t;",
        "",
        "extern const touch_map_t touch_bindings[];",
        "extern const int touch_bindings_count;",
        "",
    ]

    source_lines = [
        "/* Auto-generated by psvita-toolkit (Touch-to-Pad visual mapper). */",
        '#include "touch_bindings.h"',
        "#include <psp2/ctrl.h>",
        "",
        "const touch_map_t touch_bindings[] = {",
    ]
    for b in bindings:
        x = round(b["x"] * scale_x)
        y = round(b["y"] * scale_y)
        w = round(b["w"] * scale_x)
        h = round(b["h"] * scale_y)
        label = str(b.get("label", "")).replace('"', "'")
        button = b.get("button") or ""
        button_expr = button if button in _SCE_CTRL_BUTTONS else "0"
        event = event_enum.get(b.get("event", "down"), "TOUCH_DOWN")
        source_lines.append(f'    {{ {x}, {y}, {w}, {h}, "{label}", {button_expr}, {event} }},')
    source_lines.append("};")
    source_lines.append("")
    source_lines.append("const int touch_bindings_count = sizeof(touch_bindings) / sizeof(touch_bindings[0]);")
    source_lines.append("")

    header_path = out_dir / "touch_bindings.h"
    source_path = out_dir / "touch_bindings.c"
    header_path.write_text("\n".join(header_lines), encoding="utf-8")
    source_path.write_text("\n".join(source_lines), encoding="utf-8")
    return source_path


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

def _make_handler(project_cfg, global_cfg, broadcaster):
    """!
    @brief Build a `BaseHTTPRequestHandler` subclass closed over this
           dashboard session's `project_cfg`/`broadcaster` (the stdlib
           handler API takes a class, not an instance, so this is the usual
           closure-factory workaround).
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    @param broadcaster This session's `_LogBroadcaster`.
    @return A ready-to-serve `BaseHTTPRequestHandler` subclass.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # the terminal already shows the live log stream; skip HTTP access noise

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, body_text):
            body = body_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_ws_upgrade(self):
            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                self.send_error(400, "Missing Sec-WebSocket-Key")
                return
            accept = _ws_accept_key(key)
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            )
            self.wfile.write(response.encode("ascii"))
            sock = self.connection
            broadcaster.add(sock)
            try:
                while True:
                    opcode, _payload = _ws_read_frame(sock)
                    if opcode is None or opcode == 0x8:  # EOF or client close frame
                        break
            except OSError:
                pass
            finally:
                broadcaster.remove(sock)

        def do_GET(self):
            if self.path == "/ws/logs":
                self._handle_ws_upgrade()
            elif self.path == "/" or self.path == "/index.html":
                self._send_html(_DASHBOARD_HTML)
            elif self.path == "/api/status":
                self._send_json(_probe_status(project_cfg))
            elif self.path == "/api/crashes":
                self._send_json(_list_crashes(project_cfg))
            elif self.path == "/api/assets":
                self._send_json(_list_assets(project_cfg))
            elif self.path == "/api/touch-map/screenshot":
                shot = _touch_map_dir(project_cfg) / "screenshot.png"
                if not shot.exists():
                    self.send_error(404, "No screenshot.png at extras/touch_map/ yet")
                    return
                data = shot.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/touch-map/export":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                out_path = _export_touch_map(
                    project_cfg, body["bindings"], body["screenshot_w"], body["screenshot_h"])
                self._send_json({"ok": True, "path": str(out_path)})
            except Exception as e:  # noqa: BLE001 -- report any malformed request body, don't 500 silently
                self._send_json({"ok": False, "error": str(e)}, status=400)

    return Handler


def run_dashboard_server(project_cfg, global_cfg, host="127.0.0.1", port=DEFAULT_HTTP_PORT):
    """!
    @brief Start the dashboard's HTTP+WebSocket server and a background
           UDP debugnet listener feeding it, until interrupted.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    @param host Interface to bind to (default: loopback only).
    @param port TCP port for the HTTP server.
    """
    broadcaster = _LogBroadcaster()
    handler_cls = _make_handler(project_cfg, global_cfg, broadcaster)

    try:
        httpd = ThreadingHTTPServer((host, port), handler_cls)
    except OSError as e:
        print(f"{C.RED}{t('dashboard.bind_failed', port=port, error=e)}{C.RESET}")
        return

    def _on_log_line(timestamp, level, text):
        broadcaster.broadcast({"timestamp": timestamp, "level": level, "text": text})

    log_thread = threading.Thread(
        target=debugnet_server.run_live_log_server,
        kwargs={"project_cfg": project_cfg, "on_line": _on_log_line},
        daemon=True,
    )
    log_thread.start()

    print(t("dashboard.listening", port=port))
    print(f"{C.DIM}{t('dashboard.stop_hint')}{C.RESET}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        print(f"{C.GREEN}{t('dashboard.stopped')}{C.RESET}")


def dashboard_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: ask for the local port, then run the server.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    def _start():
        port_raw = input(f"{C.BOLD}{t('dashboard.port_prompt', default=DEFAULT_HTTP_PORT)}{C.RESET}").strip()
        port = int(port_raw) if port_raw.isdigit() else DEFAULT_HTTP_PORT
        run_dashboard_server(project_cfg, global_cfg, port=port)

    tui.run_menu(t("dashboard.menu_title"), [(t("dashboard.menu_start"), _start)])


# ---------------------------------------------------------------------------
# Single-page dashboard (vanilla HTML/CSS/JS, no CDN -- this has to work
# fully offline on a dev machine with no internet access).
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>psvita-toolkit dashboard</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#c9d1d9; --dim:#6e7681;
          --accent:#58a6ff; --green:#3fb950; --red:#f85149; --yellow:#d29922; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
  header { display:flex; gap:4px; padding:10px 14px; border-bottom:1px solid var(--border); align-items:center; }
  header h1 { font-size:14px; margin:0 16px 0 0; color:var(--accent); font-weight:600; }
  nav button { background:none; border:1px solid var(--border); color:var(--text); padding:6px 12px;
               border-radius:6px; cursor:pointer; font-family:inherit; font-size:13px; }
  nav button.active { background:var(--accent); color:#0d1117; border-color:var(--accent); }
  main { padding:16px; }
  .tab { display:none; }
  .tab.active { display:block; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:12px; }
  #log-view { height:65vh; overflow-y:auto; font-size:12px; white-space:pre-wrap; }
  .log-line { padding:1px 0; }
  .lvl-ERROR, .lvl-FATAL { color:var(--red); }
  .lvl-WARN, .lvl-WARNING { color:var(--yellow); }
  .lvl-INFO { color:var(--accent); }
  .lvl-DEBUG { color:var(--dim); }
  input[type=text] { background:#010409; border:1px solid var(--border); color:var(--text);
                      padding:6px 8px; border-radius:6px; font-family:inherit; width:280px; }
  select, button.act { background:#21262d; border:1px solid var(--border); color:var(--text);
                        padding:5px 10px; border-radius:6px; font-family:inherit; cursor:pointer; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  td, th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); }
  .ok { color:var(--green); } .fail { color:var(--red); }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
  .dot.up { background:var(--green); } .dot.down { background:var(--red); }
  pre.summary { white-space:pre-wrap; font-size:12px; background:#010409; padding:10px; border-radius:6px; max-height:300px; overflow:auto; }
  #tm-canvas-wrap { position:relative; border:1px solid var(--border); display:inline-block; }
  #tm-canvas { display:block; cursor:crosshair; max-width:100%; }
  .tm-zone-row { display:flex; gap:6px; align-items:center; margin:4px 0; font-size:12px; }
  .tm-form { display:flex; gap:8px; margin:10px 0; flex-wrap:wrap; align-items:center; }
</style>
</head>
<body>
<header>
  <h1>psvita-toolkit</h1>
  <nav>
    <button data-tab="logs" class="active">Logs</button>
    <button data-tab="status">Status</button>
    <button data-tab="crashes">Crashes</button>
    <button data-tab="assets">Assets</button>
    <button data-tab="touch">Touch Mapper</button>
  </nav>
</header>
<main>
  <section class="tab active" id="tab-logs">
    <div class="panel">
      <input type="text" id="log-filter" placeholder="filter (plain text, client-side)...">
      <span id="log-status" style="margin-left:10px;color:var(--dim);"></span>
    </div>
    <div class="panel" id="log-view"></div>
  </section>

  <section class="tab" id="tab-status">
    <div class="panel" id="status-view">loading...</div>
  </section>

  <section class="tab" id="tab-crashes">
    <div class="panel" id="crashes-view">loading...</div>
  </section>

  <section class="tab" id="tab-assets">
    <div class="panel" id="assets-view">loading...</div>
  </section>

  <section class="tab" id="tab-touch">
    <div class="panel">
      <p style="color:var(--dim);margin-top:0;">Drop a screenshot at <code>extras/touch_map/screenshot.png</code>, then
      drag rectangles over the buttons/zones you want mapped. Coordinates are exported in the real
      front-panel touch digitizer's own units (1920x1088), not screen pixels.</p>
      <div id="tm-canvas-wrap">
        <canvas id="tm-canvas" width="960" height="544"></canvas>
      </div>
      <div class="tm-form">
        <input type="text" id="tm-label" placeholder="label, e.g. Jump">
        <select id="tm-button">
          <option value="">(analog / gesture zone)</option>
          __SCE_CTRL_OPTIONS__
        </select>
        <select id="tm-event">
          <option value="down">TOUCH_DOWN</option>
          <option value="move">TOUCH_MOVE</option>
          <option value="up">TOUCH_UP</option>
        </select>
        <button class="act" id="tm-export">Export to touch_bindings.c</button>
        <span id="tm-status" style="color:var(--dim);"></span>
      </div>
      <div id="tm-zones"></div>
    </div>
  </section>
</main>

<script>
document.querySelectorAll("nav button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach(s => s.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  });
});

// ---- Logs ----
const logView = document.getElementById("log-view");
const logFilter = document.getElementById("log-filter");
const logStatus = document.getElementById("log-status");
let logLines = [];

function renderLogs() {
  const q = logFilter.value.toLowerCase();
  logView.innerHTML = logLines
    .filter(l => !q || l.text.toLowerCase().includes(q))
    .map(l => `<div class="log-line lvl-${l.level || ''}">[${l.timestamp}] ${l.text.replace(/</g, "&lt;")}</div>`)
    .join("");
  logView.scrollTop = logView.scrollHeight;
}
logFilter.addEventListener("input", renderLogs);

function connectLogs() {
  const ws = new WebSocket("ws://" + location.host + "/ws/logs");
  ws.onopen = () => { logStatus.textContent = "connected"; };
  ws.onclose = () => { logStatus.textContent = "disconnected -- retrying..."; setTimeout(connectLogs, 2000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    logLines.push(JSON.parse(ev.data));
    if (logLines.length > 2000) logLines = logLines.slice(-2000);
    renderLogs();
  };
}
connectLogs();

// ---- Status ----
async function refreshStatus() {
  const r = await fetch("/api/status");
  const s = await r.json();
  document.getElementById("status-view").innerHTML = `
    <table>
      <tr><td>Game</td><td>${s.game_name} (${s.titleid})</td></tr>
      <tr><td>Console</td><td><span class="dot ${s.reachable ? 'up' : 'down'}"></span>
          ${s.vita_ip}:${s.vita_port} -- ${s.reachable ? 'reachable' : 'unreachable'}
          ${s.latency_ms !== null ? ' (' + s.latency_ms + ' ms)' : ''}</td></tr>
      <tr><td>Builds</td><td>${s.build_count} -- latest: ${s.latest_build || '(none)'}</td></tr>
    </table>`;
}
refreshStatus();
setInterval(refreshStatus, 4000);

// ---- Crashes ----
async function refreshCrashes() {
  const r = await fetch("/api/crashes");
  const dumps = await r.json();
  const view = document.getElementById("crashes-view");
  if (!dumps.length) { view.innerHTML = "<p>No crash dumps downloaded locally yet.</p>"; return; }
  view.innerHTML = dumps.map((d, i) => `
    <div style="margin-bottom:10px;">
      <div><b>${d.name}</b> -- ${d.size_bytes} bytes -- ${d.mtime}
        ${d.has_summary ? '<button class="act" onclick="toggleSummary(' + i + ')">View triage summary</button>'
                         : '<span style="color:var(--dim);">not analyzed yet (run analyze)</span>'}</div>
      <pre class="summary" id="summary-${i}" style="display:none;">${(d.summary || '').replace(/</g, "&lt;")}</pre>
    </div>`).join("");
  window._dumps = dumps;
}
function toggleSummary(i) {
  const el = document.getElementById("summary-" + i);
  el.style.display = el.style.display === "none" ? "block" : "none";
}
refreshCrashes();

// ---- Assets ----
async function refreshAssets() {
  const r = await fetch("/api/assets");
  const checks = await r.json();
  document.getElementById("assets-view").innerHTML = `
    <table><tr><th>Asset</th><th>Status</th><th>Detail</th></tr>
    ${checks.map(c => `<tr><td>${c.name}</td><td class="${c.ok ? 'ok' : 'fail'}">${c.ok ? 'OK' : 'FAIL'}</td>
        <td>${c.detail}</td></tr>`).join("")}
    </table>`;
}
refreshAssets();

// ---- Touch mapper ----
const canvas = document.getElementById("tm-canvas");
const ctx = canvas.getContext("2d");
let shotImg = null;
let zones = [];
let dragStart = null;

function loadScreenshot() {
  const img = new Image();
  img.onload = () => { shotImg = img; canvas.width = img.width; canvas.height = img.height; drawCanvas(); };
  img.onerror = () => { drawCanvas(); };
  img.src = "/api/touch-map/screenshot?" + Date.now();
}
function drawCanvas() {
  ctx.fillStyle = "#010409";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (shotImg) ctx.drawImage(shotImg, 0, 0, canvas.width, canvas.height);
  else { ctx.fillStyle = "#6e7681"; ctx.fillText("No screenshot.png found under extras/touch_map/", 10, 20); }
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 2;
  zones.forEach(z => {
    ctx.strokeRect(z.x, z.y, z.w, z.h);
    ctx.fillStyle = "rgba(88,166,255,0.15)";
    ctx.fillRect(z.x, z.y, z.w, z.h);
    ctx.fillStyle = "#58a6ff";
    ctx.fillText(z.label || "(unlabeled)", z.x + 3, z.y + 12);
  });
}
canvas.addEventListener("mousedown", (e) => {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
  dragStart = { x: (e.clientX - rect.left) * scaleX, y: (e.clientY - rect.top) * scaleY };
});
canvas.addEventListener("mouseup", (e) => {
  if (!dragStart) return;
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width, scaleY = canvas.height / rect.height;
  const ex = (e.clientX - rect.left) * scaleX, ey = (e.clientY - rect.top) * scaleY;
  const x = Math.min(dragStart.x, ex), y = Math.min(dragStart.y, ey);
  const w = Math.abs(ex - dragStart.x), h = Math.abs(ey - dragStart.y);
  dragStart = null;
  if (w < 4 || h < 4) return;
  zones.push({
    x, y, w, h,
    label: document.getElementById("tm-label").value || ("zone" + (zones.length + 1)),
    button: document.getElementById("tm-button").value,
    event: document.getElementById("tm-event").value,
  });
  renderZones();
  drawCanvas();
});
function renderZones() {
  document.getElementById("tm-zones").innerHTML = zones.map((z, i) => `
    <div class="tm-zone-row">
      <span>${i + 1}. ${z.label} -- ${z.button || 'analog/gesture'} -- ${z.event}
        (${Math.round(z.x)},${Math.round(z.y)} ${Math.round(z.w)}x${Math.round(z.h)})</span>
      <button class="act" onclick="removeZone(${i})">remove</button>
    </div>`).join("");
}
function removeZone(i) { zones.splice(i, 1); renderZones(); drawCanvas(); }
document.getElementById("tm-export").addEventListener("click", async () => {
  const status = document.getElementById("tm-status");
  const r = await fetch("/api/touch-map/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bindings: zones, screenshot_w: canvas.width, screenshot_h: canvas.height }),
  });
  const res = await r.json();
  status.textContent = res.ok ? ("written: " + res.path) : ("error: " + res.error);
});
loadScreenshot();
drawCanvas();
</script>
</body>
</html>
"""

_DASHBOARD_HTML = _DASHBOARD_HTML.replace(
    "__SCE_CTRL_OPTIONS__",
    "\n          ".join(f'<option value="{name}">{name}</option>' for name in _SCE_CTRL_BUTTONS),
)
