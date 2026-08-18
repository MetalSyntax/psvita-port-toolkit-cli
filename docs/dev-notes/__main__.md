# `__main__.py` — Developer Notes

## Why the flow is structured as two nested loops

The toolkit alternates between two screens: the project selector
(`project.select_or_create_project()`) and the active project's main menu
(`show_project_menu()`). `main()`'s outer `while True` loop hands control between them; each
inner loop only needs to worry about the exception that returns control to *its own* level:

- `show_project_menu()`'s inner loop only catches `tui.GoToMainMenu` (so any submenu action,
  no matter how deep, can jump straight back to the main menu by raising it) and otherwise lets
  `tui.SwitchProject`/`tui.ExitApp` propagate up untouched.
- `main()` catches `tui.SwitchProject` to go back to the project selector, and `tui.ExitApp` to
  break out of the whole program for good.

This mirrors `tui.py`'s general exception-based navigation model (see
`docs/dev-notes/tui.md`) applied specifically to the two top-level screens.
