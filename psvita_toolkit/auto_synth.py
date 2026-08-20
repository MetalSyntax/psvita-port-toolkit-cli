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

Two efficiency improvements on top of that core loop, both real and bounded
(neither adds any new autonomy claim):
1. `_wait_and_check_for_crash()` polls the console a few times across the
   wait window instead of sleeping the FULL `run_seconds` before checking
   once -- an iteration whose crash happens quickly moves on immediately
   instead of always paying the full wait. Deliberately capped at a small,
   fixed number of checks (not "check every second"): `ftp_ops.py` already
   documents VitaShell's ftpd occasionally refusing a connection attempt
   made right after a previous one just closed, so hammering it with rapid
   reconnects would trade a speed gain for flakiness.
2. `_check_stubs_wired_into_build()` reads the project's `CMakeLists.txt`
   once at the start and reports whether it already globs the directory
   `jni_analyzer.py`/`so_patcher.py` write candidate stubs into -- if so, a
   regenerated stub is genuinely picked up by the NEXT build automatically;
   if not, the porter still needs to add it to `CMakeLists.txt` by hand.
   Reported honestly either way, not assumed.
"""

import re
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
        "es": "[*] Esperando hasta {seconds}s a que el juego arranque/corra (chequeos parciales, no espera ciega)...",
        "en": "[*] Waiting up to {seconds}s for the game to boot/run (polled in parts, not a blind wait)...",
        "pt": "[*] Esperando até {seconds}s para o jogo iniciar/rodar (verificações parciais, não é espera cega)...",
    },
    "auto_synth.stubs_wired_yes": {
        "es": "[i] CMakeLists.txt ya incluye 'source/*.c' -- un stub regenerado se recompila solo en la próxima build.",
        "en": "[i] CMakeLists.txt already globs 'source/*.c' -- a regenerated stub gets rebuilt automatically next iteration.",
        "pt": "[i] CMakeLists.txt já inclui 'source/*.c' -- um stub regenerado é recompilado automaticamente na próxima build.",
    },
    "auto_synth.stubs_wired_no": {
        "es": "[!] CMakeLists.txt no parece incluir 'source/*.c' -- los stubs regenerados no se recompilan solos, hay que agregarlos a mano.",
        "en": "[!] CMakeLists.txt doesn't appear to glob 'source/*.c' -- regenerated stubs won't be rebuilt automatically, they need adding by hand.",
        "pt": "[!] CMakeLists.txt não parece incluir 'source/*.c' -- os stubs regenerados não são recompilados automaticamente, é preciso adicioná-los manualmente.",
    },
    "auto_synth.stubs_wired_unknown": {
        "es": "[?] No se encontró CMakeLists.txt -- no se pudo chequear si los stubs regenerados se recompilan solos.",
        "en": "[?] No CMakeLists.txt found -- couldn't check whether regenerated stubs get rebuilt automatically.",
        "pt": "[?] Nenhum CMakeLists.txt encontrado -- não foi possível verificar se os stubs regenerados são recompilados automaticamente.",
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


def _wait_and_check_for_crash(project_cfg, global_cfg, run_seconds, last_seen_dump, checks=3):
    """!
    @brief Poll the console up to `checks` times across `run_seconds`
           instead of sleeping the whole window before one single check --
           returns as soon as a NEW dump shows up.
    @param project_cfg Active project config dict.
    @param global_cfg Global config dict.
    @param run_seconds Total seconds to spend waiting, across all checks.
    @param last_seen_dump The dump filename to compare against (whatever was
           there before this wait started).
    @param checks How many times to poll at most. Deliberately small and
           fixed -- see the module docstring for why this doesn't poll
           every second.
    @return The latest remote dump filename as of the last check performed
           (may still equal `last_seen_dump` if nothing new ever showed up).
    """
    interval = max(run_seconds / checks, 5)
    elapsed = 0.0
    latest_name = last_seen_dump
    while elapsed < run_seconds:
        sleep_for = min(interval, run_seconds - elapsed)
        time.sleep(sleep_for)
        elapsed += sleep_for
        latest_name = _latest_remote_dump_name(project_cfg, global_cfg)
        if latest_name is not None and latest_name != last_seen_dump:
            break
    return latest_name


_GLOB_KEYWORD_RE = re.compile(r'\bGLOB(_RECURSE)?\b', re.IGNORECASE)
_SOURCE_GLOB_PATTERN_RE = re.compile(r'source[/\\]\*\.c\b')


def _check_stubs_wired_into_build(project_dir):
    """!
    @brief Best-effort check of whether `CMakeLists.txt` already globs the
           directory `jni_analyzer.py`/`so_patcher.py` write candidate stubs
           into (`<project_dir>/source/*.c`) -- if so, a regenerated stub is
           genuinely picked up by the next build with no further action; if
           not, the porter still has to add it to `CMakeLists.txt` by hand.
    @details Deliberately loose (checks for CMake's `GLOB`/`GLOB_RECURSE`
           keyword and a literal `source/*.c` pattern appearing ANYWHERE in
           the file, not that they're part of the same `file(...)` call) --
           a real CMake parser would be needed to check that precisely, and
           a false "yes" here just means the porter double-checks something
           that was already fine, while a false "no" means they add a line
           that was already unnecessary. Neither is a dangerous outcome, so
           the loose heuristic is worth the simplicity.
    @param project_dir Path to the port's project directory.
    @return `True`/`False` if `CMakeLists.txt` exists and was checked, or
           `None` if there's no `CMakeLists.txt` to check at all (can't tell).
    """
    cmake_path = Path(project_dir) / "CMakeLists.txt"
    if not cmake_path.exists():
        return None
    text = cmake_path.read_text(encoding="utf-8", errors="ignore")
    return bool(_GLOB_KEYWORD_RE.search(text) and _SOURCE_GLOB_PATTERN_RE.search(text))


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

    stubs_wired = _check_stubs_wired_into_build(project_dir)
    if stubs_wired is True:
        msg = t("auto_synth.stubs_wired_yes")
    elif stubs_wired is False:
        msg = t("auto_synth.stubs_wired_no")
    else:
        msg = t("auto_synth.stubs_wired_unknown")
    print(f"{C.DIM}{msg}{C.RESET}")
    report.append(msg)

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
        latest_name = _wait_and_check_for_crash(project_cfg, global_cfg, run_seconds, last_seen_dump)

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
