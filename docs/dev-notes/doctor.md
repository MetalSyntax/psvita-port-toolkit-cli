# `doctor.py` — Developer Notes

## Why OK/WARN/FAIL, not just pass/fail

Some dependencies are load-bearing for basically everything (`cmake`, the VITASDK toolchain
binaries, Pillow) and their absence should read as a hard stop. Others are genuinely optional
today (`ninja` — the fallback path builds fine with plain `make`; `deep-translator` — only used
by one doc-translation utility; `psp2cgc`/`cgc` — nothing in the toolkit calls it yet, this is
laying groundwork for the shader-validation pipeline). Collapsing all of that into a single
pass/fail would either cry wolf on a fresh machine that will never touch shader validation, or
hide a genuinely broken VITASDK install behind a wall of unrelated warnings. Three levels let
the summary line answer "can I actually use this toolkit right now" at a glance.

## Why every check function returns instead of printing directly

`_check_config_paths()`, `_check_toolchain()`, etc. all build and return plain
`(name, status, detail)` tuples instead of printing as they go. This keeps `run_checks()` pure
(no side effects, easy to call from a script/test) and lets `print_report()` be the single place
that knows about color/icons/i18n — including the `--plain` flag the CLI needs for log files
where ANSI escape codes would just show up as garbage.

## Why this module doesn't hard-depend on any other module at import time

`doctor.py` is meant to be the first thing that runs on a new machine, often before
`~/.psvita-toolkit/config.json` even exists. `_check_config_paths()` imports `config` lazily
(inside the function, not at module top) specifically so `import doctor` alone never risks a
cycle or an early failure — every other check function is entirely self-contained (`shutil`,
`subprocess`, `importlib`), so a `doctor` run degrades gracefully instead of crashing before it
can tell you what's wrong.

## Why Docker/jadx/`psp2cgc` detection isn't shared with `init_port.py`'s `check_prereqs()`

`init_port.py` already has `_have()`/`_have_docker_image()`/`check_prereqs()`, scoped narrowly to
what the new-port wizard needs (jadx + `devrvk/so-decompiler`) and printed inline as part of that
wizard's flow. `doctor.py` deliberately doesn't import and reuse those — it needs a much wider
check list (VITASDK toolchain binaries, CMake/Ninja, Python packages, `vita-parse-core`) that
doesn't belong conceptually inside `init_port.py`, and it needs to run standalone, without a
project or the wizard's context. Some duplication (docker/jadx presence checks exist in both
places) is the accepted cost of keeping `init_port.py`'s prereq check focused on exactly its own
narrow use case.
