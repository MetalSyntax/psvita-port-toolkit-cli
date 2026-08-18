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


def _print_project_table(projects):
    print(f"  {C.BOLD}{'#':<4}{'Nombre':<28}{'Estado':<14}{'Ruta'}{C.RESET}")
    print(f"  {'-' * 4}{'-' * 28}{'-' * 14}{'-' * 30}")
    for i, p in enumerate(projects, 1):
        estado = f"{C.GREEN}adoptado{C.RESET}" if p["adopted"] else f"{C.YELLOW}sin config{C.RESET}"
        nombre = p["game_name"] or p["name"]
        print(f"  {i:<4}{nombre:<28}{estado:<23}{C.DIM}{p['path']}{C.RESET}")


def _adopt_project(project_dir):
    """Un port viejo (o cualquier carpeta que 'parece' un port) sin
    .psvita-toolkit.json todavía: auto-detecta lo que se pueda y confirma
    con el usuario antes de guardar la config."""
    guess = cfgmod.autodetect_legacy_fields(project_dir)

    tui.clear()
    tui.print_banner("Adoptar proyecto existente", icon="📂",
                      subtitle=str(project_dir))
    print(f"{C.DIM}Detectado automáticamente desde CMakeLists.txt / porting_tools/ heredado.{C.RESET}")
    print(f"{C.DIM}Confirmá o corregí cada valor (Enter = aceptar el detectado).{C.RESET}\n")

    game_name = input(f"{C.BOLD}Nombre del juego{C.RESET} [{guess['game_name']}]: ").strip() or guess["game_name"]
    default_slug = guess["slug"] or "".join(c for c in game_name.lower() if c.isalnum())
    slug = input(f"{C.BOLD}Slug interno{C.RESET} [{default_slug}]: ").strip() or default_slug
    project_name = input(f"{C.BOLD}Nombre de proyecto CMake{C.RESET} [{guess['project_name']}]: ").strip() or guess["project_name"]

    while True:
        titleid = input(f"{C.BOLD}TITLEID{C.RESET} (9 caracteres) [{guess['titleid'] or '???'}]: ").strip() or guess["titleid"]
        if len(titleid) == 9:
            break
        print(f"{C.RED}Debe tener exactamente 9 caracteres.{C.RESET}")

    vita_ip = input(f"{C.BOLD}IP de la PS Vita de pruebas{C.RESET} [{guess['vita_ip']}]: ").strip() or guess["vita_ip"]
    port_raw = input(f"{C.BOLD}Puerto FTP{C.RESET} [{guess['vita_port']}]: ").strip()
    vita_port = int(port_raw) if port_raw.isdigit() else guess["vita_port"]

    project_cfg = cfgmod.new_project_config(
        game_name=game_name, slug=slug, project_name=project_name,
        titleid=titleid, vita_ip=vita_ip, vita_port=vita_port,
    )
    cfgmod.save_project_config(project_dir, project_cfg)
    print(f"\n{C.GREEN}[+] Proyecto adoptado -- guardado en {project_dir}/.psvita-toolkit.json{C.RESET}")
    tui.pause()
    project_cfg["_project_dir"] = str(project_dir)
    return project_cfg


def _select_from_list(global_cfg):
    base_dir = global_cfg.get("base_dir", "")
    exclude = [global_cfg.get("boilerplate_dir", "")]
    projects = cfgmod.discover_projects(base_dir, exclude_dirs=exclude) if base_dir else []

    while True:
        tui.clear()
        tui.print_banner("Continuar con un port existente", icon="📂",
                          subtitle=base_dir)
        if not projects:
            print(f"{C.YELLOW}No se detectó ningún port bajo '{base_dir}'.{C.RESET}\n")
        else:
            _print_project_table(projects)
            print()

        print(f"  {C.GREEN}[R]{C.RESET} Ingresar una ruta manualmente")
        print(f"  {C.RED}[0]{C.RESET} Volver")
        choice = input(f"\n{C.BOLD}Elegí un proyecto [1-{len(projects)}], R, o 0: {C.RESET}").strip().lower()

        if choice in ("0", "q", ""):
            return None
        if choice == "r":
            manual = tui.input_path("Ruta absoluta a la carpeta del port:", must_exist=True, is_dir=True)
            return _open_project(manual)
        try:
            idx = int(choice)
            if 1 <= idx <= len(projects):
                return _open_project(projects[idx - 1]["path"])
        except ValueError:
            pass
        print(f"{C.RED}Opción inválida.{C.RESET}")
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
            items.append((f"▶️  Continuar con el último port: {C.BOLD}{Path(last).name}{C.RESET}", wrap_action(continuar_ultimo)))
        items.append(("📂 Continuar con otro port existente (elegir de la lista / ruta manual)", wrap_action(continuar_lista)))
        items.append(("🆕 Crear un port NUEVO desde cero (APK Android -> PS Vita)", wrap_action(crear_nuevo)))
        items.append(("⚙️  Configuración global (rutas de BASE_DIR, VITASDK, etc.)", wrap_action(lambda: _edit_global_config(global_cfg))))
        items.append((f"{C.RED}❌ Salir{C.RESET}", None))

        idx = 0
        n = len(items)
        while True:
            tui.clear()
            tui.print_banner("PS VITA PORT TOOLKIT", subtitle="Android → PS Vita, de punta a punta", icon="🎮")
            print(f"{C.DIM}Base: {global_cfg.get('base_dir', '(sin configurar)')}{C.RESET}\n")
            for i, (label, _cb) in enumerate(items):
                prefix = f"{i + 1:2d}. " if i < 9 else "    "
                if i == idx:
                    print(f"{C.BLUE}{C.BOLD}\033[7m> {prefix}{label}{C.RESET}")
                else:
                    print(f"  {prefix}{label}")
            print(f"\n{C.DIM}↑/↓ mover · Enter elegir · Ctrl+C salir{C.RESET}")

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
    tui.print_banner("Configuración global", icon="⚙️")
    for key, desc in cfgmod.REQUIRED_GLOBAL_KEYS.items():
        current = global_cfg.get(key, "")
        raw = input(f"{C.BOLD}{desc}{C.RESET}\n[{current}] > ").strip()
        if raw:
            global_cfg[key] = raw
    cfgmod.save_global_config(global_cfg)
    print(f"\n{C.GREEN}[+] Configuración guardada.{C.RESET}")
    tui.pause()
