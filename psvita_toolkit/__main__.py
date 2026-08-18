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
from . import i18n
from . import livearea
from . import project
from . import tui
from . import utils
from .i18n import t
from .tui import C

STRINGS = {
    "main.status.dir_line": {
        "es": "📁 {project_dir}",
        "en": "📁 {project_dir}",
        "pt": "📁 {project_dir}",
    },
    "main.status.info_line": {
        "es": "🆔 TITLEID {titleid} · 📡 {vita_ip}:{vita_port} · 📦 {vpk_info}",
        "en": "🆔 TITLEID {titleid} · 📡 {vita_ip}:{vita_port} · 📦 {vpk_info}",
        "pt": "🆔 TITLEID {titleid} · 📡 {vita_ip}:{vita_port} · 📦 {vpk_info}",
    },
    "main.status.vpk_count": {
        "es": "{count} build(s) en {build_dir}/",
        "en": "{count} build(s) in {build_dir}/",
        "pt": "{count} build(s) em {build_dir}/",
    },
    "main.status.vpk_none": {
        "es": "sin builds todavía",
        "en": "no builds yet",
        "pt": "sem builds ainda",
    },
    "main.shaders.title": {
        "es": "Shaders",
        "en": "Shaders",
        "pt": "Shaders",
    },
    "main.shaders.breadcrumb": {
        "es": "{game_name} › Shaders",
        "en": "{game_name} › Shaders",
        "pt": "{game_name} › Shaders",
    },
    "main.shaders.sync": {
        "es": "Sincronizar (descargar GLSL sin traducir + subir CG traducidos)",
        "en": "Sync (download untranslated GLSL + upload translated CG)",
        "pt": "Sincronizar (baixar GLSL não traduzido + enviar CG traduzido)",
    },
    "main.shaders.download_glsl": {
        "es": "Descargar shaders GLSL volcados por la Vita",
        "en": "Download GLSL shaders dumped by the Vita",
        "pt": "Baixar shaders GLSL despejados pela Vita",
    },
    "main.shaders.clean_boilerplate": {
        "es": "Limpiar boilerplate GLES de los .glsl volcados (-> assets/cg/*.cg)",
        "en": "Strip GLES boilerplate from dumped .glsl files (-> assets/cg/*.cg)",
        "pt": "Limpar boilerplate GLES dos .glsl despejados (-> assets/cg/*.cg)",
    },
    "main.shaders.upload_cg": {
        "es": "Subir shaders .cg ya traducidos a la Vita",
        "en": "Upload already-translated .cg shaders to the Vita",
        "pt": "Enviar shaders .cg já traduzidos para a Vita",
    },
    "main.shaders.check_libshacccg": {
        "es": "Chequear libshacccg.suprx en la consola (tamaño/existencia)",
        "en": "Check libshacccg.suprx on the console (size/existence)",
        "pt": "Verificar libshacccg.suprx no console (tamanho/existência)",
    },
    "main.utils.title": {
        "es": "Utilidades",
        "en": "Utilities",
        "pt": "Utilitários",
    },
    "main.utils.breadcrumb": {
        "es": "{game_name} › Utilidades",
        "en": "{game_name} › Utilities",
        "pt": "{game_name} › Utilitários",
    },
    "main.utils.search_symbols_prompt": {
        "es": "Patrón (regex) a buscar en los símbolos dinámicos, ej. 'png_|Decode': ",
        "en": "Pattern (regex) to search dynamic symbols for, e.g. 'png_|Decode': ",
        "pt": "Padrão (regex) para buscar nos símbolos dinâmicos, ex. 'png_|Decode': ",
    },
    "main.utils.clean_macos_junk": {
        "es": "🧹 Limpiar basura de macOS (._*) en todo el proyecto",
        "en": "🧹 Clean macOS junk (._*) across the whole project",
        "pt": "🧹 Limpar lixo do macOS (._*) em todo o projeto",
    },
    "main.utils.decompile_all": {
        "es": "🔬 Re-correr decompilación completa (jadx + Ghidra/so-decompiler)",
        "en": "🔬 Re-run full decompilation (jadx + Ghidra/so-decompiler)",
        "pt": "🔬 Rodar novamente a decompilação completa (jadx + Ghidra/so-decompiler)",
    },
    "main.utils.run_tests": {
        "es": "🧪 Correr tests/run_tests.sh del proyecto (si existe)",
        "en": "🧪 Run the project's tests/run_tests.sh (if it exists)",
        "pt": "🧪 Rodar tests/run_tests.sh do projeto (se existir)",
    },
    "main.utils.search_symbols": {
        "es": "🔎 Buscar símbolos por patrón en los .so (readelf --dyn-syms)",
        "en": "🔎 Search symbols by pattern in the .so files (readelf --dyn-syms)",
        "pt": "🔎 Buscar símbolos por padrão nos .so (readelf --dyn-syms)",
    },
    "main.utils.verify_assets": {
        "es": "📂 Verificar assets de datos (conteo local vs. Vita por FTP)",
        "en": "📂 Verify data assets (local count vs. Vita via FTP)",
        "pt": "📂 Verificar assets de dados (contagem local vs. Vita via FTP)",
    },
    "main.utils.translate_docs": {
        "es": "🌐 Traducir los .md del proyecto en lote (deep-translator)",
        "en": "🌐 Batch-translate the project's .md files (deep-translator)",
        "pt": "🌐 Traduzir os .md do projeto em lote (deep-translator)",
    },
    "main.ask_reference_dir": {
        "es": "Carpeta local de referencia de assets (relativa al proyecto) [assets]: ",
        "en": "Local reference assets folder (relative to the project) [assets]: ",
        "pt": "Pasta local de referência de assets (relativa ao projeto) [assets]: ",
    },
    "main.ask_lang": {
        "es": "Idioma destino (código ISO, ej. en/es/pt) [en]: ",
        "en": "Target language (ISO code, e.g. en/es/pt) [en]: ",
        "pt": "Idioma de destino (código ISO, ex. en/es/pt) [en]: ",
    },
    "main.settings.title": {
        "es": "Configuración del proyecto",
        "en": "Project settings",
        "pt": "Configurações do projeto",
    },
    "main.settings.saved": {
        "es": "\n[+] Config del proyecto guardada.",
        "en": "\n[+] Project config saved.",
        "pt": "\n[+] Configuração do projeto salva.",
    },
    "main.automation.no_quartz": {
        "es": "[!] pyobjc no instalado -- pip install pyobjc para usar esto.",
        "en": "[!] pyobjc not installed -- pip install pyobjc to use this.",
        "pt": "[!] pyobjc não instalado -- pip install pyobjc para usar isto.",
    },
    "main.automation.click_x": {
        "es": "X: ",
        "en": "X: ",
        "pt": "X: ",
    },
    "main.automation.click_y": {
        "es": "Y: ",
        "en": "Y: ",
        "pt": "Y: ",
    },
    "main.automation.click_count": {
        "es": "Cantidad de clics [1]: ",
        "en": "Number of clicks [1]: ",
        "pt": "Quantidade de cliques [1]: ",
    },
    "main.automation.click_done": {
        "es": "[+] Clic hecho en ({x}, {y}).",
        "en": "[+] Click done at ({x}, {y}).",
        "pt": "[+] Clique feito em ({x}, {y}).",
    },
    "main.automation.double_click_done": {
        "es": "[+] Doble clic realizado sobre el primer juego de la biblioteca.",
        "en": "[+] Double-click done on the first game in the library.",
        "pt": "[+] Duplo clique realizado no primeiro jogo da biblioteca.",
    },
    "main.automation.click_coord": {
        "es": "Clic en una coordenada de pantalla",
        "en": "Click at a screen coordinate",
        "pt": "Clique em uma coordenada da tela",
    },
    "main.automation.double_click_game": {
        "es": "Doble clic en el primer juego de la biblioteca de Vita3K",
        "en": "Double-click the first game in the Vita3K library",
        "pt": "Duplo clique no primeiro jogo da biblioteca do Vita3K",
    },
    "main.automation.bring_front": {
        "es": "Traer Vita3K al frente",
        "en": "Bring Vita3K to the front",
        "pt": "Trazer o Vita3K para frente",
    },
    "main.automation.title": {
        "es": "Automatización Vita3K (Quartz)",
        "en": "Vita3K Automation (Quartz)",
        "pt": "Automação do Vita3K (Quartz)",
    },
    "main.menu.build_deploy": {
        "es": "🔨 Compilar y Desplegar (asistente: destino + preset + despliegue)",
        "en": "🔨 Build and Deploy (wizard: target + preset + deploy)",
        "pt": "🔨 Compilar e Implantar (assistente: destino + preset + implantação)",
    },
    "main.menu.redeploy": {
        "es": "🎮 Re-desplegar el último build en Vita3K (sin recompilar)",
        "en": "🎮 Redeploy the last build to Vita3K (no rebuild)",
        "pt": "🎮 Reimplantar o último build no Vita3K (sem recompilar)",
    },
    "main.menu.upload_eboot": {
        "es": "⚡ Subir SOLO eboot.bin a la PS Vita (rápido)",
        "en": "⚡ Upload ONLY eboot.bin to the PS Vita (fast)",
        "pt": "⚡ Enviar SOMENTE o eboot.bin para a PS Vita (rápido)",
    },
    "main.menu.upload_vpk": {
        "es": "📦 Subir VPK completo a la PS Vita (ux0:downloads/)",
        "en": "📦 Upload the full VPK to the PS Vita (ux0:downloads/)",
        "pt": "📦 Enviar o VPK completo para a PS Vita (ux0:downloads/)",
    },
    "main.menu.download_logs": {
        "es": "📥 Descargar logs / crash dumps (último, elegir uno, o ver historial local)",
        "en": "📥 Download logs / crash dumps (latest, pick one, or view local history)",
        "pt": "📥 Baixar logs / crash dumps (o último, escolher um, ou ver histórico local)",
    },
    "main.menu.analyze_crash": {
        "es": "🔍 Analizar un crash dump ya descargado",
        "en": "🔍 Analyze an already-downloaded crash dump",
        "pt": "🔍 Analisar um crash dump já baixado",
    },
    "main.menu.livearea": {
        "es": "🎨 LiveArea (bg0/pic0/icon0/startup)",
        "en": "🎨 LiveArea (bg0/pic0/icon0/startup)",
        "pt": "🎨 LiveArea (bg0/pic0/icon0/startup)",
    },
    "main.menu.shaders": {
        "es": "🧩 Shaders (GLSL <-> CG)",
        "en": "🧩 Shaders (GLSL <-> CG)",
        "pt": "🧩 Shaders (GLSL <-> CG)",
    },
    "main.menu.utilities": {
        "es": "🧰 Utilidades (limpieza, decompilar, tests, símbolos, assets, docs)",
        "en": "🧰 Utilities (cleanup, decompile, tests, symbols, assets, docs)",
        "pt": "🧰 Utilitários (limpeza, decompilar, testes, símbolos, assets, docs)",
    },
    "main.menu.automation": {
        "es": "🖱️  Automatización Vita3K (clics/teclado simulados, macOS)",
        "en": "🖱️  Vita3K Automation (simulated clicks/keyboard, macOS)",
        "pt": "🖱️  Automação do Vita3K (cliques/teclado simulados, macOS)",
    },
    "main.menu.project_settings": {
        "es": "⚙️  Configuración de este proyecto",
        "en": "⚙️  Settings for this project",
        "pt": "⚙️  Configurações deste projeto",
    },
    "main.menu.switch_project": {
        "es": "🔁 Cambiar de proyecto / crear otro port",
        "en": "🔁 Switch project / create another port",
        "pt": "🔁 Trocar de projeto / criar outro port",
    },
    "main.menu.exit": {
        "es": "❌ Salir del toolkit",
        "en": "❌ Exit the toolkit",
        "pt": "❌ Sair da ferramenta",
    },
    "main.menu.subtitle": {
        "es": "Android → PS Vita",
        "en": "Android → PS Vita",
        "pt": "Android → PS Vita",
    },
    "main.farewell": {
        "es": "👋 ¡Hasta luego!",
        "en": "👋 See you later!",
        "pt": "👋 Até logo!",
    },
}
i18n.register(STRINGS)


def _status_header(project_cfg):
    def render():
        project_dir = Path(project_cfg["_project_dir"])
        vpks = ftp_ops.list_local_vpks(project_dir, project_cfg.get("build_dir", "build"))
        vpk_info = (
            t("main.status.vpk_count", count=len(vpks), build_dir=project_cfg.get("build_dir", "build"))
            if vpks
            else t("main.status.vpk_none")
        )
        print(f"{C.DIM}{t('main.status.dir_line', project_dir=project_dir)}{C.RESET}")
        print(f"{C.DIM}{t('main.status.info_line', titleid=project_cfg['titleid'], vita_ip=project_cfg['vita_ip'], vita_port=project_cfg.get('vita_port', 1337), vpk_info=vpk_info)}{C.RESET}")
    return render


def _shaders_submenu(project_cfg, global_cfg):
    items = [
        (t("main.shaders.sync"), lambda: ftp_ops.sync_shaders(project_cfg, global_cfg)),
        (t("main.shaders.download_glsl"), lambda: ftp_ops.download_glsl_shaders(project_cfg, global_cfg)),
        (t("main.shaders.clean_boilerplate"), lambda: utils.translate_shaders_boilerplate(project_cfg)),
        (t("main.shaders.upload_cg"), lambda: ftp_ops.upload_cg_shaders(project_cfg, global_cfg)),
        (t("main.shaders.check_libshacccg"), lambda: ftp_ops.check_libshacccg(project_cfg, global_cfg)),
    ]
    tui.run_menu(
        t("main.shaders.title"),
        items,
        breadcrumb=t("main.shaders.breadcrumb", game_name=project_cfg["game_name"]),
        icon="🧩",
    )


def _utils_submenu(project_cfg, global_cfg):
    def do_search_symbols():
        pattern = input(t("main.utils.search_symbols_prompt")).strip()
        if pattern:
            utils.search_symbols(project_cfg, global_cfg, pattern)

    items = [
        (t("main.utils.clean_macos_junk"), lambda: utils.clean_macos_junk(project_cfg["_project_dir"])),
        (t("main.utils.decompile_all"), lambda: utils.decompile_all(project_cfg, global_cfg)),
        (t("main.utils.run_tests"), lambda: utils.run_project_tests(project_cfg)),
        (t("main.utils.search_symbols"), do_search_symbols),
        (t("main.utils.verify_assets"),
         lambda: ftp_ops.verify_data_assets(project_cfg, global_cfg, _ask_reference_dir())),
        (t("main.utils.translate_docs"), lambda: utils.translate_docs(project_cfg, _ask_lang())),
    ]
    tui.run_menu(
        t("main.utils.title"),
        items,
        breadcrumb=t("main.utils.breadcrumb", game_name=project_cfg["game_name"]),
        icon="🧰",
    )


def _ask_reference_dir():
    return input(t("main.ask_reference_dir")).strip() or "assets"


def _ask_lang():
    # Nota: esto pregunta el idioma DESTINO para utils.translate_docs() (que
    # traduce los .md del proyecto que se está porteando), no tiene relación
    # con el idioma de la UI de este toolkit (ese es i18n.set_language()).
    return input(t("main.ask_lang")).strip() or "en"


def _project_settings(project_cfg):
    tui.clear()
    tui.print_banner(t("main.settings.title"), icon="⚙️", subtitle=project_cfg["_project_dir"])
    editable = ["game_name", "vita_ip", "vita_port", "titleid", "build_dir",
                "vita_downloads_dir", "vita_data_dir", "vita_logs_dir", "vita_cg_dir", "vita_glsl_dir"]
    for key in editable:
        current = project_cfg.get(key, "")
        raw = input(f"{C.BOLD}{key}{C.RESET} [{current}]: ").strip()
        if raw:
            project_cfg[key] = int(raw) if key == "vita_port" and raw.isdigit() else raw
    cfgmod.save_project_config(project_cfg["_project_dir"], project_cfg)
    print(f"{C.GREEN}{t('main.settings.saved')}{C.RESET}")


def _automation_submenu():
    if not automation_mac.HAVE_QUARTZ:
        print(f"{C.YELLOW}{t('main.automation.no_quartz')}{C.RESET}")
        return

    def do_click():
        x = float(input(t("main.automation.click_x")).strip())
        y = float(input(t("main.automation.click_y")).strip())
        n = input(t("main.automation.click_count")).strip()
        automation_mac.click(x, y, int(n) if n else 1)
        print(f"{C.GREEN}{t('main.automation.click_done', x=x, y=y)}{C.RESET}")

    def do_double_click_game():
        automation_mac.bring_to_front("Vita3K")
        if automation_mac.double_click_first_game_row():
            print(f"{C.GREEN}{t('main.automation.double_click_done')}{C.RESET}")

    items = [
        (t("main.automation.click_coord"), do_click),
        (t("main.automation.double_click_game"), do_double_click_game),
        (t("main.automation.bring_front"), lambda: automation_mac.bring_to_front("Vita3K")),
    ]
    tui.run_menu(t("main.automation.title"), items, icon="🖱️")


def _raise_switch_project():
    raise tui.SwitchProject()


def _raise_exit_app():
    raise tui.ExitApp()


def show_project_menu(project_cfg, global_cfg):
    items = [
        (t("main.menu.build_deploy"),
         lambda: build_deploy.build_and_deploy_wizard(project_cfg, global_cfg)),
        (t("main.menu.redeploy"),
         lambda: build_deploy.deploy_only_vita3k(project_cfg, global_cfg)),
        (t("main.menu.upload_eboot"), lambda: ftp_ops.upload_eboot(project_cfg, global_cfg)),
        (t("main.menu.upload_vpk"), lambda: ftp_ops.upload_vpk(project_cfg, global_cfg)),
        (t("main.menu.download_logs"),
         lambda: ftp_ops.download_logs_and_dumps(project_cfg, global_cfg)),
        (t("main.menu.analyze_crash"), lambda: crash_analyzer.analyze_menu(project_cfg, global_cfg)),
        (t("main.menu.livearea"), lambda: livearea.livearea_menu(project_cfg)),
        (t("main.menu.shaders"), lambda: _shaders_submenu(project_cfg, global_cfg)),
        (t("main.menu.utilities"),
         lambda: _utils_submenu(project_cfg, global_cfg)),
        (t("main.menu.automation"), _automation_submenu),
        (t("main.menu.project_settings"), lambda: _project_settings(project_cfg)),
        (t("main.menu.switch_project"), _raise_switch_project),
        (f"{C.RED}{t('main.menu.exit')}{C.RESET}", _raise_exit_app),
    ]

    while True:
        try:
            tui.run_menu(
                f"{project_cfg['game_name']}",
                items,
                breadcrumb=project_cfg["game_name"],
                subtitle=t("main.menu.subtitle"),
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
    print(f"{C.GREEN}{t('main.farewell')}{C.RESET}\n")


if __name__ == "__main__":
    main()
