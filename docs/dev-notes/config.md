# `config.py` — Developer Notes

This file collects the *why* behind `config.py` that doesn't belong in the code itself as
Doxygen documentation (which only covers the *what*: brief/param/return). Read this if you're
about to change ordering, remove what looks like a redundant check, or wonder why something is
shaped the way it is.

## Two config tiers, not one

Global config (`~/.psvita-toolkit/config.json`) and per-project config
(`<port_dir>/.psvita-toolkit.json`) are deliberately separate files, not one big config:

- Global config holds machine-level paths (`BASE_DIR`, `soloader-boilerplate`, Claude Code
  skills, `VITASDK`) that are the same no matter which port you're working on. Asked once,
  ever, on this machine. (It used to also hold Vita3K emulator paths -- removed along with
  Vita3K support entirely, see `docs/dev-notes/build_deploy.md`.)
- Per-project config holds per-port facts (name, slug, TITLEID, test Vita IP/port, build dir)
  that are specific to one port and travel with it — it lives *inside* the port's own
  directory, so a port stays self-describing even without the toolkit installed alongside it.

## Language must be decided before anything else

`ensure_language()` is called before `ensure_global_config()` specifically so that every
prompt shown after it (the required-paths wizard, the project selector, everything) already
renders in the user's chosen language. Swapping that order would mean the first-run experience
starts in Spanish (the fallback) regardless of what the user picks, which defeats the point of
asking early.

`prompt_language()` itself cannot call `t()` for its own text, because there is no active
language yet at that point — it's a chicken-and-egg problem, so its prompt is hardcoded showing
all three supported languages at once ("Selecciona idioma / Select language / Selecione
idioma"), and only *after* the user picks one does `t()` become usable for the rest of the app.

## `REQUIRED_GLOBAL_KEYS` values are i18n keys, not literal text

This was a deliberate refactor when the i18n system was introduced: `REQUIRED_GLOBAL_KEYS`
used to map each config key to its literal Spanish prompt text. Now it maps to an i18n key
string instead (e.g. `"config.required.base_dir"`), resolved via `t(REQUIRED_GLOBAL_KEYS[key])`
at the point of display. This is why `project.py`'s `_edit_global_config()` has to call
`t(desc)` on the value it pulls from this dict, instead of printing `desc` directly — if you
add a new required key, remember its value must be an i18n key, not a hardcoded string.

## Why `soloader-boilerplate` is excluded from `discover_projects()`

The boilerplate scaffold itself ships a `CMakeLists.txt` with a placeholder `VITA_TITLEID`
(`"SOLOADER0"`). `looks_like_port()`'s heuristic (does this folder have a `CMakeLists.txt`
mentioning `VITA_TITLEID`?) would happily match the boilerplate folder itself, showing it in
the project picker as if it were a real port. `_NON_PORT_DIR_NAMES` exists purely to special-case
that one folder name out of the results. The `exclude_dirs` parameter generalizes this for
callers whose `boilerplate_dir` lives under a differently-named folder.

## `_expand()`'s quote-stripping (real bug fixed in production use)

Early versions of `_expand()` only did `expanduser`/`expandvars` on the raw input. In practice,
users often paste a path copied from Finder or another editor with the quotes still attached
(`'/Volumes/Seagate/PSVITA Develop/soloader-boilerplate'`, literal quote characters included).
Without stripping those, `os.path.isdir(raw)` always failed — even for a path that genuinely
existed — because the literal string started with a `'` character that doesn't exist on disk.
`_expand()` now strips a single layer of surrounding single/double quotes and un-escapes
backslash-escaped spaces (the same cleanup `tui.clean_path_input()` already did elsewhere)
before expanding `~`/env vars.
