"""!
@file shader_transpiler.py
@brief AST-based GLSL ES -> Cg shader transpiler, built on real Khronos
       tooling instead of a hand-rolled GLSL grammar.

@details
The plan this responds to explicitly named `glslang`/`SPIRV-Cross` as the
kind of AST parser to use -- so this pipes a shader through the REAL ones
instead of writing a from-scratch GLSL parser+codegen (a full GLSL grammar
has enough edge cases -- preprocessor macros, overload resolution, implicit
conversions -- that a hand-rolled version risks silently mis-translating a
shader into something that compiles but renders wrong, which is worse than
refusing to translate it at all):

    GLSL ES 1.00 --[_preprocess_es100_to_es310(), this module]--> GLSL ES 3.10
    --[glslangValidator -V -R]--> SPIR-V --[spirv-cross --hlsl --shader-model 30]-->
    HLSL (Shader Model 3, legacy combined sampler2D/tex2D() style)
    --[hlsl_sm3_to_cg(), this module]--> Cg

Two real, tool-verified findings shaped this pipeline (confirmed by actually
running both tools against real GLSL ES 1.00 shaders while building this,
not assumed from documentation):

1. `glslangValidator` flatly REFUSES to emit SPIR-V for GLSL ES older than
   3.10 -- `attribute`/`varying`/`gl_FragColor`/`texture2D()` are ES 1.00/2.0
   constructs SPIR-V's execution model has no equivalent for, so they're
   rejected outright, version pragma or not. Since ES 1.00 (GLES 2.0) is
   exactly the dialect real mobile game shaders use (the plan's own stated
   target), `_preprocess_es100_to_es310()` runs a well-documented, purely
   mechanical ES 1.00 -> ES 3.00+ migration first (this is the same rewrite
   Khronos's own migration guides describe, not an invented shortcut):
   `attribute` -> `in`; `varying` -> `out` (vertex) / `in` (fragment);
   `gl_FragColor` -> an explicit declared `out vec4` (removed in ES 3.00);
   `texture2D()`/`textureCube()`/etc. -> unified `texture()`. `-R` (relaxed
   Vulkan rules) is also required -- without it, glslang rejects loose
   (non-block) non-opaque uniforms, which is how every real ES 1.00 shader
   declares them.
2. SPIRV-Cross's Shader-Model-3 HLSL backend appends a Direct3D9-specific
   half-pixel rasterization correction (`gl_HalfPixel`) to vertex shaders --
   a real, well-known D3D9 quirk that has NO equivalent on PS Vita's GXM and
   would introduce an actual, incorrect pixel offset if left in. It also
   name-mangles uniform-block members with a numeric prefix
   (`_16_u_mvp`), which `hlsl_sm3_to_cg()` strips back to the original name
   so the result is still readable against the porter's own C-side
   uniform-setting code.

What's genuinely low-risk (and why this approach beats a hand-rolled
parser): SPIRV-Cross gets the actually error-prone semantic work right
automatically -- e.g. it emits `mul(vector, matrix)`, not
`mul(matrix, vector)`, correctly compensating for GLSL's column-major
convention, exactly the "column-major vs row-major" gotcha the plan calls
out as historically easy to get wrong by hand. `--shader-model 30` also
keeps SPIRV-Cross's HLSL backend on the OLD `POSITION`/`COLOR0`/`TEXCOORD0`
semantics instead of SM4+'s `SV_Position`/`SV_Target`, which happen to be
EXACTLY Cg's own semantic names (Cg and early HLSL were co-developed by
NVIDIA/Microsoft and share the same intrinsics for this shader-model era --
`mul()`, `tex2D()`, `lerp()`, `saturate()`). What's left for
`hlsl_sm3_to_cg()` to adapt is narrow and mechanical: unwrap `cbuffer`
blocks into flat `uniform` declarations (Cg has no buffer-block concept),
strip `: register(...)`/`: packoffset(...)` annotations and `row_major`/
`column_major` qualifiers Cg's compiler doesn't know, strip the
`gl_HalfPixel` D3D9 shim, and undo the numeric name-mangling.

Every output is then validated for real with `psp2cgc` via
`utils.validate_shader()` -- the same compiler this toolkit already uses to
validate hand-translated shaders -- so a mistake in either the real tools'
output or this module's touch-up pass surfaces as a compile error, not a
silently wrong `.gxp`. (This module's own test suite of real shaders was
verified through `glslangValidator`+`spirv-cross` directly; `psp2cgc`
itself is proprietary Sony VITASDK tooling this development environment
doesn't have, so the LAST hop -- does a real `psp2cgc` accept the resulting
Cg -- still needs to be confirmed by the porter's own run, same honesty
posture as everywhere else in this toolkit.)

Both `glslangValidator` and `spirv-cross` are real, independently
downloadable, widely-used open-source tools (Khronos Group) -- never
bundled with this toolkit. See `docs/dev-notes/shader_transpiler.md`.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import i18n
from . import tui
from . import utils
from .i18n import t
from .tui import C

STRINGS = {
    "shader_transpiler.menu_title": {
        "es": "Transpilador Semántico GLSL -> CG (AST)",
        "en": "Semantic GLSL -> CG Transpiler (AST)",
        "pt": "Transpilador Semântico GLSL -> CG (AST)",
    },
    "shader_transpiler.menu_transpile": {
        "es": "Transpilar shaders .glsl volcados (glsl_dump/ -> assets/cg/)",
        "en": "Transpile dumped .glsl shaders (glsl_dump/ -> assets/cg/)",
        "pt": "Transpilar shaders .glsl despejados (glsl_dump/ -> assets/cg/)",
    },
    "shader_transpiler.tools_missing": {
        "es": "[!] Falta glslangValidator y/o spirv-cross -- ver 'Doctor'. No se puede transpilar (la limpieza por regex de utils.py sigue disponible).",
        "en": "[!] glslangValidator and/or spirv-cross missing -- see 'Doctor'. Can't transpile (utils.py's regex cleanup is still available).",
        "pt": "[!] Falta glslangValidator e/ou spirv-cross -- veja 'Doctor'. Não é possível transpilar (a limpeza por regex de utils.py continua disponível).",
    },
    "shader_transpiler.no_dump_dir": {
        "es": "[-] No existe {dump_dir} -- descargá los shaders volcados primero.",
        "en": "[-] {dump_dir} doesn't exist -- download the dumped shaders first.",
        "pt": "[-] {dump_dir} não existe -- baixe os shaders despejados primeiro.",
    },
    "shader_transpiler.no_glsl_found": {
        "es": "[-] No había .glsl en {dump_dir}.",
        "en": "[-] There were no .glsl files in {dump_dir}.",
        "pt": "[-] Não havia .glsl em {dump_dir}.",
    },
    "shader_transpiler.unknown_stage": {
        "es": "no se pudo inferir vertex/fragment del nombre -- renombralo con un hint (_vs/_fs) o pasá stage explícito",
        "en": "couldn't infer vertex/fragment from the filename -- rename with a hint (_vs/_fs) or pass an explicit stage",
        "pt": "não foi possível inferir vertex/fragment pelo nome -- renomeie com uma dica (_vs/_fs) ou passe o stage explícito",
    },
    "shader_transpiler.transpiled": {
        "es": "  [+] {name} -> {out_name} ({stage})",
        "en": "  [+] {name} -> {out_name} ({stage})",
        "pt": "  [+] {name} -> {out_name} ({stage})",
    },
    "shader_transpiler.transpile_failed": {
        "es": "  [-] {name}: {error}",
        "en": "  [-] {name}: {error}",
        "pt": "  [-] {name}: {error}",
    },
    "shader_transpiler.validate_ok": {
        "es": "      {color_green}validado con psp2cgc OK{color_reset}",
        "en": "      {color_green}validated with psp2cgc OK{color_reset}",
        "pt": "      {color_green}validado com psp2cgc OK{color_reset}",
    },
    "shader_transpiler.validate_failed": {
        "es": "      {color_yellow}psp2cgc rechazó el .cg generado -- revisar a mano: {message}{color_reset}",
        "en": "      {color_yellow}psp2cgc rejected the generated .cg -- needs manual review: {message}{color_reset}",
        "pt": "      {color_yellow}psp2cgc rejeitou o .cg gerado -- revisar manualmente: {message}{color_reset}",
    },
    "shader_transpiler.validate_unavailable": {
        "es": "      {color_yellow}no se pudo validar: {message}{color_reset}",
        "en": "      {color_yellow}couldn't validate: {message}{color_reset}",
        "pt": "      {color_yellow}não foi possível validar: {message}{color_reset}",
    },
    "shader_transpiler.summary": {
        "es": "[*] {ok} transpilado(s), {failed} fallido(s).",
        "en": "[*] {ok} transpiled, {failed} failed.",
        "pt": "[*] {ok} transpilado(s), {failed} falharam.",
    },
}
i18n.register(STRINGS)

_VERTEX_NAME_HINTS = ("vert", "_vs", "vs_", "vp_", "-vs", "-vp")
_FRAGMENT_NAME_HINTS = ("frag", "_fs", "fs_", "fp_", "-fs", "-fp")

# Same PS Vita Cg profiles utils.py's own psp2cgc validation already uses --
# duplicated locally rather than importing utils's private constants, same
# convention as _find_primary_so() across this codebase.
_VERTEX_PROFILE = "sce_vp_psp2"
_FRAGMENT_PROFILE = "sce_fp_psp2"


def find_glslang_validator():
    """!
    @brief Look for `glslangValidator` on `PATH` (e.g. `brew install glslang`).
    @return Full path if found, `None` otherwise.
    """
    return shutil.which("glslangValidator")


def find_spirv_cross():
    """!
    @brief Look for `spirv-cross` on `PATH` (e.g. `brew install spirv-cross`).
    @return Full path if found, `None` otherwise.
    """
    return shutil.which("spirv-cross")


def _infer_stage(glsl_path):
    """!
    @brief Guess whether a `.glsl` file is a vertex or fragment shader, from
           its filename -- same hint convention as `utils.guess_shader_profile()`.
    @param glsl_path Path to the `.glsl` file.
    @return `"vert"`, `"frag"`, or `None` if the filename gives no hint.
    """
    name = Path(glsl_path).stem.lower()
    if any(hint in name for hint in _VERTEX_NAME_HINTS):
        return "vert"
    if any(hint in name for hint in _FRAGMENT_NAME_HINTS):
        return "frag"
    return None


# ---------------------------------------------------------------------------
# GLSL ES 1.00 -> ES 3.10 preprocessing (required for glslangValidator -V --
# see the module docstring's finding #1)
# ---------------------------------------------------------------------------

_VERSION_LINE_RE = re.compile(r'^\s*#version\s+\d+\s*(es)?\s*\n', re.M)
_PRECISION_LINE_RE = re.compile(r'^[ \t]*precision\s+\w+\s+\w+\s*;[ \t]*\n', re.M)
_ATTRIBUTE_RE = re.compile(r'\battribute\b')
_VARYING_RE = re.compile(r'\bvarying\b')
_LEGACY_TEXTURE_FN_RE = re.compile(r'\b(texture2DProj|texture2DLod|texture2D|textureCubeLod|textureCube)\s*\(')
_LEGACY_TEXTURE_FN_MAP = {
    "texture2D": "texture", "texture2DProj": "textureProj", "texture2DLod": "textureLod",
    "textureCube": "texture", "textureCubeLod": "textureLod",
}


def preprocess_es100_to_es310(text, stage):
    """!
    @brief Mechanically migrate GLSL ES 1.00 (GLES 2.0, the dialect real
           mobile game shaders actually use) to the ES 3.10 subset
           `glslangValidator -V` requires to emit SPIR-V at all.
    @details Every rewrite here is the same one Khronos's own ES 1.00 -> ES
           3.00+ migration guidance describes -- nothing invented. See
           finding #1 in the module docstring for why this step exists.
    @param text Original `.glsl` source text (ES 1.00 style, or already
           ES 3.00+ -- rewrites are no-ops on constructs not present).
    @param stage `"vert"` or `"frag"`.
    @return ES 3.10-compatible GLSL source text, `#version 310 es` prefixed.
    """
    text = _VERSION_LINE_RE.sub("", text)
    precision_lines = [ln.rstrip("\n") for ln in _PRECISION_LINE_RE.findall(text)]
    text = _PRECISION_LINE_RE.sub("", text)

    if stage == "vert":
        text = _ATTRIBUTE_RE.sub("in", text)
        text = _VARYING_RE.sub("out", text)
    else:
        text = _VARYING_RE.sub("in", text)
        if "gl_FragColor" in text:
            text = text.replace("gl_FragColor", "fragColor")
            text = "out vec4 fragColor;\n" + text
        text = _LEGACY_TEXTURE_FN_RE.sub(lambda m: _LEGACY_TEXTURE_FN_MAP[m.group(1)] + "(", text)

    header = "#version 310 es\n"
    if precision_lines:
        header += "\n".join(precision_lines) + "\n"
    elif stage == "frag":
        header += "precision mediump float;\n"  # ES fragment shaders have no default float precision
    return header + text


# ---------------------------------------------------------------------------
# HLSL (Shader Model 3) -> Cg touch-up pass
# ---------------------------------------------------------------------------

_CBUFFER_RE = re.compile(r'cbuffer\s+\w+(?:\s*:\s*register\([^)]*\))?\s*\{([^}]*)\}\s*;?', re.S)
_ANNOTATION_RE = re.compile(r'\s*:\s*(register|packoffset)\([^)]*\)')
_ROWCOL_MAJOR_RE = re.compile(r'\b(row_major|column_major)\s+')
_PRAGMA_LINE_RE = re.compile(r'^\s*#pragma.*$\n?', re.M)
# SPIRV-Cross's Direct3D9-only half-pixel rasterization correction -- no GXM
# equivalent, and leaving it in would introduce a real, incorrect pixel
# offset on PS Vita. See finding #2 in the module docstring.
_HALFPIXEL_LINE_RE = re.compile(r'^.*\bgl_HalfPixel\b.*$\n?', re.M)
# SPIRV-Cross prefixes uniform-block members it reflects with a numeric ID
# (e.g. "_16_u_mvp") -- stripped back to the original name so the result is
# still readable against the porter's own C-side uniform-setting code.
_MANGLED_NAME_RE = re.compile(r'\b_\d+_(\w+)\b')


def _unwrap_cbuffers(hlsl_text):
    """!
    @brief Flatten every `cbuffer NAME { ... }` block into loose `uniform`
           declarations -- Cg has no buffer-block concept, everything is a
           flat uniform parameter.
    @param hlsl_text HLSL source text.
    @return Text with every `cbuffer` block (and its closing `;`) replaced
            by its members' declarations, each prefixed `uniform` if not
            already.
    """
    def _replace(m):
        body = m.group(1)
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        out = []
        for ln in lines:
            out.append(ln if ln.startswith("uniform") else f"uniform {ln}")
        return "\n".join(out)
    return _CBUFFER_RE.sub(_replace, hlsl_text)


def hlsl_sm3_to_cg(hlsl_text):
    """!
    @brief Adapt SPIRV-Cross's Shader-Model-3 HLSL output into Cg.
    @details See the module docstring's findings #1/#2 and the "what's
           genuinely low-risk" paragraph for why this stays a small,
           mechanical set of transforms rather than a full re-parse:
           semantics (`POSITION`/`COLOR0`/`TEXCOORD0`), intrinsics
           (`mul()`/`tex2D()`/`lerp()`), and struct-based entry points are
           already shared between SM3 HLSL and Cg at this shader-model era.
    @param hlsl_text `spirv-cross --hlsl --shader-model 30` output.
    @return Cg source text, with a header comment noting the pipeline that
           produced it.
    """
    text = _unwrap_cbuffers(hlsl_text)
    text = _ANNOTATION_RE.sub("", text)
    text = _ROWCOL_MAJOR_RE.sub("", text)
    text = _PRAGMA_LINE_RE.sub("", text)
    text = _HALFPIXEL_LINE_RE.sub("", text)
    text = _MANGLED_NAME_RE.sub(lambda m: m.group(1), text)
    header = (
        "// Auto-generated by psvita-toolkit's shader_transpiler.py.\n"
        "// Pipeline: GLSL ES 1.00 -> ES 3.10 -> glslangValidator (SPIR-V) -> spirv-cross\n"
        "// (HLSL, Shader Model 3) -> this touch-up pass -> Cg. See\n"
        "// docs/dev-notes/shader_transpiler.md for the two real tool-verified findings that\n"
        "// shaped this pipeline. Validated below with psp2cgc; if that failed, the mismatch\n"
        "// is flagged in this run's output -- read it before trusting this file.\n\n"
    )
    return header + text


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

def transpile_shader(glsl_path, dest_dir, global_cfg=None, stage=None):
    """!
    @brief Run one `.glsl` file through the full GLSL -> SPIR-V -> HLSL(SM3)
           -> Cg pipeline and validate the result with `psp2cgc`.
    @param glsl_path Path to the source `.glsl` file.
    @param dest_dir Directory to write the resulting `.cg` file into.
    @param global_cfg Global config dict (used to locate `psp2cgc` for validation).
    @param stage `"vert"`/`"frag"`, or `None` to infer from the filename via `_infer_stage()`.
    @return `(out_path, validated_ok, message)` on success -- `out_path` is
           the written `.cg` file; `validated_ok`/`message` are
           `utils.validate_shader()`'s own three-way result verbatim:
           `True` (compiled clean), `False` + a rejection message (compiled,
           psp2cgc rejected it), or `None` + an explanation (psp2cgc isn't
           installed, so this couldn't be checked at all -- distinct from a
           rejection). Raises `RuntimeError` with a human-readable reason on
           failure (missing tools, unrecognized stage, or either external
           tool erroring -- the caller, `transpile_shaders_dir()`, catches
           this per-file).
    """
    glslang = find_glslang_validator()
    spirv_cross = find_spirv_cross()
    if not glslang or not spirv_cross:
        raise RuntimeError(t("shader_transpiler.tools_missing"))

    stage = stage or _infer_stage(glsl_path)
    if stage not in ("vert", "frag"):
        raise RuntimeError(t("shader_transpiler.unknown_stage"))

    original_text = Path(glsl_path).read_text(encoding="utf-8", errors="ignore")
    es310_text = preprocess_es100_to_es310(original_text, stage)

    with tempfile.TemporaryDirectory() as tmp:
        preprocessed_path = Path(tmp) / f"input.{stage}"
        preprocessed_path.write_text(es310_text, encoding="utf-8")

        spv_path = Path(tmp) / "out.spv"
        r1 = subprocess.run(
            [glslang, "-V", "-R", "-S", stage, "--auto-map-bindings", "--auto-map-locations",
             "-o", str(spv_path), str(preprocessed_path)],
            capture_output=True, text=True,
        )
        if r1.returncode != 0 or not spv_path.exists():
            # glslangValidator's own error text references the temp preprocessed file
            # (about to be deleted with this tempdir), not glsl_path -- rewrite the
            # only mention of it so a "transpile failed" report is still actionable.
            msg = (r1.stderr or r1.stdout).strip().replace(str(preprocessed_path), str(glsl_path))
            raise RuntimeError(msg)

        hlsl_path = Path(tmp) / "out.hlsl"
        r2 = subprocess.run(
            [spirv_cross, str(spv_path), "--hlsl", "--shader-model", "30", "--output", str(hlsl_path)],
            capture_output=True, text=True,
        )
        if r2.returncode != 0 or not hlsl_path.exists():
            raise RuntimeError((r2.stderr or r2.stdout).strip())

        hlsl_text = hlsl_path.read_text(encoding="utf-8", errors="ignore")

    cg_text = hlsl_sm3_to_cg(hlsl_text)

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{Path(glsl_path).stem}.cg"
    out_path.write_text(cg_text, encoding="utf-8")

    profile = _VERTEX_PROFILE if stage == "vert" else _FRAGMENT_PROFILE
    validated_ok, message = utils.validate_shader(out_path, profile, global_cfg)
    return out_path, validated_ok, message


def transpile_shaders_dir(dump_dir, dest_dir, global_cfg=None):
    """!
    @brief Batch-transpile every `.glsl` file in `dump_dir`.
    @param dump_dir Directory containing dumped `.glsl` files (non-recursive).
    @param dest_dir Directory to write the resulting `.cg` files into.
    @param global_cfg Global config dict.
    @return `(ok_count, failed_count)`.
    """
    dump_dir = Path(dump_dir)
    if not dump_dir.is_dir():
        print(f"{C.RED}{t('shader_transpiler.no_dump_dir', dump_dir=dump_dir)}{C.RESET}")
        return 0, 0

    glsl_files = sorted(dump_dir.glob("*.glsl"))
    if not glsl_files:
        print(f"{C.YELLOW}{t('shader_transpiler.no_glsl_found', dump_dir=dump_dir)}{C.RESET}")
        return 0, 0

    ok = failed = 0
    for glsl_path in glsl_files:
        try:
            out_path, validated_ok, message = transpile_shader(glsl_path, dest_dir, global_cfg)
        except RuntimeError as e:
            print(t("shader_transpiler.transpile_failed", name=glsl_path.name, error=e))
            failed += 1
            continue
        stage = _infer_stage(glsl_path) or "?"
        print(t("shader_transpiler.transpiled", name=glsl_path.name, out_name=out_path.name, stage=stage))
        if validated_ok is True:
            print(t("shader_transpiler.validate_ok", color_green=C.GREEN, color_reset=C.RESET))
        elif validated_ok is False:
            print(t("shader_transpiler.validate_failed", color_yellow=C.YELLOW, message=message, color_reset=C.RESET))
        else:
            print(t("shader_transpiler.validate_unavailable", color_yellow=C.YELLOW, message=message, color_reset=C.RESET))
        ok += 1
    print(t("shader_transpiler.summary", ok=ok, failed=failed))
    return ok, failed


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

def transpiler_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: transpile every dumped `.glsl` shader for this project.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    if not find_glslang_validator() or not find_spirv_cross():
        print(f"{C.YELLOW}{t('shader_transpiler.tools_missing')}{C.RESET}")
        return

    def _run():
        project_dir = Path(project_cfg["_project_dir"])
        dump_dir = project_dir / "glsl_dump"
        dest_dir = project_dir / "assets" / "cg"
        transpile_shaders_dir(dump_dir, dest_dir, global_cfg)

    tui.run_menu(t("shader_transpiler.menu_title"), [(t("shader_transpiler.menu_transpile"), _run)])
