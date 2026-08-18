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
development workflow (Vita3K, VITASDK, jadx, Docker-based Ghidra decompilation) targets macOS
specifically, so this wasn't worth generalizing.

## Why navigation is exception-based (`GoToMainMenu`, `ExitApp`, `SwitchProject`)

Menus can nest arbitrarily deep (main menu → shaders submenu → a specific action), and the
requirement was that pressing `M` or Ctrl+C jumps straight back to the main menu *from any
depth*, not just one level up. A return-value-based "go back" signal would have to be threaded
and checked at every single level of nesting. Raising an exception instead lets any deeply
nested `run_menu()` call unwind straight past all the intermediate menu loops in one shot — the
top-level loop in `__main__.py`'s `main()` is the only place that needs to catch it. `ExitApp`
and `SwitchProject` (return to the project selector) follow the same pattern for the same
reason.
