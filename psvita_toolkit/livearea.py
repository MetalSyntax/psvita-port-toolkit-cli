"""!
@file livearea.py
@brief Converts PNG images into PS Vita LiveArea assets (background, splash/lockscreen,
       app icon, startup gate banner).

@details
Each source image is resized to the target asset's exact PS Vita dimensions (using a
crop/fit/stretch mode) and then quantized to an 8-bit indexed PNG, which is a hard
requirement of the PS Vita LiveArea format. Output is written to
`<project_dir>/extras/livearea/`.

See `docs/dev-notes/livearea.md` for the history behind this module and the resize-mode
design rationale.
"""

import shutil
from pathlib import Path

from PIL import Image, ImageOps

from . import i18n
from .i18n import t
from . import tui
from .tui import C

VITA_SPECS = {
    "bg0": {"filename": "bg0.png", "width": 840, "height": 500, "max_kb": 128,
            "name": "livearea.spec.bg0.name", "desc": "livearea.spec.bg0.desc"},
    "pic0": {"filename": "pic0.png", "width": 960, "height": 544, "max_kb": 1024,
             "name": "livearea.spec.pic0.name", "desc": "livearea.spec.pic0.desc"},
    "icon0": {"filename": "icon0.png", "width": 128, "height": 128, "max_kb": 128,
              "name": "livearea.spec.icon0.name", "desc": "livearea.spec.icon0.desc"},
    "startup": {"filename": "startup.png", "width": 280, "height": 158, "max_kb": 128,
                "name": "livearea.spec.startup.name", "desc": "livearea.spec.startup.desc"},
}

STRINGS = {
    "livearea.spec.bg0.name": {
        "es": "LiveArea Background",
        "en": "LiveArea Background",
        "pt": "LiveArea Background",
    },
    "livearea.spec.bg0.desc": {
        "es": "Fondo principal de la LiveArea",
        "en": "Main LiveArea background",
        "pt": "Fundo principal da LiveArea",
    },
    "livearea.spec.pic0.name": {
        "es": "Splash Screen / Lockscreen",
        "en": "Splash Screen / Lockscreen",
        "pt": "Splash Screen / Lockscreen",
    },
    "livearea.spec.pic0.desc": {
        "es": "Pantalla de inicio / desbloqueo",
        "en": "Startup / unlock screen",
        "pt": "Tela de inicialização / desbloqueio",
    },
    "livearea.spec.icon0.name": {
        "es": "App Icon",
        "en": "App Icon",
        "pt": "App Icon",
    },
    "livearea.spec.icon0.desc": {
        "es": "Icono de la burbuja en el menú Home",
        "en": "Bubble icon in the Home menu",
        "pt": "Ícone da bolha no menu Home",
    },
    "livearea.spec.startup.name": {
        "es": "Gate Startup Banner",
        "en": "Gate Startup Banner",
        "pt": "Gate Startup Banner",
    },
    "livearea.spec.startup.desc": {
        "es": "Banner de la compuerta de arranque",
        "en": "Startup gate banner",
        "pt": "Banner do portão de inicialização",
    },
    "livearea.backup_saved": {
        "es": "[BACKUP] {name}",
        "en": "[BACKUP] {name}",
        "pt": "[BACKUP] {name}",
    },
    "livearea.processing": {
        "es": "⚙️  {bold}Procesando:{reset} '{cyan}{input_name}{reset}' -> '{green}{filename}{reset}' ({spec_name})",
        "en": "⚙️  {bold}Processing:{reset} '{cyan}{input_name}{reset}' -> '{green}{filename}{reset}' ({spec_name})",
        "pt": "⚙️  {bold}Processando:{reset} '{cyan}{input_name}{reset}' -> '{green}{filename}{reset}' ({spec_name})",
    },
    "livearea.input_dims": {
        "es": "📐 {dim}Entrada:{reset} {width}x{height} ({mode})",
        "en": "📐 {dim}Input:{reset} {width}x{height} ({mode})",
        "pt": "📐 {dim}Entrada:{reset} {width}x{height} ({mode})",
    },
    "livearea.adjust": {
        "es": "✂️  {dim}Ajuste ({mode}):{reset} {width}x{height}",
        "en": "✂️  {dim}Adjustment ({mode}):{reset} {width}x{height}",
        "pt": "✂️  {dim}Ajuste ({mode}):{reset} {width}x{height}",
    },
    "livearea.saved": {
        "es": "{bold}Guardado:{reset} {out_file}",
        "en": "{bold}Saved:{reset} {out_file}",
        "pt": "{bold}Salvo:{reset} {out_file}",
    },
    "livearea.size_info": {
        "es": "📊 Tamaño: {size_kb:.2f} KB (límite {max_kb} KB)",
        "en": "📊 Size: {size_kb:.2f} KB (limit {max_kb} KB)",
        "pt": "📊 Tamanho: {size_kb:.2f} KB (limite {max_kb} KB)",
    },
    "livearea.exceeds_limit": {
        "es": "⚠️  Supera el límite -- LiveArea podría no cargarlo.",
        "en": "⚠️  Exceeds the limit -- LiveArea might fail to load it.",
        "pt": "⚠️  Excede o limite -- a LiveArea pode não carregá-lo.",
    },
    "livearea.dest_dir": {
        "es": "📁 Directorio destino: {dest_dir}",
        "en": "📁 Destination directory: {dest_dir}",
        "pt": "📁 Diretório de destino: {dest_dir}",
    },
    "livearea.status_title": {
        "es": "📋 Estado actual de los assets:",
        "en": "📋 Current status of the assets:",
        "pt": "📋 Estado atual dos assets:",
    },
    "livearea.header.asset": {
        "es": "Asset",
        "en": "Asset",
        "pt": "Asset",
    },
    "livearea.header.dimensions": {
        "es": "Dimensiones",
        "en": "Dimensions",
        "pt": "Dimensões",
    },
    "livearea.header.status": {
        "es": "Estado",
        "en": "Status",
        "pt": "Estado",
    },
    "livearea.header.size": {
        "es": "Tamaño",
        "en": "Size",
        "pt": "Tamanho",
    },
    "livearea.header.description": {
        "es": "Descripción",
        "en": "Description",
        "pt": "Descrição",
    },
    "livearea.status_present": {
        "es": "PRESENTE",
        "en": "PRESENT",
        "pt": "PRESENTE",
    },
    "livearea.status_missing": {
        "es": "FALTA",
        "en": "MISSING",
        "pt": "FALTA",
    },
    "livearea.mode_ok_fallback": {
        "es": "OK",
        "en": "OK",
        "pt": "OK",
    },
    "livearea.mode_prompt_title": {
        "es": "Modo de ajuste:",
        "en": "Resize mode:",
        "pt": "Modo de ajuste:",
    },
    "livearea.mode_option_crop": {
        "es": "1) Recorte centrado (Crop) {bold}[Recomendado]{reset}",
        "en": "1) Centered crop (Crop) {bold}[Recommended]{reset}",
        "pt": "1) Recorte centralizado (Crop) {bold}[Recomendado]{reset}",
    },
    "livearea.mode_option_fit": {
        "es": "2) Contener completo (Fit, con márgenes)",
        "en": "2) Contain fully (Fit, with margins)",
        "pt": "2) Conter completo (Fit, com margens)",
    },
    "livearea.mode_option_stretch": {
        "es": "3) Estirar directo (Stretch)",
        "en": "3) Stretch directly (Stretch)",
        "pt": "3) Esticar direto (Stretch)",
    },
    "livearea.mode_input_prompt": {
        "es": "Modo [1-3] (Default 1): ",
        "en": "Mode [1-3] (Default 1): ",
        "pt": "Modo [1-3] (Padrão 1): ",
    },
    "livearea.asset_flow_header": {
        "es": "📌 {filename} ({desc})",
        "en": "📌 {filename} ({desc})",
        "pt": "📌 {filename} ({desc})",
    },
    "livearea.drag_drop_tip": {
        "es": "Tip: podés arrastrar la imagen desde el Finder directamente acá.",
        "en": "Tip: you can drag the image straight from Finder here.",
        "pt": "Dica: você pode arrastar a imagem direto do Finder para aqui.",
    },
    "livearea.image_path_prompt": {
        "es": "🖼️  Ruta de la imagen original (Enter para omitir):",
        "en": "🖼️  Path to the original image (Enter to skip):",
        "pt": "🖼️  Caminho da imagem original (Enter para pular):",
    },
    "livearea.image_loaded": {
        "es": "✓ Imagen cargada: {width}x{height} ({format}, {mode})",
        "en": "✓ Image loaded: {width}x{height} ({format}, {mode})",
        "pt": "✓ Imagem carregada: {width}x{height} ({format}, {mode})",
    },
    "livearea.image_read_error": {
        "es": "❌ Error al leer la imagen: {error}",
        "en": "❌ Error reading the image: {error}",
        "pt": "❌ Erro ao ler a imagem: {error}",
    },
    "livearea.conversion_error": {
        "es": "❌ Error en la conversión: {error}",
        "en": "❌ Error during conversion: {error}",
        "pt": "❌ Erro na conversão: {error}",
    },
    "livearea.batch_title": {
        "es": "📦 Conversión en lote:",
        "en": "📦 Batch conversion:",
        "pt": "📦 Conversão em lote:",
    },
    "livearea.batch_image_prompt": {
        "es": "🖼️  Imagen para {filename} ({name}) [Enter omitir]:",
        "en": "🖼️  Image for {filename} ({name}) [Enter to skip]:",
        "pt": "🖼️  Imagem para {filename} ({name}) [Enter para pular]:",
    },
    "livearea.batch_error": {
        "es": "❌ Error en {filename}: {error}",
        "en": "❌ Error on {filename}: {error}",
        "pt": "❌ Erro em {filename}: {error}",
    },
    "livearea.item_bg0": {
        "es": "Adaptar {bold}bg0.png{reset}   (840x500 -- Fondo LiveArea)",
        "en": "Adapt {bold}bg0.png{reset}   (840x500 -- LiveArea background)",
        "pt": "Adaptar {bold}bg0.png{reset}   (840x500 -- Fundo LiveArea)",
    },
    "livearea.item_pic0": {
        "es": "Adaptar {bold}pic0.png{reset}  (960x544 -- Splash / Inicio)",
        "en": "Adapt {bold}pic0.png{reset}  (960x544 -- Splash / Startup)",
        "pt": "Adaptar {bold}pic0.png{reset}  (960x544 -- Splash / Início)",
    },
    "livearea.item_icon0": {
        "es": "Adaptar {bold}icon0.png{reset} (128x128 -- Icono de burbuja)",
        "en": "Adapt {bold}icon0.png{reset} (128x128 -- Bubble icon)",
        "pt": "Adaptar {bold}icon0.png{reset} (128x128 -- Ícone de bolha)",
    },
    "livearea.item_startup": {
        "es": "Adaptar {bold}startup.png{reset} (280x158 -- Banner de compuerta)",
        "en": "Adapt {bold}startup.png{reset} (280x158 -- Gate banner)",
        "pt": "Adaptar {bold}startup.png{reset} (280x158 -- Banner do portão)",
    },
    "livearea.item_batch_all": {
        "es": "Adaptar TODOS los assets en lote",
        "en": "Adapt ALL assets in batch",
        "pt": "Adaptar TODOS os assets em lote",
    },
    "livearea.menu_title": {
        "es": "LiveArea -- {game_name}",
        "en": "LiveArea -- {game_name}",
        "pt": "LiveArea -- {game_name}",
    },
    "livearea.breadcrumb": {
        "es": "{game_name} › LiveArea",
        "en": "{game_name} › LiveArea",
        "pt": "{game_name} › LiveArea",
    },
}
i18n.register(STRINGS)


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
        print(f"  {C.DIM}{t('livearea.backup_saved', name=bak.name)}{C.RESET}")

    print(f"\n{t('livearea.processing', bold=C.BOLD, reset=C.RESET, cyan=C.CYAN, input_name=Path(input_path).name, green=C.GREEN, filename=spec['filename'], spec_name=t(spec['name']))}")

    with Image.open(input_path) as src:
        print(f"  {t('livearea.input_dims', dim=C.DIM, reset=C.RESET, width=src.size[0], height=src.size[1], mode=src.mode)}")
        resized = _resize(src, spec["width"], spec["height"], mode=mode)
        print(f"  {t('livearea.adjust', dim=C.DIM, reset=C.RESET, mode=mode, width=spec['width'], height=spec['height'])}")
        indexed = _to_8bit_indexed(resized, dither=dither)
        indexed.save(out_file, format="PNG", optimize=True)

    size_kb = out_file.stat().st_size / 1024
    ok = size_kb <= spec["max_kb"]
    icon = "✅" if ok else "⚠️ "
    color = C.GREEN if ok else C.YELLOW
    print(f"  {icon} {t('livearea.saved', bold=C.BOLD, reset=C.RESET, out_file=out_file)}")
    print(f"  {color}{t('livearea.size_info', size_kb=size_kb, max_kb=spec['max_kb'])}{C.RESET}")
    if not ok:
        print(f"  {C.YELLOW}{t('livearea.exceeds_limit')}{C.RESET}")
    return out_file


def _render_status(dest_dir):
    print(f"{C.DIM}{t('livearea.dest_dir', dest_dir=dest_dir)}{C.RESET}\n")
    print(f"{C.BOLD}{t('livearea.status_title')}{C.RESET}")
    header_asset = t("livearea.header.asset")
    header_dim = t("livearea.header.dimensions")
    header_status = t("livearea.header.status")
    header_size = t("livearea.header.size")
    header_desc = t("livearea.header.description")
    print(f"  {header_asset:<12} {header_dim:<14} {header_status:<10} {header_size:<18} {header_desc}")
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
                dim, mode = f"{spec['width']}x{spec['height']}", t("livearea.mode_ok_fallback")
            kb_color = C.GREEN if kb <= spec["max_kb"] else C.YELLOW
            status, size_info = f"{C.GREEN}{t('livearea.status_present')}{C.RESET}", f"{kb_color}{kb:.1f} KB ({mode}){C.RESET}"
        else:
            dim = f"{spec['width']}x{spec['height']}"
            status, size_info = f"{C.RED}{t('livearea.status_missing')}{C.RESET}", f"{C.DIM}--{C.RESET}"
        print(f"  {C.BOLD}{spec['filename']:<12}{C.RESET} {dim:<14} {status:<19} {size_info:<27} {C.DIM}{t(spec['name'])}{C.RESET}")
    print()


def _pick_mode():
    print(f"{C.BOLD}{t('livearea.mode_prompt_title')}{C.RESET}")
    print(f"  {C.GREEN}{t('livearea.mode_option_crop', bold=C.BOLD, reset=C.RESET)}{C.RESET}")
    print(f"  {C.GREEN}{t('livearea.mode_option_fit')}{C.RESET}")
    print(f"  {C.GREEN}{t('livearea.mode_option_stretch')}{C.RESET}")
    choice = input(t("livearea.mode_input_prompt")).strip()
    return {"1": "crop", "2": "fit", "3": "stretch"}.get(choice, "crop")


def _asset_flow(asset_type, dest_dir):
    spec = VITA_SPECS[asset_type]
    print(f"\n{C.BOLD}{t('livearea.asset_flow_header', filename=spec['filename'], desc=t(spec['desc']))}{C.RESET}")
    print(f"{C.DIM}{t('livearea.drag_drop_tip')}{C.RESET}")
    raw = tui.input_path(t("livearea.image_path_prompt"), allow_blank=True)
    if not raw:
        return
    try:
        with Image.open(raw) as test_img:
            print(f"  {C.GREEN}{t('livearea.image_loaded', width=test_img.size[0], height=test_img.size[1], format=test_img.format, mode=test_img.mode)}{C.RESET}")
    except Exception as e:
        print(f"{C.RED}{t('livearea.image_read_error', error=e)}{C.RESET}")
        return
    mode = _pick_mode()
    try:
        process_asset(raw, asset_type, dest_dir, mode=mode, dither=True, backup=True)
    except Exception as e:
        print(f"{C.RED}{t('livearea.conversion_error', error=e)}{C.RESET}")


def livearea_menu(project_cfg):
    project_dir = Path(project_cfg["_project_dir"])
    dest_dir = project_dir / "extras" / "livearea"

    def make_flow(asset_type):
        return lambda: _asset_flow(asset_type, dest_dir)

    def batch_flow():
        print(f"\n{C.BOLD}{t('livearea.batch_title')}{C.RESET}")
        for atype in VITA_SPECS:
            spec = VITA_SPECS[atype]
            raw = tui.input_path(t("livearea.batch_image_prompt", filename=spec['filename'], name=t(spec['name'])), allow_blank=True)
            if not raw:
                continue
            try:
                process_asset(raw, atype, dest_dir, mode="crop", dither=True, backup=True)
            except Exception as e:
                print(f"{C.RED}{t('livearea.batch_error', filename=spec['filename'], error=e)}{C.RESET}")

    items = [
        (t("livearea.item_bg0", bold=C.BOLD, reset=C.RESET), make_flow("bg0")),
        (t("livearea.item_pic0", bold=C.BOLD, reset=C.RESET), make_flow("pic0")),
        (t("livearea.item_icon0", bold=C.BOLD, reset=C.RESET), make_flow("icon0")),
        (t("livearea.item_startup", bold=C.BOLD, reset=C.RESET), make_flow("startup")),
        (t("livearea.item_batch_all"), batch_flow),
    ]

    def header():
        _render_status(dest_dir)

    tui.run_menu(t("livearea.menu_title", game_name=project_cfg['game_name']), items,
                 breadcrumb=t("livearea.breadcrumb", game_name=project_cfg['game_name']), icon="🎨",
                 header_extra=header)
