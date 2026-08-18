"""
Utilidades varias: limpieza de basura de macOS, re-decompilación, corrida de
tests del proyecto, traducción de shaders/documentación, búsqueda genérica
de símbolos en los .so (reemplaza el cheatsheet ai_bash_commands.sh, que
tenía offsets hardcodeados de UN binario puntual -- acá se busca por patrón,
sin asumir ningún símbolo/motor en particular).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from .tui import C


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
    print(f"{C.GREEN}[+] {removed} archivo(s) '._*' eliminado(s).{C.RESET}")


def decompile_all(project_cfg, global_cfg):
    """Re-corre jadx + devrvk/so-decompiler para el proyecto activo --
    reusa la extracción ya hecha en <slug>_extract/ si existe."""
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

    print("[1/2] Decompilación Java (jadx)...")
    if apk_file and shutil.which("jadx"):
        subprocess.run(["jadx", "-d", str(apk_out_dir), str(apk_file)])
        print(f"{C.GREEN}[+] JADX terminado. Resultados en {apk_out_dir}{C.RESET}")
    elif not apk_file:
        print(f"{C.YELLOW}[!] No se encontró el .apk original en {project_dir} -- se omite jadx.{C.RESET}")
    else:
        print(f"{C.YELLOW}[!] jadx no está instalado (brew install jadx) -- se omite.{C.RESET}")

    print("\n[2/2] Decompilación de .so (Ghidra vía devrvk/so-decompiler)...")
    if not lib_dir.is_dir():
        print(f"{C.YELLOW}[!] No se encontró {lib_dir} -- ¿ya extrajiste el APK? (se hace al crear el port){C.RESET}")
        return
    if not shutil.which("docker"):
        print(f"{C.YELLOW}[!] docker no está instalado -- se omite.{C.RESET}")
        return

    for so_file in lib_dir.rglob("*.so"):
        abi = so_file.parent.name
        so_out = decompiled_dir / f"{so_file.stem}_{abi}" / "ghidra"
        so_out.mkdir(parents=True, exist_ok=True)
        print(f"[*] Decompilando {so_file.name} ({abi})...")
        r = subprocess.run([
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-v", f"{so_file.parent}:/input", "-v", f"{so_out}:/output",
            "devrvk/so-decompiler", "decompile", f"/input/{so_file.name}", "/output",
        ])
        print(f"{'[+] Listo: ' + str(so_out) if r.returncode == 0 else '[!] Falló ' + so_file.name}")


def run_project_tests(project_cfg):
    """Corre tests/run_tests.sh DEL PROYECTO (si existe) -- la lógica de qué
    testear es específica de cada motor/juego, vive en el repo del port, no
    en el toolkit genérico."""
    project_dir = Path(project_cfg["_project_dir"])
    script = project_dir / "tests" / "run_tests.sh"
    if not script.exists():
        print(f"{C.YELLOW}[-] Este proyecto no tiene tests/run_tests.sh.{C.RESET}")
        print(f"{C.DIM}    Es opcional y específico de cada port -- crealo si necesitás un test suite de host.{C.RESET}")
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
    """Limpieza de boilerplate GLES en shaders volcados (glsl_dump/*.glsl ->
    assets/cg/*.cg) -- NO es una traducción GLSL->Cg completa, solo saca
    precision qualifiers y macros de Android. Cada shader sigue necesitando
    revisión/reescritura a mano (traducción real de shaders es específica de
    cada motor)."""
    project_dir = Path(project_cfg["_project_dir"])
    dump_dir = project_dir / "glsl_dump"
    out_dir = project_dir / "assets" / "cg"

    if not dump_dir.is_dir():
        print(f"{C.YELLOW}[-] No existe {dump_dir} -- descargá los shaders volcados primero.{C.RESET}")
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
        print(f"  {glsl_file.name} -> {out_file.name}")
        count += 1

    if count:
        print(f"{C.GREEN}[+] {count} shader(s) con boilerplate limpio en {out_dir}.{C.RESET}")
        print(f"{C.YELLOW}[!] Revisar/reescribir a mano cada uno -- esto NO es una traducción GLSL->Cg real.{C.RESET}")
    else:
        print(f"{C.YELLOW}[-] No había .glsl en {dump_dir}.{C.RESET}")


def translate_docs(project_cfg, target_lang="en"):
    """Traduce en lote los .md del proyecto con deep_translator (Google
    Translate). Requiere `pip install deep-translator`."""
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print(f"{C.RED}[-] Falta 'deep-translator' -- instalar con: pip install deep-translator{C.RESET}")
        return

    project_dir = Path(project_cfg["_project_dir"])
    md_files = [p for p in project_dir.glob("*.md") if not p.name.startswith("._")]
    if not md_files:
        print(f"{C.YELLOW}[-] No hay archivos .md en la raíz de {project_dir}.{C.RESET}")
        return

    translator = GoogleTranslator(source="auto", target=target_lang)
    for md in md_files:
        text = md.read_text(errors="ignore")
        try:
            translated = translator.translate(text)
        except Exception as e:
            print(f"{C.RED}[-] Error traduciendo {md.name}: {e}{C.RESET}")
            continue
        out = md.with_name(f"{md.stem}.{target_lang}.md")
        out.write_text(translated)
        print(f"{C.GREEN}[+] {md.name} -> {out.name}{C.RESET}")


def search_symbols(project_cfg, global_cfg, pattern, so_relpath=None):
    """Búsqueda genérica de símbolos (readelf --dyn-syms) en el/los .so del
    proyecto, filtrados por una expresión regular a elección -- reemplazo
    generalizado de ai_bash_commands.sh (que tenía offsets/símbolos
    hardcodeados de un binario puntual, no reusables entre ports)."""
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
        print(f"{C.RED}[-] No se encontró ningún .so (ni en <slug>_extract/lib/ ni en lib/).{C.RESET}")
        return

    readelf = shutil.which("arm-vita-eabi-readelf") or "arm-vita-eabi-readelf"
    for so in so_files:
        if not so.exists():
            continue
        print(f"\n{C.BOLD}--- {so} ---{C.RESET}")
        r = subprocess.run([readelf, "-W", "--dyn-syms", str(so)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{C.RED}[-] {r.stderr.strip() or 'readelf falló (¿VITASDK en PATH?)'}{C.RESET}")
            continue
        matches = [line for line in r.stdout.splitlines() if re.search(pattern, line)]
        if matches:
            for line in matches:
                print(f"  {line}")
        else:
            print(f"  {C.DIM}(sin coincidencias para '{pattern}'){C.RESET}")
