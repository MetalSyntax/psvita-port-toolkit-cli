#!/usr/bin/env python3
"""!
@file gen_docs.py
@brief Maintainer tool and project documentation manager.
@details
Automates two key documentation tasks:
  1. Python toolkit documentation (skeletons and docs/api/<module>.md reference).
  2. Codebase design comment extraction & Doxygen transformation:
     Extracts block comments (e.g. `// ...` or `/* ... */`) that contain architectural/design
     explanations into standalone markdown files (`docs/<module>.md`), and replaces them in the
     source code with concise Doxygen blocks (`/** @brief ... @note Ver docs/... */` or `\"\"\"! ... \"\"\"`)
     linked to the nearest function, structure, or symbol.

Usage:
    python3 dev-tools/gen_docs.py                                # both steps for toolkit
    python3 dev-tools/gen_docs.py --skeletons-only
    python3 dev-tools/gen_docs.py --api-only
    python3 dev-tools/gen_docs.py --check
    python3 dev-tools/gen_docs.py --extract-comments <file/dir>  # extract & convert comments
"""

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
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
    @brief Check whether a function body contains a `return <value>`.
    @param node `ast.FunctionDef`/`ast.AsyncFunctionDef` to inspect.
    @return `True` if a `return` with a value is found in this function's own body.
    """
    return any(isinstance(n, ast.Return) and n.value is not None for n in _walk_own_body(node))


def _param_names(node):
    """!
    @brief Extract parameter names from a function's signature, skipping `self`/`cls`.
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
    @return List of `(node_or_None_for_module, kind)` tuples needing a skeleton.
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
    insertions = []
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
# Step 2: Markdown API reference generation
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"^@(brief|details|param|return|returns|note|warning)\b\s*(.*)$")


def _parse_doxygen_docstring(doc):
    info = {
        "brief": "",
        "details": [],
        "params": [],
        "returns": None,
        "notes": [],
        "warnings": [],
    }
    if not doc:
        return info

    current_tag = "details"
    for raw_line in doc.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _TAG_RE.match(line)
        if m:
            tag, rest = m.group(1), m.group(2).strip()
            if tag == "brief":
                info["brief"] = rest
                current_tag = "brief"
            elif tag == "details":
                if rest:
                    info["details"].append(rest)
                current_tag = "details"
            elif tag == "param":
                parts = rest.split(None, 1)
                name = parts[0] if parts else ""
                desc = parts[1] if len(parts) > 1 else ""
                info["params"].append((name, desc))
                current_tag = "param"
            elif tag in ("return", "returns"):
                info["returns"] = rest
                current_tag = "return"
            elif tag == "note":
                info["notes"].append(rest)
                current_tag = "note"
            elif tag == "warning":
                info["warnings"].append(rest)
                current_tag = "warning"
        else:
            if current_tag == "brief" and not info["brief"]:
                info["brief"] = line
            elif current_tag == "details":
                info["details"].append(line)
            elif current_tag == "note" and info["notes"]:
                info["notes"][-1] += f" {line}"
            elif current_tag == "warning" and info["warnings"]:
                info["warnings"][-1] += f" {line}"
            elif current_tag == "return" and info["returns"]:
                info["returns"] += f" {line}"
            elif current_tag == "param" and info["params"]:
                name, desc = info["params"][-1]
                info["params"][-1] = (name, f"{desc} {line}".strip())
            else:
                info["details"].append(line)

    return info


def _format_symbol_section(symbol_name, node, docstring):
    info = _parse_doxygen_docstring(docstring)
    lines = [f"### `{symbol_name}`\n"]
    if info["brief"]:
        lines.append(f"{info['brief']}\n")
    if info["details"]:
        lines.append(f"\n{' '.join(info['details'])}\n")
    if info["warnings"]:
        for w in info["warnings"]:
            lines.append(f"\n> **Warning:** {w}\n")
    if info["notes"]:
        for n in info["notes"]:
            lines.append(f"\n> **Note:** {n}\n")
    if info["params"]:
        lines.append("\n**Parameters:**\n")
        for name, desc in info["params"]:
            lines.append(f"- `{name}`: {desc}\n")
    if info["returns"]:
        lines.append(f"\n**Returns:** {info['returns']}\n")
    lines.append("\n---\n\n")
    return "".join(lines)


def _signature(node):
    return f"{node.name}({', '.join(_param_names(node))})"


def build_api_markdown(py_file):
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
    API_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for py_file in iter_py_files():
        md = build_api_markdown(py_file)
        out_path = API_DOCS_DIR / f"{py_file.stem}.md"
        out_path.write_text(md, encoding="utf-8")
        count += 1
    return count


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
    subprocess.run([doxygen_bin, "-g", str(doxyfile_path)], cwd=REPO_ROOT,
                    capture_output=True, text=True, check=True)
    text = doxyfile_path.read_text(encoding="utf-8")
    overrides = dict(_DOXYFILE_OVERRIDES,
                     XML_OUTPUT=str(xml_out_dir.name),
                     OUTPUT_DIRECTORY=str(xml_out_dir.parent))
    for key, value in overrides.items():
        pattern = re.compile(rf"^{key}\s*=.*$", re.MULTILINE)
        replacement = f"{key} = {value}"
        if pattern.search(text):
            text = pattern.sub(replacement, text)
        else:
            text += f"\n{replacement}\n"
    doxyfile_path.write_text(text, encoding="utf-8")


def generate_api_docs_with_doxygen(doxygen_bin, doxybook2_bin):
    if not doxybook2_bin:
        print("[!] doxygen está instalado pero falta doxybook2 (necesario para convertir XML a Markdown).")
        print("[!] Usando el extractor Markdown integrado (fallback).")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()
        xml_out = tmp_path / "xml"
        doxyfile = tmp_path / "Doxyfile"
        _write_doxyfile(doxygen_bin, doxyfile, xml_out)
        r = subprocess.run([doxygen_bin, str(doxyfile)], cwd=REPO_ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[-] doxygen falló:\n{r.stderr}")
            return False
        API_DOCS_DIR.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([doxybook2_bin, "--input", str(xml_out), "--output", str(API_DOCS_DIR)],
                            cwd=REPO_ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[-] doxybook2 falló:\n{r.stderr}")
            return False
        return True


# ---------------------------------------------------------------------------
# Step 3: Architecture / Design Comments Extraction & Doxygen Transformation
# ---------------------------------------------------------------------------

_TRANSLATION_CACHE = {}

def _translate_to_english(text):
    """!
    @brief Translate comment/text to English using deep-translator if available.
    @param text String to translate.
    @return Translated string (or original string if translator is unavailable or translation fails).
    """
    if not text or not text.strip():
        return text
    clean_text = text.strip()
    if clean_text in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[clean_text]

    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="auto", target="en")
        # Split very long text if needed, else translate directly
        if len(clean_text) < 4500:
            translated = translator.translate(clean_text)
        else:
            parts = [clean_text[i:i+4000] for i in range(0, len(clean_text), 4000)]
            translated = " ".join([translator.translate(p) for p in parts if p.strip()])
        _TRANSLATION_CACHE[clean_text] = translated.strip()
        return _TRANSLATION_CACHE[clean_text]
    except Exception:
        return clean_text


def _find_target_symbol(lines, start_line_idx):
    """!
    @brief Scan lines starting after a comment block to find the nearest function/symbol definition.
    @return tuple: (symbol_name, symbol_kind, signature_or_decl, line_idx)
    """
    func_pattern = re.compile(r'^\s*(?:(?:static|inline|extern|const|unsigned|void|int|char|float|double|uint\d+_t|int\d+_t|size_t|GLuint|Sce\w+)\s+)+[*]*\s*([a-zA-Z_]\w*)\s*\(([^)]*)\)')
    struct_pattern = re.compile(r'^\s*(?:typedef\s+)?struct\s*(?:[a-zA-Z_]\w*)?\s*\{?')
    define_pattern = re.compile(r'^\s*#define\s+([a-zA-Z_]\w*)')
    var_or_member_pattern = re.compile(r'^\s*(?:(?:static|const|unsigned|void|int|char|float|double|uint\d+_t|int\d+_t|size_t|GLuint|Sce\w+|int16_t|int32_t|int64_t|uint16_t|uint32_t|uint64_t|OggVorbis_File|voice_t)\s+)+[*]*\s*([a-zA-Z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:[=;,])')

    for i in range(start_line_idx, min(len(lines), start_line_idx + 12)):
        line = lines[i].strip()
        if not line:
            continue
        if line.startswith("#ifndef") or line.startswith("#define _") or line.startswith("#include"):
            continue

        m_func = func_pattern.match(line)
        if m_func:
            return (m_func.group(1), "function", line, i)

        m_def = define_pattern.match(line)
        if m_def:
            return (m_def.group(1), "define", line, i)

        if struct_pattern.match(line):
            return ("struct", "struct", line, i)

        m_var = var_or_member_pattern.match(line)
        if m_var:
            return (m_var.group(1), "variable", line, i)

    return (None, "general", "", start_line_idx)


def _clean_summary(text, max_len=140):
    """!
    @brief Extract a clean, coherent first sentence or complete summary from comment text in English.
    @param text Raw extracted comment text.
    @param max_len Maximum length for the summary line.
    @return Cleaned single-line summary translated to English.
    """
    cleaned = re.sub(r"^[#/*\-\s]+", "", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "Design note"

    # Translate the summary text to English
    translated = _translate_to_english(cleaned)

    # 1. Try to find the first complete sentence ending in ., !, or ?
    match = re.search(r"^([^.!?\n]+[.!?])", translated)
    if match:
        sentence = match.group(1).strip()
        # Ensure it has balanced parentheses
        if sentence.count("(") == sentence.count(")") and len(sentence) <= max_len and len(sentence) > 15:
            return sentence.rstrip(".!?")

    # 2. Try to cut at natural clauses (--, :, ;) if available before max_len
    for sep in (" -- ", " - ", ": ", "; "):
        if sep in translated:
            part = translated.split(sep, 1)[0].strip()
            if 15 <= len(part) <= max_len and part.count("(") == part.count(")"):
                return part.rstrip(".!?:")

    # 3. If too long, cut at word boundary without leaving unclosed parentheses or dangling commas
    if len(translated) > max_len:
        cut = translated[:max_len]
        last_space = cut.rfind(" ")
        if last_space > 30:
            result = cut[:last_space].strip()
        else:
            result = cut.strip()
    else:
        result = translated.strip()

    # Clean up trailing punctuation, unclosed parentheses, or incomplete words
    result = re.sub(r"[,:;\-\(\[\{]+$", "", result).strip()
    if result.count("(") > result.count(")"):
        result += ")"
    if result.count("[") > result.count("]"):
        result += "]"

    return result.rstrip(".!?")


def _generate_doxygen_c(symbol_name, symbol_kind, comment_clean, doc_relpath, line_num=None, indent=""):
    """!
    @brief Build a C/C++ Doxygen comment from the extracted comment and target symbol in English.
    """
    summary = _clean_summary(comment_clean)
    line_ref = f":{line_num}" if line_num else ""

    # For struct fields or inline variables, generate single line Doxygen or clean concise block
    if symbol_kind == "variable" and len(summary) <= 80:
        return f"{indent}/**< @brief {summary}. */"

    lines = [f"{indent}/**"]
    lines.append(f"{indent} * @brief {summary}.")
    if doc_relpath:
        lines.append(f"{indent} * @note See `{doc_relpath}{line_ref}` for detailed design rationale.")
    lines.append(f"{indent} */")
    return "\n".join(lines)


def _generate_doxygen_python_or_hash(symbol_name, symbol_kind, comment_clean, doc_relpath, line_num=None, is_python=True, indent=""):
    """!
    @brief Build Python docstring (\"\"\"! ... \"\"\") or hash-based Doxygen block in English.
    """
    summary = _clean_summary(comment_clean)
    line_ref = f":{line_num}" if line_num else ""

    if is_python:
        lines = [f'{indent}"""!']
        lines.append(f"{indent}@brief {summary}.")
        if doc_relpath:
            lines.append(f"{indent}@note See `{doc_relpath}{line_ref}` for detailed design rationale.")
        lines.append(f'{indent}"""')
    else:
        lines = [
            f"{indent}## @brief {summary}.",
        ]
        if doc_relpath:
            lines.append(f"{indent}## @note See `{doc_relpath}{line_ref}` for detailed design rationale.")
    return "\n".join(lines)


def _load_existing_doc_sections(doc_md_abs, repo_root=None, rel_path=None):
    """!
    @brief If docs already exist (in root docs/, docs/loader/es, docs/loader/en, etc.),
           load section quotes so we can restore full context and translate properly.
    """
    candidate_paths = [doc_md_abs]
    if repo_root and rel_path:
        # Search common doc structures
        stem = rel_path.stem
        candidate_paths.extend([
            repo_root / "docs" / "loader" / "en" / f"{stem}.en.md",
            repo_root / "docs" / "loader" / "es" / f"{stem}.md",
            repo_root / "docs" / "loader" / f"{stem}.md",
            repo_root / "docs" / f"{stem}.md",
            repo_root / "docs" / rel_path.with_suffix(".md"),
        ])

    sections = {}
    pattern = re.compile(r"##\s+(.+?)(?:\s+\((?:l[ií]nea|line)\s+~(\d+)\))?[\r\n]+([\s\S]*?)(?=\n##|\Z)", re.IGNORECASE)

    for path in candidate_paths:
        if not path or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            idx = 1
            for m in pattern.finditer(text):
                title = m.group(1).strip()
                line_str = m.group(2)
                line_no = int(line_str) if line_str else (idx * 20)
                body = m.group(3)
                quote_lines = []
                for l in body.splitlines():
                    if l.startswith(">"):
                        quote_lines.append(l[1:].strip())
                if quote_lines:
                    quote_text = "\n".join(quote_lines).strip()
                    sections[line_no] = quote_text
                    clean_title = title.replace("`", "").strip()
                    sections[clean_title] = quote_text
                idx += 1
            if sections:
                break
        except Exception:
            continue
    return sections


def process_comments_in_file(file_path, repo_root=None, dry_run=False):
    """!
    @brief Extract architecture/design comments from C/C++/Python/Text source files,
           save them to docs/<relpath_without_ext>.md, and replace in-source with Doxygen blocks.
    @param file_path File Path to process.
    @param repo_root Base repository root for computing relative paths and docs/ output.
    @param dry_run If True, don't modify files on disk.
    @return Number of extracted sections.
    """
    if repo_root is None:
        repo_root = file_path.parent
        curr = file_path.parent
        while curr != curr.parent:
            if (curr / ".git").exists() or (curr / ".psvita-toolkit.json").exists() or (curr / "CMakeLists.txt").exists():
                repo_root = curr
                break
            curr = curr.parent

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[-] Error reading {file_path}: {e}")
        return 0

    ext = file_path.suffix.lower()
    is_c_cpp = ext in (".c", ".h", ".cpp", ".hpp", ".cc", ".cxx")
    is_py = ext == ".py"
    is_txt = ext in (".txt", ".cmake", ".sh", ".bash")

    lines = content.splitlines(keepends=True)
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        rel = Path(file_path.name)

    doc_md_rel = Path("docs") / rel.with_suffix(".md")
    doc_md_abs = repo_root / doc_md_rel
    existing_docs = _load_existing_doc_sections(doc_md_abs, repo_root=repo_root, rel_path=rel)

    extracted_sections = []
    i = 0
    modifications = []  # (start_idx, end_idx, replacement_text)

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 1. Existing or new C-style block comments /* ... */ (C/C++)
        if is_c_cpp and (stripped.startswith("/*") or stripped.startswith("/**")):
            start_idx = i
            comment_lines = []
            while i < len(lines):
                c_line = lines[i].strip()
                comment_lines.append(c_line)
                i += 1
                if "*/" in c_line:
                    break
            end_idx = i

            raw_text = "\n".join(comment_lines)
            cleaned = re.sub(r"^/\*+|\*+/$", "", raw_text).strip()
            cleaned_lines = [re.sub(r"^\s*\*+\s?", "", l) for l in cleaned.splitlines()]
            cleaned_text = "\n".join(cleaned_lines).strip()

            # Check if this was a previous Doxygen block that refers to docs/
            is_prev_dox = ("@note Ver docs/" in raw_text or "@note See docs/" in raw_text or "@note See `" in raw_text or "@brief" in raw_text)
            orig_text = None
            if is_prev_dox:
                symbol_name, symbol_kind, sig, target_idx = _find_target_symbol(lines, end_idx)
                if symbol_name and symbol_name in existing_docs:
                    orig_text = existing_docs[symbol_name]
                else:
                    for l_no in sorted([k for k in existing_docs.keys() if isinstance(k, int)]):
                        if abs(l_no - (start_idx + 1)) <= 25:
                            orig_text = existing_docs[l_no]
                            break
                    if not orig_text:
                        # Extract brief or text from existing doxygen block to translate it
                        brief_m = re.search(r"@brief\s+(.+?)(?:\*\/|\@|\n|$)", raw_text, re.DOTALL)
                        if brief_m:
                            orig_text = brief_m.group(1).replace("*", "").strip()

            target_text = orig_text if orig_text else cleaned_text

            if len(target_text) > 10 and (not target_text.startswith("@") or orig_text):
                indent_match = re.match(r"^(\s*)", lines[start_idx])
                indent = indent_match.group(1) if indent_match else ""
                symbol_name, symbol_kind, sig, target_idx = _find_target_symbol(lines, end_idx)
                # If it was already a Doxygen block, don't link/generate .md unless new design notes exist
                ref_doc = str(doc_md_rel) if not is_prev_dox else None
                dox = _generate_doxygen_c(symbol_name, symbol_kind, target_text, ref_doc, line_num=start_idx + 1, indent=indent)
                modifications.append((start_idx, end_idx, dox + "\n"))

                if not is_prev_dox:
                    title = f"`{symbol_name}`" if symbol_name else f"`{file_path.name}` (line ~{start_idx + 1})"
                    sec_md = f"## {title} (line ~{start_idx + 1})\n\n"
                    sec_md += f"**Source File:** `{rel}`\n\n"
                    for l in (orig_text.splitlines() if orig_text else cleaned_lines):
                        sec_md += f"> {l}\n" if l.strip() else ">\n"
                    sec_md += "\n---\n"
                    extracted_sections.append(sec_md)

            i = end_idx

        # 2. Double-slash // comments (C/C++)
        elif is_c_cpp and stripped.startswith("//") and not stripped.startswith("///"):
            start_idx = i
            comment_lines = []
            while i < len(lines) and lines[i].strip().startswith("//"):
                comment_lines.append(lines[i].strip()[2:].strip())
                i += 1
            end_idx = i

            cleaned_text = "\n".join(comment_lines).strip()
            if len(cleaned_text) > 30 and not cleaned_text.startswith("@"):
                indent_match = re.match(r"^(\s*)", lines[start_idx])
                indent = indent_match.group(1) if indent_match else ""
                symbol_name, symbol_kind, sig, target_idx = _find_target_symbol(lines, end_idx)
                dox = _generate_doxygen_c(symbol_name, symbol_kind, cleaned_text, str(doc_md_rel), line_num=start_idx + 1, indent=indent)
                modifications.append((start_idx, end_idx, dox + "\n"))

                title = f"`{symbol_name}`" if symbol_name else f"`{file_path.name}` (line ~{start_idx + 1})"
                sec_md = f"## {title} (line ~{start_idx + 1})\n\n"
                sec_md += f"**Source File:** `{rel}`\n\n"
                for l in comment_lines:
                    sec_md += f"> {l}\n" if l.strip() else ">\n"
                sec_md += "\n---\n"
                extracted_sections.append(sec_md)

            i = end_idx

        # 3. Hash # comments (.py, .txt, .cmake, .sh)
        elif (is_py or is_txt) and stripped.startswith("#") and not stripped.startswith("#!") and not stripped.startswith("#pragma"):
            start_idx = i
            comment_lines = []
            is_prev_dox = False
            while i < len(lines) and lines[i].strip().startswith("#"):
                l_strip = lines[i].strip()
                if l_strip.startswith("#!"):
                    break
                if "## @note See" in l_strip or "## @note Ver" in l_strip or "## @brief" in l_strip:
                    is_prev_dox = True
                c_content = re.sub(r"^#+\s?", "", l_strip).strip()
                comment_lines.append(c_content)
                i += 1
            end_idx = i

            cleaned_text = "\n".join(comment_lines).strip()
            orig_text = None
            if is_prev_dox:
                symbol_name, symbol_kind, sig, target_idx = _find_target_symbol(lines, end_idx)
                if symbol_name and symbol_name in existing_docs:
                    orig_text = existing_docs[symbol_name]
                else:
                    for l_no in sorted([k for k in existing_docs.keys() if isinstance(k, int)]):
                        if abs(l_no - (start_idx + 1)) <= 25:
                            orig_text = existing_docs[l_no]
                            break
                    if not orig_text:
                        brief_m = re.search(r"@brief\s+(.+?)(?:@|\n|$)", cleaned_text, re.DOTALL)
                        if brief_m:
                            orig_text = brief_m.group(1).replace("#", "").strip()

            target_text = orig_text if orig_text else cleaned_text

            if len(target_text) > 10 and (not target_text.startswith("@") or orig_text):
                indent_match = re.match(r"^(\s*)", lines[start_idx])
                indent = indent_match.group(1) if indent_match else ""
                symbol_name, symbol_kind, sig, target_idx = _find_target_symbol(lines, end_idx)
                ref_doc = str(doc_md_rel) if not is_prev_dox else None
                dox = _generate_doxygen_python_or_hash(symbol_name, symbol_kind, target_text, ref_doc, line_num=start_idx + 1, is_python=is_py, indent=indent)
                modifications.append((start_idx, end_idx, dox + "\n"))

                if not is_prev_dox:
                    title = f"`{symbol_name}`" if symbol_name else f"`{file_path.name}` (line ~{start_idx + 1})"
                    sec_md = f"## {title} (line ~{start_idx + 1})\n\n"
                    sec_md += f"**Source File:** `{rel}`\n\n"
                    for l in (orig_text.splitlines() if orig_text else comment_lines):
                        sec_md += f"> {l}\n" if l.strip() else ">\n"
                    sec_md += "\n---\n"
                    extracted_sections.append(sec_md)

            i = end_idx
        else:
            i += 1

    if not modifications:
        return 0

    if dry_run:
        print(f"\n\033[93m[DRY-RUN] Simulación para: {rel} ({len(modifications)} modificación/es)\033[0m")
        if extracted_sections:
            print(f"  \033[96m-> Crearía/actualizaría archivo de diseño: {doc_md_rel} ({len(extracted_sections)} sección/es)\033[0m")
        for s_idx, e_idx, repl in modifications:
            print(f"  \033[90mLine ~{s_idx + 1}:\033[0m")
            for rline in repl.strip().splitlines():
                print(f"    \033[92m+ {rline}\033[0m")
    else:
        if extracted_sections:
            doc_md_abs.parent.mkdir(parents=True, exist_ok=True)
            md_header = f"# `{rel}` — Design Architecture & Notes\n\n"
            md_header += "Explanatory and architectural design notes extracted from source code and replaced with concise technical Doxygen blocks. This document preserves the reasoning ('why') separated from technical API documentation.\n\n"
            full_md = md_header + "\n".join(extracted_sections)
            doc_md_abs.write_text(full_md, encoding="utf-8")
            print(f"[+] Design documentation saved in: {doc_md_rel}")

        for s_idx, e_idx, repl in sorted(modifications, key=lambda x: x[0], reverse=True):
            lines[s_idx:e_idx] = [repl]

        file_path.write_text("".join(lines), encoding="utf-8")
        print(f"[+] Updated original source file with Doxygen: {rel}")

    return len(modifications)


# Backward compatibility alias
process_c_comments_in_file = process_comments_in_file


def extract_comments_command(target_path, repo_root=None, dry_run=False):
    target = Path(target_path).resolve()
    valid_exts = {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".py", ".txt"}

    if target.is_file():
        files = [target]
    else:
        files = []
        for p in target.rglob("*"):
            if p.is_file() and p.suffix.lower() in valid_exts:
                files.append(p)
        files = sorted(files)

    ignored_dirs = {"build", ".git", "__pycache__", "decompiled_so", "decompiled", ".claude", "logs"}
    total = 0
    if dry_run:
        print("\n\033[93m" + "=" * 70)
        print("  MODO SIMULACIÓN (DRY-RUN) ACTIVO — No se modificará ningún archivo")
        print("=" * 70 + "\033[0m")

    for f in files:
        if f.name.startswith("._") or any(part in ignored_dirs for part in f.parts):
            continue
        n = process_comments_in_file(f, repo_root=repo_root, dry_run=dry_run)
        total += n

    if dry_run:
        print(f"\n\033[93m[DRY-RUN] Simulación completa. Total de bloques que serían modificados: {total}\033[0m")
    else:
        print(f"\n[+] Total de bloques procesados: {total}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skeletons-only", action="store_true", help="Only insert missing Doxygen skeletons.")
    parser.add_argument("--api-only", action="store_true", help="Only (re)generate docs/api/*.md.")
    parser.add_argument("--check", action="store_true",
                         help="Report missing docstrings and exit 1 if any -- writes nothing.")
    parser.add_argument("--extract-comments", metavar="PATH",
                         help="Extract explanatory comments from source files to docs/*.md and convert to Doxygen.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes to disk.")
    args = parser.parse_args()

    if args.extract_comments:
        extract_comments_command(args.extract_comments, dry_run=args.dry_run)
        return

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
            n = insert_missing_skeletons(py_file, dry_run=args.dry_run)
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


def run_docs_menu():
    """!
    @brief Interactive documentation workflow from the TUI: inspect missing
           docstrings, insert skeletons, and generate API reference markdown.
    """
    print("[*] Verificando docstrings en el código del toolkit...")
    total_missing = 0
    missing_by_file = {}
    for py_file in iter_py_files():
        missing = find_missing_docstrings(py_file)
        if missing:
            total_missing += len(missing)
            missing_by_file[py_file] = missing
            print(f"  - {py_file.name}: {len(missing)} símbolo(s) sin docstring")

    if not total_missing:
        print("\n[+] ¡Todos los símbolos están documentados con Doxygen!")
    else:
        print(f"\n[!] Total: {total_missing} símbolo(s) sin docstring.")

    doxygen_bin = find_doxygen()
    doxybook2_bin = find_doxybook2()
    print("\n[*] Generando referencia API en docs/api/...")
    used_real_tool = False
    if doxygen_bin:
        print(f"[*] doxygen encontrado ({doxygen_bin}) -- usando herramienta externa.")
        used_real_tool = generate_api_docs_with_doxygen(doxygen_bin, doxybook2_bin)
    else:
        print("[*] doxygen no instalado -- usando extractor fallback de AST.")

    if not used_real_tool:
        count = generate_api_docs_fallback()
        print(f"[+] docs/api/*.md generado para {count} módulo(s).")


if __name__ == "__main__":
    main()
