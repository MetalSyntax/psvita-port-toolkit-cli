"""!
@file utils.py
@brief Misc utilities: macOS junk cleanup, re-decompilation, running project
       tests, shader/doc translation, generic symbol search in the port's
       `.so` files.

See `docs/dev-notes/utils.md` for why `search_symbols()` replaces the old
`ai_bash_commands.sh` cheatsheet, and why `translate_shaders_boilerplate()`
is boilerplate cleanup rather than a real GLSL->Cg translator.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from . import i18n
from .i18n import t

from .tui import C

STRINGS = {
    "utils.macos_junk_removed": {
        "es": "{color_green}[+] {removed} archivo(s) '._*' eliminado(s).{color_reset}",
        "en": "{color_green}[+] {removed} '._*' file(s) removed.{color_reset}",
        "pt": "{color_green}[+] {removed} arquivo(s) '._*' removido(s).{color_reset}",
    },
    "utils.decompile_jadx_step": {
        "es": "[1/2] Decompilación Java (jadx)...",
        "en": "[1/2] Java decompilation (jadx)...",
        "pt": "[1/2] Decompilação Java (jadx)...",
    },
    "utils.decompile_jadx_done": {
        "es": "{color_green}[+] JADX terminado. Resultados en {apk_out_dir}{color_reset}",
        "en": "{color_green}[+] JADX finished. Results in {apk_out_dir}{color_reset}",
        "pt": "{color_green}[+] JADX concluído. Resultados em {apk_out_dir}{color_reset}",
    },
    "utils.decompile_apk_not_found": {
        "es": "{color_yellow}[!] No se encontró el .apk original en {project_dir} -- se omite jadx.{color_reset}",
        "en": "{color_yellow}[!] Original .apk not found in {project_dir} -- skipping jadx.{color_reset}",
        "pt": "{color_yellow}[!] Não foi encontrado o .apk original em {project_dir} -- pulando jadx.{color_reset}",
    },
    "utils.decompile_jadx_not_installed": {
        "es": "{color_yellow}[!] jadx no está instalado (brew install jadx) -- se omite.{color_reset}",
        "en": "{color_yellow}[!] jadx isn't installed (brew install jadx) -- skipping.{color_reset}",
        "pt": "{color_yellow}[!] jadx não está instalado (brew install jadx) -- pulando.{color_reset}",
    },
    "utils.decompile_so_step": {
        "es": "\n[2/2] Decompilación de .so (Ghidra vía devrvk/so-decompiler)...",
        "en": "\n[2/2] .so decompilation (Ghidra via devrvk/so-decompiler)...",
        "pt": "\n[2/2] Decompilação de .so (Ghidra via devrvk/so-decompiler)...",
    },
    "utils.decompile_lib_dir_missing": {
        "es": "{color_yellow}[!] No se encontró {lib_dir} -- ¿ya extrajiste el APK? (se hace al crear el port){color_reset}",
        "en": "{color_yellow}[!] {lib_dir} not found -- did you extract the APK already? (this happens when creating the port){color_reset}",
        "pt": "{color_yellow}[!] {lib_dir} não encontrado -- você já extraiu o APK? (isso acontece ao criar o port){color_reset}",
    },
    "utils.decompile_docker_not_installed": {
        "es": "{color_yellow}[!] docker no está instalado -- se omite.{color_reset}",
        "en": "{color_yellow}[!] docker isn't installed -- skipping.{color_reset}",
        "pt": "{color_yellow}[!] docker não está instalado -- pulando.{color_reset}",
    },
    "utils.decompile_so_running": {
        "es": "[*] Decompilando {name} ({abi})...",
        "en": "[*] Decompiling {name} ({abi})...",
        "pt": "[*] Decompilando {name} ({abi})...",
    },
    "utils.decompile_so_done": {
        "es": "[+] Listo: {path}",
        "en": "[+] Done: {path}",
        "pt": "[+] Concluído: {path}",
    },
    "utils.decompile_so_failed": {
        "es": "[!] Falló {name}",
        "en": "[!] {name} failed",
        "pt": "[!] Falhou {name}",
    },
    "utils.tests_no_script": {
        "es": "{color_yellow}[-] Este proyecto no tiene tests/run_tests.sh.{color_reset}",
        "en": "{color_yellow}[-] This project doesn't have tests/run_tests.sh.{color_reset}",
        "pt": "{color_yellow}[-] Este projeto não tem tests/run_tests.sh.{color_reset}",
    },
    "utils.tests_no_script_hint": {
        "es": "{color_dim}    Es opcional y específico de cada port -- crealo si necesitás un test suite de host.{color_reset}",
        "en": "{color_dim}    It's optional and specific to each port -- create it if you need a host test suite.{color_reset}",
        "pt": "{color_dim}    É opcional e específico de cada port -- crie-o se precisar de uma suíte de testes no host.{color_reset}",
    },
    "utils.shaders_no_dump_dir": {
        "es": "{color_yellow}[-] No existe {dump_dir} -- descargá los shaders volcados primero.{color_reset}",
        "en": "{color_yellow}[-] {dump_dir} doesn't exist -- download the dumped shaders first.{color_reset}",
        "pt": "{color_yellow}[-] {dump_dir} não existe -- baixe os shaders extraídos primeiro.{color_reset}",
    },
    "utils.shaders_converted": {
        "es": "  {src} -> {dst}",
        "en": "  {src} -> {dst}",
        "pt": "  {src} -> {dst}",
    },
    "utils.shaders_cleaned_count": {
        "es": "{color_green}[+] {count} shader(s) con boilerplate limpio en {out_dir}.{color_reset}",
        "en": "{color_green}[+] {count} shader(s) with boilerplate cleaned in {out_dir}.{color_reset}",
        "pt": "{color_green}[+] {count} shader(s) com boilerplate limpo em {out_dir}.{color_reset}",
    },
    "utils.shaders_manual_review_warning": {
        "es": "{color_yellow}[!] Revisar/reescribir a mano cada uno -- esto NO es una traducción GLSL->Cg real.{color_reset}",
        "en": "{color_yellow}[!] Review/rewrite each one by hand -- this is NOT a real GLSL->Cg translation.{color_reset}",
        "pt": "{color_yellow}[!] Revise/reescreva cada um manualmente -- isso NÃO é uma tradução GLSL->Cg real.{color_reset}",
    },
    "utils.shaders_no_glsl_found": {
        "es": "{color_yellow}[-] No había .glsl en {dump_dir}.{color_reset}",
        "en": "{color_yellow}[-] There were no .glsl files in {dump_dir}.{color_reset}",
        "pt": "{color_yellow}[-] Não havia .glsl em {dump_dir}.{color_reset}",
    },
    "utils.docs_missing_deep_translator": {
        "es": "{color_red}[-] Falta 'deep-translator' -- instalar con: pip install deep-translator{color_reset}",
        "en": "{color_red}[-] 'deep-translator' is missing -- install it with: pip install deep-translator{color_reset}",
        "pt": "{color_red}[-] Falta o 'deep-translator' -- instale com: pip install deep-translator{color_reset}",
    },
    "utils.docs_no_md_files": {
        "es": "{color_yellow}[-] No hay archivos .md en la raíz de {project_dir}.{color_reset}",
        "en": "{color_yellow}[-] There are no .md files in the root of {project_dir}.{color_reset}",
        "pt": "{color_yellow}[-] Não há arquivos .md na raiz de {project_dir}.{color_reset}",
    },
    "utils.docs_translate_error": {
        "es": "{color_red}[-] Error traduciendo {name}: {error}{color_reset}",
        "en": "{color_red}[-] Error translating {name}: {error}{color_reset}",
        "pt": "{color_red}[-] Erro ao traduzir {name}: {error}{color_reset}",
    },
    "utils.docs_translated": {
        "es": "{color_green}[+] {src} -> {dst}{color_reset}",
        "en": "{color_green}[+] {src} -> {dst}{color_reset}",
        "pt": "{color_green}[+] {src} -> {dst}{color_reset}",
    },
    "utils.symbols_no_so_found": {
        "es": "{color_red}[-] No se encontró ningún .so (ni en <slug>_extract/lib/ ni en lib/).{color_reset}",
        "en": "{color_red}[-] No .so found (neither in <slug>_extract/lib/ nor in lib/).{color_reset}",
        "pt": "{color_red}[-] Nenhum .so encontrado (nem em <slug>_extract/lib/ nem em lib/).{color_reset}",
    },
    "utils.symbols_so_header": {
        "es": "\n{color_bold}--- {so} ---{color_reset}",
        "en": "\n{color_bold}--- {so} ---{color_reset}",
        "pt": "\n{color_bold}--- {so} ---{color_reset}",
    },
    "utils.symbols_readelf_failed_fallback": {
        "es": "readelf falló (¿VITASDK en PATH?)",
        "en": "readelf failed (is VITASDK in PATH?)",
        "pt": "readelf falhou (o VITASDK está no PATH?)",
    },
    "utils.symbols_error_line": {
        "es": "{color_red}[-] {msg}{color_reset}",
        "en": "{color_red}[-] {msg}{color_reset}",
        "pt": "{color_red}[-] {msg}{color_reset}",
    },
    "utils.symbols_no_matches": {
        "es": "  {color_dim}(sin coincidencias para '{pattern}'){color_reset}",
        "en": "  {color_dim}(no matches for '{pattern}'){color_reset}",
        "pt": "  {color_dim}(sem correspondências para '{pattern}'){color_reset}",
    },
    "utils.gen_docs_checking": {
        "es": "[*] Verificando docstrings en módulos del toolkit...",
        "en": "[*] Checking docstrings in toolkit modules...",
        "pt": "[*] Verificando docstrings nos módulos do toolkit...",
    },
    "utils.gen_docs_missing_line": {
        "es": "  {color_yellow}- {name}: {count} símbolo(s) sin docstring{color_reset}",
        "en": "  {color_yellow}- {name}: {count} symbol(s) missing docstrings{color_reset}",
        "pt": "  {color_yellow}- {name}: {count} símbolo(s) sem docstring{color_reset}",
    },
    "utils.gen_docs_all_documented": {
        "es": "{color_green}[+] ¡Todos los símbolos están documentados!{color_reset}",
        "en": "{color_green}[+] All symbols are documented!{color_reset}",
        "pt": "{color_green}[+] Todos os símbolos estão documentados!{color_reset}",
    },
    "utils.gen_docs_total_missing": {
        "es": "\n{color_yellow}[!] Total: {count} símbolo(s) sin docstring.{color_reset}",
        "en": "\n{color_yellow}[!] Total: {count} symbol(s) missing docstrings.{color_reset}",
        "pt": "\n{color_yellow}[!] Total: {count} símbolo(s) sem docstring.{color_reset}",
    },
    "utils.gen_docs_generating_api": {
        "es": "\n[*] Generando referencia API en docs/api/...",
        "en": "\n[*] Generating API reference in docs/api/...",
        "pt": "\n[*] Gerando referência de API em docs/api/...",
    },
    "utils.gen_docs_using_doxygen": {
        "es": "[*] doxygen encontrado ({bin}) -- usando herramienta nativa.",
        "en": "[*] doxygen found ({bin}) -- using native tool.",
        "pt": "[*] doxygen encontrado ({bin}) -- usando ferramenta nativa.",
    },
    "utils.gen_docs_using_fallback": {
        "es": "[*] doxygen no instalado (o sin doxybook2) -- usando extractor AST fallback.",
        "en": "[*] doxygen not installed (or missing doxybook2) -- using fallback AST extractor.",
        "pt": "[*] doxygen não instalado (ou sem doxybook2) -- usando extrator AST de fallback.",
    },
    "utils.gen_docs_done": {
        "es": "{color_green}[+] docs/api/*.md generado para {count} módulo(s).{color_reset}",
        "en": "{color_green}[+] docs/api/*.md generated for {count} module(s).{color_reset}",
        "pt": "{color_green}[+] docs/api/*.md gerado para {count} módulo(s).{color_reset}",
    },
}
i18n.register(STRINGS)


def clean_macos_junk(project_dir):
    removed = 0
    for root, _dirs, files in os.walk(project_dir):
        for f in files:
            if f.startswith("._"):
                try:
                    os.remove(os.path.join(root, f))
                    removed += 1
                except OSError:
                    pass
    print(t("utils.macos_junk_removed", color_green=C.GREEN, removed=removed, color_reset=C.RESET))


def decompile_all(project_cfg, global_cfg):
    """!
    @brief Re-run jadx + `devrvk/so-decompiler` for the active project.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (unused here beyond the shared signature).
    @note Reuses the extraction already present in `<slug>_extract/`, if any.
    """
    project_dir = Path(project_cfg["_project_dir"])
    slug = project_cfg["slug"]
    apk_basename = project_cfg.get("apk_basename", "")

    apk_file = project_dir / apk_basename if apk_basename else None
    if not apk_file or not apk_file.exists():
        candidates = list(project_dir.glob("*.apk"))
        apk_file = candidates[0] if candidates else None

    extract_dir = project_dir / f"{slug}_extract"
    lib_dir = extract_dir / "lib"
    decompiled_dir = project_dir / "decompiled"
    apk_out_dir = decompiled_dir / "apk_jadx"
    apk_out_dir.mkdir(parents=True, exist_ok=True)

    print(t("utils.decompile_jadx_step"))
    if apk_file and shutil.which("jadx"):
        subprocess.run(["jadx", "-d", str(apk_out_dir), str(apk_file)])
        print(t("utils.decompile_jadx_done", color_green=C.GREEN, apk_out_dir=apk_out_dir, color_reset=C.RESET))
    elif not apk_file:
        print(t("utils.decompile_apk_not_found", color_yellow=C.YELLOW, project_dir=project_dir, color_reset=C.RESET))
    else:
        print(t("utils.decompile_jadx_not_installed", color_yellow=C.YELLOW, color_reset=C.RESET))

    print(t("utils.decompile_so_step"))
    if not lib_dir.is_dir():
        print(t("utils.decompile_lib_dir_missing", color_yellow=C.YELLOW, lib_dir=lib_dir, color_reset=C.RESET))
        return
    if not shutil.which("docker"):
        print(t("utils.decompile_docker_not_installed", color_yellow=C.YELLOW, color_reset=C.RESET))
        return

    for so_file in lib_dir.rglob("*.so"):
        abi = so_file.parent.name
        so_out = decompiled_dir / f"{so_file.stem}_{abi}" / "ghidra"
        so_out.mkdir(parents=True, exist_ok=True)
        print(t("utils.decompile_so_running", name=so_file.name, abi=abi))
        r = subprocess.run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{so_file.parent}:/input", "-v", f"{so_out}:/output",
            "devrvk/so-decompiler", "decompile", f"/input/{so_file.name}", "/output",
        ])
        print(t("utils.decompile_so_done", path=so_out) if r.returncode == 0 else t("utils.decompile_so_failed", name=so_file.name))


def run_project_tests(project_cfg):
    """!
    @brief Run the project's own `tests/run_tests.sh`, if it has one.
    @param project_cfg Per-project config dict.
    @note What to test is engine/game-specific and lives in the port's own
          repo, not in this generic toolkit.
    """
    project_dir = Path(project_cfg["_project_dir"])
    script = project_dir / "tests" / "run_tests.sh"
    if not script.exists():
        print(t("utils.tests_no_script", color_yellow=C.YELLOW, color_reset=C.RESET))
        print(t("utils.tests_no_script_hint", color_dim=C.DIM, color_reset=C.RESET))
        return
    subprocess.run(["bash", str(script)], cwd=project_dir)


_GLES_JUNK_RE = [
    (re.compile(r"#define\s+GLITCH_OPENGLES_2\s*"), ""),
    (re.compile(r"\bhighp\b"), ""),
    (re.compile(r"\bmediump\b"), ""),
    (re.compile(r"\blowp\b"), ""),
    (re.compile(r"precision\s+\w+\s+\w+\s*;"), ""),
]


def translate_shaders_boilerplate(project_cfg):
    """!
    @brief Strip GLES boilerplate from dumped shaders (`glsl_dump/*.glsl` ->
           `assets/cg/*.cg`).
    @param project_cfg Per-project config dict.
    @warning This is NOT a full GLSL->Cg translation -- it only strips
             precision qualifiers and Android-specific macros. Every shader
             still needs manual review/rewriting; real shader translation is
             engine-specific.
    """
    project_dir = Path(project_cfg["_project_dir"])
    dump_dir = project_dir / "glsl_dump"
    out_dir = project_dir / "assets" / "cg"

    if not dump_dir.is_dir():
        print(t("utils.shaders_no_dump_dir", color_yellow=C.YELLOW, dump_dir=dump_dir, color_reset=C.RESET))
        return
    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for glsl_file in dump_dir.glob("*.glsl"):
        if glsl_file.name.startswith("._"):
            continue
        code = glsl_file.read_text(errors="ignore")
        for pattern, repl in _GLES_JUNK_RE:
            code = pattern.sub(repl, code)
        out_file = out_dir / glsl_file.with_suffix(".cg").name
        out_file.write_text(code.strip())
        print(t("utils.shaders_converted", src=glsl_file.name, dst=out_file.name))
        count += 1

    if count:
        print(t("utils.shaders_cleaned_count", color_green=C.GREEN, count=count, out_dir=out_dir, color_reset=C.RESET))
        print(t("utils.shaders_manual_review_warning", color_yellow=C.YELLOW, color_reset=C.RESET))
    else:
        print(t("utils.shaders_no_glsl_found", color_yellow=C.YELLOW, dump_dir=dump_dir, color_reset=C.RESET))


def translate_docs(project_cfg, target_lang="en"):
    """!
    @brief Batch-translate the project's own `.md` files with `deep-translator`
           (Google Translate).
    @param project_cfg Per-project config dict.
    @param target_lang Target ISO language code (default `"en"`).
    @note Requires `pip install deep-translator`. This translates the *port's*
          documentation into whatever language the user asks for here --
          unrelated to this toolkit's own UI language (see `i18n.py`).
    """
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print(t("utils.docs_missing_deep_translator", color_red=C.RED, color_reset=C.RESET))
        return

    project_dir = Path(project_cfg["_project_dir"])
    md_files = [p for p in project_dir.glob("*.md") if not p.name.startswith("._")]
    if not md_files:
        print(t("utils.docs_no_md_files", color_yellow=C.YELLOW, project_dir=project_dir, color_reset=C.RESET))
        return

    translator = GoogleTranslator(source="auto", target=target_lang)
    for md in md_files:
        text = md.read_text(errors="ignore")
        try:
            translated = translator.translate(text)
        except Exception as e:
            print(t("utils.docs_translate_error", color_red=C.RED, name=md.name, error=e, color_reset=C.RESET))
            continue
        out = md.with_name(f"{md.stem}.{target_lang}.md")
        out.write_text(translated)
        print(t("utils.docs_translated", color_green=C.GREEN, src=md.name, dst=out.name, color_reset=C.RESET))


def search_symbols(project_cfg, global_cfg, pattern, so_relpath=None):
    """!
    @brief Generic symbol search (`readelf --dyn-syms`) across the project's
           `.so` file(s), filtered by a regex pattern.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (used to locate VITASDK's `bin/`).
    @param pattern Regex pattern to filter matching symbol lines.
    @param so_relpath Optional path (relative to the project) to a specific
           `.so`; if omitted, auto-discovers `.so` files under
           `<slug>_extract/lib/` or `lib/`.
    @note Generalized replacement for `ai_bash_commands.sh`, whose offsets and
          symbol names were hardcoded to one specific binary and not reusable
          across ports. See `docs/dev-notes/utils.md`.
    """
    project_dir = Path(project_cfg["_project_dir"])
    vitasdk_bin = os.path.join(global_cfg.get("vitasdk", ""), "bin")
    if os.path.isdir(vitasdk_bin) and vitasdk_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{vitasdk_bin}:{os.environ.get('PATH', '')}"

    if so_relpath:
        so_files = [project_dir / so_relpath]
    else:
        so_files = sorted(project_dir.glob(f"{project_cfg['slug']}_extract/lib/**/*.so"))
        if not so_files:
            so_files = sorted(project_dir.glob("lib/**/*.so"))

    if not so_files:
        print(t("utils.symbols_no_so_found", color_red=C.RED, color_reset=C.RESET))
        return

    readelf = shutil.which("arm-vita-eabi-readelf") or "arm-vita-eabi-readelf"
    for so in so_files:
        if not so.exists():
            continue
        print(t("utils.symbols_so_header", color_bold=C.BOLD, so=so, color_reset=C.RESET))
        r = subprocess.run([readelf, "-W", "--dyn-syms", str(so)], capture_output=True, text=True)
        if r.returncode != 0:
            msg = r.stderr.strip() or t("utils.symbols_readelf_failed_fallback")
            print(t("utils.symbols_error_line", color_red=C.RED, msg=msg, color_reset=C.RESET))
            continue
        matches = [line for line in r.stdout.splitlines() if re.search(pattern, line)]
        if matches:
            for line in matches:
                print(f"  {line}")
        else:
            print(t("utils.symbols_no_matches", color_dim=C.DIM, pattern=pattern, color_reset=C.RESET))


def generate_toolkit_docs():
    """!
    @brief Check Doxygen docstrings and generate markdown API reference docs
           for all modules in the toolkit.
    """
    from . import gen_docs

    print(t("utils.gen_docs_checking"))
    total_missing = 0
    for py_file in gen_docs.iter_py_files():
        missing = gen_docs.find_missing_docstrings(py_file)
        if missing:
            total_missing += len(missing)
            print(t("utils.gen_docs_missing_line", color_yellow=C.YELLOW, name=py_file.name, count=len(missing), color_reset=C.RESET))

    if not total_missing:
        print(t("utils.gen_docs_all_documented", color_green=C.GREEN, color_reset=C.RESET))
    else:
        print(t("utils.gen_docs_total_missing", color_yellow=C.YELLOW, count=total_missing, color_reset=C.RESET))

    doxygen_bin = gen_docs.find_doxygen()
    doxybook2_bin = gen_docs.find_doxybook2()
    print(t("utils.gen_docs_generating_api"))
    used_real_tool = False
    if doxygen_bin:
        print(t("utils.gen_docs_using_doxygen", bin=doxygen_bin))
        used_real_tool = gen_docs.generate_api_docs_with_doxygen(doxygen_bin, doxybook2_bin)
    else:
        print(t("utils.gen_docs_using_fallback"))

    if not used_real_tool:
        count = gen_docs.generate_api_docs_fallback()
        print(t("utils.gen_docs_done", color_green=C.GREEN, count=count, color_reset=C.RESET))

