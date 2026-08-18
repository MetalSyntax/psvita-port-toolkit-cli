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

## Why `_run_build()` falls back to plain `cmake`+`make`

Not every port under `BASE_DIR` was created by this toolkit's `init_port.py` (which clones
`soloader-boilerplate`, and that scaffold ships a root `build.sh`). A port adopted from before
this toolkit existed — one that was always built by hand with `cmake . && make` directly, with
no wrapper script ever written — has no `build.sh` at all, and the wizard used to just fail with
"couldn't find build.sh" for those projects, even though building them is perfectly possible.

`_run_build()` now distinguishes two cases: `build.sh` **exists but isn't executable** (a real
problem, likely a lost `chmod +x` — still reported as an error), versus `build.sh` **doesn't
exist at all** (a legacy/adopted project), which falls back to `_run_cmake_direct()`: run
`cmake <project_dir>` then `make -jN` directly inside the project's `build_dir`, reusing
whatever CMake cache is already there if the project was previously built manually in that same
folder. The preset still maps to `-DCMAKE_BUILD_TYPE=...` in this path; project-specific extra
flags (auto-discovered from `build.sh` for boilerplate-based ports) simply don't exist for these
projects, since there's no `build.sh` to scan.

**Real bug hit in practice**: the first version of this fallback ran `cmake`/`make` with the
toolkit's own inherited environment, unchanged. That's missing exactly what every hand-written
`build.sh` normally does at its top -- `export VITASDK=...; export PATH="$VITASDK/bin:$PATH"`.
Without it, `make` fails partway through with `vita-libs-gen: command not found` (and would hit
the same wall later on `vita-elf-create`/`vita-make-fself`/`vita-mksfoex`/`vita-pack-vpk`, all of
which live in `$VITASDK/bin/` and get invoked by bare name from the Vita CMake toolchain's custom
build steps). `_vitasdk_env()` now builds the subprocess environment explicitly from
`global_cfg["vitasdk"]` before running either command. This came up specifically because a user
deleted a legacy project's `build.sh` on the assumption that this toolkit's generic fallback
fully replaces it -- which is the right assumption, but meant the fallback had to actually be a
complete replacement, not just the `cmake`/`make` calls alone.

## Why `UNIVERSAL_PRESETS` stores i18n keys, not resolved text

`UNIVERSAL_PRESETS` is a module-level list, built once at import time — which happens before
`main.py` calls `cfgmod.ensure_global_config()` and fixes the active UI language. If the preset
descriptions were resolved with `t()` directly in that list literal, they'd be frozen in
whatever the default language is (Spanish) forever, regardless of what the user picks. Storing
the i18n *key* string instead, and resolving it with `t()` only inside `_choose_preset()` (which
runs well after the language is chosen), avoids that trap. This is a general gotcha worth
remembering: any module-level data structure built at import time that embeds translated text
needs the same treatment.
