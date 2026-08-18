# `build_deploy.py` — Developer Notes

## Why build presets are auto-discovered instead of hardcoded

This wizard generalizes the various `build_and_install.sh` scripts that used to live in each
port's own `porting_tools/` (the most complete version was Advena's: pick a target, then a
preset, then deploy). The 4 universal presets (Debug, Release, RelWithDebInfo, MinSizeRel)
always apply to any port, but several ports also had engine-specific performance flags baked
into their own `build_and_install.sh` — Zenonia 4 alone had roughly 30 of them (NEON toggles,
dirty-rect optimizations, downsample ratios, and more). Hardcoding that list into this generic,
standalone toolkit would mean every port-specific flag leaks into every other port's menu,
whether or not it applies.

Instead, `_discover_extra_flags()` and `_flag_comment()` scan the *active project's own*
`build.sh` for `"$1" = "--xxx"` conditions and their attached comments, and `_choose_preset()`
lists whatever it finds alongside the 4 universal presets. A flag simply doesn't show up unless
that specific port's `build.sh` defines it — no per-engine special-casing lives in this toolkit
at all.

## Why `UNIVERSAL_PRESETS` stores i18n keys, not resolved text

`UNIVERSAL_PRESETS` is a module-level list, built once at import time — which happens before
`main.py` calls `cfgmod.ensure_global_config()` and fixes the active UI language. If the preset
descriptions were resolved with `t()` directly in that list literal, they'd be frozen in
whatever the default language is (Spanish) forever, regardless of what the user picks. Storing
the i18n *key* string instead, and resolving it with `t()` only inside `_choose_preset()` (which
runs well after the language is chosen), avoids that trap. This is a general gotcha worth
remembering: any module-level data structure built at import time that embeds translated text
needs the same treatment.
