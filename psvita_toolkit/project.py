"""!
@file project.py
@brief Project selection, adoption, and creation screen.

@details
Entry screen of the toolkit: continue with an existing port (auto-detected under
`BASE_DIR`, picked from the recents list, or given as a manual path), or create a
new one from scratch (delegates to `init_port.py`).

See `docs/dev-notes/project.md` for the rationale behind the adoption flow and
the main menu's use of `tui.MenuResult`.
"""

from pathlib import Path

from . import catalog
from . import config as cfgmod
from . import doctor
from . import tui
from .tui import C
from . import i18n
from .i18n import t

STRINGS = {
    "project.status_adopted": {
        "es": "adoptado",
        "en": "adopted",
        "pt": "adotado",
    },
    "project.status_no_config": {
        "es": "sin config",
        "en": "no config",
        "pt": "sem config",
    },
    "project.adopt_banner_title": {
        "es": "Adoptar proyecto existente",
        "en": "Adopt existing project",
        "pt": "Adotar projeto existente",
    },
    "project.adopt_detected_note": {
        "es": "Detectado automáticamente desde CMakeLists.txt / porting_tools/ heredado.",
        "en": "Auto-detected from CMakeLists.txt / inherited porting_tools/.",
        "pt": "Detectado automaticamente a partir de CMakeLists.txt / porting_tools/ herdado.",
    },
    "project.adopt_confirm_note": {
        "es": "Confirmá o corregí cada valor (Enter = aceptar el detectado).",
        "en": "Confirm or correct each value (Enter = accept the detected one).",
        "pt": "Confirme ou corrija cada valor (Enter = aceitar o detectado).",
    },
    "project.game_name_prompt": {
        "es": "Nombre del juego",
        "en": "Game name",
        "pt": "Nome do jogo",
    },
    "project.slug_prompt": {
        "es": "Slug interno",
        "en": "Internal slug",
        "pt": "Slug interno",
    },
    "project.cmake_project_name_prompt": {
        "es": "Nombre de proyecto CMake",
        "en": "CMake project name",
        "pt": "Nome do projeto CMake",
    },
    "project.titleid_prompt": {
        "es": "TITLEID (9 caracteres)",
        "en": "TITLEID (9 characters)",
        "pt": "TITLEID (9 caracteres)",
    },
    "project.titleid_length_error": {
        "es": "Debe tener exactamente 9 caracteres.",
        "en": "Must be exactly 9 characters long.",
        "pt": "Deve ter exatamente 9 caracteres.",
    },
    "project.vita_ip_prompt": {
        "es": "IP de la PS Vita de pruebas",
        "en": "Test PS Vita IP address",
        "pt": "IP do PS Vita de testes",
    },
    "project.vita_port_prompt": {
        "es": "Puerto FTP",
        "en": "FTP port",
        "pt": "Porta FTP",
    },
    "project.adopt_saved": {
        "es": "[+] Proyecto adoptado -- guardado en {path}/.psvita-toolkit.json",
        "en": "[+] Project adopted -- saved to {path}/.psvita-toolkit.json",
        "pt": "[+] Projeto adotado -- salvo em {path}/.psvita-toolkit.json",
    },
    "project.select_existing_banner_title": {
        "es": "Continuar con un port existente",
        "en": "Continue with an existing port",
        "pt": "Continuar com um port existente",
    },
    "project.no_ports_detected": {
        "es": "No se detectó ningún port bajo '{base_dir}'.",
        "en": "No port was detected under '{base_dir}'.",
        "pt": "Nenhum port foi detectado em '{base_dir}'.",
    },
    "project.enter_path_manually": {
        "es": "Ingresar una ruta manualmente",
        "en": "Enter a path manually",
        "pt": "Digitar um caminho manualmente",
    },
    "project.manual_path_prompt": {
        "es": "Ruta absoluta a la carpeta del port:",
        "en": "Absolute path to the port folder:",
        "pt": "Caminho absoluto para a pasta do port:",
    },
    "project.continue_current": {
        "es": "Continuar con el port actual:",
        "en": "Continue with current port:",
        "pt": "Continuar com o port atual:",
    },
    "project.continue_last": {
        "es": "Continuar con el último port:",
        "en": "Continue with the last port:",
        "pt": "Continuar com o último port:",
    },
    "project.menu_continue_other": {
        "es": "Continuar con otro port existente (elegir de la lista / ruta manual)",
        "en": "Continue with another existing port (pick from list / manual path)",
        "pt": "Continuar com outro port existente (escolher da lista / caminho manual)",
    },
    "project.menu_create_new": {
        "es": "Crear un port NUEVO desde cero (APK Android -> PS Vita)",
        "en": "Create a NEW port from scratch (Android APK -> PS Vita)",
        "pt": "Criar um port NOVO do zero (APK Android -> PS Vita)",
    },
    "project.menu_global_config": {
        "es": "Configuración global (rutas de BASE_DIR, VITASDK, etc.)",
        "en": "Global settings (BASE_DIR, VITASDK paths, etc.)",
        "pt": "Configuração global (caminhos de BASE_DIR, VITASDK, etc.)",
    },
    "project.menu_doctor": {
        "es": "Doctor -- chequear el entorno (VITASDK, Docker, jadx, CMake...)",
        "en": "Doctor -- check the environment (VITASDK, Docker, jadx, CMake...)",
        "pt": "Doctor -- verificar o ambiente (VITASDK, Docker, jadx, CMake...)",
    },
    "project.menu_catalog": {
        "es": "Catálogo de Herramientas -- qué hace cada una",
        "en": "Tool Catalog -- what each one does",
        "pt": "Catálogo de Ferramentas -- o que cada uma faz",
    },
    "project.menu_exit": {
        "es": "Salir",
        "en": "Exit",
        "pt": "Sair",
    },
    "project.main_menu_title": {
        "es": "PS VITA PORT TOOLKIT",
        "en": "PS VITA PORT TOOLKIT",
        "pt": "PS VITA PORT TOOLKIT",
    },
    "project.main_menu_subtitle": {
        "es": "Android → PS Vita, de punta a punta",
        "en": "Android → PS Vita, end to end",
        "pt": "Android → PS Vita, de ponta a ponta",
    },
    "project.base_not_configured": {
        "es": "(sin configurar)",
        "en": "(not configured)",
        "pt": "(não configurado)",
    },
    "project.base_prefix": {
        "es": "Base: {path}",
        "en": "Base: {path}",
        "pt": "Base: {path}",
    },
    "project.global_config_banner_title": {
        "es": "Configuración global",
        "en": "Global settings",
        "pt": "Configuração global",
    },
    "project.change_language_confirm": {
        "es": "Idioma actual: {name}. ¿Querés cambiarlo?",
        "en": "Current language: {name}. Do you want to change it?",
        "pt": "Idioma atual: {name}. Deseja alterá-lo?",
    },
    "project.config_saved": {
        "es": "[+] Configuración guardada.",
        "en": "[+] Settings saved.",
        "pt": "[+] Configuração salva.",
    },
}
i18n.register(STRINGS)


def _adopt_project(project_dir):
    """!
    @brief Interactive wizard to adopt a port that has no `.psvita-toolkit.json` yet.
    @details Pre-fills each field with `cfgmod.autodetect_legacy_fields()`'s best-effort
             guess and lets the user confirm or correct every value before saving.
    @param project_dir Path to the port directory being adopted.
    @return The new per-project config dict (same shape as
            `cfgmod.new_project_config()`'s output), with `_project_dir` set.
    """
    guess = cfgmod.autodetect_legacy_fields(project_dir)

    tui.clear()
    tui.print_banner(t("project.adopt_banner_title"), subtitle=str(project_dir))
    print(f"{C.DIM}{t('project.adopt_detected_note')}{C.RESET}")
    print(f"{C.DIM}{t('project.adopt_confirm_note')}{C.RESET}\n")

    game_name = input(f"{C.BOLD}{t('project.game_name_prompt')}{C.RESET} [{guess['game_name']}]: ").strip() or guess["game_name"]
    default_slug = guess["slug"] or "".join(c for c in game_name.lower() if c.isalnum())
    slug = input(f"{C.BOLD}{t('project.slug_prompt')}{C.RESET} [{default_slug}]: ").strip() or default_slug
    project_name = input(f"{C.BOLD}{t('project.cmake_project_name_prompt')}{C.RESET} [{guess['project_name']}]: ").strip() or guess["project_name"]

    while True:
        titleid = input(f"{C.BOLD}{t('project.titleid_prompt')}{C.RESET} [{guess['titleid'] or '???'}]: ").strip() or guess["titleid"]
        if len(titleid) == 9:
            break
        print(f"{C.RED}{t('project.titleid_length_error')}{C.RESET}")

    vita_ip = input(f"{C.BOLD}{t('project.vita_ip_prompt')}{C.RESET} [{guess['vita_ip']}]: ").strip() or guess["vita_ip"]
    port_raw = input(f"{C.BOLD}{t('project.vita_port_prompt')}{C.RESET} [{guess['vita_port']}]: ").strip()
    vita_port = int(port_raw) if port_raw.isdigit() else guess["vita_port"]

    project_cfg = cfgmod.new_project_config(
        game_name=game_name, slug=slug, project_name=project_name,
        titleid=titleid, vita_ip=vita_ip, vita_port=vita_port,
    )
    cfgmod.save_project_config(project_dir, project_cfg)
    print(f"\n{C.GREEN}{t('project.adopt_saved', path=project_dir)}{C.RESET}")
    tui.pause()
    project_cfg["_project_dir"] = str(project_dir)
    return project_cfg


_MANUAL_PATH_ENTRY = {"_manual_path": True}


def _project_entry_label(p):
    if p.get("_manual_path"):
        return f"{C.CYAN}{t('project.enter_path_manually')}{C.RESET}"
    status = (f"{C.GREEN}{t('project.status_adopted')}{C.RESET}" if p["adopted"]
              else f"{C.YELLOW}{t('project.status_no_config')}{C.RESET}")
    name = p["game_name"] or p["name"]
    return f"{C.BOLD}{name}{C.RESET}  {C.DIM}[{C.RESET}{status}{C.DIM}] {p['path']}{C.RESET}"


def _select_from_list(global_cfg):
    """!
    @brief List detected ports under `global_cfg`'s `base_dir` and let the user pick
           one, enter a manual path, or go back.
    @param global_cfg The global config dict (reads `base_dir` and `boilerplate_dir`).
    @return The opened project's config dict (see `_open_project()`), or `None` if
            the user chose to go back.
    """
    base_dir = global_cfg.get("base_dir", "")
    exclude = [global_cfg.get("boilerplate_dir", "")]
    projects = cfgmod.discover_projects(base_dir, exclude_dirs=exclude) if base_dir else []
    entries = list(projects) + [_MANUAL_PATH_ENTRY]

    def header():
        if not projects:
            print(f"{C.YELLOW}{t('project.no_ports_detected', base_dir=base_dir)}{C.RESET}")

    chosen = tui.select_list(
        t("project.select_existing_banner_title"), entries, label_fn=_project_entry_label,
        subtitle=base_dir, header_extra=header,
    )
    if chosen is None:
        return None
    if chosen.get("_manual_path"):
        manual = tui.input_path(t("project.manual_path_prompt"), must_exist=True, is_dir=True)
        return _open_project(manual)
    return _open_project(chosen["path"])


def _open_project(project_dir):
    project_dir = Path(project_dir).expanduser().resolve()
    cfg = cfgmod.load_project_config(project_dir)
    if cfg is None:
        cfg = _adopt_project(project_dir)
    cfgmod.remember_project(project_dir)
    return cfg


def select_or_create_project(global_cfg):
    """!
    @brief Toolkit's entry screen: continue / pick / adopt / create a project.
    @param global_cfg Global config dict.
    @return Ready-to-use per-project config dict (with `_project_dir` set),
            or `None` if the user chose to exit.
    @note "Continue last/other" and "create new" raise `tui.MenuResult` so
          `run_menu()` hands the chosen project straight back here without an
          Enter-to-continue prompt; "global settings"/"doctor" are plain
          callbacks, so `run_menu()` just redraws this same menu after them.
    """
    from . import init_port  # deferred import: avoids a cycle, init_port isn't needed unless creating a new port

    def continuar_ultimo():
        raise tui.MenuResult(_open_project(global_cfg.get("last_project")))

    def continuar_lista():
        raise tui.MenuResult(_select_from_list(global_cfg))

    def crear_nuevo():
        raise tui.MenuResult(init_port.run_wizard(global_cfg))

    global_cfg.update(cfgmod.load_global_config())
    last = global_cfg.get("last_project")

    items = []
    cwd = Path.cwd().resolve()
    current_has_config = (cwd / cfgmod.PROJECT_CONFIG_FILENAME).is_file()

    if current_has_config:
        current_cfg = cfgmod.load_project_config(cwd)
        current_name = (current_cfg.get("game_name") if current_cfg else None) or cwd.name
        def continuar_actual():
            raise tui.MenuResult(_open_project(cwd))
        items.append((f"{t('project.continue_current')} {C.BOLD}{current_name}{C.RESET}", continuar_actual))

    if last and Path(last).is_dir():
        last_dir = Path(last).resolve()
        if not current_has_config or last_dir != cwd:
            last_cfg = cfgmod.load_project_config(last_dir)
            last_name = (last_cfg.get("game_name") if last_cfg else None) or last_dir.name
            items.append((f"{t('project.continue_last')} {C.BOLD}{last_name}{C.RESET}", continuar_ultimo))

    items.append((t("project.menu_continue_other"), continuar_lista))
    items.append((t("project.menu_create_new"), crear_nuevo))
    items.append((t("project.menu_global_config"), lambda: _edit_global_config(global_cfg)))
    items.append((t("project.menu_doctor"), lambda: doctor.doctor_menu(global_cfg)))
    items.append((t("project.menu_catalog"), catalog.print_catalog))
    items.append((f"{C.RED}{t('project.menu_exit')}{C.RESET}", None))

    def header():
        base = global_cfg.get("base_dir") or t("project.base_not_configured")
        print(f"{C.DIM}{t('project.base_prefix', path=base)}{C.RESET}")

    return tui.run_menu(
        t("project.main_menu_title"), items,
        subtitle=t("project.main_menu_subtitle"), header_extra=header,
    )


def _edit_global_config(global_cfg):
    tui.clear()
    tui.print_banner(t("project.global_config_banner_title"))

    current_lang = i18n.get_language()
    if tui.confirm(t("project.change_language_confirm", name=i18n.LANGUAGE_NAMES[current_lang]), default=False):
        new_lang = cfgmod.prompt_language()
        i18n.set_language(new_lang)
        global_cfg["language"] = new_lang
        cfgmod.save_global_config(global_cfg)
        print(f"\n{C.GREEN}{t('config.language_saved', name=i18n.LANGUAGE_NAMES[new_lang])}{C.RESET}\n")

    for key, desc in cfgmod.REQUIRED_GLOBAL_KEYS.items():
        current = global_cfg.get(key, "")
        raw = input(f"{C.BOLD}{t(desc)}{C.RESET}\n[{current}] > ").strip()
        if raw:
            global_cfg[key] = raw
    cfgmod.save_global_config(global_cfg)
    print(f"\n{C.GREEN}{t('project.config_saved')}{C.RESET}")
    tui.pause()
