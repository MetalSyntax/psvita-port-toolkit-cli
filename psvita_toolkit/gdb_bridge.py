"""!
@file gdb_bridge.py
@brief GDB symbol-map exporter: generates a ready-to-source `.gdb` script that
       gives `gdb-multiarch` symbol names for BOTH the loader's own `.elf`
       and the original Android `.so` still running inside it, against
       whatever gdbstub is already reachable on the REAL PS Vita.

@details
This is deliberately NOT a GDB server -- this toolkit doesn't own the
loader's code, so it can't guarantee one exists, let alone which gdbstub
implementation a given soloader project bundles (if any). What it CAN do
honestly is the annoying, mechanical part every debugging session on a
soloader-based port needs: GDB only knows the LOADER's own symbols out of
the box (from its `.elf`); the Android `.so`'s code is mapped into memory
at some runtime-decided base address the loader itself chose, and GDB has
no idea that blob of memory has symbols at all unless told so explicitly
via `add-symbol-file <path> <base-address>`.

`generate_symbol_map()` writes exactly that -- `target remote`, `symbol-file`
for the loader `.elf`, and `add-symbol-file` for the Android `.so` -- as one
`.gdb` script the porter sources with `gdb-multiarch -x <script>`. The one
thing it genuinely cannot know statically is the `.so`'s runtime base
address (that's a property of the loader's own relocation logic, decided
fresh every run) -- see `docs/dev-notes/gdb_bridge.md` for why that's left
as an explicit placeholder instead of guessed.
"""

import glob
import os
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "gdb_bridge.menu_title": {
        "es": "GDB Bridge (exportador de mapa de símbolos)",
        "en": "GDB Bridge (symbol-map exporter)",
        "pt": "GDB Bridge (exportador de mapa de símbolos)",
    },
    "gdb_bridge.menu_generate": {
        "es": "Generar script .gdb (loader .elf + .so Android)",
        "en": "Generate .gdb script (loader .elf + Android .so)",
        "pt": "Gerar script .gdb (loader .elf + .so Android)",
    },
    "gdb_bridge.gdb_port_prompt": {
        "es": "Puerto del gdbstub en la Vita [{default}]: ",
        "en": "gdbstub port on the Vita [{default}]: ",
        "pt": "Porta do gdbstub na Vita [{default}]: ",
    },
    "gdb_bridge.so_base_prompt": {
        "es": "Dirección base del .so en memoria (hex, Enter si no la sabés todavía): ",
        "en": "Runtime base address of the .so (hex, Enter if you don't know it yet): ",
        "pt": "Endereço base do .so em memória (hex, Enter se ainda não souber): ",
    },
    "gdb_bridge.no_elf": {
        "es": "[!] No se encontró ningún .elf del loader -- generando el script igual, completalo a mano.",
        "en": "[!] No loader .elf found -- generating the script anyway, fill it in by hand.",
        "pt": "[!] Nenhum .elf do loader encontrado -- gerando o script mesmo assim, complete manualmente.",
    },
    "gdb_bridge.no_so": {
        "es": "[!] No se encontró ningún .so de Android -- generando el script igual, completalo a mano.",
        "en": "[!] No Android .so found -- generating the script anyway, fill it in by hand.",
        "pt": "[!] Nenhum .so Android encontrado -- gerando o script mesmo assim, complete manualmente.",
    },
    "gdb_bridge.written": {
        "es": "[+] Script GDB escrito en {path} -- gdb-multiarch -x {path}",
        "en": "[+] GDB script written to {path} -- gdb-multiarch -x {path}",
        "pt": "[+] Script GDB escrito em {path} -- gdb-multiarch -x {path}",
    },
}
i18n.register(STRINGS)

DEFAULT_GDB_PORT = 10001


def _find_primary_elf(project_dir, build_dir="build"):
    """!
    @brief Best-effort discovery of the loader's own `.elf`, same heuristic
           as `crash_analyzer._auto_find_files()` (kept local -- each
           module's own private helper, same convention as `so_patcher.py`).
    @param project_dir Path to the port's project directory.
    @param build_dir Local build output directory, relative to `project_dir`.
    @return Path string to the most likely `.elf`, or `None`.
    """
    candidates = (glob.glob(os.path.join(str(project_dir), build_dir, "*.elf"))
                  + glob.glob(os.path.join(str(project_dir), "*.elf")))
    return candidates[0] if candidates else None


def _find_primary_so(project_dir):
    """!
    @brief Best-effort discovery of the port's original Android `.so`, same
           heuristic duplicated across `jni_analyzer.py`/`so_patcher.py`/
           `mem_align_analyzer.py`.
    @param project_dir Path to the port's project directory.
    @return Path string to the most likely `.so`, or `None`.
    """
    candidates = glob.glob(os.path.join(str(project_dir), "**", "*.so"), recursive=True)
    candidates.sort(key=lambda x: 0 if ("libgame" in x or "libmain" in x) else 1)
    return candidates[0] if candidates else None


def generate_symbol_map(project_cfg, global_cfg=None, elf_path=None, so_path=None,
                         gdb_port=DEFAULT_GDB_PORT, so_base=None, out_dir=None):
    """!
    @brief Write a `.gdb` script wiring `gdb-multiarch` up to both binaries'
           symbols against a gdbstub already reachable on the real console.
    @param project_cfg Per-project config dict (reads `vita_ip`, `build_dir`).
    @param global_cfg Global config dict (accepted for a uniform menu-item
           call signature; unused today).
    @param elf_path Path to the loader's `.elf`; auto-detected if omitted.
    @param so_path Path to the original Android `.so`; auto-detected if omitted.
    @param gdb_port TCP port the Vita's gdbstub listens on.
    @param so_base Runtime base address (int) the loader mapped the `.so`
           to, if already known (e.g. logged by the loader at startup).
           Left as an editable placeholder in the script if `None` -- this
           is a per-run property of the loader's own relocation logic, not
           something static analysis can know ahead of time.
    @param out_dir Directory to write the script into; defaults to the
           project root.
    @return `Path` to the written `.gdb` script.
    """
    project_dir = Path(project_cfg["_project_dir"])
    elf_path = elf_path or _find_primary_elf(project_dir, project_cfg.get("build_dir", "build"))
    so_path = so_path or _find_primary_so(project_dir)

    if not elf_path:
        print(f"{C.YELLOW}{t('gdb_bridge.no_elf')}{C.RESET}")
    if not so_path:
        print(f"{C.YELLOW}{t('gdb_bridge.no_so')}{C.RESET}")

    vita_ip = project_cfg.get("vita_ip", "192.168.1.100")
    so_base_str = hex(so_base) if so_base is not None else "0x00000000"
    so_base_comment = "" if so_base is not None else "  /* EDIT ME: paste the loader's logged .so base address here */"

    lines = [
        "# Auto-generated by psvita-toolkit (GDB symbol-map exporter).",
        "# This assumes a gdbstub is ALREADY running and reachable on the real PS Vita --",
        "# this toolkit doesn't own the loader and can't start one for you. It also",
        "# can't know the .so's runtime base address ahead of time: have your loader log",
        "# it once (e.g. over debugnet_server / a plain printf) right after it finishes",
        "# mapping/relocating the .so, then paste that value below.",
        "# See docs/dev-notes/gdb_bridge.md.",
        "",
        f"target remote {vita_ip}:{gdb_port}",
    ]
    if elf_path:
        lines.append(f'symbol-file "{elf_path}"')
    if so_path:
        lines.append(f'add-symbol-file "{so_path}" {so_base_str}{so_base_comment}')
    lines.append("")

    dest = Path(out_dir) if out_dir else project_dir
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / "vita_debug.gdb"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"{C.GREEN}{t('gdb_bridge.written', path=out_path)}{C.RESET}")
    return out_path


def gdb_bridge_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: ask for the gdbstub port and (optionally) a known
           `.so` base address, then generate the script.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    def _generate():
        port_raw = input(f"{C.BOLD}{t('gdb_bridge.gdb_port_prompt', default=DEFAULT_GDB_PORT)}{C.RESET}").strip()
        gdb_port = int(port_raw) if port_raw.isdigit() else DEFAULT_GDB_PORT

        base_raw = input(f"{C.BOLD}{t('gdb_bridge.so_base_prompt')}{C.RESET}").strip()
        so_base = None
        if base_raw:
            try:
                so_base = int(base_raw, 16)
            except ValueError:
                so_base = None

        generate_symbol_map(project_cfg, global_cfg, gdb_port=gdb_port, so_base=so_base)

    tui.run_menu(t("gdb_bridge.menu_title"), [(t("gdb_bridge.menu_generate"), _generate)])
