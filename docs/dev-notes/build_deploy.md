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

**Real gap found in practice (Prince of Persia Classic)**: this adopted, `build.sh`-less project
turned out to have a real `build_and_install.sh` before it was deleted (on the reasonable
assumption that this toolkit's generic fallback fully replaces it) that passed
`-DEMULATOR_BUILD=ON/OFF` (toggling hardware-only safety checks like `kubridge` presence, so the
same binary can run correctly on Vita3K vs. real hardware) and `-DENABLE_VERBOSE_LOG=ON` (without
which the game writes nothing to its log file at all, not even on a crash) depending on
interactive prompts. Losing `build.sh` meant losing the only place that knew to pass these.

Rather than hardcode `EMULATOR_BUILD`/`ENABLE_VERBOSE_LOG` (or any other project's specific
option names) into this generic toolkit, `_discover_cmake_options()` scans the project's own
`CMakeLists.txt` for the standard CMake `option(NAME "description" ON|OFF)` idiom -- which is
exactly how this project (and likely others) already declares these toggles -- and
`_prompt_cmake_options()` surfaces every one it finds as an interactive ON/OFF prompt (Enter
keeps the CMakeLists.txt-declared default) before running `cmake`. This is fully generic: any
project using the standard `option()` pattern gets its toggles exposed automatically, with zero
per-project special-casing, extending the same "auto-discover, don't hardcode" principle already
used for `build.sh`-based extra flags to the `build.sh`-less fallback path too.

**Real bug hit in practice (space-in-path, `vita-pack-vpk`)**: the first version of this fallback
ran `cmake`/`make` directly inside `<project_dir>/<build_dir>/` -- fine for `cmake`/`make`
themselves, but `vita-pack-vpk` (invoked by the Vita CMake toolchain's custom `*-vpk` build step)
cannot handle a working directory whose absolute path contains a space, and failed with
`Error creating: 'Develop/Prince': Failure to create temporary file` on a project living under
`/Volumes/Seagate/PSVITA Develop/Prince of Persia /` -- the exact historical gotcha the original
per-project `build_and_install.sh` scripts always dodged by staging the build entirely under
`/tmp`. `_stage_in_tmp()`/`_copy_build_outputs()` now replicate that: `rsync` the source (minus
`.git`/dotfiles/the build dir itself) into a fresh space-free `/tmp` directory, configure and
build entirely there, then copy just the `.vpk`/`eboot.bin`/`.velf`/`.self`/raw-ELF outputs back
into the project's real `build_dir` afterward. This means every build reconfigures from scratch
(no CMake cache reuse across runs) -- a real cost, but correctness beats speed here, and it's the
only way to guarantee this works regardless of where under `BASE_DIR` a project happens to live.

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
