"""
Selección / adopción / creación de proyectos ("ports").

Este módulo resuelve la primera pantalla del toolkit: "¿con qué port querés
trabajar?" -- continuar con uno existente (detectado automáticamente bajo
BASE_DIR, elegido de los recientes, o por ruta manual), o crear uno nuevo
desde cero (delega en init_port.py).
"""

from pathlib import Path

from . import config as cfgmod
from . import tui
from .tui import C
from . import i18n
from .i18n import t

STRINGS = {
    "project.col_name": {
        "es": "Nombre",
        "en": "Name",
        "pt": "Nome",
    },
    "project.col_status": {
        "es": "Estado",
        "en": "Status",
        "pt": "Estado",
    },
    "project.col_path": {
        "es": "Ruta",
        "en": "Path",
        "pt": "Caminho",
    },
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
    "project.back_option": {
        "es": "Volver",
        "en": "Back",
        "pt": "Voltar",
    },
    "project.choose_project_prompt": {
        "es": "Elegí un proyecto [1-{n}], R, o 0: ",
        "en": "Choose a project [1-{n}], R, or 0: ",
        "pt": "Escolha um projeto [1-{n}], R, ou 0: ",
    },
    "project.manual_path_prompt": {
        "es": "Ruta absoluta a la carpeta del port:",
        "en": "Absolute path to the port folder:",
        "pt": "Caminho absoluto para a pasta do port:",
    },
    "project.invalid_option": {
        "es": "Opción inválida.",
        "en": "Invalid option.",
        "pt": "Opção inválida.",
    },
    "project.continue_last": {
        "es": "▶️  Continuar con el último port: ",
        "en": "▶️  Continue with the last port: ",
        "pt": "▶️  Continuar com o último port: ",
    },
    "project.menu_continue_other": {
        "es": "📂 Continuar con otro port existente (elegir de la lista / ruta manual)",
        "en": "📂 Continue with another existing port (pick from list / manual path)",
        "pt": "📂 Continuar com outro port existente (escolher da lista / caminho manual)",
    },
    "project.menu_create_new": {
        "es": "🆕 Crear un port NUEVO desde cero (APK Android -> PS Vita)",
        "en": "🆕 Create a NEW port from scratch (Android APK -> PS Vita)",
        "pt": "🆕 Criar um port NOVO do zero (APK Android -> PS Vita)",
    },
    "project.menu_global_config": {
        "es": "⚙️  Configuración global (rutas de BASE_DIR, VITASDK, etc.)",
        "en": "⚙️  Global settings (BASE_DIR, VITASDK paths, etc.)",
        "pt": "⚙️  Configuração global (caminhos de BASE_DIR, VITASDK, etc.)",
    },
    "project.menu_exit": {
        "es": "❌ Salir",
        "en": "❌ Exit",
        "pt": "❌ Sair",
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
    "project.footer_hint_short": {
        "es": "↑/↓ mover · Enter elegir · Ctrl+C salir",
        "en": "↑/↓ move · Enter select · Ctrl+C exit",
        "pt": "↑/↓ mover · Enter selecionar · Ctrl+C sair",
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


def _print_project_table(projects):
    print(f"  {C.BOLD}{'#':<4}{t('project.col_name'):<28}{t('project.col_status'):<14}{t('project.col_path')}{C.RESET}")
    print(f"  {'-' * 4}{'-' * 28}{'-' * 14}{'-' * 30}")
    for i, p in enumerate(projects, 1):
        estado = f"{C.GREEN}{t('project.status_adopted')}{C.RESET}" if p["adopted"] else f"{C.YELLOW}{t('project.status_no_config')}{C.RESET}"
        nombre = p["game_name"] or p["name"]
        print(f"  {i:<4}{nombre:<28}{estado:<23}{C.DIM}{p['path']}{C.RESET}")


def _adopt_project(project_dir):
    """Un port viejo (o cualquier carpeta que 'parece' un port) sin
    .psvita-toolkit.json todavía: auto-detecta lo que se pueda y confirma
    con el usuario antes de guardar la config."""
    guess = cfgmod.autodetect_legacy_fields(project_dir)

    tui.clear()
    tui.print_banner(t("project.adopt_banner_title"), icon="📂",
                      subtitle=str(project_dir))
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


def _select_from_list(global_cfg):
    base_dir = global_cfg.get("base_dir", "")
    exclude = [global_cfg.get("boilerplate_dir", "")]
    projects = cfgmod.discover_projects(base_dir, exclude_dirs=exclude) if base_dir else []

    while True:
        tui.clear()
        tui.print_banner(t("project.select_existing_banner_title"), icon="📂",
                          subtitle=base_dir)
        if not projects:
            print(f"{C.YELLOW}{t('project.no_ports_detected', base_dir=base_dir)}{C.RESET}\n")
        else:
            _print_project_table(projects)
            print()

        print(f"  {C.GREEN}[R]{C.RESET} {t('project.enter_path_manually')}")
        print(f"  {C.RED}[0]{C.RESET} {t('project.back_option')}")
        choice = input(f"\n{C.BOLD}{t('project.choose_project_prompt', n=len(projects))}{C.RESET}").strip().lower()

        if choice in ("0", "q", ""):
            return None
        if choice == "r":
            manual = tui.input_path(t("project.manual_path_prompt"), must_exist=True, is_dir=True)
            return _open_project(manual)
        try:
            idx = int(choice)
            if 1 <= idx <= len(projects):
                return _open_project(projects[idx - 1]["path"])
        except ValueError:
            pass
        print(f"{C.RED}{t('project.invalid_option')}{C.RESET}")
        tui.pause()


def _open_project(project_dir):
    project_dir = Path(project_dir).expanduser().resolve()
    cfg = cfgmod.load_project_config(project_dir)
    if cfg is None:
        cfg = _adopt_project(project_dir)
    cfgmod.remember_project(project_dir)
    return cfg


def select_or_create_project(global_cfg):
    """Pantalla de entrada del toolkit. Devuelve un dict de config de
    proyecto listo para usar (con '_project_dir' seteado), o None si el
    usuario eligió salir."""
    from . import init_port  # import diferido: evita ciclos, init_port no se necesita si no se crea nada

    last = global_cfg.get("last_project")
    recent_valid = last and Path(last).is_dir()

    def continuar_ultimo():
        return _open_project(last)

    def continuar_lista():
        return _select_from_list(global_cfg)

    def crear_nuevo():
        return init_port.run_wizard(global_cfg)

    while True:
        global_cfg.update(cfgmod.load_global_config())
        last = global_cfg.get("last_project")
        recent_valid = last and Path(last).is_dir()
        result_holder = {}

        def wrap_action(fn):
            def inner():
                result_holder["value"] = fn()
            return inner

        items = []
        if recent_valid:
            items.append((f"{t('project.continue_last')}{C.BOLD}{Path(last).name}{C.RESET}", wrap_action(continuar_ultimo)))
        items.append((t("project.menu_continue_other"), wrap_action(continuar_lista)))
        items.append((t("project.menu_create_new"), wrap_action(crear_nuevo)))
        items.append((t("project.menu_global_config"), wrap_action(lambda: _edit_global_config(global_cfg))))
        items.append((f"{C.RED}{t('project.menu_exit')}{C.RESET}", None))

        idx = 0
        n = len(items)
        while True:
            tui.clear()
            tui.print_banner(t("project.main_menu_title"), subtitle=t("project.main_menu_subtitle"), icon="🎮")
            print(f"{C.DIM}{t('project.base_prefix', path=global_cfg.get('base_dir') or t('project.base_not_configured'))}{C.RESET}\n")
            for i, (label, _cb) in enumerate(items):
                prefix = f"{i + 1:2d}. " if i < 9 else "    "
                if i == idx:
                    print(f"{C.BLUE}{C.BOLD}\033[7m> {prefix}{label}{C.RESET}")
                else:
                    print(f"  {prefix}{label}")
            print(f"\n{C.DIM}{t('project.footer_hint_short')}{C.RESET}")

            try:
                c = tui.getch()
            except (EOFError, KeyboardInterrupt):
                return None

            if c == "\x1b[A":
                idx = (idx - 1) % n
            elif c == "\x1b[B":
                idx = (idx + 1) % n
            elif c in ("\r", "\n"):
                label, cb = items[idx]
                if cb is None:
                    return None
                cb()
                if result_holder.get("value"):
                    return result_holder["value"]
                break  # volver a redibujar el menú de selección de proyecto
            elif c == "\x03":
                return None
            elif c.isdigit():
                v = int(c)
                if 1 <= v <= min(9, n):
                    idx = v - 1


def _edit_global_config(global_cfg):
    tui.clear()
    tui.print_banner(t("project.global_config_banner_title"), icon="⚙️")

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
