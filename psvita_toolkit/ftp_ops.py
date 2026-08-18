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
    print(f"[*] Desconectando VPN ({cmd})...")
    try:
        r = subprocess.run(cmd.split(), capture_output=True, text=True)
        if r.returncode == 0 or "not connected" in (r.stderr + r.stdout).lower():
            print(f"{C.GREEN}[+] VPN desconectada (o ya lo estaba).{C.RESET}")
        else:
            print(f"{C.YELLOW}[!] {r.stderr.strip() or r.stdout.strip()}{C.RESET}")
    except FileNotFoundError:
        print(f"{C.YELLOW}[!] Comando de VPN no encontrado en el PATH: {cmd}{C.RESET}")
    except Exception as e:
        print(f"{C.RED}[-] Error inesperado desconectando VPN: {e}{C.RESET}")


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
    print(f"[*] Conectando a la PS Vita en {vita_ip}:{vita_port}...")
    local_ip = _local_ip_for_route(vita_ip)
    source_addr = (local_ip, 0) if local_ip else None
    if local_ip:
        print(f"[*] Forzando ruta local vía {local_ip} (bypass de VPN si hay alguna activa).")
    try:
        ftp = FTP()
        ftp.connect(vita_ip, vita_port, timeout=10, source_address=source_addr)
        ftp.login()
        print(f"{C.GREEN}[+] Conexión FTP establecida.{C.RESET}")
        return ftp
    except all_errors as e:
        print(f"{C.RED}[-] Error al conectar por FTP a la PS Vita: {e}{C.RESET}")
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
    print(f"[*] El directorio '{path}' no existe. Creándolo...")
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
        print(f"{C.GREEN}[+] Directorio '{path}' listo.{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}[-] No se pudo crear '{path}': {e}{C.RESET}")


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
        "debug_verbose": "Debug Verboso", "relwithdebinfo": "Release + Debug Info",
        "minsizerel": "MinSizeRel", "debug": "Debug", "release": "Release",
        "glsl_dump": "GLSL + Shader Dump", "cg": "Shaders CG",
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
    vpks = list_local_vpks(project_dir, project_cfg.get("build_dir", "build"))
    if not vpks:
        print(f"{C.RED}[-] No se encontró ningún .vpk en '{project_cfg.get('build_dir', 'build')}/'. "
              f"Compilá el proyecto primero (opción 'Compilar').{C.RESET}")
        return None

    print(f"[*] {len(vpks)} VPK(s) encontrado(s) (más reciente primero):")
    for i, p in enumerate(vpks, 1):
        desc = _vpk_desc(p.name)
        size_mb = p.stat().st_size / (1024 * 1024)
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        print(f"  {i:2d}. {p.name:<32}{desc:<26} {size_mb:6.2f} MB   {mtime}")
    print("   0. [ Cancelar ]")

    choice = input(f"\nElegí el VPK a subir [1-{len(vpks)}] (Enter = el más reciente, 0 = cancelar): ").strip()
    if not choice:
        return vpks[0]
    if choice in ("0", "q"):
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(vpks):
        return vpks[int(choice) - 1]
    print(f"{C.RED}[-] Opción inválida.{C.RESET}")
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
        print(f"[*] Subiendo {local_vpk} a {dest}...")
        with open(local_vpk, "rb") as f:
            ftp.storbinary(f"STOR {dest}", f)
        print(f"{C.GREEN}[+] Transferencia exitosa. Instalá el VPK desde VitaShell en "
              f"{downloads_dir.replace('/ux0:', 'ux0:')}/{local_vpk.name}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}[-] Falló la transferencia del VPK: {e}{C.RESET}")
    finally:
        _quit(ftp)


def upload_eboot(project_cfg, global_cfg):
    project_dir = Path(project_cfg["_project_dir"])
    eboot = project_dir / project_cfg.get("build_dir", "build") / "eboot.bin"
    if not eboot.exists():
        print(f"{C.RED}[-] No se encontró '{eboot}'. Compilá el proyecto primero.{C.RESET}")
        return

    size_kb = eboot.stat().st_size / 1024
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(eboot.stat().st_mtime))
    print(f"[*] eboot.bin encontrado: {size_kb:.1f} KB ({mtime})")

    titleid = project_cfg["titleid"]
    if not tui.confirm(f"¿Subir SOLO el eboot.bin a ux0:app/{titleid}/?"):
        print("[*] Cancelado.")
        return

    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    dest_dir = f"/ux0:/app/{titleid}"
    try:
        create_dir_if_missing(ftp, dest_dir)
        dest = f"{dest_dir}/eboot.bin"
        print(f"[*] Subiendo {eboot} a {dest}...")
        with open(eboot, "rb") as f:
            ftp.storbinary(f"STOR {dest}", f)
        print(f"{C.GREEN}[+] eboot.bin subido. Ya podés iniciar el juego sin reinstalar el VPK entero.{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}[-] Falló la transferencia: {e}{C.RESET}")
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
        print("   0. [ Cancelar ]")
    choice = input(f"\nElegí [1-{len(options_with_dates)}]"
                   f"{', 0 para cancelar' if allow_cancel else ''} (Enter = el primero): ").strip()
    if not choice:
        return 0
    if choice in ("0", "q") and allow_cancel:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(options_with_dates):
        return int(choice) - 1
    print(f"{C.RED}[-] Opción inválida.{C.RESET}")
    return None


def download_logs_and_dumps(project_cfg, global_cfg):
    """Menú: descargar el ÚLTIMO log/dump, elegir uno ESPECÍFICO de lo que
    hay ahora en la consola, o abrir uno del HISTORIAL local ya descargado."""
    print(f"{C.BOLD}¿Qué querés hacer?{C.RESET}")
    print("  1. Descargar el ÚLTIMO log + último crash dump de la consola")
    print("  2. Elegir un log ESPECÍFICO de los que hay ahora en la consola")
    print("  3. Elegir un crash dump ESPECÍFICO de los que hay ahora en la consola")
    print("  4. Ver HISTORIAL local (ya descargados antes) y volver a analizar/abrir uno")
    print("  0. Cancelar")
    choice = input("Opción [1]: ").strip() or "1"

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
                print(f"{C.YELLOW}[-] No hay logs en {project_cfg.get('vita_logs_dir')}.{C.RESET}")
                return
            idx = _pick_from_menu("Logs disponibles en la consola:",
                                  [(name, mtime or "") for name, mtime in logs])
            if idx is None:
                return
            name, _ = logs[idx]
            local_path = _local_logs_dir(project_cfg) / name
            _download_remote_file(ftp, project_cfg.get("vita_logs_dir"), name, local_path)
            print(f"{C.GREEN}[+] Descargado en {local_path}{C.RESET}")
        elif choice == "3":
            dumps = list_remote_dumps(ftp, project_cfg)
            if not dumps:
                print(f"{C.YELLOW}[-] No hay crash dumps en {project_cfg.get('vita_data_dir')}.{C.RESET}")
                return
            idx = _pick_from_menu("Crash dumps disponibles en la consola:",
                                  [(name, mtime or "") for name, mtime in dumps])
            if idx is None:
                return
            name, _ = dumps[idx]
            local_path = _local_logs_dir(project_cfg) / f"{project_cfg['slug']}-{name}"
            _download_remote_file(ftp, project_cfg.get("vita_data_dir"), name, local_path)
            print(f"{C.GREEN}[+] Descargado en {local_path}{C.RESET}")
            _offer_analyze(project_cfg, local_path)
    except all_errors as e:
        print(f"{C.RED}[-] Error de FTP: {e}{C.RESET}")
    finally:
        _quit(ftp)


def _download_latest(ftp, project_cfg, want_dump=True, want_log=True):
    if want_dump:
        dumps = list_remote_dumps(ftp, project_cfg)
        if dumps:
            name, _ = dumps[0]
            local_path = _local_logs_dir(project_cfg) / f"{project_cfg['slug']}-{name}"
            print(f"[+] Último dump: '{name}' -> descargando...")
            _download_remote_file(ftp, project_cfg.get("vita_data_dir"), name, local_path)
            print(f"{C.GREEN}[+] Descargado en {local_path}{C.RESET}")
            _offer_analyze(project_cfg, local_path)
        else:
            print(f"{C.DIM}[*] No hay crash dumps pendientes en la consola.{C.RESET}")

    if want_log:
        logs = list_remote_logs(ftp, project_cfg)
        if logs:
            name, _ = logs[0]
            local_path = _local_logs_dir(project_cfg) / name
            print(f"[+] Último log: '{name}' -> descargando...")
            _download_remote_file(ftp, project_cfg.get("vita_logs_dir"), name, local_path)
            print(f"{C.GREEN}[+] Descargado en {local_path}{C.RESET}")
        else:
            print(f"{C.YELLOW}[-] No se encontraron logs en {project_cfg.get('vita_logs_dir')}.{C.RESET}")


def _offer_analyze(project_cfg, dump_path):
    if tui.confirm("¿Analizar este crash dump ahora con el analizador integrado?"):
        from . import crash_analyzer
        crash_analyzer.analyze(project_cfg, str(dump_path))


def _browse_local_history(project_cfg):
    print(f"\n{C.BOLD}Logs ya descargados antes:{C.RESET}")
    logs = list_local_history(project_cfg, "logs")
    dumps = list_local_history(project_cfg, "dumps")
    if not logs and not dumps:
        print(f"{C.DIM}(vacío -- todavía no descargaste nada a este proyecto){C.RESET}")
        return

    options = []
    for p in logs:
        options.append(("log", p))
    for p in dumps:
        options.append(("dump", p))
    options.sort(key=lambda t: t[1].stat().st_mtime, reverse=True)

    labeled = [(f"[{kind}] {p.name}", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)))
               for kind, p in options]
    idx = _pick_from_menu("Historial local (más reciente primero):", labeled)
    if idx is None:
        return
    kind, path = options[idx]
    if kind == "dump":
        _offer_analyze(project_cfg, path)
    else:
        print(f"\n{C.DIM}--- {path.name} (últimas 60 líneas) ---{C.RESET}")
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
            print(f"{C.YELLOW}[-] No hay shaders .glsl en {vita_glsl_dir}.{C.RESET}")
            return
        print(f"[+] {len(files)} shader(s) encontrados. Descargando...")
        for name in files:
            local_path = local_dir / name
            with open(local_path, "wb") as f:
                ftp.retrbinary(f"RETR {name}", f.write)
            print(f"  -> {name}")
        print(f"{C.GREEN}[+] Todos los shaders descargados en {local_dir}{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}[-] Error al listar/descargar shaders: {e}{C.RESET}")
    finally:
        _quit(ftp)


def upload_cg_shaders(project_cfg, global_cfg):
    local_dir = Path(project_cfg["_project_dir"]) / "assets" / "cg"
    if not local_dir.is_dir():
        print(f"{C.RED}[-] No se encontró '{local_dir}'.{C.RESET}")
        return
    cg_files = sorted(p for p in local_dir.iterdir() if p.suffix == ".cg" and not p.name.startswith("._"))
    if not cg_files:
        print(f"{C.RED}[-] No hay archivos .cg en '{local_dir}'.{C.RESET}")
        return

    ftp = _connect(project_cfg, global_cfg)
    if not ftp:
        return
    vita_cg_dir = project_cfg.get("vita_cg_dir")
    try:
        create_dir_if_missing(ftp, vita_cg_dir)
        print(f"[*] Subiendo {len(cg_files)} shader(s) .cg a {vita_cg_dir}...")
        for p in cg_files:
            with open(p, "rb") as f:
                ftp.storbinary(f"STOR {vita_cg_dir}/{p.name}", f)
            print(f"  -> {p.name}")
        print(f"{C.GREEN}[+] Todos los shaders .cg subidos.{C.RESET}")
    except all_errors as e:
        print(f"{C.RED}[-] Falló la subida: {e}{C.RESET}")
    finally:
        _quit(ftp)


def sync_shaders(project_cfg, global_cfg):
    print("[*] Paso 1/2: descargando shaders GLSL sin traducir...")
    download_glsl_shaders(project_cfg, global_cfg)

    local_glsl = Path(project_cfg["_project_dir"]) / "glsl_dump"
    local_cg = Path(project_cfg["_project_dir"]) / "assets" / "cg"
    dumped = {p.stem for p in local_glsl.glob("*.glsl")} if local_glsl.is_dir() else set()
    translated = {p.stem for p in local_cg.glob("*.cg")} if local_cg.is_dir() else set()
    missing = sorted(dumped - translated)
    if missing:
        print(f"{C.YELLOW}[!] {len(missing)} shader(s) todavía SIN traducir a .cg:{C.RESET}")
        for h in missing:
            print(f"  - {h}.glsl")
    else:
        print(f"{C.GREEN}[+] Todos los shaders volcados ya tienen su .cg.{C.RESET}")

    print("[*] Paso 2/2: subiendo los .cg traducidos...")
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
                warn = "  <-- sospechosamente chico/vacío!" if not size or size < 100_000 else ""
                print(f"{C.GREEN}[+]{C.RESET} {path}: existe, {size} bytes{warn}")
            except all_errors as e:
                print(f"{C.RED}[-]{C.RESET} {path}: no encontrado ({e})")
    finally:
        _quit(ftp)


def verify_data_assets(project_cfg, global_cfg, local_reference_dir):
    """Compara cantidad de entradas de primer nivel (chequeo superficial, no
    recursivo -- una carpeta como 3d/ con miles de subcarpetas agota las
    conexiones de datos del ftpd de VitaShell si se recorre entera) entre el
    volcado local de referencia y ux0:data/<slug>/ en la consola."""
    local_dir = Path(project_cfg["_project_dir"]) / local_reference_dir
    if not local_dir.is_dir():
        print(f"{C.RED}[-] No se encontró la referencia local '{local_dir}'.{C.RESET}")
        return
    subfolders = sorted(d.name for d in local_dir.iterdir() if d.is_dir())
    if not subfolders:
        print(f"{C.RED}[-] '{local_dir}' no tiene subcarpetas.{C.RESET}")
        return

    vita_game_dir = project_cfg.get("vita_game_data_dir")
    print(f"[*] Comparando {len(subfolders)} subcarpeta(s) (local vs. {vita_game_dir}, chequeo superficial)...\n")

    any_mismatch = False
    for sub in subfolders:
        local_count = sum(1 for p in (local_dir / sub).iterdir()
                           if not p.name.startswith("._") and p.name != ".DS_Store")
        ftp = _connect(project_cfg, global_cfg)
        if not ftp:
            print(f"  [?] {sub}/: no se pudo conectar")
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
            print(f"  [?] {sub}/: no se pudo listar ({e})")
            any_mismatch = True
        finally:
            _quit(ftp)

    print()
    if any_mismatch:
        print(f"{C.YELLOW}[!] Alguna(s) carpeta(s) no coinciden -- probablemente quedaron a mitad de copiar.{C.RESET}")
    else:
        print(f"{C.GREEN}[+] Todas las carpetas coinciden en cantidad de archivos.{C.RESET}")
