"""!
@file __main__.py
@brief Entry point. Run with `python3 -m psvita_toolkit` (or the `bin/psvita-toolkit` shim).

@details
Flow:
  1. Global config (asked once, then persisted).
  2. Project selector: continue with the last one, pick another from the
     list detected under `BASE_DIR`, a manual path, or create a new port
     from scratch.
  3. Main project menu -- fully navigable, with `M` returning here from any
     submenu and an explicit option to switch project.

See `docs/dev-notes/__main__.md` for the rationale behind this structure.
"""

from pathlib import Path

from . import build_deploy
from . import config as cfgmod
from . import context_feeder
from . import crash_analyzer
from . import dashboard
from . import debugnet_server
from . import doctor
from . import ecosystem
from . import ftp_ops
from . import i18n
from . import jni_analyzer
from . import livearea
from . import mem_align_analyzer
from . import mem_profiler
from . import project
from . import so_patcher
from . import tui
from . import utils
from . import zenonia2_tools
from .i18n import t
from .tui import C

STRINGS = {
    "main.status.dir_line": {
        "es": "{project_dir}",
        "en": "{project_dir}",
        "pt": "{project_dir}",
    },
    "main.status.info_line": {
        "es": "TITLEID {titleid} · {vita_ip}:{vita_port} · {vpk_info}",
        "en": "TITLEID {titleid} · {vita_ip}:{vita_port} · {vpk_info}",
        "pt": "TITLEID {titleid} · {vita_ip}:{vita_port} · {vpk_info}",
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
    "main.shaders.gen_uniforms": {
        "es": "Generar esqueletos de uniforms/samplers en C (desde glsl_dump/)",
        "en": "Generate C uniform/sampler skeletons (from glsl_dump/)",
        "pt": "Gerar esqueletos de uniforms/samplers em C (a partir de glsl_dump/)",
    },
    "main.shaders.validate": {
        "es": "Validar shaders .cg con psp2cgc (antes de subir por FTP)",
        "en": "Validate .cg shaders with psp2cgc (before uploading over FTP)",
        "pt": "Validar shaders .cg com psp2cgc (antes de enviar por FTP)",
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
        "es": "Limpiar basura de macOS (._*) en todo el proyecto",
        "en": "Clean macOS junk (._*) across the whole project",
        "pt": "Limpar lixo do macOS (._*) em todo o projeto",
    },
    "main.utils.decompile_all": {
        "es": "Re-correr decompilación completa (jadx + Ghidra/so-decompiler)",
        "en": "Re-run full decompilation (jadx + Ghidra/so-decompiler)",
        "pt": "Rodar novamente a decompilação completa (jadx + Ghidra/so-decompiler)",
    },
    "main.utils.run_tests": {
        "es": "Correr tests/run_tests.sh del proyecto (si existe)",
        "en": "Run the project's tests/run_tests.sh (if it exists)",
        "pt": "Rodar tests/run_tests.sh do projeto (se existir)",
    },
    "main.utils.search_symbols": {
        "es": "Buscar símbolos por patrón en los .so (readelf --dyn-syms)",
        "en": "Search symbols by pattern in the .so files (readelf --dyn-syms)",
        "pt": "Buscar símbolos por padrão nos .so (readelf --dyn-syms)",
    },
    "main.utils.verify_assets": {
        "es": "Verificar assets de datos (conteo local vs. Vita por FTP)",
        "en": "Verify data assets (local count vs. Vita via FTP)",
        "pt": "Verificar assets de dados (contagem local vs. Vita via FTP)",
    },
    "main.utils.translate_docs": {
        "es": "Traducir los .md del proyecto en lote (deep-translator)",
        "en": "Batch-translate the project's .md files (deep-translator)",
        "pt": "Traduzir os .md do projeto em lote (deep-translator)",
    },
    "main.utils.gen_docs": {
        "es": "Generar documentación del toolkit (skeletons + API markdown)",
        "en": "Generate toolkit documentation (skeletons + API markdown)",
        "pt": "Gerar documentação do toolkit (skeletons + API markdown)",
    },
    "main.utils.doctor": {
        "es": "Doctor -- chequear el entorno (VITASDK, Docker, jadx, CMake...)",
        "en": "Doctor -- check the environment (VITASDK, Docker, jadx, CMake...)",
        "pt": "Doctor -- verificar o ambiente (VITASDK, Docker, jadx, CMake...)",
    },
    "main.utils.detect_middleware": {
        "es": "Detectar middleware conocido en el .so (FMOD, OpenAL, Box2D...)",
        "en": "Detect known middleware in the .so (FMOD, OpenAL, Box2D...)",
        "pt": "Detectar middleware conhecido no .so (FMOD, OpenAL, Box2D...)",
    },
    "main.utils.gen_jni_stubs": {
        "es": "Generar candidatos de stubs JNI (generated_jni_table.h / _stubs.c)",
        "en": "Generate JNI stub candidates (generated_jni_table.h / _stubs.c)",
        "pt": "Gerar candidatos de stubs JNI (generated_jni_table.h / _stubs.c)",
    },
    "main.utils.doc_lifecycle": {
        "es": "Documentar métodos de ciclo de vida detectados en PORTING_PLAN.md",
        "en": "Document detected lifecycle methods in PORTING_PLAN.md",
        "pt": "Documentar métodos de ciclo de vida detectados no PORTING_PLAN.md",
    },
    "main.utils.so_patcher": {
        "es": "Auto-parcheo y neutralización de SDKs de telemetría/IAP",
        "en": "Auto-patching and telemetry/IAP SDK neutralization",
        "pt": "Auto-patch e neutralização de SDKs de telemetria/IAP",
    },
    "main.utils.align_check": {
        "es": "Analizador de alineación de memoria ARMv7 (ldrd/vld1/...)",
        "en": "ARMv7 memory alignment analyzer (ldrd/vld1/...)",
        "pt": "Analisador de alinhamento de memória ARMv7 (ldrd/vld1/...)",
    },
    "main.utils.export_context": {
        "es": "Exportar contexto de crash para Copiloto IA (Claude/Gemini/LLMs)",
        "en": "Export crash context for AI Copilot (Claude/Gemini/LLMs)",
        "pt": "Exportar contexto de crash para Copiloto IA (Claude/Gemini/LLMs)",
    },
    "main.menu.ecosystem": {
        "es": "Ecosistema Multi-Port (familias de motor, sincronización y estado)",
        "en": "Multi-Port Ecosystem (engine families, sync, and status)",
        "pt": "Ecossistema Multi-Port (famílias de motor, sincronização e status)",
    },
    "main.menu.mem_profiler": {
        "es": "Profiler de Memoria en Vivo (heap, fugas -- consola real)",
        "en": "Live Memory Profiler (heap, leaks -- real console)",
        "pt": "Profiler de Memória em Tempo Real (heap, fugas -- console real)",
    },
    "main.menu.dashboard": {
        "es": "Web Dashboard Local (logs, estado, crashes, assets, touch mapper)",
        "en": "Local Web Dashboard (logs, status, crashes, assets, touch mapper)",
        "pt": "Web Dashboard Local (logs, status, crashes, assets, touch mapper)",
    },
    "main.ask_reference_dir": {
        "es": "Carpeta local de referencia de assets (relativa al proyecto) [assets]: ",
        "en": "Local reference assets folder (relative to the project) [assets]: ",
        "pt": "Pasta local de referência de assets (relativa ao projeto) [assets]: ",
    },
    "main.ask_docs_path": {
        "es": "Ruta a traducir (archivo o carpeta, relativa o absoluta) [docs]: ",
        "en": "Path to translate (file or folder, relative or absolute) [docs]: ",
        "pt": "Caminho a traduzir (arquivo ou pasta, relativo ou absoluto) [docs]: ",
    },
    "main.ask_docs_overwrite": {
        "es": "¿Sobrescribir los archivos originales directamente? (s/N): ",
        "en": "Overwrite original files in place? (y/N): ",
        "pt": "Sobrescrever os arquivos originais diretamente? (s/N): ",
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
    "main.menu.build_deploy": {
        "es": "Compilar y Desplegar (asistente: destino + preset + despliegue)",
        "en": "Build and Deploy (wizard: target + preset + deploy)",
        "pt": "Compilar e Implantar (assistente: destino + preset + implantação)",
    },
    "main.menu.upload_eboot": {
        "es": "Subir SOLO eboot.bin a la PS Vita (rápido)",
        "en": "Upload ONLY eboot.bin to the PS Vita (fast)",
        "pt": "Enviar SOMENTE o eboot.bin para a PS Vita (rápido)",
    },
    "main.menu.upload_vpk": {
        "es": "Subir VPK completo a la PS Vita (ux0:downloads/)",
        "en": "Upload the full VPK to the PS Vita (ux0:downloads/)",
        "pt": "Enviar o VPK completo para a PS Vita (ux0:downloads/)",
    },
    "main.menu.download_logs": {
        "es": "Descargar logs / crash dumps (último, elegir uno, o ver historial local)",
        "en": "Download logs / crash dumps (latest, pick one, or view local history)",
        "pt": "Baixar logs / crash dumps (o último, escolher um, ou ver histórico local)",
    },
    "main.menu.analyze_crash": {
        "es": "Analizar un crash dump ya descargado",
        "en": "Analyze an already-downloaded crash dump",
        "pt": "Analisar um crash dump já baixado",
    },
    "main.menu.live_logs": {
        "es": "Servidor de logs en vivo (UDP debugnet)",
        "en": "Live log server (UDP debugnet)",
        "pt": "Servidor de logs em tempo real (UDP debugnet)",
    },
    "main.menu.livearea": {
        "es": "LiveArea (bg0/pic0/icon0/startup)",
        "en": "LiveArea (bg0/pic0/icon0/startup)",
        "pt": "LiveArea (bg0/pic0/icon0/startup)",
    },
    "main.menu.shaders": {
        "es": "Shaders (GLSL <-> CG)",
        "en": "Shaders (GLSL <-> CG)",
        "pt": "Shaders (GLSL <-> CG)",
    },
    "main.menu.utilities": {
        "es": "Utilidades (limpieza, decompilar, tests, símbolos, assets, docs)",
        "en": "Utilities (cleanup, decompile, tests, symbols, assets, docs)",
        "pt": "Utilitários (limpeza, decompilar, testes, símbolos, assets, docs)",
    },
    "main.menu.console_profiles": {
        "es": "Perfiles de consola (OLED / Slim / PSTV / ...)",
        "en": "Console profiles (OLED / Slim / PSTV / ...)",
        "pt": "Perfis de console (OLED / Slim / PSTV / ...)",
    },
    "main.menu.project_settings": {
        "es": "Configuración de este proyecto",
        "en": "Settings for this project",
        "pt": "Configurações deste projeto",
    },
    "main.menu.switch_project": {
        "es": "Cambiar de proyecto / crear otro port",
        "en": "Switch project / create another port",
        "pt": "Trocar de projeto / criar outro port",
    },
    "main.menu.exit": {
        "es": "Salir del toolkit",
        "en": "Exit the toolkit",
        "pt": "Sair da ferramenta",
    },
    "main.menu.subtitle": {
        "es": "Android → PS Vita",
        "en": "Android → PS Vita",
        "pt": "Android → PS Vita",
    },
    "main.farewell": {
        "es": "¡Hasta luego!",
        "en": "See you later!",
        "pt": "Até logo!",
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
        (t("main.shaders.gen_uniforms"), lambda: utils.generate_uniform_skeletons(project_cfg)),
        (t("main.shaders.validate"), lambda: utils.validate_all_shaders(project_cfg, global_cfg)),
        (t("main.shaders.upload_cg"), lambda: ftp_ops.upload_cg_shaders(project_cfg, global_cfg)),
        (t("main.shaders.check_libshacccg"), lambda: ftp_ops.check_libshacccg(project_cfg, global_cfg)),
    ]
    tui.run_menu(
        t("main.shaders.title"),
        items,
        breadcrumb=t("main.shaders.breadcrumb", game_name=project_cfg["game_name"]),
    )


def _utils_submenu(project_cfg, global_cfg):
    def do_search_symbols():
        pattern = input(t("main.utils.search_symbols_prompt")).strip()
        if pattern:
            utils.search_symbols(project_cfg, global_cfg, pattern)

    def do_translate_docs():
        raw_path = input(t("main.ask_docs_path")).strip()
        # Strip single/double quotes and extra whitespace (common when drag-and-dropping paths into terminal)
        target_path = raw_path.strip("'\"") if raw_path else "docs"
        lang = _ask_lang()
        overwrite_ans = input(t("main.ask_docs_overwrite")).strip().lower()
        overwrite = overwrite_ans in ("s", "si", "y", "yes")
        utils.translate_docs(project_cfg, target_lang=lang, custom_path=target_path, overwrite=overwrite)

    items = [
        (t("main.utils.doctor"), lambda: doctor.run_doctor(global_cfg)),
        (t("main.utils.so_patcher"), lambda: so_patcher.patch_menu(project_cfg, global_cfg)),
        (t("main.utils.align_check"), lambda: mem_align_analyzer.alignment_menu(project_cfg, global_cfg)),
        (t("main.utils.export_context"), lambda: context_feeder.export_context_menu(project_cfg, global_cfg)),
        (t("main.utils.clean_macos_junk"), lambda: utils.clean_macos_junk(project_cfg["_project_dir"])),
        (t("main.utils.decompile_all"), lambda: utils.decompile_all(project_cfg, global_cfg)),
        (t("main.utils.run_tests"), lambda: utils.run_project_tests(project_cfg)),
        (t("main.utils.search_symbols"), do_search_symbols),
        (t("main.utils.verify_assets"),
         lambda: ftp_ops.verify_data_assets(project_cfg, global_cfg, _ask_reference_dir())),
        (t("main.utils.translate_docs"), do_translate_docs),
        (t("main.utils.gen_docs"), lambda: utils.generate_toolkit_docs(project_cfg)),
        (t("main.utils.detect_middleware"), lambda: jni_analyzer.middleware_report(project_cfg)),
        (t("main.utils.gen_jni_stubs"), lambda: jni_analyzer.generate_jni_stubs(project_cfg)),
        (t("main.utils.doc_lifecycle"), lambda: jni_analyzer.document_lifecycle_in_plan(project_cfg)),
    ]
    if project_cfg.get("slug") == "zenonia-2" or project_cfg.get("titleid") == "PSVZ00002" or "zenonia" in project_cfg.get("game_name", "").lower():
        items.append((t("zen2.menu_title"), lambda: zenonia2_tools.zenonia2_menu(project_cfg)))

    tui.run_menu(
        t("main.utils.title"),
        items,
        breadcrumb=t("main.utils.breadcrumb", game_name=project_cfg["game_name"]),
    )


def _ask_reference_dir():
    return input(t("main.ask_reference_dir")).strip() or "assets"


def _ask_lang():
    # Note: this asks for the TARGET language for utils.translate_docs() (which
    # translates the .md files of the project being ported), unrelated to this
    # toolkit's own UI language (that one is i18n.set_language()).
    return input(t("main.ask_lang")).strip() or "en"


def _project_settings(project_cfg):
    tui.clear()
    tui.print_banner(t("main.settings.title"), subtitle=project_cfg["_project_dir"])
    editable = ["game_name", "vita_ip", "vita_port", "titleid", "build_dir",
                "vita_downloads_dir", "vita_data_dir", "vita_logs_dir", "vita_cg_dir", "vita_glsl_dir"]
    for key in editable:
        current = project_cfg.get(key, "")
        raw = input(f"{C.BOLD}{key}{C.RESET} [{current}]: ").strip()
        if raw:
            project_cfg[key] = int(raw) if key == "vita_port" and raw.isdigit() else raw
    cfgmod.save_project_config(project_cfg["_project_dir"], project_cfg)
    print(f"{C.GREEN}{t('main.settings.saved')}{C.RESET}")


def _raise_switch_project():
    raise tui.SwitchProject()


def _raise_exit_app():
    raise tui.ExitApp()


def show_project_menu(project_cfg, global_cfg):
    """!
    @brief Drive the active project's main menu until the user switches
           project or exits.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    @note Control-flow contract: this loop catches `tui.GoToMainMenu` raised
          by any nested submenu/action and simply redraws this same menu --
          that's what makes pressing `M` (or Ctrl+C) from any depth return
          here. Backing out of the menu itself (0/Q) has nowhere shallower to
          go, so it raises `tui.SwitchProject` to hand control back to
          `main()`'s project selector. `tui.ExitApp` is not caught here; it
          propagates up to `main()`.
    """
    items = [
        (t("main.menu.build_deploy"),
         lambda: build_deploy.build_and_deploy_wizard(project_cfg, global_cfg)),
        (t("main.menu.upload_eboot"), lambda: ftp_ops.upload_eboot(project_cfg, global_cfg)),
        (t("main.menu.upload_vpk"), lambda: ftp_ops.upload_vpk(project_cfg, global_cfg)),
        (t("main.menu.download_logs"),
         lambda: ftp_ops.download_logs_and_dumps(project_cfg, global_cfg)),
        (t("main.menu.analyze_crash"), lambda: crash_analyzer.analyze_menu(project_cfg, global_cfg)),
        (t("main.menu.live_logs"), lambda: debugnet_server.live_log_menu(project_cfg)),
        (t("main.menu.livearea"), lambda: livearea.livearea_menu(project_cfg)),
        (t("main.menu.shaders"), lambda: _shaders_submenu(project_cfg, global_cfg)),
        (t("main.menu.ecosystem"), lambda: ecosystem.ecosystem_menu(project_cfg, global_cfg)),
        (t("main.menu.mem_profiler"), lambda: mem_profiler.profiler_menu(project_cfg, global_cfg)),
        (t("main.menu.dashboard"), lambda: dashboard.dashboard_menu(project_cfg, global_cfg)),
        (t("main.menu.utilities"),
         lambda: _utils_submenu(project_cfg, global_cfg)),
        (t("main.menu.console_profiles"), lambda: ftp_ops.console_profiles_menu(project_cfg)),
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
                header_extra=_status_header(project_cfg),
            )
            # 0/Q on the main menu: the only place left to go back to is the
            # project selector.
            raise tui.SwitchProject()
        except tui.GoToMainMenu:
            continue


def main():
    """!
    @brief Toolkit entry point: bootstrap global config, then loop between
           the project selector and the active project's main menu.
    @note Control-flow contract: `tui.ExitApp` (from either loop) breaks out
          for good; `tui.SwitchProject` (raised by `show_project_menu()`)
          just `continue`s this loop, returning to `project.select_or_create_project()`.
    """
    global_cfg = cfgmod.ensure_global_config(tui)

    while True:
        try:
            project_cfg = project.select_or_create_project(global_cfg)
        except tui.GoToMainMenu:
            # 'M'/Ctrl+C at the project selector: it IS the root screen, so
            # just redraw it instead of letting the exception escape.
            continue
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


def run():
    """!
    @brief Real process entry point: dispatch to the headless CLI if `sys.argv`
           names a subcommand, otherwise fall through to the interactive TUI.
    @note Kept separate from `main()` (which is the *interactive* entry point
          used directly by tests/tools that want the TUI specifically) so
          `bin/psvita-toolkit` and `python3 -m psvita_toolkit` both get
          headless support for free. See `docs/dev-notes/cli.md`.
    """
    import sys
    from . import cli
    exit_code = cli.dispatch(sys.argv[1:])
    if exit_code is not None:
        sys.exit(exit_code)
    main()


if __name__ == "__main__":
    run()
