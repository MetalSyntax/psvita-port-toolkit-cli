# `shader_transpiler.py` — Developer Notes

## Why this pipes shaders through `glslangValidator`/`spirv-cross` instead of a hand-rolled parser

The plan this responds to explicitly named `glslang`/`SPIRV-Cross` as the AST tooling to use, and
building a from-scratch GLSL grammar has enough real edge cases (implicit conversions, overload
resolution, matrix majorness) that a hand-rolled version risks silently mis-translating a shader
into something that still compiles but renders wrong -- a failure mode strictly worse than
refusing to translate it. Piping through the real tools moved the actually error-prone semantic
work (e.g. correcting `mul()`'s argument order for GLSL's column-major convention) onto code that
already gets it right, leaving this module with a narrow, mechanical, low-risk touch-up pass.

## Why there's a whole ES 1.00 -> ES 3.10 preprocessing step before `glslangValidator` ever runs

This was NOT part of the original design -- it was discovered by actually running
`glslangValidator` against a real `attribute`/`varying`-style shader while building this module,
not assumed from documentation. `glslangValidator -V` (needed to emit SPIR-V at all) flatly
refuses GLSL older than ES 3.10: SPIR-V's execution model has no equivalent for `attribute`/
`varying`/`gl_FragColor`, so they're rejected outright regardless of `#version`. Since GLSL ES
1.00 (GLES 2.0) is exactly the dialect real mobile game shaders use -- the plan's own stated
target -- skipping this step would mean the pipeline compiles nothing real. `-R` (relaxed Vulkan
verification) was a second, equally real finding: without it, glslang rejects loose non-opaque
uniforms, which is how every ES 1.00 shader declares them. Every rewrite in
`preprocess_es100_to_es310()` mirrors Khronos's own published ES 1.00 -> ES 3.00+ migration
guidance -- nothing here is invented syntax.

## Why the `gl_HalfPixel` lines get stripped from the vertex shader output

Also found by running the real pipeline, not assumed: SPIRV-Cross's Shader-Model-3 HLSL backend
adds a Direct3D9-specific half-pixel rasterization correction to every vertex shader's output
position. That correction exists because of a real, well-known D3D9 quirk (pixel centers at
integer coordinates instead of half-integer) that PS Vita's GXM has no equivalent of. Leaving it
in wouldn't just be dead code -- it would apply an actual, incorrect positional offset to every
vertex. `_HALFPIXEL_LINE_RE` removes both the `gl_HalfPixel` uniform declaration and the two
lines that reference it.

## Why uniform names get "unmangled" back from `_16_u_mvp` to `u_mvp`

SPIRV-Cross's HLSL backend numbers members of the synthetic uniform block it wraps loose GLSL
uniforms into. Left as-is, the generated Cg would still compile fine, but a porter trying to
figure out which `cgGLSetParameter`-equivalent call sets which uniform would have to guess that
`_16_u_mvp` means the shader's `u_mvp`. `_MANGLED_NAME_RE` strips the numeric prefix mechanically
-- safe because SPIRV-Cross's mangling pattern is consistent and the resulting name is exactly
what a human already expects to see.

## Why `psp2cgc` validation still can't be verified by this module's own tests

`psp2cgc` is proprietary Sony VITASDK tooling -- this development environment doesn't have it,
so while `glslangValidator`+`spirv-cross` were installed and run for real while building this
(catching both findings above), the LAST hop of the pipeline (does a real `psp2cgc` accept the
resulting Cg) has not itself been verified against the real compiler. `transpile_shader()`
surfaces `utils.validate_shader()`'s three-way result (`True`/`False`/`None`) rather than
collapsing "couldn't check" and "checked, rejected" into one message -- a porter with VITASDK
installed gets a real answer; one without it gets an honest "couldn't check", not a false
"rejected".
