"""
Automatización de UI para Vita3K en macOS -- integrado desde
porting_tools/automation/*.py (click_helper, hold_click, mousedown/up_only,
key_helper). Vita3K usa una UI Qt que NO responde a clics sintéticos de
accesibilidad (osascript/AppleScript) -- hace falta inyectar eventos reales
de mouse/teclado a nivel de sistema operativo vía Quartz.

Requiere `pip install pyobjc` (para el módulo Quartz). Solo macOS.
"""

import subprocess
import time

from .tui import C

try:
    import Quartz
    HAVE_QUARTZ = True
except ImportError:
    HAVE_QUARTZ = False

KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29,
    "return": 36, "tab": 48, "space": 49, "escape": 53,
}


def _require_quartz():
    if not HAVE_QUARTZ:
        print(f"{C.RED}[-] Falta pyobjc -- instalar con: pip install pyobjc{C.RESET}")
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
        print(f"{C.RED}[-] Tecla desconocida: '{key}' (ver automation_mac.KEYCODES){C.RESET}")
        return
    down = Quartz.CGEventCreateKeyboardEvent(None, code, True)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(hold_sec)
    up = Quartz.CGEventCreateKeyboardEvent(None, code, False)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


# ---------------------------------------------------------------------------
# Doble clic en el ícono del juego dentro de la biblioteca de Vita3K.
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
    """Doble clic sobre la primera fila de la biblioteca de Vita3K -- offset
    fijo (fila -> título) medido empíricamente; ajustar si Vita3K cambia su
    layout de UI."""
    row_x = _osascript(_ROW_POSITION_SCRIPT.format(index=1))
    row_y = _osascript(_ROW_POSITION_SCRIPT.format(index=2))
    if not row_x or not row_y:
        print(f"{C.YELLOW}[!] No se pudo ubicar la fila del juego en la ventana de Vita3K "
              f"(¿está abierta y con foco?).{C.RESET}")
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
