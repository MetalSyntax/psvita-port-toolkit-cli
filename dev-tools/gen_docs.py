#!/usr/bin/env python3
"""!
@file gen_docs.py
@brief Maintainer tool (not part of the runtime TUI) that automates the
       mechanical half of documenting this codebase, so it doesn't cost LLM
       tokens every time a function needs a Doxygen skeleton or the API
       reference needs regenerating.

@details
Two purely mechanical steps, both driven by reading the actual function
signatures with Python's `ast` module -- neither ever invents rationale or
prose, only structure:

  1. **Skeleton generation**: for any module/class/function under
     `psvita_toolkit/*.py` that has no docstring at all yet, insert a
     Doxygen-style (triple-quote-bang) skeleton with @brief/@param/@return
     generated from its signature (parameter names, whether it returns a
     value). A human (or an LLM, spending tokens ONLY on this part) still has
     to fill in the actual `TODO` descriptions -- this step just guarantees
     every symbol has the right shape to fill in, without spending anything
     to produce that shape.

  2. **API reference markdown**: copies the `@brief`/`@details`/`@param`/
     `@return`/`@note`/`@warning` content already present in every Doxygen
     docstring into `docs/api/<module>.md`, one file per module. Pure
     copy-and-format, no interpretation -- this is NOT the same as
     `docs/dev-notes/<module>.md` (the hand-written rationale/"why" docs),
     which this script does not touch and cannot generate.

If `doxygen` is installed (`brew install doxygen`), step 2 instead shells out
to the real tool against a generated `Doxyfile` (`GENERATE_XML=YES`), and
pipes its XML through `doxybook2` (if that's also installed) to produce the
same `docs/api/` markdown -- more standards-compliant than the built-in
fallback, since it reuses Doxygen's actual Python docstring parser instead of
this script's simpler regex-based one. Both steps fall back to the
stdlib-only implementation below if the external tools aren't present, so
this always works with zero setup.

Usage:
    python3 dev-tools/gen_docs.py                 # both steps
    python3 dev-tools/gen_docs.py --skeletons-only
    python3 dev-tools/gen_docs.py --api-only
    python3 dev-tools/gen_docs.py --check          # exit 1 if anything is
                                                    # missing a docstring,
                                                    # without writing anything
                                                    # (for CI)
"""

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "psvita_toolkit"
API_DOCS_DIR = REPO_ROOT / "docs" / "api"


# ---------------------------------------------------------------------------
# External tool detection -- used if present, never required.
# ---------------------------------------------------------------------------

def find_doxygen():
    """!
    @brief Locate the `doxygen` binary, if installed.
    @return Path string to the `doxygen` executable, or `None` if not found.
    """
    return shutil.which("doxygen")


def find_doxybook2():
    """!
    @brief Locate the `doxybook2` binary (Doxygen-XML-to-Markdown converter),
           if installed.
    @return Path string to the `doxybook2` executable, or `None` if not found.
    """
    return shutil.which("doxybook2")


def iter_py_files():
    """!
    @brief Iterate every `psvita_toolkit/*.py` module (top-level only, no
           `__pycache__`).
    @return Sorted list of `Path` objects.
    @note Excludes macOS AppleDouble metadata files (`._foo.py`), which also
          match the `*.py` glob and aren't valid Python/UTF-8.
    """
    return sorted(p for p in PACKAGE_DIR.glob("*.py") if p.is_file() and not p.name.startswith("._"))


# ---------------------------------------------------------------------------
# Step 1: Doxygen skeleton generation (ast-based, always available).
# ---------------------------------------------------------------------------

def _walk_own_body(node):
    """!
    @brief Yield every descendant of `node`'s body without descending into
           nested function/class definitions.
    @param node `ast.FunctionDef`/`ast.AsyncFunctionDef`/`ast.ClassDef` to walk.
    @return Generator of `ast` nodes.
    @note `ast.walk()` cannot be pruned mid-traversal -- it precomputes the
          full flattened subtree regardless of what a caller does with each
          yielded node, so a naive `continue` on a nested `FunctionDef` does
          NOT stop its own body's nodes (including its `Return` statements)
          from still being yielded afterward. This walker prunes for real by
          only pushing a node's children onto the stack when it isn't itself
          a nested function/class boundary.
    """
    stack = list(node.body)
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(current))


def _has_nonempty_return(node):
    """!
    @brief Check whether a function body contains a `return <value>`
           (not a bare `return`), without descending into nested function
           definitions (those have their own, separate skeleton).
    @param node `ast.FunctionDef`/`ast.AsyncFunctionDef` to inspect.
    @return `True` if a `return` with a value is found in this function's
            own body.
    """
    return any(isinstance(n, ast.Return) and n.value is not None for n in _walk_own_body(node))


def _param_names(node):
    """!
    @brief Extract parameter names from a function's signature, skipping
           `self`/`cls`.
    @param node `ast.FunctionDef`/`ast.AsyncFunctionDef` to inspect.
    @return List of parameter name strings, in declaration order.
    """
    args = node.args
    names = [a.arg for a in args.posonlyargs] if hasattr(args, "posonlyargs") else []
    names += [a.arg for a in args.args]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    names += [a.arg for a in args.kwonlyargs]
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return names


def _function_skeleton(node, indent):
    """!
    @brief Build a Doxygen skeleton docstring for a function/method.
    @param node `ast.FunctionDef`/`ast.AsyncFunctionDef` to document.
    @param indent Indentation string (spaces) to prefix every line with.
    @return The skeleton docstring text, including the `\"\"\"!`/`\"\"\"` fences.
    """
    lines = [f'{indent}"""!', f"{indent}@brief TODO: describe {node.name}."]
    for name in _param_names(node):
        lines.append(f"{indent}@param {name} TODO: describe {name}.")
    if _has_nonempty_return(node):
        lines.append(f"{indent}@return TODO: describe the return value.")
    lines.append(f'{indent}"""')
    return "\n".join(lines) + "\n"


def _class_skeleton(node, indent):
    """!
    @brief Build a Doxygen skeleton docstring for a class.
    @param node `ast.ClassDef` to document.
    @param indent Indentation string (spaces) to prefix every line with.
    @return The skeleton docstring text, including the `\"\"\"!`/`\"\"\"` fences.
    """
    return f'{indent}"""!\n{indent}@brief TODO: describe {node.name}.\n{indent}"""\n'


def _module_skeleton(module_name):
    """!
    @brief Build a Doxygen skeleton docstring for a module.
    @param module_name File name of the module (e.g. `"utils.py"`).
    @return The skeleton docstring text, including the `\"\"\"!`/`\"\"\"` fences.
    """
    return f'"""!\n@file {module_name}\n@brief TODO: describe this module.\n"""\n\n'


def find_missing_docstrings(py_file):
    """!
    @brief Find every module/class/top-level-function/method in `py_file`
           that has no docstring at all yet.
    @param py_file Path to the `.py` file to inspect.
    @return List of `(node_or_None_for_module, kind)` tuples needing a
            skeleton, `kind` being one of `"module"`, `"class"`, `"function"`.
            Nested (closure) functions are intentionally skipped -- see
            `docs/dev-notes/gen_docs.md`.
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    missing = []
    if ast.get_docstring(tree) is None:
        missing.append((None, "module"))
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            if ast.get_docstring(node) is None:
                missing.append((node, "class"))
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(sub) is None:
                    missing.append((sub, "function"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node) is None:
            missing.append((node, "function"))
    return missing


def insert_missing_skeletons(py_file, dry_run=False):
    """!
    @brief Insert a generated Doxygen skeleton for every symbol in `py_file`
           that has no docstring yet.
    @param py_file Path to the `.py` file to update in place.
    @param dry_run If `True`, don't write the file -- just report the count.
    @return Number of skeletons inserted.
    """
    missing = find_missing_docstrings(py_file)
    if not missing:
        return 0

    lines = py_file.read_text(encoding="utf-8").splitlines(keepends=True)
    # Apply bottom-to-top so earlier insertions don't shift later line numbers.
    insertions = []  # (line_index_to_insert_before, text)
    for node, kind in missing:
        if kind == "module":
            insertions.append((0, _module_skeleton(py_file.name)))
        else:
            first_stmt = node.body[0]
            indent = " " * first_stmt.col_offset
            skeleton = _class_skeleton(node, indent) if kind == "class" else _function_skeleton(node, indent)
            insertions.append((first_stmt.lineno - 1, skeleton))

    for line_idx, text in sorted(insertions, key=lambda t: t[0], reverse=True):
        lines.insert(line_idx, text)

    if not dry_run:
        py_file.write_text("".join(lines), encoding="utf-8")
    return len(missing)


# ---------------------------------------------------------------------------
# Step 2 (fallback): API reference markdown, copied straight from the
# Doxygen tags already present in the code -- no interpretation.
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"^@(brief|details|param|return|returns|note|warning)\b\s*(.*)$")


def _parse_doxygen_docstring(doc):
    """!
    @brief Parse a Doxygen-style docstring's tags into structured fields.
    @param doc Raw docstring text (as returned by `ast.get_docstring()`).
    @return dict with keys `brief`, `details` (list of lines), `params`
            (list of `(name, desc)`), `returns` (str or `None`), `notes`
            (list of str), `warnings` (list of str).
    """
    info = {"brief": "", "details": [], "params": [], "returns": None, "notes": [], "warnings": []}
    if not doc:
        return info
    current = None
    for raw_line in doc.splitlines():
        line = raw_line.strip().lstrip("!").strip()
        m = _TAG_RE.match(line)
        if m:
            tag, rest = m.group(1), m.group(2)
            if tag == "brief":
                info["brief"] = rest
                current = ("brief",)
            elif tag == "details":
                info["details"].append(rest)
                current = ("details",)
            elif tag == "param":
                parts = rest.split(None, 1)
                name = parts[0] if parts else ""
                desc = parts[1] if len(parts) > 1 else ""
                info["params"].append([name, desc])
                current = ("param", len(info["params"]) - 1)
            elif tag in ("return", "returns"):
                info["returns"] = rest
                current = ("returns",)
            elif tag == "note":
                info["notes"].append(rest)
                current = ("notes", len(info["notes"]) - 1)
            elif tag == "warning":
                info["warnings"].append(rest)
                current = ("warnings", len(info["warnings"]) - 1)
            continue
        if not line:
            continue
        # Continuation line of a multi-line tag (e.g. wrapped @param text).
        if current is None:
            continue
        if current[0] == "brief":
            info["brief"] = f"{info['brief']} {line}".strip()
        elif current[0] == "details":
            info["details"][-1] = f"{info['details'][-1]} {line}".strip()
        elif current[0] == "param":
            info["params"][current[1]][1] = f"{info['params'][current[1]][1]} {line}".strip()
        elif current[0] == "returns":
            info["returns"] = f"{info['returns']} {line}".strip()
        elif current[0] in ("notes", "warnings"):
            lst = info[current[0]]
            lst[current[1]] = f"{lst[current[1]]} {line}".strip()
    return info


def _format_symbol_section(title, node, doc):
    """!
    @brief Format one module/class/function's parsed Doxygen info as a
           markdown section.
    @param title Markdown heading text (e.g. a function signature).
    @param node `ast` node the docstring belongs to (used for heading level).
    @param doc Raw docstring text.
    @return Markdown text for this symbol's section, or `""` if there's no
            docstring to report.
    """
    if not doc:
        return ""
    info = _parse_doxygen_docstring(doc)
    heading = "###" if node is not None else "##"
    out = [f"{heading} `{title}`\n"]
    if info["brief"]:
        out.append(f"{info['brief']}\n")
    for d in info["details"]:
        out.append(f"\n{d}\n")
    if info["params"]:
        out.append("\n**Parameters:**\n")
        for name, desc in info["params"]:
            out.append(f"- `{name}` — {desc}\n")
    if info["returns"]:
        out.append(f"\n**Returns:** {info['returns']}\n")
    for n in info["notes"]:
        out.append(f"\n**Note:** {n}\n")
    for w in info["warnings"]:
        out.append(f"\n**Warning:** {w}\n")
    out.append("\n")
    return "".join(out)


def _signature(node):
    """!
    @brief Build a short `name(params)` signature string for a function.
    @param node `ast.FunctionDef`/`ast.AsyncFunctionDef`.
    @return Signature string, e.g. `"t(key, **kwargs)"`.
    """
    return f"{node.name}({', '.join(_param_names(node))})"


def build_api_markdown(py_file):
    """!
    @brief Mechanically build a full API reference markdown document for one
           module, copying its Doxygen tags without interpretation.
    @param py_file Path to the `.py` file to document.
    @return Markdown text for `docs/api/<module>.md`.
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    module_doc = ast.get_docstring(tree)
    module_info = _parse_doxygen_docstring(module_doc)
    title = module_info["brief"] or py_file.stem
    sections = [f"# `{py_file.name}`\n\n{title}\n"]
    for d in module_info["details"]:
        sections.append(f"\n{d}\n")
    for n in module_info["notes"]:
        sections.append(f"\n**Note:** {n}\n")
    sections.append("\n")

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            class_doc = ast.get_docstring(node)
            sections.append(_format_symbol_section(f"class {node.name}", node, class_doc))
            for sub in ast.iter_child_nodes(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sections.append(_format_symbol_section(f"{node.name}.{_signature(sub)}", sub, ast.get_docstring(sub)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sections.append(_format_symbol_section(_signature(node), node, ast.get_docstring(node)))

    return "".join(s for s in sections if s)


def generate_api_docs_fallback():
    """!
    @brief Generate `docs/api/<module>.md` for every module, using the
           stdlib-only extractor.
    @return Number of files written.
    """
    API_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for py_file in iter_py_files():
        md = build_api_markdown(py_file)
        out_path = API_DOCS_DIR / f"{py_file.stem}.md"
        out_path.write_text(md, encoding="utf-8")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Step 2 (preferred): real doxygen + doxybook2, if installed.
# ---------------------------------------------------------------------------

_DOXYFILE_OVERRIDES = {
    "PROJECT_NAME": '"psvita-port-toolkit"',
    "INPUT": "psvita_toolkit",
    "RECURSIVE": "NO",
    "GENERATE_LATEX": "NO",
    "GENERATE_HTML": "NO",
    "GENERATE_XML": "YES",
    "QUIET": "YES",
    "WARN_IF_UNDOCUMENTED": "NO",
    "JAVADOC_AUTOBRIEF": "NO",
    "EXTRACT_ALL": "YES",
    "FILE_PATTERNS": "*.py",
}


def _write_doxyfile(doxygen_bin, doxyfile_path, xml_out_dir):
    """!
    @brief Generate a `Doxyfile` (via `doxygen -g`) and patch it for this
           project.
    @param doxygen_bin Path to the `doxygen` executable.
    @param doxyfile_path Where to write the generated `Doxyfile`.
    @param xml_out_dir Directory Doxygen should write its XML output into.
    """
    subprocess.run([doxygen_bin, "-g", str(doxyfile_path)], cwd=REPO_ROOT,
                    capture_output=True, text=True, check=True)
    text = doxyfile_path.read_text(encoding="utf-8")
    overrides = dict(_DOXYFILE_OVERRIDES, XML_OUTPUT=str(xml_out_dir.relative_to(REPO_ROOT)),
                      OUTPUT_DIRECTORY=str(xml_out_dir.parent.relative_to(REPO_ROOT)))
    for key, value in overrides.items():
        pattern = re.compile(rf"^{key}\s*=.*$", re.MULTILINE)
        replacement = f"{key} = {value}"
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text += f"\n{replacement}\n"
    doxyfile_path.write_text(text, encoding="utf-8")


def generate_api_docs_with_doxygen(doxygen_bin, doxybook2_bin):
    """!
    @brief Run the real `doxygen` (and `doxybook2`, if installed) to
           (re)generate `docs/api/`.
    @param doxygen_bin Path to the `doxygen` executable.
    @param doxybook2_bin Path to the `doxybook2` executable, or `None` to
           skip Markdown conversion (XML only).
    @return `True` on success, `False` if either tool exited non-zero.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        xml_out = tmp_path / "xml"
        doxyfile = tmp_path / "Doxyfile"
        _write_doxyfile(doxygen_bin, doxyfile, xml_out)
        r = subprocess.run([doxygen_bin, str(doxyfile)], cwd=REPO_ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[-] doxygen failed:\n{r.stderr}")
            return False
        if not doxybook2_bin:
            print("[!] doxygen ran (XML only) -- install doxybook2 to also get docs/api/*.md; "
                  "falling back to the built-in Markdown extractor for now.")
            return False
        API_DOCS_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([doxybook2_bin, "--input", str(xml_out), "--output", str(API_DOCS_DIR)],
                            cwd=REPO_ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[-] doxybook2 failed:\n{r.stderr}")
            return False
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skeletons-only", action="store_true", help="Only insert missing Doxygen skeletons.")
    parser.add_argument("--api-only", action="store_true", help="Only (re)generate docs/api/*.md.")
    parser.add_argument("--check", action="store_true",
                         help="Report missing docstrings and exit 1 if any -- writes nothing.")
    args = parser.parse_args()

    do_skeletons = not args.api_only
    do_api = not args.skeletons_only

    if args.check:
        total_missing = 0
        for py_file in iter_py_files():
            missing = find_missing_docstrings(py_file)
            if missing:
                total_missing += len(missing)
                print(f"{py_file.relative_to(REPO_ROOT)}: {len(missing)} missing docstring(s)")
        if total_missing:
            print(f"\n{total_missing} symbol(s) missing a docstring.")
            sys.exit(1)
        print("All symbols documented.")
        return

    if do_skeletons:
        total = 0
        for py_file in iter_py_files():
            n = insert_missing_skeletons(py_file)
            if n:
                print(f"[+] {py_file.relative_to(REPO_ROOT)}: inserted {n} skeleton(s)")
            total += n
        print(f"Skeletons inserted: {total}")

    if do_api:
        doxygen_bin = find_doxygen()
        doxybook2_bin = find_doxybook2()
        used_real_tool = False
        if doxygen_bin:
            print(f"[*] doxygen found ({doxygen_bin}) -- using it.")
            used_real_tool = generate_api_docs_with_doxygen(doxygen_bin, doxybook2_bin)
        else:
            print("[*] doxygen not installed (brew install doxygen for richer output) -- "
                  "using the built-in fallback extractor.")
        if not used_real_tool:
            count = generate_api_docs_fallback()
            print(f"docs/api/*.md written for {count} module(s).")


if __name__ == "__main__":
    main()
