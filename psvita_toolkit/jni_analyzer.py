"""!
@file jni_analyzer.py
@brief Engine/middleware fingerprinting and FalsoJNI stub scaffolding for a
       newly-created port.

@details
Two independent things, both meant to save the manual work `init_port.py`'s
wizard currently leaves entirely to the porter:

1. `detect_middleware()` -- fingerprints known third-party libraries linked
   into the game's `.so` (FMOD, OpenAL, BASS, libpng, libjpeg, curl,
   Chipmunk2D, Box2D, Unity/il2cpp, Cocos2d) by scanning for their known
   symbol/string signatures.
2. `generate_jni_stubs()` -- scans the jadx-decompiled Java for "bridge"
   classes (classes that declare at least one `native` method) and generates
   ready-to-fill-in FalsoJNI `NameToMethodID`/`Methods*` stub scaffolding for
   every OTHER (non-native, has-a-body) method in that same class -- these
   are exactly the methods the game's `.so` looks up via
   `GetStaticMethodID`/`GetMethodID` and calls back into "Java" through, the
   half of the JNI bridge FalsoJNI's `java.c` has to fake by hand today.

See `docs/dev-notes/jni_analyzer.md` for why the callback-method scan is
scoped to "same class as a native method", not a full call-graph analysis,
and why generated stub bodies stop short of guessing `va_arg` semantics
beyond what the C standard's own variadic-promotion rules guarantee.
"""

import os
import re
import subprocess
from pathlib import Path

from . import i18n
from .i18n import t
from .tui import C

STRINGS = {
    "jni_analyzer.middleware_found": {
        "es": "[+] Middleware detectado en {so_name}: {names}",
        "en": "[+] Middleware detected in {so_name}: {names}",
        "pt": "[+] Middleware detectado em {so_name}: {names}",
    },
    "jni_analyzer.middleware_none": {
        "es": "[*] No se detectó ningún middleware conocido en {so_name}.",
        "en": "[*] No known middleware detected in {so_name}.",
        "pt": "[*] Nenhum middleware conhecido detectado em {so_name}.",
    },
    "jni_analyzer.middleware_no_so": {
        "es": "[-] No se encontró ningún .so para analizar.",
        "en": "[-] No .so file found to analyze.",
        "pt": "[-] Nenhum .so encontrado para analisar.",
    },
    "jni_analyzer.no_jadx_sources": {
        "es": "[-] No hay fuentes jadx decompiladas en decompiled/apk_jadx/sources -- decompilá el APK primero.",
        "en": "[-] No decompiled jadx sources in decompiled/apk_jadx/sources -- decompile the APK first.",
        "pt": "[-] Não há fontes jadx decompiladas em decompiled/apk_jadx/sources -- decompile o APK primeiro.",
    },
    "jni_analyzer.no_bridge_classes": {
        "es": "[*] No se encontraron clases con métodos 'native' en el Java decompilado.",
        "en": "[*] No classes with 'native' methods found in the decompiled Java.",
        "pt": "[*] Nenhuma classe com métodos 'native' encontrada no Java decompilado.",
    },
    "jni_analyzer.stubs_generated": {
        "es": "[+] {count} candidato(s) de callback JNI en {classes} clase(s) -- ver {header}/{stub}",
        "en": "[+] {count} JNI callback candidate(s) across {classes} class(es) -- see {header}/{stub}",
        "pt": "[+] {count} candidato(s) de callback JNI em {classes} classe(s) -- ver {header}/{stub}",
    },
    "jni_analyzer.lifecycle_found": {
        "es": "[+] {count} método(s) de ciclo de vida detectado(s) -- documentados en {plan_path}",
        "en": "[+] {count} lifecycle method(s) detected -- documented in {plan_path}",
        "pt": "[+] {count} método(s) de ciclo de vida detectado(s) -- documentados em {plan_path}",
    },
    "jni_analyzer.lifecycle_none": {
        "es": "[*] No se detectó ningún método de ciclo de vida conocido (onCreate/onSurfaceCreated/nativeInit/nativeRender/...).",
        "en": "[*] No known lifecycle method detected (onCreate/onSurfaceCreated/nativeInit/nativeRender/...).",
        "pt": "[*] Nenhum método de ciclo de vida conhecido detectado (onCreate/onSurfaceCreated/nativeInit/nativeRender/...).",
    },
    "jni_analyzer.plan_not_found": {
        "es": "[!] No se encontró PORTING_PLAN.md -- se omite la sección de ciclo de vida.",
        "en": "[!] PORTING_PLAN.md not found -- skipping the lifecycle section.",
        "pt": "[!] PORTING_PLAN.md não encontrado -- pulando a seção de ciclo de vida.",
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Middleware / engine fingerprinting
# ---------------------------------------------------------------------------

# Each signature is checked as a raw substring against the .so's bytes (not
# just its dynamic symbol table) -- a stripped release .so often still
# retains string literals (version tags, asserts, log format strings) even
# once symbol names are gone, so this catches more real ports than a
# symbols-only check would. See docs/dev-notes/jni_analyzer.md.
_MIDDLEWARE_SIGNATURES = {
    "FMOD": (b"FMOD_System_Create", b"FMOD_Sound", b"fmod.dll", b"FMOD_ERR_"),
    "OpenAL": (b"alGenSources", b"alcOpenDevice", b"alSourcePlay", b"OpenAL Soft"),
    "BASS": (b"BASS_Init", b"BASS_ChannelPlay", b"BASS_StreamCreate"),
    "libpng": (b"png_read_info", b"png_create_read_struct", b"libpng version"),
    "libjpeg": (b"jpeg_read_header", b"jpeg_start_decompress", b"libjpeg"),
    "curl": (b"curl_easy_init", b"curl_easy_setopt", b"libcurl/"),
    "Chipmunk2D": (b"cpSpaceStep", b"cpBodyGetPosition", b"cpSpaceAddBody"),
    "Box2D": (b"_ZN2b2", b"b2World", b"Box2D"),
    "Unity (il2cpp/mono)": (b"il2cpp_", b"mono_runtime_invoke", b"UnityMain", b"GameAssembly"),
    "Cocos2d": (b"_ZN7cocos2d", b"cocos2d::", b"libcocos2dcpp"),
}


def detect_middleware(so_path):
    """!
    @brief Fingerprint known third-party middleware linked into a game's `.so`.
    @param so_path Path to the Android `.so` file.
    @return list of matched middleware names (keys of `_MIDDLEWARE_SIGNATURES`),
            `[]` if `so_path` doesn't exist or nothing matched.
    """
    if not so_path or not os.path.exists(so_path):
        return []
    try:
        with open(so_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    return [name for name, sigs in _MIDDLEWARE_SIGNATURES.items() if any(sig in data for sig in sigs)]


def _find_primary_so(project_dir):
    """!
    @brief Best-effort discovery of the port's original Android `.so`, same
           heuristic as `crash_analyzer._auto_find_files()`'s `.so` half.
    @param project_dir Path to the port's project directory.
    @return Path string to the most likely `.so`, or `None` if none found.
    """
    import glob
    candidates = glob.glob(os.path.join(project_dir, "**", "*.so"), recursive=True)
    candidates.sort(key=lambda x: 0 if ("libgame" in x or "libmain" in x) else 1)
    return candidates[0] if candidates else None


def middleware_report(project_cfg):
    """!
    @brief TUI-facing wrapper: detect and print middleware for the active project's `.so`.
    @param project_cfg Per-project config dict.
    """
    so_path = _find_primary_so(project_cfg["_project_dir"])
    if not so_path:
        print(f"{C.RED}{t('jni_analyzer.middleware_no_so')}{C.RESET}")
        return
    found = detect_middleware(so_path)
    so_name = os.path.basename(so_path)
    if found:
        print(f"{C.GREEN}{t('jni_analyzer.middleware_found', so_name=so_name, names=', '.join(found))}{C.RESET}")
    else:
        print(t("jni_analyzer.middleware_none", so_name=so_name))


# ---------------------------------------------------------------------------
# Java source scanning: bridge classes (native + their sibling callbacks)
# ---------------------------------------------------------------------------

_PACKAGE_RE = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.M)
_TOP_CLASS_RE = re.compile(r'^\s*(?:public\s+|final\s+|abstract\s+)*class\s+(\w+)', re.M)

# One method signature, applied to a single top-level-member text segment
# (see _iter_top_level_members()) -- NOT run globally over the whole file,
# specifically so nested/anonymous-class methods at deeper brace depth are
# never considered. Modifiers group is checked for the "native" keyword;
# the return type + name are required (excludes constructors, which have
# only one identifier before the parens).
_METHOD_SIG_RE = re.compile(
    r'^\s*(?:@\w+(?:\([^)]*\))?\s*)*'
    r'((?:public|private|protected|static|final|synchronized|native|abstract)\s*)*'
    r'([\w.$]+(?:<[^>]*>)?(?:\[\])*)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w.,\s]+)?\s*$',
)


def _strip_comments_and_literals(text):
    """!
    @brief Blank out Java comments and string/char literal contents, keeping
           line structure (so later regex match positions/line numbers stay
           meaningful) and brace/quote characters harmless.
    @param text Raw Java source text.
    @return Same-length-ish text with comments/literal contents replaced.
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(re.sub(r'[^\n]', ' ', text[i:j]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append('"' + " " * (j - i - 2) + '"' if j - i >= 2 else text[i:j])
            i = j
        elif c == "'":
            j = i + 1
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append("'" + " " * (j - i - 2) + "'" if j - i >= 2 else text[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _class_body_span(text, class_name):
    """!
    @brief Find the `{...}` span of a top-level class's body.
    @param text Full (comment-stripped) Java source.
    @param class_name Top-level class name (from `_TOP_CLASS_RE`).
    @return `(body_start, body_end)` indices into `text` (exclusive of the
            braces themselves), or `None` if not found.
    """
    m = re.search(r'\bclass\s+' + re.escape(class_name) + r'\b[^{]*\{', text)
    if not m:
        return None
    depth = 1
    i = m.end()
    n = len(text)
    while i < n and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return (m.end(), i - 1) if depth == 0 else None


def _iter_top_level_members(body_text):
    """!
    @brief Walk a class body and yield each direct-member declaration's text,
           skipping over the contents of any nested block (method bodies,
           inner classes, anonymous classes, initializers) at deeper brace depth.
    @param body_text Comment-stripped text between a class's outer `{`/`}`.
    @return Generator of `(segment_text, has_body: bool)` tuples -- `has_body`
            is `True` if the member's declaration is followed by a `{...}`
            block (a method with a body, or a nested type), `False` if it
            just ends in `;` (a field, or a `native`/abstract method).
    """
    depth = 0
    seg_start = 0
    i, n = 0, len(body_text)
    while i < n:
        c = body_text[i]
        if depth == 0 and c == ";":
            yield body_text[seg_start:i], False
            seg_start = i + 1
        elif depth == 0 and c == "{":
            yield body_text[seg_start:i], True
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if body_text[j] == "{":
                    depth += 1
                elif body_text[j] == "}":
                    depth -= 1
                j += 1
            i = j
            seg_start = i
            continue
        i += 1


def scan_bridge_classes(project_dir):
    """!
    @brief Find every jadx-decompiled class declaring at least one `native`
           method, and split its direct members into that class's own
           native methods and its non-native "callback" methods (candidates
           for what the `.so` calls back into "Java" through).
    @param project_dir Path to the port's project directory.
    @return list of dicts: `{"fqcn", "file", "native_methods": [...],
            "callback_methods": [...]}`. Each method entry is a dict:
            `{"name", "return_type", "params": [java_type, ...]}`.
    @note Scoped to one top-level `class` per file, matching this toolkit's
          existing best-effort convention (see `_jni_method_name_from_symbol()`
          in `crash_analyzer.py`) -- a native method declared in a nested
          class isn't picked up. See `docs/dev-notes/jni_analyzer.md`.
    """
    jadx_dir = Path(project_dir) / "decompiled" / "apk_jadx" / "sources"
    if not jadx_dir.is_dir():
        return []

    bridge_classes = []
    for java_file in sorted(jadx_dir.glob("**/*.java")):
        try:
            raw = java_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _strip_comments_and_literals(raw)
        pkg_m = _PACKAGE_RE.search(text)
        package = pkg_m.group(1) if pkg_m else ""
        cls_m = _TOP_CLASS_RE.search(text)
        if not cls_m:
            continue
        span = _class_body_span(text, cls_m.group(1))
        if not span:
            continue
        body = text[span[0]:span[1]]

        native_methods, callback_methods = [], []
        for segment, has_body in _iter_top_level_members(body):
            m = _METHOD_SIG_RE.match(segment)
            if not m:
                continue
            modifiers, return_type, name, params_raw = m.groups()
            is_native = "native" in (modifiers or "")
            if is_native and has_body:
                continue  # malformed match, e.g. a nested type declaration
            if not is_native and not has_body:
                continue  # interface/abstract method declaration, not a real callback target
            params = []
            if params_raw.strip():
                for p in params_raw.split(","):
                    p = p.strip().replace("final ", "")
                    if not p:
                        continue
                    parts = p.rsplit(None, 1)
                    params.append(parts[0] if len(parts) == 2 else p)
            entry = {"name": name, "return_type": return_type, "params": params}
            (native_methods if is_native else callback_methods).append(entry)

        if native_methods:
            fqcn = f"{package}.{cls_m.group(1)}" if package else cls_m.group(1)
            bridge_classes.append({
                "fqcn": fqcn, "file": str(java_file),
                "native_methods": native_methods, "callback_methods": callback_methods,
            })
    return bridge_classes


# ---------------------------------------------------------------------------
# Java type -> JNI type mapping
# ---------------------------------------------------------------------------

# (jni_descriptor, jni_c_type, method_type_enum, promoted_va_arg_c_type, dummy_return)
_JAVA_PRIMITIVE_JNI = {
    "void":    ("V", "void",     "METHOD_TYPE_VOID",    None,     None),
    "boolean": ("Z", "jboolean", "METHOD_TYPE_BOOLEAN",  "int",    "JNI_FALSE"),
    "byte":    ("B", "jbyte",    "METHOD_TYPE_BYTE",     "int",    "0"),
    "char":    ("C", "jchar",    "METHOD_TYPE_CHAR",     "int",    "0"),
    "short":   ("S", "jshort",   "METHOD_TYPE_SHORT",    "int",    "0"),
    "int":     ("I", "jint",     "METHOD_TYPE_INT",      "int",    "0"),
    "long":    ("J", "jlong",    "METHOD_TYPE_LONG",     "jlong",  "0"),
    "float":   ("F", "jfloat",   "METHOD_TYPE_FLOAT",    "double", "0.0f"),
    "double":  ("D", "jdouble",  "METHOD_TYPE_DOUBLE",   "jdouble", "0.0"),
}
_JAVA_OBJECT_JNI = {
    "String": "Ljava/lang/String;",
    "Object": "Ljava/lang/Object;",
    "Class": "Ljava/lang/Class;",
}


def java_type_to_jni(java_type):
    """!
    @brief Map a Java source type token to its JNI descriptor/C type/promoted
           `va_arg` type/safe dummy return value.
    @param java_type Type token as printed by jadx (e.g. `"int"`, `"String"`,
           `"byte[]"`, `"SomeCustomClass"`).
    @return dict with keys `descriptor`, `c_type`, `method_type`, `va_arg_type`,
            `dummy`. Unknown object types get a best-effort `L<Name>;`
            descriptor (almost certainly wrong package path -- flagged with a
            `confident: False` key for the generator to comment on).
    """
    java_type = java_type.strip()
    array_depth = 0
    while java_type.endswith("[]"):
        java_type = java_type[:-2].strip()
        array_depth += 1

    if not array_depth and java_type in _JAVA_PRIMITIVE_JNI:
        descriptor, c_type, method_type, va_arg_type, dummy = _JAVA_PRIMITIVE_JNI[java_type]
        return {"descriptor": descriptor, "c_type": c_type, "method_type": method_type,
                "va_arg_type": va_arg_type, "dummy": dummy, "confident": True}

    if array_depth and java_type in _JAVA_PRIMITIVE_JNI and array_depth == 1:
        base_descriptor = _JAVA_PRIMITIVE_JNI[java_type][0]
        return {"descriptor": "[" + base_descriptor, "c_type": f"j{java_type}Array" if java_type != "boolean" else "jbooleanArray",
                "method_type": "METHOD_TYPE_OBJECT", "va_arg_type": "jobject", "dummy": "NULL", "confident": True}

    base_descriptor = _JAVA_OBJECT_JNI.get(java_type, f"L{java_type};")
    confident = java_type in _JAVA_OBJECT_JNI
    descriptor = ("[" * array_depth) + base_descriptor
    return {"descriptor": descriptor, "c_type": "jobject", "method_type": "METHOD_TYPE_OBJECT",
            "va_arg_type": "jobject", "dummy": "NULL", "confident": confident or array_depth > 0}


def jni_signature(return_type, param_types):
    """!
    @brief Build the full JNI method signature string (e.g. `"(ILjava/lang/String;)V"`).
    @param return_type Java return type token.
    @param param_types List of Java parameter type tokens.
    @return JNI signature string.
    """
    params = "".join(java_type_to_jni(p)["descriptor"] for p in param_types)
    return f"({params}){java_type_to_jni(return_type)['descriptor']}"


# ---------------------------------------------------------------------------
# generated_jni_table.h / generated_jni_stubs.c
# ---------------------------------------------------------------------------

_METHOD_TYPE_TO_ARRAY = {
    "METHOD_TYPE_VOID": ("MethodsVoid", "methodsVoid", "void"),
    "METHOD_TYPE_OBJECT": ("MethodsObject", "methodsObject", "jobject"),
    "METHOD_TYPE_BOOLEAN": ("MethodsBoolean", "methodsBoolean", "jboolean"),
    "METHOD_TYPE_BYTE": ("MethodsByte", "methodsByte", "jbyte"),
    "METHOD_TYPE_CHAR": ("MethodsChar", "methodsChar", "jchar"),
    "METHOD_TYPE_SHORT": ("MethodsShort", "methodsShort", "jshort"),
    "METHOD_TYPE_INT": ("MethodsInt", "methodsInt", "jint"),
    "METHOD_TYPE_LONG": ("MethodsLong", "methodsLong", "jlong"),
    "METHOD_TYPE_FLOAT": ("MethodsFloat", "methodsFloat", "jfloat"),
    "METHOD_TYPE_DOUBLE": ("MethodsDouble", "methodsDouble", "jdouble"),
}


def _stub_function_name(fqcn, method_name, index):
    """!
    @brief Build a unique, valid C identifier for a generated stub function.
    @param fqcn Owning class's fully-qualified Java name.
    @param method_name Java method name.
    @param index Unique index (disambiguates overloads sharing a name).
    @return C function name string, e.g. `"stub_Natives_OnSoundPlay_4"`.
    """
    safe_class = re.sub(r'[^A-Za-z0-9_]', '_', fqcn.rsplit(".", 1)[-1])
    return f"stub_{safe_class}_{method_name}_{index}"


def _generate_stub_body(entry, fqcn, index):
    """!
    @brief Generate one FalsoJNI method-stub function body.
    @param entry Method entry (`{"name", "return_type", "params"}`) from `scan_bridge_classes()`.
    @param fqcn Owning class's fully-qualified name (for the log line and function name).
    @param index Unique index, to disambiguate overloads sharing a name.
    @return `(func_name, method_type, c_source_lines)`.
    @note Extracts each argument from `va_list args` using the promoted type
          C's variadic-argument default promotions guarantee (`float`->`double`,
          integer types smaller than `int`->`int`) -- this is standard C
          semantics, not a project-specific guess. It does NOT attempt to
          infer what the stub should actually DO; that's for the porter to
          fill in after reading the real Java implementation (jadx already
          has it, right there in the same file) -- see `docs/dev-notes/jni_analyzer.md`.
    """
    ret = java_type_to_jni(entry["return_type"])
    func_name = _stub_function_name(fqcn, entry["name"], index)
    sig = jni_signature(entry["return_type"], entry["params"])

    # Non-static: generated_jni_table.h declares these `extern` so java.c can
    # reference them by name once merged -- `static` here would conflict
    # with that declaration's linkage.
    lines = [f"{ret['c_type']} {func_name}(jmethodID id, va_list args) {{"]
    lines.append(f'    fjni_logv_dbg("[JNI STUB] {fqcn}.{entry["name"]}{sig} called -- fill in from the real Java impl (jadx).");')
    for i, ptype in enumerate(entry["params"]):
        pinfo = java_type_to_jni(ptype)
        note = "" if pinfo["confident"] else "  // best-effort descriptor, confirm the real class path"
        lines.append(f"    {pinfo['c_type']} arg{i} = ({pinfo['c_type']}) va_arg(args, {pinfo['va_arg_type']});{note}")
    if ret["dummy"] is not None:
        lines.append(f"    return {ret['dummy']};")
    lines.append("}")
    return func_name, ret["method_type"], lines


def generate_jni_stubs(project_cfg, out_dir=None):
    """!
    @brief Scan the decompiled Java for bridge classes and generate
           `generated_jni_table.h` + `generated_jni_stubs.c`: ready-to-review
           FalsoJNI `NameToMethodID`/`Methods*` scaffolding for every
           non-native callback method found alongside a `native` method.
    @param project_cfg Per-project config dict.
    @param out_dir Directory to write the two files into; defaults to
           `<project_dir>/source` if it exists, else the project root.
    @note These are candidates to review and merge into the project's real
          `lib/falso_jni`-consuming `java.c` -- NOT a drop-in replacement for
          it. Every generated stub logs its call and returns a safe dummy;
          none of them replicate the real Java behavior, which still has to
          be read (jadx has it, right there) and ported by hand. See
          `docs/dev-notes/jni_analyzer.md`.
    """
    project_dir = Path(project_cfg["_project_dir"])
    bridge_classes = scan_bridge_classes(project_dir)
    if not bridge_classes:
        jadx_dir = project_dir / "decompiled" / "apk_jadx" / "sources"
        print(t("jni_analyzer.no_jadx_sources") if not jadx_dir.is_dir() else t("jni_analyzer.no_bridge_classes"))
        return

    by_method_type = {}
    name_table_entries = []
    total = 0
    idx = 0

    header_lines = [
        "/* Auto-generated by psvita-toolkit from decompiled/apk_jadx/sources -- FalsoJNI callback candidates. */",
        "/* Every entry here is a CANDIDATE: confirm the method belongs on the .so's actual call path, and */",
        "/* read the real Java implementation (jadx) before trusting the stub body. See docs/dev-notes/jni_analyzer.md. */",
        "#pragma once",
        "#include <stdarg.h>",
        "#include <falso_jni/FalsoJNI_Impl.h>",
        "",
    ]
    stub_lines = [
        "/* Auto-generated by psvita-toolkit -- see generated_jni_table.h */",
        '#include <falso_jni/FalsoJNI_Impl.h>',
        '#include "generated_jni_table.h"', "",
    ]

    for cls in bridge_classes:
        if not cls["callback_methods"]:
            continue
        stub_lines.append(f"// ---- {cls['fqcn']} ----")
        for entry in cls["callback_methods"]:
            func_name, method_type, body_lines = _generate_stub_body(entry, cls["fqcn"], idx)
            stub_lines.extend(body_lines)
            stub_lines.append("")
            array_type, array_name, c_type = _METHOD_TYPE_TO_ARRAY[method_type]
            by_method_type.setdefault(array_name, (array_type, []))[1].append((idx, func_name))
            name_table_entries.append((idx, entry["name"], method_type, cls["fqcn"]))
            header_lines.append(f"extern {c_type} {func_name}(jmethodID id, va_list args);")
            total += 1
            idx += 1

    header_lines.append("")
    header_lines.append("// Candidate NameToMethodID entries -- append into the real nameToMethodId[] in java.c")
    header_lines.append("static const NameToMethodID generated_nameToMethodId[] = {")
    for mid, name, method_type, fqcn in name_table_entries:
        header_lines.append(f'    {{ {mid}, "{name}", {method_type} }},  // {fqcn}')
    header_lines.append("};")
    header_lines.append("")
    for array_name, (array_type, entries) in by_method_type.items():
        header_lines.append(f"// Candidate {array_name}[] entries -- append into the real {array_name}[] in java.c")
        header_lines.append(f"static const {array_type} generated_{array_name}[] = {{")
        for mid, func_name in entries:
            header_lines.append(f"    {{ {mid}, {func_name} }},")
        header_lines.append("};")
        header_lines.append("")

    if not total:
        print(t("jni_analyzer.no_bridge_classes"))
        return

    dest = Path(out_dir) if out_dir else (project_dir / "source" if (project_dir / "source").is_dir() else project_dir)
    dest.mkdir(parents=True, exist_ok=True)
    header_path = dest / "generated_jni_table.h"
    stub_path = dest / "generated_jni_stubs.c"
    header_path.write_text("\n".join(header_lines) + "\n", encoding="utf-8")
    stub_path.write_text("\n".join(stub_lines) + "\n", encoding="utf-8")

    print(t("jni_analyzer.stubs_generated", count=total, classes=len(bridge_classes),
            header=header_path.name, stub=stub_path.name))


# ---------------------------------------------------------------------------
# Lifecycle method detection -> PORTING_PLAN.md
# ---------------------------------------------------------------------------

_LIFECYCLE_HINTS = (
    "oncreate", "onsurfacecreated", "onsurfacechanged", "ondrawframe",
    "nativeinit", "nativerender", "onresume", "onpause", "ondestroy",
    "onstart", "onstop", "nativeresize", "nativeonpause", "nativeonresume",
    "nativeonresize", "onwindowfocuschanged",
)


def _is_lifecycle_method(name):
    """!
    @brief Check whether a method name matches a known Android/GL app lifecycle hook.
    @param name Method name to check (case-insensitive).
    @return `True` if `name` contains one of `_LIFECYCLE_HINTS`.
    """
    n = name.lower()
    return any(hint in n for hint in _LIFECYCLE_HINTS)


def detect_lifecycle_methods(bridge_classes):
    """!
    @brief Flag native methods whose name matches a well-known Android/GL
           app lifecycle hook, across every scanned bridge class.
    @param bridge_classes Result of `scan_bridge_classes()`.
    @return list of `(fqcn, method_entry)` tuples, in scan order.
    """
    hits = []
    for cls in bridge_classes:
        for entry in cls["native_methods"]:
            if _is_lifecycle_method(entry["name"]):
                hits.append((cls["fqcn"], entry))
    return hits


def document_lifecycle_in_plan(project_cfg):
    """!
    @brief Detect lifecycle-shaped native methods and append a documented
           section listing them to the project's `PORTING_PLAN.md`.
    @param project_cfg Per-project config dict.
    """
    project_dir = Path(project_cfg["_project_dir"])
    bridge_classes = scan_bridge_classes(project_dir)
    hits = detect_lifecycle_methods(bridge_classes)
    if not hits:
        print(t("jni_analyzer.lifecycle_none"))
        return

    plan_path = project_dir / "PORTING_PLAN.md"
    if not plan_path.exists():
        print(f"{C.YELLOW}{t('jni_analyzer.plan_not_found')}{C.RESET}")
        return

    lines = ["", "## Auto-detected lifecycle methods (psvita-toolkit)", "",
             "Native methods whose name matches a well-known Android/GL app lifecycle hook --",
             "these are the ones `main.c`/the loader most likely needs to call directly to",
             "drive the game (there's no real Android `Activity`/`GLSurfaceView` calling them", "for you).", ""]
    for fqcn, entry in hits:
        params = ", ".join(entry["params"]) or "void"
        lines.append(f"- `{fqcn}.{entry['name']}({params})`")
    lines.append("")

    with open(plan_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"{C.GREEN}{t('jni_analyzer.lifecycle_found', count=len(hits), plan_path=plan_path)}{C.RESET}")
