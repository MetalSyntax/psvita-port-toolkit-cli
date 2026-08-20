"""!
@file shader_live_reload.py
@brief "Live reload" for `.cg` shaders during iteration: watches
       `assets/cg/*.cg` for changes and auto-validates + auto-uploads each
       one to the real PS Vita over FTP the moment it's saved.

@details
This is the plan's "OTA hot-patching" item, scoped to what this toolkit can
actually back up. It speeds up the edit -> validate -> upload part of the
loop -- the part entirely under this toolkit's control -- to near-zero
manual effort. It does NOT claim the other half: making the running GAME
notice the new file on `ux0:` and actually reload/rebind the shader without
a restart needs the game/loader's own code to watch for that and re-run its
shader-compile step, which is a per-project decision this toolkit doesn't
own (same "not something we can guarantee any given loader implements"
posture as `gdb_bridge.py`'s gdbstub assumption). If the loader already has
such a watch loop, this closes the gap right up to it; if it doesn't, this
still turns "save, alt-tab, run the upload menu, wait" into "save" -- see
`docs/dev-notes/shader_live_reload.md`.

Reuses `utils.py`'s existing `guess_shader_profile()`/`validate_shader()`
(same `psp2cgc` check every other shader path in this toolkit already goes
through -- an invalid shader is reported and skipped, never uploaded) and
keeps ONE FTP connection alive for the whole watch session (`ftp_ops._keepalive()`,
reconnecting on drop) instead of reconnecting per file, which
`so_patcher.md`'s neighbor modules already flagged as something VitaShell's
ftpd doesn't always like done rapidly.
"""

import time
from pathlib import Path

from . import ftp_ops
from . import i18n
from . import tui
from . import utils
from .i18n import t
from .tui import C

STRINGS = {
    "shader_live_reload.menu_title": {
        "es": "Live Reload de Shaders (auto-subir .cg al guardar)",
        "en": "Shader Live Reload (auto-upload .cg on save)",
        "pt": "Live Reload de Shaders (auto-enviar .cg ao salvar)",
    },
    "shader_live_reload.menu_watch": {
        "es": "Vigilar assets/cg/ y subir automáticamente hasta Ctrl+C",
        "en": "Watch assets/cg/ and auto-upload until Ctrl+C",
        "pt": "Vigiar assets/cg/ e enviar automaticamente até Ctrl+C",
    },
    "shader_live_reload.connect_failed": {
        "es": "[-] No se pudo conectar por FTP -- se cancela el watch.",
        "en": "[-] Couldn't connect over FTP -- cancelling the watch.",
        "pt": "[-] Não foi possível conectar por FTP -- cancelando o watch.",
    },
    "shader_live_reload.watching": {
        "es": "[*] Vigilando {dir} -- Ctrl+C para detener. El juego sigue necesitando recargar el shader por su cuenta (ver docs/dev-notes/shader_live_reload.md).",
        "en": "[*] Watching {dir} -- Ctrl+C to stop. The game still needs to reload the shader on its own (see docs/dev-notes/shader_live_reload.md).",
        "pt": "[*] Vigiando {dir} -- Ctrl+C para parar. O jogo ainda precisa recarregar o shader por conta própria (veja docs/dev-notes/shader_live_reload.md).",
    },
    "shader_live_reload.invalid_skip": {
        "es": "  [-] {name}: inválido según psp2cgc, no se sube -- {message}",
        "en": "  [-] {name}: invalid per psp2cgc, not uploading -- {message}",
        "pt": "  [-] {name}: inválido segundo psp2cgc, não será enviado -- {message}",
    },
    "shader_live_reload.uploaded": {
        "es": "  [+] {name} subido a {vita_dir}",
        "en": "  [+] {name} uploaded to {vita_dir}",
        "pt": "  [+] {name} enviado para {vita_dir}",
    },
    "shader_live_reload.upload_failed": {
        "es": "  [-] {name}: falló la subida -- {error}",
        "en": "  [-] {name}: upload failed -- {error}",
        "pt": "  [-] {name}: falha ao enviar -- {error}",
    },
    "shader_live_reload.reconnect_failed": {
        "es": "[-] Se perdió la conexión FTP y no se pudo reconectar -- se detiene el watch.",
        "en": "[-] Lost the FTP connection and couldn't reconnect -- stopping the watch.",
        "pt": "[-] A conexão FTP caiu e não foi possível reconectar -- parando o watch.",
    },
    "shader_live_reload.stopped": {
        "es": "[+] Watch detenido -- {count} subida(s) en esta sesión.",
        "en": "[+] Watch stopped -- {count} upload(s) this session.",
        "pt": "[+] Watch parado -- {count} envio(s) nesta sessão.",
    },
}
i18n.register(STRINGS)

DEFAULT_POLL_INTERVAL = 2


def watch_and_upload_shaders(project_cfg, global_cfg, poll_interval=DEFAULT_POLL_INTERVAL, stop_event=None):
    """!
    @brief Poll `assets/cg/*.cg` for changed files; validate each with
           `psp2cgc` and upload it to the console over FTP the moment it
           changes, until interrupted.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    @param poll_interval Seconds between mtime checks.
    @param stop_event Optional `threading.Event` for a clean shutdown from
           another thread, same convention as `mem_profiler.run_memory_profiler()`.
    @note The FIRST poll uploads every `.cg` already present (nothing has a
          recorded mtime yet) -- an initial sync, not just future changes.
    """
    cg_dir = Path(project_cfg["_project_dir"]) / "assets" / "cg"
    vita_cg_dir = project_cfg.get("vita_cg_dir")

    ftp = ftp_ops._connect(project_cfg, global_cfg)
    if not ftp:
        print(f"{C.RED}{t('shader_live_reload.connect_failed')}{C.RESET}")
        return
    ftp_ops.create_dir_if_missing(ftp, vita_cg_dir)

    print(t("shader_live_reload.watching", dir=cg_dir))
    mtimes = {}
    upload_count = 0

    try:
        while not (stop_event and stop_event.is_set()):
            time.sleep(poll_interval)
            if not cg_dir.is_dir():
                continue

            for cg_path in sorted(cg_dir.glob("*.cg")):
                mtime = cg_path.stat().st_mtime
                if mtimes.get(cg_path) == mtime:
                    continue
                mtimes[cg_path] = mtime

                profile = utils.guess_shader_profile(cg_path)
                if profile:
                    validated_ok, message = utils.validate_shader(cg_path, profile, global_cfg)
                    if validated_ok is False:
                        print(t("shader_live_reload.invalid_skip", name=cg_path.name, message=message))
                        continue

                if not ftp_ops._keepalive(ftp):
                    ftp = ftp_ops._connect(project_cfg, global_cfg)
                    if not ftp:
                        print(f"{C.RED}{t('shader_live_reload.reconnect_failed')}{C.RESET}")
                        return

                try:
                    with open(cg_path, "rb") as f:
                        ftp.storbinary(f"STOR {vita_cg_dir}/{cg_path.name}", f)
                except ftp_ops.all_errors as e:
                    print(t("shader_live_reload.upload_failed", name=cg_path.name, error=e))
                    continue

                upload_count += 1
                print(f"{C.GREEN}{t('shader_live_reload.uploaded', name=cg_path.name, vita_dir=vita_cg_dir)}{C.RESET}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 -- shutting down either way
            pass
        print(f"{C.DIM}{t('shader_live_reload.stopped', count=upload_count)}{C.RESET}")


def live_reload_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: run the watch-and-upload loop until Ctrl+C.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    tui.run_menu(
        t("shader_live_reload.menu_title"),
        [(t("shader_live_reload.menu_watch"), lambda: watch_and_upload_shaders(project_cfg, global_cfg))],
    )
