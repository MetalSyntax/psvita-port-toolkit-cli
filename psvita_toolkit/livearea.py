"""
Adaptador de imágenes PNG a las specs de LiveArea de PS Vita -- integrado
desde convert_livearea.py standalone. Igual lógica de conversión (crop/fit/
stretch + indexado 8-bit), pero el directorio destino sale de la config del
proyecto activo (extras/livearea/ dentro del port) en vez de una ruta
hardcodeada a un solo juego.
"""

import shutil
from pathlib import Path

from PIL import Image, ImageOps

from . import tui
from .tui import C

VITA_SPECS = {
    "bg0": {"filename": "bg0.png", "width": 840, "height": 500, "max_kb": 128,
            "name": "LiveArea Background", "desc": "Fondo principal de la LiveArea"},
    "pic0": {"filename": "pic0.png", "width": 960, "height": 544, "max_kb": 1024,
             "name": "Splash Screen / Lockscreen", "desc": "Pantalla de inicio / desbloqueo"},
    "icon0": {"filename": "icon0.png", "width": 128, "height": 128, "max_kb": 128,
              "name": "App Icon", "desc": "Icono de la burbuja en el menú Home"},
    "startup": {"filename": "startup.png", "width": 280, "height": 158, "max_kb": 128,
                "name": "Gate Startup Banner", "desc": "Banner de la compuerta de arranque"},
}


def _resize(img, target_w, target_h, mode="crop", bg_color=(0, 0, 0, 0)):
    if mode == "stretch":
        return img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    src_w, src_h = img.size
    if mode == "fit":
        img_ratio = src_w / src_h
        target_ratio = target_w / target_h
        if img_ratio > target_ratio:
            new_w, new_h = target_w, int(round(target_w / img_ratio))
        else:
            new_h, new_w = target_h, int(round(target_h * img_ratio))
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        canvas_mode = "RGBA" if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info) else "RGB"
        color = bg_color[:3] if canvas_mode == "RGB" else bg_color
        canvas = Image.new(canvas_mode, (target_w, target_h), color)
        px, py = (target_w - new_w) // 2, (target_h - new_h) // 2
        if resized.mode == "RGBA":
            canvas.paste(resized, (px, py), mask=resized.split()[3])
        else:
            canvas.paste(resized, (px, py))
        return canvas

    return ImageOps.fit(img, (target_w, target_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _to_8bit_indexed(img, dither=True):
    dither_val = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        return img.convert("RGBA").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=dither_val)
    return img.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=256, dither=dither_val)


def process_asset(input_path, asset_type, output_dir, mode="crop", dither=True, backup=True):
    spec = VITA_SPECS[asset_type]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / spec["filename"]

    if out_file.exists() and backup:
        bak = out_file.with_suffix(".png.bak")
        shutil.copy2(out_file, bak)
        print(f"  {C.DIM}[BACKUP] {bak.name}{C.RESET}")

    print(f"\n⚙️  {C.BOLD}Procesando:{C.RESET} '{C.CYAN}{Path(input_path).name}{C.RESET}' -> "
          f"'{C.GREEN}{spec['filename']}{C.RESET}' ({spec['name']})")

    with Image.open(input_path) as src:
        print(f"  📐 {C.DIM}Entrada:{C.RESET} {src.size[0]}x{src.size[1]} ({src.mode})")
        resized = _resize(src, spec["width"], spec["height"], mode=mode)
        print(f"  ✂️  {C.DIM}Ajuste ({mode}):{C.RESET} {spec['width']}x{spec['height']}")
        indexed = _to_8bit_indexed(resized, dither=dither)
        indexed.save(out_file, format="PNG", optimize=True)

    size_kb = out_file.stat().st_size / 1024
    ok = size_kb <= spec["max_kb"]
    icon = "✅" if ok else "⚠️ "
    color = C.GREEN if ok else C.YELLOW
    print(f"  {icon} {C.BOLD}Guardado:{C.RESET} {out_file}")
    print(f"  📊 {color}Tamaño: {size_kb:.2f} KB (límite {spec['max_kb']} KB){C.RESET}")
    if not ok:
        print(f"  {C.YELLOW}⚠️  Supera el límite -- LiveArea podría no cargarlo.{C.RESET}")
    return out_file


def _render_status(dest_dir):
    print(f"{C.DIM}📁 Directorio destino:{C.RESET} {C.BOLD}{dest_dir}{C.RESET}\n")
    print(f"{C.BOLD}📋 Estado actual de los assets:{C.RESET}")
    print(f"  {'Asset':<12} {'Dimensiones':<14} {'Estado':<10} {'Tamaño':<18} {'Descripción'}")
    print(f"  {'-'*12} {'-'*14} {'-'*10} {'-'*18} {'-'*26}")
    for spec in VITA_SPECS.values():
        fpath = Path(dest_dir) / spec["filename"]
        if fpath.exists():
            kb = fpath.stat().st_size / 1024
            try:
                with Image.open(fpath) as im:
                    dim = f"{im.size[0]}x{im.size[1]}"
                    mode = "8-bit" if im.mode == "P" else im.mode
            except Exception:
                dim, mode = f"{spec['width']}x{spec['height']}", "OK"
            kb_color = C.GREEN if kb <= spec["max_kb"] else C.YELLOW
            status, size_info = f"{C.GREEN}PRESENTE{C.RESET}", f"{kb_color}{kb:.1f} KB ({mode}){C.RESET}"
        else:
            dim = f"{spec['width']}x{spec['height']}"
            status, size_info = f"{C.RED}FALTA{C.RESET}", f"{C.DIM}--{C.RESET}"
        print(f"  {C.BOLD}{spec['filename']:<12}{C.RESET} {dim:<14} {status:<19} {size_info:<27} {C.DIM}{spec['name']}{C.RESET}")
    print()


def _pick_mode():
    print(f"{C.BOLD}Modo de ajuste:{C.RESET}")
    print(f"  {C.GREEN}1){C.RESET} Recorte centrado (Crop) {C.BOLD}[Recomendado]{C.RESET}")
    print(f"  {C.GREEN}2){C.RESET} Contener completo (Fit, con márgenes)")
    print(f"  {C.GREEN}3){C.RESET} Estirar directo (Stretch)")
    choice = input("Modo [1-3] (Default 1): ").strip()
    return {"1": "crop", "2": "fit", "3": "stretch"}.get(choice, "crop")


def _asset_flow(asset_type, dest_dir):
    spec = VITA_SPECS[asset_type]
    print(f"\n{C.BOLD}📌 {spec['filename']}{C.RESET} ({spec['desc']})")
    print(f"{C.DIM}Tip: podés arrastrar la imagen desde el Finder directamente acá.{C.RESET}")
    raw = tui.input_path("🖼️  Ruta de la imagen original (Enter para omitir):", allow_blank=True)
    if not raw:
        return
    try:
        with Image.open(raw) as test_img:
            print(f"  {C.GREEN}✓{C.RESET} Imagen cargada: {test_img.size[0]}x{test_img.size[1]} ({test_img.format}, {test_img.mode})")
    except Exception as e:
        print(f"{C.RED}❌ Error al leer la imagen: {e}{C.RESET}")
        return
    mode = _pick_mode()
    try:
        process_asset(raw, asset_type, dest_dir, mode=mode, dither=True, backup=True)
    except Exception as e:
        print(f"{C.RED}❌ Error en la conversión: {e}{C.RESET}")


def livearea_menu(project_cfg):
    project_dir = Path(project_cfg["_project_dir"])
    dest_dir = project_dir / "extras" / "livearea"

    def make_flow(asset_type):
        return lambda: _asset_flow(asset_type, dest_dir)

    def batch_flow():
        print(f"\n{C.BOLD}📦 Conversión en lote:{C.RESET}")
        for atype in VITA_SPECS:
            spec = VITA_SPECS[atype]
            raw = tui.input_path(f"🖼️  Imagen para {spec['filename']} ({spec['name']}) [Enter omitir]:", allow_blank=True)
            if not raw:
                continue
            try:
                process_asset(raw, atype, dest_dir, mode="crop", dither=True, backup=True)
            except Exception as e:
                print(f"{C.RED}❌ Error en {spec['filename']}: {e}{C.RESET}")

    items = [
        (f"Adaptar {C.BOLD}bg0.png{C.RESET}   (840x500 -- Fondo LiveArea)", make_flow("bg0")),
        (f"Adaptar {C.BOLD}pic0.png{C.RESET}  (960x544 -- Splash / Inicio)", make_flow("pic0")),
        (f"Adaptar {C.BOLD}icon0.png{C.RESET} (128x128 -- Icono de burbuja)", make_flow("icon0")),
        (f"Adaptar {C.BOLD}startup.png{C.RESET} (280x158 -- Banner de compuerta)", make_flow("startup")),
        ("Adaptar TODOS los assets en lote", batch_flow),
    ]

    def header():
        _render_status(dest_dir)

    tui.run_menu(f"LiveArea -- {project_cfg['game_name']}", items,
                 breadcrumb=f"{project_cfg['game_name']} › LiveArea", icon="🎨",
                 header_extra=header)
