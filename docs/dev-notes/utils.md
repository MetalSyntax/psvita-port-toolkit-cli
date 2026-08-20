# `utils.py` — Developer Notes

## Why the shader profiles are `sce_vp_psp2`/`sce_fp_psp2`, not `vs_2_0`/`fs_2_0`

The plan item this responds to listed both naming schemes together ("vs_2_0 / sce_vp_psp2" and
"fs_2_0 / sce_fp_psp2"), which reads as the author being unsure which was correct. `vs_2_0`/`ps_2_0`
are NVIDIA Cg's DirectX 9 profile names -- they exist in generic Cg compiler documentation, but
`psp2cgc` (VITASDK's Vita-specific Cg compiler front-end) targets Sony's own PS Vita GPU profiles,
`sce_vp_psp2` (vertex) and `sce_fp_psp2` (fragment). Only these two are used here; the DirectX
names would either fail outright or silently target the wrong thing.

## Why shader validation reports "couldn't check" separately from "checked, failed"

`validate_shader()` returns `(None, message)` when no `psp2cgc`/`cgc` binary is found at all, but
`(False, message)` when the compiler ran and rejected the shader. Collapsing those into a single
"not ok" would make a missing compiler on the developer's machine look identical to a real syntax
error in the shader -- the fix for one is "install psp2cgc", the fix for the other is "read the
compiler's own error line and edit the .cg file". `validate_all_shaders()` checks for the
compiler once up front (not per-file) specifically so a missing compiler prints one clear message
instead of one confusing "failed" line per shader.

## Why the uniform/sampler extractor is a plain regex over dumped GLSL, not a real parser

`extract_shader_uniforms()` only needs to find top-level `uniform <type> <name>[N];`
declarations -- it never needs to understand shader control flow, expressions, or preprocessor
directives, so a regex over the dumped `.glsl` text is enough and avoids pulling in (or writing) a
real GLSL parser for a one-shot skeleton-generation tool. `generate_uniform_skeletons()` is
explicit in its own output that the result needs confirming against the engine's actual
`glGetUniformLocation`/`glUniform*` call sites -- this generates a starting point (names, C types,
array sizes), not a verified-correct binding layout, the same "confirm before trusting" spirit as
`init_port.py`'s auto-detected `PORTING_PLAN.md` fields.

## Why `search_symbols()` replaces `ai_bash_commands.sh`

The original `ai_bash_commands.sh` script (used during crash triage on Dungeon Hunter 2 and
Advena) was a cheatsheet of specific `objdump`/`readelf` invocations with hardcoded memory
offsets and symbol names for ONE particular binary at a time -- useful during that specific
debugging session, useless for any other port. `search_symbols()` generalizes the reusable part
of that workflow: search a port's `.so` dynamic symbols by an arbitrary regex pattern, without
assuming anything about which symbols or offsets exist. The actual offset-hunting (via
disassembly around a crash address) is handled separately by `crash_analyzer.py`.

## Why `translate_shaders_boilerplate()` is explicitly not a real translator

Early experiments tried mechanically translating GLSL shaders to Cg (the format PS Vita's GPU
toolchain expects). It turned out to only reliably handle the boilerplate parts -- stripping
`precision` qualifiers and Android-only macros -- while the actual semantic translation (moving
`attribute`/`varying` into Cg's parameter-passing convention, converting `gl_FragColor` into a
`float4 main() : COLOR` signature, relocating uniforms) is different enough per engine that no
generic mechanical pass covers it reliably. Keeping the function honest about this (it only
does cleanup, not translation) avoids someone trusting its output as shader-complete.
