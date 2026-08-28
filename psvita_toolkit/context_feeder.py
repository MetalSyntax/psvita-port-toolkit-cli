"""!
@file context_feeder.py
@brief Copiloto IA integrado y optimizador de tokens: empaqueta de forma quirúrgica
       todo lo que un modelo de IA necesita para diagnosticar y resolver un crash en PS Vita.

@details
Combina en un único archivo Markdown/JSON ultra-optimizado en tokens:
1. Diagnóstico de registros CPU, hilo y causa raíz desde `<dump_path>.analysis.txt`.
2. Instrucción exacta del crash y desensamblado ARM/Thumb.
3. Últimas líneas relevantes del log (live log / UDP / consola) previas al crash.
4. Snippets acotados (con contexto de líneas) de Ghidra pseudo-C, JADX Java y stubs C de `source/`.
5. Prompt automatizado estructurado para solicitar directamente el diagnóstico y el patch diff.

Permite tanto modo interactivo rápido (auto-detección) como modo interactivo personalizado
(ingreso manual de rutas a DMP, Log, .so, Decompilados y Stubs).
Incluye soporte para copiar directamente al portapapeles (`--copy`), emitir por salida estándar (`--stdout`),
y limitar estrictamente el presupuesto de caracteres/tokens.
"""

import glob
import json
import os
import re
import subprocess
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
    "context_feeder.clipboard_ok": {
        "es": "[+] ¡Prompt optimizado de crash copiado al portapapeles!",
        "en": "[+] Optimized crash prompt copied to clipboard!",
        "pt": "[+] Prompt otimizado de crash copiado para a área de transferência!",
    },
    "context_feeder.clipboard_failed": {
        "es": "[-] No se pudo copiar al portapapeles automáticamente (herramienta del sistema no encontrada).",
        "en": "[-] Could not copy to clipboard automatically (system tool not found).",
        "pt": "[-] Não foi possível copiar para a área de transferência automaticamente.",
    },
    "context_feeder.menu_no_dumps": {
        "es": "[-] No hay ningún crash dump descargado todavía en logs/.",
        "en": "[-] No crash dump has been downloaded yet in logs/.",
        "pt": "[-] Ainda não há nenhum crash dump baixado em logs/.",
    },
    "context_feeder.menu_no_dumps_hint": {
        "es": "    Usá 'Descargar logs / crash dumps' primero o ingresa una ruta manual.",
        "en": "    Use 'Download logs / crash dumps' first or enter a manual path.",
        "pt": "    Use 'Baixar logs / crash dumps' primeiro ou insira um caminho manual.",
    },
    "context_feeder.menu_mode_title": {
        "es": "Exportar contexto para copiloto IA -- Seleccionar modo",
        "en": "Export AI Copilot Context -- Select Mode",
        "pt": "Exportar contexto para copiloto IA -- Selecionar modo",
    },
    "context_feeder.mode_auto": {
        "es": "🚀 Modo Rápido (Auto-detectar dump, logs, .so y decompilados del proyecto)",
        "en": "🚀 Fast Mode (Auto-detect dump, logs, .so and decompiled project files)",
        "pt": "🚀 Modo Rápido (Auto-detectar dump, logs, .so e descompilados do projeto)",
    },
    "context_feeder.mode_custom": {
        "es": "⚙️ Modo Personalizado (Especificar / Editar rutas de DMP, Log, .so, Decompilados y Stubs)",
        "en": "⚙️ Custom Mode (Specify / Edit paths for DMP, Log, .so, Decompiled and Stubs)",
        "pt": "⚙️ Modo Personalizado (Especificar / Editar caminhos de DMP, Log, .so, Descompilados e Stubs)",
    },
    "context_feeder.prompt_dump": {
        "es": "Ruta al archivo Crash Dump (.psp2dmp / psp2core-*)",
        "en": "Path to Crash Dump file (.psp2dmp / psp2core-*)",
        "pt": "Caminho para o arquivo Crash Dump (.psp2dmp / psp2core-*)",
    },
    "context_feeder.prompt_log": {
        "es": "Ruta al archivo Log / Live Log (.log / consola) [opcional, Enter para omitir]",
        "en": "Path to Log / Live Log file (.log / console) [optional, Enter to skip]",
        "pt": "Caminho para o arquivo Log / Live Log (.log / console) [opcional, Enter para pular]",
    },
    "context_feeder.prompt_so": {
        "es": "Ruta al archivo binario .so de Android [opcional, Enter para omitir]",
        "en": "Path to Android .so binary [optional, Enter to skip]",
        "pt": "Caminho para o binário .so do Android [opcional, Enter para pular]",
    },
    "context_feeder.prompt_decompiled": {
        "es": "Ruta a la carpeta de Decompilados (Ghidra / jadx) [opcional, Enter para omitir]",
        "en": "Path to Decompiled folder (Ghidra / jadx) [optional, Enter to skip]",
        "pt": "Caminho para a pasta de Descompilados (Ghidra / jadx) [opcional, Enter para pular]",
    },
    "context_feeder.prompt_stubs": {
        "es": "Ruta a la carpeta de código fuente / Stubs (source/) [opcional, Enter para omitir]",
        "en": "Path to source code / Stubs folder (source/) [optional, Enter to skip]",
        "pt": "Caminho para a pasta de código-fonte / Stubs (source/) [opcional, Enter para pular]",
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
        "es": "Markdown (para pegar en un chat de IA o pasarle a un agente)",
        "en": "Markdown (paste into an AI chat or pass to an agent)",
        "pt": "Markdown (colar em um chat de IA ou passar para um agente)",
    },
    "context_feeder.format_json": {
        "es": "JSON (para otra herramienta que consuma datos estructurados)",
        "en": "JSON (for another tool that wants structured data)",
        "pt": "JSON (para outra ferramenta que consome dados estructurados)",
    },
    "context_feeder.confirm_view": {
        "es": "¿Deseas ver el prompt generado en pantalla ahora?",
        "en": "Do you want to preview the generated prompt on screen now?",
        "pt": "Deseja visualizar o prompt gerado na tela agora?",
    },
    "context_feeder.doc_title": {
        "es": "Contexto y Triaje de Crash para Asistente IA (PS Vita Port)",
        "en": "PS Vita Crash Triage & AI Copilot Context",
        "pt": "Contexto e Triagem de Crash para Assistente IA (PS Vita Port)",
    },
    "context_feeder.dump_line": {
        "es": "**Dump:** `{path}`",
        "en": "**Dump:** `{path}`",
        "pt": "**Dump:** `{path}`",
    },
    "context_feeder.diagnostics_heading": {
        "es": "Diagnóstico de CPU y Causa Raíz",
        "en": "CPU Diagnostics & Root Cause",
        "pt": "Diagnóstico de CPU e Causa Raiz",
    },
    "context_feeder.crash_instruction_heading": {
        "es": "Instrucción del Crash (Ensamblador)",
        "en": "Crashing Instruction (Disassembly)",
        "pt": "Instrução do Crash (Desmontagem)",
    },
    "context_feeder.no_crash_instruction": {
        "es": "_No se pudo determinar la instrucción exacta del crash (ver `<dump>.analysis.txt` para el detalle completo)._",
        "en": "_Couldn't determine the exact crashing instruction (see `<dump>.analysis.txt` for the full detail)._",
        "pt": "_Não foi possível determinar a instrução exata do crash (veja `<dump>.analysis.txt` para o detalhe completo)._",
    },
    "context_feeder.log_heading": {
        "es": "Log previo al Crash (últimas líneas)",
        "en": "Log Tail Prior to Crash",
        "pt": "Log prévio ao Crash (últimas líneas)",
    },
    "context_feeder.no_log_found": {
        "es": "_No se encontró log de consola o live log reciente._",
        "en": "_No recent console or live log found._",
        "pt": "_Nenhum log de console ou live log recente encontrado._",
    },
    "context_feeder.symbols_heading": {
        "es": "Evidencia de Código Cruzada (Ghidra, JADX y Stubs C)",
        "en": "Cross-Referenced Code Evidence (Ghidra, JADX & C Stubs)",
        "pt": "Evidência de Código Cruzada (Ghidra, JADX e Stubs C)",
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
        "es": "Stub actual (FalsoJNI / source)",
        "en": "Current stub (FalsoJNI / source)",
        "pt": "Stub atual (FalsoJNI / source)",
    },
    "context_feeder.more_not_shown": {
        "es": "(+{count} coincidencia(s) más no mostradas para ahorrar tokens)",
        "en": "(+{count} more match(es) omitted to save tokens)",
        "pt": "(+{count} correspondência(s) omitidas para economizar tokens)",
    },
    "context_feeder.symbols_omitted": {
        "es": "(+{count} símbolo(s) adicionales omitidos por presupuesto de tokens: {names})",
        "en": "(+{count} additional symbol(s) omitted due to token budget: {names})",
        "pt": "(+{count} símbolo(s) adicionais omitidos por orçamento de tokens: {names})",
    },
    "context_feeder.prompt_heading": {
        "es": "Instrucciones para la IA (Pedido Automatizado)",
        "en": "AI Prompt & Request",
        "pt": "Instruções para a IA (Pedido Automatizado)",
    },
    "context_feeder.prompt_body": {
        "es": (
            "Con el contexto provisto arriba (diagnóstico de CPU, instrucción en crash, últimas líneas de log,\n"
            "pseudo-C de Ghidra, Java de jadx y stub C actual), proporciona una solución directa:\n\n"
            "1. **Diagnóstico conciso** (2-3 líneas): Causa exacta de la falla y por qué falló en runtime.\n"
            "2. **Patch unificado (`git diff`)**: Código corregido listo para aplicar sobre el archivo de `source/` o `loader/` correspondiente.\n"
            "3. **Detalles no cubiertos**: Si el fix requiere implementar un stub adicional o manejar un caso borde no mostrado, indícalo claramente."
        ),
        "en": (
            "Using the context above (CPU diagnostics, crash instruction, log tail, Ghidra pseudo-C,\n"
            "jadx Java, and current C stub), provide a direct fix:\n\n"
            "1. **Concise diagnosis** (2-3 lines): Exact root cause and why it failed at runtime.\n"
            "2. **Unified diff patch (`git diff`)**: Fixed code ready to apply onto the relevant file in `source/` or `loader/`.\n"
            "3. **Missing dependencies**: If the fix requires implementing an extra stub or handling an edge case, state it explicitly."
        ),
        "pt": (
            "Com o contexto acima (diagnóstico de CPU, instrução do crash, log recente, pseudo-C do Ghidra,\n"
            "Java do jadx e stub C atual), forneça uma solução direta:\n\n"
            "1. **Diagnóstico conciso** (2-3 linhas): Causa exata da falha e por que falhou em runtime.\n"
            "2. **Patch unificado (`git diff`)**: Código corrigido pronto para aplicar no arquivo de `source/` ou `loader/`.\n"
            "3. **Dependências ausentes**: Se o fix precisar de stubs extras, indique explicitamente."
        ),
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Parsing crash_analyzer's outputs
# ---------------------------------------------------------------------------

_CRASH_INSTRUCTION_RE = re.compile(r"```\n(.*?)\n```", re.S)
_SYMBOL_HEADING_RE = re.compile(r"^### `(.+?)`\s*$", re.M)
_NEXT_HEADING_RE = re.compile(r"^#{2,3} ", re.M)
_HIT_BULLET_RE = re.compile(r"^- `(.+?):(\d+)` -- `(.*)`$", re.M)


def _parse_triage_summary(text):
    """!
    @brief Parse `crash_analyzer._write_triage_summary()`'s Markdown back into plain data.
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


def _parse_analysis_report(dump_path_or_file):
    """!
    @brief Extract key CPU registers, thread info, and root-cause lines from `<dump_path>.analysis.txt`.
    @param dump_path_or_file Path to the `.psp2dmp` file or direct path to `.analysis.txt`.
    @return dict with `thread`, `stop_reason`, `pc`, `lr`, `sp`, `registers`, and `root_cause`.
    """
    analysis_file = dump_path_or_file if str(dump_path_or_file).endswith(".analysis.txt") else f"{dump_path_or_file}.analysis.txt"
    if not os.path.isfile(analysis_file):
        return None

    try:
        content = Path(analysis_file).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    diagnostics = {
        "thread": None,
        "stop_reason": None,
        "pc": None,
        "lr": None,
        "sp": None,
        "key_regs": [],
        "root_cause": [],
    }

    in_root_cause = False
    in_registers = False

    for line in content.splitlines():
        line_s = line.strip()
        if "HILO EN CRASH" in line or "CRASHED THREAD" in line or "THREAD EM CRASH" in line:
            diagnostics["thread"] = line_s
        elif "Razón de parada:" in line or "Stop reason:" in line or "Motivo da parada:" in line:
            diagnostics["stop_reason"] = line_s
        elif line_s.startswith("PC:"):
            diagnostics["pc"] = line_s
        elif line_s.startswith("LR:"):
            diagnostics["lr"] = line_s
        elif line_s.startswith("SP :") or line_s.startswith("SP:"):
            diagnostics["sp"] = line_s
        elif "--- REGISTROS CPU ---" in line or "--- CPU REGISTERS ---" in line:
            in_registers = True
            in_root_cause = False
        elif "DIAGNÓSTICO AUTOMÁTICO DE CAUSA RAÍZ" in line or "AUTOMATIC ROOT CAUSE DIAGNOSIS" in line:
            in_root_cause = True
            in_registers = False
        elif in_registers:
            if line_s.startswith("R0") or line_s.startswith("R1") or line_s.startswith("R2") or line_s.startswith("R3"):
                diagnostics["key_regs"].append(line_s)
            elif "0x00000000" in line_s and line_s.startswith("R"):
                diagnostics["key_regs"].append(line_s)
            elif line_s.startswith("---") or line_s.startswith("==="):
                in_registers = False
        elif in_root_cause:
            if line_s.startswith("[*]"):
                diagnostics["root_cause"].append(line_s)
            elif line_s.startswith("---") or line_s.startswith("==="):
                in_root_cause = False

    return diagnostics


def _extract_log_tail(project_dir, log_path=None, max_lines=35):
    """!
    @brief Extract the last `max_lines` from the most recent log file.
    @param project_dir Path to the port's project directory.
    @param log_path Explicit log file path (optional).
    @param max_lines Maximum lines to return from the tail.
    @return list of string log lines, or empty list if no log is found.
    """
    target_log = None
    if log_path and os.path.isfile(log_path):
        target_log = log_path
    elif project_dir and os.path.isdir(project_dir):
        candidates = glob.glob(os.path.join(project_dir, "logs", "*.log")) + glob.glob(os.path.join(project_dir, "*.log"))
        if candidates:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            target_log = candidates[0]

    if not target_log or not os.path.isfile(target_log):
        return []

    try:
        with open(target_log, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.rstrip("\r\n") for l in f if l.strip()]
        return lines[-max_lines:]
    except OSError:
        return []


def _extract_snippet_window(project_dir, rel_path, line_no, radius=3):
    """!
    @brief Extract a surrounding code window around `line_no` for rich context.
    @param project_dir Base directory of the port.
    @param rel_path Path relative to `project_dir` or absolute.
    @param line_no Center line number (1-indexed).
    @param radius Number of lines above and below to include.
    @return List of formatted lines `// path:line` followed by snippet lines.
    """
    full_path = rel_path if os.path.isabs(rel_path) else os.path.join(project_dir, rel_path)
    if not os.path.isfile(full_path):
        return []

    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
    except OSError:
        return []

    start_idx = max(0, line_no - 1 - radius)
    end_idx = min(len(all_lines), line_no + radius)

    snippet = [f"// {rel_path} (lines {start_idx + 1}-{end_idx})"]
    for idx in range(start_idx, end_idx):
        cur_line_no = idx + 1
        prefix = "==> " if cur_line_no == line_no else "    "
        line_content = all_lines[idx].rstrip("\r\n")
        snippet.append(f"{prefix}{cur_line_no:4d} | {line_content}")
    return snippet


def _relpath(project_dir, path):
    """!
    @brief Best-effort relative path calculation.
    """
    try:
        return os.path.relpath(path, project_dir)
    except (ValueError, TypeError):
        return path


def _find_stub_matches(project_dir, symbol_name, max_hits=5, stubs_dir=None):
    """!
    @brief Search `source/` and generated stubs for implementations matching `symbol_name`.
    """
    if not symbol_name:
        return []

    from . import crash_analyzer
    search_terms = [symbol_name]
    jni_method = crash_analyzer._jni_method_name_from_symbol(symbol_name)
    if jni_method and jni_method not in search_terms:
        search_terms.append(jni_method)

    candidate_files = []
    search_dirs = [stubs_dir] if stubs_dir and os.path.isdir(stubs_dir) else [
        os.path.join(project_dir, "source"),
        project_dir
    ]

    for s_dir in search_dirs:
        for stub_path in (
            os.path.join(s_dir, "generated_jni_stubs.c"),
            os.path.join(s_dir, "source", "generated_jni_stubs.c"),
        ):
            if os.path.isfile(stub_path) and stub_path not in candidate_files:
                candidate_files.append(stub_path)
        for c_path in sorted(glob.glob(os.path.join(s_dir, "**", "*.c"), recursive=True)):
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
# Character Budget & Clipboard utilities
# ---------------------------------------------------------------------------

def _hit_cost(hit):
    path, line_no, text = hit
    return len(path) + len(str(line_no)) + len(text) + 30


def _fit_hits(hits, budget):
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
    fixed_overhead = len(crash_instruction or "") + len(dump_path) + 500
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


def copy_to_clipboard(text):
    """!
    @brief Copy string to OS clipboard (supports macOS pbcopy, Linux X11/Wayland, Windows clip).
    @param text String content to copy.
    @return `True` if successfully copied, `False` otherwise.
    """
    for cmd in [
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip.exe"],
    ]:
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p.communicate(text.encode("utf-8"))
            if p.returncode == 0:
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_context_bundle(project_cfg, dump_path, global_cfg=None, max_chars=14000,
                         log_path=None, log_lines=35, so_path=None, decompiled_dir=None,
                         analysis_path=None, triage_path=None, stubs_dir=None):
    """!
    @brief Build the complete, surgical crash context data structure.
    """
    project_dir = project_cfg.get("_project_dir", str(Path(dump_path).parent.parent)) if project_cfg else str(Path(dump_path).parent)
    dump_path = str(dump_path)
    target_triage = triage_path if triage_path and os.path.isfile(triage_path) else f"{dump_path}.triage_summary.md"

    if not os.path.isfile(target_triage):
        from . import crash_analyzer
        crash_analyzer.analyze(project_cfg, dump_path, global_cfg=global_cfg, so_path=so_path)

    if not os.path.isfile(target_triage):
        return None

    try:
        text = Path(target_triage).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    crash_instruction, parsed_symbols = _parse_triage_summary(text)
    diagnostics = _parse_analysis_report(analysis_path or dump_path)
    log_tail = _extract_log_tail(project_dir, log_path=log_path, max_lines=log_lines)

    symbol_entries = []
    for sym in parsed_symbols:
        stub_hits_raw = _find_stub_matches(project_dir, sym["symbol"], stubs_dir=stubs_dir)
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
        "project_dir": project_dir,
        "diagnostics": diagnostics,
        "crash_instruction": crash_instruction,
        "log_tail": log_tail,
        "symbols": trimmed_symbols,
        "truncation_notes": truncation_notes,
    }


def render_markdown(bundle):
    """!
    @brief Render bundle into a token-optimized, structured Markdown document.
    """
    project_dir = bundle.get("project_dir", "")
    lines = [f"# {t('context_feeder.doc_title')}", "", t("context_feeder.dump_line", path=bundle["dump_path"]), ""]

    # 1. CPU Diagnostics & Root Cause
    diag = bundle.get("diagnostics")
    if diag:
        lines += [f"## {t('context_feeder.diagnostics_heading')}", ""]
        if diag.get("thread"):
            lines.append(f"- **Thread:** `{diag['thread']}`")
        if diag.get("stop_reason"):
            lines.append(f"- **Stop Reason:** `{diag['stop_reason']}`")
        if diag.get("pc"):
            lines.append(f"- **PC:** `{diag['pc']}`")
        if diag.get("lr"):
            lines.append(f"- **LR:** `{diag['lr']}`")
        if diag.get("sp"):
            lines.append(f"- **SP:** `{diag['sp']}`")
        if diag.get("key_regs"):
            regs_str = " | ".join(f"`{r}`" for r in diag["key_regs"])
            lines.append(f"- **Registros clave:** {regs_str}")
        if diag.get("root_cause"):
            for rc in diag["root_cause"]:
                lines.append(f"- **Causa:** {rc}")
        lines.append("")

    # 2. Crashing Instruction
    lines += [f"## {t('context_feeder.crash_instruction_heading')}", ""]
    if bundle.get("crash_instruction"):
        lines += ["```arm", bundle["crash_instruction"], "```", ""]
    else:
        lines += [t("context_feeder.no_crash_instruction"), ""]

    # 3. Log Tail
    log_tail = bundle.get("log_tail")
    lines += [f"## {t('context_feeder.log_heading')}", ""]
    if log_tail:
        lines.append("```log")
        lines.extend(log_tail)
        lines.append("```")
        lines.append("")
    else:
        lines += [t("context_feeder.no_log_found"), ""]

    # 4. Cross-referenced Code Evidence
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
            for path, line_no, _text in entry["ghidra_hits"]:
                snippet = _extract_snippet_window(project_dir, path, line_no, radius=2)
                lines.extend(snippet if snippet else [f"// {path}:{line_no}", _text])
            lines.append("```")
            if omitted.get("ghidra"):
                lines.append(f"_{t('context_feeder.more_not_shown', count=omitted['ghidra'])}_")
            lines.append("")

        if entry["jadx_hits"]:
            lines.append(f"**{t('context_feeder.jadx_label')}**")
            lines.append("")
            lines.append("```java")
            for path, line_no, _text in entry["jadx_hits"]:
                snippet = _extract_snippet_window(project_dir, path, line_no, radius=2)
                lines.extend(snippet if snippet else [f"// {path}:{line_no}", _text])
            lines.append("```")
            if omitted.get("jadx"):
                lines.append(f"_{t('context_feeder.more_not_shown', count=omitted['jadx'])}_")
            lines.append("")

        if entry["stub_hits"]:
            lines.append(f"**{t('context_feeder.stub_label')}**")
            lines.append("")
            lines.append("```c")
            for path, line_no, _text in entry["stub_hits"]:
                snippet = _extract_snippet_window(project_dir, path, line_no, radius=2)
                lines.extend(snippet if snippet else [f"// {path}:{line_no}", _text])
            lines.append("```")
            if omitted.get("stub"):
                lines.append(f"_{t('context_feeder.more_not_shown', count=omitted['stub'])}_")
            lines.append("")

    for note in bundle.get("truncation_notes") or []:
        lines.append(f"_{note}_")
    if bundle.get("truncation_notes"):
        lines.append("")

    # 5. AI Prompt
    lines += [f"## {t('context_feeder.prompt_heading')}", "", t("context_feeder.prompt_body"), ""]
    return "\n".join(lines)


def render_json(bundle):
    """!
    @brief Render bundle as JSON for tooling.
    """
    return json.dumps(bundle, indent=2, ensure_ascii=False)


def export_context(project_cfg, dump_path, global_cfg=None, fmt="markdown", out_path=None,
                   log_path=None, log_lines=35, stdout=False, copy=False, max_chars=14000,
                   so_path=None, decompiled_dir=None, analysis_path=None, triage_path=None,
                   stubs_dir=None):
    """!
    @brief Build the crash context bundle and export/print/copy it.
    """
    dump_path = str(dump_path)
    if not os.path.isfile(dump_path):
        print(f"{C.RED}{t('context_feeder.dump_not_found', path=dump_path)}{C.RESET}")
        return None
    if fmt not in ("markdown", "json"):
        print(f"{C.RED}{t('context_feeder.bad_format', fmt=fmt)}{C.RESET}")
        return None

    bundle = build_context_bundle(
        project_cfg, dump_path, global_cfg=global_cfg,
        max_chars=max_chars, log_path=log_path, log_lines=log_lines,
        so_path=so_path, decompiled_dir=decompiled_dir,
        analysis_path=analysis_path, triage_path=triage_path,
        stubs_dir=stubs_dir
    )
    if bundle is None:
        print(f"{C.RED}{t('context_feeder.bundle_failed')}{C.RESET}")
        return None

    rendered = render_markdown(bundle) if fmt == "markdown" else render_json(bundle)

    if stdout:
        print(rendered)

    if copy:
        if copy_to_clipboard(rendered):
            print(f"{C.GREEN}{t('context_feeder.clipboard_ok')}{C.RESET}")
        else:
            print(f"{C.YELLOW}{t('context_feeder.clipboard_failed')}{C.RESET}")

    ext = "context.md" if fmt == "markdown" else "context.json"
    dest = Path(out_path) if out_path else Path(f"{dump_path}.{ext}")

    try:
        dest.write_text(rendered, encoding="utf-8")
    except OSError as e:
        print(f"{C.RED}{t('context_feeder.write_failed', error=e)}{C.RESET}")
        return None

    if not stdout:
        print(f"{C.GREEN}{t('context_feeder.exported_ok', path=dest)}{C.RESET}")
    return dest


def export_context_cli(project_cfg, dump_path, global_cfg=None, fmt="markdown", out=None,
                       log_path=None, log_lines=35, stdout=False, copy=False, max_chars=14000,
                       so_path=None, decompiled_dir=None, analysis_path=None, triage_path=None,
                       stubs_dir=None):
    """!
    @brief CLI entry point for export-context subcommand.
    """
    result = export_context(
        project_cfg, dump_path, global_cfg=global_cfg, fmt=fmt, out_path=out,
        log_path=log_path, log_lines=log_lines, stdout=stdout, copy=copy, max_chars=max_chars,
        so_path=so_path, decompiled_dir=decompiled_dir, analysis_path=analysis_path,
        triage_path=triage_path, stubs_dir=stubs_dir
    )
    return 0 if result is not None else 1


def export_context_menu(project_cfg, global_cfg):
    """!
    @brief Interactive TUI menu entry point for exporting crash context with Fast & Custom modes.
    """
    from . import ftp_ops, crash_analyzer

    project_dir = project_cfg.get("_project_dir", "")
    dumps = ftp_ops.list_local_history(project_cfg, "dumps") if project_cfg else []

    # Choose Mode
    modes = [
        ("auto", t("context_feeder.mode_auto")),
        ("custom", t("context_feeder.mode_custom")),
    ]
    mode_choice = tui.select_list(t("context_feeder.menu_mode_title"), modes, label_fn=lambda m: m[1])
    if mode_choice is None:
        return

    mode, _label = mode_choice

    # Defaults detection
    latest_dump = str(dumps[0]) if dumps else ""
    log_candidates = glob.glob(os.path.join(project_dir, "logs", "*.log")) + glob.glob(os.path.join(project_dir, "*.log")) if project_dir else []
    if log_candidates:
        log_candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    default_log = log_candidates[0] if log_candidates else ""

    _auto_elf, auto_so = crash_analyzer._auto_find_files(project_dir, project_cfg.get("build_dir", "build")) if project_dir else (None, None)
    default_so = auto_so or ""
    default_decomp = os.path.join(project_dir, "decompiled") if project_dir and os.path.isdir(os.path.join(project_dir, "decompiled")) else ""
    default_source = os.path.join(project_dir, "source") if project_dir and os.path.isdir(os.path.join(project_dir, "source")) else ""

    dump_path = ""
    log_path = default_log
    so_path = default_so
    decompiled_dir = default_decomp
    stubs_dir = default_source

    if mode == "auto":
        if dumps:
            chosen = tui.select_list(t("context_feeder.menu_pick_dump"), dumps, label_fn=lambda p: p.name)
            if chosen is None:
                return
            dump_path = str(chosen)
        else:
            print(f"{C.YELLOW}{t('context_feeder.menu_no_dumps')}{C.RESET}")
            dump_path = tui.input_path(t("context_feeder.prompt_dump"), must_exist=True)
            if not dump_path:
                return
    else:
        # Custom Mode: Prompt for each path with prefilled defaults
        print(f"\n{C.BOLD}{t('context_feeder.mode_custom')}{C.RESET}\n")
        dump_path = tui.input_path(t("context_feeder.prompt_dump"), default=latest_dump, must_exist=True)
        if not dump_path:
            return

        log_path = tui.input_path(t("context_feeder.prompt_log"), default=default_log, allow_blank=True)
        so_path = tui.input_path(t("context_feeder.prompt_so"), default=default_so, allow_blank=True)
        decompiled_dir = tui.input_path(t("context_feeder.prompt_decompiled"), default=default_decomp, is_dir=True, allow_blank=True)
        stubs_dir = tui.input_path(t("context_feeder.prompt_stubs"), default=default_source, is_dir=True, allow_blank=True)

    formats = [("markdown", t("context_feeder.format_markdown")), ("json", t("context_feeder.format_json"))]
    fmt_choice = tui.select_list(t("context_feeder.menu_pick_format"), formats, label_fn=lambda f: f[1])
    if fmt_choice is None:
        return
    fmt, _label = fmt_choice

    out_file = export_context(
        project_cfg, dump_path, global_cfg=global_cfg, fmt=fmt, copy=True,
        log_path=log_path, so_path=so_path, decompiled_dir=decompiled_dir, stubs_dir=stubs_dir
    )

    if out_file and out_file.exists():
        if tui.confirm(t("context_feeder.confirm_view"), default=False):
            try:
                print("\n" + "=" * 80)
                print(out_file.read_text(encoding="utf-8", errors="ignore"))
                print("=" * 80 + "\n")
            except Exception:
                pass
        tui.pause()

