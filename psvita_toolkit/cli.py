"""!
@file cli.py
@brief Headless CLI: `psvita-toolkit <subcommand> ...` argument parsing and dispatch.

@details
Every subcommand is a thin, non-interactive wrapper around functions the TUI
already calls -- `doctor.run_doctor()`, `build_deploy._run_build()`,
`ftp_ops.upload_eboot()`/`upload_vpk()`, `crash_analyzer.analyze()`,
`init_port.run_wizard_headless()`, `livearea.process_asset()`,
`utils.clean_macos_junk()`. No subcommand opens the arrow-key TUI or calls
`input()`: an editor, a shell alias, or a CI job can run this with a fixed
argument list and get a predictable exit code (`0` success, `1` failure,
`2` bad invocation/missing config).

`__main__.py` only imports this module and calls `dispatch(sys.argv[1:])`
when there ARE arguments; with none, it falls through to the interactive TUI
exactly as before -- see `docs/dev-notes/cli.md` for why the dispatch lives
here instead of inline in `__main__.py`.
"""

import argparse
import sys
from pathlib import Path

from . import i18n
from .i18n import t
from . import config as cfgmod


def _fail(msg):
    """!
    @brief Print an error to stderr and return the standard failure exit code.
    @param msg Error message to print.
    @return `1`, for `return _fail(...)` at every subcommand's error paths.
    """
    print(f"[-] {msg}", file=sys.stderr)
    return 1


def _load_global_config():
    """!
    @brief Load global config for headless use, without ever prompting.
    @return `(cfg, error_message)` -- `error_message` is `None` on success,
            or an explanation of which required key is missing (the CLI has
            no TTY to ask for it on -- run the interactive TUI once first).
    """
    cfg = cfgmod.load_global_config()
    missing = [k for k in cfgmod.REQUIRED_GLOBAL_KEYS if not cfg.get(k)]
    if missing:
        return cfg, (
            f"Missing global config key(s): {', '.join(missing)}. "
            f"Run `psvita-toolkit` once interactively to set them up "
            f"(saved to {cfgmod.GLOBAL_CONFIG_PATH})."
        )
    return cfg, None


def _load_project(project_arg):
    """!
    @brief Resolve and load a project's config for headless use.
    @param project_arg Path to the project directory, or `None` to fall back
           to the current working directory.
    @return `(project_cfg, error_message)` -- `error_message` is `None` on success.
    """
    project_dir = Path(project_arg).expanduser().resolve() if project_arg else Path.cwd()
    cfg = cfgmod.load_project_config(project_dir)
    if cfg is None:
        return None, (
            f"'{project_dir}' has no {cfgmod.PROJECT_CONFIG_FILENAME} -- "
            f"pass --project pointing at an already-adopted port, or open it "
            f"once with the interactive TUI to adopt it."
        )
    return cfg, None


def build_parser():
    """!
    @brief Build the top-level `argparse.ArgumentParser` for every subcommand.
    @return Configured `ArgumentParser`, with subcommands attached.
    """
    parser = argparse.ArgumentParser(
        prog="psvita-toolkit",
        description="PS Vita port toolkit. Run with no arguments for the interactive TUI.",
    )
    sub = parser.add_subparsers(dest="command")

    p_doctor = sub.add_parser("doctor", help="Check host dependencies (VITASDK, Docker, jadx, CMake/Ninja, Python packages).")
    p_doctor.add_argument("--plain", action="store_true", help="Disable ANSI colors (for log files/CI).")

    p_build = sub.add_parser("build", help="Build the project (build.sh, or direct CMake if it has none).")
    p_build.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_build.add_argument("--preset", default="debug",
                          choices=["debug", "release", "relwithdebinfo", "minsizerel"],
                          help="Build preset (default: debug).")
    p_build.add_argument("--build-dir", help="Override the project's configured build output directory.")
    p_build.add_argument("--clean", action="store_true", help="Delete the build directory before building.")

    p_deploy = sub.add_parser("deploy", help="Upload a build to the physical PS Vita over FTP.")
    p_deploy.add_argument("--project", help="Path to the port directory (default: current directory).")
    target = p_deploy.add_mutually_exclusive_group(required=True)
    target.add_argument("--eboot", action="store_true", help="Upload only eboot.bin (fast iterate).")
    target.add_argument("--vpk", action="store_true", help="Upload the full .vpk to ux0:downloads/.")
    p_deploy.add_argument("--vpk-path", help="Explicit .vpk path (only with --vpk; default: newest in the build dir).")
    p_deploy.add_argument("--ip", help="Override the project's configured PS Vita IP for this run.")
    p_deploy.add_argument("--yes", action="store_true", help="Don't ask for confirmation (only relevant to --eboot).")

    p_analyze = sub.add_parser("analyze", help="Analyze a .psp2dmp/psp2core crash dump.")
    p_analyze.add_argument("dump_path", help="Path to the .psp2dmp/psp2core-* file.")
    p_analyze.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_analyze.add_argument("--elf", help="Path to the Vita .elf (default: auto-detected).")
    p_analyze.add_argument("--so", help="Path to the original Android .so (default: auto-detected).")

    p_init = sub.add_parser("init", help="Create a new port from an Android .apk (non-interactive).")
    p_init.add_argument("--apk", required=True, help="Path to the source .apk.")
    p_init.add_argument("--name", required=True, help="Display game name.")
    p_init.add_argument("--titleid", help="9-character TITLEID (required unless resuming this project's own).")
    p_init.add_argument("--slug", help="Internal slug (default: derived from --name).")
    p_init.add_argument("--folder", help="Destination folder name under base_dir (default: derived from --name).")
    p_init.add_argument("--vita-ip", default="192.168.1.100", help="Test PS Vita IP (default: 192.168.1.100).")

    p_livearea = sub.add_parser("livearea", help="Convert LiveArea assets without the interactive picker.")
    p_livearea.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_livearea.add_argument("--auto", metavar="SRC_DIR",
                             help="Directory containing bg0.png/pic0.png/icon0.png/startup.png to convert in bulk.")
    p_livearea.add_argument("--mode", default="crop", choices=["crop", "fit", "stretch"],
                             help="Resize mode (default: crop).")
    p_livearea.add_argument("--template", action="store_true",
                             help="Generate the standard 'gate' template.xml.")
    p_livearea.add_argument("--bgm", metavar="AUDIO_PATH",
                             help="Convert AUDIO_PATH (.wav/.mp3/.at9) to bgm.at9.")
    p_livearea.add_argument("--validate", action="store_true",
                             help="Validate every LiveArea asset before packaging.")

    sub.add_parser("clean-junk", help="Remove macOS AppleDouble (._*) junk files from the project.").add_argument(
        "--project", help="Path to the port directory (default: current directory).")

    p_jni = sub.add_parser("jni-analyze", help="Detect middleware, generate JNI stub candidates, document lifecycle methods.")
    p_jni.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_jni.add_argument("--out-dir", help="Where to write generated_jni_table.h/_stubs.c (default: <project>/source or project root).")

    p_logs = sub.add_parser("logs-live", help="Listen for UDP debug logs from a running game and save them (Ctrl+C to stop).")
    p_logs.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_logs.add_argument("--port", type=int, help="UDP port to listen on (default: 9999).")
    p_logs.add_argument("--filter", help="Only show/save lines matching this regex.")

    p_ctx = sub.add_parser("export-context", help="Export crash context bundle for AI code assistants.")
    p_ctx.add_argument("dump_path", help="Path to the .psp2dmp/psp2core-* file.")
    p_ctx.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_ctx.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown).")
    p_ctx.add_argument("--out", help="Explicit output path.")

    p_patch = sub.add_parser("so-patch", help="Scan for telemetry/IAP SDKs and generate neutralization stubs.")
    p_patch.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_patch.add_argument("--gen-stubs", action="store_true", help="Generate telemetry_stubs.c/.h for detected SDKs.")
    p_patch.add_argument("--out-dir", help="Output directory for generated stubs.")
    p_patch.add_argument("--apply-patch", metavar="VADDR",
                          help="Apply a real safe-return binary patch at this CONFIRMED virtual address (hex, e.g. 0x812a4f10). "
                               "Requires --so; does NOT guess the address -- confirm it with Ghidra/objdump/analyze first.")
    p_patch.add_argument("--revert-patches", action="store_true", help="Restore --so from its .orig backup, undoing every applied patch.")
    p_patch.add_argument("--so", help="Path to the .so to patch/revert (only with --apply-patch/--revert-patches).")
    p_patch.add_argument("--mode", default="thumb", choices=["thumb", "arm"], help="Instruction mode for --apply-patch (default: thumb).")

    p_sync = sub.add_parser("sync-shared", help="Sync shared component across ports of the same engine family.")
    p_sync.add_argument("--engine", required=True, help="Engine family name (e.g. 'Zenonia Series', 'Unity 4/5', 'Gamevil RPGs').")
    p_sync.add_argument("--module", required=True, help="Relative path of component to sync (e.g. 'source/falso_jni').")
    p_sync.add_argument("--source", help="Explicit source port directory (default: auto-detected).")
    p_sync.add_argument("--yes", action="store_true", help="Apply sync changes (without this flag, runs in dry-run mode).")

    p_eco = sub.add_parser("ecosystem-status", help="Show global status of all adopted ports under base_dir.")
    p_eco.add_argument("--plain", action="store_true", help="Disable ANSI colors.")

    p_align = sub.add_parser("align-check", help="Scan the .so for ARM memory-alignment risks and document findings.")
    p_align.add_argument("--project", help="Path to the port directory (default: current directory).")

    p_memprof = sub.add_parser("mem-profile", help="Listen for live heap metrics from the real PS Vita (UDP), or generate the C-side hooks.")
    p_memprof.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_memprof.add_argument("--port", type=int, help="UDP port to listen on (default: 9998).")
    p_memprof.add_argument("--gen-hooks", action="store_true", help="Generate mem_profiler_hooks.c/.h instead of listening.")
    p_memprof.add_argument("--host-ip", help="Dev machine IP to bake into the generated hooks (only with --gen-hooks).")
    p_memprof.add_argument("--out-dir", help="Output directory for generated hooks (only with --gen-hooks).")

    p_web = sub.add_parser("web", help="Start the local web dashboard (live logs, status, crashes, assets, touch mapper).")
    p_web.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_web.add_argument("--port", type=int, help="Local HTTP port (default: 8080).")

    p_gdb = sub.add_parser("gdb-map", help="Generate a .gdb symbol-map script for gdb-multiarch against a real gdbstub.")
    p_gdb.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_gdb.add_argument("--gdb-port", type=int, help="gdbstub port on the Vita (default: 10001).")
    p_gdb.add_argument("--so-base", help="Runtime base address of the .so, hex (e.g. 0x81000000), if already known.")
    p_gdb.add_argument("--watch-base", action="store_true",
                        help="Auto-capture the base address from a UDP 'SO_BASE=0x...' log line instead of --so-base.")
    p_gdb.add_argument("--watch-port", type=int, help="UDP port to listen on for --watch-base (default: 9999).")
    p_gdb.add_argument("--watch-timeout", type=int, help="Seconds to wait for --watch-base (default: 30).")

    p_transcode = sub.add_parser("transcode-assets", help="Batch-transcode textures (.rawtex + mipmaps) and/or audio (.at9).")
    p_transcode.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_transcode.add_argument("--textures-dir", help="Source folder of images to transcode.")
    p_transcode.add_argument("--audio-dir", help="Source folder of .wav/.mp3/.ogg/.at9 files to transcode.")
    p_transcode.add_argument("--out-dir", help="Output directory (default: <project>/extras/native_assets).")
    p_transcode.add_argument("--gen-loader", action="store_true",
                              help="Also generate rawtex_loader.c/.h (real C to load .rawtex via sceGxmTextureInitLinear).")

    p_perf = sub.add_parser("perf-telemetry", help="Listen for live frame-pacing/core telemetry from the real console, or generate the C-side hooks.")
    p_perf.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_perf.add_argument("--port", type=int, help="UDP port to listen on (default: 9996).")
    p_perf.add_argument("--gen-hooks", action="store_true", help="Generate perf_telemetry_hooks.c/.h instead of listening.")
    p_perf.add_argument("--host-ip", help="Dev machine IP to bake into the generated hooks (only with --gen-hooks).")
    p_perf.add_argument("--out-dir", help="Output directory for generated hooks (only with --gen-hooks).")

    p_soak = sub.add_parser("soak-test", help="Listen for a soak-test heartbeat from the real console, or generate the C-side hooks.")
    p_soak.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_soak.add_argument("--port", type=int, help="UDP port to listen on (default: 9995).")
    p_soak.add_argument("--hang-timeout", type=int, help="Seconds without a heartbeat to flag a hang (default: 30).")
    p_soak.add_argument("--gen-hooks", action="store_true", help="Generate monkey_test_hooks.c/.h instead of listening.")
    p_soak.add_argument("--host-ip", help="Dev machine IP to bake into the generated hooks (only with --gen-hooks).")
    p_soak.add_argument("--out-dir", help="Output directory for generated hooks (only with --gen-hooks).")
    p_soak.add_argument("--with-mem-profile", action="store_true",
                         help="Also run mem_profiler.py's heap listener in parallel (the plan's 'Leak Sentinel' pairing).")
    p_soak.add_argument("--mem-port", type=int, help="UDP port for --with-mem-profile (default: 9998).")

    p_auto = sub.add_parser("auto-bootstrap", help="Assisted build/deploy/crash-check loop against the real console.")
    p_auto.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_auto.add_argument("--max-iterations", type=int, help="Stop after this many build attempts (default: 5).")
    p_auto.add_argument("--run-seconds", type=int, help="Seconds to wait after each deploy before checking for a crash (default: 25).")
    p_auto.add_argument("--preset", default="debug",
                         choices=["debug", "release", "relwithdebinfo", "minsizerel"],
                         help="Build preset (default: debug).")

    p_transpile = sub.add_parser("shader-transpile", help="AST-based GLSL -> Cg transpile (glslangValidator + spirv-cross) for a whole dump directory.")
    p_transpile.add_argument("--dump-dir", required=True, help="Directory of dumped .glsl files to transpile.")
    p_transpile.add_argument("--out-dir", required=True, help="Directory to write the resulting .cg files into.")
    p_transpile.add_argument("--vitasdk", help="Override VITASDK path for psp2cgc validation (default: from global config).")

    p_live_reload = sub.add_parser("shader-live-reload", help="Watch assets/cg/*.cg and auto-upload each one to the real console on save.")
    p_live_reload.add_argument("--project", help="Path to the port directory (default: current directory).")
    p_live_reload.add_argument("--poll-interval", type=int, help="Seconds between mtime checks (default: 2).")

    return parser


def _cmd_doctor(args):
    """!
    @brief `doctor` subcommand handler: run every environment check and print the report.
    @param args Parsed CLI args (`plain`).
    @return `1` if any check failed, else `0`.
    """
    from . import doctor
    cfg = cfgmod.load_global_config()
    i18n.set_language(cfg.get("language", i18n.DEFAULT_LANGUAGE))
    return doctor.run_doctor(cfg, use_color=not args.plain)


def _cmd_build(args):
    """!
    @brief `build` subcommand handler: run the project's build non-interactively.
    @param args Parsed CLI args (`project`, `preset`, `build_dir`, `clean`).
    @return `1` on missing config/project or a failed build, else `0`.
    """
    from . import build_deploy
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))

    project_dir = Path(project_cfg["_project_dir"])
    build_dir = args.build_dir or project_cfg.get("build_dir", "build")
    ok = build_deploy._run_build(project_dir, args.preset, [], build_dir=build_dir,
                                  global_cfg=global_cfg, non_interactive=True, clean=args.clean)
    if not ok:
        return _fail("Build failed.")
    vpk_path = build_deploy._find_output_vpk(project_dir, build_dir, project_cfg["project_name"], args.preset)
    print(f"[+] Build OK.{' VPK: ' + str(vpk_path) if vpk_path else ''}")
    return 0


def _cmd_deploy(args):
    """!
    @brief `deploy` subcommand handler: upload the eboot or VPK to the PS Vita over FTP.
    @param args Parsed CLI args (`project`, `eboot`, `vpk`, `vpk_path`, `ip`, `yes`).
    @return `1` on missing config/project, else `0` (upload failures are
            reported by `ftp_ops` itself; this always returns success once dispatched).
    """
    from . import ftp_ops
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    if args.ip:
        project_cfg["vita_ip"] = args.ip

    if args.eboot:
        ftp_ops.upload_eboot(project_cfg, global_cfg, assume_yes=args.yes)
    else:
        ftp_ops.upload_vpk(project_cfg, global_cfg, vpk_path=args.vpk_path, non_interactive=True)
    return 0


def _cmd_analyze(args):
    """!
    @brief `analyze` subcommand handler: parse and report on a crash dump.
    @param args Parsed CLI args (`dump_path`, `project`, `elf`, `so`).
    @return `1` on missing config/project or if the dump couldn't be parsed, else `0`.
    """
    from . import crash_analyzer
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    ok = crash_analyzer.analyze(project_cfg, args.dump_path, global_cfg=global_cfg,
                                 elf_path=args.elf, so_path=args.so)
    return 0 if ok else 1


def _cmd_init(args):
    """!
    @brief `init` subcommand handler: create a new port non-interactively.
    @param args Parsed CLI args (`apk`, `name`, `titleid`, `slug`, `folder`, `vita_ip`).
    @return `1` on missing global config or invalid input, else `0`.
    """
    from . import init_port
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    try:
        project_cfg = init_port.run_wizard_headless(
            global_cfg, apk_path=args.apk, game_name=args.name, titleid=args.titleid,
            slug=args.slug, folder_name=args.folder, vita_ip=args.vita_ip,
        )
    except RuntimeError as e:
        return _fail(str(e))
    print(f"[+] Port created at {project_cfg['_project_dir']}")
    return 0


def _cmd_livearea(args):
    """!
    @brief `livearea` subcommand handler: batch-convert assets, generate
           `template.xml`/`bgm.at9`, and/or validate -- whichever flags were passed.
    @param args Parsed CLI args (`project`, `auto`, `mode`, `template`, `bgm`, `validate`).
    @return `1` if no action flag was given, on missing config/project, or if
            any requested action failed; else `0`.
    """
    from . import livearea
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    dest_dir = Path(project_cfg["_project_dir"]) / "extras" / "livearea"

    if not any([args.auto, args.template, args.bgm, args.validate]):
        return _fail("livearea (headless) requires at least one of --auto/--template/--bgm/--validate "
                     "-- the interactive per-asset picker needs the TUI.")

    ok = True
    if args.auto:
        src_dir = Path(args.auto).expanduser()
        if not src_dir.is_dir():
            return _fail(f"'{src_dir}' is not a directory.")
        any_found = False
        for asset_type, spec in livearea.VITA_SPECS.items():
            src_file = src_dir / spec["filename"]
            if not src_file.exists():
                continue
            any_found = True
            try:
                livearea.process_asset(src_file, asset_type, dest_dir, mode=args.mode, dither=True, backup=True)
            except Exception as e:  # noqa: BLE001 -- one bad asset shouldn't abort the whole batch
                print(f"[-] {spec['filename']}: {e}", file=sys.stderr)
                ok = False
        if not any_found:
            print(f"[-] No bg0.png/pic0.png/icon0.png/startup.png found in '{src_dir}'.", file=sys.stderr)
            ok = False

    if args.template:
        livearea.generate_template_xml(dest_dir)

    if args.bgm:
        if not livearea.convert_bgm_to_at9(args.bgm, dest_dir, global_cfg):
            ok = False

    if args.validate:
        if not livearea.print_validation(livearea.validate_livearea_dir(dest_dir)):
            ok = False

    return 0 if ok else 1


def _cmd_clean_junk(args):
    """!
    @brief `clean-junk` subcommand handler: remove macOS `._*` junk files from the project.
    @param args Parsed CLI args (`project`).
    @return `1` on missing project, else `0`.
    """
    from . import utils
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    utils.clean_macos_junk(project_cfg["_project_dir"])
    return 0


def _cmd_jni_analyze(args):
    """!
    @brief `jni-analyze` subcommand handler: middleware detection, JNI stub
           generation, and lifecycle-method documentation, in one pass.
    @param args Parsed CLI args (`project`, `out_dir`).
    @return `1` on missing project, else `0`.
    """
    from . import jni_analyzer
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    jni_analyzer.middleware_report(project_cfg)
    jni_analyzer.generate_jni_stubs(project_cfg, out_dir=args.out_dir)
    jni_analyzer.document_lifecycle_in_plan(project_cfg)
    return 0


def _cmd_logs_live(args):
    """!
    @brief `logs-live` subcommand handler: run the UDP live log server until interrupted.
    @param args Parsed CLI args (`project`, `port`, `filter`).
    @return `1` on missing project or an invalid `--filter` regex, else `0`
            (once the server starts, it only returns on Ctrl+C).
    """
    import re
    from . import debugnet_server
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    tag_filter = None
    if args.filter:
        try:
            tag_filter = re.compile(args.filter)
        except re.error as e:
            return _fail(f"Invalid --filter regex: {e}")
    debugnet_server.run_live_log_server(
        project_cfg, port=args.port or debugnet_server.DEFAULT_PORT, tag_filter=tag_filter)
    return 0


def _cmd_export_context(args):
    """!
    @brief `export-context` subcommand handler: export AI crash context bundle.
    @param args Parsed CLI args (`dump_path`, `project`, `format`, `out`).
    @return `1` on missing config/project or error, else `0`.
    """
    from . import context_feeder
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    return context_feeder.export_context_cli(
        project_cfg, args.dump_path, global_cfg=global_cfg, fmt=args.format, out=args.out)


def _cmd_so_patch(args):
    """!
    @brief `so-patch` subcommand handler: detect telemetry SDKs, optionally
           generate stubs, or apply/revert a real binary patch.
    @param args Parsed CLI args (`project`, `gen_stubs`, `out_dir`,
           `apply_patch`, `revert_patches`, `so`, `mode`).
    @return `1` on missing project/invalid input or a failed patch, else `0`.
    """
    from . import so_patcher

    if args.apply_patch or args.revert_patches:
        if not args.so:
            return _fail("--apply-patch/--revert-patches require --so.")
        if args.revert_patches:
            ok, msg = so_patcher.revert_binary_patches(args.so)
        else:
            try:
                vaddr = int(args.apply_patch, 16)
            except ValueError:
                return _fail(f"--apply-patch '{args.apply_patch}' is not a valid hex address.")
            ok, msg = so_patcher.apply_binary_patch(args.so, vaddr, mode=args.mode)
        print(("[+] " if ok else "[-] ") + msg)
        return 0 if ok else 1

    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    sdk_hits, _path_hits = so_patcher.run_patch_scan(project_cfg, global_cfg)
    if args.gen_stubs and sdk_hits:
        so_patcher.generate_telemetry_stubs(project_cfg, list(sdk_hits.keys()), out_dir=args.out_dir)
    return 0


def _cmd_sync_shared(args):
    """!
    @brief `sync-shared` subcommand handler: sync shared components between ports in an engine family.
    @param args Parsed CLI args (`engine`, `module`, `source`, `yes`).
    @return `1` on error or missing config, else `0`.
    """
    from . import ecosystem
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    return ecosystem.sync_shared_cli(
        global_cfg, args.engine, args.module, source=args.source, assume_yes=args.yes)


def _cmd_ecosystem_status(args):
    """!
    @brief `ecosystem-status` subcommand handler: render global overview of all ports.
    @param args Parsed CLI args (`plain`).
    @return `0`.
    """
    from . import ecosystem
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    return ecosystem.global_status_cli(global_cfg, plain=args.plain)


def _cmd_align_check(args):
    """!
    @brief `align-check` subcommand handler: scan the `.so` for ARM
           memory-alignment risks and document findings in `PORTING_PLAN.md`.
    @param args Parsed CLI args (`project`).
    @return `1` on missing project, else `0`.
    """
    from . import mem_align_analyzer
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    mem_align_analyzer.run_alignment_scan(project_cfg, global_cfg)
    return 0


def _cmd_mem_profile(args):
    """!
    @brief `mem-profile` subcommand handler: run the UDP heap-metric listener
           until interrupted, or (with `--gen-hooks`) generate the C-side hooks.
    @param args Parsed CLI args (`project`, `port`, `gen_hooks`, `host_ip`, `out_dir`).
    @return `1` on missing project, else `0`.
    """
    from . import mem_profiler
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    if args.gen_hooks:
        mem_profiler.generate_profiler_hooks(
            project_cfg, host_ip=args.host_ip, port=args.port or mem_profiler.DEFAULT_PORT, out_dir=args.out_dir)
    else:
        mem_profiler.run_memory_profiler(project_cfg, port=args.port or mem_profiler.DEFAULT_PORT)
    return 0


def _cmd_web(args):
    """!
    @brief `web` subcommand handler: run the local dashboard server until interrupted.
    @param args Parsed CLI args (`project`, `port`).
    @return `1` on missing config/project, else `0`.
    """
    from . import dashboard
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    dashboard.run_dashboard_server(project_cfg, global_cfg, port=args.port or dashboard.DEFAULT_HTTP_PORT)
    return 0


def _cmd_gdb_map(args):
    """!
    @brief `gdb-map` subcommand handler: generate the GDB symbol-map script.
    @param args Parsed CLI args (`project`, `gdb_port`, `so_base`,
           `watch_base`, `watch_port`, `watch_timeout`).
    @return `1` on missing project or a bad `--so-base`, else `0`.
    """
    from . import gdb_bridge
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)

    so_base = None
    if args.watch_base:
        so_base = gdb_bridge.watch_for_so_base(
            port=args.watch_port or gdb_bridge.DEFAULT_WATCH_PORT,
            timeout=args.watch_timeout or gdb_bridge.DEFAULT_WATCH_TIMEOUT,
        )
        if so_base is None:
            print("[-] No SO_BASE=0x... line arrived in time -- falling back to no base address.", file=sys.stderr)
    elif args.so_base:
        try:
            so_base = int(args.so_base, 16)
        except ValueError:
            return _fail(f"--so-base '{args.so_base}' is not a valid hex address.")

    gdb_bridge.generate_symbol_map(project_cfg, gdb_port=args.gdb_port or gdb_bridge.DEFAULT_GDB_PORT, so_base=so_base)
    return 0


def _cmd_transcode_assets(args):
    """!
    @brief `transcode-assets` subcommand handler: batch-transcode textures and/or audio.
    @param args Parsed CLI args (`project`, `textures_dir`, `audio_dir`, `out_dir`, `gen_loader`).
    @return `1` on missing project or if none of `--textures-dir`/`--audio-dir`/`--gen-loader` was given, else `0`.
    """
    from . import asset_transcoder
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    if not args.textures_dir and not args.audio_dir and not args.gen_loader:
        return _fail("transcode-assets requires at least one of --textures-dir/--audio-dir/--gen-loader.")
    out_dir = args.out_dir or (Path(project_cfg["_project_dir"]) / "extras" / "native_assets")
    if args.textures_dir:
        asset_transcoder.transcode_texture_dir(args.textures_dir, out_dir)
    if args.audio_dir:
        asset_transcoder.transcode_audio_dir(args.audio_dir, out_dir, global_cfg)
    if args.gen_loader:
        asset_transcoder.generate_rawtex_loader(project_cfg)
    return 0


def _cmd_perf_telemetry(args):
    """!
    @brief `perf-telemetry` subcommand handler: run the UDP telemetry listener
           until interrupted, or (with `--gen-hooks`) generate the C-side hooks.
    @param args Parsed CLI args (`project`, `port`, `gen_hooks`, `host_ip`, `out_dir`).
    @return `1` on missing project, else `0`.
    """
    from . import perf_telemetry
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    if args.gen_hooks:
        perf_telemetry.generate_perf_hooks(
            project_cfg, host_ip=args.host_ip, port=args.port or perf_telemetry.DEFAULT_PORT, out_dir=args.out_dir)
    else:
        perf_telemetry.run_perf_telemetry(project_cfg, port=args.port or perf_telemetry.DEFAULT_PORT)
    return 0


def _cmd_soak_test(args):
    """!
    @brief `soak-test` subcommand handler: run the heartbeat listener until
           interrupted, or (with `--gen-hooks`) generate the C-side hooks.
    @param args Parsed CLI args (`project`, `port`, `hang_timeout`, `gen_hooks`,
           `host_ip`, `out_dir`, `with_mem_profile`, `mem_port`).
    @return `1` on missing project, else `0`.
    """
    from . import monkey_tester
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    if args.gen_hooks:
        monkey_tester.generate_monkey_hooks(
            project_cfg, host_ip=args.host_ip, port=args.port or monkey_tester.DEFAULT_PORT, out_dir=args.out_dir)
    elif args.with_mem_profile:
        monkey_tester.run_combined_soak_session(
            project_cfg, heartbeat_port=args.port or monkey_tester.DEFAULT_PORT,
            mem_port=args.mem_port, hang_timeout=args.hang_timeout or monkey_tester.DEFAULT_HANG_TIMEOUT)
    else:
        monkey_tester.run_soak_test(
            project_cfg, port=args.port or monkey_tester.DEFAULT_PORT,
            hang_timeout=args.hang_timeout or monkey_tester.DEFAULT_HANG_TIMEOUT)
    return 0


def _cmd_auto_bootstrap(args):
    """!
    @brief `auto-bootstrap` subcommand handler: run the assisted build/deploy/crash-check loop.
    @param args Parsed CLI args (`project`, `max_iterations`, `run_seconds`, `preset`).
    @return `1` on missing config/project, `0` if the loop reported stability,
            else `1` (stopped early: build failure, no progress, or max iterations).
    """
    from . import auto_synth
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    ok = auto_synth.run_auto_bootstrap(
        project_cfg, global_cfg,
        max_iterations=args.max_iterations or auto_synth.DEFAULT_MAX_ITERATIONS,
        run_seconds=args.run_seconds or auto_synth.DEFAULT_RUN_SECONDS,
        preset=args.preset,
    )
    return 0 if ok else 1


def _cmd_shader_transpile(args):
    """!
    @brief `shader-transpile` subcommand handler: batch AST-based GLSL -> Cg transpile.
    @param args Parsed CLI args (`dump_dir`, `out_dir`, `vitasdk`).
    @return `1` if any shader failed to transpile, else `0`.
    """
    from . import shader_transpiler
    global_cfg, err = _load_global_config()
    if err:
        global_cfg = {}
    if args.vitasdk:
        global_cfg["vitasdk"] = args.vitasdk
    _ok, failed = shader_transpiler.transpile_shaders_dir(args.dump_dir, args.out_dir, global_cfg)
    return 1 if failed else 0


def _cmd_shader_live_reload(args):
    """!
    @brief `shader-live-reload` subcommand handler: watch and auto-upload `.cg` shaders until interrupted.
    @param args Parsed CLI args (`project`, `poll_interval`).
    @return `1` on missing config/project, else `0`.
    """
    from . import shader_live_reload
    global_cfg, err = _load_global_config()
    if err:
        return _fail(err)
    project_cfg, err = _load_project(args.project)
    if err:
        return _fail(err)
    i18n.set_language(global_cfg.get("language", i18n.DEFAULT_LANGUAGE))
    shader_live_reload.watch_and_upload_shaders(
        project_cfg, global_cfg, poll_interval=args.poll_interval or shader_live_reload.DEFAULT_POLL_INTERVAL)
    return 0


_HANDLERS = {
    "doctor": _cmd_doctor,
    "build": _cmd_build,
    "deploy": _cmd_deploy,
    "analyze": _cmd_analyze,
    "init": _cmd_init,
    "livearea": _cmd_livearea,
    "clean-junk": _cmd_clean_junk,
    "jni-analyze": _cmd_jni_analyze,
    "logs-live": _cmd_logs_live,
    "export-context": _cmd_export_context,
    "so-patch": _cmd_so_patch,
    "sync-shared": _cmd_sync_shared,
    "ecosystem-status": _cmd_ecosystem_status,
    "align-check": _cmd_align_check,
    "mem-profile": _cmd_mem_profile,
    "web": _cmd_web,
    "gdb-map": _cmd_gdb_map,
    "transcode-assets": _cmd_transcode_assets,
    "perf-telemetry": _cmd_perf_telemetry,
    "soak-test": _cmd_soak_test,
    "auto-bootstrap": _cmd_auto_bootstrap,
    "shader-transpile": _cmd_shader_transpile,
    "shader-live-reload": _cmd_shader_live_reload,
}


def dispatch(argv):
    """!
    @brief Parse `argv` and run the matching subcommand handler.
    @param argv Argument list, e.g. `sys.argv[1:]`.
    @return Process exit code (`0` success, non-zero failure), or `None` if
            `argv` names no subcommand -- signals the caller to fall through
            to the interactive TUI instead.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        return None
    return _HANDLERS[args.command](args)
