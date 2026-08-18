"""!
@file config.py
@brief Persistent configuration for the toolkit: global settings and per-project settings.

@details
Two tiers:
  - Global config (`~/.psvita-toolkit/config.json`): paths that don't depend on the active
    port (`BASE_DIR`, `soloader-boilerplate`, Claude Code skills, `VITASDK`, `Vita3K.app`).
    Asked once.
  - Per-project config (`<port_dir>/.psvita-toolkit.json`): per-port data (name, slug,
    TITLEID, test Vita IP, etc). Created when initializing a new port, or auto-detected
    ("adopted") the first time an existing port without this file is opened.

See `docs/dev-notes/config.md` for the rationale behind the two-tier split, the language
bootstrap ordering, and the `soloader-boilerplate` exclusion in project discovery.
"""

import json
import os
import re
from pathlib import Path

from . import i18n
from .i18n import t

GLOBAL_CONFIG_DIR = Path.home() / ".psvita-toolkit"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.json"
PROJECT_CONFIG_FILENAME = ".psvita-toolkit.json"

DEFAULT_VITA_PORT = 1337

STRINGS = {
    "config.first_run_title": {
        "es": "🛠️  Primera vez -- configuración inicial del toolkit",
        "en": "🛠️  First run -- initial toolkit setup",
        "pt": "🛠️  Primeira vez -- configuração inicial da ferramenta",
    },
    "config.first_run_saved_at": {
        "es": "Esto se pregunta una sola vez y se guarda en {path}",
        "en": "This is only asked once and saved to {path}",
        "pt": "Isso é perguntado uma única vez e salvo em {path}",
    },
    "config.required_value": {
        "es": "Este valor es obligatorio.",
        "en": "This value is required.",
        "pt": "Este valor é obrigatório.",
    },
    "config.dir_not_exist_confirm": {
        "es": "'{path}' no existe todavía -- ¿continuar de todas formas?",
        "en": "'{path}' doesn't exist yet -- continue anyway?",
        "pt": "'{path}' ainda não existe -- continuar mesmo assim?",
    },
    "config.required.base_dir": {
        "es": "Carpeta base donde viven todos tus ports (ej. /Volumes/Seagate/PSVITA Develop)",
        "en": "Base folder where all your ports live (e.g. /Volumes/Seagate/PSVITA Develop)",
        "pt": "Pasta base onde ficam todos os seus ports (ex. /Volumes/Seagate/PSVITA Develop)",
    },
    "config.required.boilerplate_dir": {
        "es": "Ruta a soloader-boilerplate (scaffold usado para ports nuevos)",
        "en": "Path to soloader-boilerplate (scaffold used for new ports)",
        "pt": "Caminho para soloader-boilerplate (scaffold usado para novos ports)",
    },
    "config.required.skills_source": {
        "es": "Carpeta de skills de Claude Code a copiar en cada port nuevo",
        "en": "Claude Code skills folder to copy into each new port",
        "pt": "Pasta de skills do Claude Code para copiar em cada novo port",
    },
    "config.required.vitasdk": {
        "es": "Ruta a VITASDK",
        "en": "Path to VITASDK",
        "pt": "Caminho para o VITASDK",
    },
    "config.language_prompt": {
        "es": "Selecciona idioma / Select language / Selecione idioma:",
        "en": "Selecciona idioma / Select language / Selecione idioma:",
        "pt": "Selecciona idioma / Select language / Selecione idioma:",
    },
    "config.language_choice": {
        "es": "Idioma [1]: ",
        "en": "Idioma [1]: ",
        "pt": "Idioma [1]: ",
    },
    "config.language_saved": {
        "es": "[+] Idioma guardado: {name}. Podés cambiarlo después desde Configuración global.",
        "en": "[+] Language saved: {name}. You can change it later from Global settings.",
        "pt": "[+] Idioma salvo: {name}. Você pode alterá-lo depois em Configuração global.",
    },
}
i18n.register(STRINGS)


def _expand(p):
    """!
    @brief Normalize a user-typed path: strip surrounding quotes, unescape
           backslash-escaped spaces, expand `~` and environment variables.
    @param p Raw path string as typed/pasted by the user.
    @return Normalized absolute-or-relative path string, or `p` unchanged if falsy.
    """
    if not p:
        return p
    p = p.strip()
    if len(p) >= 2 and ((p[0] == p[-1] == '"') or (p[0] == p[-1] == "'")):
        p = p[1:-1]
    p = p.replace("\\ ", " ")
    return str(Path(os.path.expanduser(os.path.expandvars(p))))


# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------

def load_global_config():
    """!
    @brief Load the global config JSON, if it exists.
    @return dict with the saved config, or `{}` if missing/unreadable.
    """
    if not GLOBAL_CONFIG_PATH.exists():
        return {}
    try:
        with open(GLOBAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_global_config(cfg):
    """!
    @brief Persist the global config dict to `~/.psvita-toolkit/config.json`.
    @param cfg Config dict to write (creates the parent directory if needed).
    """
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(GLOBAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


REQUIRED_GLOBAL_KEYS = {
    "base_dir": "config.required.base_dir",
    "boilerplate_dir": "config.required.boilerplate_dir",
    "skills_source": "config.required.skills_source",
    "vitasdk": "config.required.vitasdk",
}

OPTIONAL_GLOBAL_KEYS = {
    "vita3k_app": "/Applications/Vita3K.app/Contents/MacOS/Vita3K",
    "vita3k_fs_dir": str(Path.home() / "Library/Application Support/Vita3K/Vita3K/fs/ux0/app"),
    "vita3k_logs_dir": str(Path.home() / "Library/Application Support/Vita3K/Vita3K/logs"),
    "vita_parse_core_dir": str(Path.home() / "vita-tools/vita-parse-core"),
    "vpn_disconnect_cmd": "",
    "recent_projects": [],
    "last_project": "",
}


def prompt_language():
    """!
    @brief Tri-lingual language picker shown before a language is active.
    @note Must not call `t()` -- there is no active language yet, so the prompt
          text is hardcoded in all 3 supported languages simultaneously.
    @return Language code chosen by the user (one of `i18n.SUPPORTED_LANGUAGES`).
    """
    print(f"\n{t('config.language_prompt')}")
    for i, code in enumerate(i18n.SUPPORTED_LANGUAGES, 1):
        print(f"  {i}. {i18n.LANGUAGE_NAMES[code]}")
    while True:
        choice = input(f"\n{t('config.language_choice')}").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(i18n.SUPPORTED_LANGUAGES):
            return i18n.SUPPORTED_LANGUAGES[int(choice) - 1]


def ensure_language(cfg=None):
    """!
    @brief Load and activate the saved UI language, or prompt for one on first run.
    @note Must run before `ensure_global_config()` so every subsequent prompt is
          already shown in the chosen language.
    @param cfg Optional pre-loaded global config dict; loaded from disk if omitted.
    @return The (possibly updated) global config dict, with `cfg["language"]` set.
    """
    if cfg is None:
        cfg = load_global_config()
    lang = cfg.get("language")
    if lang in i18n.SUPPORTED_LANGUAGES:
        i18n.set_language(lang)
        return cfg
    lang = prompt_language()
    i18n.set_language(lang)
    cfg["language"] = lang
    save_global_config(cfg)
    print(t("config.language_saved", name=i18n.LANGUAGE_NAMES[lang]))
    return cfg


def ensure_global_config(tui):
    """!
    @brief Load the global config, prompting interactively for any required
           key still missing (asked once, then persisted).
    @param tui The `tui` module (passed in, not imported, to avoid a circular import).
    @return The complete global config dict.
    """
    cfg = ensure_language(load_global_config())
    changed = False

    if cfg:
        missing = [k for k in REQUIRED_GLOBAL_KEYS if not cfg.get(k)]
    else:
        missing = list(REQUIRED_GLOBAL_KEYS)

    if missing:
        tui.clear()
        print(f"{tui.C.CYAN}{tui.C.BOLD}================================================================{tui.C.RESET}")
        print(f"{tui.C.CYAN}{tui.C.BOLD}  {t('config.first_run_title')}{tui.C.RESET}")
        print(f"{tui.C.CYAN}{tui.C.BOLD}================================================================{tui.C.RESET}")
        print(f"{tui.C.DIM}{t('config.first_run_saved_at', path=GLOBAL_CONFIG_PATH)}{tui.C.RESET}\n")

        for key in missing:
            desc = t(REQUIRED_GLOBAL_KEYS[key])
            default = cfg.get(key, "")
            while True:
                prompt = f"{tui.C.BOLD}{desc}{tui.C.RESET}"
                if default:
                    prompt += f" [{default}]"
                prompt += ":\n> "
                raw = input(prompt).strip() or default
                raw = _expand(raw)
                if not raw:
                    print(f"{tui.C.RED}{t('config.required_value')}{tui.C.RESET}")
                    continue
                if key in ("boilerplate_dir", "skills_source", "vitasdk") and not os.path.isdir(raw):
                    ok = tui.confirm(t("config.dir_not_exist_confirm", path=raw), default=False)
                    if not ok:
                        continue
                cfg[key] = raw
                break
        changed = True

    for key, default in OPTIONAL_GLOBAL_KEYS.items():
        if key not in cfg:
            cfg[key] = default
            changed = True

    if changed:
        save_global_config(cfg)
    return cfg


def update_global_config(**kwargs):
    """!
    @brief Merge `kwargs` into the saved global config and persist it.
    @return The updated global config dict.
    """
    cfg = load_global_config()
    cfg.update(kwargs)
    save_global_config(cfg)
    return cfg


def remember_project(project_dir):
    """!
    @brief Record `project_dir` as the most recently used project.
    @param project_dir Path to the project directory to remember.
    """
    cfg = load_global_config()
    recents = cfg.get("recent_projects", [])
    project_dir = str(project_dir)
    recents = [p for p in recents if p != project_dir]
    recents.insert(0, project_dir)
    cfg["recent_projects"] = recents[:15]
    cfg["last_project"] = project_dir
    save_global_config(cfg)


# ---------------------------------------------------------------------------
# Per-project config
# ---------------------------------------------------------------------------

def project_config_path(project_dir):
    """!
    @brief Path to a project's `.psvita-toolkit.json`.
    @param project_dir Path to the project directory.
    @return `Path` to the project's config file (may not exist yet).
    """
    return Path(project_dir) / PROJECT_CONFIG_FILENAME


def has_project_config(project_dir):
    """!
    @brief Check whether `project_dir` already has a `.psvita-toolkit.json`.
    @param project_dir Path to the project directory.
    @return `True` if the project config file exists.
    """
    return project_config_path(project_dir).exists()


def load_project_config(project_dir):
    """!
    @brief Load a project's `.psvita-toolkit.json`, if present.
    @param project_dir Path to the project directory.
    @return dict with `_project_dir` injected, or `None` if missing/unreadable.
    """
    p = project_config_path(project_dir)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            data["_project_dir"] = str(project_dir)
            return data
    except (json.JSONDecodeError, OSError):
        return None


def save_project_config(project_dir, cfg):
    """!
    @brief Persist a project's config to `<project_dir>/.psvita-toolkit.json`.
    @param project_dir Path to the project directory.
    @param cfg Config dict to write (internal `_`-prefixed keys are stripped first).
    """
    p = project_config_path(project_dir)
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def new_project_config(
    game_name, slug, project_name, titleid,
    vita_ip="192.168.1.100", vita_port=DEFAULT_VITA_PORT,
    apk_basename="", build_dir="build",
):
    """!
    @brief Build a fresh per-project config dict with the standard Vita path layout.
    @param game_name Display name of the game.
    @param slug Short internal slug (used to derive `ux0:` paths).
    @param project_name CMake project / VPK name.
    @param titleid 9-character PS Vita TITLEID.
    @param vita_ip Test PS Vita's IP address.
    @param vita_port FTP port of the test PS Vita (VitaShell default: 1337).
    @param apk_basename Original `.apk` filename, if known.
    @param build_dir Local build output directory, relative to the project root.
    @return New per-project config dict, ready to pass to `save_project_config()`.
    """
    return {
        "game_name": game_name,
        "slug": slug,
        "project_name": project_name,
        "titleid": titleid,
        "vita_ip": vita_ip,
        "vita_port": vita_port,
        "apk_basename": apk_basename,
        "build_dir": build_dir,
        "vita_downloads_dir": "/ux0:/downloads",
        "vita_data_dir": "/ux0:/data",
        "vita_logs_dir": f"/ux0:/data/{slug}/logs",
        "vita_cg_dir": f"ux0:/data/{slug}/cg",
        "vita_glsl_dir": f"ux0:/data/{slug}/glsl",
        "vita_game_data_dir": f"/ux0:/data/{slug}",
    }


# ---------------------------------------------------------------------------
# Project discovery / adoption
# ---------------------------------------------------------------------------

_TITLEID_RE = re.compile(r'VITA_TITLEID\s+"([A-Za-z0-9]{9})"')
_APPNAME_RE = re.compile(r'VITA_APP_NAME\s+"([^"]+)"')
_PROJECT_RE = re.compile(r'project\(([A-Za-z0-9_]+)')
_IP_RE = re.compile(r'VITA_IP\s*=\s*"([^"]+)"')
_PORT_RE = re.compile(r'VITA_PORT\s*=\s*(\d+)')


def looks_like_port(path):
    """!
    @brief Heuristic: does `path` look like a PS Vita port directory?
    @details True if it already has our own config, or a `CMakeLists.txt`
             containing `VITA_TITLEID`, or a legacy `porting_tools/` folder.
    @param path Directory to check.
    @return `True` if `path` looks like a port.
    """
    path = Path(path)
    if project_config_path(path).exists():
        return True
    if (path / "CMakeLists.txt").exists():
        try:
            text = (path / "CMakeLists.txt").read_text(errors="ignore")
            if "VITA_TITLEID" in text:
                return True
        except OSError:
            pass
    if (path / "porting_tools").is_dir():
        return True
    return False


_NON_PORT_DIR_NAMES = {"soloader-boilerplate"}


def discover_projects(base_dir, exclude_dirs=()):
    """!
    @brief List subfolders of `base_dir` that look like ports.
    @details Excludes the `soloader-boilerplate` scaffold itself (see
             `docs/dev-notes/config.md` for why) and any extra path passed in
             `exclude_dirs`.
    @param base_dir Root directory containing all ports.
    @param exclude_dirs Extra absolute paths to exclude from the results.
    @return list of dicts: `{"path", "name", "adopted", "game_name"}`.
    """
    base = Path(base_dir)
    if not base.is_dir():
        return []
    excluded_resolved = {Path(p).resolve() for p in exclude_dirs if p}
    found = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name in _NON_PORT_DIR_NAMES or entry.resolve() in excluded_resolved:
            continue
        if looks_like_port(entry):
            cfg = load_project_config(entry)
            found.append({
                "path": str(entry),
                "name": entry.name,
                "adopted": cfg is not None,
                "game_name": cfg["game_name"] if cfg else None,
            })
    return found


def autodetect_legacy_fields(project_dir):
    """!
    @brief Best-effort field extraction for adopting a pre-existing port.
    @details Reads `CMakeLists.txt` and a legacy `porting_tools/manage_vita.py`
             (if present) to guess TITLEID / game name / project name / Vita
             IP and port, minimizing manual prompts when adopting an older port.
    @param project_dir Path to the project directory being adopted.
    @return dict of guessed fields (same shape as `new_project_config()`'s inputs).
    """
    project_dir = Path(project_dir)
    guess = {
        "game_name": project_dir.name.replace("-vita", "").replace("-", " "),
        "slug": re.sub(r"[^a-z0-9]", "", project_dir.name.lower()),
        "project_name": "",
        "titleid": "",
        "vita_ip": "192.168.1.100",
        "vita_port": DEFAULT_VITA_PORT,
    }

    cmake = project_dir / "CMakeLists.txt"
    if cmake.exists():
        try:
            text = cmake.read_text(errors="ignore")
            m = _TITLEID_RE.search(text)
            if m:
                guess["titleid"] = m.group(1)
            m = _APPNAME_RE.search(text)
            if m:
                guess["game_name"] = m.group(1)
            m = _PROJECT_RE.search(text)
            if m:
                guess["project_name"] = m.group(1)
        except OSError:
            pass

    legacy_manage = project_dir / "porting_tools" / "manage_vita.py"
    if legacy_manage.exists():
        try:
            text = legacy_manage.read_text(errors="ignore")
            m = _IP_RE.search(text)
            if m:
                guess["vita_ip"] = m.group(1)
            m = _PORT_RE.search(text)
            if m:
                guess["vita_port"] = int(m.group(1))
        except OSError:
            pass

    if not guess["project_name"]:
        guess["project_name"] = guess["slug"] or "port"

    return guess
