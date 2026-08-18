"""!
@file build_deploy.py
@brief Build and deploy wizard: target selection, build presets, running the
       project's `build.sh`, locating the resulting `.vpk`, and deploying to
       Vita3K or a physical PS Vita.

@details
Build presets are not hardcoded per game: the 4 universal ones (Debug,
Release, RelWithDebInfo, MinSizeRel) are always offered, and additional
project-specific flags are auto-discovered by scanning the active project's
`build.sh` for `"$1" = "--xxx"` conditions.

See `docs/dev-notes/build_deploy.md` for the rationale behind this
auto-discovery design (vs. hardcoding engine-specific flags) and the
import-time i18n-key freezing in `UNIVERSAL_PRESETS`.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

# UNIVERSAL_PRESETS' descriptors are stored as i18n KEYS (not resolved text)
# because this list is built at module scope, at import time, before the
# active language is set. See docs/dev-notes/build_deploy.md for why. They
# are resolved with t() in _choose_preset(), once the language is known.
UNIVERSAL_PRESETS = [
    ("Debug", "debug", "build_deploy.preset_debug_desc"),
    ("Release", "release", "build_deploy.preset_release_desc"),
    ("RelWithDebInfo", "relwithdebinfo", "build_deploy.preset_relwithdebinfo_desc"),
    ("MinSizeRel", "minsizerel", "build_deploy.preset_minsizerel_desc"),
]

STRINGS = {
    "build_deploy.preset_debug_desc": {
        "es": "Logs activos, -O2 -g -- recomendado para desarrollo",
        "en": "Active logs, -O2 -g -- recommended for development",
        "pt": "Logs ativos, -O2 -g -- recomendado para desenvolvimento",
    },
    "build_deploy.preset_release_desc": {
        "es": "Optimizado -O3, recomendado para producción",
        "en": "Optimized -O3, recommended for production",
        "pt": "Otimizado -O3, recomendado para produção",
    },
    "build_deploy.preset_relwithdebinfo_desc": {
        "es": "Release -O3 + símbolos -g (para symbolizar crash dumps)",
        "en": "Release -O3 + -g symbols (to symbolicate crash dumps)",
        "pt": "Release -O3 + símbolos -g (para simbolizar crash dumps)",
    },
    "build_deploy.preset_minsizerel_desc": {
        "es": "Optimizado para tamaño mínimo de binario -Os",
        "en": "Optimized for minimum binary size -Os",
        "pt": "Otimizado para tamanho mínimo de binário -Os",
    },
    "build_deploy.choose_target_title": {
        "es": "[1/3] ¿Cuál es el destino de ejecución?",
        "en": "[1/3] What's the execution target?",
        "pt": "[1/3] Qual é o destino de execução?",
    },
    "build_deploy.target_vita3k": {
        "es": "Vita3K            (Emulador -- iteración rápida)",
        "en": "Vita3K            (Emulator -- fast iteration)",
        "pt": "Vita3K            (Emulador -- iteração rápida)",
    },
    "build_deploy.target_psvita": {
        "es": "PS Vita Física    (Consola real vía FTP)",
        "en": "Physical PS Vita  (Real console via FTP)",
        "pt": "PS Vita Física    (Console real via FTP)",
    },
    "build_deploy.target_local": {
        "es": "Solo Compilar     (Generar binarios sin desplegar)",
        "en": "Build Only        (Generate binaries without deploying)",
        "pt": "Somente Compilar  (Gerar binários sem implantar)",
    },
    "build_deploy.cancel": {
        "es": "Cancelar",
        "en": "Cancel",
        "pt": "Cancelar",
    },
    "build_deploy.target_prompt": {
        "es": "Destino [1]: ",
        "en": "Target [1]: ",
        "pt": "Destino [1]: ",
    },
    "build_deploy.choose_preset_title": {
        "es": "[2/3] Configuración de compilación:",
        "en": "[2/3] Build configuration:",
        "pt": "[2/3] Configuração de compilação:",
    },
    "build_deploy.flag_no_desc": {
        "es": "(ver el comentario de este flag en build.sh)",
        "en": "(see this flag's comment in build.sh)",
        "pt": "(ver o comentário desta flag em build.sh)",
    },
    "build_deploy.preset_custom": {
        "es": "Personalizado (flags de CMake a mano)",
        "en": "Custom (manual CMake flags)",
        "pt": "Personalizado (flags de CMake manuais)",
    },
    "build_deploy.option_prompt": {
        "es": "Opción [1]: ",
        "en": "Option [1]: ",
        "pt": "Opção [1]: ",
    },
    "build_deploy.downsample_ratio_prompt": {
        "es": "Ratio de downsample DS_NUM/DS_DEN [2/3]: ",
        "en": "Downsample ratio DS_NUM/DS_DEN [2/3]: ",
        "pt": "Proporção de downsample DS_NUM/DS_DEN [2/3]: ",
    },
    "build_deploy.custom_flags_prompt": {
        "es": "Flags de CMake, separados por espacio (ej. -DDEBUG_SOLOADER=ON): ",
        "en": "CMake flags, space-separated (e.g. -DDEBUG_SOLOADER=ON): ",
        "pt": "Flags do CMake, separadas por espaço (ex. -DDEBUG_SOLOADER=ON): ",
    },
    "build_deploy.build_sh_not_found": {
        "es": "[-] No se encontró (o no es ejecutable) {build_sh}.",
        "en": "[-] Couldn't find (or it's not executable) {build_sh}.",
        "pt": "[-] Não foi encontrado (ou não é executável) {build_sh}.",
    },
    "build_deploy.running_command": {
        "es": "[*] Ejecutando: {cmd}",
        "en": "[*] Running: {cmd}",
        "pt": "[*] Executando: {cmd}",
    },
    "build_deploy.deploy_vita3k_title": {
        "es": "¿Cómo desplegar en Vita3K?",
        "en": "How do you want to deploy to Vita3K?",
        "pt": "Como implantar no Vita3K?",
    },
    "build_deploy.deploy_vita3k_opt1": {
        "es": "Hot-swap eboot.bin + relanzar Vita3K (rápido -- recomendado)",
        "en": "Hot-swap eboot.bin + relaunch Vita3K (fast -- recommended)",
        "pt": "Hot-swap do eboot.bin + relançar Vita3K (rápido -- recomendado)",
    },
    "build_deploy.deploy_vita3k_opt2": {
        "es": "Instalar VPK completo y lanzar",
        "en": "Install full VPK and launch",
        "pt": "Instalar o VPK completo e iniciar",
    },
    "build_deploy.deploy_vita3k_opt3": {
        "es": "Solo copiar eboot.bin (sin abrir el emulador)",
        "en": "Just copy eboot.bin (without opening the emulator)",
        "pt": "Apenas copiar o eboot.bin (sem abrir o emulador)",
    },
    "build_deploy.deploy_vita3k_opt4": {
        "es": "Omitir despliegue",
        "en": "Skip deployment",
        "pt": "Pular implantação",
    },
    "build_deploy.eboot_not_found": {
        "es": "[-] No se encontró {eboot}.",
        "en": "[-] Couldn't find {eboot}.",
        "pt": "[-] Não foi encontrado {eboot}.",
    },
    "build_deploy.eboot_deployed_relaunched": {
        "es": "[+] eboot.bin desplegado en {vita3k_fs} y Vita3K relanzado.",
        "en": "[+] eboot.bin deployed to {vita3k_fs} and Vita3K relaunched.",
        "pt": "[+] eboot.bin implantado em {vita3k_fs} e Vita3K relançado.",
    },
    "build_deploy.confirm_double_click": {
        "es": "¿Hacer doble clic automático en el ícono del juego (Quartz)?",
        "en": "Auto double-click the game icon (Quartz)?",
        "pt": "Fazer duplo clique automático no ícone do jogo (Quartz)?",
    },
    "build_deploy.double_click_done": {
        "es": "[+] Doble clic realizado.",
        "en": "[+] Double-click done.",
        "pt": "[+] Duplo clique realizado.",
    },
    "build_deploy.live_log_tip": {
        "es": "Log en vivo: tail -f \"{log_path}\"",
        "en": "Live log: tail -f \"{log_path}\"",
        "pt": "Log ao vivo: tail -f \"{log_path}\"",
    },
    "build_deploy.vita3k_installing_vpk": {
        "es": "[+] Vita3K instalando y lanzando el VPK.",
        "en": "[+] Vita3K installing and launching the VPK.",
        "pt": "[+] Vita3K instalando e iniciando o VPK.",
    },
    "build_deploy.no_vpk_to_install": {
        "es": "[-] No hay VPK para instalar.",
        "en": "[-] No VPK available to install.",
        "pt": "[-] Não há VPK para instalar.",
    },
    "build_deploy.eboot_copied": {
        "es": "[+] eboot.bin copiado a {vita3k_fs}",
        "en": "[+] eboot.bin copied to {vita3k_fs}",
        "pt": "[+] eboot.bin copiado para {vita3k_fs}",
    },
    "build_deploy.vita3k_deploy_skipped": {
        "es": "[*] Despliegue en Vita3K omitido.",
        "en": "[*] Vita3K deployment skipped.",
        "pt": "[*] Implantação no Vita3K ignorada.",
    },
    "build_deploy.deploy_psvita_title": {
        "es": "¿Cómo desplegar a la PS Vita física (FTP)?",
        "en": "How do you want to deploy to the physical PS Vita (FTP)?",
        "pt": "Como implantar no PS Vita físico (FTP)?",
    },
    "build_deploy.deploy_psvita_opt1": {
        "es": "Subir SOLO eboot.bin (rápido, no reinstala el VPK)",
        "en": "Upload ONLY eboot.bin (fast, doesn't reinstall the VPK)",
        "pt": "Enviar SOMENTE o eboot.bin (rápido, não reinstala o VPK)",
    },
    "build_deploy.deploy_psvita_opt2": {
        "es": "Subir VPK completo a ux0:downloads/ (instalar desde VitaShell)",
        "en": "Upload full VPK to ux0:downloads/ (install from VitaShell)",
        "pt": "Enviar o VPK completo para ux0:downloads/ (instalar pelo VitaShell)",
    },
    "build_deploy.deploy_psvita_opt3": {
        "es": "Omitir",
        "en": "Skip",
        "pt": "Ignorar",
    },
    "build_deploy.psvita_upload_skipped": {
        "es": "[*] Subida a PS Vita omitida.",
        "en": "[*] Upload to PS Vita skipped.",
        "pt": "[*] Envio para o PS Vita ignorado.",
    },
    "build_deploy.cancelled": {
        "es": "[*] Cancelado.",
        "en": "[*] Cancelled.",
        "pt": "[*] Cancelado.",
    },
    "build_deploy.building_header": {
        "es": "🔨 Compilando {game_name}  (preset: {preset}, destino: {target})",
        "en": "🔨 Building {game_name}  (preset: {preset}, target: {target})",
        "pt": "🔨 Compilando {game_name}  (preset: {preset}, destino: {target})",
    },
    "build_deploy.build_failed": {
        "es": "[-] El build falló -- revisar el output de arriba.",
        "en": "[-] The build failed -- check the output above.",
        "pt": "[-] O build falhou -- verifique a saída acima.",
    },
    "build_deploy.build_success": {
        "es": "[+] Build exitoso: {vpk_path}",
        "en": "[+] Build successful: {vpk_path}",
        "pt": "[+] Build concluído com sucesso: {vpk_path}",
    },
    "build_deploy.build_no_vpk_found": {
        "es": "[!] Build terminó pero no se encontró ningún .vpk en {build_dir}/ -- revisar VITA_VPKNAME en CMakeLists.txt.",
        "en": "[!] The build finished but no .vpk was found in {build_dir}/ -- check VITA_VPKNAME in CMakeLists.txt.",
        "pt": "[!] O build terminou, mas nenhum .vpk foi encontrado em {build_dir}/ -- verifique VITA_VPKNAME no CMakeLists.txt.",
    },
    "build_deploy.deploy_later_tip": {
        "es": "Tip: para desplegar más tarde usá las opciones del menú principal 'Desplegar en Vita3K' / 'Subir a PS Vita'.",
        "en": "Tip: to deploy later, use the main menu options 'Deploy to Vita3K' / 'Upload to PS Vita'.",
        "pt": "Dica: para implantar depois, use as opções do menu principal 'Implantar no Vita3K' / 'Enviar para o PS Vita'.",
    },
}
i18n.register(STRINGS)


def _discover_extra_flags(build_sh_path):
    """!
    @brief Scan a project's `build.sh` for custom `"$1" = "--xxx"` flag conditions.
    @details Returns the flags in the order they appear in the file, making no
             assumptions about which flags exist.
    @param build_sh_path Path to the project's `build.sh`.
    @return List of flag strings (e.g. `["--downsample-test"]`), in file order;
            `[]` if `build_sh_path` doesn't exist.
    """
    if not build_sh_path.exists():
        return []
    text = build_sh_path.read_text(errors="ignore")
    seen = []
    for m in re.finditer(r'"(--[a-z0-9-]+)"', text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def _flag_comment(build_sh_path, flag):
    """!
    @brief Find the comment describing a given `build.sh` flag, for display
           as a short description in the preset menu.
    @details Looks for a `#`-comment on the line immediately before or after
             the line that defines the flag (typical pattern: an
             `elif [ "$1" = "--x" ]; then` line with a comment above or below
             it describing the variant).
    @param build_sh_path Path to the project's `build.sh`.
    @param flag Flag string to look up (e.g. `"--downsample-test"`).
    @return The comment text with the leading `#` stripped, or `""` if none found.
    """
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
    """!
    @brief Prompt the user to pick the execution target.
    @return `"vita3k"`, `"psvita"`, `"local"`, or `None` if cancelled/invalid input.
    """
    print(f"{C.BOLD}{t('build_deploy.choose_target_title')}{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} {t('build_deploy.target_vita3k')}")
    print(f"  {C.GREEN}2){C.RESET} {t('build_deploy.target_psvita')}")
    print(f"  {C.GREEN}3){C.RESET} {t('build_deploy.target_local')}")
    print(f"  {C.RED}q){C.RESET} {t('build_deploy.cancel')}")
    choice = input(t("build_deploy.target_prompt")).strip() or "1"
    return {"1": "vita3k", "2": "psvita", "3": "local"}.get(choice)


def _choose_preset(build_sh_path):
    """!
    @brief Prompt the user to pick a build preset: the 4 universal presets,
           any extra flags auto-discovered from the project's `build.sh`, or
           a custom manual CMake flags entry.
    @param build_sh_path Path to the project's `build.sh`, used to discover extra flags.
    @return Tuple `(preset_value, extra_cmake_flags)`, or `(None, None)` if cancelled.
    """
    extra_flags = _discover_extra_flags(build_sh_path)

    print(f"\n{C.BOLD}{t('build_deploy.choose_preset_title')}{C.RESET}")
    # Resolve UNIVERSAL_PRESETS' i18n keys now, since the active language is
    # already set by this point (see the note next to UNIVERSAL_PRESETS above).
    options = [(label, value, t(desc_key)) for label, value, desc_key in UNIVERSAL_PRESETS]
    for flag in extra_flags:
        desc = _flag_comment(build_sh_path, flag) or t("build_deploy.flag_no_desc")
        options.append((flag, flag, desc))

    for i, (label, _value, desc) in enumerate(options, 1):
        print(f"  {C.GREEN}{i}){C.RESET} {C.BOLD}{label:<18}{C.RESET} {desc}")
    print(f"  {C.GREEN}{len(options) + 1}){C.RESET} {t('build_deploy.preset_custom')}")
    print(f"  {C.RED}q){C.RESET} {t('build_deploy.cancel')}")

    choice = input(t("build_deploy.option_prompt")).strip() or "1"
    if choice.lower() in ("q",):
        return None, None
    if not choice.isdigit():
        return None, None
    idx = int(choice)
    if 1 <= idx <= len(options):
        label, value, _ = options[idx - 1]
        extra_cmake_flags = []
        if value == "--downsample-test":
            ratio = input(t("build_deploy.downsample_ratio_prompt")).strip() or "2/3"
            num, den = ratio.split("/")
            extra_cmake_flags = [num, den]
        return value, extra_cmake_flags
    if idx == len(options) + 1:
        custom = input(t("build_deploy.custom_flags_prompt")).strip()
        return "custom", custom.split()
    return None, None


def _run_build(project_dir, preset, extra_args):
    """!
    @brief Run the project's `build.sh` with the chosen preset and extra args.
    @param project_dir Path to the project directory.
    @param preset Preset value to pass as the first argument to `build.sh`
                  (omitted if `"custom"` or falsy).
    @param extra_args Extra list of arguments to append after the preset.
    @return `True` if `build.sh` exited with code 0.
    """
    build_sh = project_dir / "build.sh"
    if not build_sh.exists() or not os.access(build_sh, os.X_OK):
        print(f"{C.RED}{t('build_deploy.build_sh_not_found', build_sh=build_sh)}{C.RESET}")
        return False
    args = ["bash", str(build_sh)]
    if preset and preset != "custom":
        args.append(preset)
    args.extend(extra_args or [])
    print(f"{t('build_deploy.running_command', cmd=' '.join(args))}\n")
    r = subprocess.run(args, cwd=project_dir)
    return r.returncode == 0


def _find_output_vpk(project_dir, build_dir, project_name, preset):
    """!
    @brief Locate the most likely `.vpk` produced by a build.
    @details Picks the most recently modified `.vpk` in `build_dir` whose
             filename matches `preset`, falling back to the most recently
             modified `.vpk` overall if none matches or `preset` is falsy.
    @param project_dir Path to the project directory.
    @param build_dir Build output directory, relative to `project_dir`.
    @param project_name Project/VPK name (unused by the current matching logic).
    @param preset Preset value to match against candidate filenames.
    @return `Path` to the selected `.vpk`, or `None` if `build_dir` doesn't
            exist or contains no `.vpk` files.
    """
    build_path = project_dir / build_dir
    if not build_path.is_dir():
        return None
    candidates = sorted(build_path.glob("*.vpk"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    # Prefer the candidate whose filename matches the preset/project name,
    # in case several .vpk files were touched recently.
    for p in candidates:
        if preset and preset.replace("-", "_").replace("--", "") in p.stem:
            return p
    return candidates[0]


def _deploy_vita3k(project_cfg, global_cfg, vpk_path):
    """!
    @brief Interactive menu to deploy a build to the Vita3K emulator.
    @details Offers: hot-swap `eboot.bin` + relaunch Vita3K, install the full
             `.vpk` and launch, copy only `eboot.bin` without launching, or skip.
    @param project_cfg Per-project config dict (must include `_project_dir`,
                        `titleid`, `game_name`, and optionally `build_dir`).
    @param global_cfg Global config dict (Vita3K app path, filesystem dir, logs dir).
    @param vpk_path Path to the built `.vpk`, or `None` if not found.
    """
    project_dir = Path(project_cfg["_project_dir"])
    titleid = project_cfg["titleid"]
    build_dir = project_dir / project_cfg.get("build_dir", "build")
    eboot = build_dir / "eboot.bin"

    print(f"\n{C.BOLD}{t('build_deploy.deploy_vita3k_title')}{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} {t('build_deploy.deploy_vita3k_opt1')}")
    print(f"  {C.GREEN}2){C.RESET} {t('build_deploy.deploy_vita3k_opt2')}")
    print(f"  {C.GREEN}3){C.RESET} {t('build_deploy.deploy_vita3k_opt3')}")
    print(f"  {C.GREEN}4){C.RESET} {t('build_deploy.deploy_vita3k_opt4')}")
    choice = input(t("build_deploy.option_prompt")).strip() or "1"

    vita3k_fs = Path(global_cfg.get("vita3k_fs_dir", "")) / titleid
    vita3k_app = global_cfg.get("vita3k_app", "")

    if choice == "1":
        if not eboot.exists():
            print(f"{C.RED}{t('build_deploy.eboot_not_found', eboot=eboot)}{C.RESET}")
            return
        vita3k_fs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(eboot, vita3k_fs / "eboot.bin")
        font = project_dir / "extras" / "fonts" / "DejaVuSans.ttf"
        if font.exists():
            shutil.copy2(font, vita3k_fs / "DejaVuSans.ttf")
        subprocess.run(["pkill", "-9", "-x", "Vita3K"], capture_output=True)
        time.sleep(1)
        subprocess.run(["open", "-a", "Vita3K"])
        print(f"{C.GREEN}{t('build_deploy.eboot_deployed_relaunched', vita3k_fs=vita3k_fs)}{C.RESET}")

        if tui.confirm(t("build_deploy.confirm_double_click")):
            from . import automation_mac
            time.sleep(3)
            automation_mac.bring_to_front("Vita3K")
            time.sleep(1)
            if automation_mac.double_click_first_game_row():
                print(f"{C.GREEN}{t('build_deploy.double_click_done')}{C.RESET}")
        log_path = Path(global_cfg.get("vita3k_logs_dir", "")) / f"{titleid} - [{project_cfg['game_name']}].log"
        print(f"{C.DIM}{t('build_deploy.live_log_tip', log_path=log_path)}{C.RESET}")
    elif choice == "2":
        if not (vpk_path and vpk_path.exists()):
            print(f"{C.RED}{t('build_deploy.no_vpk_to_install')}{C.RESET}")
            return
        if vita3k_app and os.access(vita3k_app, os.X_OK):
            subprocess.Popen([vita3k_app, "-B", "OpenGL", str(vpk_path)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"{C.GREEN}{t('build_deploy.vita3k_installing_vpk')}{C.RESET}")
        else:
            subprocess.run(["open", "-a", "Vita3K", str(vpk_path)])
    elif choice == "3":
        if not eboot.exists():
            print(f"{C.RED}{t('build_deploy.eboot_not_found', eboot=eboot)}{C.RESET}")
            return
        vita3k_fs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(eboot, vita3k_fs / "eboot.bin")
        print(f"{C.GREEN}{t('build_deploy.eboot_copied', vita3k_fs=vita3k_fs)}{C.RESET}")
    else:
        print(t("build_deploy.vita3k_deploy_skipped"))


def _deploy_psvita(project_cfg, global_cfg, vpk_path):
    """!
    @brief Interactive menu to deploy a build to a physical PS Vita over FTP.
    @details Offers: upload only `eboot.bin`, upload the full `.vpk` to
             `ux0:downloads/`, or skip. Actual transfer is delegated to `ftp_ops`.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (Vita IP/port, consumed by `ftp_ops`).
    @param vpk_path Path to the built `.vpk`, or `None` if not found.
    """
    from . import ftp_ops
    print(f"\n{C.BOLD}{t('build_deploy.deploy_psvita_title')}{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} {t('build_deploy.deploy_psvita_opt1')}")
    print(f"  {C.GREEN}2){C.RESET} {t('build_deploy.deploy_psvita_opt2')}")
    print(f"  {C.GREEN}3){C.RESET} {t('build_deploy.deploy_psvita_opt3')}")
    choice = input(t("build_deploy.option_prompt")).strip() or "1"
    if choice == "1":
        ftp_ops.upload_eboot(project_cfg, global_cfg)
    elif choice == "2":
        ftp_ops.upload_vpk(project_cfg, global_cfg)
    else:
        print(t("build_deploy.psvita_upload_skipped"))


def build_and_deploy_wizard(project_cfg, global_cfg):
    """!
    @brief Full interactive build+deploy flow: choose target, choose preset,
           run the build, locate the output `.vpk`, then deploy it.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    project_dir = Path(project_cfg["_project_dir"])
    build_sh_path = project_dir / "build.sh"

    target = _choose_target()
    if not target:
        print(t("build_deploy.cancelled"))
        return

    preset, extra_args = _choose_preset(build_sh_path)
    if preset is None:
        print(t("build_deploy.cancelled"))
        return

    print(f"\n{C.CYAN}{C.BOLD}================================================================{C.RESET}")
    print(f"  {t('build_deploy.building_header', game_name=project_cfg['game_name'], preset=preset, target=target)}")
    print(f"{C.CYAN}{C.BOLD}================================================================{C.RESET}\n")

    ok = _run_build(project_dir, preset, extra_args)
    if not ok:
        print(f"{C.RED}{t('build_deploy.build_failed')}{C.RESET}")
        return

    vpk_path = _find_output_vpk(project_dir, project_cfg.get("build_dir", "build"),
                                 project_cfg["project_name"], preset)
    if vpk_path:
        print(f"{C.GREEN}{t('build_deploy.build_success', vpk_path=vpk_path)}{C.RESET}")
    else:
        print(f"{C.YELLOW}{t('build_deploy.build_no_vpk_found', build_dir=project_cfg.get('build_dir', 'build'))}{C.RESET}")

    if target == "vita3k":
        _deploy_vita3k(project_cfg, global_cfg, vpk_path)
    elif target == "psvita":
        _deploy_psvita(project_cfg, global_cfg, vpk_path)
    else:
        print(f"\n{C.DIM}{t('build_deploy.deploy_later_tip')}{C.RESET}")


def deploy_only_vita3k(project_cfg, global_cfg):
    """!
    @brief Redeploy the last already-built output to Vita3K, without rebuilding.
    @details Equivalent to running the wizard and picking "copy/relaunch" when
             a fresh `eboot.bin` already exists from a previous build.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    project_dir = Path(project_cfg["_project_dir"])
    vpk_path = _find_output_vpk(project_dir, project_cfg.get("build_dir", "build"),
                                 project_cfg["project_name"], None)
    _deploy_vita3k(project_cfg, global_cfg, vpk_path)
