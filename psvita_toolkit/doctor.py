"""!
@file doctor.py
@brief Environment diagnostics: `psvita-toolkit doctor` -- checks every host
       dependency the toolkit relies on (VITASDK toolchain, Docker, jadx,
       CMake/Ninja, Python packages, vita-parse-core) and reports exactly
       what's missing and how to fix it.

@details
Runs standalone (no project needs to be selected) so it's useful both as the
very first thing a new machine runs and as a quick sanity check when some
other command fails with a confusing error. Every check is independent and
never raises -- a missing tool is reported as `FAIL`/`WARN`, not an exception,
so one broken check never hides the rest of the report.

See `docs/dev-notes/doctor.md` for why checks are OK/WARN/FAIL (not just
pass/fail), and why this doesn't hard-depend on any other module.
"""

import importlib
import os
import shutil
import subprocess
from pathlib import Path

from . import i18n
from .i18n import t
from . import tui
from .tui import C

STRINGS = {
    "doctor.title": {
        "es": "Doctor -- Diagnóstico del entorno",
        "en": "Doctor -- Environment diagnostics",
        "pt": "Doctor -- Diagnóstico do ambiente",
    },
    "doctor.section.config": {
        "es": "Configuración global",
        "en": "Global configuration",
        "pt": "Configuração global",
    },
    "doctor.section.toolchain": {
        "es": "Toolchain VITASDK",
        "en": "VITASDK toolchain",
        "pt": "Toolchain VITASDK",
    },
    "doctor.section.build_tools": {
        "es": "Herramientas de build",
        "en": "Build tools",
        "pt": "Ferramentas de build",
    },
    "doctor.section.docker": {
        "es": "Docker (decompilación de .so)",
        "en": "Docker (.so decompilation)",
        "pt": "Docker (decompilação de .so)",
    },
    "doctor.section.decompilers": {
        "es": "Decompiladores",
        "en": "Decompilers",
        "pt": "Decompiladores",
    },
    "doctor.section.python": {
        "es": "Paquetes de Python",
        "en": "Python packages",
        "pt": "Pacotes Python",
    },
    "doctor.summary.ok": {
        "es": "[+] Todo en orden -- {ok} de {total} chequeos OK.",
        "en": "[+] Everything checks out -- {ok}/{total} checks OK.",
        "pt": "[+] Tudo certo -- {ok}/{total} verificações OK.",
    },
    "doctor.summary.warn": {
        "es": "[!] {warn} advertencia(s) -- funcional, pero revisa lo marcado [WARN].",
        "en": "[!] {warn} warning(s) -- functional, but review anything marked [WARN].",
        "pt": "[!] {warn} aviso(s) -- funcional, mas revise o que está marcado [WARN].",
    },
    "doctor.summary.fail": {
        "es": "[-] {fail} chequeo(s) fallido(s) -- estas funciones no van a andar hasta corregirlo.",
        "en": "[-] {fail} failing check(s) -- those features won't work until fixed.",
        "pt": "[-] {fail} verificação(ões) com falha -- esses recursos não vão funcionar até corrigir.",
    },
    "doctor.hint.config_key_missing": {
        "es": "no configurado -- corregir desde Configuración global",
        "en": "not configured -- fix it from Global settings",
        "pt": "não configurado -- corrija em Configuração global",
    },
    "doctor.hint.config_path_missing": {
        "es": "configurado en {path}, pero no existe en disco",
        "en": "configured as {path}, but it doesn't exist on disk",
        "pt": "configurado como {path}, mas não existe no disco",
    },
    "doctor.hint.vita_parse_core_optional": {
        "es": "opcional -- solo hace falta para 'Analizar un crash dump'",
        "en": "optional -- only needed for 'Analyze a crash dump'",
        "pt": "opcional -- só é necessário para 'Analisar um crash dump'",
    },
    "doctor.hint.binary_missing_vitasdk": {
        "es": "no encontrado en {vitasdk_bin} ni en PATH -- revisa la instalación de VITASDK",
        "en": "not found in {vitasdk_bin} or PATH -- check your VITASDK install",
        "pt": "não encontrado em {vitasdk_bin} nem no PATH -- verifique a instalação do VITASDK",
    },
    "doctor.hint.binary_missing_brew": {
        "es": "no encontrado -- instalar con: brew install {formula}",
        "en": "not found -- install with: brew install {formula}",
        "pt": "não encontrado -- instale com: brew install {formula}",
    },
    "doctor.hint.psp2cgc_missing": {
        "es": "no encontrado -- necesario para validar shaders localmente (ver mejora 9 del plan)",
        "en": "not found -- needed to validate shaders locally (see plan item 9)",
        "pt": "não encontrado -- necessário para validar shaders localmente (ver item 9 do plano)",
    },
    "doctor.hint.docker_not_installed": {
        "es": "no encontrado -- instalar Docker Desktop",
        "en": "not found -- install Docker Desktop",
        "pt": "não encontrado -- instale o Docker Desktop",
    },
    "doctor.hint.docker_not_running": {
        "es": "instalado, pero el daemon no responde -- abrí Docker Desktop",
        "en": "installed, but the daemon isn't responding -- open Docker Desktop",
        "pt": "instalado, mas o daemon não responde -- abra o Docker Desktop",
    },
    "doctor.hint.docker_image_missing": {
        "es": "falta la imagen -- docker pull devrvk/so-decompiler",
        "en": "image missing -- docker pull devrvk/so-decompiler",
        "pt": "falta a imagem -- docker pull devrvk/so-decompiler",
    },
    "doctor.hint.jadx_missing": {
        "es": "no encontrado -- brew install jadx",
        "en": "not found -- brew install jadx",
        "pt": "não encontrado -- brew install jadx",
    },
    "doctor.hint.pip_missing": {
        "es": "no instalado -- pip install {package}",
        "en": "not installed -- pip install {package}",
        "pt": "não instalado -- pip install {package}",
    },
    "doctor.hint.pip_missing_optional": {
        "es": "no instalado (opcional) -- pip install {package}",
        "en": "not installed (optional) -- pip install {package}",
        "pt": "não instalado (opcional) -- pip install {package}",
    },
    "doctor.hint.ninja_optional": {
        "es": "no encontrado (opcional) -- brew install ninja para builds en paralelo más rápidos",
        "en": "not found (optional) -- brew install ninja for faster parallel builds",
        "pt": "não encontrado (opcional) -- brew install ninja para builds paralelos mais rápidos",
    },
    "doctor.detail.found_at": {
        "es": "encontrado en {path}",
        "en": "found at {path}",
        "pt": "encontrado em {path}",
    },
    "doctor.detail.docker_running": {
        "es": "daemon activo",
        "en": "daemon running",
        "pt": "daemon ativo",
    },
    "doctor.detail.image_present": {
        "es": "imagen presente",
        "en": "image present",
        "pt": "imagem presente",
    },
}
i18n.register(STRINGS)

OK, WARN, FAIL = "ok", "warn", "fail"

_STATUS_COLOR = {OK: C.GREEN, WARN: C.YELLOW, FAIL: C.RED}
_STATUS_LABEL = {OK: "OK", WARN: "WARN", FAIL: "FAIL"}


def _check(name, status, detail=""):
    """!
    @brief Build one check result entry.
    @param name Human-readable name of the thing being checked (already translated).
    @param status One of `OK`/`WARN`/`FAIL`.
    @param detail Extra detail/hint text shown next to the status (already translated).
    @return `(name, status, detail)` tuple, as consumed by `print_report()`.
    """
    return (name, status, detail)


def _vitasdk_bin(global_cfg):
    """!
    @brief Resolve `$VITASDK/bin` from the global config, without mutating `os.environ`.
    @param global_cfg Global config dict; reads `vitasdk`.
    @return Path string to `$VITASDK/bin`, or `""` if `vitasdk` isn't configured.
    """
    vitasdk = (global_cfg or {}).get("vitasdk", "")
    return os.path.join(vitasdk, "bin") if vitasdk else ""


def _find_binary(binary_name, extra_dir=""):
    """!
    @brief Look for `binary_name` in `extra_dir` first, then on `PATH`.
    @param binary_name Executable name (no path), e.g. `"arm-vita-eabi-gcc"`.
    @param extra_dir Directory to check before falling back to `PATH` (e.g. `$VITASDK/bin`).
    @return Full path to the binary if found, `""` otherwise.
    """
    if extra_dir:
        candidate = Path(extra_dir) / binary_name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(binary_name) or ""


def _check_config_paths(global_cfg):
    """!
    @brief Verify every required global-config path is set and exists on disk.
    @param global_cfg Global config dict.
    @return list of check tuples.
    """
    checks = []
    from . import config as cfgmod

    for key, desc_key in cfgmod.REQUIRED_GLOBAL_KEYS.items():
        label = t(desc_key)
        value = (global_cfg or {}).get(key, "")
        if not value:
            checks.append(_check(label, FAIL, t("doctor.hint.config_key_missing")))
        elif not os.path.isdir(value):
            checks.append(_check(label, FAIL, t("doctor.hint.config_path_missing", path=value)))
        else:
            checks.append(_check(label, OK, t("doctor.detail.found_at", path=value)))

    vpc_dir = (global_cfg or {}).get("vita_parse_core_dir", "")
    if vpc_dir and os.path.isdir(vpc_dir):
        checks.append(_check("vita-parse-core", OK, t("doctor.detail.found_at", path=vpc_dir)))
    else:
        checks.append(_check("vita-parse-core", WARN, t("doctor.hint.vita_parse_core_optional")))

    return checks


_TOOLCHAIN_BINARIES = (
    "arm-vita-eabi-gcc",
    "arm-vita-eabi-g++",
    "arm-vita-eabi-objdump",
    "arm-vita-eabi-c++filt",
    "arm-vita-eabi-addr2line",
    "arm-vita-eabi-strip",
    "vita-mksfoex",
    "vita-make-fself",
    "vita-elf-create",
    "vita-libs-gen",
    "vita-pack-vpk",
)

_SHADER_COMPILER_CANDIDATES = ("psp2cgc", "cgc")


def _check_toolchain(global_cfg):
    """!
    @brief Verify every VITASDK toolchain binary the toolkit shells out to is reachable.
    @param global_cfg Global config dict; reads `vitasdk`.
    @return list of check tuples.
    """
    checks = []
    vitasdk_bin = _vitasdk_bin(global_cfg)
    for name in _TOOLCHAIN_BINARIES:
        path = _find_binary(name, vitasdk_bin)
        if path:
            checks.append(_check(name, OK, t("doctor.detail.found_at", path=path)))
        else:
            checks.append(_check(
                name, FAIL,
                t("doctor.hint.binary_missing_vitasdk", vitasdk_bin=vitasdk_bin or "$VITASDK/bin"),
            ))

    shader_compiler = None
    for name in _SHADER_COMPILER_CANDIDATES:
        path = _find_binary(name, vitasdk_bin)
        if path:
            shader_compiler = (name, path)
            break
    if shader_compiler:
        checks.append(_check(shader_compiler[0], OK, t("doctor.detail.found_at", path=shader_compiler[1])))
    else:
        checks.append(_check("psp2cgc/cgc", WARN, t("doctor.hint.psp2cgc_missing")))

    return checks


def _check_build_tools():
    """!
    @brief Verify the generators the universal build system can target.
    @return list of check tuples.
    """
    checks = []
    cmake_path = shutil.which("cmake")
    checks.append(_check(
        "cmake", OK if cmake_path else FAIL,
        t("doctor.detail.found_at", path=cmake_path) if cmake_path
        else t("doctor.hint.binary_missing_brew", formula="cmake"),
    ))
    ninja_path = shutil.which("ninja")
    checks.append(_check(
        "ninja", OK if ninja_path else WARN,
        t("doctor.detail.found_at", path=ninja_path) if ninja_path else t("doctor.hint.ninja_optional"),
    ))
    make_path = shutil.which("make")
    checks.append(_check(
        "make", OK if make_path else WARN,
        t("doctor.detail.found_at", path=make_path) if make_path
        else t("doctor.hint.binary_missing_brew", formula="make"),
    ))
    return checks


def _docker_daemon_running():
    """!
    @brief Check whether the Docker daemon responds, without depending on any other module.
    @return `True` if `docker info` succeeds within a short timeout.
    """
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check_docker():
    """!
    @brief Verify Docker is installed, its daemon is up, and the
           `devrvk/so-decompiler` image used by `init_port.py`/`utils.py` is present.
    @return list of check tuples.
    """
    docker_path = shutil.which("docker")
    if not docker_path:
        return [_check("docker", FAIL, t("doctor.hint.docker_not_installed"))]

    checks = [_check("docker", OK, t("doctor.detail.found_at", path=docker_path))]
    if not _docker_daemon_running():
        checks.append(_check("docker daemon", FAIL, t("doctor.hint.docker_not_running")))
        return checks

    checks.append(_check("docker daemon", OK, t("doctor.detail.docker_running")))
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", "devrvk/so-decompiler"],
            capture_output=True, text=True, timeout=10,
        )
        has_image = r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        has_image = False
    checks.append(_check(
        "devrvk/so-decompiler", OK if has_image else WARN,
        t("doctor.detail.image_present") if has_image else t("doctor.hint.docker_image_missing"),
    ))
    return checks


def _check_jadx():
    """!
    @brief Verify `jadx` (APK Java decompiler) is reachable.
    @return list with a single check tuple.
    """
    path = shutil.which("jadx")
    if path:
        return [_check("jadx", OK, t("doctor.detail.found_at", path=path))]
    return [_check("jadx", FAIL, t("doctor.hint.jadx_missing"))]


_REQUIRED_PY_PACKAGES = (("Pillow", "PIL"),)
_OPTIONAL_PY_PACKAGES = (("deep-translator", "deep_translator"),)


def _check_python_packages():
    """!
    @brief Verify the Python packages the toolkit imports at runtime are installed.
    @return list of check tuples.
    """
    checks = []
    for package, module_name in _REQUIRED_PY_PACKAGES:
        try:
            importlib.import_module(module_name)
            checks.append(_check(package, OK, ""))
        except ImportError:
            checks.append(_check(package, FAIL, t("doctor.hint.pip_missing", package=package)))
    for package, module_name in _OPTIONAL_PY_PACKAGES:
        try:
            importlib.import_module(module_name)
            checks.append(_check(package, OK, ""))
        except ImportError:
            checks.append(_check(package, WARN, t("doctor.hint.pip_missing_optional", package=package)))
    return checks


def run_checks(global_cfg):
    """!
    @brief Run every diagnostic check and group the results by section.
    @param global_cfg Global config dict (as returned by `config.ensure_global_config()`).
    @return list of `(section_title, [check, ...])` tuples, in report order.
    """
    return [
        (t("doctor.section.config"), _check_config_paths(global_cfg)),
        (t("doctor.section.toolchain"), _check_toolchain(global_cfg)),
        (t("doctor.section.build_tools"), _check_build_tools()),
        (t("doctor.section.docker"), _check_docker()),
        (t("doctor.section.decompilers"), _check_jadx()),
        (t("doctor.section.python"), _check_python_packages()),
    ]


def print_report(sections, use_color=True):
    """!
    @brief Print a formatted, colorized report of every check, grouped by section.
    @param sections Result of `run_checks()`.
    @param use_color Whether to emit ANSI color codes (disabled for `--plain`/non-tty CLI output).
    @return `(ok_count, warn_count, fail_count)` totals across all sections.
    """
    ok_count = warn_count = fail_count = 0
    for title, checks in sections:
        header = f"{C.BOLD}{C.CYAN}{title}{C.RESET}" if use_color else title
        print(f"\n{header}")
        for name, status, detail in checks:
            if status == OK:
                ok_count += 1
            elif status == WARN:
                warn_count += 1
            else:
                fail_count += 1
            if use_color:
                color = _STATUS_COLOR[status]
                line = f"  [{color}{_STATUS_LABEL[status]}{C.RESET}] {C.BOLD}{name}{C.RESET}"
            else:
                line = f"  [{_STATUS_LABEL[status]}] {name}"
            if detail:
                line += f" -- {detail}"
            print(line)

    total = ok_count + warn_count + fail_count
    print()
    if fail_count:
        msg = t("doctor.summary.fail", fail=fail_count)
        print(f"{C.RED}{C.BOLD}{msg}{C.RESET}" if use_color else msg)
    if warn_count:
        msg = t("doctor.summary.warn", warn=warn_count)
        print(f"{C.YELLOW}{msg}{C.RESET}" if use_color else msg)
    if not fail_count and not warn_count:
        msg = t("doctor.summary.ok", ok=ok_count, total=total)
        print(f"{C.GREEN}{C.BOLD}{msg}{C.RESET}" if use_color else msg)

    return ok_count, warn_count, fail_count


def run_doctor(global_cfg, use_color=True):
    """!
    @brief Entry point shared by the TUI menu item and the `doctor` CLI subcommand.
    @param global_cfg Global config dict.
    @param use_color Passed through to `print_report()`.
    @return Process-style exit code: `0` if no check failed, `1` if at least one `FAIL`.
    """
    sections = run_checks(global_cfg)
    _, _, fail_count = print_report(sections, use_color=use_color)
    return 1 if fail_count else 0


def doctor_menu(global_cfg):
    """!
    @brief TUI-facing wrapper: clears the screen, prints the banner, runs the report.
    @param global_cfg Global config dict.
    """
    tui.clear()
    tui.print_banner(t("doctor.title"))
    run_doctor(global_cfg, use_color=True)
    tui.pause()
