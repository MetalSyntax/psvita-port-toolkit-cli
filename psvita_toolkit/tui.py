"""!
@file tui.py
@brief Reusable TUI framework: ANSI arrow-key menu (no curses or external
       dependencies), consistent across the whole toolkit.

@details
Global navigation available in ANY menu, at any level of depth:
  - ↑/↓ arrows   move the selection
  - Enter        choose the selected item
  - 1-9          jump directly to that numbered option
  - 0 / Q        go back one level (returns to the menu that called this one)
  - M            jump directly to the MAIN menu from anywhere
  - Ctrl+C       same as 'M' while navigating (never kills the process abruptly)

See `docs/dev-notes/tui.md` for the rationale behind avoiding `curses`, the
`termios`-based `getch()`, and the exception-based navigation model.
"""

import glob
import os
import shutil
import sys
import textwrap
from pathlib import Path

from . import i18n
from .i18n import t

STRINGS = {
    "tui.press_enter": {
        "es": "Presiona ENTER para continuar...",
        "en": "Press ENTER to continue...",
        "pt": "Pressione ENTER para continuar...",
    },
    "tui.footer_hint": {
        "es": "↑/↓ mover · Enter elegir · 1-9 salto directo · 0/Q volver · M menú principal · Ctrl+C salir",
        "en": "↑/↓ move · Enter select · 1-9 jump · 0/Q back · M main menu · Ctrl+C exit",
        "pt": "↑/↓ mover · Enter selecionar · 1-9 atalho · 0/Q voltar · M menu principal · Ctrl+C sair",
    },
    "tui.interrupted": {
        "es": "[!] Operación interrumpida.",
        "en": "[!] Operation interrupted.",
        "pt": "[!] Operação interrompida.",
    },
    "tui.unexpected_error": {
        "es": "[-] Error inesperado: {error}",
        "en": "[-] Unexpected error: {error}",
        "pt": "[-] Erro inesperado: {error}",
    },
    "tui.required_value": {
        "es": "Este valor es obligatorio.",
        "en": "This value is required.",
        "pt": "Este valor é obrigatório.",
    },
    "tui.path_not_found": {
        "es": "No existe: {path}",
        "en": "Doesn't exist: {path}",
        "pt": "Não existe: {path}",
    },
    "tui.path_not_a_dir": {
        "es": "No es un directorio: {path}",
        "en": "Not a directory: {path}",
        "pt": "Não é um diretório: {path}",
    },
}
i18n.register(STRINGS)


class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    RESET = "\033[0m"


class GoToMainMenu(Exception):
    """!
    @brief Raise from a menu callback to return straight to the root menu,
           regardless of how many submenus are in between.
    """


class ExitApp(Exception):
    """!
    @brief Raise to shut down the program cleanly.
    """


class SwitchProject(Exception):
    """!
    @brief Raise to return to the project selector (pick another port /
           create a new one) without closing the program.
    """


def clear():
    # \033[H = cursor home, \033[2J = clear entire screen, \033[3J = clear scrollback buffer
    sys.stdout.write("\033[H\033[2J\033[3J")
    sys.stdout.flush()


def term_width(default=80):
    return shutil.get_terminal_size((default, 24)).columns


def getch():
    """!
    @brief Read a single character (or an arrow-key escape sequence) without
           waiting for Enter.
    @return The character read, or the 3-character escape sequence for an
            arrow key (e.g. `"\\x1b[A"` for the up arrow).
    @note macOS/Linux only -- relies on `termios`/`tty` raw mode.
    """
    import termios
    import tty
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch += sys.stdin.read(2)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


def pause(msg=None):
    print(f"\n{C.DIM}[ {C.RESET}{C.BOLD}{msg or t('tui.press_enter')}{C.RESET}{C.DIM} ]{C.RESET}")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def confirm(prompt, default=True):
    yes_letter = "y" if i18n.get_language() == "en" else "s"
    suffix = f"[{yes_letter.upper()}/n]" if default else f"[{yes_letter}/N]"
    try:
        raw = input(f"{C.BOLD}{prompt}{C.RESET} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not raw:
        return default
    return raw in ("s", "si", "sí", "sim", "y", "yes")


def print_banner(title, subtitle=None, breadcrumb=None, icon="🎮"):
    width = min(term_width(), 78)
    bar = "═" * width
    print(f"{C.CYAN}{C.BOLD}╔{bar}╗{C.RESET}")
    line = f"{icon}  {title}"
    print(f"{C.CYAN}{C.BOLD}║{C.RESET}{line.center(width)}{C.CYAN}{C.BOLD}║{C.RESET}")
    if subtitle:
        print(f"{C.CYAN}{C.BOLD}║{C.RESET}{C.DIM}{subtitle.center(width)}{C.RESET}{C.CYAN}{C.BOLD}║{C.RESET}")
    print(f"{C.CYAN}{C.BOLD}╚{bar}╝{C.RESET}")
    if breadcrumb:
        print(f"{C.DIM}📍 {breadcrumb}{C.RESET}")
    print()


def _footer_hint():
    print()
    print(f"{C.DIM}{t('tui.footer_hint')}{C.RESET}")


def run_menu(title, items, breadcrumb="", subtitle=None, icon="🎮", header_extra=None):
    """!
    @brief Render and drive an arrow-key menu until the user backs out of it.
    @param title Banner title.
    @param items List of `(label: str, callback: callable | None)` tuples.
           `callback()` runs when that item is chosen; its return value is
           ignored. Raise `GoToMainMenu()` from a callback to return to the
           main menu, or `ExitApp()` to quit. `callback is None` marks an
           explicit "back" item (same as pressing 0/Q on it).
    @param breadcrumb Optional breadcrumb line shown under the banner.
    @param subtitle Optional subtitle shown under the banner title.
    @param icon Emoji shown in the banner.
    @param header_extra Optional zero-arg callable invoked (and printed)
           right after the banner, before the item list.
    @return Normally, when the user backs out (0/Q, or chose a `None`-callback
            item). Lets `GoToMainMenu`/`ExitApp`/`SwitchProject` propagate up
            so the caller's loop decides what to do next.
    """
    idx = 0
    n = len(items)
    # Hide cursor during menu navigation
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    try:
        while True:
            clear()
            print_banner(title, subtitle=subtitle, breadcrumb=breadcrumb, icon=icon)
            if header_extra:
                header_extra()
                print()

            for i, (label, _cb) in enumerate(items):
                prefix = f"{i + 1:2d}. " if i < 9 else "    "
                if i == idx:
                    print(f"{C.BLUE}\033[1m\033[7m> {prefix}{label} {C.RESET}")
                else:
                    print(f"  {prefix}{label}")

            _footer_hint()

            try:
                c = getch()
            except (EOFError, KeyboardInterrupt):
                raise GoToMainMenu()

            if c in ("\x1b[A", "k"):
                idx = (idx - 1) % n
            elif c in ("\x1b[B", "j"):
                idx = (idx + 1) % n
            elif c in ("\r", "\n"):
                label, cb = items[idx]
                if cb is None:
                    return
                # Show cursor for normal command execution
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()
                clear()
                print(f"{C.GREEN}{C.BOLD}▶ {label}{C.RESET}\n")
                try:
                    cb()
                except (GoToMainMenu, ExitApp, SwitchProject):
                    raise
                except KeyboardInterrupt:
                    print(f"\n{C.YELLOW}{t('tui.interrupted')}{C.RESET}")
                except Exception as e:  # noqa: BLE001 -- the menu must never die from an action's error
                    print(f"\n{C.RED}{t('tui.unexpected_error', error=e)}{C.RESET}")
                pause()
                # Re-hide cursor for menu navigation
                sys.stdout.write("\033[?25l")
                sys.stdout.flush()
            elif c in ("0", "q", "Q"):
                return
            elif c in ("m", "M"):
                raise GoToMainMenu()
            elif c == "\x03":
                raise GoToMainMenu()
            elif c.isdigit():
                v = int(c)
                if 1 <= v <= min(9, n):
                    idx = v - 1
    finally:
        # Always restore cursor when leaving menu
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Path input with autocompletion (Finder drag & drop, ~ and quotes)
# ---------------------------------------------------------------------------

def setup_path_completer():
    import readline
    def complete(text, state):
        text = os.path.expanduser(text)
        matches = glob.glob(text + "*")
        matches = [m + "/" if os.path.isdir(m) else m for m in matches]
        try:
            return matches[state]
        except IndexError:
            return None
    readline.set_completer_delims(" \t\n;")
    readline.parse_and_bind("tab: complete")
    readline.set_completer(complete)


def clean_path_input(raw):
    """!
    @brief Clean up quotes and escaped spaces typical of dragging a file
           from the Finder into the Terminal.
    @param raw Raw path string as typed/pasted by the user.
    @return Cleaned, `~`-expanded path string.
    """
    p = raw.strip()
    if len(p) >= 2 and ((p[0] == p[-1] == '"') or (p[0] == p[-1] == "'")):
        p = p[1:-1]
    p = p.replace("\\ ", " ")
    p = os.path.expanduser(p)
    return p


def input_path(prompt, default=None, must_exist=False, is_dir=False, allow_blank=False):
    setup_path_completer()
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{C.BOLD}{prompt}{C.RESET}{suffix}\n> ").strip()
        if not raw:
            if default:
                raw = default
            elif allow_blank:
                return ""
            else:
                print(f"{C.RED}{t('tui.required_value')}{C.RESET}")
                continue
        cleaned = clean_path_input(raw)
        p = Path(cleaned).expanduser()
        if must_exist and not p.exists():
            print(f"{C.RED}{t('tui.path_not_found', path=p)}{C.RESET}")
            continue
        if is_dir and p.exists() and not p.is_dir():
            print(f"{C.RED}{t('tui.path_not_a_dir', path=p)}{C.RESET}")
            continue
        return str(p)


def wrap(text, width=None):
    width = width or min(term_width(), 78)
    return "\n".join(textwrap.wrap(text, width))
