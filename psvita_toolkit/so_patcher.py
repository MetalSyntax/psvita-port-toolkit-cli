"""!
@file so_patcher.py
@brief Static detection + reviewable-C-stub generation for Android-only
       telemetry/IAP SDKs and hardcoded Android paths that commonly hang or
       crash a soloader-based port on first boot.

@details
Android ports of this game genre commonly link Google Play Services/Play
Games, Firebase Analytics, Google Play Billing (IAP), AdMob, Tapjoy, the
Facebook SDK, and/or Flurry -- none of which exist on PS Vita. Blocking
network calls into any of them (or hardcoded `/sdcard/`, `/data/data/`
paths) are a common source of hangs/crashes before the game ever reaches
its own menu.

This module does NOT attempt live in-memory ASM patching of the loaded
`.so` at runtime -- that would need an ELF loader/relocator this
soloader-based toolkit doesn't own, and is a fundamentally different (and
far riskier) engineering effort. Instead it follows the exact same pattern
`jni_analyzer.py` already established for FalsoJNI stubs: detect the
problem statically, then generate REVIEWABLE C source the porter links in
instead of the real SDK object.

Three independent things:
1. `detect_telemetry_sdks()` / `scan_telemetry_sdks_in_java()` -- fingerprint
   known SDKs, first in the game's own `.so` (raw byte scan, same technique
   as `jni_analyzer.detect_middleware()`), then -- usually more fruitful --
   across the jadx-decompiled Java, reporting file:line evidence.
2. `find_hardcoded_paths()` -- regex-scans the same decompiled Java plus the
   `.so`'s raw printable strings for `/sdcard/...`, `/data/data/...` and
   `http(s)://...` literals, and suggests this project's own configured
   Vita-side path as a replacement.
3. `generate_telemetry_stubs()` -- for detected SDKs, writes a checklist-style
   `telemetry_stubs.c`/`.h` pair: what usually needs neutralizing for that
   SDK, NOT fabricated function bodies calling entry points this static scan
   can't actually confirm exist on the real call path.

See `docs/dev-notes/so_patcher.md` for why this stays a static, source-level
tool (no in-memory patching), why detection runs two passes instead of one,
and why the generated stubs are checklists, not fake drop-in replacements.
"""

import os
import re
from pathlib import Path

from . import i18n
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "so_patcher.scan_title": {
        "es": "[*] Escaneando SDKs de telemetría/IAP y rutas hardcodeadas...",
        "en": "[*] Scanning for telemetry/IAP SDKs and hardcoded paths...",
        "pt": "[*] Escaneando SDKs de telemetria/IAP e caminhos hardcoded...",
    },
    "so_patcher.so_not_found": {
        "es": "[!] No se encontró ningún .so -- se escanea igual el Java decompilado.",
        "en": "[!] No .so file found -- scanning the decompiled Java anyway.",
        "pt": "[!] Nenhum .so encontrado -- escaneando o Java decompilado mesmo assim.",
    },
    "so_patcher.sdk_found": {
        "es": "[+] SDK(s) detectado(s): {names}",
        "en": "[+] SDK(s) detected: {names}",
        "pt": "[+] SDK(s) detectado(s): {names}",
    },
    "so_patcher.sdk_none": {
        "es": "[*] No se detectó ningún SDK de telemetría/IAP conocido.",
        "en": "[*] No known telemetry/IAP SDK detected.",
        "pt": "[*] Nenhum SDK de telemetria/IAP conhecido detectado.",
    },
    "so_patcher.path_found": {
        "es": "[!] {count} ruta(s)/URL hardcodeada(s) encontrada(s).",
        "en": "[!] {count} hardcoded path(s)/URL(s) found.",
        "pt": "[!] {count} caminho(s)/URL(s) hardcoded encontrado(s).",
    },
    "so_patcher.path_none": {
        "es": "[*] No se encontraron rutas hardcodeadas ni URLs sospechosas.",
        "en": "[*] No hardcoded paths or suspicious URLs found.",
        "pt": "[*] Nenhum caminho hardcoded ou URL suspeita encontrado.",
    },
    "so_patcher.plan_not_found": {
        "es": "[!] No se encontró PORTING_PLAN.md -- se omite la sección de auto-parcheo.",
        "en": "[!] PORTING_PLAN.md not found -- skipping the auto-patch section.",
        "pt": "[!] PORTING_PLAN.md não encontrado -- pulando a seção de auto-patch.",
    },
    "so_patcher.plan_updated": {
        "es": "[+] Hallazgos documentados en {plan_path}",
        "en": "[+] Findings documented in {plan_path}",
        "pt": "[+] Achados documentados em {plan_path}",
    },
    "so_patcher.plan_nothing_to_document": {
        "es": "[*] Nada que documentar -- no se detectó ningún SDK ni ruta hardcodeada.",
        "en": "[*] Nothing to document -- no SDK or hardcoded path detected.",
        "pt": "[*] Nada para documentar -- nenhum SDK ou caminho hardcoded detectado.",
    },
    "so_patcher.stubs_generated": {
        "es": "[+] Stubs de neutralización generados para {count} SDK(s) -- ver {header}/{stub}",
        "en": "[+] Neutralization stubs generated for {count} SDK(s) -- see {header}/{stub}",
        "pt": "[+] Stubs de neutralização gerados para {count} SDK(s) -- ver {header}/{stub}",
    },
    "so_patcher.stubs_none": {
        "es": "[*] Ninguno de los SDKs indicados tiene checklist conocido -- no se generó nada.",
        "en": "[*] None of the given SDKs have a known checklist -- nothing generated.",
        "pt": "[*] Nenhum dos SDKs indicados tem checklist conhecido -- nada foi gerado.",
    },
    "so_patcher.no_sdks_detected_for_stubs": {
        "es": "[*] No se detectó ningún SDK -- corré primero el escaneo.",
        "en": "[*] No SDK detected -- run the scan first.",
        "pt": "[*] Nenhum SDK detectado -- rode o escaneamento primeiro.",
    },
    "so_patcher.menu_title": {
        "es": "Auto-Parcheo Binario y Neutralización de Telemetría/IAP",
        "en": "Binary Auto-Patcher and Telemetry/IAP Neutralization",
        "pt": "Auto-Patch Binário e Neutralização de Telemetria/IAP",
    },
    "so_patcher.menu_scan": {
        "es": "Escanear SDKs de telemetría/IAP y rutas hardcodeadas",
        "en": "Scan for telemetry/IAP SDKs and hardcoded paths",
        "pt": "Escanear SDKs de telemetria/IAP e caminhos hardcoded",
    },
    "so_patcher.menu_gen_stubs": {
        "es": "Generar stubs de neutralización para los SDKs detectados",
        "en": "Generate neutralization stubs for the detected SDKs",
        "pt": "Gerar stubs de neutralização para os SDKs detectados",
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Telemetry/IAP SDK fingerprinting
# ---------------------------------------------------------------------------

# Same technique as jni_analyzer._MIDDLEWARE_SIGNATURES: raw substring match
# against a file's bytes, not just its symbol table -- a stripped release
# .so still keeps string literals (AIDL action strings, log tags, version
# strings) even once symbol names are gone. See docs/dev-notes/so_patcher.md
# for why detection ALSO runs a second pass over the decompiled Java below --
# these signatures are primarily Java/DEX-shaped strings, so a game's own
# native lib often won't contain them even when the APK clearly bundles the
# SDK (the Java glue calls into the SDK's OWN .so, not the game's).
_TELEMETRY_SIGNATURES = {
    "Google Play Services / Play Games": (
        b"com/google/android/gms",
        b"GoogleApiClient",
        b"com/google/android/gms/games",
        b"PlayGamesSdk",
        b"GamesSignInClient",
    ),
    "Firebase Analytics": (
        b"FirebaseAnalytics",
        b"firebase-analytics",
        b"com/google/firebase/analytics",
        b"FirebaseApp",
    ),
    "In-App Billing / Google Play Billing": (
        b"com.android.vending.billing",
        b"IInAppBillingService",
        b"com/android/billingclient",
        b"BillingClient",
    ),
    "AdMob": (
        b"com/google/android/gms/ads",
        b"AdRequest",
        b"AdView",
        b"com.google.android.gms.ads",
    ),
    "Tapjoy": (
        b"com/tapjoy",
        b"TapjoyConnect",
        b"TJPlacement",
    ),
    "Facebook SDK": (
        b"com/facebook",
        b"FacebookSdk",
        b"com/facebook/appevents",
    ),
    "Flurry": (
        b"com/flurry",
        b"FlurryAgent",
    ),
}


def detect_telemetry_sdks(so_path):
    """!
    @brief Fingerprint known telemetry/IAP SDKs linked into a game's `.so`.
    @details Mirrors `jni_analyzer.detect_middleware()` exactly: a raw
           substring scan over the whole file's bytes, not just the dynamic
           symbol table.
    @param so_path Path to the Android `.so` file.
    @return list of matched SDK names (keys of `_TELEMETRY_SIGNATURES`),
            `[]` if `so_path` doesn't exist or nothing matched.
    """
    if not so_path or not os.path.exists(so_path):
        return []
    try:
        with open(so_path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    return [name for name, sigs in _TELEMETRY_SIGNATURES.items() if any(sig in data for sig in sigs)]


def _find_primary_so(project_dir):
    """!
    @brief Best-effort discovery of the port's original Android `.so`, same
           heuristic as `jni_analyzer._find_primary_so()` (duplicated locally
           rather than imported, since it's a private helper of that module).
    @param project_dir Path to the port's project directory.
    @return Path string to the most likely `.so`, or `None` if none found.
    """
    import glob
    candidates = glob.glob(os.path.join(str(project_dir), "**", "*.so"), recursive=True)
    candidates.sort(key=lambda x: 0 if ("libgame" in x or "libmain" in x) else 1)
    return candidates[0] if candidates else None


def _signature_variants(sig_bytes):
    """!
    @brief Build the string variants a `_TELEMETRY_SIGNATURES` byte-signature
           can plausibly appear as in decompiled Java source.
    @details A `.so`/DEX-shaped signature like `b"com/google/android/gms"`
           uses the internal (slash) form; jadx-decompiled Java source
           almost always prints the same package as dotted
           (`com.google.android.gms`). Checking both means the same
           signature table catches both without duplicating every entry.
    @param sig_bytes One raw byte-string signature.
    @return set of candidate substring(s) to search a line of Java text for.
    """
    s = sig_bytes.decode("ascii", errors="ignore")
    variants = {s}
    if "/" in s:
        variants.add(s.replace("/", "."))
    return variants


def scan_telemetry_sdks_in_java(project_dir):
    """!
    @brief Second, often more fruitful detection pass: scan the
           jadx-decompiled Java sources for the same `_TELEMETRY_SIGNATURES`
           strings, reporting exactly which file/line references each SDK
           (not just a yes/no per SDK).
    @param project_dir Path to the port's project directory.
    @return dict `{sdk_name: [(relative_file_path, line_no), ...], ...}`.
            Only SDKs with at least one hit are present. `{}` if there's no
            decompiled Java to scan.
    """
    project_dir = Path(project_dir)
    jadx_dir = project_dir / "decompiled" / "apk_jadx" / "sources"
    hits = {}
    if not jadx_dir.is_dir():
        return hits

    needles_by_sdk = {
        name: set().union(*(_signature_variants(sig) for sig in sigs))
        for name, sigs in _TELEMETRY_SIGNATURES.items()
    }

    for java_file in sorted(jadx_dir.glob("**/*.java")):
        if java_file.name.startswith("._"):
            continue
        try:
            lines = java_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        try:
            rel = str(java_file.relative_to(project_dir))
        except ValueError:
            rel = str(java_file)
        for line_no, line in enumerate(lines, 1):
            for name, needles in needles_by_sdk.items():
                if any(needle in line for needle in needles):
                    hits.setdefault(name, []).append((rel, line_no))
    return hits


# ---------------------------------------------------------------------------
# Hardcoded Android-only path / URL detection
# ---------------------------------------------------------------------------

# No capturing group needed: the whole alternation IS the match, used
# directly via m.group()/m.group(0) in finditer().
_HARDCODED_PATH_RE = re.compile(
    r'/sdcard/[A-Za-z0-9_./\-]*|/data/data/[A-Za-z0-9_./\-]*|https?://[^\s"\'<>\\]+'
)

# `strings`-equivalent, pure Python: a run of >=4 printable ASCII bytes is
# what the real `strings` binary would report by default -- no subprocess
# needed, and it works the same way on a stripped release .so since it never
# looks at the symbol table.
_PRINTABLE_RUN_RE = re.compile(rb'[\x20-\x7e]{4,}')


def _extract_printable_strings(data):
    """!
    @brief Pure-Python equivalent of `strings <file>`: extract every run of
           4+ printable ASCII bytes.
    @param data Raw file bytes.
    @return list of decoded strings, in file order.
    """
    return [m.decode("ascii", errors="ignore") for m in _PRINTABLE_RUN_RE.findall(data)]


def _categorize_hardcoded_path(matched_text):
    """!
    @brief Classify one matched hardcoded-path/URL literal.
    @param matched_text The literal matched by `_HARDCODED_PATH_RE`.
    @return One of `"sdcard"`, `"data_data"`, `"url"`.
    """
    if matched_text.startswith("http://") or matched_text.startswith("https://"):
        return "url"
    if matched_text.startswith("/data/data/"):
        return "data_data"
    return "sdcard"


def _suggestion_for_category(category, project_cfg):
    """!
    @brief Suggest a fix for one hardcoded-path category, using THIS
           project's own configured Vita path layout (not a guessed one).
    @param category Result of `_categorize_hardcoded_path()`.
    @param project_cfg Per-project config dict; reads `vita_game_data_dir`.
    @return Human-readable suggestion string (plain English -- this text is
            written straight into PORTING_PLAN.md, not through `t()`, same as
            `jni_analyzer.document_lifecycle_in_plan()`'s appended lines).
    """
    if category == "url":
        return ("network call -- likely needs FalsoJNI-side interception or "
                "removal, not a path rewrite (case-by-case, read the call site)")
    vita_dir = project_cfg.get("vita_game_data_dir", "/ux0:/data/<slug>")
    return f"suggested Vita-side replacement: `{vita_dir}` (this project's configured vita_game_data_dir)"


def find_hardcoded_paths(project_dir):
    """!
    @brief Scan the jadx-decompiled Java sources AND the primary `.so`'s raw
           strings for `/sdcard/...`, `/data/data/...` and `http(s)://...`
           literals -- common causes of a first-boot hang/crash on Vita.
    @param project_dir Path to the port's project directory.
    @return list of `(source_kind, file_or_so_name, line_no_or_None, matched_text)`
            tuples, in scan order. `source_kind` is `"java"` or `"so"`;
            `line_no_or_None` is `None` for `.so` hits (a stripped binary's
            raw strings carry no line information).
    """
    project_dir = Path(project_dir)
    hits = []

    jadx_dir = project_dir / "decompiled" / "apk_jadx" / "sources"
    if jadx_dir.is_dir():
        for java_file in sorted(jadx_dir.glob("**/*.java")):
            if java_file.name.startswith("._"):
                continue
            try:
                lines = java_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            try:
                rel = str(java_file.relative_to(project_dir))
            except ValueError:
                rel = str(java_file)
            for line_no, line in enumerate(lines, 1):
                for m in _HARDCODED_PATH_RE.finditer(line):
                    hits.append(("java", rel, line_no, m.group()))

    so_path = _find_primary_so(project_dir)
    if so_path:
        try:
            with open(so_path, "rb") as f:
                data = f.read()
        except OSError:
            data = b""
        so_name = os.path.basename(so_path)
        for s in _extract_printable_strings(data):
            for m in _HARDCODED_PATH_RE.finditer(s):
                hits.append(("so", so_name, None, m.group()))

    return hits


# ---------------------------------------------------------------------------
# telemetry_stubs.c / telemetry_stubs.h generation
# ---------------------------------------------------------------------------

# Checklists, not fabricated function bodies: without the SDK's real headers
# (or per-call-site confirmation of what the .so actually links against),
# generating fake `FirebaseAnalytics_logEvent(...)`-shaped functions would be
# dishonest -- static detection here confirms the SDK is PRESENT, not its
# exact native entry-point signatures. This is well-known public knowledge of
# each SDK's typical integration surface, offered as a starting checklist for
# the porter to adapt against the real Java (jadx has it, right there). See
# docs/dev-notes/so_patcher.md.
_SDK_STUB_CHECKLISTS = {
    "Google Play Services / Play Games": (
        "GoogleApiClient.connect() / GoogleSignInClient sign-in calls: make them report",
        "an immediate \"not signed in\" / \"connection failed\" result so the game's own",
        "offline/guest-mode fallback path runs, instead of the game waiting forever on a",
        "callback that will never fire.",
        "Leaderboards / Achievements / Cloud Save calls: no-op, returning empty results.",
        "Any Activity-result callback the game awaits after one of the above: make sure",
        "FalsoJNI still delivers a \"cancelled\"/\"failed\" result so the UI doesn't hang.",
    ),
    "Firebase Analytics": (
        "FirebaseApp.initializeApp(): no-op that still returns \"success\", so later",
        "logEvent()/setUserProperty() calls don't NPE on a null app instance.",
        "logEvent() / setUserProperty() / setCurrentScreen(): pure no-ops -- this is",
        "fire-and-forget telemetry, always safe to drop entirely.",
        "Crashlytics, if bundled alongside Analytics: also a safe no-op (don't let it",
        "try to phone home a real crash report from a Vita).",
    ),
    "In-App Billing / Google Play Billing": (
        "isBillingSupported() / BillingClient.startConnection(): report \"not available\"",
        "immediately -- there is no real store to buy from on PS Vita.",
        "getSkuDetails() / queryPurchases() / getPurchases(): return an empty",
        "list/inventory rather than blocking on a service that will never respond.",
        "launchPurchaseFlow() / purchase intent: report \"cancelled\"/\"failed\" immediately",
        "so the game's own purchase-failed UI path runs (confirm that path exists and",
        "doesn't itself assume a retry will succeed).",
    ),
    "AdMob": (
        "AdRequest.Builder / ad-load calls: always report \"no fill\" / \"failed to load\",",
        "so the game's own ad-failure fallback runs (skip the ad, continue play) instead",
        "of the game waiting on a network ad server that doesn't exist here.",
        "Rewarded-ad callbacks: report failure so the game doesn't wait forever for a",
        "reward callback that will never happen.",
        "Banner/interstitial show calls: no-op -- don't attempt to render a",
        "WebView-backed ad view on Vita.",
    ),
    "Tapjoy": (
        "TapjoyConnect.connect() / content-availability checks (TJPlacement etc.):",
        "report \"not available\"/\"not connected\" immediately.",
        "Video / offer-wall placements: no-op, never report as ready to show.",
    ),
    "Facebook SDK": (
        "FacebookSdk.sdkInitialize(): no-op.",
        "AppEventsLogger.logEvent(): pure no-op -- telemetry only, safe to fully remove.",
        "Login / Graph API calls, if the game paths through them at all (uncommon for a",
        "plain analytics/ad integration): report failure/cancelled.",
    ),
    "Flurry": (
        "FlurryAgent.onStartSession() / onEndSession() / logEvent(): pure no-ops --",
        "telemetry only, safe to fully remove rather than stub.",
    ),
}


def _telemetry_stub_header_lines():
    """!
    @brief Shared header comment block for both generated files.
    @return list of comment lines (no trailing newline).
    """
    return [
        "/* Auto-generated by psvita-toolkit -- telemetry/IAP neutralization checklist. */",
        "/* This is a STARTING CHECKLIST for the porter to adapt, NOT a drop-in         */",
        "/* replacement: these SDKs' real native entry points can't be known generically */",
        "/* from static detection alone (no headers, no per-call-site confirmation).     */",
        "/* Read the real Java implementation (jadx has it) before wiring anything in.   */",
        "/* See docs/dev-notes/so_patcher.md.                                            */",
    ]


def generate_telemetry_stubs(project_cfg, sdk_names, out_dir=None):
    """!
    @brief Generate `telemetry_stubs.c` + `telemetry_stubs.h`: a reviewable,
           checklist-style starting point for neutralizing the given detected
           SDKs, plus a small set of genuinely generic, safe no-op helpers.
    @param project_cfg Per-project config dict.
    @param sdk_names Iterable of detected SDK names (keys of
           `_TELEMETRY_SIGNATURES`); unknown names are silently skipped.
    @param out_dir Directory to write the two files into; defaults to
           `<project_dir>/source` if it exists, else the project root (same
           convention as `jni_analyzer.generate_jni_stubs()`).
    @note Every function actually EMITTED here is a generic, unconditionally
          safe no-op (`return 0`/`return false`/nothing) -- NOT a fabricated
          SDK-specific entry point. What each SDK typically needs stubbed is
          documented as a checklist comment for the porter to act on by hand,
          mirroring the honesty of `jni_analyzer.generate_jni_stubs()`'s own
          "candidates to review and merge... NOT a drop-in replacement" note.
    """
    project_dir = Path(project_cfg["_project_dir"])
    known = [name for name in sdk_names if name in _SDK_STUB_CHECKLISTS]
    if not known:
        print(t("so_patcher.stubs_none"))
        return

    header_lines = _telemetry_stub_header_lines() + [
        "",
        "#pragma once",
        "",
        "/* Generic, genuinely safe no-op helpers -- adapt names/return types to whatever", "",
    ]
    # (kept simple below; real content assembled next)
    header_lines = _telemetry_stub_header_lines() + [
        "",
        "#pragma once",
        "",
        "/* Generic, genuinely safe no-op helpers a stub can call into -- adapt names and",
        " * return types to whatever the porter ends up linking in place of the real SDK. */",
        "int psvita_toolkit_noop_return_zero(void);",
        "int psvita_toolkit_noop_return_false(void);",
        "void psvita_toolkit_noop_void(void);",
        "",
    ]

    stub_lines = _telemetry_stub_header_lines() + [
        "",
        '#include "telemetry_stubs.h"',
        "",
        "int psvita_toolkit_noop_return_zero(void) { return 0; }",
        "int psvita_toolkit_noop_return_false(void) { return 0; }",
        "void psvita_toolkit_noop_void(void) { /* intentionally empty */ }",
        "",
    ]

    for name in known:
        stub_lines.append(f"/* {'=' * 76} */")
        stub_lines.append(f"/* {name}")
        stub_lines.append(" *")
        stub_lines.append(" * Checklist -- confirm each item against the real Java in jadx before")
        stub_lines.append(" * touching any native call site:")
        for line in _SDK_STUB_CHECKLISTS[name]:
            stub_lines.append(f" *   - {line}")
        stub_lines.append(f" * {'=' * 76} */")
        stub_lines.append("")

    dest = Path(out_dir) if out_dir else (project_dir / "source" if (project_dir / "source").is_dir() else project_dir)
    dest.mkdir(parents=True, exist_ok=True)
    header_path = dest / "telemetry_stubs.h"
    stub_path = dest / "telemetry_stubs.c"
    header_path.write_text("\n".join(header_lines) + "\n", encoding="utf-8")
    stub_path.write_text("\n".join(stub_lines) + "\n", encoding="utf-8")

    print(t("so_patcher.stubs_generated", count=len(known), header=header_path.name, stub=stub_path.name))


# ---------------------------------------------------------------------------
# PORTING_PLAN.md reporting
# ---------------------------------------------------------------------------

def document_findings_in_plan(project_cfg, sdk_hits, path_hits):
    """!
    @brief Append a `## Auto-patch findings (psvita-toolkit)` section to the
           project's `PORTING_PLAN.md`, same append-if-exists-else-warn
           pattern as `jni_analyzer.document_lifecycle_in_plan()`.
    @param project_cfg Per-project config dict.
    @param sdk_hits dict `{sdk_name: {"so": bool, "java_evidence": [(file, line), ...]}}`
           (the shape `run_patch_scan()` assembles from `detect_telemetry_sdks()`
           and `scan_telemetry_sdks_in_java()`).
    @param path_hits Result of `find_hardcoded_paths()`.
    """
    project_dir = Path(project_cfg["_project_dir"])
    if not sdk_hits and not path_hits:
        print(t("so_patcher.plan_nothing_to_document"))
        return

    plan_path = project_dir / "PORTING_PLAN.md"
    if not plan_path.exists():
        print(f"{C.YELLOW}{t('so_patcher.plan_not_found')}{C.RESET}")
        return

    lines = ["", "## Auto-patch findings (psvita-toolkit)", "",
             "Telemetry/IAP SDKs and hardcoded Android-only paths/URLs detected by a static",
             "scan of the game's `.so` and the jadx-decompiled Java -- see",
             "`docs/dev-notes/so_patcher.md` for why this stays a static, source-level scan",
             "(no live in-memory binary patching), and why the generated stubs in",
             "`telemetry_stubs.c` are a checklist to adapt, not a drop-in replacement.", ""]

    if sdk_hits:
        lines.append("### Telemetry / IAP SDKs detected")
        lines.append("")
        for name in sorted(sdk_hits):
            info = sdk_hits[name]
            where = []
            if info.get("so"):
                where.append("in the game's `.so`")
            evidence = info.get("java_evidence") or []
            if evidence:
                where.append(f"{len(evidence)} reference(s) in the decompiled Java")
            lines.append(f"- **{name}** -- detected {', '.join(where) if where else '(source unclear)'}")
            for file, line_no in evidence[:5]:
                lines.append(f"  - `{file}:{line_no}`")
            if len(evidence) > 5:
                lines.append(f"  - ... and {len(evidence) - 5} more reference(s)")
        lines.append("")

    if path_hits:
        lines.append("### Hardcoded Android-only paths / network calls")
        lines.append("")
        capped = path_hits[:30]
        for source_kind, name, line_no, matched in capped:
            loc = f"{name}:{line_no}" if line_no is not None else name
            category = _categorize_hardcoded_path(matched)
            suggestion = _suggestion_for_category(category, project_cfg)
            lines.append(f"- `{matched}` ({source_kind}, `{loc}`) -- {suggestion}")
        if len(path_hits) > len(capped):
            lines.append(f"- ... and {len(path_hits) - len(capped)} more (re-run the scan to see the full list)")
        lines.append("")

    with open(plan_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"{C.GREEN}{t('so_patcher.plan_updated', plan_path=plan_path)}{C.RESET}")


# ---------------------------------------------------------------------------
# Orchestration + TUI
# ---------------------------------------------------------------------------

def run_patch_scan(project_cfg, global_cfg=None):
    """!
    @brief Full detection pass: find the primary `.so`, fingerprint
           telemetry/IAP SDKs (binary + decompiled-Java passes) and hardcoded
           paths/URLs, print a summary, then document it all in
           `PORTING_PLAN.md`.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (accepted for a uniform menu-item
           call signature; unused today).
    @return `(sdk_hits, path_hits)` -- same shapes as
            `document_findings_in_plan()`'s parameters, handy for a caller
            (e.g. `patch_menu()`) that wants to act on what was just found.
    """
    project_dir = Path(project_cfg["_project_dir"])
    print(t("so_patcher.scan_title"))

    so_path = _find_primary_so(project_dir)
    if not so_path:
        print(f"{C.YELLOW}{t('so_patcher.so_not_found')}{C.RESET}")

    so_sdk_names = detect_telemetry_sdks(so_path) if so_path else []
    java_hits = scan_telemetry_sdks_in_java(project_dir)

    sdk_hits = {}
    for name in _TELEMETRY_SIGNATURES:
        if name in so_sdk_names or name in java_hits:
            sdk_hits[name] = {"so": name in so_sdk_names, "java_evidence": java_hits.get(name, [])}

    if sdk_hits:
        print(f"{C.GREEN}{t('so_patcher.sdk_found', names=', '.join(sorted(sdk_hits)))}{C.RESET}")
    else:
        print(t("so_patcher.sdk_none"))

    path_hits = find_hardcoded_paths(project_dir)
    if path_hits:
        print(f"{C.YELLOW}{t('so_patcher.path_found', count=len(path_hits))}{C.RESET}")
        for source_kind, name, line_no, matched in path_hits[:10]:
            loc = f"{name}:{line_no}" if line_no is not None else name
            print(f"    [{source_kind}] {loc} -- {matched}")
        if len(path_hits) > 10:
            print(f"    ... {len(path_hits) - 10} more")
    else:
        print(t("so_patcher.path_none"))

    document_findings_in_plan(project_cfg, sdk_hits, path_hits)
    return sdk_hits, path_hits


def patch_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: menu with "scan" and "generate stubs for
           whatever was detected" actions.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (passed through to `run_patch_scan()`).
    """
    def _scan():
        run_patch_scan(project_cfg, global_cfg)

    def _gen_stubs():
        project_dir = Path(project_cfg["_project_dir"])
        so_path = _find_primary_so(project_dir)
        so_sdk_names = detect_telemetry_sdks(so_path) if so_path else []
        java_hits = scan_telemetry_sdks_in_java(project_dir)
        all_names = sorted(set(so_sdk_names) | set(java_hits))
        if not all_names:
            print(t("so_patcher.no_sdks_detected_for_stubs"))
            return
        generate_telemetry_stubs(project_cfg, all_names)

    tui.run_menu(
        t("so_patcher.menu_title"),
        [
            (t("so_patcher.menu_scan"), _scan),
            (t("so_patcher.menu_gen_stubs"), _gen_stubs),
        ],
    )
