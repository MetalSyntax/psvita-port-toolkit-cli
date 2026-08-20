# `crash_analyzer.py` — Developer Notes

## Why `_write_triage_summary()` exists, and why it's a grep, not a real cross-reference

This automates steps 4/6/7 of the `so-crash-triage` skill's manual procedure -- resolve the
crash address to a `.so` symbol name (already done by `SymbolTable.lookup()`), then grep the
project's own `decompiled/*/ghidra/*.c` (Ghidra pseudo-C) and `decompiled/apk_jadx/sources/`
(jadx Java) for that same name, so the developer doesn't have to run those two greps by hand for
every single crash the way the skill documents. Confirmed against a real port's decompiled
output (ILLUSIA-vita): searching for a symbol found on the crashing thread's resolved frames
correctly located its real call sites in `out_angr.c`.

This is deliberately a plain substring grep, not a real symbol-table/AST cross-reference --
Ghidra's pseudo-C output doesn't carry the kind of structured index this toolkit could query
precisely, and a plain grep against the *demangled* name (the same thing the skill's step 6 does
by hand: `grep -n "NombreDeLaFuncion" decompiled_so/out_ghidra.c`) already gets real hits in
practice. It can occasionally over- or under-match (a short/common symbol name can appear in
unrelated lines) -- the intent is to save the developer the two manual greps and point them at
the right file/line ballpark, not to replace reading the surrounding code.

## Why the JNI method name extraction is "best-effort", not a full JNI demangler

`_jni_method_name_from_symbol()` only undoes the two mangling rules that matter for turning a
`Java_...` symbol back into a *searchable* method name (the `_1`-escaped literal underscore, and
the `__`-prefixed overload signature suffix) -- it doesn't attempt to fully resolve package/class
nesting or JNI's `_0xxxx` Unicode escape, because the *only* thing this result is used for is a
substring grep against jadx sources, not a precise symbol resolution. A slightly-off guess still
usually finds the right file via the grep; a full demangler would be a lot more code for a result
consumed by nothing more exact than `if method_name in line`.

## Why the triage summary is per-dump (`<dump>.triage_summary.md`), not one shared file

The plan item this responds to named the output `triage_summary.md` generically, but this
codebase already has a per-dump sibling-file convention (`<dump_path>.analysis.txt` from
`analyze()`) -- a second dump analyzed later would silently overwrite a single shared
`triage_summary.md`, losing the previous crash's triage. `<dump_path>.triage_summary.md` keeps
one triage report per dump, consistent with the existing `.analysis.txt` pattern, so a `logs/`
folder full of old crash dumps keeps each one's triage alongside it.

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
