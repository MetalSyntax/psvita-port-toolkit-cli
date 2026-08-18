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

## Why `select_or_create_project()`'s main loop uses a `result_holder` closure

The main entry menu is built from `tui.run_menu()`-style items, but unlike a typical menu
callback (which just performs an action and returns nothing useful), several of these callbacks
need to return an actual value — the opened project's config dict — back out to the caller.
Since a menu callback's return value is normally discarded, `result_holder` is a one-item dict
captured by each callback's closure purely as an out-of-band way to smuggle that return value
back to the loop driving the menu, which then checks `result_holder.get("value")` after the
callback runs and returns it if set. It's a small trick, but a deliberate one — don't replace it
with a return-value-based design without also reworking how `run_menu()`'s generic item
callbacks are invoked.

## `_edit_global_config()`'s language-change step

`_edit_global_config()` offers to change the active UI language first, before walking through
`REQUIRED_GLOBAL_KEYS`. This reuses `cfgmod.prompt_language()` (the same tri-lingual picker
shown on first run) rather than a separate implementation — see `docs/dev-notes/config.md` for
why that picker can't just call `t()`. Remember that `REQUIRED_GLOBAL_KEYS`'s values are i18n
*keys*, not literal text — `desc` must be resolved with `t(desc)` before display, which is why
that line looks like `t(desc)` instead of a plain `desc`.
