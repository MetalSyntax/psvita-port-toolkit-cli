"""!
@file ftp_ops.py
@brief FTP operations against the PS Vita's VitaShell ftpd: upload VPK/eboot, download
       logs/crash dumps, sync shaders, verify data assets, and check libshacccg.suprx health.

@details
Covers connecting to the console (with an optional VPN-bypass local route), uploading a
built VPK or a lone eboot.bin for fast iteration, downloading the latest / a chosen / a
previously-downloaded log or crash dump, GLSL <-> CG shader sync, and a shallow data-asset
integrity check.

Everything reads from the active project's config (`project_cfg` / `project_dir`) instead
of hardcoded constants -- see `docs/dev-notes/ftp_ops.md` for why, plus the rationale behind
the VPN bypass, the shallow (non-recursive) asset comparison, and the three log/crash-dump
download modes.
"""

import socket
import subprocess
import sys
import time
from ftplib import FTP, all_errors
from pathlib import Path

from . import config as cfgmod
from . import tui
from .tui import C
from . import i18n
from .i18n import t

STRINGS = {
    "ftp_ops.vpn_disconnecting": {
        "es": "[*] Desconectando VPN ({cmd})...",
        "en": "[*] Disconnecting VPN ({cmd})...",
        "pt": "[*] Desconectando VPN ({cmd})...",
    },
    "ftp_ops.vpn_disconnected": {
        "es": "[+] VPN desconectada (o ya lo estaba).",
        "en": "[+] VPN disconnected (or already was).",
        "pt": "[+] VPN desconectada (ou já estava).",
    },
    "ftp_ops.vpn_cmd_not_found": {
        "es": "[!] Comando de VPN no encontrado en el PATH: {cmd}",
        "en": "[!] VPN command not found in PATH: {cmd}",
        "pt": "[!] Comando de VPN não encontrado no PATH: {cmd}",
    },
    "ftp_ops.vpn_unexpected_error": {
        "es": "[-] Error inesperado desconectando VPN: {error}",
        "en": "[-] Unexpected error disconnecting VPN: {error}",
        "pt": "[-] Erro inesperado ao desconectar a VPN: {error}",
    },
    "ftp_ops.connecting": {
        "es": "[*] Conectando a la PS Vita en {ip}:{port}...",
        "en": "[*] Connecting to the PS Vita at {ip}:{port}...",
        "pt": "[*] Conectando ao PS Vita em {ip}:{port}...",
    },
    "ftp_ops.retry_connect": {
        "es": "[!] Falló la conexión -- reintentando ({attempt}/{retries})...",
        "en": "[!] Connection failed -- retrying ({attempt}/{retries})...",
        "pt": "[!] Falha na conexão -- tentando novamente ({attempt}/{retries})...",
    },
    "ftp_ops.console_menu_title": {
        "es": "Perfiles de consola",
        "en": "Console profiles",
        "pt": "Perfis de console",
    },
    "ftp_ops.console_breadcrumb": {
        "es": "{game_name} › Perfiles de consola",
        "en": "{game_name} › Console profiles",
        "pt": "{game_name} › Perfis de console",
    },
    "ftp_ops.console_active": {
        "es": "Consola activa: {ip}:{port}",
        "en": "Active console: {ip}:{port}",
        "pt": "Console ativo: {ip}:{port}",
    },
    "ftp_ops.console_none_saved": {
        "es": "(sin perfiles guardados todavía -- 'Agregar perfil' para crear uno)",
        "en": "(no profiles saved yet -- 'Add profile' to create one)",
        "pt": "(nenhum perfil salvo ainda -- 'Adicionar perfil' para criar um)",
    },
    "ftp_ops.console_add": {
        "es": "Agregar perfil (ej. OLED, Slim, PSTV)",
        "en": "Add profile (e.g. OLED, Slim, PSTV)",
        "pt": "Adicionar perfil (ex. OLED, Slim, PSTV)",
    },
    "ftp_ops.console_switch": {
        "es": "Cambiar de consola activa",
        "en": "Switch active console",
        "pt": "Trocar console ativo",
    },
    "ftp_ops.console_delete": {
        "es": "Eliminar perfil",
        "en": "Delete profile",
        "pt": "Excluir perfil",
    },
    "ftp_ops.console_name_prompt": {
        "es": "Nombre del perfil (ej. 'OLED'): ",
        "en": "Profile name (e.g. 'OLED'): ",
        "pt": "Nome do perfil (ex. 'OLED'): ",
    },
    "ftp_ops.console_ip_prompt": {
        "es": "IP [{default}]: ",
        "en": "IP [{default}]: ",
        "pt": "IP [{default}]: ",
    },
    "ftp_ops.console_port_prompt": {
        "es": "Puerto FTP [{default}]: ",
        "en": "FTP port [{default}]: ",
        "pt": "Porta FTP [{default}]: ",
    },
    "ftp_ops.console_choose_title": {
        "es": "Elegir un perfil",
        "en": "Choose a profile",
        "pt": "Escolher um perfil",
    },
    "ftp_ops.console_saved": {
        "es": "[+] Perfil '{name}' guardado.",
        "en": "[+] Profile '{name}' saved.",
        "pt": "[+] Perfil '{name}' salvo.",
    },
    "ftp_ops.console_switched": {
        "es": "[+] Consola activa: '{name}'.",
        "en": "[+] Active console: '{name}'.",
        "pt": "[+] Console ativo: '{name}'.",
    },
    "ftp_ops.console_deleted": {
        "es": "[+] Perfil '{name}' eliminado.",
        "en": "[+] Profile '{name}' deleted.",
        "pt": "[+] Perfil '{name}' excluído.",
    },
    "ftp_ops.forcing_local_route": {
        "es": "[*] Forzando ruta local vía {ip} (bypass de VPN si hay alguna activa).",
        "en": "[*] Forcing local route via {ip} (bypasses any active VPN).",
        "pt": "[*] Forçando rota local via {ip} (contorna qualquer VPN ativa).",
    },
    "ftp_ops.connected": {
        "es": "[+] Conexión FTP establecida.",
        "en": "[+] FTP connection established.",
        "pt": "[+] Conexão FTP estabelecida.",
    },
    "ftp_ops.connect_error": {
        "es": "[-] Error al conectar por FTP a la PS Vita: {error}",
        "en": "[-] Error connecting via FTP to the PS Vita: {error}",
        "pt": "[-] Erro ao conectar via FTP ao PS Vita: {error}",
    },
    "ftp_ops.dir_creating": {
        "es": "[*] El directorio '{path}' no existe. Creándolo...",
        "en": "[*] Directory '{path}' doesn't exist. Creating it...",
        "pt": "[*] O diretório '{path}' não existe. Criando...",
    },
    "ftp_ops.dir_ready": {
        "es": "[+] Directorio '{path}' listo.",
        "en": "[+] Directory '{path}' ready.",
        "pt": "[+] Diretório '{path}' pronto.",
    },
    "ftp_ops.dir_create_failed": {
        "es": "[-] No se pudo crear '{path}': {error}",
        "en": "[-] Couldn't create '{path}': {error}",
        "pt": "[-] Não foi possível criar '{path}': {error}",
    },
    "ftp_ops.vpk_tag_debug_verbose": {
        "es": "Debug Verboso",
        "en": "Verbose Debug",
        "pt": "Debug Verboso",
    },
    "ftp_ops.vpk_tag_relwithdebinfo": {
        "es": "Release + Debug Info",
        "en": "Release + Debug Info",
        "pt": "Release + Debug Info",
    },
    "ftp_ops.vpk_tag_minsizerel": {
        "es": "MinSizeRel",
        "en": "MinSizeRel",
        "pt": "MinSizeRel",
    },
    "ftp_ops.vpk_tag_debug": {
        "es": "Debug",
        "en": "Debug",
        "pt": "Debug",
    },
    "ftp_ops.vpk_tag_release": {
        "es": "Release",
        "en": "Release",
        "pt": "Release",
    },
    "ftp_ops.vpk_tag_glsl_dump": {
        "es": "GLSL + Shader Dump",
        "en": "GLSL + Shader Dump",
        "pt": "GLSL + Dump de Shaders",
    },
    "ftp_ops.vpk_tag_cg": {
        "es": "Shaders CG",
        "en": "CG Shaders",
        "pt": "Shaders CG",
    },
    "ftp_ops.no_vpk_found": {
        "es": "[-] No se encontró ningún .vpk en '{build_dir}/'. Compilá el proyecto primero (opción 'Compilar').",
        "en": "[-] No .vpk found in '{build_dir}/'. Build the project first ('Build' option).",
        "pt": "[-] Nenhum .vpk encontrado em '{build_dir}/'. Compile o projeto primeiro (opção 'Compilar').",
    },
    "ftp_ops.vpk_not_found": {
        "es": "[-] No existe: {path}",
        "en": "[-] Doesn't exist: {path}",
        "pt": "[-] Não existe: {path}",
    },
    "ftp_ops.vpks_found": {
        "es": "[*] {count} VPK(s) encontrado(s) (más reciente primero):",
        "en": "[*] {count} VPK(s) found (newest first):",
        "pt": "[*] {count} VPK(s) encontrado(s) (mais recente primeiro):",
    },
    "ftp_ops.uploading_file": {
        "es": "[*] Subiendo {local} a {dest}...",
        "en": "[*] Uploading {local} to {dest}...",
        "pt": "[*] Enviando {local} para {dest}...",
    },
    "ftp_ops.vpk_upload_success": {
        "es": "[+] Transferencia exitosa. Instalá el VPK desde VitaShell en {path}",
        "en": "[+] Transfer successful. Install the VPK from VitaShell at {path}",
        "pt": "[+] Transferência bem-sucedida. Instale o VPK pelo VitaShell em {path}",
    },
    "ftp_ops.vpk_upload_failed": {
        "es": "[-] Falló la transferencia del VPK: {error}",
        "en": "[-] VPK transfer failed: {error}",
        "pt": "[-] Falha na transferência do VPK: {error}",
    },
    "ftp_ops.eboot_not_found": {
        "es": "[-] No se encontró '{path}'. Compilá el proyecto primero.",
        "en": "[-] '{path}' not found. Build the project first.",
        "pt": "[-] '{path}' não encontrado. Compile o projeto primeiro.",
    },
    "ftp_ops.eboot_found": {
        "es": "[*] eboot.bin encontrado: {size_kb:.1f} KB ({mtime})",
        "en": "[*] eboot.bin found: {size_kb:.1f} KB ({mtime})",
        "pt": "[*] eboot.bin encontrado: {size_kb:.1f} KB ({mtime})",
    },
    "ftp_ops.confirm_upload_eboot_only": {
        "es": "¿Subir SOLO el eboot.bin a ux0:app/{titleid}/?",
        "en": "Upload ONLY eboot.bin to ux0:app/{titleid}/?",
        "pt": "Enviar SOMENTE o eboot.bin para ux0:app/{titleid}/?",
    },
    "ftp_ops.cancelled": {
        "es": "[*] Cancelado.",
        "en": "[*] Cancelled.",
        "pt": "[*] Cancelado.",
    },
    "ftp_ops.eboot_upload_success": {
        "es": "[+] eboot.bin subido. Ya podés iniciar el juego sin reinstalar el VPK entero.",
        "en": "[+] eboot.bin uploaded. You can now launch the game without reinstalling the whole VPK.",
        "pt": "[+] eboot.bin enviado. Já dá para iniciar o jogo sem reinstalar o VPK inteiro.",
    },
    "ftp_ops.transfer_failed": {
        "es": "[-] Falló la transferencia: {error}",
        "en": "[-] Transfer failed: {error}",
        "pt": "[-] Falha na transferência: {error}",
    },
    "ftp_ops.no_logs_found": {
        "es": "[-] No hay logs en {dir}.",
        "en": "[-] No logs in {dir}.",
        "pt": "[-] Não há logs em {dir}.",
    },
    "ftp_ops.downloaded_at": {
        "es": "[+] Descargado en {path}",
        "en": "[+] Downloaded to {path}",
        "pt": "[+] Baixado em {path}",
    },
    "ftp_ops.no_dumps_found": {
        "es": "[-] No hay crash dumps en {dir}.",
        "en": "[-] No crash dumps in {dir}.",
        "pt": "[-] Não há crash dumps em {dir}.",
    },
    "ftp_ops.ftp_generic_error": {
        "es": "[-] Error de FTP: {error}",
        "en": "[-] FTP error: {error}",
        "pt": "[-] Erro de FTP: {error}",
    },
    "ftp_ops.what_to_do": {
        "es": "¿Qué querés hacer?",
        "en": "What do you want to do?",
        "pt": "O que você quer fazer?",
    },
    "ftp_ops.menu_download_latest": {
        "es": "Descargar el ÚLTIMO log + último crash dump de la consola",
        "en": "Download the LATEST log + latest crash dump from the console",
        "pt": "Baixar o ÚLTIMO log + último crash dump do console",
    },
    "ftp_ops.menu_pick_log": {
        "es": "Elegir un log ESPECÍFICO de los que hay ahora en la consola",
        "en": "Pick a SPECIFIC log from what's currently on the console",
        "pt": "Escolher um log ESPECÍFICO dos que estão agora no console",
    },
    "ftp_ops.menu_pick_dump": {
        "es": "Elegir un crash dump ESPECÍFICO de los que hay ahora en la consola",
        "en": "Pick a SPECIFIC crash dump from what's currently on the console",
        "pt": "Escolher um crash dump ESPECÍFICO dos que estão agora no console",
    },
    "ftp_ops.menu_local_history": {
        "es": "Ver HISTORIAL local (ya descargados antes) y volver a analizar/abrir uno",
        "en": "View local HISTORY (previously downloaded) and re-analyze/open one",
        "pt": "Ver HISTÓRICO local (já baixados antes) e reanalisar/abrir um",
    },
    "ftp_ops.logs_available_title": {
        "es": "Logs disponibles en la consola:",
        "en": "Logs available on the console:",
        "pt": "Logs disponíveis no console:",
    },
    "ftp_ops.dumps_available_title": {
        "es": "Crash dumps disponibles en la consola:",
        "en": "Crash dumps available on the console:",
        "pt": "Crash dumps disponíveis no console:",
    },
    "ftp_ops.downloading_latest_dump": {
        "es": "[+] Último dump: '{name}' -> descargando...",
        "en": "[+] Latest dump: '{name}' -> downloading...",
        "pt": "[+] Último dump: '{name}' -> baixando...",
    },
    "ftp_ops.no_pending_dumps": {
        "es": "[*] No hay crash dumps pendientes en la consola.",
        "en": "[*] No pending crash dumps on the console.",
        "pt": "[*] Não há crash dumps pendentes no console.",
    },
    "ftp_ops.downloading_latest_log": {
        "es": "[+] Último log: '{name}' -> descargando...",
        "en": "[+] Latest log: '{name}' -> downloading...",
        "pt": "[+] Último log: '{name}' -> baixando...",
    },
    "ftp_ops.no_logs_found_alt": {
        "es": "[-] No se encontraron logs en {dir}.",
        "en": "[-] No logs found in {dir}.",
        "pt": "[-] Nenhum log encontrado em {dir}.",
    },
    "ftp_ops.confirm_analyze_dump": {
        "es": "¿Analizar este crash dump ahora con el analizador integrado?",
        "en": "Analyze this crash dump now with the built-in analyzer?",
        "pt": "Analisar este crash dump agora com o analisador integrado?",
    },
    "ftp_ops.previously_downloaded_title": {
        "es": "Logs ya descargados antes:",
        "en": "Previously downloaded logs:",
        "pt": "Logs já baixados antes:",
    },
    "ftp_ops.history_empty": {
        "es": "(vacío -- todavía no descargaste nada a este proyecto)",
        "en": "(empty -- you haven't downloaded anything to this project yet)",
        "pt": "(vazio -- você ainda não baixou nada para este projeto)",
    },
    "ftp_ops.local_history_title": {
        "es": "Historial local (más reciente primero):",
        "en": "Local history (newest first):",
        "pt": "Histórico local (mais recente primeiro):",
    },
    "ftp_ops.log_tail_header": {
        "es": "--- {name} (últimas 60 líneas) ---",
        "en": "--- {name} (last 60 lines) ---",
        "pt": "--- {name} (últimas 60 linhas) ---",
    },
    "ftp_ops.no_glsl_shaders": {
        "es": "[-] No hay shaders .glsl en {dir}.",
        "en": "[-] No .glsl shaders in {dir}.",
        "pt": "[-] Não há shaders .glsl em {dir}.",
    },
    "ftp_ops.shaders_found_downloading": {
        "es": "[+] {count} shader(s) encontrados. Descargando...",
        "en": "[+] {count} shader(s) found. Downloading...",
        "pt": "[+] {count} shader(s) encontrado(s). Baixando...",
    },
    "ftp_ops.all_shaders_downloaded": {
        "es": "[+] Todos los shaders descargados en {dir}",
        "en": "[+] All shaders downloaded to {dir}",
        "pt": "[+] Todos os shaders baixados em {dir}",
    },
    "ftp_ops.shaders_list_download_error": {
        "es": "[-] Error al listar/descargar shaders: {error}",
        "en": "[-] Error listing/downloading shaders: {error}",
        "pt": "[-] Erro ao listar/baixar shaders: {error}",
    },
    "ftp_ops.dir_not_found": {
        "es": "[-] No se encontró '{path}'.",
        "en": "[-] '{path}' not found.",
        "pt": "[-] '{path}' não encontrado.",
    },
    "ftp_ops.no_cg_files": {
        "es": "[-] No hay archivos .cg en '{path}'.",
        "en": "[-] No .cg files in '{path}'.",
        "pt": "[-] Não há arquivos .cg em '{path}'.",
    },
    "ftp_ops.uploading_cg_shaders": {
        "es": "[*] Subiendo {count} shader(s) .cg a {dir}...",
        "en": "[*] Uploading {count} .cg shader(s) to {dir}...",
        "pt": "[*] Enviando {count} shader(s) .cg para {dir}...",
    },
    "ftp_ops.all_cg_shaders_uploaded": {
        "es": "[+] Todos los shaders .cg subidos.",
        "en": "[+] All .cg shaders uploaded.",
        "pt": "[+] Todos os shaders .cg enviados.",
    },
    "ftp_ops.upload_failed_generic": {
        "es": "[-] Falló la subida: {error}",
        "en": "[-] Upload failed: {error}",
        "pt": "[-] Falha no envio: {error}",
    },
    "ftp_ops.sync_step1": {
        "es": "[*] Paso 1/2: descargando shaders GLSL sin traducir...",
        "en": "[*] Step 1/2: downloading untranslated GLSL shaders...",
        "pt": "[*] Passo 1/2: baixando shaders GLSL não traduzidos...",
    },
    "ftp_ops.shaders_missing_translation": {
        "es": "[!] {count} shader(s) todavía SIN traducir a .cg:",
        "en": "[!] {count} shader(s) still NOT translated to .cg:",
        "pt": "[!] {count} shader(s) ainda SEM tradução para .cg:",
    },
    "ftp_ops.all_shaders_translated": {
        "es": "[+] Todos los shaders volcados ya tienen su .cg.",
        "en": "[+] All dumped shaders already have their .cg.",
        "pt": "[+] Todos os shaders extraídos já têm seu .cg.",
    },
    "ftp_ops.sync_step2": {
        "es": "[*] Paso 2/2: subiendo los .cg traducidos...",
        "en": "[*] Step 2/2: uploading translated .cg files...",
        "pt": "[*] Passo 2/2: enviando os .cg traduzidos...",
    },
    "ftp_ops.suspiciously_small_warn": {
        "es": "  <-- sospechosamente chico/vacío!",
        "en": "  <-- suspiciously small/empty!",
        "pt": "  <-- suspeitosamente pequeno/vazio!",
    },
    "ftp_ops.libshacccg_exists": {
        "es": "{path}: existe, {size} bytes{warn}",
        "en": "{path}: exists, {size} bytes{warn}",
        "pt": "{path}: existe, {size} bytes{warn}",
    },
    "ftp_ops.libshacccg_not_found": {
        "es": "{path}: no encontrado ({error})",
        "en": "{path}: not found ({error})",
        "pt": "{path}: não encontrado ({error})",
    },
    "ftp_ops.local_reference_not_found": {
        "es": "[-] No se encontró la referencia local '{path}'.",
        "en": "[-] Local reference '{path}' not found.",
        "pt": "[-] Referência local '{path}' não encontrada.",
    },
    "ftp_ops.no_subfolders": {
        "es": "[-] '{path}' no tiene subcarpetas.",
        "en": "[-] '{path}' has no subfolders.",
        "pt": "[-] '{path}' não tem subpastas.",
    },
    "ftp_ops.comparing_subfolders": {
        "es": "[*] Comparando {count} subcarpeta(s) (local vs. {dir}, chequeo superficial)...\n",
        "en": "[*] Comparing {count} subfolder(s) (local vs. {dir}, shallow check)...\n",
        "pt": "[*] Comparando {count} subpasta(s) (local vs. {dir}, checagem superficial)...\n",
    },
    "ftp_ops.subfolder_connect_failed": {
        "es": "[?] {sub}/: no se pudo conectar",
        "en": "[?] {sub}/: couldn't connect",
        "pt": "[?] {sub}/: não foi possível conectar",
    },
    "ftp_ops.subfolder_list_failed": {
        "es": "[?] {sub}/: no se pudo listar ({error})",
        "en": "[?] {sub}/: couldn't list ({error})",
        "pt": "[?] {sub}/: não foi possível listar ({error})",
    },
    "ftp_ops.folders_mismatch_warning": {
        "es": "[!] Alguna(s) carpeta(s) no coinciden -- probablemente quedaron a mitad de copiar.",
        "en": "[!] Some folder(s) don't match -- they probably got copied halfway.",
        "pt": "[!] Alguma(s) pasta(s) não coincidem -- provavelmente ficaram copiadas pela metade.",
    },
    "ftp_ops.all_folders_match": {
        "es": "[+] Todas las carpetas coinciden en cantidad de archivos.",
        "en": "[+] All folders match in file count.",
        "pt": "[+] Todas as pastas coincidem na quantidade de arquivos.",
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def disconnect_vpn(global_cfg):
    """!
    @brief Best-effort disconnect of a user-configured VPN before talking FTP.
    @param global_cfg Global config dict; reads the optional `vpn_disconnect_cmd` key.
    @note No-op if `vpn_disconnect_cmd` isn't set. See docs/dev-notes/ftp_ops.md for why a
          VPN might need disconnecting at all (full-tunnel VPNs breaking the LAN route to
          the Vita).
    """
    cmd = global_cfg.get("vpn_disconnect_cmd")
    if not cmd:
        return
    print(t("ftp_ops.vpn_disconnecting", cmd=cmd))
    try:
        r = subprocess.run(cmd.split(), capture_output=True, text=True)
        if r.returncode == 0 or "not connected" in (r.stderr + r.stdout).lower():
            print(f"{C.GREEN}{t('ftp_ops.vpn_disconnected')}{C.RESET}")
        else:
            print(f"{C.YELLOW}[!] {r.stderr.strip() or r.stdout.strip()}{C.RESET}")
    except FileNotFoundError:
        print(f"{C.YELLOW}{t('ftp_ops.vpn_cmd_not_found', cmd=cmd)}{C.RESET}")
    except Exception as e:
        print(f"{C.RED}{t('ftp_ops.vpn_unexpected_error', error=e)}{C.RESET}")


def _local_ip_for_route(vita_ip):
    """!
    @brief Find the local IP address that reaches the Vita's LAN subnet directly.
    @param vita_ip The test PS Vita's IP address.
    @return The local IP in the same /24 subnet as `vita_ip`, or `None` if not found.
    @note Used to force the FTP socket to bind to this address instead of a possibly-active
          VPN's tunnel interface. See docs/dev-notes/ftp_ops.md for the VPN-bypass rationale.
    """
    prefix = ".".join(vita_ip.split(".")[:3]) + "."
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip.startswith(prefix):
                return ip
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((vita_ip, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip.startswith(prefix) else None
    except Exception:
        return None


def connect_ftp(project_cfg, global_cfg=None):
    """!
    @brief Open an FTP connection to the project's configured test PS Vita.
    @param project_cfg Active project config dict (`vita_ip`, `vita_port`).
    @param global_cfg Unused by this function; kept for call-site symmetry with `_connect()`.
    @return An anonymous, logged-in `ftplib.FTP` instance, or `None` on failure.
    """
    vita_ip = project_cfg["vita_ip"]
    vita_port = project_cfg.get("vita_port", 1337)
    print(t("ftp_ops.connecting", ip=vita_ip, port=vita_port))
    local_ip = _local_ip_for_route(vita_ip)
    source_addr = (local_ip, 0) if local_ip else None
    if local_ip:
        print(t("ftp_ops.forcing_local_route", ip=local_ip))
    try:
        ftp = FTP()
        ftp.connect(vita_ip, vita_port, timeout=10, source_address=source_addr)
        ftp.login()
        print(f"{C.GREEN}{t('ftp_ops.connected')}{C.RESET}")
        return ftp
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.connect_error', error=e)}{C.RESET}")
        return None


def _connect(project_cfg, global_cfg):
    """!
    @brief Disconnect any configured VPN, then open the FTP connection.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict (used for `vpn_disconnect_cmd`).
    @return An `ftplib.FTP` instance, or `None` on failure.
    """
    if global_cfg:
        disconnect_vpn(global_cfg)
    return connect_ftp(project_cfg)


def _connect_with_retry(project_cfg, global_cfg, retries=2, delay=2.0):
    """!
    @brief `_connect()`, retrying a couple of times on failure before giving up.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict.
    @param retries Extra connection attempts after the first one fails.
    @param delay Seconds to wait between attempts.
    @return An `ftplib.FTP` instance, or `None` if every attempt failed.
    @note VitaShell's ftpd occasionally refuses a new connection attempt made
          right after a previous one closed (still tearing down the old data
          connection) -- a short, silent retry recovers from that without
          making the user re-trigger the whole operation by hand.
    """
    ftp = _connect(project_cfg, global_cfg)
    attempt = 0
    while ftp is None and attempt < retries:
        attempt += 1
        print(t("ftp_ops.retry_connect", attempt=attempt, retries=retries))
        time.sleep(delay)
        ftp = _connect(project_cfg, global_cfg)
    return ftp


def _keepalive(ftp):
    """!
    @brief Send `NOOP` to keep an idle FTP control connection from timing out.
    @param ftp Connected `ftplib.FTP` instance.
    @return `True` if the connection is still alive.
    """
    try:
        ftp.voidcmd("NOOP")
        return True
    except all_errors:
        return False


def _progress_callback(total_size, label=""):
    """!
    @brief Build an `ftplib` `storbinary`/`retrbinary` callback that prints a
           throttled progress bar (percent, KB/s, ETA) to stdout.
    @param total_size Expected total transfer size in bytes, or `0`/`None` if
           unknown (e.g. the server doesn't support `SIZE`) -- falls back to a
           running total + speed display with no percent/bar/ETA.
    @param label Short text shown at the end of the progress line (e.g. the filename).
    @return A callback suitable for `storbinary(..., callback=...)` or passed
            as `retrbinary`'s write callback (wrap it to also write the block).
    @note Throttled to at most ~1 redraw per 150ms so it doesn't spend more
          time printing than transferring on a fast LAN.
    """
    state = {"done": 0, "start": time.time(), "last": 0.0}

    def callback(block):
        state["done"] += len(block)
        now = time.time()
        finished = bool(total_size) and state["done"] >= total_size
        if not finished and now - state["last"] < 0.15:
            return
        state["last"] = now
        elapsed = max(now - state["start"], 0.001)
        speed_kbps = (state["done"] / 1024) / elapsed
        if total_size:
            pct = min(100.0, state["done"] * 100 / total_size)
            remaining_kb = max(total_size - state["done"], 0) / 1024
            eta = f"{remaining_kb / speed_kbps:5.1f}s" if speed_kbps > 0 else "--"
            bar_len = 24
            filled = int(bar_len * pct / 100)
            bar = "#" * filled + "-" * (bar_len - filled)
            line = f"\r  [{bar}] {pct:5.1f}%  {speed_kbps:7.1f} KB/s  ETA {eta}  {label}"
        else:
            line = f"\r  {state['done'] / 1024:9.1f} KB  {speed_kbps:7.1f} KB/s  {label}"
        sys.stdout.write(line)
        sys.stdout.flush()
        if finished:
            sys.stdout.write("\n")

    return callback


def _remote_size(ftp, path):
    """!
    @brief Best-effort remote file size lookup, for the download progress bar.
    @param ftp Connected `ftplib.FTP` instance.
    @param path Remote file path.
    @return Size in bytes, or `0` if the server doesn't support `SIZE`/it fails.
    """
    try:
        ftp.voidcmd("TYPE I")
        return ftp.size(path) or 0
    except all_errors:
        return 0


def create_dir_if_missing(ftp, path):
    """!
    @brief Ensure `path` exists on the Vita, creating intermediate directories as needed.
    @param ftp Connected `ftplib.FTP` instance.
    @param path Absolute remote path (e.g. `/ux0:/data/<slug>`).
    """
    try:
        ftp.cwd(path)
        return
    except all_errors:
        pass
    print(t("ftp_ops.dir_creating", path=path))
    parts = [p for p in path.split("/") if p]
    current = ""
    for part in parts:
        current = part if ":" in part else f"{current}/{part}"
        try:
            ftp.mkd(current)
        except all_errors:
            pass
    try:
        ftp.cwd(path)
        print(f"{C.GREEN}{t('ftp_ops.dir_ready', path=path)}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.dir_create_failed', path=path, error=e)}{C.RESET}")


def _list_entries(ftp, path):
    """!
    @brief List the entries of a remote directory.
    @param ftp Connected `ftplib.FTP` instance.
    @param path Remote directory to list.
    @return list of `(name, is_dir, mtime_or_None)` tuples.
    @note Calls `cwd()` first (fails clearly if the path doesn't exist), then tries `MLSD`
          and falls back to plain `LIST` -- passing the full path as an argument to `LIST`
          confuses VitaShell's ftpd.
    """
    ftp.cwd(path)
    entries = []
    try:
        for name, facts in ftp.mlsd():
            if name in (".", ".."):
                continue
            mtime = facts.get("modify")
            entries.append((name, facts.get("type") == "dir", mtime))
        return entries
    except all_errors:
        pass
    lines = []
    ftp.retrlines("LIST", lines.append)
    for line in lines:
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        name = parts[8]
        if name in (".", ".."):
            continue
        entries.append((name, line.startswith("d"), None))
    return entries


# ---------------------------------------------------------------------------
# Local VPKs (to pick which one to upload)
# ---------------------------------------------------------------------------

def _vpk_desc(filename):
    """!
    @brief Build a short display tag for a VPK based on keywords in its filename.
    @param filename VPK filename to inspect.
    @return A bracketed tag string (e.g. `" [Release]"`), or `""` if no keyword matches.
    """
    lower = filename.lower()
    tags = {
        "debug_verbose": t("ftp_ops.vpk_tag_debug_verbose"),
        "relwithdebinfo": t("ftp_ops.vpk_tag_relwithdebinfo"),
        "minsizerel": t("ftp_ops.vpk_tag_minsizerel"),
        "debug": t("ftp_ops.vpk_tag_debug"),
        "release": t("ftp_ops.vpk_tag_release"),
        "glsl_dump": t("ftp_ops.vpk_tag_glsl_dump"),
        "cg": t("ftp_ops.vpk_tag_cg"),
    }
    for key, label in tags.items():
        if key in lower:
            return f" [{label}]"
    return ""


def list_local_vpks(project_dir, build_dir="build"):
    """!
    @brief List local `.vpk` files in the project's build directory.
    @param project_dir Path to the project directory.
    @param build_dir Build output subdirectory, relative to `project_dir`.
    @return list of `Path`s, newest first.
    """
    build_path = Path(project_dir) / build_dir
    if not build_path.is_dir():
        return []
    vpks = [p for p in build_path.iterdir() if p.suffix == ".vpk" and not p.name.startswith("._")]
    vpks.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return vpks


def choose_vpk(project_cfg, non_interactive=False):
    """!
    @brief Interactive picker for which local VPK to upload.
    @param project_cfg Active project config dict.
    @param non_interactive If `True`, skip the prompt and return the newest
           local VPK directly (or `None` if there isn't one) -- used by the
           headless CLI (`psvita-toolkit deploy`), which has no TTY to prompt on.
    @return The chosen `Path`, or `None` if cancelled, none found, or an invalid choice.
    """
    project_dir = project_cfg["_project_dir"]
    build_dir = project_cfg.get("build_dir", "build")
    vpks = list_local_vpks(project_dir, build_dir)
    if not vpks:
        print(f"{C.RED}{t('ftp_ops.no_vpk_found', build_dir=build_dir)}{C.RESET}")
        return None

    if non_interactive:
        return vpks[0]

    def label(p):
        desc = _vpk_desc(p.name)
        size_mb = p.stat().st_size / (1024 * 1024)
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        return f"{p.name:<32}{C.DIM}{desc:<26} {size_mb:6.2f} MB   {mtime}{C.RESET}"

    return tui.select_list(t("ftp_ops.vpks_found", count=len(vpks)), vpks, label_fn=label)


# ---------------------------------------------------------------------------
# Upload: VPK / eboot
# ---------------------------------------------------------------------------

def upload_vpk(project_cfg, global_cfg, vpk_path=None, non_interactive=False):
    """!
    @brief Upload the chosen local VPK to the Vita's downloads folder.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict (VPN bypass, etc).
    @param vpk_path Explicit `.vpk` path to upload, bypassing the picker
           entirely -- used by the headless CLI when `--vpk-path` is given.
    @param non_interactive If `True` (and `vpk_path` isn't given), pick the
           newest local VPK without prompting -- see `choose_vpk()`.
    """
    local_vpk = Path(vpk_path) if vpk_path else choose_vpk(project_cfg, non_interactive=non_interactive)
    if not local_vpk or not Path(local_vpk).exists():
        if local_vpk:
            print(f"{C.RED}{t('ftp_ops.vpk_not_found', path=local_vpk)}{C.RESET}")
        return
    ftp = _connect_with_retry(project_cfg, global_cfg)
    if not ftp:
        return
    try:
        downloads_dir = project_cfg.get("vita_downloads_dir", "/ux0:/downloads")
        create_dir_if_missing(ftp, downloads_dir)
        dest = f"{downloads_dir}/{local_vpk.name}"
        print(t("ftp_ops.uploading_file", local=local_vpk, dest=dest))
        total_size = Path(local_vpk).stat().st_size
        with open(local_vpk, "rb") as f:
            ftp.storbinary(f"STOR {dest}", f, callback=_progress_callback(total_size, label=local_vpk.name))
        dest_display = f"{downloads_dir.replace('/ux0:', 'ux0:')}/{local_vpk.name}"
        print(f"{C.GREEN}{t('ftp_ops.vpk_upload_success', path=dest_display)}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.vpk_upload_failed', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def upload_eboot(project_cfg, global_cfg, assume_yes=False):
    """!
    @brief Upload only `eboot.bin` to `ux0:app/<titleid>/`, for a fast iterate cycle.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict (VPN bypass, etc).
    @param assume_yes Skip the overwrite confirmation -- used by the headless
           CLI (`psvita-toolkit deploy --eboot --yes`), which has no TTY to confirm on.
    @note Requires user confirmation before overwriting the installed eboot,
          unless `assume_yes` is set.
    """
    project_dir = Path(project_cfg["_project_dir"])
    eboot = project_dir / project_cfg.get("build_dir", "build") / "eboot.bin"
    if not eboot.exists():
        print(f"{C.RED}{t('ftp_ops.eboot_not_found', path=eboot)}{C.RESET}")
        return

    size_kb = eboot.stat().st_size / 1024
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(eboot.stat().st_mtime))
    print(t("ftp_ops.eboot_found", size_kb=size_kb, mtime=mtime))

    titleid = project_cfg["titleid"]
    if not assume_yes and not tui.confirm(t("ftp_ops.confirm_upload_eboot_only", titleid=titleid)):
        print(t("ftp_ops.cancelled"))
        return

    ftp = _connect_with_retry(project_cfg, global_cfg)
    if not ftp:
        return
    dest_dir = f"/ux0:/app/{titleid}"
    try:
        create_dir_if_missing(ftp, dest_dir)
        dest = f"{dest_dir}/eboot.bin"
        print(t("ftp_ops.uploading_file", local=eboot, dest=dest))
        with open(eboot, "rb") as f:
            ftp.storbinary(f"STOR {dest}", f, callback=_progress_callback(eboot.stat().st_size, label="eboot.bin"))
        print(f"{C.GREEN}{t('ftp_ops.eboot_upload_success')}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.transfer_failed', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def _quit(ftp):
    """!
    @brief Best-effort FTP QUIT, swallowing any error.
    @param ftp Connected `ftplib.FTP` instance to close.
    """
    try:
        ftp.quit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Log / crash dump download: latest, pick from the remote list, or local
# history already downloaded ("memoria").
# ---------------------------------------------------------------------------

def _local_logs_dir(project_cfg):
    """!
    @brief Path to the project's local logs directory, creating it if needed.
    @param project_cfg Active project config dict.
    @return `Path` to `<project_dir>/logs`.
    """
    d = Path(project_cfg["_project_dir"]) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_remote_logs(ftp, project_cfg):
    """!
    @brief List `.txt` logs in the Vita's configured logs directory.
    @param ftp Connected `ftplib.FTP` instance.
    @param project_cfg Active project config dict (`vita_logs_dir`).
    @return list of `(name, mtime)` tuples, newest first.
    """
    vita_logs_dir = project_cfg.get("vita_logs_dir", "/ux0:/data")
    entries = _list_entries(ftp, vita_logs_dir)
    logs = [(name, mtime) for name, is_dir, mtime in entries
            if not is_dir and (name.endswith(".txt") or "log" in name.lower())]
    logs.sort(key=lambda x: x[1] or "", reverse=True)
    return logs


def list_remote_dumps(ftp, project_cfg):
    """!
    @brief List crash dumps (`psp2core*`/`.dmp`) in the Vita's configured data directory.
    @param ftp Connected `ftplib.FTP` instance.
    @param project_cfg Active project config dict (`vita_data_dir`).
    @return list of `(name, mtime)` tuples, newest first.
    """
    vita_data_dir = project_cfg.get("vita_data_dir", "/ux0:/data")
    entries = _list_entries(ftp, vita_data_dir)
    dumps = [(name, mtime) for name, is_dir, mtime in entries
             if not is_dir and (name.startswith("psp2core") or name.endswith(".dmp")) and not name.endswith(".tmp")]
    dumps.sort(key=lambda x: x[1] or "", reverse=True)
    return dumps


def _download_remote_file(ftp, remote_dir, remote_name, local_path):
    """!
    @brief Download a single remote file to a local path, with a progress bar.
    @param ftp Connected `ftplib.FTP` instance.
    @param remote_dir Remote directory containing the file.
    @param remote_name Remote filename.
    @param local_path Local destination path.
    """
    ftp.cwd(remote_dir)
    total_size = _remote_size(ftp, remote_name)
    progress = _progress_callback(total_size, label=remote_name)
    with open(local_path, "wb") as f:
        def _write_and_report(block):
            f.write(block)
            progress(block)
        ftp.retrbinary(f"RETR {remote_name}", _write_and_report)


def list_local_history(project_cfg, kind="logs"):
    """!
    @brief List previously downloaded logs/dumps kept in `<project_dir>/logs/`.
    @param project_cfg Active project config dict.
    @param kind `"logs"` for `.txt` files, or `"dumps"` for `psp2core*`/`.dmp` files.
    @return list of `Path`s, newest first.
    """
    logs_dir = _local_logs_dir(project_cfg)
    if kind == "dumps":
        files = [p for p in logs_dir.iterdir()
                 if p.is_file() and (p.name.startswith("psp2core") or p.suffix == ".dmp")]
    else:
        files = [p for p in logs_dir.iterdir() if p.is_file() and p.suffix == ".txt" and ".analysis" not in p.name]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def download_logs_and_dumps(project_cfg, global_cfg):
    """!
    @brief Menu: download the LATEST log/dump, pick a SPECIFIC one from what's currently on
           the console, or browse the local HISTORY of previously downloaded files.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict (VPN bypass, etc).
    @note The local-history mode is a deliberate third mode, not just a
          convenience shortcut -- see docs/dev-notes/ftp_ops.md.
    """
    actions = [
        ("latest", t("ftp_ops.menu_download_latest")),
        ("pick_log", t("ftp_ops.menu_pick_log")),
        ("pick_dump", t("ftp_ops.menu_pick_dump")),
        ("history", t("ftp_ops.menu_local_history")),
    ]
    chosen = tui.select_list(t("ftp_ops.what_to_do"), actions, label_fn=lambda a: a[1])
    if chosen is None:
        return
    action, _ = chosen

    if action == "history":
        _browse_local_history(project_cfg)
        return

    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    try:
        if action == "latest":
            _download_latest(ftp, project_cfg, want_dump=True, want_log=True)
        elif action == "pick_log":
            logs = list_remote_logs(ftp, project_cfg)
            if not logs:
                print(f"{C.YELLOW}{t('ftp_ops.no_logs_found', dir=project_cfg.get('vita_logs_dir'))}{C.RESET}")
                return
            picked = tui.select_list(
                t("ftp_ops.logs_available_title"), logs,
                label_fn=lambda e: f"{e[0]:<40} {C.DIM}{e[1] or ''}{C.RESET}",
            )
            if picked is None:
                return
            name, _ = picked
            local_path = _local_logs_dir(project_cfg) / name
            _download_remote_file(ftp, project_cfg.get("vita_logs_dir"), name, local_path)
            print(f"{C.GREEN}{t('ftp_ops.downloaded_at', path=local_path)}{C.RESET}")
        elif action == "pick_dump":
            dumps = list_remote_dumps(ftp, project_cfg)
            if not dumps:
                print(f"{C.YELLOW}{t('ftp_ops.no_dumps_found', dir=project_cfg.get('vita_data_dir'))}{C.RESET}")
                return
            picked = tui.select_list(
                t("ftp_ops.dumps_available_title"), dumps,
                label_fn=lambda e: f"{e[0]:<40} {C.DIM}{e[1] or ''}{C.RESET}",
            )
            if picked is None:
                return
            name, _ = picked
            local_path = _local_logs_dir(project_cfg) / f"{project_cfg['slug']}-{name}"
            _download_remote_file(ftp, project_cfg.get("vita_data_dir"), name, local_path)
            print(f"{C.GREEN}{t('ftp_ops.downloaded_at', path=local_path)}{C.RESET}")
            _offer_analyze(project_cfg, local_path)
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.ftp_generic_error', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def _download_latest(ftp, project_cfg, want_dump=True, want_log=True):
    """!
    @brief Download the most recent crash dump and/or log from the console.
    @param ftp Connected `ftplib.FTP` instance.
    @param project_cfg Active project config dict.
    @param want_dump Whether to download the latest crash dump.
    @param want_log Whether to download the latest log.
    """
    if want_dump:
        dumps = list_remote_dumps(ftp, project_cfg)
        if dumps:
            name, _ = dumps[0]
            local_path = _local_logs_dir(project_cfg) / f"{project_cfg['slug']}-{name}"
            print(t("ftp_ops.downloading_latest_dump", name=name))
            _download_remote_file(ftp, project_cfg.get("vita_data_dir"), name, local_path)
            print(f"{C.GREEN}{t('ftp_ops.downloaded_at', path=local_path)}{C.RESET}")
            _offer_analyze(project_cfg, local_path)
        else:
            print(f"{C.DIM}{t('ftp_ops.no_pending_dumps')}{C.RESET}")

    if want_log:
        logs = list_remote_logs(ftp, project_cfg)
        if logs:
            name, _ = logs[0]
            local_path = _local_logs_dir(project_cfg) / name
            print(t("ftp_ops.downloading_latest_log", name=name))
            _download_remote_file(ftp, project_cfg.get("vita_logs_dir"), name, local_path)
            print(f"{C.GREEN}{t('ftp_ops.downloaded_at', path=local_path)}{C.RESET}")
        else:
            print(f"{C.YELLOW}{t('ftp_ops.no_logs_found_alt', dir=project_cfg.get('vita_logs_dir'))}{C.RESET}")


def fetch_latest_dump_headless(project_cfg, global_cfg):
    """!
    @brief Headless equivalent of `_download_latest(want_dump=True)`: connect,
           download the newest remote crash dump if there is one, and return
           without ever prompting -- `_offer_analyze()`'s `tui.confirm()`
           would block forever with no TTY attached.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict.
    @return Local `Path` to the downloaded dump, or `None` if the console is
            unreachable or has no crash dump waiting.
    @note Used by `auto_synth.py`'s bootstrap loop, which decides for itself
          whether to analyze the result -- this function only fetches.
    """
    ftp = _connect_with_retry(project_cfg, global_cfg)
    if not ftp:
        return None
    try:
        dumps = list_remote_dumps(ftp, project_cfg)
        if not dumps:
            return None
        name, _ = dumps[0]
        local_path = _local_logs_dir(project_cfg) / f"{project_cfg['slug']}-{name}"
        _download_remote_file(ftp, project_cfg.get("vita_data_dir"), name, local_path)
        return local_path
    except all_errors:
        return None
    finally:
        _quit(ftp)


def _offer_analyze(project_cfg, dump_path):
    """!
    @brief Offer to run the built-in crash analyzer on a just-downloaded dump.
    @param project_cfg Active project config dict.
    @param dump_path Local path to the downloaded crash dump.
    """
    if tui.confirm(t("ftp_ops.confirm_analyze_dump")):
        from . import crash_analyzer
        crash_analyzer.analyze(project_cfg, str(dump_path))


def _browse_local_history(project_cfg):
    """!
    @brief Interactive browser over local log/dump history; opens or analyzes the pick.
    @param project_cfg Active project config dict.
    """
    print(f"\n{C.BOLD}{t('ftp_ops.previously_downloaded_title')}{C.RESET}")
    logs = list_local_history(project_cfg, "logs")
    dumps = list_local_history(project_cfg, "dumps")
    if not logs and not dumps:
        print(f"{C.DIM}{t('ftp_ops.history_empty')}{C.RESET}")
        return

    options = []
    for p in logs:
        options.append(("log", p))
    for p in dumps:
        options.append(("dump", p))
    options.sort(key=lambda t: t[1].stat().st_mtime, reverse=True)

    def label(entry):
        kind, p = entry
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        return f"[{kind}] {p.name:<36} {C.DIM}{mtime}{C.RESET}"

    chosen = tui.select_list(t("ftp_ops.local_history_title"), options, label_fn=label)
    if chosen is None:
        return
    kind, path = chosen
    if kind == "dump":
        _offer_analyze(project_cfg, path)
    else:
        print(f"\n{C.DIM}{t('ftp_ops.log_tail_header', name=path.name)}{C.RESET}")
        lines = path.read_text(errors="ignore").splitlines()
        print("\n".join(lines[-60:]))


# ---------------------------------------------------------------------------
# Shaders (dumped GLSL <-> translated CG)
# ---------------------------------------------------------------------------

def download_glsl_shaders(project_cfg, global_cfg):
    """!
    @brief Download all dumped `.glsl` shaders from the Vita to `<project_dir>/glsl_dump/`.
    @param project_cfg Active project config dict (`vita_glsl_dir`).
    @param global_cfg Global config dict (VPN bypass, etc).
    """
    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    vita_glsl_dir = project_cfg.get("vita_glsl_dir")
    local_dir = Path(project_cfg["_project_dir"]) / "glsl_dump"
    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        entries = _list_entries(ftp, vita_glsl_dir)
        files = [name for name, is_dir, _ in entries if not is_dir and name.endswith(".glsl")]
        if not files:
            print(f"{C.YELLOW}{t('ftp_ops.no_glsl_shaders', dir=vita_glsl_dir)}{C.RESET}")
            return
        print(t("ftp_ops.shaders_found_downloading", count=len(files)))
        for name in files:
            local_path = local_dir / name
            with open(local_path, "wb") as f:
                ftp.retrbinary(f"RETR {name}", f.write)
            print(f"  -> {name}")
        print(f"{C.GREEN}{t('ftp_ops.all_shaders_downloaded', dir=local_dir)}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.shaders_list_download_error', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def upload_cg_shaders(project_cfg, global_cfg):
    """!
    @brief Upload all local translated `.cg` shaders to the Vita.
    @param project_cfg Active project config dict (`vita_cg_dir`).
    @param global_cfg Global config dict (VPN bypass, etc).
    """
    local_dir = Path(project_cfg["_project_dir"]) / "assets" / "cg"
    if not local_dir.is_dir():
        print(f"{C.RED}{t('ftp_ops.dir_not_found', path=local_dir)}{C.RESET}")
        return
    cg_files = sorted(p for p in local_dir.iterdir() if p.suffix == ".cg" and not p.name.startswith("._"))
    if not cg_files:
        print(f"{C.RED}{t('ftp_ops.no_cg_files', path=local_dir)}{C.RESET}")
        return

    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    vita_cg_dir = project_cfg.get("vita_cg_dir")
    try:
        create_dir_if_missing(ftp, vita_cg_dir)
        print(t("ftp_ops.uploading_cg_shaders", count=len(cg_files), dir=vita_cg_dir))
        for p in cg_files:
            with open(p, "rb") as f:
                ftp.storbinary(f"STOR {vita_cg_dir}/{p.name}", f)
            print(f"  -> {p.name}")
        print(f"{C.GREEN}{t('ftp_ops.all_cg_shaders_uploaded')}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.upload_failed_generic', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def sync_shaders(project_cfg, global_cfg):
    """!
    @brief Two-step shader sync: download undtranslated GLSL dumps and report which still
           lack a `.cg` translation, then upload whatever `.cg` files exist locally.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict (VPN bypass, etc).
    """
    print(t("ftp_ops.sync_step1"))
    download_glsl_shaders(project_cfg, global_cfg)

    local_glsl = Path(project_cfg["_project_dir"]) / "glsl_dump"
    local_cg = Path(project_cfg["_project_dir"]) / "assets" / "cg"
    dumped = {p.stem for p in local_glsl.glob("*.glsl")} if local_glsl.is_dir() else set()
    translated = {p.stem for p in local_cg.glob("*.cg")} if local_cg.is_dir() else set()
    missing = sorted(dumped - translated)
    if missing:
        print(f"{C.YELLOW}{t('ftp_ops.shaders_missing_translation', count=len(missing))}{C.RESET}")
        for h in missing:
            print(f"  - {h}.glsl")
    else:
        print(f"{C.GREEN}{t('ftp_ops.all_shaders_translated')}{C.RESET}")

    print(t("ftp_ops.sync_step2"))
    upload_cg_shaders(project_cfg, global_cfg)


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def check_libshacccg(project_cfg, global_cfg):
    """!
    @brief Check that `libshacccg.suprx` exists (and isn't suspiciously small) at its known
           candidate paths.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict (VPN bypass, etc).
    @note A corrupt or missing `libshacccg.suprx` produces a "fatal internal error" on ANY
          shader, even a trivial one -- worth checking in isolation. See
          docs/dev-notes/ftp_ops.md.
    """
    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    candidates = ["/ur0:/data/libshacccg.suprx", "/ur0:/data/external/libshacccg.suprx"]
    try:
        ftp.voidcmd("TYPE I")
        for path in candidates:
            try:
                size = ftp.size(path)
                warn = t("ftp_ops.suspiciously_small_warn") if not size or size < 100_000 else ""
                print(f"{C.GREEN}[+]{C.RESET} {t('ftp_ops.libshacccg_exists', path=path, size=size, warn=warn)}")
            except all_errors as e:
                print(f"{C.RED}[-]{C.RESET} {t('ftp_ops.libshacccg_not_found', path=path, error=e)}")
    finally:
        _quit(ftp)


def verify_data_assets(project_cfg, global_cfg, local_reference_dir):
    """!
    @brief Compare first-level entry counts, per subfolder, between a local reference asset
           dump and `ux0:data/<slug>/` on the console.
    @param project_cfg Active project config dict (`vita_game_data_dir`, `slug`).
    @param global_cfg Global config dict (VPN bypass, etc).
    @param local_reference_dir Local directory (relative to the project dir) to compare
           against, e.g. an extracted APK assets folder.
    @note Shallow by design, not recursive: a full recursive listing on a folder with
          thousands of entries exhausts VitaShell's ftpd data connections. See
          docs/dev-notes/ftp_ops.md.
    """
    local_dir = Path(project_cfg["_project_dir"]) / local_reference_dir
    if not local_dir.is_dir():
        print(f"{C.RED}{t('ftp_ops.local_reference_not_found', path=local_dir)}{C.RESET}")
        return
    subfolders = sorted(d.name for d in local_dir.iterdir() if d.is_dir())
    if not subfolders:
        print(f"{C.RED}{t('ftp_ops.no_subfolders', path=local_dir)}{C.RESET}")
        return

    vita_game_dir = project_cfg.get("vita_game_data_dir")
    print(t("ftp_ops.comparing_subfolders", count=len(subfolders), dir=vita_game_dir))

    # One persistent connection for the whole comparison, instead of
    # reconnecting per subfolder (VitaShell's ftpd only tolerates so many
    # connect/disconnect cycles back to back -- see docs/dev-notes/ftp_ops.md).
    # A NOOP keep-alive between subfolders, and a single reconnect-and-retry
    # if the connection actually drops mid-loop, cover both failure modes
    # without paying a fresh handshake for every subfolder.
    ftp = _connect_with_retry(project_cfg, global_cfg)
    if not ftp:
        print(f"  {t('ftp_ops.subfolder_connect_failed', sub=subfolders[0])}")
        return

    any_mismatch = False
    try:
        for sub in subfolders:
            local_count = sum(1 for p in (local_dir / sub).iterdir()
                               if not p.name.startswith("._") and p.name != ".DS_Store")
            if not _keepalive(ftp):
                ftp = _connect_with_retry(project_cfg, global_cfg)
                if not ftp:
                    print(f"  {t('ftp_ops.subfolder_connect_failed', sub=sub)}")
                    any_mismatch = True
                    continue
            try:
                entries = _list_entries(ftp, f"{vita_game_dir}/{sub}")
                remote_count = sum(1 for name, _, _ in entries
                                    if not name.startswith("._") and name != ".DS_Store")
                status = "OK" if local_count == remote_count else "MISMATCH"
                if status == "MISMATCH":
                    any_mismatch = True
                print(f"  [{status}] {sub}/: local={local_count}  vita={remote_count}")
            except all_errors as e:
                print(f"  {t('ftp_ops.subfolder_list_failed', sub=sub, error=e)}")
                any_mismatch = True
    finally:
        if ftp:
            _quit(ftp)

    print()
    if any_mismatch:
        print(f"{C.YELLOW}{t('ftp_ops.folders_mismatch_warning')}{C.RESET}")
    else:
        print(f"{C.GREEN}{t('ftp_ops.all_folders_match')}{C.RESET}")


# ---------------------------------------------------------------------------
# Console profiles (multiple named PS Vita units: OLED / Slim / PSTV / ...)
# ---------------------------------------------------------------------------

def list_console_profiles(project_cfg):
    """!
    @brief Get every saved console profile for this project.
    @param project_cfg Active project config dict.
    @return dict of `{name: {"ip": ..., "port": ...}}`, `{}` if none saved yet.
    """
    return project_cfg.get("consoles", {})


def save_console_profile(project_cfg, name, ip, port):
    """!
    @brief Save (or overwrite) a named console profile and persist it.
    @param project_cfg Active project config dict (mutated in place).
    @param name Profile name (e.g. `"OLED"`, `"Slim"`, `"PSTV"`).
    @param ip Console's IP address.
    @param port Console's FTP port.
    """
    profiles = project_cfg.setdefault("consoles", {})
    profiles[name] = {"ip": ip, "port": port}
    cfgmod.save_project_config(project_cfg["_project_dir"], project_cfg)


def delete_console_profile(project_cfg, name):
    """!
    @brief Delete a saved console profile.
    @param project_cfg Active project config dict (mutated in place).
    @param name Profile name to delete.
    @return `True` if it existed and was deleted.
    """
    profiles = project_cfg.get("consoles", {})
    if name not in profiles:
        return False
    del profiles[name]
    cfgmod.save_project_config(project_cfg["_project_dir"], project_cfg)
    return True


def switch_console_profile(project_cfg, name):
    """!
    @brief Make a saved console profile the active one (`vita_ip`/`vita_port`).
    @param project_cfg Active project config dict (mutated in place).
    @param name Profile name to switch to.
    @return `True` if the profile existed and was switched to.
    @note Every FTP call site already reads `project_cfg["vita_ip"]`/`["vita_port"]`
          directly -- switching the active profile just overwrites those two
          keys, no other call site needs to know profiles exist at all.
    """
    profiles = project_cfg.get("consoles", {})
    if name not in profiles:
        return False
    project_cfg["vita_ip"] = profiles[name]["ip"]
    project_cfg["vita_port"] = profiles[name]["port"]
    cfgmod.save_project_config(project_cfg["_project_dir"], project_cfg)
    return True


def console_profiles_menu(project_cfg):
    """!
    @brief TUI submenu: list/add/switch/delete named console profiles.
    @param project_cfg Active project config dict.
    """
    def header():
        active_ip = project_cfg.get("vita_ip")
        active_port = project_cfg.get("vita_port")
        print(f"{C.BOLD}{t('ftp_ops.console_active', ip=active_ip, port=active_port)}{C.RESET}\n")
        profiles = list_console_profiles(project_cfg)
        if not profiles:
            print(t("ftp_ops.console_none_saved"))
            return
        for name, info in profiles.items():
            marker = f"{C.GREEN}★{C.RESET}" if (info.get("ip"), info.get("port")) == (active_ip, active_port) else " "
            print(f"  {marker} {name:<14} {info.get('ip')}:{info.get('port')}")

    def do_add():
        name = input(t("ftp_ops.console_name_prompt")).strip()
        if not name:
            return
        ip = input(t("ftp_ops.console_ip_prompt", default=project_cfg.get("vita_ip", "192.168.1.100"))).strip() \
            or project_cfg.get("vita_ip", "192.168.1.100")
        port_raw = input(t("ftp_ops.console_port_prompt", default=project_cfg.get("vita_port", 1337))).strip()
        port = int(port_raw) if port_raw.isdigit() else project_cfg.get("vita_port", 1337)
        save_console_profile(project_cfg, name, ip, port)
        print(f"{C.GREEN}{t('ftp_ops.console_saved', name=name)}{C.RESET}")

    def _pick_profile_name():
        profiles = list_console_profiles(project_cfg)
        if not profiles:
            print(t("ftp_ops.console_none_saved"))
            return None
        names = list(profiles)
        return tui.select_list(
            t("ftp_ops.console_choose_title"), names,
            label_fn=lambda n: f"{n}  {C.DIM}({profiles[n].get('ip')}:{profiles[n].get('port')}){C.RESET}",
        )

    def do_switch():
        name = _pick_profile_name()
        if name and switch_console_profile(project_cfg, name):
            print(f"{C.GREEN}{t('ftp_ops.console_switched', name=name)}{C.RESET}")

    def do_delete():
        name = _pick_profile_name()
        if name and delete_console_profile(project_cfg, name):
            print(f"{C.GREEN}{t('ftp_ops.console_deleted', name=name)}{C.RESET}")

    items = [
        (t("ftp_ops.console_add"), do_add),
        (t("ftp_ops.console_switch"), do_switch),
        (t("ftp_ops.console_delete"), do_delete),
    ]
    tui.run_menu(
        t("ftp_ops.console_menu_title"), items,
        breadcrumb=t("ftp_ops.console_breadcrumb", game_name=project_cfg["game_name"]),
        header_extra=header,
    )
