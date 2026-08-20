"""!
@file auto_synth.py
@brief Assisted bootstrap loop: build, deploy, wait, and check for a crash on
       the REAL PS Vita, in a loop -- regenerating candidate JNI/telemetry
       stub fixes between attempts instead of leaving every iteration to be
       triggered by hand.

@details
The plan item this responds to described a fully autonomous loop against
Vita3K that "injects the missing hook or stub and retries until the game
renders its first frame". Two things about that don't hold up honestly here:
Vita3K isn't usable for this project's ports at all (see `build_deploy.py`'s
removal of it), and `jni_analyzer.py`/`so_patcher.py` generate REVIEWABLE
CANDIDATE stubs, not verified fixes -- they can't know a generated stub
actually resolves the specific crash that triggered it, only that it's a
plausible starting point. Silently trusting that and looping forever would
be exactly the kind of overclaiming this project's other modules avoid.

So this is an ASSISTED loop, not a fully autonomous one: each iteration
builds, deploys (full VPK once, then just `eboot.bin` for speed), waits, and
checks the real console for a NEW crash dump -- "new" meaning the remote
dump's FILENAME changed since the last check (`_latest_remote_dump_name()`),
which tells apart "nothing new happened" from "a fresh crash occurred"
without needing to know anything about what's inside it. Whether that fresh
crash is actually the SAME underlying bug as a previous iteration's is a
separate question, answered by comparing each crash's resolved symbol
(`_crash_signature()`, read back from `crash_analyzer.analyze()`'s own
`<dump>.triage_summary.md`) -- filename churn alone can't tell that apart,
since every dump gets a fresh timestamped name whether or not the underlying
crash repeats. A same-signature repeat regenerates nothing further (the
candidate stubs already tried didn't help); a genuinely new signature
regenerates whatever candidate stubs `jni_analyzer.py`/`so_patcher.py` can,
then retries. It stops -- reporting into `PORTING_PLAN.md`, and exporting a
full AI-copilot context bundle via `context_feeder.py` -- as soon as: the
build fails, no new dump appears (looks stable), the SAME crash signature
repeats (no measurable progress), or `max_iterations` is reached. See
`docs/dev-notes/auto_synth.md`.
"""

import time
from pathlib import Path

from . import build_deploy
from . import context_feeder
from . import crash_analyzer
from . import ftp_ops
from . import i18n
from . import jni_analyzer
from . import so_patcher
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "auto_synth.menu_title": {
        "es": "Auto-Synthesizer (bootstrap asistido en consola real)",
        "en": "Auto-Synthesizer (assisted bootstrap on the real console)",
        "pt": "Auto-Synthesizer (bootstrap assistido no console real)",
    },
    "auto_synth.menu_run": {
        "es": "Correr el bootstrap asistido",
        "en": "Run the assisted bootstrap",
        "pt": "Rodar o bootstrap assistido",
    },
    "auto_synth.max_iter_prompt": {
        "es": "Máximo de iteraciones [{default}]: ",
        "en": "Max iterations [{default}]: ",
        "pt": "Máximo de iterações [{default}]: ",
    },
    "auto_synth.run_seconds_prompt": {
        "es": "Segundos a esperar tras desplegar antes de chequear un crash [{default}]: ",
        "en": "Seconds to wait after deploying before checking for a crash [{default}]: ",
        "pt": "Segundos a esperar após implantar antes de verificar um crash [{default}]: ",
    },
    "auto_synth.iteration_header": {
        "es": "\n=== Iteración {n}/{max_n} ===",
        "en": "\n=== Iteration {n}/{max_n} ===",
        "pt": "\n=== Iteração {n}/{max_n} ===",
    },
    "auto_synth.build_failed": {
        "es": "[-] Iteración {n}: el build falló -- necesita intervención manual.",
        "en": "[-] Iteration {n}: the build failed -- needs manual intervention.",
        "pt": "[-] Iteração {n}: o build falhou -- precisa de intervenção manual.",
    },
    "auto_synth.no_vpk": {
        "es": "[-] Iteración {n}: el build no produjo ningún .vpk.",
        "en": "[-] Iteration {n}: the build didn't produce any .vpk.",
        "pt": "[-] Iteração {n}: o build não produziu nenhum .vpk.",
    },
    "auto_synth.waiting": {
        "es": "[*] Esperando {seconds}s a que el juego arranque/corra en la consola real...",
        "en": "[*] Waiting {seconds}s for the game to boot/run on the real console...",
        "pt": "[*] Esperando {seconds}s para o jogo iniciar/rodar no console real...",
    },
    "auto_synth.looks_stable": {
        "es": "[+] Iteración {n}: no apareció ningún crash dump nuevo tras {seconds}s -- parece estable.",
        "en": "[+] Iteration {n}: no new crash dump after {seconds}s -- looks stable.",
        "pt": "[+] Iteração {n}: nenhum crash dump novo após {seconds}s -- parece estável.",
    },
    "auto_synth.same_crash": {
        "es": "[!] Iteración {n}: mismo crash que la iteración anterior ({name}) -- sin progreso medible, se detiene para revisión manual.",
        "en": "[!] Iteration {n}: same crash as the previous iteration ({name}) -- no measurable progress, stopping for manual review.",
        "pt": "[!] Iteração {n}: mesmo crash da iteração anterior ({name}) -- sem progresso mensurável, parando para revisão manual.",
    },
    "auto_synth.new_crash": {
        "es": "[!] Iteración {n}: crash nuevo ({name}) -- analizando y regenerando candidatos de fix...",
        "en": "[!] Iteration {n}: new crash ({name}) -- analyzing and regenerating candidate fixes...",
        "pt": "[!] Iteração {n}: crash novo ({name}) -- analisando e regenerando candidatos de correção...",
    },
    "auto_synth.max_reached": {
        "es": "[!] Se llegó al máximo de {max_n} iteraciones sin confirmar estabilidad.",
        "en": "[!] Reached the max of {max_n} iterations without confirming stability.",
        "pt": "[!] Alcançou o máximo de {max_n} iterações sem confirmar estabilidade.",
    },
    "auto_synth.context_exported": {
        "es": "[+] Contexto para copiloto IA exportado -- ver el resultado de export-context.",
        "en": "[+] AI-copilot context exported -- see export-context's output.",
        "pt": "[+] Contexto para copiloto IA exportado -- veja a saída do export-context.",
    },
    "auto_synth.report_written": {
        "es": "[+] Reporte del Auto-Synthesizer agregado a {plan_path}",
        "en": "[+] Auto-Synthesizer report appended to {plan_path}",
        "pt": "[+] Relatório do Auto-Synthesizer adicionado a {plan_path}",
    },
}
i18n.register(STRINGS)

DEFAULT_MAX_ITERATIONS = 5
DEFAULT_RUN_SECONDS = 25


def _latest_remote_dump_name(project_cfg, global_cfg):
    """!
    @brief Peek at the console's newest crash dump filename WITHOUT
           downloading it (cheap, repeatable "did anything new happen" check).
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict.
    @return The newest dump's filename, or `None` if unreachable or there's
            no dump at all.
    """
    ftp = ftp_ops._connect_with_retry(project_cfg, global_cfg)
    if not ftp:
        return None
    try:
        dumps = ftp_ops.list_remote_dumps(ftp, project_cfg)
        return dumps[0][0] if dumps else None
    except ftp_ops.all_errors:
        return None
    finally:
        ftp_ops._quit(ftp)


def _crash_signature(dump_path):
    """!
    @brief Read back the resolved crashing symbol from
           `crash_analyzer.analyze()`'s own `<dump>.triage_summary.md`, to
           tell apart "a new crash" (different filename) from "the SAME
           underlying bug crashing again" (same symbol).
    @param dump_path Local path to the crash dump `crash_analyzer.analyze()`
           was just run against.
    @return The first cross-referenced symbol's name, the raw crash
           instruction line if no symbol resolved, or `None` if the summary
           doesn't exist / has neither (analysis failed to produce anything
           comparable -- treated as "unknown", never silently equal to a
           previous "unknown").
    """
    summary_path = Path(str(dump_path) + ".triage_summary.md")
    if not summary_path.exists():
        return None
    text = summary_path.read_text(encoding="utf-8", errors="ignore")
    crash_instruction, symbols = context_feeder._parse_triage_summary(text)
    if symbols:
        return symbols[0]["symbol"]
    return crash_instruction


def _write_report(project_cfg, lines):
    """!
    @brief Append a `## Auto-Synthesizer report` section to `PORTING_PLAN.md`,
           same append-if-exists-else-warn pattern as `so_patcher.py`.
    @param project_cfg Per-project config dict.
    @param lines Report lines (one per iteration/outcome) to record.
    """
    plan_path = Path(project_cfg["_project_dir"]) / "PORTING_PLAN.md"
    if not plan_path.exists():
        return
    body = ["", "## Auto-Synthesizer report (psvita-toolkit)", "",
            "Assisted build/deploy/crash-check loop against the real console -- see",
            "`docs/dev-notes/auto_synth.md` for why this regenerates candidate stubs between",
            "attempts instead of claiming a fully autonomous fix loop.", ""]
    body.extend(f"- {line}" for line in lines)
    body.append("")
    with open(plan_path, "a", encoding="utf-8") as f:
        f.write("\n".join(body))
    print(f"{C.GREEN}{t('auto_synth.report_written', plan_path=plan_path)}{C.RESET}")


def run_auto_bootstrap(project_cfg, global_cfg, max_iterations=DEFAULT_MAX_ITERATIONS,
                        run_seconds=DEFAULT_RUN_SECONDS, preset="debug"):
    """!
    @brief Run the assisted build/deploy/crash-check loop.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    @param max_iterations Stop after this many build attempts either way.
    @param run_seconds Seconds to wait after each deploy before checking the
           console for a new crash dump.
    @param preset Build preset to use every iteration.
    @return `True` if a full pass produced no new crash (looks stable),
            `False` if it stopped for any other reason (build failure, no
            progress, or max iterations reached).
    """
    project_dir = Path(project_cfg["_project_dir"])
    build_dir = project_cfg.get("build_dir", "build")
    report = []
    last_seen_dump = _latest_remote_dump_name(project_cfg, global_cfg)
    last_signature = None

    for i in range(1, max_iterations + 1):
        print(t("auto_synth.iteration_header", n=i, max_n=max_iterations))

        ok = build_deploy._run_build(project_dir, preset, [], build_dir=build_dir,
                                      global_cfg=global_cfg, non_interactive=True, clean=False)
        if not ok:
            msg = t("auto_synth.build_failed", n=i)
            print(f"{C.RED}{msg}{C.RESET}")
            report.append(msg)
            _write_report(project_cfg, report)
            return False

        if i == 1:
            vpk_path = build_deploy._find_output_vpk(project_dir, build_dir, project_cfg["project_name"], preset)
            if not vpk_path:
                msg = t("auto_synth.no_vpk", n=i)
                print(f"{C.RED}{msg}{C.RESET}")
                report.append(msg)
                _write_report(project_cfg, report)
                return False
            ftp_ops.upload_vpk(project_cfg, global_cfg, vpk_path=str(vpk_path), non_interactive=True)
        else:
            ftp_ops.upload_eboot(project_cfg, global_cfg, assume_yes=True)

        print(t("auto_synth.waiting", seconds=run_seconds))
        time.sleep(run_seconds)

        latest_name = _latest_remote_dump_name(project_cfg, global_cfg)
        if latest_name is None or latest_name == last_seen_dump:
            # Either no dump at all, or the same one that was already there
            # before this check -- either way, nothing NEW crashed.
            msg = t("auto_synth.looks_stable", n=i, seconds=run_seconds)
            print(f"{C.GREEN}{msg}{C.RESET}")
            report.append(msg)
            _write_report(project_cfg, report)
            return True

        last_seen_dump = latest_name
        dump_path = ftp_ops.fetch_latest_dump_headless(project_cfg, global_cfg)
        if not dump_path:
            msg = t("auto_synth.new_crash", n=i, name=latest_name)
            print(f"{C.YELLOW}{msg}{C.RESET}")
            report.append(msg)
            _write_report(project_cfg, report)
            return False

        crash_analyzer.analyze(project_cfg, str(dump_path), global_cfg=global_cfg)
        signature = _crash_signature(dump_path)

        if signature is not None and signature == last_signature:
            msg = t("auto_synth.same_crash", n=i, name=signature)
            print(f"{C.YELLOW}{msg}{C.RESET}")
            report.append(msg)
            context_feeder.export_context_cli(project_cfg, str(dump_path), global_cfg=global_cfg,
                                                fmt="markdown", out=None)
            print(t("auto_synth.context_exported"))
            _write_report(project_cfg, report)
            return False

        last_signature = signature
        msg = t("auto_synth.new_crash", n=i, name=(signature or latest_name))
        print(f"{C.YELLOW}{msg}{C.RESET}")
        report.append(msg)

        jni_analyzer.generate_jni_stubs(project_cfg)
        sdk_hits, _path_hits = so_patcher.run_patch_scan(project_cfg, global_cfg)
        if sdk_hits:
            so_patcher.generate_telemetry_stubs(project_cfg, list(sdk_hits.keys()))

    msg = t("auto_synth.max_reached", max_n=max_iterations)
    print(f"{C.YELLOW}{msg}{C.RESET}")
    report.append(msg)
    _write_report(project_cfg, report)
    return False


def auto_synth_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: ask for the loop's parameters, then run it.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    def _run():
        max_raw = input(f"{C.BOLD}{t('auto_synth.max_iter_prompt', default=DEFAULT_MAX_ITERATIONS)}{C.RESET}").strip()
        max_iterations = int(max_raw) if max_raw.isdigit() else DEFAULT_MAX_ITERATIONS
        seconds_raw = input(f"{C.BOLD}{t('auto_synth.run_seconds_prompt', default=DEFAULT_RUN_SECONDS)}{C.RESET}").strip()
        run_seconds = int(seconds_raw) if seconds_raw.isdigit() else DEFAULT_RUN_SECONDS
        run_auto_bootstrap(project_cfg, global_cfg, max_iterations=max_iterations, run_seconds=run_seconds)

    tui.run_menu(t("auto_synth.menu_title"), [(t("auto_synth.menu_run"), _run)])
