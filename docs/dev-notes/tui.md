# `tui.py` — Developer Notes

## Why no `curses`

This toolkit's menu framework is a hand-rolled ANSI arrow-key menu instead of Python's `curses`.
`curses` takes over the whole terminal screen and buffers, which is more machinery than this
tool needs and complicates mixing menu navigation with plain `print()`/`input()` calls used
throughout the rest of the codebase (progress messages during a build, FTP transfer output,
etc). The simpler approach here — clear the screen, redraw, read one keypress, repeat — is
easy to reason about and composes cleanly with ordinary stdout.

## Why `getch()` is macOS/Linux only

`getch()` reads a single keypress (including a full arrow-key escape sequence) without waiting
for Enter, using `termios`/`tty` raw mode — a POSIX-only mechanism. Windows would need a
completely different implementation (`msvcrt`), which was out of scope: the whole toolkit's
development workflow (VITASDK, jadx, Docker-based Ghidra decompilation) targets macOS
specifically, so this wasn't worth generalizing.

## Why navigation is exception-based (`GoToMainMenu`, `ExitApp`, `SwitchProject`)

Menus can nest arbitrarily deep (main menu → shaders submenu → a specific action), and the
requirement was that pressing `M` or Ctrl+C jumps straight back to the main menu *from any
depth*, not just one level up. A return-value-based "go back" signal would have to be threaded
and checked at every single level of nesting. Raising an exception instead lets any deeply
nested `run_menu()` call unwind straight past all the intermediate menu loops in one shot — the
top-level loop in `__main__.py`'s `main()` is the only place that needs to catch it. `ExitApp`
and `SwitchProject` (return to the project selector) follow the same pattern for the same
reason. `MenuResult(value)` is the odd one out: it's not a "jump to a different screen" signal,
it's "hand `value` back to whoever called `run_menu()`, right now, skipping the usual pause and
redraw" -- see `docs/dev-notes/project.md` for the one place that needs it.

## Why `run_menu()` and `select_list()` share a single `_navigate()` core

Every selectable list in the toolkit -- the main menus, but also "pick a VPK to upload", "pick a
crash dump to analyze", "pick a console profile", "pick a project" -- needs identical rendering
and identical shortcuts (arrows, 1-9/letters, `/` search, 0/Q back, M/Ctrl+C to the main menu).
Rather than reimplement that per call site (which is exactly what `ftp_ops.py`'s VPK/dump
pickers and `project.py`'s old project-selector loop used to do, each slightly differently),
`_navigate(title, labels, ...)` renders the list and drives the keyboard once, returning either
the chosen index or `None`. `run_menu()` wraps it with the "callback + pause + redraw forever"
loop; `select_list(title, entries, label_fn, ...)` just returns `entries[idx]` for callers that
want a value back instead of firing a callback. Any new "choose one of these X" screen should
use `select_list()` instead of hand-rolling another `input()`-based numbered picker.

## Why shortcuts run out at 9 digits + the letter pool, not further

`_shortcut_for_index()` assigns `'1'`-`'9'` to the first nine items, then letters `a`-`z` for
item 10 onward, skipping `j`/`k`/`m`/`q` because those are reserved navigation keys (Down,
Up-alternative, main menu, back) at every menu depth -- letting an item "steal" one of them
would make that reserved key ambiguous depending on which menu is open. That leaves 22 letters,
so up to 31 items total get a direct one-key shortcut; anything beyond that is still reachable
with the arrow keys (or `/` search), it just has no dedicated key. No menu in the toolkit is
anywhere near that size, but the fallback is there so a future one wouldn't silently misbehave.

## Why search mode has no jump-shortcuts of its own

While typing a `/` search query, every printable key is query text -- so digits and letters
can't *also* mean "jump to item N" without ambiguity (is `a` narrowing the query or jumping to
option 10?). Search mode therefore only recognizes Up/Down/Enter/Backspace/Esc/Ctrl+C; direct
shortcuts resume once the user leaves search mode. Escape only reliably cancels search because
`getch()` peeks (via a short `select()` timeout) for whether more bytes follow a lone `\x1b` --
a real arrow-key escape sequence arrives as one burst from the terminal driver, a standalone
Escape key doesn't, so the two are told apart instead of `getch()` always assuming an arrow.
