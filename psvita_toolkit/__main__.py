"""
Punto de entrada. Ejecutar con:

    python3 -m psvita_toolkit
    (o el shim bin/psvita-toolkit, que hace exactamente esto)

Flujo:
  1. Config global (una sola vez, después queda guardada).
  2. Selector de proyecto: continuar con el último, elegir otro de la lista
     detectada bajo BASE_DIR, ruta manual, o crear un port nuevo desde cero.
  3. Menú principal del proyecto -- todo navegable, con 'M' para volver acá
     desde cualquier submenú y una opción explícita para cambiar de proyecto.
"""

from pathlib import Path

from . import automation_mac
from . import build_deploy
from . import config as cfgmod
from . import crash_analyzer
from . import ftp_ops
from . import livearea
from . import project
from . import tui
from . import utils
from .tui import C


def _status_header(project_cfg):
    def render():
        project_dir = Path(project_cfg["_project_dir"])
        vpks = ftp_ops.list_local_vpks(project_dir, project_cfg.get("build_dir", "build"))
        vpk_info = f"{len(vpks)} build(s) en {project_cfg.get('build_dir', 'build')}/" if vpks else "sin builds todavía"
        print(f"{C.DIM}📁 {project_dir}{C.RESET}")
        print(f"{C.DIM}🆔 TITLEID {project_cfg['titleid']} · 📡 {project_cfg['vita_ip']}:{project_cfg.get('vita_port', 1337)} "
              f"· 📦 {vpk_info}{C.RESET}")
    return render


def _shaders_submenu(project_cfg, global_cfg):
    items = [
        ("Sincronizar (descargar GLSL sin traducir + subir CG traducidos)",
         lambda: ftp_ops.sync_shaders(project_cfg, global_cfg)),
        ("Descargar shaders GLSL volcados por la Vita", lambda: ftp_ops.download_glsl_shaders(project_cfg, global_cfg)),
        ("Limpiar boilerplate GLES de los .glsl volcados (-> assets/cg/*.cg)",
         lambda: utils.translate_shaders_boilerplate(project_cfg)),
        ("Subir shaders .cg ya traducidos a la Vita", lambda: ftp_ops.upload_cg_shaders(project_cfg, global_cfg)),
        ("Chequear libshacccg.suprx en la consola (tamaño/existencia)",
         lambda: ftp_ops.check_libshacccg(project_cfg, global_cfg)),
    ]
    tui.run_menu("Shaders", items, breadcrumb=f"{project_cfg['game_name']} › Shaders", icon="🧩")


def _utils_submenu(project_cfg, global_cfg):
    def do_search_symbols():
        pattern = input("Patrón (regex) a buscar en los símbolos dinámicos, ej. 'png_|Decode': ").strip()
        if pattern:
            utils.search_symbols(project_cfg, global_cfg, pattern)

    items = [
        ("🧹 Limpiar basura de macOS (._*) en todo el proyecto", lambda: utils.clean_macos_junk(project_cfg["_project_dir"])),
        ("🔬 Re-correr decompilación completa (jadx + Ghidra/so-decompiler)", lambda: utils.decompile_all(project_cfg, global_cfg)),
        ("🧪 Correr tests/run_tests.sh del proyecto (si existe)", lambda: utils.run_project_tests(project_cfg)),
        ("🔎 Buscar símbolos por patrón en los .so (readelf --dyn-syms)", do_search_symbols),
        ("📂 Verificar assets de datos (conteo local vs. Vita por FTP)",
         lambda: ftp_ops.verify_data_assets(project_cfg, global_cfg, _ask_reference_dir())),
        ("🌐 Traducir los .md del proyecto en lote (deep-translator)", lambda: utils.translate_docs(project_cfg, _ask_lang())),
    ]
    tui.run_menu("Utilidades", items, breadcrumb=f"{project_cfg['game_name']} › Utilidades", icon="🧰")


def _ask_reference_dir():
    return input("Carpeta local de referencia de assets (relativa al proyecto) [assets]: ").strip() or "assets"


def _ask_lang():
    return input("Idioma destino (código ISO, ej. en/es/pt) [en]: ").strip() or "en"


def _project_settings(project_cfg):
    tui.clear()
    tui.print_banner("Configuración del proyecto", icon="⚙️", subtitle=project_cfg["_project_dir"])
    editable = ["game_name", "vita_ip", "vita_port", "titleid", "build_dir",
                "vita_downloads_dir", "vita_data_dir", "vita_logs_dir", "vita_cg_dir", "vita_glsl_dir"]
    for key in editable:
        current = project_cfg.get(key, "")
        raw = input(f"{C.BOLD}{key}{C.RESET} [{current}]: ").strip()
        if raw:
            project_cfg[key] = int(raw) if key == "vita_port" and raw.isdigit() else raw
    cfgmod.save_project_config(project_cfg["_project_dir"], project_cfg)
    print(f"\n{C.GREEN}[+] Config del proyecto guardada.{C.RESET}")


def _automation_submenu():
    if not automation_mac.HAVE_QUARTZ:
        print(f"{C.YELLOW}[!] pyobjc no instalado -- pip install pyobjc para usar esto.{C.RESET}")
        return

    def do_click():
        x = float(input("X: ").strip())
        y = float(input("Y: ").strip())
        n = input("Cantidad de clics [1]: ").strip()
        automation_mac.click(x, y, int(n) if n else 1)
        print(f"{C.GREEN}[+] Clic hecho en ({x}, {y}).{C.RESET}")

    def do_double_click_game():
        automation_mac.bring_to_front("Vita3K")
        if automation_mac.double_click_first_game_row():
            print(f"{C.GREEN}[+] Doble clic realizado sobre el primer juego de la biblioteca.{C.RESET}")

    items = [
        ("Clic en una coordenada de pantalla", do_click),
        ("Doble clic en el primer juego de la biblioteca de Vita3K", do_double_click_game),
        ("Traer Vita3K al frente", lambda: automation_mac.bring_to_front("Vita3K")),
    ]
    tui.run_menu("Automatización Vita3K (Quartz)", items, icon="🖱️")


def _raise_switch_project():
    raise tui.SwitchProject()


def _raise_exit_app():
    raise tui.ExitApp()


def show_project_menu(project_cfg, global_cfg):
    items = [
        ("🔨 Compilar y Desplegar (asistente: destino + preset + despliegue)",
         lambda: build_deploy.build_and_deploy_wizard(project_cfg, global_cfg)),
        ("🎮 Re-desplegar el último build en Vita3K (sin recompilar)",
         lambda: build_deploy.deploy_only_vita3k(project_cfg, global_cfg)),
        ("⚡ Subir SOLO eboot.bin a la PS Vita (rápido)", lambda: ftp_ops.upload_eboot(project_cfg, global_cfg)),
        ("📦 Subir VPK completo a la PS Vita (ux0:downloads/)", lambda: ftp_ops.upload_vpk(project_cfg, global_cfg)),
        ("📥 Descargar logs / crash dumps (último, elegir uno, o ver historial local)",
         lambda: ftp_ops.download_logs_and_dumps(project_cfg, global_cfg)),
        ("🔍 Analizar un crash dump ya descargado", lambda: crash_analyzer.analyze_menu(project_cfg, global_cfg)),
        ("🎨 LiveArea (bg0/pic0/icon0/startup)", lambda: livearea.livearea_menu(project_cfg)),
        ("🧩 Shaders (GLSL <-> CG)", lambda: _shaders_submenu(project_cfg, global_cfg)),
        ("🧰 Utilidades (limpieza, decompilar, tests, símbolos, assets, docs)",
         lambda: _utils_submenu(project_cfg, global_cfg)),
        ("🖱️  Automatización Vita3K (clics/teclado simulados, macOS)", _automation_submenu),
        ("⚙️  Configuración de este proyecto", lambda: _project_settings(project_cfg)),
        ("🔁 Cambiar de proyecto / crear otro port", _raise_switch_project),
        (f"{C.RED}❌ Salir del toolkit{C.RESET}", _raise_exit_app),
    ]

    while True:
        try:
            tui.run_menu(
                f"{project_cfg['game_name']}",
                items,
                breadcrumb=project_cfg["game_name"],
                subtitle="Android → PS Vita",
                icon="🎮",
                header_extra=_status_header(project_cfg),
            )
            # 0/Q en el menú principal: no hay a dónde volver salvo el
            # selector de proyectos.
            raise tui.SwitchProject()
        except tui.GoToMainMenu:
            continue


def main():
    global_cfg = cfgmod.ensure_global_config(tui)

    while True:
        try:
            project_cfg = project.select_or_create_project(global_cfg)
        except tui.ExitApp:
            break
        if project_cfg is None:
            break

        try:
            show_project_menu(project_cfg, global_cfg)
        except tui.SwitchProject:
            continue
        except tui.ExitApp:
            break

    tui.clear()
    print(f"{C.GREEN}👋 ¡Hasta luego!{C.RESET}\n")


if __name__ == "__main__":
    main()
