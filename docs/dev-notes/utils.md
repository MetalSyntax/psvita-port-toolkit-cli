# `utils.py` — Developer Notes

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
