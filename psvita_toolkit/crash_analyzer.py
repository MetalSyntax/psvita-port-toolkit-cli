"""!
@file crash_analyzer.py
@brief Crash dump (`.psp2dmp` / `psp2core-*`) analyzer for PS Vita ports.

@details
Wraps `vita-parse-core` (see `global_cfg['vita_parse_core_dir']`) to parse a Vita
core dump against the port's ELF and original Android `.so`, producing a
human-readable report: crashed-thread info, CPU registers, disassembly around
the crash PC, stack backtrace, reconstructed call chain, and loaded modules.

Requires `vita-parse-core` cloned locally and VITASDK's ARM toolchain
(`arm-vita-eabi-objdump`, `arm-vita-eabi-c++filt`) on `PATH`.

See `docs/dev-notes/crash_analyzer.md` for why this wraps `vita-parse-core`
instead of reimplementing dump parsing, and the rationale behind the `.so`
memory-base auto-detection strategy.
"""

import glob
import os
import subprocess
import sys
from collections import Counter, defaultdict

from .tui import C
from . import i18n
from .i18n import t

STRINGS = {
    "crash_analyzer.stop_reason.no_reason": {
        "es": "No reason (ejecución normal)",
        "en": "No reason (normal execution)",
        "pt": "No reason (execução normal)",
    },
    "crash_analyzer.stop_reason.undefined_instruction": {
        "es": "Undefined instruction exception (instrucción inválida/corrupta)",
        "en": "Undefined instruction exception (invalid/corrupt instruction)",
        "pt": "Undefined instruction exception (instrução inválida/corrompida)",
    },
    "crash_analyzer.stop_reason.prefetch_abort": {
        "es": "Prefetch abort exception (fallo al buscar instrucción en memoria)",
        "en": "Prefetch abort exception (failed to fetch instruction from memory)",
        "pt": "Prefetch abort exception (falha ao buscar instrução na memória)",
    },
    "crash_analyzer.stop_reason.data_abort": {
        "es": "Data abort exception (fallo de acceso a memoria / puntero nulo o inválido)",
        "en": "Data abort exception (memory access fault / null or invalid pointer)",
        "pt": "Data abort exception (falha de acesso à memória / ponteiro nulo ou inválido)",
    },
    "crash_analyzer.stop_reason.division_by_zero": {
        "es": "Division by zero",
        "en": "Division by zero",
        "pt": "Divisão por zero",
    },
    "crash_analyzer.toolchain_load_failed": {
        "es": "[-] No se pudo cargar 'vita-parse-core' desde '{dir}': {error}",
        "en": "[-] Could not load 'vita-parse-core' from '{dir}': {error}",
        "pt": "[-] Não foi possível carregar 'vita-parse-core' de '{dir}': {error}",
    },
    "crash_analyzer.toolchain_load_hint": {
        "es": "    Clonar vita-parse-core o corregir 'vita_parse_core_dir' en la config global.",
        "en": "    Clone vita-parse-core or fix 'vita_parse_core_dir' in the global config.",
        "pt": "    Clone o vita-parse-core ou corrija 'vita_parse_core_dir' na configuração global.",
    },
    "crash_analyzer.symbols_extract_failed": {
        "es": "[!] No se pudieron extraer símbolos de {path}: {error}",
        "en": "[!] Could not extract symbols from {path}: {error}",
        "pt": "[!] Não foi possível extrair símbolos de {path}: {error}",
    },
    "crash_analyzer.crash_instruction_marker": {
        "es": "INSTRUCCIÓN DEL CRASH",
        "en": "CRASH INSTRUCTION",
        "pt": "INSTRUÇÃO DO CRASH",
    },
    "crash_analyzer.disassemble_error": {
        "es": "Error al desensamblar: {error}",
        "en": "Error while disassembling: {error}",
        "pt": "Erro ao desmontar: {error}",
    },
    "crash_analyzer.dump_not_found": {
        "es": "[-] Archivo dump no encontrado: {path}",
        "en": "[-] Dump file not found: {path}",
        "pt": "[-] Arquivo de dump não encontrado: {path}",
    },
    "crash_analyzer.report_title": {
        "es": "           PS VITA CRASH DUMP ANALYSIS REPORT (vita-parse-core)",
        "en": "           PS VITA CRASH DUMP ANALYSIS REPORT (vita-parse-core)",
        "pt": "           PS VITA CRASH DUMP ANALYSIS REPORT (vita-parse-core)",
    },
    "crash_analyzer.header_core_dump": {
        "es": " Core Dump: {path}",
        "en": " Core Dump: {path}",
        "pt": " Core Dump: {path}",
    },
    "crash_analyzer.header_elf": {
        "es": " ELF (Vita): {path}",
        "en": " ELF (Vita): {path}",
        "pt": " ELF (Vita): {path}",
    },
    "crash_analyzer.header_so": {
        "es": " SO (Android): {path}",
        "en": " SO (Android): {path}",
        "pt": " SO (Android): {path}",
    },
    "crash_analyzer.not_detected": {
        "es": "no detectado",
        "en": "not detected",
        "pt": "não detectado",
    },
    "crash_analyzer.so_base_auto": {
        "es": "[+] Base de memoria del .so AUTO-DETECTADA en: 0x{addr:x}",
        "en": "[+] .so memory base AUTO-DETECTED at: 0x{addr:x}",
        "pt": "[+] Base de memória do .so AUTO-DETECTADA em: 0x{addr:x}",
    },
    "crash_analyzer.so_base_fallback": {
        "es": "[*] Base de memoria del .so, fallback: 0x{addr:x}",
        "en": "[*] .so memory base, fallback: 0x{addr:x}",
        "pt": "[*] Base de memória do .so, fallback: 0x{addr:x}",
    },
    "crash_analyzer.thread_crashed": {
        "es": "\n[!] HILO EN CRASH: '{name}' (ID: 0x{uid:x})",
        "en": "\n[!] CRASHED THREAD: '{name}' (ID: 0x{uid:x})",
        "pt": "\n[!] THREAD EM CRASH: '{name}' (ID: 0x{uid:x})",
    },
    "crash_analyzer.stop_reason_line": {
        "es": "    Razón de parada: 0x{code:x} ({reason})",
        "en": "    Stop reason: 0x{code:x} ({reason})",
        "pt": "    Motivo da parada: 0x{code:x} ({reason})",
    },
    "crash_analyzer.section_cpu_registers": {
        "es": "\n--- REGISTROS CPU ---",
        "en": "\n--- CPU REGISTERS ---",
        "pt": "\n--- REGISTRADORES DA CPU ---",
    },
    "crash_analyzer.section_root_cause": {
        "es": "                   DIAGNÓSTICO AUTOMÁTICO DE CAUSA RAÍZ",
        "en": "                   AUTOMATIC ROOT CAUSE DIAGNOSIS",
        "pt": "                DIAGNÓSTICO AUTOMÁTICO DA CAUSA RAIZ",
    },
    "crash_analyzer.root_cause_data_abort": {
        "es": " [*] Data Abort (acceso a memoria inválida).",
        "en": " [*] Data Abort (invalid memory access).",
        "pt": " [*] Data Abort (acesso inválido à memória).",
    },
    "crash_analyzer.root_cause_undefined_instruction": {
        "es": " [*] Undefined Instruction (código corrupto/inválido).",
        "en": " [*] Undefined Instruction (corrupt/invalid code).",
        "pt": " [*] Undefined Instruction (código corrompido/inválido).",
    },
    "crash_analyzer.root_cause_division_by_zero": {
        "es": " [*] Division by zero.",
        "en": " [*] Division by zero.",
        "pt": " [*] Divisão por zero.",
    },
    "crash_analyzer.crash_location": {
        "es": " [*] Ubicación del crash: PC = {pc}",
        "en": " [*] Crash location: PC = {pc}",
        "pt": " [*] Localização do crash: PC = {pc}",
    },
    "crash_analyzer.crash_instruction": {
        "es": " [*] Instrucción causante: {line}",
        "en": " [*] Faulting instruction: {line}",
        "pt": " [*] Instrução causadora: {line}",
    },
    "crash_analyzer.probable_cause_null_ptr": {
        "es": " [*] CAUSA PROBABLE: R{reg} es 0x00000000 (puntero NULO).",
        "en": " [*] PROBABLE CAUSE: R{reg} is 0x00000000 (NULL pointer).",
        "pt": " [*] CAUSA PROVÁVEL: R{reg} é 0x00000000 (ponteiro NULO).",
    },
    "crash_analyzer.section_disasm_pc": {
        "es": "\n--- DESENSAMBLADO EN PC ---",
        "en": "\n--- DISASSEMBLY AT PC ---",
        "pt": "\n--- DESMONTAGEM NO PC ---",
    },
    "crash_analyzer.section_stack_content": {
        "es": "\n--- CONTENIDO DE LA PILA (BACKTRACE) ---",
        "en": "\n--- STACK CONTENTS (BACKTRACE) ---",
        "pt": "\n--- CONTEÚDO DA PILHA (BACKTRACE) ---",
    },
    "crash_analyzer.stack_frame": {
        "es": "Pila 0x{addr:x}: {resolved}",
        "en": "Stack 0x{addr:x}: {resolved}",
        "pt": "Pilha 0x{addr:x}: {resolved}",
    },
    "crash_analyzer.section_call_chain": {
        "es": "\n--- SECUENCIA DE LLAMADAS RECONSTRUIDA ---",
        "en": "\n--- RECONSTRUCTED CALL CHAIN ---",
        "pt": "\n--- SEQUÊNCIA DE CHAMADAS RECONSTRUÍDA ---",
    },
    "crash_analyzer.section_modules_loaded": {
        "es": "                       MÓDULOS CARGADOS EN LA VITA",
        "en": "                       MODULES LOADED ON THE VITA",
        "pt": "                    MÓDULOS CARREGADOS NA VITA",
    },
    "crash_analyzer.module_segment": {
        "es": "Seg{num}: 0x{start:x} (tamaño 0x{size:x})",
        "en": "Seg{num}: 0x{start:x} (size 0x{size:x})",
        "pt": "Seg{num}: 0x{start:x} (tamanho 0x{size:x})",
    },
    "crash_analyzer.report_saved": {
        "es": "[+] Reporte guardado en: {path}",
        "en": "[+] Report saved to: {path}",
        "pt": "[+] Relatório salvo em: {path}",
    },
    "crash_analyzer.report_save_failed": {
        "es": "[-] No se pudo guardar el reporte: {error}",
        "en": "[-] Could not save the report: {error}",
        "pt": "[-] Não foi possível salvar o relatório: {error}",
    },
    "crash_analyzer.menu_no_dumps": {
        "es": "[-] No hay ningún crash dump descargado todavía en logs/.",
        "en": "[-] No crash dump has been downloaded yet in logs/.",
        "pt": "[-] Ainda não há nenhum crash dump baixado em logs/.",
    },
    "crash_analyzer.menu_no_dumps_hint": {
        "es": "    Usá 'Descargar logs / crash dumps' primero.",
        "en": "    Use 'Download logs / crash dumps' first.",
        "pt": "    Use 'Baixar logs / crash dumps' primeiro.",
    },
    "crash_analyzer.menu_available_dumps": {
        "es": "Crash dumps disponibles localmente:",
        "en": "Crash dumps available locally:",
        "pt": "Crash dumps disponíveis localmente:",
    },
    "crash_analyzer.menu_choose_prompt": {
        "es": "Elegí uno [1] (Enter = el más reciente): ",
        "en": "Choose one [1] (Enter = most recent): ",
        "pt": "Escolha um [1] (Enter = mais recente): ",
    },
    "crash_analyzer.menu_invalid_choice": {
        "es": "[-] Opción inválida.",
        "en": "[-] Invalid option.",
        "pt": "[-] Opção inválida.",
    },
}
i18n.register(STRINGS)

STOP_REASONS = defaultdict(str, {
    0: "crash_analyzer.stop_reason.no_reason",
    0x30002: "crash_analyzer.stop_reason.undefined_instruction",
    0x30003: "crash_analyzer.stop_reason.prefetch_abort",
    0x30004: "crash_analyzer.stop_reason.data_abort",
    0x60080: "crash_analyzer.stop_reason.division_by_zero",
})


def _ensure_toolchain(global_cfg):
    """!
    @brief Wire up `PATH`/`sys.path` for the VITASDK toolchain and
           `vita-parse-core`, then verify its parser modules import.
    @param global_cfg Global config dict; reads `vitasdk` and
           `vita_parse_core_dir`.
    @return `True` if `vita-parse-core`'s `core`/`elf`/`util` modules imported
            successfully, `None` otherwise (after printing an error).
    """
    vitasdk_bin = os.path.join(global_cfg.get("vitasdk", ""), "bin")
    if os.path.isdir(vitasdk_bin) and vitasdk_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{vitasdk_bin}:{os.environ.get('PATH', '')}"

    vpc_dir = global_cfg.get("vita_parse_core_dir", "")
    if os.path.isdir(vpc_dir) and vpc_dir not in sys.path:
        sys.path.insert(0, vpc_dir)

    try:
        from core import CoreParser  # noqa: F401
        from elf import ElfParser  # noqa: F401
        from util import u32  # noqa: F401
    except ImportError as e:
        print(f"{C.RED}{t('crash_analyzer.toolchain_load_failed', dir=vpc_dir, error=e)}{C.RESET}")
        print(f"{C.DIM}{t('crash_analyzer.toolchain_load_hint')}{C.RESET}")
        return None
    return True


def _demangle(name):
    """!
    @brief Demangle a C++ symbol name (e.g. `_Z...`) via `c++filt`.
    @param name Raw (possibly mangled) symbol name.
    @return Demangled name, or `name` unchanged if it isn't mangled or no
            `c++filt` binary is available.
    """
    if not name or not name.startswith("_Z"):
        return name
    for cmd in ("arm-vita-eabi-c++filt", "c++filt"):
        try:
            r = subprocess.run([cmd, name], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            continue
    return name


class SymbolTable:
    """!
    @brief Dynamic symbol table for an Android `.so`, built from
           `arm-vita-eabi-objdump -T` output, used to resolve crash addresses
           back to function names.
    """

    def __init__(self, so_path):
        """!
        @brief Build the symbol table for `so_path` (calls `_load()` immediately).
        @param so_path Path to the `.so` file to extract symbols from.
        """
        self.so_path = so_path
        self.symbols = []
        self._load()

    def _load(self):
        """!
        @brief Parse `objdump -T` output into `(start, end, demangled, raw)`
               tuples, keeping only function symbols (flag `F`).
        @details Populates and sorts `self.symbols` by start address. Leaves
                 `self.symbols` empty if the `.so` doesn't exist or objdump fails.
        """
        if not self.so_path or not os.path.exists(self.so_path):
            return
        try:
            r = subprocess.run(["arm-vita-eabi-objdump", "-T", self.so_path], capture_output=True, text=True)
            if r.returncode != 0:
                return
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 6 and parts[1] in ("g", "l") and "F" in parts[2]:
                    try:
                        addr, size = int(parts[0], 16), int(parts[4], 16)
                        raw = parts[-1]
                        self.symbols.append((addr, addr + size, _demangle(raw), raw))
                    except ValueError:
                        continue
            self.symbols.sort(key=lambda x: x[0])
        except Exception as e:
            print(f"{C.YELLOW}{t('crash_analyzer.symbols_extract_failed', path=self.so_path, error=e)}{C.RESET}")

    def lookup(self, offset):
        """!
        @brief Resolve a `.so`-relative offset to `symbol_name + 0xoffset`.
        @details First tries an exact match against each symbol's `[start,
                 end)` range (with a small +4 byte tolerance past `end`, since
                 reported symbol sizes can be off by one instruction). If no
                 symbol contains the offset, falls back to the nearest
                 preceding symbol by address.
        @param offset Offset relative to the `.so`'s load base.
        @return `"symbol + 0xN"` string, or `"0xN"` (the raw offset) if the
                symbol table is empty.
        """
        for start, end, demangled, _ in self.symbols:
            if start <= offset < end or (start <= offset <= end + 4 and end > start):
                return f"{demangled} + 0x{offset - start:x}"
        prev = None
        for start, _end, demangled, _ in self.symbols:
            if start <= offset:
                prev = (start, demangled)
            else:
                break
        return f"{prev[1]} + 0x{offset - prev[0]:x}" if prev else f"0x{offset:x}"


def _auto_detect_so_base(dump_addrs, so_syms):
    """!
    @brief Guess the `.so`'s runtime load base by voting across candidate
           addresses found on the stack/registers against known symbol offsets.
    @details For every raw address in `dump_addrs` that falls in the
             `0x80000000`-`0x9fffffff` window (the Vita's typical user `.so`
             mapping range), and for every known symbol `(sym_start, sym_end)`
             in `so_syms`, computes a candidate base as `(addr - sym_start)`
             rounded down to a 4 KiB page boundary, then keeps it only if the
             address falls within that symbol's `[0, size]` span relative to
             the candidate base. Each valid `(address, symbol)` pair casts one
             vote for its candidate base; the most-voted candidate wins.
    @param dump_addrs Iterable of raw addresses collected from the crashed
           thread(s) (PC, LR, stack words).
    @param so_syms `SymbolTable` for the `.so`.
    @return The most likely load base address, or `None` if no candidate
            received any votes (or `so_syms` has no symbols).
    """
    if not so_syms or not so_syms.symbols:
        return None
    candidates = Counter()
    for raw_addr in dump_addrs:
        val = raw_addr & ~1
        if not (0x80000000 <= val <= 0x9fffffff):
            continue
        for sym_start, sym_end, _, _ in so_syms.symbols:
            size = max(sym_end - sym_start, 0x100)
            base_cand = (val - sym_start) & ~0xFFF
            if 0x80000000 <= base_cand <= 0x9fffffff and 0 <= (val - base_cand - sym_start) <= size:
                candidates[base_cand] += 1
    if candidates:
        return candidates.most_common(1)[0][0]
    return None


def _disassemble_around(bin_path, offset, is_thumb=True):
    """!
    @brief Disassemble a window of instructions around `offset` in `bin_path`,
           marking the exact crashing instruction.
    @param bin_path Path to the ELF or `.so` binary to disassemble.
    @param offset Byte offset within `bin_path` to center the window on.
    @param is_thumb If `True`, clears the Thumb bit from `offset` and forces
           Thumb-mode disassembly (`-Mforce-thumb`); PS Vita ARM code is
           overwhelmingly Thumb-2.
    @return List of formatted disassembly lines, with the crashing instruction
            prefixed by `==>` and a translated marker; a single error-message
            line if disassembly failed.
    """
    if not bin_path or not os.path.exists(bin_path):
        return []
    addr = offset & ~1 if is_thumb else offset
    start, end = max(0, addr - 0x18), addr + 0x18
    cmd = ["arm-vita-eabi-objdump", "-d", f"--start-address=0x{start:x}", f"--stop-address=0x{end:x}", bin_path]
    if is_thumb:
        cmd.append("-Mforce-thumb")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        lines, in_text = [], False
        for line in r.stdout.splitlines():
            if "Disassembly of section" in line:
                in_text = True
                continue
            if in_text and line.strip():
                marker = f"{addr:x}:" in line.lower() or f"{addr:08x}:" in line.lower()
                lines.append(f"  ==> {line:<52} <== [{t('crash_analyzer.crash_instruction_marker')}]" if marker else f"      {line}")
        return lines
    except Exception as e:
        return [t("crash_analyzer.disassemble_error", error=e)]


def _auto_find_files(project_dir, build_dir):
    """!
    @brief Best-effort discovery of the port's ELF and Android `.so` binaries.
    @details Looks for a single `*.elf` under the build dir (or project root),
             and recursively for `*.so` files under the project, preferring
             any whose name contains `libgame` or `libmain`.
    @param project_dir Path to the port's project directory.
    @param build_dir Local build output directory, relative to `project_dir`.
    @return `(elf_file, so_file)` tuple; either element is `None` if nothing matched.
    """
    elf_candidates = glob.glob(os.path.join(project_dir, build_dir, "*.elf")) + glob.glob(os.path.join(project_dir, "*.elf"))
    elf_file = elf_candidates[0] if elf_candidates else None

    so_candidates = glob.glob(os.path.join(project_dir, "**", "*.so"), recursive=True)
    so_candidates.sort(key=lambda x: 0 if ("libgame" in x or "libmain" in x) else 1)
    so_file = so_candidates[0] if so_candidates else None
    return elf_file, so_file


def analyze(project_cfg, dump_path, global_cfg=None, elf_path=None, so_path=None, so_base=None, stack_depth=36):
    """!
    @brief Parse a crash dump and print/write a full human-readable analysis report.
    @details Resolves the crashed thread(s), CPU registers, the `.so` memory
             base (auto-detected via `_auto_detect_so_base()` if not given),
             disassembly around the crash PC, a stack backtrace, and the list
             of loaded modules. Saves the same report as text to
             `<dump_path>.analysis.txt`.
    @param project_cfg Per-project config dict (needs `_project_dir`; reads
           `project_name`, `build_dir`).
    @param dump_path Path to the `.psp2dmp`/`psp2core-*` file to analyze.
    @param global_cfg Global config dict; loaded from disk if omitted.
    @param elf_path Path to the port's Vita `.elf`; auto-detected if omitted.
    @param so_path Path to the original Android `.so`; auto-detected if omitted.
    @param so_base Known `.so` load base; auto-detected if omitted.
    @param stack_depth Number of stack words (above/below SP) to scan for
           backtrace candidates and address collection.
    @return None. Prints the report to stdout and writes it to
            `<dump_path>.analysis.txt`.
    """
    from . import config as cfgmod
    if global_cfg is None:
        global_cfg = cfgmod.load_global_config()

    if not _ensure_toolchain(global_cfg):
        return
    from core import CoreParser
    from elf import ElfParser
    from util import u32

    project_dir = project_cfg["_project_dir"]
    exe_name = project_cfg.get("project_name", "")
    build_dir = project_cfg.get("build_dir", "build")

    auto_elf, auto_so = _auto_find_files(project_dir, build_dir)
    elf_path = elf_path or auto_elf
    so_path = so_path or auto_so

    if not os.path.exists(dump_path):
        print(f"{C.RED}{t('crash_analyzer.dump_not_found', path=dump_path)}{C.RESET}")
        return

    report = []

    def log(msg=""):
        print(msg)
        report.append(msg)

    log("=" * 80)
    log(t("crash_analyzer.report_title"))
    log("=" * 80)
    log(t("crash_analyzer.header_core_dump", path=dump_path))
    log(t("crash_analyzer.header_elf", path=elf_path or t("crash_analyzer.not_detected")))
    log(t("crash_analyzer.header_so", path=so_path or t("crash_analyzer.not_detected")))
    log("=" * 80)

    core = CoreParser(dump_path)
    elf = ElfParser(elf_path) if elf_path and os.path.exists(elf_path) else None
    so_syms = SymbolTable(so_path) if so_path and os.path.exists(so_path) else None

    crashed = [t for t in core.threads if t.stop_reason != 0] or core.threads[:1]

    all_addrs = []
    for thr in crashed:
        all_addrs.append(thr.pc)
        all_addrs.append(thr.regs.gpr[14])
        sp = thr.regs.gpr[13]
        for x in range(-4, stack_depth):
            d = core.read_vaddr(sp + 4 * x, 4)
            if d:
                all_addrs.append(u32(d, 0))

    if not so_base and so_syms:
        so_base = _auto_detect_so_base(all_addrs, so_syms)
        if so_base:
            log(t("crash_analyzer.so_base_auto", addr=so_base))
        else:
            so_base = 0x98000000
            log(t("crash_analyzer.so_base_fallback", addr=so_base))

    def resolve(addr):
        notation = core.get_address_notation("", addr)
        if notation.is_located():
            mod_name = notation._VitaAddress__module.name
            seg = notation._VitaAddress__segment.num
            off = notation._VitaAddress__offset
            if elf and (mod_name.endswith(".elf") or mod_name == exe_name) and seg == 1:
                line_info = elf.addr2line(off)
                line_str = line_info.decode("utf-8", "ignore") if isinstance(line_info, bytes) else str(line_info)
                return f"0x{addr:x} [{mod_name} + 0x{off:x}] ({line_str})"
            return f"0x{addr:x} [{mod_name} seg{seg} + 0x{off:x}]"
        if so_base and (so_base <= addr <= so_base + 0x2000000 or 0x90000000 <= addr <= 0x9fffffff):
            so_off = addr - so_base
            so_name = os.path.basename(so_path) if so_path else "lib.so"
            if so_syms:
                return f"0x{addr:x} [{so_name} + 0x{so_off:x} -> {so_syms.lookup(so_off)}]"
            return f"0x{addr:x} [{so_name} + 0x{so_off:x}]"
        return f"0x{addr:x}"

    for thread in crashed:
        log(t("crash_analyzer.thread_crashed", name=thread.name, uid=thread.uid))
        log(t("crash_analyzer.stop_reason_line", code=thread.stop_reason, reason=t(STOP_REASONS[thread.stop_reason])))
        lr = thread.regs.gpr[14]
        log(f"    PC: {resolve(thread.pc)}")
        log(f"    LR: {resolve(lr)}")

        log(t("crash_analyzer.section_cpu_registers"))
        for i in range(13):
            v = thread.regs.gpr[i]
            log(f"    R{i:<2}: 0x{v:08x}  {resolve(v) if v > 0x10000 else ''}")
        sp = thread.regs.gpr[13]
        log(f"    SP : 0x{sp:08x}")

        log("\n" + "=" * 80)
        log(t("crash_analyzer.section_root_cause"))
        log("=" * 80)
        if thread.stop_reason == 0x30004:
            log(t("crash_analyzer.root_cause_data_abort"))
        elif thread.stop_reason == 0x30002:
            log(t("crash_analyzer.root_cause_undefined_instruction"))
        elif thread.stop_reason == 0x60080:
            log(t("crash_analyzer.root_cause_division_by_zero"))
        log(t("crash_analyzer.crash_location", pc=resolve(thread.pc)))

        if so_base and 0x80000000 <= thread.pc <= 0x9fffffff and so_path:
            pc_off = thread.pc - so_base
            crash_marker = f"[{t('crash_analyzer.crash_instruction_marker')}]"
            for line in _disassemble_around(so_path, pc_off, is_thumb=True):
                if crash_marker in line:
                    log(t("crash_analyzer.crash_instruction", line=line.strip()))
                    for i in range(13):
                        if f"r{i}" in line.lower() and thread.regs.gpr[i] == 0:
                            log(t("crash_analyzer.probable_cause_null_ptr", reg=i))

        log(t("crash_analyzer.section_disasm_pc"))
        if so_base and 0x80000000 <= thread.pc <= 0x9fffffff and so_path:
            for line in _disassemble_around(so_path, thread.pc - so_base, is_thumb=True):
                log(line)
        elif elf:
            notation = core.get_address_notation("PC", thread.pc)
            if notation.is_located():
                elf.disas_around_addr(notation._VitaAddress__offset)

        log(t("crash_analyzer.section_stack_content"))
        chain = [f"PC: {resolve(thread.pc)}", f"LR: {resolve(lr)}"]
        for x in range(-4, stack_depth):
            addr = sp + 4 * x
            d = core.read_vaddr(addr, 4)
            if d:
                val = u32(d, 0)
                resolved = resolve(val)
                if ".so" in resolved or ".elf" in resolved or exe_name in resolved:
                    chain.append(t("crash_analyzer.stack_frame", addr=addr, resolved=resolved))
                prefix = "SP => " if addr == sp else "      "
                log(f"    {prefix}0x{addr:08x}: 0x{val:08x}  -> {resolved}")

        log(t("crash_analyzer.section_call_chain"))
        seen = set()
        for frame in chain:
            if frame not in seen:
                log(f"  -> {frame}")
                seen.add(frame)

    log("\n" + "=" * 80)
    log(t("crash_analyzer.section_modules_loaded"))
    log("=" * 80)
    for mod in core.modules:
        segs = ", ".join(t("crash_analyzer.module_segment", num=s.num, start=s.start, size=s.size) for s in mod.segments)
        log(f" - {mod.name:<24} | {segs}")

    analysis_file = f"{dump_path}.analysis.txt"
    try:
        with open(analysis_file, "w", encoding="utf-8") as f:
            f.write("\n".join(report))
        print(f"\n{C.GREEN}{t('crash_analyzer.report_saved', path=analysis_file)}{C.RESET}")
    except OSError as e:
        print(f"{C.RED}{t('crash_analyzer.report_save_failed', error=e)}{C.RESET}")


def analyze_menu(project_cfg, global_cfg):
    """!
    @brief Interactive menu: pick a locally downloaded crash dump and analyze it.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    from . import ftp_ops
    dumps = ftp_ops.list_local_history(project_cfg, "dumps")
    if not dumps:
        print(f"{C.YELLOW}{t('crash_analyzer.menu_no_dumps')}{C.RESET}")
        print(f"{C.DIM}{t('crash_analyzer.menu_no_dumps_hint')}{C.RESET}")
        return
    print(f"{C.BOLD}{t('crash_analyzer.menu_available_dumps')}{C.RESET}")
    for i, p in enumerate(dumps, 1):
        print(f"  {i:2d}. {p.name}")
    choice = input(t("crash_analyzer.menu_choose_prompt")).strip() or "1"
    if not choice.isdigit() or not (1 <= int(choice) <= len(dumps)):
        print(f"{C.RED}{t('crash_analyzer.menu_invalid_choice')}{C.RESET}")
        return
    analyze(project_cfg, str(dumps[int(choice) - 1]), global_cfg)
