# `auto_synth.py` — Developer Notes

## Why this is an "assisted" loop, not the plan's fully autonomous one

The plan described a loop that "injects the missing hook or stub and retries" against Vita3K
until the game renders. Vita3K isn't usable for this project's ports at all (see
`build_deploy.py`'s removal of it), which already rules out the literal design -- but even
retargeting the same idea at the real console, the deeper issue is that
`jni_analyzer.generate_jni_stubs()`/`so_patcher.generate_telemetry_stubs()` produce REVIEWABLE
CANDIDATES, not verified fixes (both modules' own docstrings say so explicitly). Silently trusting
a candidate stub as "the fix" and looping unattended would misrepresent what those tools actually
guarantee. This loop regenerates candidates and retries, but treats "did that actually help" as
an open question the NEXT iteration's crash signature answers -- not something it assumes.

## Why "new dump" (filename) and "same crash" (signature) are two separate checks

A crash dump's filename is virtually always unique (timestamped) whether or not the underlying
bug is the same one as before -- so filename comparison can only ever answer "did anything happen
since I last checked", not "is this the same bug". Reading back the crashing symbol from
`crash_analyzer.analyze()`'s own `<dump>.triage_summary.md` (via
`context_feeder._parse_triage_summary()`, already written for exactly this data) is what actually
lets `_crash_signature()` tell "a fresh, different crash" apart from "the same one, unresolved".
Collapsing these into one filename-only check was an earlier version of this module's logic and
was WRONG in a specific, verified way: a stale crash dump already sitting on the console before
the loop even started would get misread as "the same crash repeating" on the very first
iteration, when it actually meant nothing had changed yet. `last_seen_dump` (filename) and
`last_signature` (crash symbol) are kept as two independent pieces of state so that mistake can't
recur.

## Why `last_signature` starts at `None` and a `None` signature is never treated as a match

`crash_analyzer.analyze()` can fail to resolve a symbol (stripped binary, an address outside any
known `.so` range, etc.), in which case `_crash_signature()` returns `None`. If two DIFFERENT,
unrelated crashes both failed to resolve a symbol, comparing `None == None` would wrongly report
"no progress" and stop the loop on a coincidence rather than a real repeat. The check is
explicitly `signature is not None and signature == last_signature` -- an unresolved crash is
always treated as "new, keep trying", never silently equated with a previous unresolved one.

## Why `_wait_and_check_for_crash()` polls a few times instead of sleeping the whole window

The original version slept the FULL `run_seconds` before checking even once -- correct, but it
means every iteration pays the whole wait even when the crash (or the "looks stable" outcome)
was knowable much earlier. Polling a small, fixed number of times (`checks=3` by default, each
interval floored at 5s) gets most of that time back on the common case without hammering
VitaShell's ftpd with rapid reconnect attempts -- `ftp_ops.py` already documents that server
occasionally refusing a connection made right after a previous one just closed, which is
specifically the failure mode "just poll every second" would risk.

## Why `_check_stubs_wired_into_build()` is a loose text search, not a real CMake parse

Confirming precisely that a `file(GLOB ...)` call's pattern argument matches
`<project_dir>/source/*.c` would need an actual CMake parser -- overkill for a check whose two
possible wrong answers are both harmless: a false "yes" just means the porter double-checks
something that was already fine, and a false "no" means they add a `CMakeLists.txt` line that
turns out to have already been unnecessary. Searching the whole file for the `GLOB`/`GLOB_RECURSE`
keyword and the literal `source/*.c` pattern, independently, catches the common case honestly
without pretending to verify the exact call structure. An earlier version of this check
required BOTH to appear inside what looked like a single `GLOB(...)`-shaped call -- a
misunderstanding of CMake's actual syntax (the parenthesis belongs to the surrounding `file(...)`
command, not to `GLOB` itself), caught by testing it against a real `file(GLOB ...)` line and
watching it wrongly report `False`.

## Why the VPK is uploaded fully only on iteration 1, then just `eboot.bin`

The console needs the VPK installed (LiveArea, save data paths, etc.) at least once; every
iteration after that only changed the executable, so re-uploading just `eboot.bin` (the same
"fast iterate" path the interactive menu already offers) keeps each retry's turnaround time close
to the actual build time instead of re-transferring the whole package every attempt.
