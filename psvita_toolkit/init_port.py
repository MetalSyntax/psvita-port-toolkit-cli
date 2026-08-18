"""
Asistente "crear port nuevo desde cero" -- puerto a Python de init_new_port.sh,
parametrizado con la config global en vez de rutas hardcodeadas.

Diferencia clave con el script original: NO copia porting_tools/ adentro del
port nuevo. Ese es justamente el motivo de ser de este toolkit standalone --
el port nuevo solo necesita `.psvita-toolkit.json` (que este wizard genera al
final) para que el resto del toolkit (build_deploy, ftp_ops, livearea,
crash_analyzer...) sepa operar sobre él desde afuera. Lo único que se copia
DENTRO del repo del port son las skills de Claude Code y el scaffold de
soloader-boilerplate en sí (source/lib/CMakeLists.txt), porque eso sí es
código fuente del port.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config as cfgmod
from . import tui
from .tui import C


def _sh(cmd, cwd=None, check=True, capture=False):
    return subprocess.run(cmd, cwd=cwd, check=check,
                           capture_output=capture, text=True)


def _have(cmd):
    return shutil.which(cmd) is not None


def _have_docker_image(image):
    if not _have("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", image],
                        capture_output=True, text=True)
    return r.returncode == 0


def check_prereqs(global_cfg):
    print(f"{C.BOLD}Verificando prerrequisitos...{C.RESET}")
    have_jadx = _have("jadx")
    print(f"  {'✅' if have_jadx else '⚠️ '} jadx {'encontrado' if have_jadx else '(brew install jadx) -- se podrá correr manualmente después'}")

    have_docker_so = _have_docker_image("devrvk/so-decompiler")
    if _have("docker"):
        print(f"  {'✅' if have_docker_so else '⚠️ '} docker + devrvk/so-decompiler "
              f"{'encontrados' if have_docker_so else '(falta la imagen -- docker pull devrvk/so-decompiler)'}")
    else:
        print("  ⚠️  docker no encontrado -- la decompilación de .so se podrá correr manualmente después.")

    for tool in ("git", "unzip"):
        if not _have(tool):
            raise RuntimeError(f"'{tool}' no está instalado, no se puede continuar.")

    boilerplate_dir = Path(global_cfg["boilerplate_dir"])
    if not boilerplate_dir.is_dir():
        raise RuntimeError(f"No se encontró soloader-boilerplate en {boilerplate_dir}")

    return have_jadx, have_docker_so


def _default_slug(game_name):
    return "".join(c for c in game_name.lower() if c.isalnum())


def _used_titleids(base_dir):
    used = set()
    base = Path(base_dir)
    if not base.is_dir():
        return used
    for cmake in base.glob("*/CMakeLists.txt"):
        try:
            text = cmake.read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(r'VITA_TITLEID\s+"([A-Za-z0-9]{9})"', text)
        if m:
            used.add(m.group(1))
    return used


def prompt_inputs(global_cfg):
    tui.clear()
    tui.print_banner("Crear port nuevo: Android → PS Vita", icon="🆕")

    game_name = input(f"{C.BOLD}Nombre del juego (display, ej. 'Inotia 4'):{C.RESET}\n> ").strip()
    if not game_name:
        raise RuntimeError("El nombre del juego es obligatorio.")

    default_slug = _default_slug(game_name)
    slug = input(f"{C.BOLD}Slug corto interno, sin espacios{C.RESET} [{default_slug}]: ").strip() or default_slug

    default_folder = game_name.replace(" ", "-") + "-vita"
    folder_name = input(f"{C.BOLD}Nombre de la carpeta del proyecto{C.RESET} [{default_folder}]: ").strip() or default_folder

    project_name = slug.replace("-", "_")

    apk_path = tui.input_path("Ruta absoluta al .apk original:", must_exist=True)

    vita_ip = input(f"{C.BOLD}IP de la PS Vita de pruebas{C.RESET} [192.168.1.100]: ").strip() or "192.168.1.100"

    base_dir = Path(global_cfg["base_dir"])
    used_ids = _used_titleids(base_dir)
    print(f"\n{C.DIM}TITLEIDs ya usados en {base_dir} (no reusar, colisiona en LiveArea):{C.RESET}")
    for t in sorted(used_ids):
        print(f"    {t}")
    while True:
        titleid = input(f"{C.BOLD}TITLEID nuevo, 9 caracteres alfanuméricos{C.RESET} (ej. PSVXX0001): ").strip().upper()
        if len(titleid) != 9:
            print(f"{C.RED}Debe tener exactamente 9 caracteres.{C.RESET}")
            continue
        if titleid in used_ids:
            print(f"{C.RED}Ese TITLEID ya está en uso -- elegí otro.{C.RESET}")
            continue
        break

    new_dir = base_dir / folder_name
    if new_dir.exists():
        print(f"{C.YELLOW}[!] Ya existe {new_dir} -- se reutiliza tal cual está y se continúa.{C.RESET}")

    print(f"\n{C.BOLD}Resumen:{C.RESET}")
    print(f"  Juego:    {game_name}")
    print(f"  Slug:     {slug}")
    print(f"  Carpeta:  {new_dir}")
    print(f"  Proyecto: {project_name}")
    print(f"  APK:      {apk_path}")
    print(f"  TITLEID:  {titleid}")
    print(f"  Vita IP:  {vita_ip}")

    if not tui.confirm("\n¿Continuar?"):
        raise RuntimeError("Cancelado por el usuario.")

    return {
        "game_name": game_name, "slug": slug, "folder_name": folder_name,
        "project_name": project_name, "apk_path": apk_path,
        "vita_ip": vita_ip, "titleid": titleid, "new_dir": new_dir,
    }


def setup_repo_dir(global_cfg, ctx):
    new_dir = ctx["new_dir"]
    boilerplate_dir = Path(global_cfg["boilerplate_dir"])

    if (new_dir / ".git").is_dir():
        print(f"{C.YELLOW}[!] {new_dir} ya es un repo git -- se deja como está.{C.RESET}")
        return

    if new_dir.exists() and any(new_dir.iterdir()):
        print(f"[*] {new_dir} ya existe y tiene contenido -- se mergea el scaffold sin pisar nada.")
    else:
        print(f"[*] Clonando soloader-boilerplate en {new_dir} ...")

    with tempfile.TemporaryDirectory() as tmp_clone:
        _sh(["git", "clone", "--quiet", str(boilerplate_dir), tmp_clone])
        print("[*] Inicializando submódulo lib/falso_jni (requiere red)...")
        r = subprocess.run(["git", "submodule", "update", "--init", "--recursive"],
                            cwd=tmp_clone, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{C.YELLOW}[!] No se pudo bajar el submódulo (¿sin red?) -- correr manualmente después.{C.RESET}")

        shutil.rmtree(Path(tmp_clone) / ".git", ignore_errors=True)
        new_dir.mkdir(parents=True, exist_ok=True)
        _sh(["cp", "-Rn", f"{tmp_clone}/.", str(new_dir) + "/"])

    cmake_path = new_dir / "CMakeLists.txt"
    if cmake_path.exists():
        print("[*] Adaptando CMakeLists.txt (VITA_APP_NAME/VITA_TITLEID/project/DATA_PATH)...")
        text = cmake_path.read_text()
        text = text.replace('project(so_loader C CXX)', f'project({ctx["project_name"]} C CXX)')
        text = text.replace('set(VITA_APP_NAME "so-loader")', f'set(VITA_APP_NAME "{ctx["game_name"]}")')
        text = text.replace('set(VITA_TITLEID "SOLOADER0")', f'set(VITA_TITLEID "{ctx["titleid"]}")')
        text = text.replace('set(VITA_VPKNAME "so_loader")', f'set(VITA_VPKNAME "{ctx["project_name"]}")')
        text = re.sub(r'set\(PSVITAIP "[^"]*"', f'set(PSVITAIP "{ctx["vita_ip"]}"', text)
        text = text.replace('ux0:data/gamename/', f'ux0:data/{ctx["slug"]}/')
        cmake_path.write_text(text)
        print(f"{C.GREEN}[+] CMakeLists.txt adaptado.{C.RESET}")


def place_apk_and_detect(ctx):
    new_dir = ctx["new_dir"]
    apk_path = Path(ctx["apk_path"])
    apk_basename = apk_path.name
    apk_stem = apk_path.stem

    print("[*] Copiando .apk (y su .zip gemelo)...")
    dest_apk = new_dir / apk_basename
    if dest_apk.resolve() != apk_path.resolve():
        shutil.copy2(apk_path, dest_apk)
    shutil.copy2(apk_path, new_dir / f"{apk_stem}.zip")

    extract_dir = new_dir / f"{ctx['slug']}_extract"
    print(f"[*] Extrayendo APK a {extract_dir.name}/ ...")
    extract_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["unzip", "-qq", "-o", str(apk_path), "-d", str(extract_dir)])

    abis = []
    lib_dir = extract_dir / "lib"
    if lib_dir.is_dir():
        abis = sorted(d.name for d in lib_dir.iterdir() if d.is_dir())

    preferred_abi = "armeabi-v7a" if "armeabi-v7a" in abis else (abis[0] if abis else None)

    print(f"\n[*] ABIs nativas encontradas: {', '.join(abis) or 'ninguna'}")
    if not preferred_abi:
        arch_note = "No se encontró lib/<abi>/ nativo -- confirmar si el juego tiene motor nativo antes de asumir soloader."
    elif preferred_abi == "armeabi-v7a":
        arch_note = "armeabi-v7a presente -> ARMv7 (hard-float/NEON disponible). El CPU de Vita (Cortex-A9) corre esto sin traducción."
    else:
        arch_note = "Solo armeabi (ARMv6, soft-float) -- Vita lo ejecuta igual (ARMv7 es superset), sin NEON de v7a."
    if len(abis) > 1:
        arch_note += f" Hay más de una ABI ({', '.join(abis)}) -- se eligió {preferred_abi} para el análisis."
    print(f"[+] {arch_note}")

    so_files = []
    if preferred_abi:
        so_files = sorted((lib_dir / preferred_abi).glob("*.so"))
    print(f"\n[*] .so encontrados en lib/{preferred_abi}/:")
    for f in so_files:
        size_kb = f.stat().st_size / 1024
        print(f"    {f.name}  ({size_kb:.0f} KB)")

    gles_hint = "sin determinar"
    if so_files and _have("objdump"):
        gles1 = gles2 = gles3 = 0
        for so in so_files:
            r = subprocess.run(["objdump", "-T", str(so)], capture_output=True, text=True)
            syms = set(re.findall(r"gl[A-Za-z0-9_]*", r.stdout))
            if syms & {"glVertexPointer", "glClearColorx", "glTexParameterx", "glColor4x"}:
                gles1 += 1
            if syms & {"glCreateShader", "glCreateProgram", "glUseProgram", "glGetUniformLocation"}:
                gles2 += 1
            if syms & {"glDrawArraysInstanced", "glDrawRangeElements", "glGenVertexArrays", "glBindVertexArray"}:
                gles3 += 1
        if gles3:
            gles_hint = "GLES3 (glGenVertexArrays/glDrawArraysInstanced presentes)"
        elif gles2:
            gles_hint = "GLES2 (glCreateShader/glCreateProgram/glUseProgram -- pipeline programable)"
        elif gles1:
            gles_hint = "GLES1 (pipeline fijo: glVertexPointer/glClearColorx/glTexParameterx)"
        else:
            gles_hint = "sin señal clara por símbolos (posible Unity/libil2cpp) -- revisar con Ghidra"
    print(f"[*] Heurística de símbolos GL: {gles_hint}")

    manifest = extract_dir / "AndroidManifest.xml"
    java_package = ""
    if manifest.exists():
        m = re.search(rb'package="([^"]*)"', manifest.read_bytes())
        if m:
            java_package = m.group(1).decode(errors="ignore")

    ctx.update({
        "apk_basename": apk_basename, "extract_dir": extract_dir,
        "abis": abis, "preferred_abi": preferred_abi, "arch_note": arch_note,
        "so_files": so_files, "gles_hint": gles_hint, "java_package": java_package,
    })


def decompile(global_cfg, ctx, have_jadx, have_docker_so):
    new_dir = ctx["new_dir"]
    decompiled_dir = new_dir / "decompiled"
    apk_out_dir = decompiled_dir / "apk_jadx"
    apk_out_dir.mkdir(parents=True, exist_ok=True)

    jadx_ok = False
    gles_final = ctx["gles_hint"]
    if have_jadx:
        print("[*] Decompilando Java del APK con jadx (puede tardar unos minutos)...")
        r = subprocess.run(["jadx", "-d", str(apk_out_dir), str(new_dir / ctx["apk_basename"])])
        jadx_ok = r.returncode == 0
        print(f"{'[+] jadx terminó sin errores.' if jadx_ok else '[!] jadx terminó con errores (normal si son solo SDKs de ads/analytics).'}")

        manifest_decoded = apk_out_dir / "resources" / "AndroidManifest.xml"
        if manifest_decoded.exists():
            text = manifest_decoded.read_text(errors="ignore")
            m = re.search(r'glEsVersion="([^"]*)"', text)
            if m:
                gles_map = {"0x00010000": "GLES1", "65536": "GLES1",
                            "0x00020000": "GLES2", "131072": "GLES2",
                            "0x00030000": "GLES3", "196608": "GLES3"}
                gles_final = gles_map.get(m.group(1), f"valor no estándar en manifest: {m.group(1)}") + " (declarado en AndroidManifest.xml)"
            else:
                gles_final = f"AndroidManifest.xml no declara glEsVersion -- usar heurística ({ctx['gles_hint']})"
            m = re.search(r'package="([^"]*)"', text)
            if m:
                ctx["java_package"] = m.group(1)
        else:
            gles_final = f"no se pudo leer AndroidManifest.xml decodificado -- usar heurística ({ctx['gles_hint']})"
    else:
        gles_final = f"jadx no corrió -- heurística de símbolos solamente ({ctx['gles_hint']})"

    print(f"\n[+] Versión de GLES determinada: {gles_final}")

    if have_docker_so and ctx["so_files"]:
        for so_file in ctx["so_files"]:
            abi = so_file.parent.name
            so_out = decompiled_dir / f"{so_file.stem}_{abi}" / "ghidra"
            so_out.mkdir(parents=True, exist_ok=True)
            print(f"[*] Decompilando {so_file.name} ({abi}) con Ghidra headless (puede tardar varios minutos)...")
            r = subprocess.run([
                "docker", "run", "--rm", "--platform", "linux/amd64",
                "-v", f"{so_file.parent}:/input", "-v", f"{so_out}:/output",
                "devrvk/so-decompiler", "decompile", f"/input/{so_file.name}", "/output",
            ])
            print(f"{'[+] Listo: ' + str(so_out) if r.returncode == 0 else '[!] Falló la decompilación de ' + so_file.name}")
    else:
        print(f"{C.YELLOW}[!] Se omite la decompilación de .so (docker/imagen no disponibles) -- correr manualmente después.{C.RESET}")

    ctx["jadx_ok"] = jadx_ok
    ctx["have_docker_so"] = have_docker_so
    ctx["gles_final"] = gles_final


def git_init_and_ignore(ctx):
    new_dir = ctx["new_dir"]
    slug = ctx["slug"]
    print("[*] git init + .gitignore anti-DMCA...")
    if not (new_dir / ".git").is_dir():
        _sh(["git", "init", "-q"], cwd=new_dir)

    gitignore = f"""# macOS metadata
.DS_Store
._*
.Spotlight-V100
.Trashes

# Android APK/ZIP originales y extracción -- nunca commitear el juego (DMCA)
*.apk
*.zip
/{slug}_extract/

# Java decompilado con jadx (derivado, regenerable: jadx -d decompiled/apk_jadx "{ctx['apk_basename']}")
/decompiled/apk_jadx/

# Pseudo-C decompilado del/los .so (derivado, regenerable con devrvk/so-decompiler)
/decompiled/*/ghidra/

# Librerías .so propietarias del juego original
lib/*.so
lib/**/*.so
{slug}_extract/lib/

# Assets del juego montados para pruebas
ux0_data/
assets/

# Build artifacts
/build/
CMakeCache.txt
CMakeFiles/
Makefile
cmake_install.cmake
*.elf
*.self
*.vpk
*.suprx

# Debugging en consola real
/logs/
log_*.txt
*.psp2dmp

# Python
__pycache__/
*.pyc

# Config local del toolkit (contiene IP de tu Vita -- no es secreto pero es de tu red)
.psvita-toolkit.json

# IDE
.vscode/
.idea/
*.swp
cmake-build-*/
"""
    (new_dir / ".gitignore").write_text(gitignore)
    print(f"{C.GREEN}[+] .gitignore escrito.{C.RESET}")


def write_plan_and_progress(ctx):
    import datetime
    new_dir = ctx["new_dir"]
    today = datetime.date.today().isoformat()

    so_list = "".join(
        f"- `{f.relative_to(new_dir)}` ({f.stat().st_size / 1024:.0f} KB)\n"
        for f in ctx["so_files"]
    ) or "(ninguno detectado automáticamente -- revisar la extracción a mano)\n"

    jni_exports = ""
    if ctx["so_files"] and _have("objdump"):
        r = subprocess.run(["objdump", "-T", str(ctx["so_files"][0])], capture_output=True, text=True)
        names = sorted(set(re.findall(r"Java_\S+", r.stdout)))
        jni_exports = "".join(f"- `{n}`\n" for n in names)
    if not jni_exports:
        jni_exports = ("(no se encontraron exports `Java_*` -- confirmar a mano con objdump -T, "
                        "puede que el motor registre con RegisterNatives en vez de convención de nombre)\n")

    plan = f"""# Plan de Port — {ctx['game_name']} (PS Vita)

> Generado por psvita-port-toolkit el {today}. Punto de partida con lo detectado automáticamente --
> confirmar todo con objdump/Ghidra/jadx a mano antes de asumirlo como cierto.

## 0. Contexto

- **Juego:** {ctx['game_name']}
- **Paquete Java:** {ctx['java_package'] or '(pendiente, ver decompiled/apk_jadx/resources/AndroidManifest.xml)'}
- **APK original:** `{ctx['apk_basename']}`
- **TITLEID asignado:** `{ctx['titleid']}`

**¿Motor conocido?** Revisar si algún port hermano (bajo la misma BASE_DIR) comparte motor antes de
reusar su código -- confirmar con símbolos JNI reales, no por analogía superficial.

## 1. Detección automática

- **ABI(s):** {', '.join(ctx['abis']) or 'ninguna'}
- **ABI elegida:** {ctx['preferred_abi'] or 'N/A'}
- **Nota de arquitectura:** {ctx['arch_note']}
- **Versión de GLES:** {ctx['gles_final']}

## 2. .so encontrados (ABI {ctx['preferred_abi'] or 'N/A'})

{so_list}

## 3. Exports JNI (convención `Java_*`)

{jni_exports}

## 4. Checklist

- [x] Repo creado desde soloader-boilerplate, git init, .gitignore anti-DMCA.
- [x] APK decompilado (jadx) y .so decompilado(s) (Ghidra) -- ver sección 2/3.
- [ ] Análisis del motor real (ciclo de vida nativo, reuso de otro port o boilerplate genérico).
- [ ] Bootstrap del loader: so_file_load/so_relocate/so_resolve, primer build.
- [ ] Tabla JNI (FalsoJNI): registrar exports + callbacks hacia "Java".
- [ ] Primer arranque (Vita3K primero, consola real después).
- [ ] Gráficos (wrappers GL según versión detectada).
- [ ] Input, Audio, Assets, LiveArea/VPK.
- [ ] Pruebas en hardware real.

## 5. Herramientas

Este port se gestiona con **psvita-port-toolkit** (standalone, fuera de este repo). Desde el
toolkit: `Continuar con un port existente` → elegí esta carpeta (ya tiene `.psvita-toolkit.json`).
"""
    (new_dir / "PORTING_PLAN.md").write_text(plan)
    print(f"{C.GREEN}[+] PORTING_PLAN.md escrito.{C.RESET}")

    progress = f"""# Registro de Progreso — {ctx['game_name']} (PS Vita)

## Fase 1: Configuración y Preparación (Completada — {today})
- Repo creado desde soloader-boilerplate, `.gitignore` anti-DMCA.
- APK `{ctx['apk_basename']}` copiado y extraído.
- ABI detectada: {', '.join(ctx['abis']) or 'ninguna'} (elegida: {ctx['preferred_abi'] or 'N/A'}).
- GLES detectado: {ctx['gles_final']}

## Fase 2: Decompilación (Completada — {today})
- jadx: {'corrido, resultados en decompiled/apk_jadx/.' if ctx['jadx_ok'] else 'NO corrido -- pendiente.'}
- Ghidra (.so): {'corrido para cada .so.' if ctx['have_docker_so'] else 'NO corrido (docker/imagen no disponibles) -- pendiente.'}

## Fase 3: Análisis del Motor Real (Pendiente)
- [ ] Confirmar si comparte motor con algún port hermano.
- [ ] Leer decompiled/apk_jadx/sources/ para el ciclo de vida nativo real.
- [ ] Confirmar exports JNI reales y si hay RegisterNatives.

## Fase 4 en adelante: Pendiente
Ver PORTING_PLAN.md sección 4. Actualizar con un bug confirmado a la vez en pruebas reales.
"""
    (new_dir / "port_progress.md").write_text(progress)
    print(f"{C.GREEN}[+] port_progress.md escrito.{C.RESET}")


def write_claude_md_and_skills(global_cfg, ctx):
    new_dir = ctx["new_dir"]
    skills_source = Path(global_cfg["skills_source"])
    skills_dest = new_dir / ".claude" / "skills"
    skills_dest.mkdir(parents=True, exist_ok=True)

    for skill in ("psvita-porting", "so-crash-triage", "psvita-port-init"):
        src = skills_source / skill
        if src.is_dir():
            dst = skills_dest / skill
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("._*"))
            print(f"{C.GREEN}[+] Skill '{skill}' copiada al repo.{C.RESET}")
        else:
            print(f"{C.YELLOW}[!] Skill '{skill}' no encontrada en {skills_source} -- se omite.{C.RESET}")

    claude_md = f"""# {ctx['game_name']} — Port a PS Vita

Port de `{ctx['apk_basename']}` (Android) a PS Vita vía soloader. Generado con **psvita-port-toolkit**.

## Estructura

- `{ctx['slug']}_extract/` — APK extraído (gitignored).
- `decompiled/` — Java (jadx) y pseudo-C (Ghidra) del/los .so (gitignored, regenerable).
- `source/`, `lib/so_util`, `lib/falso_jni` — scaffold del boilerplate (SoLoader + FalsoJNI).
- `PORTING_PLAN.md` — plan vivo, actualizar a medida que se confirman cosas del motor real.
- `port_progress.md` — bitácora, un bug confirmado a la vez.
- `.psvita-toolkit.json` — config para el toolkit standalone (build/deploy/logs/LiveArea/crash dumps).

Este port **no** tiene una copia local de `porting_tools/` -- todo el build/deploy/debug se maneja
desde **psvita-port-toolkit**, la herramienta standalone (fuera de este repo). Abrí el toolkit y
elegí "Continuar con un port existente" apuntando a esta carpeta.

## Hallazgos de motor (automáticos, sin confirmar)

- ABI: {', '.join(ctx['abis']) or 'ninguna'} (preferida: {ctx['preferred_abi'] or 'N/A'})
- GLES: {ctx['gles_final']}
- Paquete Java: {ctx['java_package'] or 'pendiente'}

## Flujo de trabajo esperado

1. Análisis de símbolos antes de tocar loader/source -- skill `psvita-port-init` cubrió la Fase 0-2.
2. Bootstrap del loader guiado por la skill `psvita-porting`.
3. Build/deploy con el toolkit standalone → probar en Vita3K → probar en consola real.
4. Un bug a la vez, guiado por el log real -- skill `so-crash-triage`.
5. Actualizar `port_progress.md` con cada bug confirmado.
"""
    (new_dir / "CLAUDE.md").write_text(claude_md)
    print(f"{C.GREEN}[+] CLAUDE.md escrito.{C.RESET}")


def write_project_config(ctx):
    project_cfg = cfgmod.new_project_config(
        game_name=ctx["game_name"], slug=ctx["slug"],
        project_name=ctx["project_name"], titleid=ctx["titleid"],
        vita_ip=ctx["vita_ip"], apk_basename=ctx["apk_basename"],
    )
    cfgmod.save_project_config(ctx["new_dir"], project_cfg)
    print(f"{C.GREEN}[+] {ctx['new_dir']}/.psvita-toolkit.json escrito -- el toolkit ya reconoce este port.{C.RESET}")
    project_cfg["_project_dir"] = str(ctx["new_dir"])
    return project_cfg


def run_wizard(global_cfg):
    """Punto de entrada. Devuelve la config de proyecto lista para usar, o
    None si el usuario canceló en algún punto."""
    try:
        have_jadx, have_docker_so = check_prereqs(global_cfg)
        ctx = prompt_inputs(global_cfg)

        tui.clear()
        tui.print_banner(f"Creando port: {ctx['game_name']}", icon="🆕")
        setup_repo_dir(global_cfg, ctx)
        place_apk_and_detect(ctx)
        decompile(global_cfg, ctx, have_jadx, have_docker_so)
        git_init_and_ignore(ctx)
        write_plan_and_progress(ctx)
        write_claude_md_and_skills(global_cfg, ctx)
        project_cfg = write_project_config(ctx)

        print(f"\n{C.CYAN}{C.BOLD}================================================================{C.RESET}")
        print(f"{C.GREEN}{C.BOLD}  ✅ Listo: {ctx['new_dir']}{C.RESET}")
        print(f"{C.CYAN}{C.BOLD}================================================================{C.RESET}")
        print("Siguiente paso: seguir PORTING_PLAN.md sección 4 (empezando por la Fase 3,")
        print("análisis real del motor) antes de escribir código en source/.")
        tui.pause()
        return project_cfg
    except RuntimeError as e:
        print(f"{C.RED}[-] {e}{C.RESET}")
        tui.pause()
        return None
