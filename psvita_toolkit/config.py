"""
Configuración persistente del toolkit.

Dos niveles:
  - Config GLOBAL (~/.psvita-toolkit/config.json): rutas que no dependen del
    port activo (BASE_DIR donde viven todos los ports, soloader-boilerplate,
    skills de Claude, VITASDK, Vita3K.app). Se pregunta una sola vez.
  - Config POR PROYECTO (<port_dir>/.psvita-toolkit.json): datos propios de
    cada port (nombre, slug, TITLEID, IP de la Vita de pruebas, etc). Se crea
    al inicializar un port nuevo, o se "adopta" (auto-detectada) la primera
    vez que se abre un port viejo que todavía no tiene este archivo.
"""

import json
import os
import re
from pathlib import Path

GLOBAL_CONFIG_DIR = Path.home() / ".psvita-toolkit"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "config.json"
PROJECT_CONFIG_FILENAME = ".psvita-toolkit.json"

DEFAULT_VITA_PORT = 1337


def _expand(p):
    if not p:
        return p
    p = p.strip()
    if len(p) >= 2 and ((p[0] == p[-1] == '"') or (p[0] == p[-1] == "'")):
        p = p[1:-1]
    p = p.replace("\\ ", " ")
    return str(Path(os.path.expanduser(os.path.expandvars(p))))


# ---------------------------------------------------------------------------
# Config global
# ---------------------------------------------------------------------------

def load_global_config():
    if not GLOBAL_CONFIG_PATH.exists():
        return {}
    try:
        with open(GLOBAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_global_config(cfg):
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(GLOBAL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


REQUIRED_GLOBAL_KEYS = {
    "base_dir": "Carpeta base donde viven todos tus ports (ej. /Volumes/Seagate/PSVITA Develop)",
    "boilerplate_dir": "Ruta a soloader-boilerplate (scaffold usado para ports nuevos)",
    "skills_source": "Carpeta de skills de Claude Code a copiar en cada port nuevo",
    "vitasdk": "Ruta a VITASDK",
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


def ensure_global_config(tui):
    """Carga la config global; si faltan claves requeridas, las pregunta
    interactivamente (una sola vez, quedan guardadas para la próxima)."""
    cfg = load_global_config()
    changed = False

    if cfg:
        missing = [k for k in REQUIRED_GLOBAL_KEYS if not cfg.get(k)]
    else:
        missing = list(REQUIRED_GLOBAL_KEYS)

    if missing:
        tui.clear()
        print(f"{tui.C.CYAN}{tui.C.BOLD}================================================================{tui.C.RESET}")
        print(f"{tui.C.CYAN}{tui.C.BOLD}  🛠️  Primera vez -- configuración inicial del toolkit{tui.C.RESET}")
        print(f"{tui.C.CYAN}{tui.C.BOLD}================================================================{tui.C.RESET}")
        print(f"{tui.C.DIM}Esto se pregunta una sola vez y se guarda en {GLOBAL_CONFIG_PATH}{tui.C.RESET}\n")

        for key in missing:
            desc = REQUIRED_GLOBAL_KEYS[key]
            default = cfg.get(key, "")
            while True:
                prompt = f"{tui.C.BOLD}{desc}{tui.C.RESET}"
                if default:
                    prompt += f" [{default}]"
                prompt += ":\n> "
                raw = input(prompt).strip() or default
                raw = _expand(raw)
                if not raw:
                    print(f"{tui.C.RED}Este valor es obligatorio.{tui.C.RESET}")
                    continue
                if key in ("boilerplate_dir", "skills_source", "vitasdk") and not os.path.isdir(raw):
                    ok = tui.confirm(f"'{raw}' no existe todavía -- ¿continuar de todas formas?", default=False)
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
    cfg = load_global_config()
    cfg.update(kwargs)
    save_global_config(cfg)
    return cfg


def remember_project(project_dir):
    cfg = load_global_config()
    recents = cfg.get("recent_projects", [])
    project_dir = str(project_dir)
    recents = [p for p in recents if p != project_dir]
    recents.insert(0, project_dir)
    cfg["recent_projects"] = recents[:15]
    cfg["last_project"] = project_dir
    save_global_config(cfg)


# ---------------------------------------------------------------------------
# Config por proyecto
# ---------------------------------------------------------------------------

def project_config_path(project_dir):
    return Path(project_dir) / PROJECT_CONFIG_FILENAME


def has_project_config(project_dir):
    return project_config_path(project_dir).exists()


def load_project_config(project_dir):
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
# Descubrimiento / adopción de proyectos
# ---------------------------------------------------------------------------

_TITLEID_RE = re.compile(r'VITA_TITLEID\s+"([A-Za-z0-9]{9})"')
_APPNAME_RE = re.compile(r'VITA_APP_NAME\s+"([^"]+)"')
_PROJECT_RE = re.compile(r'project\(([A-Za-z0-9_]+)')
_IP_RE = re.compile(r'VITA_IP\s*=\s*"([^"]+)"')
_PORT_RE = re.compile(r'VITA_PORT\s*=\s*(\d+)')


def looks_like_port(path):
    """Heurística: ¿esta carpeta parece un port de PS Vita? (CMakeLists.txt
    con VITA_TITLEID, o ya tiene porting_tools/, o ya tiene nuestra config)."""
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
    """Lista subcarpetas de base_dir que parecen ports (con o sin config
    nuestra todavía) -- para el selector 'continuar con un port existente'.
    Excluye el propio scaffold de soloader-boilerplate (su CMakeLists.txt
    trae un VITA_TITLEID placeholder que si no lo hiciéramos matchearía la
    misma heurística) y cualquier ruta extra que se le pase (ej. el propio
    boilerplate_dir configurado, por si vive con otro nombre de carpeta)."""
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
    """Para un port viejo (Zenonia2/3/4, DH2, Advena, o cualquiera creado
    antes de este toolkit) sin .psvita-toolkit.json: extrae lo que se pueda
    de CMakeLists.txt y de un porting_tools/manage_vita.py heredado, para
    'adoptarlo' con el mínimo de preguntas manuales."""
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
