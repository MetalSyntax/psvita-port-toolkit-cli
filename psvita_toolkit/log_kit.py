"""!
@brief Canonical on-device log standard + deterministic function-trace injector.
@details Every sibling port historically invented its own log convention
         (`log_<timestamp>.txt` in the repo root, `log_NNN.log` starting at 0,
         `<slug>_NNN.log` starting at 1 with or without `next.idx`, hardcoded
         `ux0:data/...` paths vs `DATA_PATH`, compile-time vs runtime log
         levels). This module pins ONE standard -- the Asphalt-5-Vita logger,
         the most complete one -- and gives every other port a mechanical,
         AI-free path to converge on it:
         - `check_log_standard()` reports the discrepancies of a port without
           touching anything;
         - `ensure_local_log_dirs()` recreates the `logs/`+`saves/` staging
           dirs (with `.gitkeep` so git tracks them; the FTP uploader skips the
           placeholder payload but still creates the remote dir);
         - `inject_trace_logs()` / `strip_trace_logs()` add or remove detailed
           per-function entry logs guarded by `#ifdef PORT_TRACE`, purely with
           regex -- no LLM involved -- so a debug build can be traced and the
           exact same tree ships to production with the flag off.
@note Trace markers are entry-only (`[TRACE] >> func() file:line`). Exit/return
       tracing is deliberately NOT attempted: with early returns and gotos,
       regex-placed exit logs lie, and a lying log is worse than no log.
"""

import re
from pathlib import Path

LOG_DIR_NAME = "logs"
SAVES_DIR_NAME = "saves"
LOG_INDEX_MIN = 1
LOG_INDEX_MAX = 999

TRACE_MACRO = "PORT_TRACE"
TRACE_MARKER = "PORT_TRACE_AUTO"
CMAKE_TRACE_BLOCK = """\
# --- PORT_TRACE managed by psvita-toolkit log_kit (do not hand-edit) ---
option(PORT_TRACE "Detailed per-function trace logs (debug only, OFF for production)" OFF)
if (PORT_TRACE)
  add_definitions(-DPORT_TRACE)
endif()
# --- /PORT_TRACE managed by psvita-toolkit log_kit ---
"""

#: Function-definition matcher: `<ret> name(<args>) {` at line start
#: (K&R, static, pointer returns). Conservative on purpose -- a missed
#: function only loses a trace line; a false positive would break a build.
_FUNC_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?:static[ \t]+)?"
    r"(?:[A-Za-z_][\w\s\*]*?)"      # return type (incl. pointers)
    r"(?P<name>[A-Za-z_]\w*)"       # function name
    r"[ \t]*\([^;]*\)"              # parameter list, no prototype `;`
    r"[ \t]*\{[ \t]*$"
)
_CONTROL_KEYWORDS = {
    "if", "for", "while", "switch", "do", "else", "return",
    "sizeof", "typedef", "struct", "enum", "union",
}


def expected_log_basename(slug, index):
    """!
    @brief Canonical per-run log filename for a port.
    @param slug Port slug (e.g. `asphalt5`).
    @param index Run index in [LOG_INDEX_MIN, LOG_INDEX_MAX].
    @return Basename like `asphalt5_001.log`.
    """
    return f"{slug}_{index:03d}.log"


def _find_logger_files(project_dir):
    """!
    @brief Locate a port's logger implementation, if any.
    @param project_dir Port root directory.
    @return Dict with `header`/`source` Path or None values.
    """
    project_dir = Path(project_dir)
    header = source = None
    for cand in (
        project_dir / "source" / "utils" / "logger.h",
        project_dir / "source" / "reimpl" / "logger.h",
        project_dir / "loader" / "logger.h",
    ):
        if cand.exists():
            header = cand
            break
    for cand in (
        project_dir / "source" / "utils" / "logger.c",
        project_dir / "source" / "reimpl" / "logger.c",
        project_dir / "loader" / "logger.c",
    ):
        if cand.exists():
            source = cand
            break
    return {"header": header, "source": source}


def check_log_standard(project_dir, slug):
    """!
    @brief Compare a port's logger against the canonical standard, read-only.
    @param project_dir Port root directory.
    @param slug Port slug used for the expected `<slug>_NNN.log` basename.
    @return List of discrepancy strings (empty means conformant).
    @details Checks, in order: logger files exist; basename uses the slug
             prefix (`log_NNN` and `log_<timestamp>` fail); index starts at
             001, not 000; rotation caps at 999; path built from `DATA_PATH`
             instead of a hardcoded `ux0:data/<game>` literal; a production
             flag exists (`ENABLE_FILE_LOG`, `ACTIVE_LOG_LEVEL`,
             `RELEASE_BUILD`, or `PORT_TRACE`); `logs/`+`saves/` staging dirs
             exist under `ux0_data/<slug>/`.
    """
    project_dir = Path(project_dir)
    issues = []
    found = _find_logger_files(project_dir)
    if not found["header"] and not found["source"]:
        issues.append("no logger.h/logger.c found (expected source/utils/logger.*)")
        return issues
    text = ""
    for key in ("header", "source"):
        if found[key]:
            try:
                text += "\n" + found[key].read_text(errors="replace")
            except OSError:
                issues.append(f"unreadable {found[key]}")
    if "log_" in text and f"{slug}_" not in text:
        issues.append(
            f"basename does not use the slug prefix (expected `{slug}_NNN.log`, "
            "found generic `log_NNN.log` or timestamp scheme)"
        )
    if "log_%03d" in text and '"%03d", i' in text.replace(" ", ""):
        pass  # naming shape ok, range checked below
    m = re.search(r"for\s*\(\s*int\s+i\s*=\s*(\d+)", text)
    if m and int(m.group(1)) == 0 and "LOG_INDEX_MIN" not in text:
        issues.append("rotation starts at 000, standard starts at 001")
    if "999" not in text and "1000" not in text and "LOG_INDEX_MAX" not in text:
        issues.append("no 001..999 rotation cap found")
    if "next.idx" not in text and "LOG_NEXT_PATH" not in text:
        issues.append("no next.idx hint (each boot re-scans 001..999 linearly)")
    if "DATA_PATH" not in text and "ux0:data/" in text:
        issues.append("log path hardcoded (`ux0:data/<game>/...`) instead of DATA_PATH")
    if not any(k in text for k in ("ENABLE_FILE_LOG", "ACTIVE_LOG_LEVEL", "RELEASE_BUILD", TRACE_MACRO)):
        issues.append("no production flag (ENABLE_FILE_LOG / ACTIVE_LOG_LEVEL / PORT_TRACE)")
    staging = project_dir / "ux0_data" / slug
    for dirname in (LOG_DIR_NAME, SAVES_DIR_NAME):
        if not (staging / dirname).is_dir():
            issues.append(f"missing local staging dir `ux0_data/{slug}/{dirname}/`")
    return issues


def ensure_local_log_dirs(project_dir, slug):
    """!
    @brief Recreate `ux0_data/<slug>/logs/` and `saves/` with `.gitkeep`.
    @param project_dir Port root directory.
    @param slug Port slug.
    @return List of directories created (or already present).
    @note The FTP uploader skips `.gitkeep` payload but still creates the
          remote dir, so these arrive on the console even while empty.
    """
    project_dir = Path(project_dir)
    ready = []
    for dirname in (LOG_DIR_NAME, SAVES_DIR_NAME):
        d = project_dir / "ux0_data" / slug / dirname
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.write_text("")
        ready.append(d)
    return ready


def inject_trace_logs(project_dir, subdirs=("source",), dry_run=False):
    """!
    @brief Insert entry-trace logs in every C function found, deterministically.
    @param project_dir Port root directory.
    @param subdirs Subdirectories to scan (default: just `source/`).
    @param dry_run When True, count only -- write nothing.
    @return Dict with `files_scanned`, `functions_found`, `inserted`, `skipped`
            (already instrumented), and per-file `details`.
    @details Pure regex, no AI: a line looking like a function definition gets
             one `#ifdef PORT_TRACE ... l_debug("[TRACE] >> ...")` block
             inserted right after its opening brace, tagged with
             `PORT_TRACE_AUTO`. Files already containing the marker for a given
             function are skipped, so re-runs are idempotent. Requires the
             port's `l_debug` macro (every soloader logger provides it).
    """
    project_dir = Path(project_dir)
    stats = {"files_scanned": 0, "functions_found": 0, "inserted": 0,
             "skipped": 0, "details": []}
    targets = []
    for sub in subdirs:
        root = project_dir / sub
        if root.is_dir():
            targets.extend(sorted(root.rglob("*.c")))
    for path in targets:
        try:
            lines = path.read_text(errors="replace").splitlines(keepends=True)
        except OSError:
            continue
        stats["files_scanned"] += 1
        out = []
        file_inserts = 0
        for idx, line in enumerate(lines):
            out.append(line)
            stripped = line.strip()
            if not stripped.endswith("{"):
                continue
            m = _FUNC_DEF_RE.match(line.rstrip("\n"))
            if not m:
                continue
            # Two guards against control statements (`if (x) {` parses as
            # return-type `i` + name `f` under the loose regex above):
            # definitions live at column 0 in this codebase, and neither the
            # first nor the last identifier before `(` may be a keyword.
            if m.group("indent") != "":
                continue
            words = re.findall(r"[A-Za-z_]\w*", line.split("(", 1)[0])
            if not words or words[0] in _CONTROL_KEYWORDS or words[-1] in _CONTROL_KEYWORDS:
                continue
            func = words[-1]
            stats["functions_found"] += 1
            # Idempotency: the block we insert sits directly below, so if any
            # of the next lines already carries our marker, skip.
            if any(TRACE_MARKER in nxt for nxt in lines[idx + 1:idx + 4]):
                continue
            # Rebuild with correct indent: first line `#ifdef` at base indent.
            base = m.group("indent")
            snippet_lines = [
                base + "#ifdef " + TRACE_MACRO + "\n",
                base + "    { /* " + TRACE_MARKER + " */ "
                + f'l_debug("[TRACE] >> {func}() {path.name}:%d", __LINE__); }}\n',
                base + "#endif\n",
            ]
            out.append("".join(snippet_lines))
            file_inserts += 1
        if file_inserts:
            stats["inserted"] += file_inserts
            stats["details"].append((str(path.relative_to(project_dir)), file_inserts))
            if not dry_run:
                path.write_text("".join(out))
    stats["skipped"] = stats["functions_found"] - stats["inserted"]
    return stats


def strip_trace_logs(project_dir, subdirs=("source",)):
    """!
    @brief Remove every trace block previously added by `inject_trace_logs()`.
    @param project_dir Port root directory.
    @param subdirs Subdirectories to scan (default: just `source/`).
    @return Dict with `files_scanned`, `blocks_removed`, and per-file `details`.
    @details Only removes the exact 3-line `#ifdef PORT_TRACE ... #endif`
             blocks carrying the `PORT_TRACE_AUTO` marker -- hand-written
             logging is never touched. This is the production path: strip (or
             just build with `PORT_TRACE=OFF`, which compiles the blocks out).
    """
    project_dir = Path(project_dir)
    stats = {"files_scanned": 0, "blocks_removed": 0, "details": []}
    targets = []
    for sub in subdirs:
        root = project_dir / sub
        if root.is_dir():
            targets.extend(sorted(root.rglob("*.c")))
    block_re = re.compile(
        r"^[ \t]*#ifdef " + TRACE_MACRO + r"\n"
        r"^[ \t]*\{ /\* " + TRACE_MARKER + r" \*/.*?\n"
        r"^[ \t]*#endif\n",
        re.MULTILINE,
    )
    for path in targets:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        stats["files_scanned"] += 1
        new_text, n = block_re.subn("", text)
        if n:
            stats["blocks_removed"] += n
            stats["details"].append((str(path.relative_to(project_dir)), n))
            path.write_text(new_text)
    return stats


def ensure_cmake_trace_flag(project_dir):
    """!
    @brief Ensure `CMakeLists.txt` has the `PORT_TRACE` option block.
    @param project_dir Port root directory.
    @return `True` if the block was already present, `"added"` if appended,
             `None` when no `CMakeLists.txt` exists.
    @details Production flow: `cmake -DPORT_TRACE=ON` for trace builds,
             default `OFF` compiles every injected block out -- the tree
             ships the same either way, no manual log cleanup.
    """
    cmake = Path(project_dir) / "CMakeLists.txt"
    if not cmake.exists():
        return None
    text = cmake.read_text(errors="replace")
    if TRACE_MACRO in text:
        return True
    with open(cmake, "a") as f:
        f.write("\n" + CMAKE_TRACE_BLOCK)
    return "added"
