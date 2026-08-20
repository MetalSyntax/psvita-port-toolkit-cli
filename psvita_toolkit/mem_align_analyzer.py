"""!
@file mem_align_analyzer.py
@brief Heuristic scan for ARM memory-alignment risks that pass silently on
       Android but raise a Data Abort on PS Vita's Cortex-A9.

@details
Android's kernel (and most ARMv7 Linux kernels) leaves the CPU's strict
alignment check (`SCTLR.A`) disabled, so plain integer loads/stores across an
unaligned address are just slower there, not fatal. PS Vita's Cortex-A9 keeps
that same relaxation for ordinary `ldr`/`str`, but a handful of instructions
are ALWAYS alignment-checked regardless of `SCTLR.A` -- `ldrd`/`strd` (must be
4-byte aligned; GCC/Clang only emit them for 8-byte accesses it already
believes are aligned, so a mismatch usually means the *source* struct/pointer
math is wrong, not the instruction choice) and the NEON/VFP multiple-register
transfers `vld1`/`vst1`/`vldm`/`vstm` (element-size-aligned, always). A game
built against a permissive Android target can ship code the Android compiler
happened to produce with one of these, that never once faulted in testing on
a phone, and Data-Aborts on first run on a real Vita.

This is intentionally a HEURISTIC, predictive report, not a certainty. A
disassembly-level regex scan can point at every `ldrd`/`vld1`/... in the
binary and which function it's in, but it cannot prove any specific one is
actually fed an unaligned address at runtime -- that depends on data the
static binary doesn't carry. See `docs/dev-notes/mem_align_analyzer.md` for
why this stays disassembly-level (no full data-flow/VSA analysis) and why
findings are written as "audit this" warnings, not "this WILL crash" claims.

Two independent passes, same shape as `so_patcher.py`:
1. `scan_alignment_risks()` -- disassembles the primary `.so` with
   `arm-vita-eabi-objdump -d` (same VITASDK toolchain `utils.search_symbols()`
   already relies on -- it disassembles plain ARM/Thumb machine code fine
   regardless of the original ELF's target OS) and regex-scans for the
   mnemonics above, attributed to the nearest preceding function symbol.
2. `scan_struct_packing()` -- regex-scans the Ghidra-decompiled pseudo-C for
   `#pragma pack`/`__attribute__((packed))` and raw-buffer-to-struct casts
   immediately following a `memcpy`/`recv`/`fread`/`read` call, both patterns
   that break the assumption a struct's field offsets in memory can be
   dereferenced directly rather than parsed field-by-field.
"""

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "mem_align.scan_title": {
        "es": "[*] Escaneando riesgos de alineación de memoria (ARMv7 Cortex-A9)...",
        "en": "[*] Scanning for memory alignment risks (ARMv7 Cortex-A9)...",
        "pt": "[*] Escaneando riscos de alinhamento de memória (ARMv7 Cortex-A9)...",
    },
    "mem_align.so_not_found": {
        "es": "[!] No se encontró ningún .so -- se omite el escaneo de instrucciones.",
        "en": "[!] No .so file found -- skipping the instruction scan.",
        "pt": "[!] Nenhum .so encontrado -- pulando o escaneamento de instruções.",
    },
    "mem_align.objdump_missing": {
        "es": "[!] No se encontró arm-vita-eabi-objdump (VITASDK en PATH) -- se omite el escaneo de instrucciones.",
        "en": "[!] arm-vita-eabi-objdump not found (VITASDK in PATH) -- skipping the instruction scan.",
        "pt": "[!] arm-vita-eabi-objdump não encontrado (VITASDK no PATH) -- pulando o escaneamento de instruções.",
    },
    "mem_align.objdump_failed": {
        "es": "[-] objdump falló sobre {so}: {error}",
        "en": "[-] objdump failed on {so}: {error}",
        "pt": "[-] objdump falhou em {so}: {error}",
    },
    "mem_align.insn_found": {
        "es": "[!] {count} instrucción(es) sensible(s) a alineación encontradas en {funcs} función(es).",
        "en": "[!] {count} alignment-sensitive instruction(s) found across {funcs} function(s).",
        "pt": "[!] {count} instrução(ões) sensível(is) a alinhamento encontrada(s) em {funcs} função(ões).",
    },
    "mem_align.insn_none": {
        "es": "[*] No se encontraron ldrd/strd/vld1/vst1/vldm/vstm en la disassembly.",
        "en": "[*] No ldrd/strd/vld1/vst1/vldm/vstm found in the disassembly.",
        "pt": "[*] Nenhum ldrd/strd/vld1/vst1/vldm/vstm encontrado na disassembly.",
    },
    "mem_align.packing_found": {
        "es": "[!] {count} indicio(s) de struct packing / deserialización binaria encontrado(s).",
        "en": "[!] {count} struct-packing / binary-deserialization hint(s) found.",
        "pt": "[!] {count} indício(s) de struct packing / desserialização binária encontrado(s).",
    },
    "mem_align.packing_none": {
        "es": "[*] No se encontraron indicios de struct packing en el pseudo-C decompilado.",
        "en": "[*] No struct-packing hints found in the decompiled pseudo-C.",
        "pt": "[*] Nenhum indício de struct packing encontrado no pseudo-C decompilado.",
    },
    "mem_align.no_ghidra": {
        "es": "[*] No hay pseudo-C de Ghidra decompilado (decompiled/*/ghidra/*.c) -- se omite ese chequeo.",
        "en": "[*] No decompiled Ghidra pseudo-C (decompiled/*/ghidra/*.c) -- skipping that check.",
        "pt": "[*] Nenhum pseudo-C do Ghidra decompilado (decompiled/*/ghidra/*.c) -- pulando essa verificação.",
    },
    "mem_align.plan_not_found": {
        "es": "[!] No se encontró PORTING_PLAN.md -- se omite la sección de alineación.",
        "en": "[!] PORTING_PLAN.md not found -- skipping the alignment section.",
        "pt": "[!] PORTING_PLAN.md não encontrado -- pulando a seção de alinhamento.",
    },
    "mem_align.plan_updated": {
        "es": "[+] Hallazgos de alineación documentados en {plan_path}",
        "en": "[+] Alignment findings documented in {plan_path}",
        "pt": "[+] Achados de alinhamento documentados em {plan_path}",
    },
    "mem_align.plan_nothing_to_document": {
        "es": "[*] Nada que documentar -- no se encontraron riesgos de alineación.",
        "en": "[*] Nothing to document -- no alignment risks found.",
        "pt": "[*] Nada para documentar -- nenhum risco de alinhamento encontrado.",
    },
    "mem_align.menu_title": {
        "es": "Analizador de Alineación de Memoria (ARMv7)",
        "en": "Memory Alignment Analyzer (ARMv7)",
        "pt": "Analisador de Alinhamento de Memória (ARMv7)",
    },
    "mem_align.menu_scan": {
        "es": "Escanear riesgos de alineación y documentar en PORTING_PLAN.md",
        "en": "Scan for alignment risks and document in PORTING_PLAN.md",
        "pt": "Escanear riscos de alinhamento e documentar no PORTING_PLAN.md",
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Disassembly-level instruction scan
# ---------------------------------------------------------------------------

# Always alignment-checked on Cortex-A9 regardless of SCTLR.A, unlike plain
# ldr/str -- see the module docstring for why these specific mnemonics.
# objdump prints the Thumb/ARM mnemonic in lowercase, optionally followed by
# a condition code or `.w`/size suffix (e.g. "vld1.32", "ldrd.w"), so this
# matches the mnemonic root only, at a word boundary.
_ALIGNMENT_MNEMONIC_RE = re.compile(r'\b(ldrd|strd|vld1|vst1|vldm|vstm)(?:\.\w+)?\b', re.IGNORECASE)

# objdump -d function header line, e.g. "0001a2b4 <Java_com_foo_Bar_baz>:"
_FUNC_HEADER_RE = re.compile(r'^[0-9a-fA-F]+\s+<([^>]+)>:$')

# objdump -d instruction line, e.g. "   1a2b8:\t\tf9 42 8d ec \tldrd\tr4, r5, [sp, #12]"
_INSN_LINE_RE = re.compile(r'^\s*([0-9a-fA-F]+):\s*[0-9a-fA-F ]+\t(.+)$')


def _find_primary_so(project_dir):
    """!
    @brief Best-effort discovery of the port's original Android `.so`, same
           heuristic duplicated in `jni_analyzer.py`/`so_patcher.py` (kept
           local rather than imported -- it's each module's own private helper).
    @param project_dir Path to the port's project directory.
    @return Path string to the most likely `.so`, or `None` if none found.
    """
    candidates = glob.glob(os.path.join(str(project_dir), "**", "*.so"), recursive=True)
    candidates.sort(key=lambda x: 0 if ("libgame" in x or "libmain" in x) else 1)
    return candidates[0] if candidates else None


def _disassemble(so_path, global_cfg):
    """!
    @brief Run `arm-vita-eabi-objdump -d` over the given `.so`.
    @param so_path Path to the `.so` file.
    @param global_cfg Global config dict (used to locate VITASDK's `bin/`,
           same convention as `utils.search_symbols()`).
    @return `(stdout_lines, error_message)` -- `error_message` is `None` on
            success, or a human-readable reason nothing was disassembled.
    """
    vitasdk_bin = os.path.join((global_cfg or {}).get("vitasdk", ""), "bin")
    if os.path.isdir(vitasdk_bin) and vitasdk_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{vitasdk_bin}:{os.environ.get('PATH', '')}"

    objdump = shutil.which("arm-vita-eabi-objdump")
    if not objdump:
        return [], t("mem_align.objdump_missing")

    r = subprocess.run([objdump, "-d", "--no-show-raw-insn", str(so_path)],
                        capture_output=True, text=True)
    if r.returncode != 0:
        return [], t("mem_align.objdump_failed", so=os.path.basename(str(so_path)), error=r.stderr.strip())
    return r.stdout.splitlines(), None


def scan_alignment_risks(project_dir, global_cfg, so_path=None):
    """!
    @brief Disassemble the primary `.so` and collect every alignment-sensitive
           instruction, attributed to its enclosing function.
    @param project_dir Path to the port's project directory.
    @param global_cfg Global config dict.
    @param so_path Explicit `.so` path; auto-discovered via
           `_find_primary_so()` if omitted.
    @return `(hits, error_message)`. `hits` is a list of
            `(function_name, address, instruction_text)` tuples, in
            disassembly order. `error_message` is `None` on success, or why
            the scan couldn't run (no `.so`, no objdump, objdump failed) --
            `hits` is always `[]` in that case.
    """
    so_path = so_path or _find_primary_so(project_dir)
    if not so_path:
        return [], t("mem_align.so_not_found")

    lines, err = _disassemble(so_path, global_cfg)
    if err:
        return [], err

    hits = []
    current_func = "?"
    for line in lines:
        m = _FUNC_HEADER_RE.match(line)
        if m:
            current_func = m.group(1)
            continue
        m = _INSN_LINE_RE.match(line)
        if not m:
            continue
        addr, insn_text = m.groups()
        if _ALIGNMENT_MNEMONIC_RE.search(insn_text):
            hits.append((current_func, addr, insn_text.strip()))
    return hits, None


# ---------------------------------------------------------------------------
# Struct-packing / binary-deserialization scan (Ghidra pseudo-C)
# ---------------------------------------------------------------------------

_PRAGMA_PACK_RE = re.compile(r'#pragma\s+pack|__attribute__\s*\(\s*\(\s*packed\s*\)\s*\)')

# A raw-buffer read call, optionally followed (within a couple of lines) by a
# cast of some pointer to a struct type -- the classic "read bytes off the
# wire/disk, then reinterpret them as a struct in place" pattern whose field
# offsets depend on the COMPILER's struct layout, not the wire format's.
_BUFFER_READ_RE = re.compile(r'\b(memcpy|recv|read|fread)\s*\(')
_STRUCT_CAST_RE = re.compile(r'\(\s*struct\s+\w+\s*\*\s*\)')


def scan_struct_packing(project_dir):
    """!
    @brief Grep every `decompiled/*/ghidra/*.c` file (same discovery glob as
           `crash_analyzer._find_ghidra_matches()`) for `#pragma pack`/
           `__attribute__((packed))` declarations and buffer-read-then-cast
           patterns.
    @param project_dir Path to the port's project directory.
    @return list of `(relative_file, line_no, kind, snippet)` tuples, where
            `kind` is `"packed_struct"` or `"buffer_cast"`. `[]` if there's
            no decompiled Ghidra output to scan.
    """
    project_dir = Path(project_dir)
    hits = []
    for c_path_str in sorted(glob.glob(os.path.join(str(project_dir), "decompiled", "*", "ghidra", "*.c"))):
        c_path = Path(c_path_str)
        try:
            lines = c_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        try:
            rel = str(c_path.relative_to(project_dir))
        except ValueError:
            rel = str(c_path)

        for line_no, line in enumerate(lines, 1):
            if _PRAGMA_PACK_RE.search(line):
                hits.append((rel, line_no, "packed_struct", line.strip()))

        for line_no, line in enumerate(lines, 1):
            if not _BUFFER_READ_RE.search(line):
                continue
            # Look at this line and the next few for a struct-pointer cast --
            # decompiled pseudo-C often splits the call and the cast/assignment
            # across adjacent statements.
            window = lines[line_no - 1: line_no + 3]
            for offset, wline in enumerate(window):
                if _STRUCT_CAST_RE.search(wline):
                    hits.append((rel, line_no + offset, "buffer_cast", wline.strip()))
                    break
    return hits


def _has_ghidra_output(project_dir):
    """!
    @brief Check whether any Ghidra-decompiled pseudo-C exists to scan.
    @param project_dir Path to the port's project directory.
    @return `True` if at least one `decompiled/*/ghidra/*.c` file exists.
    """
    return bool(glob.glob(os.path.join(str(project_dir), "decompiled", "*", "ghidra", "*.c")))


# ---------------------------------------------------------------------------
# PORTING_PLAN.md reporting
# ---------------------------------------------------------------------------

def document_alignment_findings_in_plan(project_cfg, insn_hits, packing_hits):
    """!
    @brief Append a `## Memory alignment findings (psvita-toolkit)` section to
           `PORTING_PLAN.md`, same append-if-exists-else-warn pattern as
           `so_patcher.document_findings_in_plan()`.
    @param project_cfg Per-project config dict.
    @param insn_hits Result of `scan_alignment_risks()`.
    @param packing_hits Result of `scan_struct_packing()`.
    """
    project_dir = Path(project_cfg["_project_dir"])
    if not insn_hits and not packing_hits:
        print(t("mem_align.plan_nothing_to_document"))
        return

    plan_path = project_dir / "PORTING_PLAN.md"
    if not plan_path.exists():
        print(f"{C.YELLOW}{t('mem_align.plan_not_found')}{C.RESET}")
        return

    lines = ["", "## Memory alignment findings (psvita-toolkit)", "",
             "Heuristic predictions, not confirmed crashes -- see",
             "`docs/dev-notes/mem_align_analyzer.md` for why. Each entry below is worth a",
             "manual look before it bites you as a first-boot Data Abort.", ""]

    if insn_hits:
        lines.append("### Alignment-sensitive instructions (`ldrd`/`strd`/`vld1`/`vst1`/`vldm`/`vstm`)")
        lines.append("")
        by_func = {}
        for func, addr, insn in insn_hits:
            by_func.setdefault(func, []).append((addr, insn))
        for func in sorted(by_func):
            entries = by_func[func]
            lines.append(f"- **{func}** -- {len(entries)} occurrence(s)")
            for addr, insn in entries[:5]:
                lines.append(f"  - `{addr}`: `{insn}`")
            if len(entries) > 5:
                lines.append(f"  - ... and {len(entries) - 5} more")
        lines.append("")
        lines.append("Wrap any pointer fed into these functions' loads/stores from a byte-level")
        lines.append("source (network, file, or a `void*`/`char*`-cast buffer) in an explicit")
        lines.append("aligned-copy helper (`memcpy` into a locally-aligned temporary) before")
        lines.append("dereferencing it as a wider type.")
        lines.append("")

    if packing_hits:
        lines.append("### Struct packing / binary deserialization")
        lines.append("")
        for rel, line_no, kind, snippet in packing_hits[:30]:
            label = "packed struct declaration" if kind == "packed_struct" else "buffer read + struct-pointer cast"
            lines.append(f"- `{rel}:{line_no}` ({label}): `{snippet}`")
        if len(packing_hits) > 30:
            lines.append(f"- ... and {len(packing_hits) - 30} more (re-run the scan to see the full list)")
        lines.append("")

    with open(plan_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"{C.GREEN}{t('mem_align.plan_updated', plan_path=plan_path)}{C.RESET}")


# ---------------------------------------------------------------------------
# Orchestration + TUI
# ---------------------------------------------------------------------------

def run_alignment_scan(project_cfg, global_cfg=None):
    """!
    @brief Full pass: disassemble the primary `.so`, grep the decompiled
           Ghidra pseudo-C for packing hints, print a summary, then document
           it all in `PORTING_PLAN.md`.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    @return `(insn_hits, packing_hits)`, same shapes `document_alignment_findings_in_plan()` expects.
    """
    project_dir = Path(project_cfg["_project_dir"])
    print(t("mem_align.scan_title"))

    insn_hits, err = scan_alignment_risks(project_dir, global_cfg)
    if err:
        print(f"{C.YELLOW}{err}{C.RESET}")
    elif insn_hits:
        funcs = len({f for f, _, _ in insn_hits})
        print(f"{C.YELLOW}{t('mem_align.insn_found', count=len(insn_hits), funcs=funcs)}{C.RESET}")
        for func, addr, insn in insn_hits[:10]:
            print(f"    {func} @ {addr}: {insn}")
        if len(insn_hits) > 10:
            print(f"    ... {len(insn_hits) - 10} more")
    else:
        print(t("mem_align.insn_none"))

    if not _has_ghidra_output(project_dir):
        print(t("mem_align.no_ghidra"))
        packing_hits = []
    else:
        packing_hits = scan_struct_packing(project_dir)
        if packing_hits:
            print(f"{C.YELLOW}{t('mem_align.packing_found', count=len(packing_hits))}{C.RESET}")
        else:
            print(t("mem_align.packing_none"))

    document_alignment_findings_in_plan(project_cfg, insn_hits, packing_hits)
    return insn_hits, packing_hits


def alignment_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: single-action menu (the scan doubles as the report).
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    tui.run_menu(
        t("mem_align.menu_title"),
        [(t("mem_align.menu_scan"), lambda: run_alignment_scan(project_cfg, global_cfg))],
    )
