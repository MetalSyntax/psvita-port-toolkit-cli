"""!
@file automation_mac.py
@brief Vita3K UI automation on macOS: synthetic mouse clicks and key presses
       via Quartz, plus AppleScript helpers to bring Vita3K to front and
       double-click the first game row in its library.

@details
Vita3K's UI is Qt-based and does NOT respond to accessibility-level synthetic
clicks (`osascript`/AppleScript) -- real OS-level mouse/keyboard events must
be injected via Quartz instead. Requires `pip install pyobjc` (for the
`Quartz` module). macOS only.

See `docs/dev-notes/automation_mac.md` for why this module exists at all
(ported from `porting_tools/automation/*.py`) and the empirical-offset
caveat on `double_click_first_game_row()`.
"""

import subprocess
import time

from . import i18n
from .i18n import t
from .tui import C

try:
    import Quartz
    HAVE_QUARTZ = True
except ImportError:
    HAVE_QUARTZ = False

STRINGS = {
    "automation_mac.missing_pyobjc": {
        "es": "[-] Falta pyobjc -- instalar con: pip install pyobjc",
        "en": "[-] Missing pyobjc -- install with: pip install pyobjc",
        "pt": "[-] Falta o pyobjc -- instale com: pip install pyobjc",
    },
    "automation_mac.unknown_key": {
        "es": "[-] Tecla desconocida: '{keyname}' (ver automation_mac.KEYCODES)",
        "en": "[-] Unknown key: '{keyname}' (see automation_mac.KEYCODES)",
        "pt": "[-] Tecla desconhecida: '{keyname}' (ver automation_mac.KEYCODES)",
    },
    "automation_mac.game_row_not_found": {
        "es": "[!] No se pudo ubicar la fila del juego en la ventana de Vita3K (¿está abierta y con foco?).",
        "en": "[!] Could not locate the game row in the Vita3K window (is it open and focused?).",
        "pt": "[!] Não foi possível localizar a linha do jogo na janela do Vita3K (ela está aberta e em foco?).",
    },
}
i18n.register(STRINGS)

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
    "return": 36, "tab": 48, "space": 49, "escape": 53,
}


def _require_quartz():
    if not HAVE_QUARTZ:
        print(f"{C.RED}{t('automation_mac.missing_pyobjc')}{C.RESET}")
        return False
    return True


def click(x, y, click_count=1, button="left"):
    if not _require_quartz():
        return
    down_type = Quartz.kCGEventLeftMouseDown if button == "left" else Quartz.kCGEventRightMouseDown
    up_type = Quartz.kCGEventLeftMouseUp if button == "left" else Quartz.kCGEventRightMouseUp
    mouse_button = Quartz.kCGMouseButtonLeft if button == "left" else Quartz.kCGMouseButtonRight

    move = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (x, y), mouse_button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
    time.sleep(0.05)

    for i in range(click_count):
        down = Quartz.CGEventCreateMouseEvent(None, down_type, (x, y), mouse_button)
        Quartz.CGEventSetIntegerValueField(down, Quartz.kCGMouseEventClickState, i + 1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.03)
        up = Quartz.CGEventCreateMouseEvent(None, up_type, (x, y), mouse_button)
        Quartz.CGEventSetIntegerValueField(up, Quartz.kCGMouseEventClickState, i + 1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.08)


def mouse_down(x, y, button="left"):
    if not _require_quartz():
        return
    down_type = Quartz.kCGEventLeftMouseDown if button == "left" else Quartz.kCGEventRightMouseDown
    mouse_button = Quartz.kCGMouseButtonLeft if button == "left" else Quartz.kCGMouseButtonRight
    move = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (x, y), mouse_button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
    time.sleep(0.05)
    down = Quartz.CGEventCreateMouseEvent(None, down_type, (x, y), mouse_button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)


def mouse_up(x, y, button="left"):
    if not _require_quartz():
        return
    up_type = Quartz.kCGEventLeftMouseUp if button == "left" else Quartz.kCGEventRightMouseUp
    mouse_button = Quartz.kCGMouseButtonLeft if button == "left" else Quartz.kCGMouseButtonRight
    up = Quartz.CGEventCreateMouseEvent(None, up_type, (x, y), mouse_button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def hold_click(x, y, hold_sec=1.0):
    mouse_down(x, y)
    time.sleep(hold_sec)
    mouse_up(x, y)


def key_press(key, hold_sec=0.05):
    if not _require_quartz():
        return
    code = KEYCODES.get(key.lower())
    if code is None:
        print(f"{C.RED}{t('automation_mac.unknown_key', keyname=key)}{C.RESET}")
        return
    down = Quartz.CGEventCreateKeyboardEvent(None, code, True)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(hold_sec)
    up = Quartz.CGEventCreateKeyboardEvent(None, code, False)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


# ---------------------------------------------------------------------------
# Double-click on the game icon inside Vita3K's library.
# ---------------------------------------------------------------------------

_ROW_POSITION_SCRIPT = """
tell application "System Events"
    try
        tell process "Vita3K"
            set p to position of row 1 of table 1 of window 1
            return item {index} of p
        end tell
    end try
end tell
"""


def _osascript(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()


def double_click_first_game_row(offset_x=60, offset_y=38):
    """!
    @brief Double-click the first row of Vita3K's game library.
    @param offset_x Pixel offset from the row's origin to the click point (X).
    @param offset_y Pixel offset from the row's origin to the click point (Y).
    @return `True` if the click was performed, `False` if the row couldn't be
            located or its position couldn't be parsed.
    @warning The `(offset_x, offset_y)` defaults were measured empirically
             (row position -> title) against Vita3K's current UI layout;
             they may need adjusting if that layout changes.
    """
    row_x = _osascript(_ROW_POSITION_SCRIPT.format(index=1))
    row_y = _osascript(_ROW_POSITION_SCRIPT.format(index=2))
    if not row_x or not row_y:
        print(f"{C.YELLOW}{t('automation_mac.game_row_not_found')}{C.RESET}")
        return False
    try:
        click_x = int(float(row_x)) + offset_x
        click_y = int(float(row_y)) + offset_y
    except ValueError:
        return False
    click(click_x, click_y, click_count=2)
    return True


def bring_to_front(app_name="Vita3K"):
    _osascript(f'tell application "System Events" to tell process "{app_name}" to set frontmost to true')
