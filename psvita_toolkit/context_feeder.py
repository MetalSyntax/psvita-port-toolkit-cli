"""!
@file context_feeder.py
@brief Copiloto IA integrado: empaqueta todo lo que un asistente de código (Claude
       Code, etc.) necesita para proponer un fix de un crash en UN solo archivo.

@details
`crash_analyzer.analyze()` ya resuelve la cadena de llamadas de un crash contra los
símbolos del `.so`, la cruza contra el pseudo-C decompilado con Ghidra y el Java
decompilado con jadx, y escribe todo eso en `<dump_path>.triage_summary.md` (ver
`crash_analyzer._write_triage_summary()`). Este módulo NO reimplementa nada de eso:
lee ese Markdown ya generado, lo parsea de vuelta a una estructura de datos, le suma
una tercera fuente que `crash_analyzer` no mira (el stub JNI actual en
`generated_jni_stubs.c`/`source/**/*.c`, generado por `jni_analyzer.generate_jni_stubs()`),
y arma con las tres un documento único -- listo para pegar en un chat de IA o pasarle
a `claude -p` -- o el mismo contenido como JSON para consumo por otra herramienta.

Sin este módulo, portear un fix significaba abrir a mano 4 cosas (el log/dump, el
pseudo-C de Ghidra, el Java de jadx, y el stub .c) y copiar/pegar cada una por
separado en el chat del asistente. `export_context()` deja todo eso en un archivo.

See `docs/dev-notes/context_feeder.md` for why this parses `.triage_summary.md`
instead of duplicating `crash_analyzer`'s cross-referencing, why truncation is an
honest character budget instead of a silent hard cap, and why
`generated_jni_stubs.c` is checked in two possible locations.
"""

import glob
import json
import os
import re
from pathlib import Path

from . import i18n
from .i18n import t
from . import tui
from .tui import C

STRINGS = {
    "context_feeder.dump_not_found": {
        "es": "[-] Archivo dump no encontrado: {path}",
        "en": "[-] Dump file not found: {path}",
        "pt": "[-] Arquivo de dump não encontrado: {path}",
    },
    "context_feeder.bad_format": {
        "es": "[-] Formato desconocido: '{fmt}' (usar 'markdown' o 'json').",
        "en": "[-] Unknown format: '{fmt}' (use 'markdown' or 'json').",
        "pt": "[-] Formato desconhecido: '{fmt}' (use 'markdown' ou 'json').",
    },
    "context_feeder.bundle_failed": {
        "es": "[-] No se pudo generar el contexto -- revisar que el análisis del crash haya funcionado (ver <dump>.triage_summary.md).",
        "en": "[-] Couldn't build the context -- check that the crash analysis actually ran (see <dump>.triage_summary.md).",
        "pt": "[-] Não foi possível gerar o contexto -- verifique se a análise do crash rodou (veja <dump>.triage_summary.md).",
    },
    "context_feeder.write_failed": {
        "es": "[-] No se pudo escribir el archivo de contexto: {error}",
        "en": "[-] Couldn't write the context file: {error}",
        "pt": "[-] Não foi possível escrever o arquivo de contexto: {error}",
    },
    "context_feeder.exported_ok": {
        "es": "[+] Contexto para IA exportado a: {path}",
        "en": "[+] AI context exported to: {path}",
        "pt": "[+] Contexto para IA exportado para: {path}",
    },
    "context_feeder.menu_no_dumps": {
        "es": "[-] No hay ningún crash dump descargado todavía en logs/.",
        "en": "[-] No crash dump has been downloaded yet in logs/.",
        "pt": "[-] Ainda não há nenhum crash dump baixado em logs/.",
    },
    "context_feeder.menu_no_dumps_hint": {
        "es": "    Usá 'Descargar logs / crash dumps' primero.",
        "en": "    Use 'Download logs / crash dumps' first.",
        "pt": "    Use 'Baixar logs / crash dumps' primeiro.",
    },
    "context_feeder.menu_pick_dump": {
        "es": "Elegí el crash dump para el que querés armar el contexto",
        "en": "Pick the crash dump to build the context for",
        "pt": "Escolha o crash dump para o qual montar o contexto",
    },
    "context_feeder.menu_pick_format": {
        "es": "Formato de salida",
        "en": "Output format",
        "pt": "Formato de saída",
    },
    "context_feeder.format_markdown": {
        "es": "Markdown (para pegar en un chat de IA o pasarle a 'claude -p')",
        "en": "Markdown (paste into an AI chat, or hand it to 'claude -p')",
        "pt": "Markdown (colar em um chat de IA, ou passar para 'claude -p')",
    },
    "context_feeder.format_json": {
        "es": "JSON (para otra herramienta que consuma datos estructurados)",
        "en": "JSON (for another tool that wants structured data)",
        "pt": "JSON (para outra ferramenta que consome dados estruturados)",
    },
    "context_feeder.doc_title": {
        "es": "Contexto de crash para asistente de IA",
        "en": "Crash context for AI assistant",
        "pt": "Contexto de crash para assistente de IA",
    },
    "context_feeder.dump_line": {
        "es": "**Dump:** `{path}`",
        "en": "**Dump:** `{path}`",
        "pt": "**Dump:** `{path}`",
    },
    "context_feeder.crash_instruction_heading": {
        "es": "Instrucción que crasheó",
        "en": "Crashing instruction",
        "pt": "Instrução que crashou",
    },
    "context_feeder.no_crash_instruction": {
        "es": "_No se pudo determinar la instrucción exacta del crash (ver `<dump>.analysis.txt` para el detalle completo)._",
        "en": "_Couldn't determine the exact crashing instruction (see `<dump>.analysis.txt` for the full detail)._",
        "pt": "_Não foi possível determinar a instrução exata do crash (veja `<dump>.analysis.txt` para o detalhe completo)._",
    },
    "context_feeder.symbols_heading": {
        "es": "Funciones involucradas, cruzadas contra las 3 fuentes",
        "en": "Involved functions, cross-referenced against all 3 sources",
        "pt": "Funções envolvidas, cruzadas com as 3 fontes",
    },
    "context_feeder.no_symbols_found": {
        "es": "_No se encontraron coincidencias en `decompiled/` ni en el stub JNI actual -- ver `<dump>.triage_summary.md` para el detalle._",
        "en": "_No matches found under `decompiled/` or in the current JNI stub -- see `<dump>.triage_summary.md` for detail._",
        "pt": "_Nenhuma correspondência encontrada em `decompiled/` nem no stub JNI atual -- veja `<dump>.triage_summary.md` para o detalhe._",
    },
    "context_feeder.ghidra_label": {
        "es": "Ghidra (pseudo-C nativo)",
        "en": "Ghidra (native pseudo-C)",
        "pt": "Ghidra (pseudo-C nativo)",
    },
    "context_feeder.jadx_label": {
        "es": "JADX (Java original)",
        "en": "JADX (original Java)",
        "pt": "JADX (Java original)",
    },
    "context_feeder.stub_label": {
        "es": "Stub actual (FalsoJNI/source)",
        "en": "Current stub (FalsoJNI/source)",
        "pt": "Stub atual (FalsoJNI/source)",
    },
    "context_feeder.more_not_shown": {
        "es": "(+{count} coincidencia(s) más no mostradas por el límite de tamaño)",
        "en": "(+{count} more match(es) not shown due to the size budget)",
        "pt": "(+{count} correspondência(s) a mais não mostradas pelo limite de tamanho)",
    },
    "context_feeder.symbols_omitted": {
        "es": "(+{count} símbolo(s) más no mostrados por el límite de tamaño: {names})",
        "en": "(+{count} more symbol(s) not shown due to the size budget: {names})",
        "pt": "(+{count} símbolo(s) a mais não mostrados pelo limite de tamanho: {names})",
    },
    "context_feeder.prompt_heading": {
        "es": "Pedido al asistente",
        "en": "Request to the assistant",
        "pt": "Pedido ao assistente",
    },
    "context_feeder.prompt_body": {
        "es": (
            "Con el contexto de arriba (instrucción del crash, pseudo-C de Ghidra, Java original\n"
            "de jadx, y el stub JNI actual), proponé un fix concreto. Respondé con:\n\n"
            "1. Diagnóstico breve (2-3 líneas): qué está fallando y por qué.\n"
            "2. El fix como un patch unificado (formato `diff`, aplicable con `git apply`) sobre\n"
            "   el/los archivo(s) de `source/` que corresponda -- no reescribas el archivo entero.\n"
            "3. Si el fix depende de algo que no está en este contexto (otro archivo, otra parte\n"
            "   del stub, un dato que hace falta confirmar), decilo explícitamente en vez de inventarlo."
        ),
        "en": (
            "Using the context above (crash instruction, Ghidra pseudo-C, original jadx Java,\n"
            "and the current JNI stub), propose a concrete fix. Reply with:\n\n"
            "1. A short diagnosis (2-3 lines): what's failing and why.\n"
            "2. The fix as a unified diff (`diff` format, applyable with `git apply`) against\n"
            "   the relevant `source/` file(s) -- don't rewrite the whole file.\n"
            "3. If the fix depends on something not in this context (another file, another part\n"
            "   of the stub, something that needs confirming), say so explicitly instead of guessing."
        ),
        "pt": (
            "Com o contexto acima (instrução do crash, pseudo-C do Ghidra, Java original do jadx,\n"
            "e o stub JNI atual), proponha uma correção concreta. Responda com:\n\n"
            "1. Diagnóstico breve (2-3 linhas): o que está falhando e por quê.\n"
            "2. A correção como um patch unificado (formato `diff`, aplicável com `git apply`)\n"
            "   sobre o(s) arquivo(s) de `source/` que corresponda -- não reescreva o arquivo inteiro.\n"
            "3. Se a correção depender de algo que não está neste contexto (outro arquivo, outra\n"
            "   parte do stub, um dado que precisa confirmar), diga isso explicitamente em vez de inventar."
        ),
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Parsing crash_analyzer's already-written <dump>.triage_summary.md
# ---------------------------------------------------------------------------

# The triage summary has exactly one fenced code block: the crashing
# instruction (see crash_analyzer._write_triage_summary()). Matched
# language-independently -- it never depends on the active i18n language
# matching whatever language the summary was originally written in.
_CRASH_INSTRUCTION_RE = re.compile(r"```\n(.*?)\n```", re.S)

# "### `symbol_name`" -- one per cross-referenced symbol.
_SYMBOL_HEADING_RE = re.compile(r"^### `(.+?)`\s*$", re.M)

# Any level-2 or level-3 Markdown heading, used to find where a symbol's
# section ends (the next heading of either level).
_NEXT_HEADING_RE = re.compile(r"^#{2,3} ", re.M)

# "- `relative/path:line` -- `code text`" -- one per Ghidra/JADX hit, written
# by crash_analyzer._write_triage_summary(). Whether a hit is Ghidra or JADX
# is told apart by the path's extension (.c vs .java), not by the (translated,
# hence unstable) bold label text above it.
_HIT_BULLET_RE = re.compile(r"^- `(.+?):(\d+)` -- `(.*)`$", re.M)


def _parse_triage_summary(text):
    """!
    @brief Parse `crash_analyzer._write_triage_summary()`'s Markdown back into
           plain data, without depending on any translated heading/label text.
    @param text Full contents of a `<dump_path>.triage_summary.md` file.
    @return `(crash_instruction, symbols)` -- `crash_instruction` is the
            crashing instruction line (str) or `None` if the summary has none;
            `symbols` is a list of `{"symbol", "ghidra_hits", "jadx_hits"}`
            dicts, each hit list already `(rel_path, line_no, text)` tuples
            with paths relative to the project dir (as the summary wrote them).
    """
    crash_instruction = None
    m = _CRASH_INSTRUCTION_RE.search(text)
    if m:
        crash_instruction = m.group(1).strip()

    symbols = []
    headings = list(_SYMBOL_HEADING_RE.finditer(text))
    for i, hm in enumerate(headings):
        start = hm.end()
        next_heading = _NEXT_HEADING_RE.search(text, start)
        end = next_heading.start() if next_heading else len(text)
        section = text[start:end]

        ghidra_hits, jadx_hits = [], []
        for bm in _HIT_BULLET_RE.finditer(section):
            path, line_no, line_text = bm.group(1), int(bm.group(2)), bm.group(3)
            (jadx_hits if path.endswith(".java") else ghidra_hits).append((path, line_no, line_text))

        symbols.append({"symbol": hm.group(1), "ghidra_hits": ghidra_hits, "jadx_hits": jadx_hits})
    return crash_instruction, symbols


def _relpath(project_dir, path):
    """!
    @brief Best-effort `os.path.relpath()`, tolerant of paths that can't be
           made relative (e.g. a different drive on Windows).
    @param project_dir Path to the port's project directory.
    @param path Absolute (or already-relative) path to shorten.
    @return Relative path string, or `path` unchanged if relativizing fails.
    """
    try:
        return os.path.relpath(path, project_dir)
    except ValueError:
        return path


def _find_stub_matches(project_dir, symbol_name, max_hits=5):
    """!
    @brief Grep the port's current FalsoJNI stub scaffolding and its own
           `source/` for `symbol_name` (or the Java method name derived from
           it, if it looks like a `Java_...` JNI export) -- the third source
           `crash_analyzer` doesn't cross-reference on its own.
    @details Checks `generated_jni_stubs.c` in both places
             `jni_analyzer.generate_jni_stubs()` can write it to
             (`<project_dir>/source/` if that directory exists, else
             `<project_dir>/` itself), then every other `*.c` under
             `<project_dir>/source/` (recursively, skipping macOS `._*` junk).
    @param project_dir Path to the port's project directory.
    @param symbol_name Function/symbol name to search for (as parsed from the
           triage summary -- may be a demangled C++ name, a plain C name, or
           a `Java_...` JNI export name).
    @param max_hits Stop after this many matching lines (across all files).
    @return list of `(file_path, line_no, line_text)` tuples (absolute paths --
            relativized by the caller, matching `_find_ghidra_matches()`'s
            and `_find_jadx_matches()`'s tuple shape).
    """
    if not symbol_name:
        return []

    from . import crash_analyzer
    search_terms = [symbol_name]
    jni_method = crash_analyzer._jni_method_name_from_symbol(symbol_name)
    if jni_method and jni_method not in search_terms:
        search_terms.append(jni_method)

    candidate_files = []
    for stub_path in (
        os.path.join(project_dir, "source", "generated_jni_stubs.c"),
        os.path.join(project_dir, "generated_jni_stubs.c"),
    ):
        if os.path.isfile(stub_path) and stub_path not in candidate_files:
            candidate_files.append(stub_path)
    for c_path in sorted(glob.glob(os.path.join(project_dir, "source", "**", "*.c"), recursive=True)):
        if os.path.basename(c_path).startswith("._"):
            continue
        if c_path not in candidate_files:
            candidate_files.append(c_path)

    hits = []
    for c_path in candidate_files:
        try:
            with open(c_path, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if any(term in line for term in search_terms):
                        hits.append((c_path, i, line.strip()))
                        if len(hits) >= max_hits:
                            return hits
        except OSError:
            continue
    return hits


# ---------------------------------------------------------------------------
# Character budget: trim least-relevant hits first, but always say what got cut
# ---------------------------------------------------------------------------

def _hit_cost(hit):
    """!
    @brief Rough character-cost estimate of rendering one `(path, line_no, text)` hit.
    @param hit `(path, line_no, text)` tuple.
    @return Estimated character count, including formatting overhead.
    """
    path, line_no, text = hit
    return len(path) + len(str(line_no)) + len(text) + 20


def _fit_hits(hits, budget):
    """!
    @brief Keep as many hits (in their existing, already-most-relevant-first
           order) as fit in `budget`, always keeping at least the first one.
    @param hits list of `(path, line_no, text)` tuples.
    @param budget Remaining character budget (may be `<= 0`).
    @return `(kept_hits, cut_count)`.
    @note Always keeping the first hit even over a near-zero budget is
          deliberate: a symbol section with zero evidence shown is far less
          useful than one slightly over budget with at least one code line.
    """
    if not hits:
        return [], 0
    kept = [hits[0]]
    used = _hit_cost(hits[0])
    cut = 0
    for h in hits[1:]:
        cost = _hit_cost(h)
        if used + cost <= budget:
            kept.append(h)
            used += cost
        else:
            cut += 1
    return kept, cut


def _apply_budget(dump_path, crash_instruction, symbol_entries, max_chars):
    """!
    @brief Trim `symbol_entries`' hit lists (and, if needed, whole trailing
           symbols) so the rendered bundle stays around `max_chars`, without
           ever silently dropping something without saying so.
    @param dump_path Path to the `.psp2dmp` being analyzed (counts toward the
           fixed overhead).
    @param crash_instruction Crash instruction text, or `None`.
    @param symbol_entries list of `{"symbol", "ghidra_hits", "jadx_hits",
           "stub_hits"}` dicts, in relevance order (as found on the crashed
           thread's resolved call chain).
    @param max_chars Target character budget for the whole bundle.
    @return `(trimmed_entries, global_notes)` -- `trimmed_entries` is
            `symbol_entries` with each hit list possibly shortened and an
            `"omitted"` dict (`{"ghidra": n, "jadx": n, "stub": n}`, only
            present keys) added; `global_notes` is a list of translated
            strings describing whole symbols dropped for lack of budget (empty
            if none were).
    """
    fixed_overhead = len(crash_instruction or "") + len(dump_path) + 300
    remaining = max(max_chars - fixed_overhead, 0)

    trimmed = []
    omitted_symbols = []
    for i, entry in enumerate(symbol_entries):
        if remaining <= 0 and trimmed:
            omitted_symbols = [s["symbol"] for s in symbol_entries[i:]]
            break

        ghidra_hits, g_cut = _fit_hits(entry["ghidra_hits"], remaining)
        remaining -= sum(_hit_cost(h) for h in ghidra_hits)
        jadx_hits, j_cut = _fit_hits(entry["jadx_hits"], remaining)
        remaining -= sum(_hit_cost(h) for h in jadx_hits)
        stub_hits, s_cut = _fit_hits(entry["stub_hits"], remaining)
        remaining -= sum(_hit_cost(h) for h in stub_hits)

        omitted = {}
        if g_cut:
            omitted["ghidra"] = g_cut
        if j_cut:
            omitted["jadx"] = j_cut
        if s_cut:
            omitted["stub"] = s_cut

        trimmed.append({
            "symbol": entry["symbol"],
            "ghidra_hits": ghidra_hits,
            "jadx_hits": jadx_hits,
            "stub_hits": stub_hits,
            "omitted": omitted,
        })

    global_notes = []
    if omitted_symbols:
        names = ", ".join(omitted_symbols[:5]) + ("..." if len(omitted_symbols) > 5 else "")
        global_notes.append(t("context_feeder.symbols_omitted", count=len(omitted_symbols), names=names))
    return trimmed, global_notes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_context_bundle(project_cfg, dump_path, global_cfg=None, max_chars=12000):
    """!
    @brief Build the plain-data structure behind `export_context()`: the
           crash instruction plus, per crashed-chain symbol, its Ghidra/JADX
           cross-references (parsed from `crash_analyzer`'s
           `<dump_path>.triage_summary.md`) and the current FalsoJNI stub hits
           (grepped fresh by this module).
    @param project_cfg Per-project config dict (needs `_project_dir`).
    @param dump_path Path to the `.psp2dmp`/`psp2core-*` file.
    @param global_cfg Global config dict, forwarded to `crash_analyzer.analyze()`
           if the triage summary doesn't exist yet.
    @param max_chars Target character budget for the whole bundle; hits
           beyond it are trimmed (least-relevant first) via `_apply_budget()`,
           never silently -- see `docs/dev-notes/context_feeder.md`.
    @return dict with `dump_path`, `crash_instruction` (str or `None`),
            `symbols` (list of `{"symbol", "ghidra_hits", "jadx_hits",
            "stub_hits", "omitted"}` dicts), and `truncation_notes` (list of
            translated strings, empty if nothing was cut) -- or `None` if
            `dump_path` doesn't exist or its triage summary couldn't be
            produced/read.
    """
    project_dir = project_cfg["_project_dir"]
    dump_path = str(dump_path)
    triage_path = f"{dump_path}.triage_summary.md"

    if not os.path.isfile(triage_path):
        from . import crash_analyzer
        crash_analyzer.analyze(project_cfg, dump_path, global_cfg=global_cfg)

    if not os.path.isfile(triage_path):
        return None

    try:
        text = Path(triage_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    crash_instruction, parsed_symbols = _parse_triage_summary(text)

    symbol_entries = []
    for sym in parsed_symbols:
        stub_hits_raw = _find_stub_matches(project_dir, sym["symbol"])
        stub_hits = [(_relpath(project_dir, p), n, txt) for p, n, txt in stub_hits_raw]
        symbol_entries.append({
            "symbol": sym["symbol"],
            "ghidra_hits": sym["ghidra_hits"],
            "jadx_hits": sym["jadx_hits"],
            "stub_hits": stub_hits,
        })

    trimmed_symbols, truncation_notes = _apply_budget(dump_path, crash_instruction, symbol_entries, max_chars)

    return {
        "dump_path": dump_path,
        "crash_instruction": crash_instruction,
        "symbols": trimmed_symbols,
        "truncation_notes": truncation_notes,
    }


def _format_hit_block(hits):
    """!
    @brief Render a list of `(path, line_no, text)` hits as fenced-code-block body lines.
    @param hits list of `(path, line_no, text)` tuples.
    @return list of lines (a `// path:line` comment followed by the code line, per hit).
    """
    lines = []
    for path, line_no, text in hits:
        lines.append(f"// {path}:{line_no}")
        lines.append(text)
    return lines


def render_markdown(bundle):
    """!
    @brief Render a `build_context_bundle()` result as a single Markdown
           document meant to be pasted directly into an AI chat, or saved and
           handed to `claude -p`.
    @param bundle Result of `build_context_bundle()`.
    @return Markdown document string.
    """
    lines = [f"# {t('context_feeder.doc_title')}", "", t("context_feeder.dump_line", path=bundle["dump_path"]), ""]

    lines += [f"## {t('context_feeder.crash_instruction_heading')}", ""]
    if bundle.get("crash_instruction"):
        lines += ["```", bundle["crash_instruction"], "```", ""]
    else:
        lines += [t("context_feeder.no_crash_instruction"), ""]

    symbols = bundle.get("symbols") or []
    lines += [f"## {t('context_feeder.symbols_heading')}", ""]
    if not symbols:
        lines += [t("context_feeder.no_symbols_found"), ""]

    for entry in symbols:
        lines.append(f"### `{entry['symbol']}`")
        lines.append("")
        omitted = entry.get("omitted", {})

        if entry["ghidra_hits"]:
            lines.append(f"**{t('context_feeder.ghidra_label')}**")
            lines.append("")
            lines.append("```c")
            lines.extend(_format_hit_block(entry["ghidra_hits"]))
            lines.append("```")
            if omitted.get("ghidra"):
                lines.append(f"_{t('context_feeder.more_not_shown', count=omitted['ghidra'])}_")
            lines.append("")

        if entry["jadx_hits"]:
            lines.append(f"**{t('context_feeder.jadx_label')}**")
            lines.append("")
            lines.append("```java")
            lines.extend(_format_hit_block(entry["jadx_hits"]))
            lines.append("```")
            if omitted.get("jadx"):
                lines.append(f"_{t('context_feeder.more_not_shown', count=omitted['jadx'])}_")
            lines.append("")

        if entry["stub_hits"]:
            lines.append(f"**{t('context_feeder.stub_label')}**")
            lines.append("")
            lines.append("```c")
            lines.extend(_format_hit_block(entry["stub_hits"]))
            lines.append("```")
            if omitted.get("stub"):
                lines.append(f"_{t('context_feeder.more_not_shown', count=omitted['stub'])}_")
            lines.append("")

    for note in bundle.get("truncation_notes") or []:
        lines.append(f"_{note}_")
    if bundle.get("truncation_notes"):
        lines.append("")

    lines += [f"## {t('context_feeder.prompt_heading')}", "", t("context_feeder.prompt_body"), ""]
    return "\n".join(lines)


def render_json(bundle):
    """!
    @brief Render a `build_context_bundle()` result as JSON, for tooling that
           wants structured input instead of Markdown.
    @param bundle Result of `build_context_bundle()`.
    @return JSON string (`indent=2`, non-ASCII characters kept literal).
    """
    return json.dumps(bundle, indent=2, ensure_ascii=False)


def export_context(project_cfg, dump_path, global_cfg=None, fmt="markdown", out_path=None):
    """!
    @brief Build the crash context bundle and write it to disk in one call --
           the function behind both `export_context_cli()` and `export_context_menu()`.
    @param project_cfg Per-project config dict.
    @param dump_path Path to the `.psp2dmp`/`psp2core-*` file.
    @param global_cfg Global config dict, forwarded to `build_context_bundle()`.
    @param fmt `"markdown"` or `"json"`.
    @param out_path Explicit output path; defaults to `<dump_path>.context.md`
           or `<dump_path>.context.json` depending on `fmt`.
    @return The `Path` written, or `None` on failure (`dump_path` missing, bad
            `fmt`, the bundle couldn't be built, or the write itself failed --
            each case prints a `[-]` error first instead of raising).
    """
    dump_path = str(dump_path)
    if not os.path.isfile(dump_path):
        print(f"{C.RED}{t('context_feeder.dump_not_found', path=dump_path)}{C.RESET}")
        return None
    if fmt not in ("markdown", "json"):
        print(f"{C.RED}{t('context_feeder.bad_format', fmt=fmt)}{C.RESET}")
        return None

    bundle = build_context_bundle(project_cfg, dump_path, global_cfg=global_cfg)
    if bundle is None:
        print(f"{C.RED}{t('context_feeder.bundle_failed')}{C.RESET}")
        return None

    rendered = render_markdown(bundle) if fmt == "markdown" else render_json(bundle)
    ext = "context.md" if fmt == "markdown" else "context.json"
    dest = Path(out_path) if out_path else Path(f"{dump_path}.{ext}")

    try:
        dest.write_text(rendered, encoding="utf-8")
    except OSError as e:
        print(f"{C.RED}{t('context_feeder.write_failed', error=e)}{C.RESET}")
        return None

    print(f"{C.GREEN}{t('context_feeder.exported_ok', path=dest)}{C.RESET}")
    return dest


def export_context_cli(project_cfg, dump_path, global_cfg=None, fmt="markdown", out=None):
    """!
    @brief CLI-shaped wrapper around `export_context()`: same behavior, but
           returns a process-style exit code instead of a `Path`/`None`.
    @details Not wired into `cli.py` yet -- meant to back a future
             `psvita-toolkit export-context <dump_path> [--project PATH]
             [--format markdown|json] [--out PATH]` subcommand, following the
             same `_cmd_*(args)` -> handler-in-`_HANDLERS` pattern `cli.py`
             already uses for `_cmd_analyze()` et al. This function is the
             handler's body; the subcommand wiring itself is a separate pass.
    @param project_cfg Per-project config dict.
    @param dump_path Path to the `.psp2dmp`/`psp2core-*` file.
    @param global_cfg Global config dict.
    @param fmt `"markdown"` or `"json"`.
    @param out Explicit output path (CLI's `--out`); default naming applies if omitted.
    @return `0` on success, `1` on any failure (`export_context()` already
            printed the specific `[-]` error).
    """
    result = export_context(project_cfg, dump_path, global_cfg=global_cfg, fmt=fmt, out_path=out)
    return 0 if result is not None else 1


def export_context_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: pick a locally downloaded crash dump, pick a
           format, and export its AI context bundle.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    from . import ftp_ops
    dumps = ftp_ops.list_local_history(project_cfg, "dumps")
    if not dumps:
        print(f"{C.YELLOW}{t('context_feeder.menu_no_dumps')}{C.RESET}")
        print(f"{C.DIM}{t('context_feeder.menu_no_dumps_hint')}{C.RESET}")
        return

    chosen = tui.select_list(t("context_feeder.menu_pick_dump"), dumps, label_fn=lambda p: p.name)
    if chosen is None:
        return

    formats = [("markdown", t("context_feeder.format_markdown")), ("json", t("context_feeder.format_json"))]
    fmt_choice = tui.select_list(t("context_feeder.menu_pick_format"), formats, label_fn=lambda f: f[1])
    if fmt_choice is None:
        return
    fmt, _label = fmt_choice

    export_context(project_cfg, str(chosen), global_cfg=global_cfg, fmt=fmt)
