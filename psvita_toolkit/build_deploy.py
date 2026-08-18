"""
Asistente de compilación y despliegue -- generaliza build_and_install.sh
(versión Advena, la más completa: destino -> preset -> despliegue).

Los presets NO están hardcodeados por juego: los 4 universales (Debug,
Release, RelWithDebInfo, MinSizeRel) siempre están, y además se auto-
descubren banderas extra grepeando las condiciones `"$1" = "--xxx"` del
`build.sh` del proyecto activo (mismo truco que ya usaban los
build_and_install.sh de Zenonia4/Dungeon Hunter 2) -- así ningún flag
específico de un motor (NEON, dirty-rect, downsample, etc.) queda
hardcodeado en la herramienta genérica; simplemente aparece si el build.sh
de ESE port lo define.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import tui
from .tui import C

UNIVERSAL_PRESETS = [
    ("Debug", "debug", "Logs activos, -O2 -g -- recomendado para desarrollo"),
    ("Release", "release", "Optimizado -O3, recomendado para producción"),
    ("RelWithDebInfo", "relwithdebinfo", "Release -O3 + símbolos -g (para symbolizar crash dumps)"),
    ("MinSizeRel", "minsizerel", "Optimizado para tamaño mínimo de binario -Os"),
]


def _discover_extra_flags(build_sh_path):
    """Grepea las condiciones '"$1" = "--xxx"' de build.sh del proyecto,
    en el orden en que aparecen -- sin asumir NADA sobre qué flags son."""
    if not build_sh_path.exists():
        return []
    text = build_sh_path.read_text(errors="ignore")
    seen = []
    for m in re.finditer(r'"(--[a-z0-9-]+)"', text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def _flag_comment(build_sh_path, flag):
    """Busca el comentario pegado a donde se define el flag en build.sh
    (la línea anterior al 'if/elif', o la primera línea del bloque si el
    comentario está adentro -- patrón típico: 'elif [ "$1" = "--x" ]; then'
    seguido de un comentario describiendo la variante) para mostrarlo como
    descripción corta en el menú."""
    if not build_sh_path.exists():
        return ""
    lines = build_sh_path.read_text(errors="ignore").splitlines()
    for i, line in enumerate(lines):
        if f'"{flag}"' not in line:
            continue
        if i > 0 and lines[i - 1].strip().startswith("#"):
            return lines[i - 1].strip().lstrip("#").strip()
        if i + 1 < len(lines) and lines[i + 1].strip().startswith("#"):
            return lines[i + 1].strip().lstrip("#").strip()
    return ""


def _choose_target():
    print(f"{C.BOLD}[1/3] ¿Cuál es el destino de ejecución?{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} Vita3K            (Emulador -- iteración rápida)")
    print(f"  {C.GREEN}2){C.RESET} PS Vita Física    (Consola real vía FTP)")
    print(f"  {C.GREEN}3){C.RESET} Solo Compilar     (Generar binarios sin desplegar)")
    print(f"  {C.RED}q){C.RESET} Cancelar")
    choice = input("Destino [1]: ").strip() or "1"
    return {"1": "vita3k", "2": "psvita", "3": "local"}.get(choice)


def _choose_preset(build_sh_path):
    extra_flags = _discover_extra_flags(build_sh_path)

    print(f"\n{C.BOLD}[2/3] Configuración de compilación:{C.RESET}")
    options = list(UNIVERSAL_PRESETS)
    for flag in extra_flags:
        desc = _flag_comment(build_sh_path, flag) or "(ver el comentario de este flag en build.sh)"
        options.append((flag, flag, desc))

    for i, (label, _value, desc) in enumerate(options, 1):
        print(f"  {C.GREEN}{i}){C.RESET} {C.BOLD}{label:<18}{C.RESET} {desc}")
    print(f"  {C.GREEN}{len(options) + 1}){C.RESET} Personalizado (flags de CMake a mano)")
    print(f"  {C.RED}q){C.RESET} Cancelar")

    choice = input("Opción [1]: ").strip() or "1"
    if choice.lower() in ("q",):
        return None, None
    if not choice.isdigit():
        return None, None
    idx = int(choice)
    if 1 <= idx <= len(options):
        label, value, _ = options[idx - 1]
        extra_cmake_flags = []
        if value == "--downsample-test":
            ratio = input("Ratio de downsample DS_NUM/DS_DEN [2/3]: ").strip() or "2/3"
            num, den = ratio.split("/")
            extra_cmake_flags = [num, den]
        return value, extra_cmake_flags
    if idx == len(options) + 1:
        custom = input("Flags de CMake, separados por espacio (ej. -DDEBUG_SOLOADER=ON): ").strip()
        return "custom", custom.split()
    return None, None


def _run_build(project_dir, preset, extra_args):
    build_sh = project_dir / "build.sh"
    if not build_sh.exists() or not os.access(build_sh, os.X_OK):
        print(f"{C.RED}[-] No se encontró (o no es ejecutable) {build_sh}.{C.RESET}")
        return False
    args = ["bash", str(build_sh)]
    if preset and preset != "custom":
        args.append(preset)
    args.extend(extra_args or [])
    print(f"[*] Ejecutando: {' '.join(args)}\n")
    r = subprocess.run(args, cwd=project_dir)
    return r.returncode == 0


def _find_output_vpk(project_dir, build_dir, project_name, preset):
    build_path = project_dir / build_dir
    if not build_path.is_dir():
        return None
    candidates = sorted(build_path.glob("*.vpk"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    # Preferir el que matchea el preset/nombre de proyecto si hay varios recién tocados
    for p in candidates:
        if preset and preset.replace("-", "_").replace("--", "") in p.stem:
            return p
    return candidates[0]


def _deploy_vita3k(project_cfg, global_cfg, vpk_path):
    project_dir = Path(project_cfg["_project_dir"])
    titleid = project_cfg["titleid"]
    build_dir = project_dir / project_cfg.get("build_dir", "build")
    eboot = build_dir / "eboot.bin"

    print(f"\n{C.BOLD}¿Cómo desplegar en Vita3K?{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} Hot-swap eboot.bin + relanzar Vita3K (rápido -- recomendado)")
    print(f"  {C.GREEN}2){C.RESET} Instalar VPK completo y lanzar")
    print(f"  {C.GREEN}3){C.RESET} Solo copiar eboot.bin (sin abrir el emulador)")
    print(f"  {C.GREEN}4){C.RESET} Omitir despliegue")
    choice = input("Opción [1]: ").strip() or "1"

    vita3k_fs = Path(global_cfg.get("vita3k_fs_dir", "")) / titleid
    vita3k_app = global_cfg.get("vita3k_app", "")

    if choice == "1":
        if not eboot.exists():
            print(f"{C.RED}[-] No se encontró {eboot}.{C.RESET}")
            return
        vita3k_fs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(eboot, vita3k_fs / "eboot.bin")
        font = project_dir / "extras" / "fonts" / "DejaVuSans.ttf"
        if font.exists():
            shutil.copy2(font, vita3k_fs / "DejaVuSans.ttf")
        subprocess.run(["pkill", "-9", "-x", "Vita3K"], capture_output=True)
        time.sleep(1)
        subprocess.run(["open", "-a", "Vita3K"])
        print(f"{C.GREEN}[+] eboot.bin desplegado en {vita3k_fs} y Vita3K relanzado.{C.RESET}")

        if tui.confirm("¿Hacer doble clic automático en el ícono del juego (Quartz)?"):
            from . import automation_mac
            time.sleep(3)
            automation_mac.bring_to_front("Vita3K")
            time.sleep(1)
            if automation_mac.double_click_first_game_row():
                print(f"{C.GREEN}[+] Doble clic realizado.{C.RESET}")
        log_path = Path(global_cfg.get("vita3k_logs_dir", "")) / f"{titleid} - [{project_cfg['game_name']}].log"
        print(f"{C.DIM}Log en vivo: tail -f \"{log_path}\"{C.RESET}")
    elif choice == "2":
        if not (vpk_path and vpk_path.exists()):
            print(f"{C.RED}[-] No hay VPK para instalar.{C.RESET}")
            return
        if vita3k_app and os.access(vita3k_app, os.X_OK):
            subprocess.Popen([vita3k_app, "-B", "OpenGL", str(vpk_path)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"{C.GREEN}[+] Vita3K instalando y lanzando el VPK.{C.RESET}")
        else:
            subprocess.run(["open", "-a", "Vita3K", str(vpk_path)])
    elif choice == "3":
        if not eboot.exists():
            print(f"{C.RED}[-] No se encontró {eboot}.{C.RESET}")
            return
        vita3k_fs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(eboot, vita3k_fs / "eboot.bin")
        print(f"{C.GREEN}[+] eboot.bin copiado a {vita3k_fs}{C.RESET}")
    else:
        print("[*] Despliegue en Vita3K omitido.")


def _deploy_psvita(project_cfg, global_cfg, vpk_path):
    from . import ftp_ops
    print(f"\n{C.BOLD}¿Cómo desplegar a la PS Vita física (FTP)?{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} Subir SOLO eboot.bin (rápido, no reinstala el VPK)")
    print(f"  {C.GREEN}2){C.RESET} Subir VPK completo a ux0:downloads/ (instalar desde VitaShell)")
    print(f"  {C.GREEN}3){C.RESET} Omitir")
    choice = input("Opción [1]: ").strip() or "1"
    if choice == "1":
        ftp_ops.upload_eboot(project_cfg, global_cfg)
    elif choice == "2":
        ftp_ops.upload_vpk(project_cfg, global_cfg)
    else:
        print("[*] Subida a PS Vita omitida.")


def build_and_deploy_wizard(project_cfg, global_cfg):
    project_dir = Path(project_cfg["_project_dir"])
    build_sh_path = project_dir / "build.sh"

    target = _choose_target()
    if not target:
        print("[*] Cancelado.")
        return

    preset, extra_args = _choose_preset(build_sh_path)
    if preset is None:
        print("[*] Cancelado.")
        return

    print(f"\n{C.CYAN}{C.BOLD}================================================================{C.RESET}")
    print(f"  🔨 Compilando {project_cfg['game_name']}  (preset: {preset}, destino: {target})")
    print(f"{C.CYAN}{C.BOLD}================================================================{C.RESET}\n")

    ok = _run_build(project_dir, preset, extra_args)
    if not ok:
        print(f"{C.RED}[-] El build falló -- revisar el output de arriba.{C.RESET}")
        return

    vpk_path = _find_output_vpk(project_dir, project_cfg.get("build_dir", "build"),
                                 project_cfg["project_name"], preset)
    if vpk_path:
        print(f"{C.GREEN}[+] Build exitoso: {vpk_path}{C.RESET}")
    else:
        print(f"{C.YELLOW}[!] Build terminó pero no se encontró ningún .vpk en "
              f"{project_cfg.get('build_dir', 'build')}/ -- revisar VITA_VPKNAME en CMakeLists.txt.{C.RESET}")

    if target == "vita3k":
        _deploy_vita3k(project_cfg, global_cfg, vpk_path)
    elif target == "psvita":
        _deploy_psvita(project_cfg, global_cfg, vpk_path)
    else:
        print(f"\n{C.DIM}Tip: para desplegar más tarde usá las opciones del menú principal "
              f"'Desplegar en Vita3K' / 'Subir a PS Vita'.{C.RESET}")


def deploy_only_vita3k(project_cfg, global_cfg):
    """Re-despliega el último build ya compilado, sin recompilar (equivalente
    a elegir 'Solo copiar/relanzar' cuando ya tenés un eboot.bin fresco)."""
    project_dir = Path(project_cfg["_project_dir"])
    vpk_path = _find_output_vpk(project_dir, project_cfg.get("build_dir", "build"),
                                 project_cfg["project_name"], None)
    _deploy_vita3k(project_cfg, global_cfg, vpk_path)
