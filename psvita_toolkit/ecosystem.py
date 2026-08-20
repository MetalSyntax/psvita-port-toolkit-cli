"""!
@file ecosystem.py
@brief Ecosystem-wide view across every port under `base_dir`: engine-family
       tagging and a diff-based sync of components shared between ports that
       come from the same original engine (falso_jni variants, audio
       wrappers, shader patches, compression libs, etc).

@details
Every other module in this toolkit operates on ONE port at a time
(`project_cfg`). This one is the exception: it treats the whole `base_dir`
as a single ecosystem of related ports, for two things that only make sense
at that scope:

1. **Engine family classification** (`guess_engine_family()` /
   `set_engine_family()` / `classify_project_menu()`) -- a best-effort label
   (`"Zenonia Series"`, `"Unity 4/5"`, ...) stored on each port's own
   `project_cfg["engine_family"]`. Purely a heuristic starting point; the
   porter always has the final word, and this module never overwrites a
   value they already confirmed.
2. **Shared component sync** (`sync_shared()` / `sync_shared_cli()`) -- ports
   in the same engine family often carry a near-identical copy of some
   subsystem (a FalsoJNI bridge, an audio shim, a shader-patch folder) that
   was written once and then copy-pasted into every port from that engine.
   When a bug gets fixed in one of them, this lets that fix propagate to
   the others -- but only the files that actually changed, and only after
   a dry-run diff the caller gets to review.

Also provides the ecosystem-wide read-only report (`discover_all_ports()` /
`print_global_status()`) -- git status, LiveArea completeness, pending
shader count, and newest local build, for every adopted port at a glance.

See `docs/dev-notes/ecosystem.md` for why classification never
auto-overwrites, why sync is dry-run-by-default and diff-based rather than
a blind mirror, and why the CLI-shaped functions are kept separate from the
TUI ones.
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import i18n
from .i18n import t
from . import tui
from .tui import C
from . import config as cfgmod
from . import jni_analyzer
from . import ftp_ops
from . import livearea

STRINGS = {
    "ecosystem.title": {
        "es": "Ecosistema -- Vista global de ports",
        "en": "Ecosystem -- Global port view",
        "pt": "Ecossistema -- Visão global dos ports",
    },
    "ecosystem.breadcrumb": {
        "es": "{game_name} > Ecosistema",
        "en": "{game_name} > Ecosystem",
        "pt": "{game_name} > Ecossistema",
    },
    "ecosystem.menu.status": {
        "es": "Ver estado global del ecosistema",
        "en": "View global ecosystem status",
        "pt": "Ver status global do ecossistema",
    },
    "ecosystem.menu.sync": {
        "es": "Sincronizar componente compartido entre ports",
        "en": "Sync a shared component across ports",
        "pt": "Sincronizar componente compartilhado entre ports",
    },
    "ecosystem.menu.classify": {
        "es": "Clasificar/corregir el motor de este proyecto",
        "en": "Classify/correct this project's engine",
        "pt": "Classificar/corrigir o motor deste projeto",
    },
    "ecosystem.status.no_ports": {
        "es": "[!] No se detectó ningún port adoptado bajo '{base_dir}'.",
        "en": "[!] No adopted port detected under '{base_dir}'.",
        "pt": "[!] Nenhum port adotado detectado em '{base_dir}'.",
    },
    "ecosystem.status.col_name": {
        "es": "PORT",
        "en": "PORT",
        "pt": "PORT",
    },
    "ecosystem.status.col_engine": {
        "es": "MOTOR",
        "en": "ENGINE",
        "pt": "MOTOR",
    },
    "ecosystem.status.col_git": {
        "es": "GIT",
        "en": "GIT",
        "pt": "GIT",
    },
    "ecosystem.status.col_livearea": {
        "es": "LIVEAREA",
        "en": "LIVEAREA",
        "pt": "LIVEAREA",
    },
    "ecosystem.status.col_shaders": {
        "es": "SHADERS PEND.",
        "en": "SHADERS PEND.",
        "pt": "SHADERS PEND.",
    },
    "ecosystem.status.col_build": {
        "es": "ÚLTIMO BUILD",
        "en": "LAST BUILD",
        "pt": "ÚLTIMO BUILD",
    },
    "ecosystem.status.guessed": {
        "es": "estimado",
        "en": "guessed",
        "pt": "estimado",
    },
    "ecosystem.status.git_unknown": {
        "es": "-",
        "en": "-",
        "pt": "-",
    },
    "ecosystem.status.git_clean": {
        "es": "limpio",
        "en": "clean",
        "pt": "limpo",
    },
    "ecosystem.status.git_dirty": {
        "es": "{count} cambio(s) sin commitear",
        "en": "{count} uncommitted change(s)",
        "pt": "{count} mudança(s) sem commit",
    },
    "ecosystem.status.livearea_ok": {
        "es": "OK",
        "en": "OK",
        "pt": "OK",
    },
    "ecosystem.status.livearea_incomplete": {
        "es": "incompleto",
        "en": "incomplete",
        "pt": "incompleto",
    },
    "ecosystem.status.no_build": {
        "es": "(sin build)",
        "en": "(no build)",
        "pt": "(sem build)",
    },
    "ecosystem.status.summary": {
        "es": "{count} port(s) adoptado(s).",
        "en": "{count} adopted port(s).",
        "pt": "{count} port(s) adotado(s).",
    },
    "ecosystem.classify.title": {
        "es": "Clasificar motor del proyecto",
        "en": "Classify project engine",
        "pt": "Classificar motor do projeto",
    },
    "ecosystem.classify.current": {
        "es": "Motor actual: {family}",
        "en": "Current engine: {family}",
        "pt": "Motor atual: {family}",
    },
    "ecosystem.classify.guessed_suffix": {
        "es": "estimado, sin confirmar",
        "en": "guessed, unconfirmed",
        "pt": "estimado, não confirmado",
    },
    "ecosystem.classify.prompt": {
        "es": "Elegí la familia de motor correcta",
        "en": "Pick the correct engine family",
        "pt": "Escolha a família de motor correta",
    },
    "ecosystem.classify.saved": {
        "es": "[+] Guardado: {family}",
        "en": "[+] Saved: {family}",
        "pt": "[+] Salvo: {family}",
    },
    "ecosystem.sync.title": {
        "es": "Sincronizar componente compartido",
        "en": "Sync shared component",
        "pt": "Sincronizar componente compartilhado",
    },
    "ecosystem.sync.pick_family_title": {
        "es": "¿Qué familia de motor querés sincronizar?",
        "en": "Which engine family do you want to sync?",
        "pt": "Qual família de motor você quer sincronizar?",
    },
    "ecosystem.sync.module_prompt": {
        "es": "Ruta relativa del componente a sincronizar (ej. source/falso_jni):",
        "en": "Relative path of the component to sync (e.g. source/falso_jni):",
        "pt": "Caminho relativo do componente a sincronizar (ex. source/falso_jni):",
    },
    "ecosystem.sync.no_ports_in_family": {
        "es": "[-] Ningún port adoptado está etiquetado con la familia '{family}' -- clasificalos primero.",
        "en": "[-] No adopted port is tagged with the '{family}' family -- classify them first.",
        "pt": "[-] Nenhum port adotado está marcado com a família '{family}' -- classifique-os primeiro.",
    },
    "ecosystem.sync.no_module_found": {
        "es": "[-] Ningún port de esta familia tiene '{module}'.",
        "en": "[-] No port in this family has '{module}'.",
        "pt": "[-] Nenhum port desta família tem '{module}'.",
    },
    "ecosystem.sync.source_missing_module": {
        "es": "[-] La fuente indicada no tiene '{path}'.",
        "en": "[-] The given source doesn't have '{path}'.",
        "pt": "[-] A fonte indicada não tem '{path}'.",
    },
    "ecosystem.sync.preview_header": {
        "es": "Familia: {family}  ·  Componente: {module}  ·  Fuente: {source}",
        "en": "Family: {family}  ·  Component: {module}  ·  Source: {source}",
        "pt": "Família: {family}  ·  Componente: {module}  ·  Fonte: {source}",
    },
    "ecosystem.sync.nothing_to_sync": {
        "es": "Nada para sincronizar -- todos los ports de esta familia ya coinciden.",
        "en": "Nothing to sync -- every port in this family already matches.",
        "pt": "Nada para sincronizar -- todos os ports desta família já coincidem.",
    },
    "ecosystem.sync.confirm_apply": {
        "es": "¿Copiar {count} archivo(s) a los ports listados arriba?",
        "en": "Copy {count} file(s) to the ports listed above?",
        "pt": "Copiar {count} arquivo(s) para os ports listados acima?",
    },
    "ecosystem.sync.cancelled": {
        "es": "[!] Cancelado -- no se copió nada.",
        "en": "[!] Cancelled -- nothing was copied.",
        "pt": "[!] Cancelado -- nada foi copiado.",
    },
    "ecosystem.sync.applied_verb": {
        "es": "Copiado",
        "en": "Copied",
        "pt": "Copiado",
    },
    "ecosystem.sync.would_copy_verb": {
        "es": "Se copiaría",
        "en": "Would copy",
        "pt": "Copiaria",
    },
    "ecosystem.sync.summary_line": {
        "es": "{verb} {files} archivo(s) en {ports} port(s).",
        "en": "{verb} {files} file(s) across {ports} port(s).",
        "pt": "{verb} {files} arquivo(s) em {ports} port(s).",
    },
    "ecosystem.sync.file_new": {
        "es": "nuevo",
        "en": "new",
        "pt": "novo",
    },
    "ecosystem.sync.file_differs": {
        "es": "difiere",
        "en": "differs",
        "pt": "difere",
    },
    "ecosystem.sync.copy_errors": {
        "es": "{count} archivo(s) no se pudieron copiar.",
        "en": "{count} file(s) could not be copied.",
        "pt": "{count} arquivo(s) não puderam ser copiados.",
    },
    "ecosystem.sync.dry_run_hint": {
        "es": "[!] Solo vista previa -- pasá --yes para copiar de verdad.",
        "en": "[!] Dry run only -- pass --yes to actually copy.",
        "pt": "[!] Apenas pré-visualização -- passe --yes para copiar de verdade.",
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Engine family classification
# ---------------------------------------------------------------------------

## Known engine families this toolkit can tag a port with. "Unknown" is the
## honest fallback when neither the franchise-name heuristic nor middleware
## detection turns up anything -- see docs/dev-notes/ecosystem.md.
ENGINE_FAMILIES = (
    "Zenonia Series",
    "Gameloft Engine v1/v2",
    "Gamevil RPGs",
    "Unity 4/5",
    "Cocos2d-x",
    "Unknown",
)

# Substring (lowercased) -> family, checked against "{game_name} {slug}".
# Mirrors the existing ad hoc Zenonia 2 special-case in __main__.py's
# _utils_submenu() -- generalized to a table instead of one hardcoded `if`.
_FRANCHISE_HINTS = (
    ("zenonia", "Zenonia Series"),
    ("gameloft", "Gameloft Engine v1/v2"),
    ("gamevil", "Gamevil RPGs"),
    ("nexus", "Gamevil RPGs"),
    ("inotia", "Gamevil RPGs"),
    ("illusia", "Gamevil RPGs"),
)


def guess_engine_family(project_cfg):
    """!
    @brief Best-effort heuristic guess of a port's engine family.
    @details Checks, in order: (1) a value the user already confirmed --
             returned as-is, never contradicted; (2) known franchise-name
             substrings in `game_name`/`slug`; (3) middleware fingerprinting
             on the port's primary `.so` (Unity/Cocos2d only -- the other
             families here aren't things `jni_analyzer.detect_middleware()`
             can see). Falls back to `"Unknown"` rather than guessing wrong
             with false confidence.
    @param project_cfg Per-project config dict.
    @return One of `ENGINE_FAMILIES`.
    @note NEVER call `set_engine_family()` with this result without either
          the user confirming it (`classify_project_menu()`) or the caller
          explicitly wanting an unconfirmed guess -- see
          `docs/dev-notes/ecosystem.md`.
    """
    existing = (project_cfg or {}).get("engine_family")
    if existing:
        return existing

    haystack = f"{project_cfg.get('game_name', '')} {project_cfg.get('slug', '')}".lower()
    for needle, family in _FRANCHISE_HINTS:
        if needle in haystack:
            return family

    so_path = jni_analyzer._find_primary_so(project_cfg.get("_project_dir", ""))
    if so_path:
        middleware = jni_analyzer.detect_middleware(so_path)
        if "Unity (il2cpp/mono)" in middleware:
            return "Unity 4/5"
        if "Cocos2d" in middleware:
            return "Cocos2d-x"

    return "Unknown"


def set_engine_family(project_cfg, family):
    """!
    @brief Persist a user-confirmed engine family onto the project config.
    @param project_cfg Per-project config dict (mutated in place).
    @param family One of `ENGINE_FAMILIES`. Not validated further --
           `classify_project_menu()` is the only caller and always passes a
           value picked from that same list.
    """
    project_cfg["engine_family"] = family
    cfgmod.save_project_config(project_cfg["_project_dir"], project_cfg)


def classify_project_menu(project_cfg, global_cfg):
    """!
    @brief TUI screen: show the current (or guessed) engine family and let
           the user confirm or correct it.
    @param project_cfg Active project's config dict.
    @param global_cfg Global config dict (unused directly here, taken for
           the same shape as `ecosystem_menu()`'s other screens).
    """
    tui.clear()
    stored = project_cfg.get("engine_family")
    current = stored or guess_engine_family(project_cfg)
    label = current if stored else f"{current} {C.DIM}({t('ecosystem.classify.guessed_suffix')}){C.RESET}"
    tui.print_banner(t("ecosystem.classify.title"), subtitle=project_cfg.get("game_name", ""))
    print(t("ecosystem.classify.current", family=label))
    print()

    choice = tui.select_list(t("ecosystem.classify.prompt"), list(ENGINE_FAMILIES))
    if choice is None:
        return
    set_engine_family(project_cfg, choice)
    print(f"{C.GREEN}{t('ecosystem.classify.saved', family=choice)}{C.RESET}")
    tui.pause()


# ---------------------------------------------------------------------------
# Global status view
# ---------------------------------------------------------------------------

def _git_status(project_dir):
    """!
    @brief Best-effort `git status --porcelain` change count for a port.
    @details A port isn't required to be a git repo, so "not a repo" (or git
             missing entirely, or the command failing for any other reason)
             is reported as unknown, not as an error.
    @param project_dir Path to the port's project directory.
    @return Number of changed/untracked entries (`0` = clean), or `None` if
            it can't be determined.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(project_dir), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return len([line for line in r.stdout.splitlines() if line.strip()])


def _livearea_ok(project_dir):
    """!
    @brief Whether a port's LiveArea assets are present and pass validation.
    @param project_dir Path to the port's project directory.
    @return `True` only if `extras/livearea/` exists and every asset
            `livearea.validate_livearea_dir()` checks reports OK.
    """
    dest_dir = Path(project_dir) / "extras" / "livearea"
    if not dest_dir.is_dir():
        return False
    checks = livearea.validate_livearea_dir(dest_dir)
    return bool(checks) and all(ok for _name, ok, _detail in checks)


def _shaders_pending(project_dir):
    """!
    @brief Count of dumped GLSL shaders that don't have a converted Cg counterpart yet.
    @param project_dir Path to the port's project directory.
    @return `max(len(glsl_dump/*.glsl) - len(assets/cg/*.cg), 0)`, ignoring `._*` junk files.
    """
    project_dir = Path(project_dir)
    glsl_dir, cg_dir = project_dir / "glsl_dump", project_dir / "assets" / "cg"
    glsl = [p for p in glsl_dir.glob("*.glsl") if not p.name.startswith("._")] if glsl_dir.is_dir() else []
    cg = [p for p in cg_dir.glob("*.cg") if not p.name.startswith("._")] if cg_dir.is_dir() else []
    return max(len(glsl) - len(cg), 0)


def _last_build(project_cfg):
    """!
    @brief Newest local `.vpk` for a port, if any.
    @param project_cfg Per-project config dict.
    @return `Path` to the newest local `.vpk`, or `None`.
    """
    vpks = ftp_ops.list_local_vpks(project_cfg["_project_dir"], build_dir=project_cfg.get("build_dir", "build"))
    return vpks[0] if vpks else None


def discover_all_ports(global_cfg):
    """!
    @brief Build the ecosystem-wide status snapshot: every adopted port
           under `base_dir`, with its engine family, git status, LiveArea
           completeness, pending-shader count, and newest local build.
    @param global_cfg Global config dict; reads `base_dir`.
    @return list of dicts: `{"project_cfg", "name", "engine_family",
            "git_status", "livearea_ok", "shaders_pending", "last_build"}`.
            `engine_family` is the stored value if the port was already
            classified, else a fresh (never persisted) `guess_engine_family()`
            call -- check `project_cfg.get("engine_family")` to tell the
            two apart.
    """
    base_dir = (global_cfg or {}).get("base_dir", "")
    ports = []
    for entry in cfgmod.discover_projects(base_dir):
        if not entry["adopted"]:
            continue
        project_cfg = cfgmod.load_project_config(entry["path"])
        if not project_cfg:
            continue
        ports.append({
            "project_cfg": project_cfg,
            "name": project_cfg.get("game_name") or entry["name"],
            "engine_family": project_cfg.get("engine_family") or guess_engine_family(project_cfg),
            "git_status": _git_status(entry["path"]),
            "livearea_ok": _livearea_ok(entry["path"]),
            "shaders_pending": _shaders_pending(entry["path"]),
            "last_build": _last_build(project_cfg),
        })
    return ports


def print_global_status(global_cfg, use_color=True):
    """!
    @brief Render `discover_all_ports()` as an aligned plain-text table.
    @param global_cfg Global config dict.
    @param use_color Whether to emit ANSI colors (disabled for `--plain`/CLI log output).
    @return The `discover_all_ports()` list just rendered (handy for a caller
            that wants both the printed report and the raw data).
    """
    ports = discover_all_ports(global_cfg)
    if not ports:
        msg = t("ecosystem.status.no_ports", base_dir=(global_cfg or {}).get("base_dir", ""))
        print(f"{C.YELLOW}{msg}{C.RESET}" if use_color else msg)
        return ports

    headers = (
        t("ecosystem.status.col_name"), t("ecosystem.status.col_engine"), t("ecosystem.status.col_git"),
        t("ecosystem.status.col_livearea"), t("ecosystem.status.col_shaders"), t("ecosystem.status.col_build"),
    )

    rows = []
    for p in ports:
        engine_label = p["engine_family"]
        if not p["project_cfg"].get("engine_family"):
            engine_label = f"{engine_label} ({t('ecosystem.status.guessed')})"
        git = p["git_status"]
        if git is None:
            git_label = t("ecosystem.status.git_unknown")
        elif git == 0:
            git_label = t("ecosystem.status.git_clean")
        else:
            git_label = t("ecosystem.status.git_dirty", count=git)
        livearea_label = t("ecosystem.status.livearea_ok") if p["livearea_ok"] else t("ecosystem.status.livearea_incomplete")
        build_label = p["last_build"].name if p["last_build"] else t("ecosystem.status.no_build")
        rows.append((p["name"], engine_label, git_label, livearea_label, str(p["shaders_pending"]), build_label, git, p["livearea_ok"]))

    widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def fmt_row(cols, colors=None):
        parts = []
        for i, col in enumerate(cols):
            padded = col.ljust(widths[i])
            if colors and colors[i]:
                padded = f"{colors[i]}{padded}{C.RESET}"
            parts.append(padded)
        return "  ".join(parts)

    header_line = fmt_row(headers)
    print(f"{C.BOLD}{header_line}{C.RESET}" if use_color else header_line)
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))

    for name, engine_label, git_label, livearea_label, shaders_label, build_label, git, livearea_ok in rows:
        colors = None
        if use_color:
            git_color = C.DIM if git is None else (C.GREEN if git == 0 else C.YELLOW)
            colors = [None, None, git_color, C.GREEN if livearea_ok else C.YELLOW, None, None]
        print(fmt_row((name, engine_label, git_label, livearea_label, shaders_label, build_label), colors=colors))

    print()
    summary = t("ecosystem.status.summary", count=len(ports))
    print(f"{C.DIM}{summary}{C.RESET}" if use_color else summary)
    return ports


def global_status_cli(global_cfg, plain=False):
    """!
    @brief CLI-shaped entry point for a future `psvita-toolkit ecosystem-status [--plain]`
           subcommand. Mirrors `doctor.run_doctor(global_cfg, use_color=...)`'s shape.
    @param global_cfg Global config dict.
    @param plain Maps to `--plain` -- disables ANSI colors (for log files/CI).
    @return `0` always: this is a read-only report, there's no failing check
            for it to report the way `doctor.run_doctor()` has.
    """
    print_global_status(global_cfg, use_color=not plain)
    return 0


# ---------------------------------------------------------------------------
# Shared component sync
# ---------------------------------------------------------------------------

def _newest_mtime(path):
    """!
    @brief Newest mtime under `path` (itself, if a file; any file inside it, if a directory).
    @param path File or directory path.
    @return mtime as a float (`os.stat().st_mtime`).
    """
    path = Path(path)
    if path.is_file():
        return path.stat().st_mtime
    mtimes = [f.stat().st_mtime for f in path.rglob("*") if f.is_file()]
    return max(mtimes) if mtimes else path.stat().st_mtime


def _sha256(path):
    """!
    @brief SHA-256 hash of a file's contents, read in chunks.
    @param path File path.
    @return Hex digest string.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _diff_action(src_file, dst_file):
    """!
    @brief Decide whether `src_file` needs to be copied over `dst_file`.
    @details Cheap size+mtime comparison first; only falls back to a SHA-256
             hash when sizes match but mtimes don't, so a file that's
             byte-identical but was checked out/cloned at a different time
             (extremely common across independent port repos) isn't flagged
             as "differs" -- see docs/dev-notes/ecosystem.md.
    @param src_file Source file path (must exist).
    @param dst_file Destination file path (may not exist yet).
    @return `"new"` if `dst_file` doesn't exist, `"differs"` if it exists but
            its content doesn't match, or `None` if nothing needs to change.
    """
    if not dst_file.exists():
        return "new"
    try:
        src_stat, dst_stat = src_file.stat(), dst_file.stat()
    except OSError:
        return "differs"
    if src_stat.st_size != dst_stat.st_size:
        return "differs"
    if int(src_stat.st_mtime) == int(dst_stat.st_mtime):
        return None
    return None if _sha256(src_file) == _sha256(dst_file) else "differs"


def _ports_tagged(global_cfg, engine_family):
    """!
    @brief Every adopted port whose STORED `engine_family` exactly matches
           (never a guess -- sync only ever touches ports the user actually
           confirmed belong to this family).
    @param global_cfg Global config dict; reads `base_dir`.
    @param engine_family Family name to match.
    @return list of per-project config dicts.
    """
    base_dir = (global_cfg or {}).get("base_dir", "")
    tagged = []
    for entry in cfgmod.discover_projects(base_dir):
        if not entry["adopted"]:
            continue
        cfg = cfgmod.load_project_config(entry["path"])
        if cfg and cfg.get("engine_family") == engine_family:
            tagged.append(cfg)
    return tagged


def _resolve_sync_source(ports, module_name, source_project_dir):
    """!
    @brief Resolve which port `module_name` should be copied FROM.
    @param ports Ports tagged with the target engine family (`_ports_tagged()`'s result).
    @param module_name Relative path being synced.
    @param source_project_dir Explicit source directory, or `None` to auto-pick.
    @return `(source_dir: Path, source_label: str)` on success, or
            `(None, error_message)` if no valid source could be found.
    """
    if source_project_dir:
        source_dir = Path(source_project_dir).expanduser().resolve()
        if not (source_dir / module_name).exists():
            return None, t("ecosystem.sync.source_missing_module", path=str(source_dir / module_name))
        cfg = cfgmod.load_project_config(source_dir)
        label = (cfg.get("game_name") if cfg else None) or source_dir.name
        return source_dir, label

    candidates = [p for p in ports if (Path(p["_project_dir"]) / module_name).exists()]
    if not candidates:
        return None, t("ecosystem.sync.no_module_found", module=module_name)
    candidates.sort(key=lambda p: _newest_mtime(Path(p["_project_dir"]) / module_name), reverse=True)
    chosen = candidates[0]
    chosen_dir = Path(chosen["_project_dir"])
    return chosen_dir, (chosen.get("game_name") or chosen_dir.name)


def sync_shared(global_cfg, engine_family, module_name, source_project_dir=None, dry_run=True):
    """!
    @brief Copy `module_name` (a path relative to a port's root -- e.g.
           `"source/falso_jni"`, an audio wrapper folder, a shader-patch
           folder) from one port tagged `engine_family` to every OTHER port
           tagged with that same family, but only for files that actually
           differ.
    @param global_cfg Global config dict; reads `base_dir`.
    @param engine_family One of `ENGINE_FAMILIES`. Only ports whose STORED
           `engine_family` matches exactly participate, as both the implicit
           source candidate pool and every target -- a guessed-but-unconfirmed
           family never counts.
    @param module_name Path relative to a port's root.
    @param source_project_dir Explicit source port directory; if omitted,
           the most-recently-modified tagged port that actually has
           `module_name` is used.
    @param dry_run If `True` (the default), only computes and returns the
           diff -- nothing is copied. Pass `False` to actually copy; the
           caller (TUI wizard or `sync_shared_cli()`) is responsible for
           getting the user's confirmation first.
    @return dict: `{"ok", "error", "engine_family", "module_name",
            "source_dir", "source_name", "diffs", "applied", "copy_errors"}`.
            `diffs` is a list of `{"project_dir", "project_name", "files":
            [{"rel_path", "action"}]}` -- only files that actually changed,
            never a full mirror listing. See `docs/dev-notes/ecosystem.md`
            for why this is dry-run-by-default and diff-based.
    """
    result = {
        "ok": False, "error": None, "engine_family": engine_family, "module_name": module_name,
        "source_dir": None, "source_name": None, "diffs": [], "applied": False, "copy_errors": [],
    }

    ports = _ports_tagged(global_cfg, engine_family)
    if not ports:
        result["error"] = t("ecosystem.sync.no_ports_in_family", family=engine_family)
        return result

    source_dir, source_label_or_error = _resolve_sync_source(ports, module_name, source_project_dir)
    if source_dir is None:
        result["error"] = source_label_or_error
        return result
    source_label = source_label_or_error
    source_module_path = source_dir / module_name
    is_file_module = source_module_path.is_file()

    targets = [p for p in ports if Path(p["_project_dir"]).resolve() != source_dir.resolve()]

    diffs = []
    for target_cfg in targets:
        target_dir = Path(target_cfg["_project_dir"])
        target_module_path = target_dir / module_name
        files = []
        if is_file_module:
            action = _diff_action(source_module_path, target_module_path)
            if action:
                files.append({"rel_path": Path(module_name).name, "action": action})
        else:
            for src_file in sorted(p for p in source_module_path.rglob("*") if p.is_file()):
                rel = src_file.relative_to(source_module_path)
                action = _diff_action(src_file, target_module_path / rel)
                if action:
                    files.append({"rel_path": str(rel), "action": action})
        if files:
            diffs.append({
                "project_dir": str(target_dir),
                "project_name": target_cfg.get("game_name") or target_dir.name,
                "files": files,
            })

    result.update({"ok": True, "source_dir": str(source_dir), "source_name": source_label, "diffs": diffs})

    if not dry_run and diffs:
        copy_errors = []
        for entry in diffs:
            target_dir = Path(entry["project_dir"])
            for f in entry["files"]:
                rel = f["rel_path"]
                src_file = source_module_path if is_file_module else source_module_path / rel
                dst_file = (target_dir / module_name) if is_file_module else (target_dir / module_name / rel)
                try:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                except OSError as e:
                    copy_errors.append(f"{entry['project_name']}: {rel} -- {e}")
        result["applied"] = True
        result["copy_errors"] = copy_errors
        if copy_errors:
            result["ok"] = False
            result["error"] = t("ecosystem.sync.copy_errors", count=len(copy_errors))

    return result


def print_sync_preview(result, use_color=True):
    """!
    @brief Print the diff/preview (or copy report, if already applied) from `sync_shared()`.
    @details Shared by the TUI wizard and `sync_shared_cli()` so both show
             exactly the same information.
    @param result Return value of `sync_shared()`.
    @param use_color Whether to emit ANSI colors (disabled by `sync_shared_cli()`).
    """
    def c(code, text):
        return f"{code}{text}{C.RESET}" if use_color else text

    header = t("ecosystem.sync.preview_header", family=result["engine_family"],
               module=result["module_name"], source=result["source_name"])
    print(c(C.BOLD, header))

    if not result["diffs"]:
        print(c(C.GREEN, f"[+] {t('ecosystem.sync.nothing_to_sync')}"))
        return

    for entry in result["diffs"]:
        print(f"\n  {c(C.BOLD, entry['project_name'])}  {c(C.DIM, '(' + entry['project_dir'] + ')')}")
        for f in entry["files"]:
            tag = t("ecosystem.sync.file_new") if f["action"] == "new" else t("ecosystem.sync.file_differs")
            print(f"    - {f['rel_path']}  [{tag}]")

    total_files = sum(len(e["files"]) for e in result["diffs"])
    verb = t("ecosystem.sync.applied_verb") if result["applied"] else t("ecosystem.sync.would_copy_verb")
    print()
    print(c(C.GREEN if result["applied"] else C.YELLOW,
            t("ecosystem.sync.summary_line", verb=verb, files=total_files, ports=len(result["diffs"]))))

    if result["copy_errors"]:
        print(c(C.RED, f"[-] {t('ecosystem.sync.copy_errors', count=len(result['copy_errors']))}"))
        for err in result["copy_errors"]:
            print(f"    - {err}")


def sync_shared_cli(global_cfg, engine_family, module_name, source=None, assume_yes=False):
    """!
    @brief CLI-shaped wrapper for a future `psvita-toolkit sync-shared --engine ENGINE
           --module MODULE [--source SOURCE] [--yes]` subcommand. No `input()`/TUI --
           just a printed report and a process-style exit code.
    @param global_cfg Global config dict.
    @param engine_family Maps to `--engine`.
    @param module_name Maps to `--module`.
    @param source Maps to `--source` (explicit source port directory; default: auto-detected).
    @param assume_yes Maps to `--yes`. Without it, this only ever prints the
           dry-run preview and copies nothing -- dry-run is the loud, obvious
           default here too, same as the TUI wizard.
    @return `0` on success (including a clean no-op/dry-run), `1` if the
            family/module/source couldn't be resolved or a copy failed.
    """
    preview = sync_shared(global_cfg, engine_family, module_name, source_project_dir=source, dry_run=True)
    if not preview["ok"]:
        print(f"[-] {preview['error']}", file=sys.stderr)
        return 1
    print_sync_preview(preview, use_color=False)
    if not preview["diffs"]:
        return 0
    if not assume_yes:
        print(t("ecosystem.sync.dry_run_hint"))
        return 0

    result = sync_shared(global_cfg, engine_family, module_name, source_project_dir=source, dry_run=False)
    print_sync_preview(result, use_color=False)
    return 0 if result["ok"] else 1


# ---------------------------------------------------------------------------
# TUI entry point
# ---------------------------------------------------------------------------

def _sync_wizard(global_cfg):
    """!
    @brief Interactive flow for `ecosystem_menu()`'s "sync shared component" item.
    @param global_cfg Global config dict.
    """
    family = tui.select_list(t("ecosystem.sync.pick_family_title"), list(ENGINE_FAMILIES))
    if family is None:
        return
    module_name = input(f"\n{t('ecosystem.sync.module_prompt')}\n> ").strip()
    if not module_name:
        return

    preview = sync_shared(global_cfg, family, module_name, dry_run=True)
    print()
    if not preview["ok"]:
        print(f"{C.RED}[-] {preview['error']}{C.RESET}")
        return
    print_sync_preview(preview)
    if not preview["diffs"]:
        return

    total_files = sum(len(e["files"]) for e in preview["diffs"])
    if not tui.confirm(t("ecosystem.sync.confirm_apply", count=total_files), default=False):
        print(f"{C.YELLOW}{t('ecosystem.sync.cancelled')}{C.RESET}")
        return

    result = sync_shared(global_cfg, family, module_name, dry_run=False)
    print()
    print_sync_preview(result)


def ecosystem_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: ecosystem-wide status, shared-component sync,
           and this project's own engine classification.
    @param project_cfg Active project's config dict -- only used for
           "classify this project"; the status/sync screens operate off
           `global_cfg` alone, ecosystem-wide. Taken anyway so this wires
           into the current project's Utilities submenu the same way
           `jni_analyzer`'s items do
           (`lambda: ecosystem.ecosystem_menu(project_cfg, global_cfg)`).
    @param global_cfg Global config dict; reads `base_dir`.
    """
    items = [
        (t("ecosystem.menu.status"), lambda: print_global_status(global_cfg)),
        (t("ecosystem.menu.sync"), lambda: _sync_wizard(global_cfg)),
        (t("ecosystem.menu.classify"), lambda: classify_project_menu(project_cfg, global_cfg)),
    ]
    tui.run_menu(
        t("ecosystem.title"), items,
        breadcrumb=t("ecosystem.breadcrumb", game_name=project_cfg.get("game_name", "")),
    )
