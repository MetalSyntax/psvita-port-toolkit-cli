"""
Todo lo que habla FTP con la PS Vita (VitaShell ftpd): subir VPK/eboot,
bajar logs/crash dumps (el último, elegir uno de la lista remota, o elegir
uno del historial local ya descargado -- la "memoria" local), sincronizar
shaders, verificar assets de datos, chequear libshacccg.suprx.

Generalizado de manage_vita.py (versión más evolucionada, la de Advena):
en vez de constantes hardcodeadas al tope del archivo, todo sale de la
config del proyecto activo (project_cfg / project_dir).
"""

import socket
import subprocess
import time
from ftplib import FTP, all_errors
from pathlib import Path

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
    "ftp_ops.vpks_found": {
        "es": "[*] {count} VPK(s) encontrado(s) (más reciente primero):",
        "en": "[*] {count} VPK(s) found (newest first):",
        "pt": "[*] {count} VPK(s) encontrado(s) (mais recente primeiro):",
    },
    "ftp_ops.cancel_bracket_option": {
        "es": "0. [ Cancelar ]",
        "en": "0. [ Cancel ]",
        "pt": "0. [ Cancelar ]",
    },
    "ftp_ops.choose_vpk_prompt": {
        "es": "\nElegí el VPK a subir [1-{max}] (Enter = el más reciente, 0 = cancelar): ",
        "en": "\nPick the VPK to upload [1-{max}] (Enter = most recent, 0 = cancel): ",
        "pt": "\nEscolha o VPK para enviar [1-{max}] (Enter = o mais recente, 0 = cancelar): ",
    },
    "ftp_ops.invalid_option": {
        "es": "[-] Opción inválida.",
        "en": "[-] Invalid option.",
        "pt": "[-] Opção inválida.",
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
        "es": "1. Descargar el ÚLTIMO log + último crash dump de la consola",
        "en": "1. Download the LATEST log + latest crash dump from the console",
        "pt": "1. Baixar o ÚLTIMO log + último crash dump do console",
    },
    "ftp_ops.menu_pick_log": {
        "es": "2. Elegir un log ESPECÍFICO de los que hay ahora en la consola",
        "en": "2. Pick a SPECIFIC log from what's currently on the console",
        "pt": "2. Escolher um log ESPECÍFICO dos que estão agora no console",
    },
    "ftp_ops.menu_pick_dump": {
        "es": "3. Elegir un crash dump ESPECÍFICO de los que hay ahora en la consola",
        "en": "3. Pick a SPECIFIC crash dump from what's currently on the console",
        "pt": "3. Escolher um crash dump ESPECÍFICO dos que estão agora no console",
    },
    "ftp_ops.menu_local_history": {
        "es": "4. Ver HISTORIAL local (ya descargados antes) y volver a analizar/abrir uno",
        "en": "4. View local HISTORY (previously downloaded) and re-analyze/open one",
        "pt": "4. Ver HISTÓRICO local (já baixados antes) e reanalisar/abrir um",
    },
    "ftp_ops.menu_cancel": {
        "es": "0. Cancelar",
        "en": "0. Cancel",
        "pt": "0. Cancelar",
    },
    "ftp_ops.option_prompt_default1": {
        "es": "Opción [1]: ",
        "en": "Option [1]: ",
        "pt": "Opção [1]: ",
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
    "ftp_ops.pick_prompt_cancel": {
        "es": "\nElegí [1-{max}], 0 para cancelar (Enter = el primero): ",
        "en": "\nPick [1-{max}], 0 to cancel (Enter = the first one): ",
        "pt": "\nEscolha [1-{max}], 0 para cancelar (Enter = o primeiro): ",
    },
    "ftp_ops.pick_prompt_nocancel": {
        "es": "\nElegí [1-{max}] (Enter = el primero): ",
        "en": "\nPick [1-{max}] (Enter = the first one): ",
        "pt": "\nEscolha [1-{max}] (Enter = o primeiro): ",
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def disconnect_vpn(global_cfg):
    """Best-effort: si el usuario configuró un comando de VPN a desconectar
    antes de hablar por FTP (ej. una VPN que enruta todo el tráfico y rompe
    la conexión LAN a la Vita), lo corre. Sin config, no hace nada."""
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
    """Si la Vita está en una subred /24 local, forzar el socket a salir por
    la IP física en esa subred (en vez de una posible ruta de VPN)."""
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
    if global_cfg:
        disconnect_vpn(global_cfg)
    return connect_ftp(project_cfg)


def create_dir_if_missing(ftp, path):
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
    """(nombre, es_dir, mtime_epoch_o_None) de 'path'. cwd() primero (falla
    claro si no existe) y MLSD/LIST sin argumentos después -- pasarle el path
    completo a LIST confunde al ftpd de VitaShell."""
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
# VPKs locales (para elegir cuál subir)
# ---------------------------------------------------------------------------

def _vpk_desc(filename):
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
    build_path = Path(project_dir) / build_dir
    if not build_path.is_dir():
        return []
    vpks = [p for p in build_path.iterdir() if p.suffix == ".vpk" and not p.name.startswith("._")]
    vpks.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return vpks


def choose_vpk(project_cfg):
    project_dir = project_cfg["_project_dir"]
    build_dir = project_cfg.get("build_dir", "build")
    vpks = list_local_vpks(project_dir, build_dir)
    if not vpks:
        print(f"{C.RED}{t('ftp_ops.no_vpk_found', build_dir=build_dir)}{C.RESET}")
        return None

    print(t("ftp_ops.vpks_found", count=len(vpks)))
    for i, p in enumerate(vpks, 1):
        desc = _vpk_desc(p.name)
        size_mb = p.stat().st_size / (1024 * 1024)
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        print(f"  {i:2d}. {p.name:<32}{desc:<26} {size_mb:6.2f} MB   {mtime}")
    print(f"   {t('ftp_ops.cancel_bracket_option')}")

    choice = input(t("ftp_ops.choose_vpk_prompt", max=len(vpks))).strip()
    if not choice:
        return vpks[0]
    if choice in ("0", "q"):
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(vpks):
        return vpks[int(choice) - 1]
    print(f"{C.RED}{t('ftp_ops.invalid_option')}{C.RESET}")
    return None


# ---------------------------------------------------------------------------
# Subida: VPK / eboot
# ---------------------------------------------------------------------------

def upload_vpk(project_cfg, global_cfg):
    local_vpk = choose_vpk(project_cfg)
    if not local_vpk:
        return
    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    try:
        downloads_dir = project_cfg.get("vita_downloads_dir", "/ux0:/downloads")
        create_dir_if_missing(ftp, downloads_dir)
        dest = f"{downloads_dir}/{local_vpk.name}"
        print(t("ftp_ops.uploading_file", local=local_vpk, dest=dest))
        with open(local_vpk, "rb") as f:
            ftp.storbinary(f"STOR {dest}", f)
        dest_display = f"{downloads_dir.replace('/ux0:', 'ux0:')}/{local_vpk.name}"
        print(f"{C.GREEN}{t('ftp_ops.vpk_upload_success', path=dest_display)}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.vpk_upload_failed', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def upload_eboot(project_cfg, global_cfg):
    project_dir = Path(project_cfg["_project_dir"])
    eboot = project_dir / project_cfg.get("build_dir", "build") / "eboot.bin"
    if not eboot.exists():
        print(f"{C.RED}{t('ftp_ops.eboot_not_found', path=eboot)}{C.RESET}")
        return

    size_kb = eboot.stat().st_size / 1024
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(eboot.stat().st_mtime))
    print(t("ftp_ops.eboot_found", size_kb=size_kb, mtime=mtime))

    titleid = project_cfg["titleid"]
    if not tui.confirm(t("ftp_ops.confirm_upload_eboot_only", titleid=titleid)):
        print(t("ftp_ops.cancelled"))
        return

    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    dest_dir = f"/ux0:/app/{titleid}"
    try:
        create_dir_if_missing(ftp, dest_dir)
        dest = f"{dest_dir}/eboot.bin"
        print(t("ftp_ops.uploading_file", local=eboot, dest=dest))
        with open(eboot, "rb") as f:
            ftp.storbinary(f"STOR {dest}", f)
        print(f"{C.GREEN}{t('ftp_ops.eboot_upload_success')}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.transfer_failed', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def _quit(ftp):
    try:
        ftp.quit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Descarga de logs / crash dumps: último, elegir de la lista remota, o
# historial local ya descargado ("memoria").
# ---------------------------------------------------------------------------

def _local_logs_dir(project_cfg):
    d = Path(project_cfg["_project_dir"]) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_remote_logs(ftp, project_cfg):
    """Lista (nombre, mtime) de logs .txt en VITA_LOGS_DIR, más reciente primero."""
    vita_logs_dir = project_cfg.get("vita_logs_dir", "/ux0:/data")
    entries = _list_entries(ftp, vita_logs_dir)
    logs = [(name, mtime) for name, is_dir, mtime in entries
            if not is_dir and (name.endswith(".txt") or "log" in name.lower())]
    logs.sort(key=lambda x: x[1] or "", reverse=True)
    return logs


def list_remote_dumps(ftp, project_cfg):
    """Lista (nombre, mtime) de crash dumps (psp2core*/.dmp) en VITA_DATA_DIR."""
    vita_data_dir = project_cfg.get("vita_data_dir", "/ux0:/data")
    entries = _list_entries(ftp, vita_data_dir)
    dumps = [(name, mtime) for name, is_dir, mtime in entries
             if not is_dir and (name.startswith("psp2core") or name.endswith(".dmp")) and not name.endswith(".tmp")]
    dumps.sort(key=lambda x: x[1] or "", reverse=True)
    return dumps


def _download_remote_file(ftp, remote_dir, remote_name, local_path):
    ftp.cwd(remote_dir)
    with open(local_path, "wb") as f:
        ftp.retrbinary(f"RETR {remote_name}", f.write)


def list_local_history(project_cfg, kind="logs"):
    """'Memoria' local: lo ya descargado antes a <project_dir>/logs/, más
    reciente primero. kind: 'logs' (.txt) o 'dumps' (psp2core*/.dmp/.analysis.txt)."""
    logs_dir = _local_logs_dir(project_cfg)
    if kind == "dumps":
        files = [p for p in logs_dir.iterdir()
                 if p.is_file() and (p.name.startswith("psp2core") or p.suffix == ".dmp")]
    else:
        files = [p for p in logs_dir.iterdir() if p.is_file() and p.suffix == ".txt" and ".analysis" not in p.name]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _pick_from_menu(title, options_with_dates, allow_cancel=True):
    """options_with_dates: lista de (label, fecha_str). Devuelve el índice
    elegido, o None si canceló."""
    print(f"\n{C.BOLD}{title}{C.RESET}")
    for i, (label, date_str) in enumerate(options_with_dates, 1):
        print(f"  {i:2d}. {label:<40} {C.DIM}{date_str}{C.RESET}")
    if allow_cancel:
        print(f"   {t('ftp_ops.cancel_bracket_option')}")
    if allow_cancel:
        prompt = t("ftp_ops.pick_prompt_cancel", max=len(options_with_dates))
    else:
        prompt = t("ftp_ops.pick_prompt_nocancel", max=len(options_with_dates))
    choice = input(prompt).strip()
    if not choice:
        return 0
    if choice in ("0", "q") and allow_cancel:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(options_with_dates):
        return int(choice) - 1
    print(f"{C.RED}{t('ftp_ops.invalid_option')}{C.RESET}")
    return None


def download_logs_and_dumps(project_cfg, global_cfg):
    """Menú: descargar el ÚLTIMO log/dump, elegir uno ESPECÍFICO de lo que
    hay ahora en la consola, o abrir uno del HISTORIAL local ya descargado."""
    print(f"{C.BOLD}{t('ftp_ops.what_to_do')}{C.RESET}")
    print(f"  {t('ftp_ops.menu_download_latest')}")
    print(f"  {t('ftp_ops.menu_pick_log')}")
    print(f"  {t('ftp_ops.menu_pick_dump')}")
    print(f"  {t('ftp_ops.menu_local_history')}")
    print(f"  {t('ftp_ops.menu_cancel')}")
    choice = input(t("ftp_ops.option_prompt_default1")).strip() or "1"

    if choice == "0":
        return
    if choice == "4":
        _browse_local_history(project_cfg)
        return

    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    try:
        if choice == "1":
            _download_latest(ftp, project_cfg, want_dump=True, want_log=True)
        elif choice == "2":
            logs = list_remote_logs(ftp, project_cfg)
            if not logs:
                print(f"{C.YELLOW}{t('ftp_ops.no_logs_found', dir=project_cfg.get('vita_logs_dir'))}{C.RESET}")
                return
            idx = _pick_from_menu(t("ftp_ops.logs_available_title"),
                                  [(name, mtime or "") for name, mtime in logs])
            if idx is None:
                return
            name, _ = logs[idx]
            local_path = _local_logs_dir(project_cfg) / name
            _download_remote_file(ftp, project_cfg.get("vita_logs_dir"), name, local_path)
            print(f"{C.GREEN}{t('ftp_ops.downloaded_at', path=local_path)}{C.RESET}")
        elif choice == "3":
            dumps = list_remote_dumps(ftp, project_cfg)
            if not dumps:
                print(f"{C.YELLOW}{t('ftp_ops.no_dumps_found', dir=project_cfg.get('vita_data_dir'))}{C.RESET}")
                return
            idx = _pick_from_menu(t("ftp_ops.dumps_available_title"),
                                  [(name, mtime or "") for name, mtime in dumps])
            if idx is None:
                return
            name, _ = dumps[idx]
            local_path = _local_logs_dir(project_cfg) / f"{project_cfg['slug']}-{name}"
            _download_remote_file(ftp, project_cfg.get("vita_data_dir"), name, local_path)
            print(f"{C.GREEN}{t('ftp_ops.downloaded_at', path=local_path)}{C.RESET}")
            _offer_analyze(project_cfg, local_path)
    except all_errors as e:
        print(f"{C.RED}{t('ftp_ops.ftp_generic_error', error=e)}{C.RESET}")
    finally:
        _quit(ftp)


def _download_latest(ftp, project_cfg, want_dump=True, want_log=True):
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


def _offer_analyze(project_cfg, dump_path):
    if tui.confirm(t("ftp_ops.confirm_analyze_dump")):
        from . import crash_analyzer
        crash_analyzer.analyze(project_cfg, str(dump_path))


def _browse_local_history(project_cfg):
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

    labeled = [(f"[{kind}] {p.name}", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)))
               for kind, p in options]
    idx = _pick_from_menu(t("ftp_ops.local_history_title"), labeled)
    if idx is None:
        return
    kind, path = options[idx]
    if kind == "dump":
        _offer_analyze(project_cfg, path)
    else:
        print(f"\n{C.DIM}{t('ftp_ops.log_tail_header', name=path.name)}{C.RESET}")
        lines = path.read_text(errors="ignore").splitlines()
        print("\n".join(lines[-60:]))


# ---------------------------------------------------------------------------
# Shaders (GLSL volcado <-> CG traducido)
# ---------------------------------------------------------------------------

def download_glsl_shaders(project_cfg, global_cfg):
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
# Chequeos de salud
# ---------------------------------------------------------------------------

def check_libshacccg(project_cfg, global_cfg):
    """libshacccg.suprx corrupto/faltante produce 'fatal internal error' en
    CUALQUIER shader, incluso uno trivial -- vale la pena chequearlo aparte."""
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
    """Compara cantidad de entradas de primer nivel (chequeo superficial, no
    recursivo -- una carpeta como 3d/ con miles de subcarpetas agota las
    conexiones de datos del ftpd de VitaShell si se recorre entera) entre el
    volcado local de referencia y ux0:data/<slug>/ en la consola."""
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

    any_mismatch = False
    for sub in subfolders:
        local_count = sum(1 for p in (local_dir / sub).iterdir()
                           if not p.name.startswith("._") and p.name != ".DS_Store")
        ftp = _connect(project_cfg, global_cfg)
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
            _quit(ftp)

    print()
    if any_mismatch:
        print(f"{C.YELLOW}{t('ftp_ops.folders_mismatch_warning')}{C.RESET}")
    else:
        print(f"{C.GREEN}{t('ftp_ops.all_folders_match')}{C.RESET}")
