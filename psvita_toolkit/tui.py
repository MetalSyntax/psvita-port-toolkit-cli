"""!
@file tui.py
@brief Reusable TUI framework: ANSI arrow-key menu (no curses or external
       dependencies), consistent across the whole toolkit.

@details
Global navigation available in ANY menu, at any level of depth:
  - Up/Down arrows   move the selection
  - Mouse click      click a row to select it, click it again to confirm;
                     wheel scrolls, right-click goes back (needs a terminal
                     with mouse reporting; silently keyboard-only otherwise,
                     or set PSVITA_TOOLKIT_NO_MOUSE=1 to force it off)
  - Enter            choose the selected item
  - 1-9              jump directly to options 1-9
  - a, b, c, ...      jump directly to option 10 and beyond (letters skip
                      j/k/m/q, since those are reserved navigation keys)
  - /                enter search mode: type to filter the visible options
                      by label, Up/Down to move within the filtered list,
                      Enter to choose, Backspace on an empty query (or
                      Ctrl+C) to leave search mode
  - 0 / Q            go back one level (returns to the menu that called this one)
  - M                jump directly to the MAIN menu from anywhere
  - Ctrl+C           same as 'M' while navigating (never kills the process abruptly)
  - R                after an option finishes (or fails), run it again

Rendering is repaint-in-place (cursor home + redraw, no scrollback purge),
so navigation doesn't flicker; a terminal resize redraws immediately.

`select_list()` shares the same rendering/navigation core as `run_menu()` --
it is the picker used for "choose one of these VPKs/dumps/profiles"-style
lists throughout the toolkit, so every selectable list in the app gets the
same shortcuts, search, mouse, and look for free.

See `docs/dev-notes/tui.md` for the rationale behind avoiding `curses`, the
`termios`-based `getch()`, and the exception-based navigation model.
"""

import contextlib
import glob
import io
import os
import re
import select
import shutil
import sys
import textwrap
from pathlib import Path

from . import i18n
from .i18n import t
from .icons import Icons

STRINGS = {
    "tui.press_enter": {
        "es": "Presiona ENTER para continuar...",
        "en": "Press ENTER to continue...",
        "pt": "Pressione ENTER para continuar...",
    },
    "tui.footer_hint": {
        "es": "↑/↓ o click mover · Enter o doble-click elegir · rueda scroll · click-der volver · 1-9,a-z salto · / buscar · 0/Q volver · M principal",
        "en": "↑/↓ or click move · Enter or double-click select · wheel scrolls · right-click back · 1-9,a-z jump · / search · 0/Q back · M main",
        "pt": "↑/↓ ou clique mover · Enter ou duplo-clique escolher · roda rola · botão-dir voltar · 1-9,a-z atalho · / buscar · 0/Q voltar · M principal",
    },
    "tui.footer_hint_search": {
        "es": "↑/↓ mover · Enter elegir · Backspace borrar/salir · Esc o Ctrl+C salir de la búsqueda",
        "en": "↑/↓ move · Enter select · Backspace delete/exit · Esc or Ctrl+C exit search",
        "pt": "↑/↓ mover · Enter selecionar · Backspace apagar/sair · Esc ou Ctrl+C sair da busca",
    },
    "tui.search_prompt": {
        "es": "Buscar",
        "en": "Search",
        "pt": "Buscar",
    },
    "tui.search_no_matches": {
        "es": "(sin resultados)",
        "en": "(no matches)",
        "pt": "(sem resultados)",
    },
    "tui.no_items": {
        "es": "(nada para mostrar)",
        "en": "(nothing to show)",
        "pt": "(nada para mostrar)",
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
    "tui.after_action": {
        "es": "Enter volver al menú · R repetir esta opción",
        "en": "Enter back to menu · R run this option again",
        "pt": "Enter voltar ao menu · R repetir esta opção",
    },
    "tui.after_action_failed": {
        "es": "La opción falló -- R reintentar · Enter volver al menú",
        "en": "The option failed -- R retry · Enter back to menu",
        "pt": "A opção falhou -- R tentar de novo · Enter voltar ao menu",
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


class MenuResult(Exception):
    """!
    @brief Raise from a `run_menu()` callback to make `run_menu()` return
           `value` immediately to ITS caller, skipping the usual
           pause()-then-redraw-the-same-menu cycle.
    @details For menu items whose whole point is to hand control to a
             different screen and report back what happened there (e.g. the
             project picker's "continue with this port" -> the caller wants
             the chosen project config right away, not an Enter-to-continue
             prompt followed by the picker menu again).
    """
    def __init__(self, value=None):
        super().__init__()
        self.value = value


def clear():
    # \033[H = cursor home, \033[2J = clear entire screen, \033[3J = clear scrollback buffer
    sys.stdout.write("\033[H\033[2J\033[3J")
    sys.stdout.flush()


def term_width(default=80):
    return shutil.get_terminal_size((default, 24)).columns


def getch(wake=None):
    """!
    @brief Read a single character or escape sequence without waiting for Enter.
    @param wake Optional zero-arg predicate polled every ~250ms while waiting
           (e.g. "did the terminal get resized?"); when it returns true,
           `getch()` gives up and returns `None`. Raw mode stays on the whole
           time, so keys/clicks arriving mid-wait are never echoed or lost.
    @return `None` if `wake()` fired with no key pressed, else the character read, or the full escape sequence for an arrow/function
            key (e.g. `"\\x1b[A"` or `"\\x1bOA"` for Up arrow, `"\\x1b"` for lone Escape).
            SGR mouse reports (`"\\x1b[<Cb;x;yM/m"`) arrive whole: if a sequence
            looks truncated, up to ~50ms is spent waiting for its tail.
    @note macOS/Linux raw terminal read using os.read(fd) directly to prevent
          Python's TextIOWrapper buffer from swallowing multi-byte escape bursts.
    """
    import termios
    import tty
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        if wake is not None:
            while not select.select([fd], [], [], 0.25)[0]:
                if wake():
                    return None
        raw = os.read(fd, 64)
        # A mouse report split across two reads would otherwise be dropped as
        # garbage -- if it looks like an unfinished SGR sequence, wait briefly
        # for the rest instead.
        if re.match(r"^\x1b\[<\d*(;\d*){0,2}$", raw.decode("utf-8", errors="replace")):
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                raw += os.read(fd, 64)
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def pause(msg=None):
    print(f"\n{C.DIM}[ {C.RESET}{C.BOLD}{msg or t('tui.press_enter')}{C.RESET}{C.DIM} ]{C.RESET}")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def _after_action_prompt(failed=False):
    """!
    @brief Post-action pause that also offers re-running the same option.
    @param failed Whether the action just raised -- only changes the wording.
    @return `True` if the user pressed R (run it again), `False` to go back.
    @note Every menu action gets this for free via `run_menu()`, so an option
          that fails on a flaky thing (FTP, a console that isn't listening
          yet, a half-written file) can be retried without navigating back
          to it -- no per-action retry plumbing needed.
    """
    if not sys.stdin.isatty():
        pause()
        return False
    key = "tui.after_action_failed" if failed else "tui.after_action"
    color = C.YELLOW if failed else C.BOLD
    print(f"\n{C.DIM}[ {C.RESET}{color}{t(key)}{C.RESET}{C.DIM} ]{C.RESET}")
    while True:
        try:
            c = getch()
        except (EOFError, KeyboardInterrupt):
            return False
        if c in ("r", "R"):
            return True
        if c in ("m", "M"):
            raise GoToMainMenu()
        if c in ("\r", "\n", "\x03", "\x1b", "0", "q", "Q", " "):
            return False


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


def _banner_inner_width(cols):
    """!
    @brief Width between the banner's corner glyphs, so the box never wraps.
    """
    return max(10, min(cols - 2, 78))


def print_banner(title, subtitle=None, breadcrumb=None):
    width = _banner_inner_width(term_width())
    bar = "═" * width
    print(f"{C.CYAN}{C.BOLD}╔{bar}╗{C.RESET}")
    print(f"{C.CYAN}{C.BOLD}║{C.RESET}{C.BOLD}{title.center(width)}{C.RESET}{C.CYAN}{C.BOLD}║{C.RESET}")
    if subtitle:
        print(f"{C.CYAN}{C.BOLD}║{C.RESET}{C.DIM}{subtitle.center(width)}{C.RESET}{C.CYAN}{C.BOLD}║{C.RESET}")
    print(f"{C.CYAN}{C.BOLD}╚{bar}╝{C.RESET}")
    if breadcrumb:
        print(f"{C.DIM}{breadcrumb}{C.RESET}")
    print()


def _footer_hint(search_mode=False):
    print()
    key = "tui.footer_hint_search" if search_mode else "tui.footer_hint"
    print(f"{C.DIM}{t(key)}{C.RESET}")


# ---------------------------------------------------------------------------
# Mouse support (SGR 1006). Best-effort: terminals without mouse reporting
# simply never emit these sequences, and the keyboard path is untouched.
# ---------------------------------------------------------------------------

_MOUSE_ENABLE = "\033[?1000h\033[?1006h"
_MOUSE_DISABLE = "\033[?1006l\033[?1000l"
_MOUSE_SGR_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")

_BTN_LEFT, _BTN_MIDDLE, _BTN_RIGHT = 0, 1, 2
_BTN_WHEEL_UP, _BTN_WHEEL_DOWN = 64, 65


def _mouse_supported():
    """!
    @brief Whether to ask the terminal for mouse reports.
    @return `False` on dumb terms, non-TTYs, or with PSVITA_TOOLKIT_NO_MOUSE=1.
    """
    if os.environ.get("PSVITA_TOOLKIT_NO_MOUSE"):
        return False
    term = os.environ.get("TERM", "")
    return (sys.stdout.isatty() and sys.stdin.isatty()
            and term not in ("", "dumb", "unknown"))


def _split_mouse_events(chunk):
    """!
    @brief Split SGR mouse reports out of a raw `getch()` chunk.
    @param chunk Raw string as returned by `getch()` (may hold a click press +
           its release, or mouse bytes glued to a keypress).
    @return `(events, rest)` where `events` is a list of `(button, x, y)`
            tuples (releases already filtered out) and `rest` is any
            non-mouse remainder to handle as a normal key.
    """
    events = []
    rest = chunk
    for m in _MOUSE_SGR_RE.finditer(chunk):
        btn, x, y, kind = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        rest = rest.replace(m.group(0), "", 1)
        if kind == "m":
            continue  # button release -- the press already did the work
        btn &= ~32  # ignore the drag-motion modifier bit
        events.append((btn, x, y))
    return events, rest


def _visible_len(text):
    """!
    @brief Printable width of a line, ignoring ANSI color codes.
    """
    return len(_strip_ansi(text))


def _build_frame(title, labels, visible, idx, width, height, search_mode, query,
                 breadcrumb="", subtitle=None, header_lines=None, win_start=0):
    """!
    @brief Render one menu frame into a list of plain strings.
    @param labels All item labels, in order.
    @param visible Indices into `labels` currently shown (all of them, or the
           search-filtered subset).
    @param idx Position inside `visible` currently highlighted.
    @param width Terminal width for wrap accounting.
    @param height Terminal height; when the item block doesn't fit, only a
           window around `idx` is rendered with `··· N more ···` indicators.
    @param header_lines Captured `header_extra` output lines (no trailing blank).
    @param win_start Preferred window start (visible position); adjusted so
           `idx` stays on screen.
    @return `(frame, item_rows, win_start)` where `frame` holds every line to
            print, `item_rows` is a list of `(visible_pos, screen_row)` for
            each rendered item (0-based screen row, wrap-aware) closed by a
            `(None, end_row)` sentinel -- see `_click_pos()` -- and
            `win_start` is the adjusted window start to carry over.
    """
    import math
    inner = _banner_inner_width(width)
    bar = "═" * inner
    prefix = [
        f"{C.CYAN}{C.BOLD}╔{bar}╗{C.RESET}",
        f"{C.CYAN}{C.BOLD}║{C.RESET}{C.BOLD}{title.center(inner)}{C.RESET}{C.CYAN}{C.BOLD}║{C.RESET}",
    ]
    if subtitle:
        prefix.append(f"{C.CYAN}{C.BOLD}║{C.RESET}{C.DIM}{subtitle.center(inner)}{C.RESET}{C.CYAN}{C.BOLD}║{C.RESET}")
    prefix.append(f"{C.CYAN}{C.BOLD}╚{bar}╝{C.RESET}")
    if breadcrumb:
        prefix.append(f"{C.DIM}{breadcrumb}{C.RESET}")
    prefix.append("")
    if header_lines is not None:
        prefix.extend(header_lines)
        prefix.append("")

    item_lines = []  # (visible_pos, rendered_line)
    if not visible:
        item_lines.append((-1, f"  {C.DIM}{t('tui.search_no_matches') if search_mode and query else t('tui.no_items')}{C.RESET}"))
    elif search_mode:
        prefix.append(f"{C.BOLD}{Icons.SEARCH} {C.RESET}{query}{C.DIM}_{C.RESET}")
        prefix.append("")
        for pos, orig_i in enumerate(visible):
            if pos == idx:
                item_lines.append((pos, f"{C.CYAN}{C.BOLD}\033[7m {Icons.POINTER_BOLD}     {labels[orig_i]} {C.RESET}"))
            else:
                item_lines.append((pos, f"      {labels[orig_i]}"))
    else:
        for pos, orig_i in enumerate(visible):
            shortcut = _shortcut_for_index(orig_i)
            pfx = f"{shortcut:>2}. " if shortcut else "    "
            if pos == idx:
                item_lines.append((pos, f"{C.CYAN}{C.BOLD}\033[7m {Icons.POINTER_BOLD} {pfx}{labels[orig_i]} {C.RESET}"))
            else:
                item_lines.append((pos, f"  {pfx}{labels[orig_i]}"))

    suffix = ["", f"{C.DIM}{t('tui.footer_hint_search' if search_mode else 'tui.footer_hint')}{C.RESET}"]

    # Window the item block so huge lists render fast and clicks map right.
    def rows_of(line):
        return max(1, math.ceil(max(_visible_len(line), 1) / max(width, 1)))

    row_counts = [rows_of(line) for _, line in item_lines]
    fixed_rows = sum(rows_of(line) for line in prefix) + sum(rows_of(line) for line in suffix)
    avail = max(5, height - fixed_rows - 2)  # 2 spare for indicators
    total_rows = sum(row_counts)
    if total_rows <= avail or not visible:
        win = item_lines
        above = below = 0
    else:
        win_start = min(max(0, win_start), max(0, len(item_lines) - 1))
        if idx < win_start:
            win_start = idx
        while win_start <= idx:
            if sum(row_counts[win_start:idx + 1]) <= avail:
                break
            win_start += 1
        win, used = [], 0
        for pos, line in item_lines[win_start:]:
            need = row_counts[win_start + len(win)]
            if used + need > avail:
                break
            win.append((pos, line))
            used += need
        above = win_start
        below = len(item_lines) - win_start - len(win)

    # Track SCREEN rows (not frame lines): a breadcrumb or header line wider
    # than the terminal wraps, and click mapping must account for that.
    frame = list(prefix)
    screen_row = sum(rows_of(line) for line in prefix)
    if above:
        frame.append(f"{C.DIM}··· ↑ {above} more ···{C.RESET}")
        screen_row += 1
    item_rows = []
    for pos, line in win:
        item_rows.append((pos, screen_row))
        frame.append(line)
        screen_row += rows_of(line)
    # Sentinel end row so the last item's wrapped span is clickable too.
    item_rows.append((None, screen_row))
    if below:
        frame.append(f"{C.DIM}··· ↓ {below} more ···{C.RESET}")
    frame.extend(suffix)
    return frame, item_rows, win_start


def _click_pos(item_rows, row_1based):
    """!
    @brief Map a clicked 1-based screen row to a position in the visible list.
    @param item_rows `(visible_pos, screen_row)` pairs from `_build_frame()`,
           ending with a `(None, end_row)` sentinel.
    @return Visible position, or `None` when the click landed outside any item.
            A wrapped continuation line still counts as its item's row span.
    """
    line = row_1based - 1
    for (pos, start), (_next, end) in zip(item_rows, item_rows[1:]):
        if start <= line < end:
            return pos
    return None


# ---------------------------------------------------------------------------
# Shared selection core -- powers both run_menu() (fire-and-forget callbacks)
# and select_list() (returns the chosen entry), so every menu and every
# "pick one from this list" picker in the toolkit gets identical shortcuts,
# search, and look for free.
# ---------------------------------------------------------------------------

# Letters usable as direct-jump shortcuts for option 10 onward. j/k/m/q are
# reserved (Up/Down-alternative, main menu, back), so they're skipped.
_LETTER_POOL = tuple(c for c in "abcdefghijklmnopqrstuvwxyz" if c not in ("j", "k", "m", "q"))

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


def _shortcut_for_index(i):
    """!
    @brief Single-key shortcut for item index `i`: '1'-'9' for the first nine
           items, then a letter (skipping j/k/m/q) for item 10 onward.
    @param i Zero-based item index.
    @return Shortcut string, or `None` once the shortcut pool (9 digits + the
            available letters) is exhausted -- those items are still reachable
            with the arrow keys.
    """
    if i < 9:
        return str(i + 1)
    li = i - 9
    return _LETTER_POOL[li] if li < len(_LETTER_POOL) else None


def _shortcut_index_map(n):
    return {sc: i for i in range(n) if (sc := _shortcut_for_index(i))}


def _filter_indices(labels, query):
    """!
    @return Original indices whose label matches `query` (case-insensitive
            substring, ANSI color codes ignored), in original order.
    """
    if not query:
        return list(range(len(labels)))
    q = query.lower()
    return [i for i, label in enumerate(labels) if q in _strip_ansi(label).lower()]


def _navigate(title, labels, breadcrumb="", subtitle=None, header_extra=None, searchable=True):
    """!
    @brief Render an arrow-key/shortcut/search-filterable list and drive it
           until the user picks an item or backs out. Shared by `run_menu()`
           and `select_list()`.
    @param labels Display labels, in order (may already contain ANSI codes).
    @param searchable Whether '/' enters search mode.
    @return Zero-based index into `labels` the user picked, or `None` if they
            backed out (0/Q, or Backspace-from-empty-query in search mode).
    @note Raises `GoToMainMenu` on 'M' or Ctrl+C, from either mode.
    @note Frames repaint in place (cursor home, no scrollback purge) instead of
          a full clear per keypress, and mouse clicks/wheel work when the
          terminal reports them -- see `_mouse_supported()`.
    """
    n = len(labels)
    idx = 0
    search_mode = False
    query = ""
    filtered = list(range(n))
    pending = []
    win_start = 0

    # Capture header_extra once per menu (it is display-only by contract in
    # every caller), so each frame knows exactly which rows the items start on
    # for click mapping -- and so slow headers (FTP status, asset counts)
    # don't re-run on every single keypress.
    header_lines = None
    if header_extra is not None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            header_extra()
        header_lines = buf.getvalue().splitlines()

    mouse = _mouse_supported()
    sys.stdout.write("\033[?25l")
    if mouse:
        sys.stdout.write(_MOUSE_ENABLE)
    sys.stdout.flush()
    try:
        while True:
            width = term_width()
            height = shutil.get_terminal_size((80, 24)).lines
            visible = filtered if search_mode else list(range(n))
            if idx >= len(visible):
                idx = max(0, len(visible) - 1)
            frame, item_rows, win_start = _build_frame(
                title, labels, visible, idx, width, height, search_mode, query,
                breadcrumb=breadcrumb, subtitle=subtitle, header_lines=header_lines,
                win_start=win_start)
            sys.stdout.write("\033[H" + "\033[K\n".join(frame) + "\033[K\033[J")
            sys.stdout.flush()

            if pending:
                c = pending.pop(0)
            else:
                # Poll so a terminal resize redraws right away instead of
                # waiting for the next keypress.
                size = shutil.get_terminal_size((80, 24))
                try:
                    c = getch(wake=lambda: shutil.get_terminal_size((80, 24)) != size)
                except (EOFError, KeyboardInterrupt):
                    raise GoToMainMenu()
                if c is None:
                    continue

            events, rest = _split_mouse_events(c) if mouse else ([], c)
            if events and rest:
                pending.append(rest)
                rest = ""
            for btn, _x, y in events:
                if btn == _BTN_WHEEL_UP:
                    if visible:
                        idx = max(0, idx - 3)
                elif btn == _BTN_WHEEL_DOWN:
                    if visible:
                        idx = min(len(visible) - 1, idx + 3)
                elif btn == _BTN_RIGHT:
                    return None
                elif btn == _BTN_LEFT:
                    pos = _click_pos(item_rows, y)
                    if pos is not None and pos >= 0:
                        if pos == idx:
                            return visible[idx]
                        idx = pos
            if events and not rest:
                continue
            c = rest

            if search_mode:
                if c in ("\x03", "\x1b", "\x1b\x1b"):
                    search_mode = False
                elif c in ("\r", "\n", "\x1b[C", "\x1bOC"):
                    if filtered:
                        return filtered[idx]
                elif c in ("\x7f", "\x08"):
                    if query:
                        query = query[:-1]
                        filtered = _filter_indices(labels, query)
                        idx = 0
                    else:
                        search_mode = False
                elif c in ("\x1b[A", "\x1bOA", "\x1b[1;5A", "\x1b[a"):
                    if filtered:
                        idx = (idx - 1) % len(filtered)
                elif c in ("\x1b[B", "\x1bOB", "\x1b[1;5B", "\x1b[b"):
                    if filtered:
                        idx = (idx + 1) % len(filtered)
                elif c in ("\x1b[5~", "\x1b[5;5~"):  # Page Up
                    if filtered:
                        idx = max(0, idx - 5)
                elif c in ("\x1b[6~", "\x1b[6;5~"):  # Page Down
                    if filtered:
                        idx = min(len(filtered) - 1, idx + 5)
                elif c in ("\x1b[H", "\x1b[1~", "\x1b[7~"):  # Home
                    if filtered:
                        idx = 0
                elif c in ("\x1b[F", "\x1b[4~", "\x1b[8~"):  # End
                    if filtered:
                        idx = len(filtered) - 1
                elif len(c) == 1 and c.isprintable():
                    query += c
                    filtered = _filter_indices(labels, query)
                    idx = 0
                continue

            if n == 0:
                if c in ("0", "q", "Q", "\x1b", "\x1b[D", "\x1bOD"):
                    return None
                if c in ("m", "M", "\x03"):
                    raise GoToMainMenu()
                continue

            if c in ("\x1b[A", "\x1bOA", "\x1b[1;5A", "\x1b[a", "k", "K"):
                idx = (idx - 1) % n
            elif c in ("\x1b[B", "\x1bOB", "\x1b[1;5B", "\x1b[b", "j", "J"):
                idx = (idx + 1) % n
            elif c in ("\x1b[5~", "\x1b[5;5~"):  # Page Up
                idx = max(0, idx - 5)
            elif c in ("\x1b[6~", "\x1b[6;5~"):  # Page Down
                idx = min(n - 1, idx + 5)
            elif c in ("\x1b[H", "\x1b[1~", "\x1b[7~"):  # Home
                idx = 0
            elif c in ("\x1b[F", "\x1b[4~", "\x1b[8~"):  # End
                idx = n - 1
            elif c in ("\r", "\n", "\x1b[C", "\x1bOC"):  # Enter or Right Arrow
                return idx
            elif c in ("0", "q", "Q", "\x1b", "\x1b[D", "\x1bOD"):  # Back / Left Arrow
                return None
            elif c in ("m", "M", "\x03"):
                raise GoToMainMenu()
            elif c == "/" and searchable:
                search_mode = True
                query = ""
                filtered = list(range(n))
                idx = 0
            elif len(c) == 1:
                target = _shortcut_index_map(n).get(c.lower())
                if target is not None:
                    idx = target
    finally:
        if mouse:
            sys.stdout.write(_MOUSE_DISABLE)
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def run_menu(title, items, breadcrumb="", subtitle=None, header_extra=None):
    """!
    @brief Render and drive an arrow-key menu until the user backs out of it.
    @param title Banner title.
    @param items List of `(label: str, callback: callable | None)` tuples.
           `callback()` runs when that item is chosen; its return value is
           ignored. Afterwards the user can press R to run it again (retry
           after a failure, or just repeat a deploy/log fetch). Raise `GoToMainMenu()` from a callback to return to the
           main menu, or `ExitApp()` to quit. `callback is None` marks an
           explicit "back" item (same as pressing 0/Q on it).
    @param breadcrumb Optional breadcrumb line shown under the banner.
    @param subtitle Optional subtitle shown under the banner title.
    @param header_extra Optional zero-arg callable invoked (and printed)
           right after the banner, before the item list.
    @return `None` when the user backs out (0/Q, or chose a `None`-callback
            item), or the value passed to a `MenuResult` a callback raised.
            Lets `GoToMainMenu`/`ExitApp`/`SwitchProject` propagate up so the
            caller's loop decides what to do next.
    """
    labels = [label for label, _cb in items]
    while True:
        idx = _navigate(title, labels, breadcrumb=breadcrumb, subtitle=subtitle, header_extra=header_extra)
        if idx is None:
            return None
        label, cb = items[idx]
        if cb is None:
            return None
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        while True:
            clear()
            print(f"{C.GREEN}{C.BOLD}{Icons.POINTER_BOLD} {label}{C.RESET}\n")
            failed = False
            try:
                cb()
            except MenuResult as result:
                return result.value
            except (GoToMainMenu, ExitApp, SwitchProject):
                raise
            except KeyboardInterrupt:
                print(f"\n{C.YELLOW}{t('tui.interrupted')}{C.RESET}")
                failed = True
            except Exception as e:  # noqa: BLE001 -- the menu must never die from an action's error
                print(f"\n{C.RED}{t('tui.unexpected_error', error=e)}{C.RESET}")
                failed = True
            if not _after_action_prompt(failed=failed):
                break


def select_list(title, entries, label_fn=str, breadcrumb="", subtitle=None, header_extra=None, searchable=True):
    """!
    @brief Searchable, letter-shortcut picker: "choose one of these" for
           VPKs, crash dumps, console profiles, projects, build presets, etc.
           Shares its rendering/navigation with `run_menu()`.
    @param entries List of arbitrary items to choose from.
    @param label_fn Callable turning one entry into its display label
           (already translated/formatted; may contain ANSI codes).
    @param header_extra Optional zero-arg callable invoked right after the
           banner -- use it for an empty-state message when `entries` is empty.
    @param searchable Whether '/' enters search mode (disable for very short lists).
    @return The chosen entry, or `None` if the user backed out (0/Q). `M`/Ctrl+C
            raise `GoToMainMenu`, same as `run_menu()`.
    """
    labels = [label_fn(e) for e in entries]
    idx = _navigate(title, labels, breadcrumb=breadcrumb, subtitle=subtitle,
                     header_extra=header_extra, searchable=searchable)
    return entries[idx] if idx is not None else None


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
