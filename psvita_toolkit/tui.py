"""
Framework de TUI reutilizable: menú de flechas ANSI (sin curses ni
dependencias externas), consistente en todo el toolkit.

Navegación global disponible en CUALQUIER menú, en cualquier nivel de
profundidad:
  - flechas ↑/↓        moverse
  - Enter              elegir
  - 1-9                saltar directo a esa opción
  - 0 / q              volver un nivel (regresa al menú que llamó a este)
  - m                  ir directo al menú PRINCIPAL desde donde sea
  - Ctrl+C             igual que 'm' (nunca mata el proceso de golpe)
"""

import glob
import os
import shutil
import sys
import textwrap
from pathlib import Path


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
    """Se lanza desde un callback de menú para volver directo a la raíz,
    sin importar cuántos submenús haya en el medio."""


class ExitApp(Exception):
    """Se lanza para cerrar el programa de forma prolija."""


class SwitchProject(Exception):
    """Se lanza para volver al selector de proyectos (elegir otro port /
    crear uno nuevo) sin cerrar el programa."""


def clear():
    print("\033[H\033[J", end="")


def term_width(default=80):
    return shutil.get_terminal_size((default, 24)).columns


def getch():
    """Lee un solo carácter (o secuencia de escape de flecha) sin esperar
    Enter. Solo macOS/Linux (termios) -- el toolkit está pensado para el
    flujo de desarrollo en macOS descrito en los scripts originales."""
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


def pause(msg="Presiona ENTER para continuar..."):
    print(f"\n{C.DIM}[ {C.RESET}{C.BOLD}{msg}{C.RESET}{C.DIM} ]{C.RESET}")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def confirm(prompt, default=True):
    suffix = "[S/n]" if default else "[s/N]"
    try:
        raw = input(f"{C.BOLD}{prompt}{C.RESET} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not raw:
        return default
    return raw in ("s", "si", "sí", "y", "yes")


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
    print(f"{C.DIM}↑/↓ mover · Enter elegir · 1-9 salto directo · "
          f"0/Q volver · M menú principal · Ctrl+C salir{C.RESET}")


def run_menu(title, items, breadcrumb="", subtitle=None, icon="🎮", header_extra=None):
    """
    items: lista de tuplas (label:str, callback:callable|None).
      - callback() se ejecuta al elegir esa opción. Puede devolver lo que
        quiera (se ignora); para volver al menú principal desde el
        callback, lanzar GoToMainMenu(); para salir del programa, ExitApp().
      - callback == None marca una opción "volver" explícita en la lista
        (equivalente a apretar 0/Q en ese momento).
    Devuelve normalmente cuando el usuario vuelve un nivel (0/Q, o eligió
    un item con callback None). Deja propagar GoToMainMenu/ExitApp hacia
    arriba para que el loop principal decida qué hacer.
    """
    idx = 0
    n = len(items)
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
            clear()
            print(f"{C.GREEN}{C.BOLD}▶ {label}{C.RESET}\n")
            try:
                cb()
            except (GoToMainMenu, ExitApp, SwitchProject):
                raise
            except KeyboardInterrupt:
                print(f"\n{C.YELLOW}[!] Operación interrumpida.{C.RESET}")
            except Exception as e:  # noqa: BLE001 -- el menú nunca debe morir por un error de una acción
                print(f"\n{C.RED}[-] Error inesperado: {e}{C.RESET}")
            pause()
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


# ---------------------------------------------------------------------------
# Entrada de rutas con autocompletado (drag & drop desde Finder, ~ y comillas)
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
    """Limpia comillas y espacios escapados típicos de arrastrar un archivo
    desde el Finder a la Terminal."""
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
                print(f"{C.RED}Este valor es obligatorio.{C.RESET}")
                continue
        cleaned = clean_path_input(raw)
        p = Path(cleaned).expanduser()
        if must_exist and not p.exists():
            print(f"{C.RED}No existe: {p}{C.RESET}")
            continue
        if is_dir and p.exists() and not p.is_dir():
            print(f"{C.RED}No es un directorio: {p}{C.RESET}")
            continue
        return str(p)


def wrap(text, width=None):
    width = width or min(term_width(), 78)
    return "\n".join(textwrap.wrap(text, width))
