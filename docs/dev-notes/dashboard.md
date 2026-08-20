# `dashboard.py` — Developer Notes

## Why this is a hand-rolled WebSocket over `http.server`, not FastAPI

`requirements.txt` has stayed deliberately minimal for this whole project (`Pillow` only,
everything else -- `deep-translator`, `vita-parse-core` -- opt-in and lazily imported, so
`doctor.py` can report exactly what's missing instead of a hard import crash at startup). Adding
FastAPI + Starlette + `websockets` + Uvicorn as a hard dependency for one optional feature breaks
that posture. RFC 6455's handshake (a SHA-1 + base64 `Sec-WebSocket-Accept`) and unfragmented
text framing are small enough (`_ws_accept_key()`/`_ws_encode_text_frame()`/`_ws_read_frame()`,
well under 100 lines total) that hand-rolling them keeps the zero-new-dependency guarantee intact
while still getting genuine server-push instead of a polling loop for the one thing that
actually benefits from it (the live log tail).

## Why the WebSocket-upgraded connection blocks inside `do_GET` instead of returning

`http.server`'s per-connection loop expects to read another HTTP request line after a handler
returns (HTTP/1.1 keep-alive). Once a connection has been upgraded to WebSocket framing, there
IS no next HTTP request coming down that socket -- so `_handle_ws_upgrade()` deliberately never
returns until the client disconnects (EOF) or sends a close frame, keeping the request thread
(`ThreadingHTTPServer` gives each connection its own) alive for the WebSocket's whole lifetime
instead of racing the base class's request-parsing loop against raw WS frames.

## Why the touch mapper exports in `sceTouchPeek`'s 1920x1088 space, not screen-pixel 960x544

The plan's touch-to-pad item asked for "960x544 or the game's native coordinates". Neither of
those is what real touch-panel-reading code on Vita hardware actually receives: `sceTouchPeek`/
`sceTouchRead` report front-panel digitizer coordinates in their own fixed 1920x1088 unit space,
independent of the 960x544 screen resolution. Exporting screen-pixel rectangles would silently
require every porter to also duplicate this scaling themselves, or (more likely) get bug reports
about the mapped zones being in the wrong place. `_export_touch_map()` does the scaling once,
from whatever pixel size the screenshot happens to be, so the generated `touch_bindings[]` array
is comparable directly against a real `sceTouchPeek()` result.

## Why analog-stick/gesture zones get no `SCE_CTRL_*` bitmask

The plan also asked for mapping touch gestures to right-stick-style camera control. Vita's
analog sticks aren't discrete button bits (`SCE_CTRL_*` is a bitmask of DIGITAL buttons), and
there is no generic, statically-knowable way to turn "the user swiped this rectangle" into a
specific game's camera-control call without reading that game's own input code. Emitting a zone
with `vita_button = 0` and a label is an honest hand-off -- the porter writes the actual
swipe-to-camera glue themselves, same "checklist not a drop-in" posture as
`so_patcher.generate_telemetry_stubs()`.

## Why crash-dump analysis isn't re-run from the web UI

`/api/crashes` reads an already-written `<dump>.triage_summary.md` if one exists (same file
`context_feeder.py` already parses) rather than invoking `crash_analyzer.analyze()` itself. That
function needs an ELF/`.so` path resolved against THIS project's specific layout and can take a
while over a large dump; re-running it as a side effect of loading a browser tab would be a
surprising, slow default. Analyzing a fresh dump stays a deliberate action in the TUI/CLI
(`psvita-toolkit analyze`); the dashboard is a viewer for what's already there.

## Why the Performance tab reuses `_LogBroadcaster` for a second, unrelated sample shape

`/ws/perf` needed the exact same "fan one JSON dict out to every connected socket" behavior
`/ws/logs` already had -- the class was already generic over the payload shape (it just calls
`json.dumps()` on whatever dict it's given), so giving it a second instance (`perf_broadcaster`)
for `perf_telemetry.py`'s FRAME/CORES samples was the whole change; no new broadcasting class was
needed. See `perf_telemetry.py`'s own dev-notes for why there's no GPU counter behind the graph.
