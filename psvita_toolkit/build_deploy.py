"""!
@file build_deploy.py
@brief Build and deploy wizard: target selection, build presets, running the
       project's `build.sh`, locating the resulting `.vpk`, and deploying to
       a physical PS Vita.

@details
Build presets are not hardcoded per game: the 4 universal ones (Debug,
Release, RelWithDebInfo, MinSizeRel) are always offered, and additional
project-specific flags are auto-discovered two ways: by scanning the active
project's `build.sh` for `"$1" = "--xxx"` conditions, and (for the
`build.sh`-less fallback path) by scanning its `CMakeLists.txt` for standard
`option(NAME "description" ON|OFF)` declarations.

See `docs/dev-notes/build_deploy.md` for the rationale behind this
auto-discovery design (vs. hardcoding engine-specific flags) and the
import-time i18n-key freezing in `UNIVERSAL_PRESETS`.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import i18n
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
    "build_deploy.no_build_sh_fallback": {
        "es": "[!] Este proyecto no tiene build.sh (port legacy) -- compilando directo con CMake en {build_dir}",
        "en": "[!] This project has no build.sh (legacy port) -- building directly with CMake in {build_dir}",
        "pt": "[!] Este projeto não tem build.sh (port legado) -- compilando diretamente com CMake em {build_dir}",
    },
    "build_deploy.staging_source": {
        "es": "[*] Copiando el código fuente a un directorio temporal (respeta .gitignore -- puede tardar si hay carpetas grandes sin ignorar)...",
        "en": "[*] Staging the source into a temp directory (honors .gitignore -- may take a while if large folders aren't ignored)...",
        "pt": "[*] Copiando o código-fonte para um diretório temporário (respeita .gitignore -- pode demorar se houver pastas grandes não ignoradas)...",
    },
    "build_deploy.cmake_configure_failed": {
        "es": "[-] cmake falló al configurar -- revisar el output de arriba.",
        "en": "[-] cmake failed to configure -- check the output above.",
        "pt": "[-] O cmake falhou ao configurar -- verifique a saída acima.",
    },
    "build_deploy.cmake_options_title": {
        "es": "[*] Opciones de CMake detectadas en CMakeLists.txt (Enter = usar el valor por defecto):",
        "en": "[*] CMake options detected in CMakeLists.txt (Enter = use the default):",
        "pt": "[*] Opções de CMake detectadas no CMakeLists.txt (Enter = usar o padrão):",
    },
    "build_deploy.cmake_option_prompt": {
        "es": "  {name} -- {desc} [{default}]: ",
        "en": "  {name} -- {desc} [{default}]: ",
        "pt": "  {name} -- {desc} [{default}]: ",
    },
    "build_deploy.outputs_copied": {
        "es": "[+] Resultados del build copiados a {build_dir}",
        "en": "[+] Build outputs copied to {build_dir}",
        "pt": "[+] Resultados do build copiados para {build_dir}",
    },
    "build_deploy.running_command": {
        "es": "[*] Ejecutando: {cmd}",
        "en": "[*] Running: {cmd}",
        "pt": "[*] Executando: {cmd}",
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
        "es": "Tip: para desplegar más tarde usá las opciones del menú principal 'Subir a PS Vita'.",
        "en": "Tip: to deploy later, use the main menu options 'Upload to PS Vita'.",
        "pt": "Dica: para implantar depois, use as opções do menu principal 'Enviar para o PS Vita'.",
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
    @return `"psvita"`, `"local"`, or `None` if cancelled/invalid input.
    @note Vita3K used to be an option here; it was removed after confirming
          (with Prince of Persia Classic) that the emulator's limitations
          make it unusable for this class of port -- see
          `docs/dev-notes/build_deploy.md`.
    """
    print(f"{C.BOLD}{t('build_deploy.choose_target_title')}{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} {t('build_deploy.target_psvita')}")
    print(f"  {C.GREEN}2){C.RESET} {t('build_deploy.target_local')}")
    print(f"  {C.RED}q){C.RESET} {t('build_deploy.cancel')}")
    choice = input(t("build_deploy.target_prompt")).strip() or "1"
    return {"1": "psvita", "2": "local"}.get(choice)


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


_CMAKE_BUILD_TYPES = {
    "debug": "Debug",
    "release": "Release",
    "relwithdebinfo": "RelWithDebInfo",
    "minsizerel": "MinSizeRel",
}


_CMAKE_OPTION_RE = re.compile(r'^\s*option\(\s*(\w+)\s+"([^"]*)"\s+(ON|OFF)\s*\)', re.MULTILINE)


def _discover_cmake_options(project_dir):
    """!
    @brief Scan a project's root `CMakeLists.txt` for standard CMake
           `option(NAME "description" ON|OFF)` declarations.
    @param project_dir Path to the project directory.
    @return List of `(name, description, default_bool)` tuples, in file order;
            `[]` if there's no `CMakeLists.txt` or it declares no options.
    @note This is a generic, project-agnostic mechanism -- any project using
          the standard CMake `option()` idiom gets its toggles surfaced here
          automatically, with no per-project/per-engine special-casing. See
          `docs/dev-notes/build_deploy.md`.
    """
    cmake_path = project_dir / "CMakeLists.txt"
    if not cmake_path.exists():
        return []
    text = cmake_path.read_text(errors="ignore")
    return [(name, desc, default == "ON") for name, desc, default in _CMAKE_OPTION_RE.findall(text)]


def _prompt_cmake_options(project_dir):
    """!
    @brief Interactively let the user toggle each CMake option discovered by
           `_discover_cmake_options()`, one at a time (Enter keeps the
           CMakeLists.txt-declared default).
    @param project_dir Path to the project directory.
    @return List of `-D<NAME>=ON`/`-D<NAME>=OFF` strings, one per discovered
            option, ready to append to a `cmake` invocation.
    """
    options = _discover_cmake_options(project_dir)
    if not options:
        return []
    print(f"\n{C.BOLD}{t('build_deploy.cmake_options_title')}{C.RESET}")
    flags = []
    for name, desc, default in options:
        default_label = "ON" if default else "OFF"
        raw = input(t("build_deploy.cmake_option_prompt", name=name, desc=desc, default=default_label)).strip().upper()
        value = raw if raw in ("ON", "OFF") else default_label
        flags.append(f"-D{name}={value}")
    return flags


def _vitasdk_env(global_cfg):
    """!
    @brief Build a subprocess environment with `VITASDK` and `$VITASDK/bin`
           set up, based on `global_cfg`.
    @param global_cfg Global config dict; reads `vitasdk`.
    @return A copy of `os.environ` with `VITASDK` set and `$VITASDK/bin`
            prepended to `PATH` (only if not already there).
    @note Without this, VITASDK toolchain executables invoked by name during
          the build (`vita-libs-gen`, `vita-elf-create`, `vita-make-fself`,
          `vita-mksfoex`, `vita-pack-vpk`, ...) aren't found on `PATH`. A
          project's own `build.sh` normally does this itself -- this fallback
          exists for projects that don't have one. See
          `docs/dev-notes/build_deploy.md`.
    """
    env = os.environ.copy()
    vitasdk = (global_cfg or {}).get("vitasdk", "")
    if vitasdk:
        env.setdefault("VITASDK", vitasdk)
        vitasdk_bin = os.path.join(vitasdk, "bin")
        if os.path.isdir(vitasdk_bin) and vitasdk_bin not in env.get("PATH", ""):
            env["PATH"] = f"{vitasdk_bin}:{env.get('PATH', '')}"
    return env


def _stage_in_tmp(project_dir, build_dir):
    """!
    @brief Copy `project_dir`'s source into a space-free `/tmp` directory,
           and create a matching space-free `/tmp` build directory.
    @param project_dir Path to the real project directory (may contain spaces).
    @param build_dir Build output directory name, relative to `project_dir`
           -- excluded from the copy (rebuilt fresh in `/tmp`).
    @return `(tmp_root, tmp_src, tmp_build)` -- `tmp_root` is the parent to
            clean up afterward, `tmp_src`/`tmp_build` are its two subdirs.
    @note `vita-pack-vpk` (part of the VITASDK toolchain) cannot handle a
          working directory whose absolute path contains a space -- see
          `docs/dev-notes/build_deploy.md`. Building entirely under `/tmp`
          sidesteps this; only the final `.vpk`/`eboot.bin`/ELF get copied
          back into the real (possibly space-containing) project directory
          afterward, via `_copy_build_outputs()`.
    @note Honors the project's own `.gitignore` (via `rsync --filter=":-
          .gitignore"`) in addition to `.git`/dotfiles/the build dir --
          without it, this copies every gitignored asset/backup/decompiled
          folder too (extracted game data, `.apk`/`.obb`, decompile output,
          old VPK backups), which for an adopted legacy project can be
          gigabytes and makes the build look "stuck" while it's actually
          just copying data cmake never needed in the first place. See
          `docs/dev-notes/build_deploy.md`.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="psvita-build-"))
    tmp_src = tmp_root / "src"
    tmp_build = tmp_root / "build"
    tmp_src.mkdir()
    tmp_build.mkdir()
    print(t("build_deploy.staging_source"))
    subprocess.run([
        "rsync", "-a",
        "--filter", ":- .gitignore",
        "--exclude", ".git", "--exclude", ".*", "--exclude", str(build_dir),
        f"{project_dir}/", f"{tmp_src}/",
    ], check=True)
    return tmp_root, tmp_src, tmp_build


def _is_elf(path):
    """!
    @brief Check whether a file is an ELF binary, by its magic bytes.
    @param path File to check.
    @return `True` if `path` starts with the ELF magic number.
    """
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def _copy_build_outputs(tmp_build, build_path):
    """!
    @brief Copy build outputs from a `/tmp` build directory back into the
           project's real `build_dir`.
    @param tmp_build The `/tmp` directory the build actually ran in.
    @param build_path Real (possibly space-containing) destination directory
           (created if missing).
    @return List of copied file names.
    @note Also copies any bare ELF executable found at `tmp_build`'s top
          level (e.g. the raw linked binary before `.velf`/`.self`
          conversion, named after the CMake target) as `<name>.elf` --
          `crash_analyzer.py` needs this to symbolicate crash dumps, and it
          would otherwise be lost once `/tmp` is cleaned up.
    """
    build_path.mkdir(parents=True, exist_ok=True)
    copied = []
    for pattern in ("*.vpk", "eboot.bin", "*.velf", "*.self"):
        for f in tmp_build.glob(pattern):
            shutil.copy2(f, build_path / f.name)
            copied.append(f.name)
    for f in tmp_build.iterdir():
        if f.is_file() and f.suffix == "" and _is_elf(f):
            dest_name = f"{f.name}.elf"
            shutil.copy2(f, build_path / dest_name)
            copied.append(dest_name)
    return copied


def _run_cmake_direct(project_dir, build_dir, preset, extra_args, global_cfg):
    """!
    @brief Fallback build path for projects with no `build.sh`: stage the
           source under `/tmp`, run `cmake` then `make` there, and copy the
           resulting `.vpk`/`eboot.bin`/ELF back into the real `build_dir`.
    @param project_dir Path to the project directory.
    @param build_dir Build output directory, relative to `project_dir`.
    @param preset Preset value; mapped to `-DCMAKE_BUILD_TYPE=...` unless
           `"custom"` or falsy.
    @param extra_args Extra arguments appended to the `cmake` invocation.
    @param global_cfg Global config dict, used to set up the VITASDK
           toolchain's environment (see `_vitasdk_env()`).
    @return `True` if both `cmake` and `make` exited with code 0.
    @note Legacy ports adopted from before this toolkit (created by hand with
          `cmake`/`make` directly, never from `soloader-boilerplate`) have no
          `build.sh` at all -- see `docs/dev-notes/build_deploy.md`. Every
          call reconfigures from scratch in a fresh `/tmp` directory (no
          CMake cache reuse) -- slower than an in-place build, but avoids the
          space-in-path bug regardless of where `project_dir` lives on disk.
    """
    build_path = project_dir / build_dir
    print(f"{C.YELLOW}{t('build_deploy.no_build_sh_fallback', build_dir=build_path)}{C.RESET}")

    env = _vitasdk_env(global_cfg)
    extra_cmake_opts = _prompt_cmake_options(project_dir)

    tmp_root, tmp_src, tmp_build = _stage_in_tmp(project_dir, build_dir)
    try:
        cmake_args = ["cmake", str(tmp_src)]
        if preset and preset != "custom":
            cmake_args.append(f"-DCMAKE_BUILD_TYPE={_CMAKE_BUILD_TYPES.get(preset, 'Release')}")
        cmake_args.extend(extra_args or [])
        cmake_args.extend(extra_cmake_opts)
        print(f"{t('build_deploy.running_command', cmd=' '.join(cmake_args))}\n")
        r = subprocess.run(cmake_args, cwd=tmp_build, env=env)
        if r.returncode != 0:
            print(f"{C.RED}{t('build_deploy.cmake_configure_failed')}{C.RESET}")
            return False

        jobs = subprocess.run(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True).stdout.strip() or "4"
        make_args = ["make", f"-j{jobs}"]
        print(f"\n{t('build_deploy.running_command', cmd=' '.join(make_args))}\n")
        r = subprocess.run(make_args, cwd=tmp_build, env=env)
        if r.returncode != 0:
            return False

        if _copy_build_outputs(tmp_build, build_path):
            print(f"{C.GREEN}{t('build_deploy.outputs_copied', build_dir=build_path)}{C.RESET}")
        return True
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _run_build(project_dir, preset, extra_args, build_dir="build", global_cfg=None):
    """!
    @brief Run the project's `build.sh` with the chosen preset and extra args,
           falling back to a direct `cmake`+`make` invocation if the project
           has no `build.sh`.
    @param project_dir Path to the project directory.
    @param preset Preset value to pass as the first argument to `build.sh`
                  (omitted if `"custom"` or falsy).
    @param extra_args Extra list of arguments to append after the preset.
    @param build_dir Build output directory, relative to `project_dir` --
           only used by the `build.sh`-less fallback path.
    @param global_cfg Global config dict -- only used by the `build.sh`-less
           fallback path, to set up the VITASDK toolchain's environment.
    @return `True` if the build exited with code 0.
    """
    build_sh = project_dir / "build.sh"
    if not build_sh.exists():
        return _run_cmake_direct(project_dir, build_dir, preset, extra_args, global_cfg)
    if not os.access(build_sh, os.X_OK):
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

    ok = _run_build(project_dir, preset, extra_args, project_cfg.get("build_dir", "build"), global_cfg)
    if not ok:
        print(f"{C.RED}{t('build_deploy.build_failed')}{C.RESET}")
        return

    vpk_path = _find_output_vpk(project_dir, project_cfg.get("build_dir", "build"),
                                 project_cfg["project_name"], preset)
    if vpk_path:
        print(f"{C.GREEN}{t('build_deploy.build_success', vpk_path=vpk_path)}{C.RESET}")
    else:
        print(f"{C.YELLOW}{t('build_deploy.build_no_vpk_found', build_dir=project_cfg.get('build_dir', 'build'))}{C.RESET}")

    if target == "psvita":
        _deploy_psvita(project_cfg, global_cfg, vpk_path)
    else:
        print(f"\n{C.DIM}{t('build_deploy.deploy_later_tip')}{C.RESET}")
