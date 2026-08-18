"""
Analizador de crash dumps (.psp2dmp / psp2core-*) -- integrado desde
parse_dump.py, generalizado: el nombre del ejecutable principal y las rutas
de VITASDK/vita-parse-core salen de la config del proyecto/global en vez de
estar hardcodeadas a un juego y a un usuario.

Requiere `vita-parse-core` clonado (ver global_cfg['vita_parse_core_dir']) y
las herramientas de VITASDK (arm-vita-eabi-objdump/c++filt) en el PATH.
"""

import glob
import os
import subprocess
import sys
from collections import Counter, defaultdict

from .tui import C

STOP_REASONS = defaultdict(str, {
    0: "No reason (ejecución normal)",
    0x30002: "Undefined instruction exception (instrucción inválida/corrupta)",
    0x30003: "Prefetch abort exception (fallo al buscar instrucción en memoria)",
    0x30004: "Data abort exception (fallo de acceso a memoria / puntero nulo o inválido)",
    0x60080: "Division by zero",
})


def _ensure_toolchain(global_cfg):
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
        print(f"{C.RED}[-] No se pudo cargar 'vita-parse-core' desde '{vpc_dir}': {e}{C.RESET}")
        print(f"{C.DIM}    Clonar vita-parse-core o corregir 'vita_parse_core_dir' en la config global.{C.RESET}")
        return None
    return True


def _demangle(name):
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
    def __init__(self, so_path):
        self.so_path = so_path
        self.symbols = []
        self._load()

    def _load(self):
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
            print(f"{C.YELLOW}[!] No se pudieron extraer símbolos de {self.so_path}: {e}{C.RESET}")

    def lookup(self, offset):
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
                lines.append(f"  ==> {line:<52} <== [INSTRUCCIÓN DEL CRASH]" if marker else f"      {line}")
        return lines
    except Exception as e:
        return [f"Error al desensamblar: {e}"]


def _auto_find_files(project_dir, build_dir):
    elf_candidates = glob.glob(os.path.join(project_dir, build_dir, "*.elf")) + glob.glob(os.path.join(project_dir, "*.elf"))
    elf_file = elf_candidates[0] if elf_candidates else None

    so_candidates = glob.glob(os.path.join(project_dir, "**", "*.so"), recursive=True)
    so_candidates.sort(key=lambda x: 0 if ("libgame" in x or "libmain" in x) else 1)
    so_file = so_candidates[0] if so_candidates else None
    return elf_file, so_file


def analyze(project_cfg, dump_path, global_cfg=None, elf_path=None, so_path=None, so_base=None, stack_depth=36):
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
        print(f"{C.RED}[-] Archivo dump no encontrado: {dump_path}{C.RESET}")
        return

    report = []

    def log(msg=""):
        print(msg)
        report.append(msg)

    log("=" * 80)
    log("           PS VITA CRASH DUMP ANALYSIS REPORT (vita-parse-core)")
    log("=" * 80)
    log(f" Core Dump: {dump_path}")
    log(f" ELF (Vita): {elf_path or 'no detectado'}")
    log(f" SO (Android): {so_path or 'no detectado'}")
    log("=" * 80)

    core = CoreParser(dump_path)
    elf = ElfParser(elf_path) if elf_path and os.path.exists(elf_path) else None
    so_syms = SymbolTable(so_path) if so_path and os.path.exists(so_path) else None

    crashed = [t for t in core.threads if t.stop_reason != 0] or core.threads[:1]

    all_addrs = []
    for t in crashed:
        all_addrs.append(t.pc)
        all_addrs.append(t.regs.gpr[14])
        sp = t.regs.gpr[13]
        for x in range(-4, stack_depth):
            d = core.read_vaddr(sp + 4 * x, 4)
            if d:
                all_addrs.append(u32(d, 0))

    if not so_base and so_syms:
        so_base = _auto_detect_so_base(all_addrs, so_syms)
        if so_base:
            log(f"[+] Base de memoria del .so AUTO-DETECTADA en: 0x{so_base:x}")
        else:
            so_base = 0x98000000
            log(f"[*] Base de memoria del .so, fallback: 0x{so_base:x}")

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
        log(f"\n[!] HILO EN CRASH: '{thread.name}' (ID: 0x{thread.uid:x})")
        log(f"    Razón de parada: 0x{thread.stop_reason:x} ({STOP_REASONS[thread.stop_reason]})")
        lr = thread.regs.gpr[14]
        log(f"    PC: {resolve(thread.pc)}")
        log(f"    LR: {resolve(lr)}")

        log("\n--- REGISTROS CPU ---")
        for i in range(13):
            v = thread.regs.gpr[i]
            log(f"    R{i:<2}: 0x{v:08x}  {resolve(v) if v > 0x10000 else ''}")
        sp = thread.regs.gpr[13]
        log(f"    SP : 0x{sp:08x}")

        log("\n" + "=" * 80)
        log("                   DIAGNÓSTICO AUTOMÁTICO DE CAUSA RAÍZ")
        log("=" * 80)
        if thread.stop_reason == 0x30004:
            log(" [*] Data Abort (acceso a memoria inválida).")
        elif thread.stop_reason == 0x30002:
            log(" [*] Undefined Instruction (código corrupto/inválido).")
        elif thread.stop_reason == 0x60080:
            log(" [*] Division by zero.")
        log(f" [*] Ubicación del crash: PC = {resolve(thread.pc)}")

        if so_base and 0x80000000 <= thread.pc <= 0x9fffffff and so_path:
            pc_off = thread.pc - so_base
            for line in _disassemble_around(so_path, pc_off, is_thumb=True):
                if "[INSTRUCCIÓN DEL CRASH]" in line:
                    log(f" [*] Instrucción causante: {line.strip()}")
                    for i in range(13):
                        if f"r{i}" in line.lower() and thread.regs.gpr[i] == 0:
                            log(f" [*] CAUSA PROBABLE: R{i} es 0x00000000 (puntero NULO).")

        log("\n--- DESENSAMBLADO EN PC ---")
        if so_base and 0x80000000 <= thread.pc <= 0x9fffffff and so_path:
            for line in _disassemble_around(so_path, thread.pc - so_base, is_thumb=True):
                log(line)
        elif elf:
            notation = core.get_address_notation("PC", thread.pc)
            if notation.is_located():
                elf.disas_around_addr(notation._VitaAddress__offset)

        log("\n--- CONTENIDO DE LA PILA (BACKTRACE) ---")
        chain = [f"PC: {resolve(thread.pc)}", f"LR: {resolve(lr)}"]
        for x in range(-4, stack_depth):
            addr = sp + 4 * x
            d = core.read_vaddr(addr, 4)
            if d:
                val = u32(d, 0)
                resolved = resolve(val)
                if ".so" in resolved or ".elf" in resolved or exe_name in resolved:
                    chain.append(f"Stack 0x{addr:x}: {resolved}")
                prefix = "SP => " if addr == sp else "      "
                log(f"    {prefix}0x{addr:08x}: 0x{val:08x}  -> {resolved}")

        log("\n--- SECUENCIA DE LLAMADAS RECONSTRUIDA ---")
        seen = set()
        for frame in chain:
            if frame not in seen:
                log(f"  -> {frame}")
                seen.add(frame)

    log("\n" + "=" * 80)
    log("                       MÓDULOS CARGADOS EN LA VITA")
    log("=" * 80)
    for mod in core.modules:
        segs = ", ".join(f"Seg{s.num}: 0x{s.start:x} (size 0x{s.size:x})" for s in mod.segments)
        log(f" - {mod.name:<24} | {segs}")

    analysis_file = f"{dump_path}.analysis.txt"
    try:
        with open(analysis_file, "w", encoding="utf-8") as f:
            f.write("\n".join(report))
        print(f"\n{C.GREEN}[+] Reporte guardado en: {analysis_file}{C.RESET}")
    except OSError as e:
        print(f"{C.RED}[-] No se pudo guardar el reporte: {e}{C.RESET}")


def analyze_menu(project_cfg, global_cfg):
    from . import ftp_ops
    dumps = ftp_ops.list_local_history(project_cfg, "dumps")
    if not dumps:
        print(f"{C.YELLOW}[-] No hay ningún crash dump descargado todavía en logs/.{C.RESET}")
        print(f"{C.DIM}    Usá 'Descargar logs / crash dumps' primero.{C.RESET}")
        return
    print(f"{C.BOLD}Crash dumps disponibles localmente:{C.RESET}")
    for i, p in enumerate(dumps, 1):
        print(f"  {i:2d}. {p.name}")
    choice = input("Elegí uno [1] (Enter = el más reciente): ").strip() or "1"
    if not choice.isdigit() or not (1 <= int(choice) <= len(dumps)):
        print(f"{C.RED}[-] Opción inválida.{C.RESET}")
        return
    analyze(project_cfg, str(dumps[int(choice) - 1]), global_cfg)
