# `crash_analyzer.py` — Developer Notes

## Why this wraps `vita-parse-core` instead of reimplementing dump parsing

Parsing a PS Vita core dump (`.psp2dmp`/`psp2core-*`) correctly means understanding the dump's
binary format, the loaded-module table, and per-thread register/stack layout — all of which
`vita-parse-core` already implements and maintains. Reimplementing that here would duplicate a
nontrivial amount of low-level, Vita-specific parsing logic for no real benefit; this module's
job is the *port-specific* half of the problem instead: correlating the parsed dump against a
particular port's own ELF and the original Android `.so`; the crash-dump parsing plumbing itself
is delegated to `vita-parse-core`, imported dynamically via `global_cfg['vita_parse_core_dir']`.

## Why the `.so` load base is auto-detected instead of assumed fixed

Different games' loaders can end up placing the original Android `.so` at different addresses
in the Vita's `0x80000000`-`0x9fffffff` user-mapping window, so a single hardcoded base doesn't
reliably work across ports. `_auto_detect_so_base()` instead treats every address found on the
crashed thread's registers and stack as a *candidate* clue, and cross-references each one
against the `.so`'s own known symbol offsets — an address that happens to fall within a real
function's `[start, size]` span, once you subtract a candidate base, casts a vote for that base.
The most-voted candidate wins. This is a voting heuristic rather than a guarantee, which is why
`analyze()` still accepts a manual `--so-base` override for the cases where auto-detection picks
wrong (e.g. very few stack addresses actually land inside the `.so`).
