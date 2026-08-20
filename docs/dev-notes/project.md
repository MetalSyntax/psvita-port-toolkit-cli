# `project.py` — Developer Notes

## What this screen is for

`project.py` resolves the toolkit's very first question: "which port do you want to work on?"
— continue with an existing one (auto-detected under `BASE_DIR`, picked from the recents list,
or given as a manual path), or create a new one from scratch (delegated entirely to
`init_port.py`). Everything downstream (build, deploy, LiveArea, crash analysis...) operates on
whatever project config this screen hands back.

## Why the "adopt" flow exists

Any of the five original ports (Zenonia 2/3/4, Dungeon Hunter 2, Advena) — or any port created
before this toolkit existed — has no `.psvita-toolkit.json`. Rather than forcing the user to
fill in every field by hand for a port that already has most of that information sitting in its
`CMakeLists.txt` and legacy `porting_tools/manage_vita.py`, `_adopt_project()` calls
`cfgmod.autodetect_legacy_fields()` first and pre-fills every prompt with its best guess —
Enter accepts the detected value, so adopting a well-behaved legacy port is nearly a formality.

## Why `select_or_create_project()`'s main loop raises `tui.MenuResult`

The main entry menu is built from plain `tui.run_menu()` items, but unlike a typical menu
callback (which just performs an action and returns nothing useful), "continue with the last
port"/"continue with another port"/"create a new port" need to hand an actual value — the opened
project's config dict — straight back to `select_or_create_project()`'s caller, and to do it
immediately, without the usual `pause()` ("Press ENTER to continue...") `run_menu()` shows after
every other action. `tui.MenuResult(value)` exists exactly for this: raising it from a callback
makes `run_menu()` return `value` right away instead of pausing and redrawing. The other two
items ("global settings", "doctor") are ordinary callbacks with no early return, so `run_menu()`
just redraws this same menu after them — which is also why the function no longer needs its own
outer `while True`: that redraw-after-a-plain-action behavior is `run_menu()`'s job now, not
this screen's.

## Why `main()` catches `tui.GoToMainMenu` around this screen

This screen sits at the root: pressing `M` or Ctrl+C from here has nowhere shallower to jump to,
so `tui.GoToMainMenu` would otherwise escape `select_or_create_project()` uncaught. `__main__.main()`
catches it right there and just loops back to redraw the project selector — consistent with
every other menu treating `GoToMainMenu` as "go back to my own root", since this screen IS the root.

## `_edit_global_config()`'s language-change step

`_edit_global_config()` offers to change the active UI language first, before walking through
`REQUIRED_GLOBAL_KEYS`. This reuses `cfgmod.prompt_language()` (the same tri-lingual picker
shown on first run) rather than a separate implementation — see `docs/dev-notes/config.md` for
why that picker can't just call `t()`. Remember that `REQUIRED_GLOBAL_KEYS`'s values are i18n
*keys*, not literal text — `desc` must be resolved with `t(desc)` before display, which is why
that line looks like `t(desc)` instead of a plain `desc`.
