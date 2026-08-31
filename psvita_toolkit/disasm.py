"""!
@file disasm.py
@brief Standalone ARM/Thumb disassembly of a single function or address range from a
       port's Android `.so`, cross-referenced against its Ghidra pseudo-C (when
       available) and exported as a single reviewable Markdown file -- meant to be
       pasted straight into an AI assistant.

@details
This is the manual so-crash-triage technique (`arm-vita-eabi-nm` to resolve a
function's real address/size -> `arm-vita-eabi-objdump -d -Mforce-thumb` to get the
real Thumb-2 code -> compare against Ghidra's pseudo-C, which can have gaps or
mistranslated idioms) packaged as a single command instead of five manual ones.

Given a symbol name (mangled, demangled, or Ghidra's `FUN_<hex>` convention for
unnamed local functions) or a raw hex address, resolves the exact function boundaries
via the `.so`'s full (non-dynamic-only) symbol table, disassembles the real code, and
(if a `decompiled/*/ghidra/*.c` dump exists) extracts the matching pseudo-C function
body so both can be reviewed side by side.
"""

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import i18n
from .i18n import t
from .tui import C
from .context_feeder import copy_to_clipboard

STRINGS = {
    "disasm.so_not_found": {
        "es": "[-] No se encontró ningún .so en el proyecto -- pasa --so con la ruta exacta.",
        "en": "[-] No .so found under the project -- pass --so with the exact path.",
        "pt": "[-] Nenhum .so encontrado no projeto -- passe --so com o caminho exato.",
    },
    "disasm.nm_missing": {
        "es": "[-] 'arm-vita-eabi-nm' no está en el PATH -- corré `psvita-toolkit doctor` para diagnosticar el toolchain de VITASDK.",
        "en": "[-] 'arm-vita-eabi-nm' not found on PATH -- run `psvita-toolkit doctor` to diagnose the VITASDK toolchain.",
        "pt": "[-] 'arm-vita-eabi-nm' não está no PATH -- rode `psvita-toolkit doctor` para diagnosticar o toolchain do VITASDK.",
    },
    "disasm.objdump_missing": {
        "es": "[-] 'arm-vita-eabi-objdump' no está en el PATH -- corré `psvita-toolkit doctor` para diagnosticar el toolchain de VITASDK.",
        "en": "[-] 'arm-vita-eabi-objdump' not found on PATH -- run `psvita-toolkit doctor` to diagnose the VITASDK toolchain.",
        "pt": "[-] 'arm-vita-eabi-objdump' não está no PATH -- rode `psvita-toolkit doctor` para diagnosticar o toolchain do VITASDK.",
    },
    "disasm.objdump_failed": {
        "es": "[-] objdump falló: {detail}",
        "en": "[-] objdump failed: {detail}",
        "pt": "[-] objdump falhou: {detail}",
    },
    "disasm.symbol_not_found": {
        "es": "[-] Ningún símbolo coincide con esa consulta en el .so. Probá con una dirección hexadecimal (0x...) o 'FUN_<hex>' si viene de Ghidra.",
        "en": "[-] No symbol in the .so matches that query. Try a hex address (0x...) or 'FUN_<hex>' if it came from Ghidra.",
        "pt": "[-] Nenhum símbolo no .so corresponde a essa consulta. Tente um endereço hex (0x...) ou 'FUN_<hex>' se veio do Ghidra.",
    },
    "disasm.multiple_matches": {
        "es": "[!] Varios símbolos coinciden -- sé más específico o usá la dirección exacta:",
        "en": "[!] Multiple symbols matched -- be more specific or use the exact address:",
        "pt": "[!] Vários símbolos correspondem -- seja mais específico ou use o endereço exato:",
    },
    "disasm.field_project": {"es": "Proyecto", "en": "Project", "pt": "Projeto"},
    "disasm.field_so": {"es": ".so analizado", "en": "Analyzed .so", "pt": ".so analisado"},
    "disasm.field_address": {"es": "Dirección", "en": "Address", "pt": "Endereço"},
    "disasm.field_size": {"es": "Tamaño", "en": "Size", "pt": "Tamanho"},
    "disasm.field_raw_symbol": {"es": "Símbolo real (mangled)", "en": "Real symbol (mangled)", "pt": "Símbolo real (mangled)"},
    "disasm.size_guessed_note": {
        "es": "tamaño estimado, no confirmado -- puede cortar la función; usá --range para ajustarlo",
        "en": "estimated size, not confirmed -- may cut the function short; use --range to adjust",
        "pt": "tamanho estimado, não confirmado -- pode cortar a função; use --range para ajustar",
    },
    "disasm.size_guessed_warning": {
        "es": "[!] El tamaño real de la función no se pudo confirmar por símbolo -- se usó una ventana de {size} bytes. Si el desensamblado se corta, repetí con --range.",
        "en": "[!] The function's real size couldn't be confirmed from the symbol table -- used a {size}-byte window. If the disassembly looks cut off, re-run with --range.",
        "pt": "[!] O tamanho real da função não pôde ser confirmado pela tabela de símbolos -- foi usada uma janela de {size} bytes. Se o desmontagem parecer cortado, rode de novo com --range.",
    },
    "disasm.ghidra_heading": {
        "es": "Pseudo-C de Ghidra (para comparar)",
        "en": "Ghidra pseudo-C (for comparison)",
        "pt": "Pseudo-C do Ghidra (para comparação)",
    },
    "disasm.ghidra_not_found": {
        "es": "No se encontró una función coincidente bajo decompiled/*/ghidra/ -- solo se incluye el desensamblado real.",
        "en": "No matching function found under decompiled/*/ghidra/ -- only the real disassembly is included.",
        "pt": "Nenhuma função correspondente encontrada em decompiled/*/ghidra/ -- apenas o desmontagem real está incluído.",
    },
    "disasm.arm_heading": {
        "es": "Desensamblado ARM/Thumb real (arm-vita-eabi-objdump)",
        "en": "Real ARM/Thumb disassembly (arm-vita-eabi-objdump)",
        "pt": "Desmontagem ARM/Thumb real (arm-vita-eabi-objdump)",
    },
    "disasm.prompt_heading": {
        "es": "Instrucciones para la IA",
        "en": "Instructions for the AI",
        "pt": "Instruções para a IA",
    },
    "disasm.prompt_body": {
        "es": (
            "El desensamblado ARM/Thumb de arriba es la ÚNICA fuente de verdad -- viene directo del "
            "binario real, no es una traducción. El pseudo-C de Ghidra (si está presente) puede tener "
            "huecos, offsets mal traducidos o idioms de Ghidra mal interpretados; úsalo solo como ayuda "
            "de lectura, nunca como autoridad por encima del ARM real.\n\n"
            "1. Si te doy un hook/reimplementación en C de esta función, verificá instrucción por "
            "instrucción que sea fiel al ARM real (offsets, condiciones, orden de operaciones).\n"
            "2. Señalá cualquier discrepancia entre el pseudo-C de Ghidra y el ARM real como un hallazgo "
            "aparte, con la dirección/instrucción exacta.\n"
            "3. Si proponés un fix, dalo como parche listo para aplicar sobre `source/patch.c` (o el "
            "archivo que corresponda), no como pseudocódigo."
        ),
        "en": (
            "The ARM/Thumb disassembly above is the ONLY source of truth -- it comes straight from the "
            "real binary, it's not a translation. Ghidra's pseudo-C (if present) can have gaps, "
            "mistranslated offsets, or misread Ghidra idioms; use it only as a reading aid, never as an "
            "authority above the real ARM.\n\n"
            "1. If I give you a C hook/reimplementation of this function, verify instruction-by-"
            "instruction that it's faithful to the real ARM (offsets, conditions, operation order).\n"
            "2. Flag any discrepancy between Ghidra's pseudo-C and the real ARM as a separate finding, "
            "with the exact address/instruction.\n"
            "3. If you propose a fix, give it as a ready-to-apply patch against `source/patch.c` (or "
            "whichever file applies), not as pseudocode."
        ),
        "pt": (
            "O desmontagem ARM/Thumb acima é a ÚNICA fonte de verdade -- vem direto do binário real, não "
            "é uma tradução. O pseudo-C do Ghidra (se presente) pode ter lacunas, offsets mal traduzidos "
            "ou idiomas do Ghidra mal interpretados; use-o apenas como ajuda de leitura, nunca como "
            "autoridade acima do ARM real.\n\n"
            "1. Se eu te der um hook/reimplementação em C dessa função, verifique instrução por "
            "instrução se é fiel ao ARM real (offsets, condições, ordem das operações).\n"
            "2. Aponte qualquer discrepância entre o pseudo-C do Ghidra e o ARM real como um achado "
            "separado, com o endereço/instrução exata.\n"
            "3. Se propuser um fix, entregue como patch pronto para aplicar em `source/patch.c` (ou o "
            "arquivo correspondente), não como pseudocódigo."
        ),
    },
    "disasm.exported_ok": {
        "es": "[+] Desensamblado exportado a: {path}",
        "en": "[+] Disassembly exported to: {path}",
        "pt": "[+] Desmontagem exportada para: {path}",
    },
    "disasm.write_failed": {
        "es": "[-] No se pudo escribir el archivo de desensamblado: {error}",
        "en": "[-] Couldn't write the disassembly file: {error}",
        "pt": "[-] Não foi possível escrever o arquivo de desmontagem: {error}",
    },
    "disasm.clipboard_ok": {
        "es": "[+] Desensamblado copiado al portapapeles.",
        "en": "[+] Disassembly copied to clipboard.",
        "pt": "[+] Desmontagem copiada para a área de transferência.",
    },
    "disasm.clipboard_failed": {
        "es": "[!] No se pudo copiar al portapapeles (no se encontró pbcopy/xclip/xsel/clip.exe).",
        "en": "[!] Couldn't copy to clipboard (no pbcopy/xclip/xsel/clip.exe found).",
        "pt": "[!] Não foi possível copiar para a área de transferência (pbcopy/xclip/xsel/clip.exe não encontrados).",
    },
}
i18n.register(STRINGS)


_FUN_ADDR_RE = re.compile(r'^FUN_([0-9a-fA-F]{5,8})$')
_HEX_ADDR_RE = re.compile(r'^0x([0-9a-fA-F]{4,8})$')


def _ensure_vitasdk_path(global_cfg):
    """!
    @brief Make sure VITASDK's `bin/` (where the arm-vita-eabi-* binaries live) is on
           PATH, same convention as `mem_align_analyzer._disassemble()`.
    @param global_cfg Global config dict; reads `vitasdk`.
    """
    vitasdk_bin = os.path.join((global_cfg or {}).get("vitasdk", ""), "bin")
    if os.path.isdir(vitasdk_bin) and vitasdk_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{vitasdk_bin}:{os.environ.get('PATH', '')}"


def _find_so(project_dir, project_cfg=None, so_path=None):
    """!
    @brief Resolve which Android `.so` to disassemble: explicit path, then the
           project's configured `main_so`, then the largest/most-likely `.so` found
           under the project directory.
    @param project_dir Path to the port's project directory.
    @param project_cfg Optional project config dict.
    @param so_path Explicit override path.
    @return Path string, or `None` if nothing was found.
    """
    if so_path:
        return so_path if os.path.isfile(so_path) else None

    if project_cfg:
        configured = project_cfg.get("main_so") or project_cfg.get("so_name") or project_cfg.get("so_file")
        if configured:
            full = os.path.join(str(project_dir), configured) if not os.path.isabs(configured) else configured
            if os.path.isfile(full):
                return full

    candidates = [p for p in glob.glob(os.path.join(str(project_dir), "**", "*.so"), recursive=True)
                  if not os.path.basename(p).startswith("._")]
    if not candidates:
        return None
    candidates.sort(key=lambda x: 0 if ("libgame" in x.lower() or "libmain" in x.lower()) else 1)
    return candidates[0]


def _parse_address_literal(query):
    """!
    @brief Resolve a raw hex address ('0x...') or Ghidra's 'FUN_<hex>' naming
           convention for unnamed local functions directly to an address.
    @param query Raw CLI query string.
    @return Integer address, or `None` if `query` isn't one of those two forms.
    """
    m = _FUN_ADDR_RE.match(query)
    if m:
        return int(m.group(1), 16)
    m = _HEX_ADDR_RE.match(query)
    if m:
        return int(m.group(1), 16)
    return None


def _load_full_symbols(so_path):
    """!
    @brief Function symbol table via `arm-vita-eabi-nm -D -S` (the dynamic table --
           these Android `.so`s ship with their regular `.symtab` fully stripped, so
           plain `nm` reports "no symbols"; `-D` is required), demangled via
           `c++filt`. Purely local/static functions with no symbol at all (the ones
           Ghidra labels `FUN_<hex>`) won't appear here either way -- those are
           resolved directly by address in `_parse_address_literal()` instead.
    @param so_path Path to the `.so` to inspect.
    @return List of `[addr, size, raw_name, demangled_name]` sorted by address, or
            `None` if `arm-vita-eabi-nm` isn't on PATH.
    """
    nm = shutil.which("arm-vita-eabi-nm")
    if not nm:
        return None
    cfilt = shutil.which("arm-vita-eabi-c++filt") or shutil.which("c++filt")

    r = subprocess.run([nm, "-D", "-S", so_path], capture_output=True, text=True)
    if r.returncode != 0:
        return []

    entries = []
    for line in r.stdout.splitlines():
        parts = line.split()
        try:
            if len(parts) >= 4:
                addr, size, typ, raw = int(parts[0], 16), int(parts[1], 16), parts[2], parts[3]
            elif len(parts) == 3:
                addr, size, typ, raw = int(parts[0], 16), 0, parts[1], parts[2]
            else:
                continue
        except ValueError:
            continue
        if typ.lower() not in ("t", "w"):  # text/code symbols only (T/t, and weak W/w)
            continue
        entries.append([addr, size, raw, raw])
    entries.sort(key=lambda e: e[0])

    if cfilt and entries:
        names_in = "\n".join(e[2] for e in entries)
        r2 = subprocess.run([cfilt], input=names_in, capture_output=True, text=True)
        if r2.returncode == 0:
            demangled = r2.stdout.splitlines()
            if len(demangled) == len(entries):
                for e, d in zip(entries, demangled):
                    e[3] = d
    return entries


def resolve_symbol(so_path, query, range_override=None):
    """!
    @brief Resolve `query` (mangled/demangled C++ name, Ghidra `FUN_<hex>`, or raw hex
           address) to a concrete `(address, size)` pair to disassemble.
    @param so_path Path to the `.so` to inspect.
    @param query Raw CLI query string.
    @param range_override Explicit byte-size override (always wins if given).
    @return dict; either `{"error": "..."}` (`"nm_missing"`, `"not_found"`,
            `"ambiguous"` with a `"candidates"` list) or the resolved
            `{"address", "size", "raw_name", "demangled_name", "size_guessed"}`.
    """
    symbols = _load_full_symbols(so_path)
    if symbols is None:
        return {"error": "nm_missing"}

    addr_literal = _parse_address_literal(query)
    entry = None
    candidates = []

    if addr_literal is not None:
        for e in symbols:
            if e[0] == addr_literal:
                entry = e
                break
    else:
        q = query.strip()
        exact = [e for e in symbols if e[2] == q or e[3] == q]
        if exact:
            entry = exact[0]
        else:
            sub = [e for e in symbols if q.lower() in e[2].lower() or q.lower() in e[3].lower()]
            if len(sub) == 1:
                entry = sub[0]
            elif len(sub) > 1:
                candidates = sub

    if candidates:
        return {"error": "ambiguous", "candidates": candidates}
    if entry is None and addr_literal is None:
        return {"error": "not_found"}

    if entry is not None:
        address, size, raw_name, demangled = entry[0], entry[1], entry[2], entry[3]
    else:
        address, size, raw_name, demangled = addr_literal, 0, None, None

    size_guessed = False
    if range_override:
        size = range_override
    elif not size:
        later = sorted(s[0] for s in symbols if s[0] > address)
        gap = (later[0] - address) if later else 0
        size = gap if 0 < gap <= 0x1000 else 0x100
        size_guessed = True

    return {
        "address": address, "size": size, "raw_name": raw_name,
        "demangled_name": demangled, "size_guessed": size_guessed,
    }


def _disassemble_range(so_path, address, size):
    """!
    @brief Disassemble `[address, address+size)` as Thumb-2 code via
           `arm-vita-eabi-objdump -Mforce-thumb -C` (demangled labels, raw
           instruction bytes kept for byte-level review).
    @param so_path Path to the `.so` to disassemble.
    @param address Start address (Thumb bit is stripped automatically).
    @param size Number of bytes to cover.
    @return `(disassembly_text, error_message)` -- `error_message` is `None` on success.
    """
    objdump = shutil.which("arm-vita-eabi-objdump")
    if not objdump:
        return None, "objdump_missing"

    start = address & ~1
    end = start + max(size, 2)
    cmd = [objdump, "-d", f"--start-address=0x{start:x}", f"--stop-address=0x{end:x}",
           "-Mforce-thumb", "-C", so_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None, (r.stderr or "unknown objdump error").strip()

    lines, in_text = [], False
    for line in r.stdout.splitlines():
        if "Disassembly of section" in line:
            in_text = True
            continue
        if in_text:
            lines.append(line)
    return "\n".join(lines).strip("\n"), None


def _find_ghidra_snippet(project_dir, terms, max_lines=600):
    """!
    @brief Best-effort extraction of the Ghidra pseudo-C function body matching any of
           `terms` (raw mangled name, demangled name, or `FUN_<hex>`), for side-by-side
           comparison against the real disassembly.
    @param project_dir Path to the port's project directory.
    @param terms Ordered list of candidate names to search for (first match wins).
    @param max_lines Safety cap on how many source lines a single function may span.
    @return `(relative_path, start_line_1indexed, snippet_text)`, or
            `(None, None, None)` if nothing matched.
    """
    c_files = sorted(glob.glob(os.path.join(str(project_dir), "decompiled", "*", "ghidra", "*.c")))
    for term in terms:
        if not term:
            continue
        for c_path in c_files:
            try:
                with open(c_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for i, line in enumerate(lines):
                if term not in line:
                    continue
                brace_idx = None
                for j in range(i, min(i + 6, len(lines))):
                    if lines[j].strip() == "{":
                        brace_idx = j
                        break
                if brace_idx is None:
                    continue

                start = i
                k = i - 1
                while k >= 0 and k >= i - 3 and (lines[k].strip().startswith("//") or not lines[k].strip()):
                    start = k
                    k -= 1

                depth, end = 0, None
                for j in range(brace_idx, min(len(lines), brace_idx + max_lines)):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if depth == 0 and j > brace_idx:
                        end = j
                        break
                if end is None:
                    end = min(len(lines), brace_idx + max_lines) - 1

                while start < i and not lines[start].strip():
                    start += 1

                rel = os.path.relpath(c_path, str(project_dir))
                return rel, start + 1, "".join(lines[start:end + 1])
    return None, None, None


def build_disasm_bundle(project_cfg, query, global_cfg=None, so_path=None, range_override=None):
    """!
    @brief Resolve `query`, disassemble it, and pair it with a matching Ghidra
           snippet (if any) into a single result dict.
    @param project_cfg Per-project config dict.
    @param query Symbol name, `FUN_<hex>`, or raw hex address.
    @param global_cfg Global config dict (used to locate VITASDK's `bin/`).
    @param so_path Explicit `.so` path override.
    @param range_override Explicit disassembly window size in bytes.
    @return dict; either `{"error": "..."}` or the full bundle for `render_markdown()`.
    """
    project_dir = Path(project_cfg["_project_dir"])
    _ensure_vitasdk_path(global_cfg)

    so_path = _find_so(project_dir, project_cfg, so_path)
    if not so_path:
        return {"error": "so_not_found"}

    resolved = resolve_symbol(so_path, query, range_override)
    if resolved.get("error"):
        return resolved

    disasm_text, derr = _disassemble_range(so_path, resolved["address"], resolved["size"])
    if derr == "objdump_missing":
        return {"error": "objdump_missing"}
    if derr:
        return {"error": "objdump_failed", "detail": derr}

    terms = [resolved.get("raw_name"), resolved.get("demangled_name"), f"FUN_{resolved['address']:06x}"]
    ghidra_path, ghidra_line, ghidra_snippet = _find_ghidra_snippet(project_dir, terms)

    label = resolved.get("demangled_name") or resolved.get("raw_name") or query
    return {
        "query": query, "so_path": str(so_path),
        "project": project_cfg.get("project_name") or project_cfg.get("slug"),
        "label": label, "address": resolved["address"], "size": resolved["size"],
        "size_guessed": resolved.get("size_guessed", False),
        "raw_name": resolved.get("raw_name"), "demangled_name": resolved.get("demangled_name"),
        "disasm_text": disasm_text,
        "ghidra_path": ghidra_path, "ghidra_line": ghidra_line, "ghidra_snippet": ghidra_snippet,
    }


def render_markdown(bundle):
    """!
    @brief Render a resolved bundle (from `build_disasm_bundle()`) as a single
           self-contained Markdown document.
    @param bundle Bundle dict (must not carry an `"error"` key).
    @return Markdown text.
    """
    lines = [f"# Disassembly -- `{bundle['label']}`", ""]
    lines.append(f"- **{t('disasm.field_project')}**: {bundle.get('project') or '?'}")
    lines.append(f"- **{t('disasm.field_so')}**: `{bundle['so_path']}`")
    lines.append(f"- **{t('disasm.field_address')}**: `0x{bundle['address']:08x}`")
    size_note = f" ({t('disasm.size_guessed_note')})" if bundle.get("size_guessed") else ""
    lines.append(f"- **{t('disasm.field_size')}**: {bundle['size']} bytes{size_note}")
    if bundle.get("raw_name"):
        lines.append(f"- **{t('disasm.field_raw_symbol')}**: `{bundle['raw_name']}`")
    lines.append("")

    if bundle.get("ghidra_snippet"):
        lines.append(f"## {t('disasm.ghidra_heading')}")
        lines.append(f"_{bundle['ghidra_path']}:{bundle['ghidra_line']}_")
        lines.append("")
        lines.append("```c")
        lines.append(bundle["ghidra_snippet"].rstrip("\n"))
        lines.append("```")
        lines.append("")
    else:
        lines.append(f"_{t('disasm.ghidra_not_found')}_")
        lines.append("")

    lines.append(f"## {t('disasm.arm_heading')}")
    lines.append("```text")
    lines.append(bundle["disasm_text"])
    lines.append("```")
    lines.append("")

    lines.append(f"## {t('disasm.prompt_heading')}")
    lines.append(t("disasm.prompt_body"))
    return "\n".join(lines)


def _print_error(bundle):
    """!
    @brief Print the right translated error message for a failed bundle.
    @param bundle dict carrying an `"error"` key from `build_disasm_bundle()`.
    """
    err = bundle.get("error")
    if err == "so_not_found":
        print(f"{C.RED}{t('disasm.so_not_found')}{C.RESET}")
    elif err == "nm_missing":
        print(f"{C.RED}{t('disasm.nm_missing')}{C.RESET}")
    elif err == "objdump_missing":
        print(f"{C.RED}{t('disasm.objdump_missing')}{C.RESET}")
    elif err == "objdump_failed":
        print(f"{C.RED}{t('disasm.objdump_failed', detail=bundle.get('detail'))}{C.RESET}")
    elif err == "not_found":
        print(f"{C.RED}{t('disasm.symbol_not_found')}{C.RESET}")
    elif err == "ambiguous":
        print(f"{C.YELLOW}{t('disasm.multiple_matches')}{C.RESET}")
        for e in bundle["candidates"][:20]:
            print(f"    0x{e[0]:08x}  {e[3]}")
        if len(bundle["candidates"]) > 20:
            print(f"    ... {len(bundle['candidates']) - 20} more")


def export_disasm(project_cfg, query, global_cfg=None, out_path=None, stdout=False, copy=False,
                   so_path=None, range_override=None):
    """!
    @brief Build the disassembly bundle and export/print/copy it.
    @param project_cfg Per-project config dict.
    @param query Symbol name, `FUN_<hex>`, or raw hex address.
    @param global_cfg Global config dict.
    @param out_path Explicit output path (default: `decompiled/disasm/<label>_<addr>.md`).
    @param stdout Also print the rendered Markdown to standard output.
    @param copy Also copy the rendered Markdown to the system clipboard.
    @param so_path Explicit `.so` path override.
    @param range_override Explicit disassembly window size in bytes.
    @return `Path` written to, or `None` on any failure.
    """
    bundle = build_disasm_bundle(project_cfg, query, global_cfg=global_cfg, so_path=so_path,
                                  range_override=range_override)
    if bundle.get("error"):
        _print_error(bundle)
        return None

    rendered = render_markdown(bundle)

    if stdout:
        print(rendered)

    if copy:
        if copy_to_clipboard(rendered):
            print(f"{C.GREEN}{t('disasm.clipboard_ok')}{C.RESET}")
        else:
            print(f"{C.YELLOW}{t('disasm.clipboard_failed')}{C.RESET}")

    project_dir = Path(project_cfg["_project_dir"])
    safe_label = re.sub(r'[^A-Za-z0-9_.-]+', '_', bundle["label"]).strip('_')[:80] or "symbol"
    default_path = project_dir / "decompiled" / "disasm" / f"{safe_label}_0x{bundle['address']:08x}.md"
    dest = Path(out_path) if out_path else default_path

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
    except OSError as e:
        print(f"{C.RED}{t('disasm.write_failed', error=e)}{C.RESET}")
        return None

    if not stdout:
        print(f"{C.GREEN}{t('disasm.exported_ok', path=dest)}{C.RESET}")
    if bundle.get("size_guessed"):
        print(f"{C.YELLOW}{t('disasm.size_guessed_warning', size=bundle['size'])}{C.RESET}")
    return dest


def export_disasm_cli(project_cfg, query, global_cfg=None, out=None, stdout=False, copy=False,
                       so_path=None, range_override=None):
    """!
    @brief CLI entry point for the `disasm` subcommand.
    @return `0` on success, `1` on any failure.
    """
    result = export_disasm(
        project_cfg, query, global_cfg=global_cfg, out_path=out, stdout=stdout, copy=copy,
        so_path=so_path, range_override=range_override,
    )
    return 0 if result is not None else 1
