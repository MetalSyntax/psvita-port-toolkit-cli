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

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
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
        "es": "{bold}Procesando:{reset} '{cyan}{input_name}{reset}' -> '{green}{filename}{reset}' ({spec_name})",
        "en": "{bold}Processing:{reset} '{cyan}{input_name}{reset}' -> '{green}{filename}{reset}' ({spec_name})",
        "pt": "{bold}Processando:{reset} '{cyan}{input_name}{reset}' -> '{green}{filename}{reset}' ({spec_name})",
    },
    "livearea.input_dims": {
        "es": "{dim}Entrada:{reset} {width}x{height} ({mode})",
        "en": "{dim}Input:{reset} {width}x{height} ({mode})",
        "pt": "{dim}Entrada:{reset} {width}x{height} ({mode})",
    },
    "livearea.adjust": {
        "es": "{dim}Ajuste ({mode}):{reset} {width}x{height}",
        "en": "{dim}Adjustment ({mode}):{reset} {width}x{height}",
        "pt": "{dim}Ajuste ({mode}):{reset} {width}x{height}",
    },
    "livearea.saved": {
        "es": "{bold}Guardado:{reset} {out_file}",
        "en": "{bold}Saved:{reset} {out_file}",
        "pt": "{bold}Salvo:{reset} {out_file}",
    },
    "livearea.size_info": {
        "es": "Tamaño: {size_kb:.2f} KB (límite {max_kb} KB)",
        "en": "Size: {size_kb:.2f} KB (limit {max_kb} KB)",
        "pt": "Tamanho: {size_kb:.2f} KB (limite {max_kb} KB)",
    },
    "livearea.exceeds_limit": {
        "es": "Supera el límite -- LiveArea podría no cargarlo.",
        "en": "Exceeds the limit -- LiveArea might fail to load it.",
        "pt": "Excede o limite -- a LiveArea pode não carregá-lo.",
    },
    "livearea.dest_dir": {
        "es": "Directorio destino: {dest_dir}",
        "en": "Destination directory: {dest_dir}",
        "pt": "Diretório de destino: {dest_dir}",
    },
    "livearea.status_title": {
        "es": "Estado actual de los assets:",
        "en": "Current status of the assets:",
        "pt": "Estado atual dos assets:",
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
        "es": "Recorte centrado (Crop) {bold}[Recomendado]{reset}",
        "en": "Centered crop (Crop) {bold}[Recommended]{reset}",
        "pt": "Recorte centralizado (Crop) {bold}[Recomendado]{reset}",
    },
    "livearea.mode_option_fit": {
        "es": "Contener completo (Fit, con márgenes)",
        "en": "Contain fully (Fit, with margins)",
        "pt": "Conter completo (Fit, com margens)",
    },
    "livearea.mode_option_stretch": {
        "es": "Estirar directo (Stretch)",
        "en": "Stretch directly (Stretch)",
        "pt": "Esticar direto (Stretch)",
    },
    "livearea.asset_flow_header": {
        "es": "{filename} ({desc})",
        "en": "{filename} ({desc})",
        "pt": "{filename} ({desc})",
    },
    "livearea.drag_drop_tip": {
        "es": "Tip: podés arrastrar la imagen desde el Finder directamente acá.",
        "en": "Tip: you can drag the image straight from Finder here.",
        "pt": "Dica: você pode arrastar a imagem direto do Finder para aqui.",
    },
    "livearea.image_path_prompt": {
        "es": "Ruta de la imagen original (Enter para omitir):",
        "en": "Path to the original image (Enter to skip):",
        "pt": "Caminho da imagem original (Enter para pular):",
    },
    "livearea.image_loaded": {
        "es": "Imagen cargada: {width}x{height} ({format}, {mode})",
        "en": "Image loaded: {width}x{height} ({format}, {mode})",
        "pt": "Imagem carregada: {width}x{height} ({format}, {mode})",
    },
    "livearea.image_read_error": {
        "es": "Error al leer la imagen: {error}",
        "en": "Error reading the image: {error}",
        "pt": "Erro ao ler a imagem: {error}",
    },
    "livearea.conversion_error": {
        "es": "Error en la conversión: {error}",
        "en": "Error during conversion: {error}",
        "pt": "Erro na conversão: {error}",
    },
    "livearea.batch_title": {
        "es": "Conversión en lote:",
        "en": "Batch conversion:",
        "pt": "Conversão em lote:",
    },
    "livearea.batch_image_prompt": {
        "es": "Imagen para {filename} ({name}) [Enter omitir]:",
        "en": "Image for {filename} ({name}) [Enter to skip]:",
        "pt": "Imagem para {filename} ({name}) [Enter para pular]:",
    },
    "livearea.batch_error": {
        "es": "Error en {filename}: {error}",
        "en": "Error on {filename}: {error}",
        "pt": "Erro em {filename}: {error}",
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
    "livearea.item_template": {
        "es": "Generar {bold}template.xml{reset} (layout 'gate' -- el mismo que usan todos los ports)",
        "en": "Generate {bold}template.xml{reset} ('gate' layout -- the same one every port uses)",
        "pt": "Gerar {bold}template.xml{reset} (layout 'gate' -- o mesmo usado por todos os ports)",
    },
    "livearea.item_bgm": {
        "es": "Convertir audio de fondo a {bold}bgm.at9{reset}",
        "en": "Convert background audio to {bold}bgm.at9{reset}",
        "pt": "Converter áudio de fundo para {bold}bgm.at9{reset}",
    },
    "livearea.item_validate": {
        "es": "Validar assets antes de empaquetar (tamaños, formato, referencias)",
        "en": "Validate assets before packaging (sizes, format, references)",
        "pt": "Validar assets antes de empacotar (tamanhos, formato, referências)",
    },
    "livearea.template_written": {
        "es": "template.xml escrito en {out_file}",
        "en": "template.xml written to {out_file}",
        "pt": "template.xml escrito em {out_file}",
    },
    "livearea.bgm_path_prompt": {
        "es": "Ruta al audio de fondo (.wav/.mp3/.at9, Enter para omitir):",
        "en": "Path to the background audio (.wav/.mp3/.at9, Enter to skip):",
        "pt": "Caminho para o áudio de fundo (.wav/.mp3/.at9, Enter para pular):",
    },
    "livearea.bgm_copied": {
        "es": "bgm.at9 copiado a {out_file} (ya estaba en formato AT9)",
        "en": "bgm.at9 copied to {out_file} (was already AT9)",
        "pt": "bgm.at9 copiado para {out_file} (já estava em formato AT9)",
    },
    "livearea.bgm_encoder_missing": {
        "es": "No se encontró un encoder AT9 (at9tool/atrac9tool) en VITASDK ni en PATH -- ATRAC9 es un formato propietario de Sony, no lo codifica ffmpeg. Conseguí 'atrac9tool' del SDK oficial de PS4/Vita, o pasá un .at9 ya codificado.",
        "en": "No AT9 encoder (at9tool/atrac9tool) found in VITASDK or PATH -- ATRAC9 is a proprietary Sony format, ffmpeg can't encode it. Get 'atrac9tool' from the official PS4/Vita SDK, or pass an already-encoded .at9 file.",
        "pt": "Nenhum encoder AT9 (at9tool/atrac9tool) encontrado no VITASDK ou no PATH -- ATRAC9 é um formato proprietário da Sony, o ffmpeg não o codifica. Consiga o 'atrac9tool' do SDK oficial de PS4/Vita, ou passe um .at9 já codificado.",
    },
    "livearea.bgm_encoding": {
        "es": "[*] Codificando: {cmd}",
        "en": "[*] Encoding: {cmd}",
        "pt": "[*] Codificando: {cmd}",
    },
    "livearea.bgm_encode_failed": {
        "es": "Falló la codificación a AT9: {error}",
        "en": "AT9 encoding failed: {error}",
        "pt": "Falha ao codificar para AT9: {error}",
    },
    "livearea.bgm_saved": {
        "es": "bgm.at9 generado en {out_file}",
        "en": "bgm.at9 generated at {out_file}",
        "pt": "bgm.at9 gerado em {out_file}",
    },
    "livearea.validate_title": {
        "es": "Validando assets de LiveArea antes de empaquetar:",
        "en": "Validating LiveArea assets before packaging:",
        "pt": "Validando assets da LiveArea antes de empacotar:",
    },
    "livearea.validate_missing": {
        "es": "falta -- no existe en el directorio",
        "en": "missing -- doesn't exist in the directory",
        "pt": "faltando -- não existe no diretório",
    },
    "livearea.validate_too_big": {
        "es": "{size_kb:.1f} KB supera el límite de {max_kb} KB -- vita-pack-vpk podría rechazarlo",
        "en": "{size_kb:.1f} KB exceeds the {max_kb} KB limit -- vita-pack-vpk might reject it",
        "pt": "{size_kb:.1f} KB excede o limite de {max_kb} KB -- o vita-pack-vpk pode rejeitá-lo",
    },
    "livearea.validate_bad_format": {
        "es": "formato inesperado ({width}x{height}, modo {mode}) -- correr 'Adaptar' de nuevo",
        "en": "unexpected format ({width}x{height}, {mode} mode) -- run 'Adapt' again",
        "pt": "formato inesperado ({width}x{height}, modo {mode}) -- execute 'Adaptar' novamente",
    },
    "livearea.validate_ok": {
        "es": "OK ({size_kb:.1f} KB, 8-bit indexado)",
        "en": "OK ({size_kb:.1f} KB, 8-bit indexed)",
        "pt": "OK ({size_kb:.1f} KB, 8-bit indexado)",
    },
    "livearea.validate_ok_generic": {
        "es": "OK",
        "en": "OK",
        "pt": "OK",
    },
    "livearea.validate_template_refs_missing": {
        "es": "referencia imagen(es) inexistente(s): {refs}",
        "en": "references non-existent image(s): {refs}",
        "pt": "referencia imagem(ns) inexistente(s): {refs}",
    },
    "livearea.validate_template_invalid": {
        "es": "XML inválido: {error}",
        "en": "invalid XML: {error}",
        "pt": "XML inválido: {error}",
    },
    "livearea.validate_summary_ok": {
        "es": "[+] Todo listo para empaquetar con vita-pack-vpk.",
        "en": "[+] Everything's ready to package with vita-pack-vpk.",
        "pt": "[+] Tudo pronto para empacotar com o vita-pack-vpk.",
    },
    "livearea.validate_summary_fail": {
        "es": "[-] Corregí lo marcado con antes de empaquetar.",
        "en": "[-] Fix what's marked before packaging.",
        "pt": "[-] Corrija o que está marcado com antes de empacotar.",
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
    marker = "[+]" if ok else "[!]"
    color = C.GREEN if ok else C.YELLOW
    print(f"  {marker} {t('livearea.saved', bold=C.BOLD, reset=C.RESET, out_file=out_file)}")
    print(f"  {color}{t('livearea.size_info', size_kb=size_kb, max_kb=spec['max_kb'])}{C.RESET}")
    if not ok:
        print(f"  {C.YELLOW}{t('livearea.exceeds_limit')}{C.RESET}")
    return out_file


# ---------------------------------------------------------------------------
# template.xml
# ---------------------------------------------------------------------------

# The "a1 gate" style: tap-anywhere-to-launch, no interactive tiles/buttons.
# Verified byte-for-byte against every real port's shipped template.xml in
# this collection (Zenonia 2/3/4, ILLUSIA 1/2, Inotia 3, Advena, and the
# soloader-boilerplate scaffold itself) -- see docs/dev-notes/livearea.md for
# why this is the only layout offered.
TEMPLATE_XML_CONTENT = (
    '<?xml version="1.0" encoding="utf-8"?>\n\n'
    '<livearea style="a1" format-ver="01.00" content-rev="1">\n'
    '\t<livearea-background>\n'
    '\t\t<image>bg0.png</image>\n'
    '\t</livearea-background>\n\n'
    '\t<gate>\n'
    '\t\t<startup-image>startup.png</startup-image>\n'
    '\t</gate>\n'
    '</livearea>\n'
)


def generate_template_xml(dest_dir, backup=True):
    """!
    @brief Write the standard "a1 gate" `template.xml` (tap-to-launch, no
           interactive tiles) to `dest_dir`.
    @param dest_dir Project's `extras/livearea/` directory (created if missing).
    @param backup If `True` and a `template.xml` already exists there, save
           it as `template.xml.bak` first.
    @return `Path` to the written `template.xml`.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_file = dest_dir / "template.xml"
    if out_file.exists() and backup:
        bak = out_file.with_suffix(".xml.bak")
        shutil.copy2(out_file, bak)
        print(f"  {C.DIM}{t('livearea.backup_saved', name=bak.name)}{C.RESET}")
    out_file.write_text(TEMPLATE_XML_CONTENT, encoding="utf-8")
    print(f"  [+] {t('livearea.template_written', out_file=out_file)}")
    return out_file


# ---------------------------------------------------------------------------
# Background music (bgm.at9)
# ---------------------------------------------------------------------------

_AT9_ENCODER_CANDIDATES = ("at9tool", "atrac9tool", "psvat9encoder")


def _find_at9_encoder(global_cfg=None):
    """!
    @brief Look for an ATRAC9 encoder binary in `$VITASDK/bin` or `PATH`.
    @param global_cfg Global config dict; reads `vitasdk`.
    @return Full path to the encoder if found, `None` otherwise.
    @note ATRAC9 is a proprietary Sony codec -- VITASDK doesn't bundle an
          encoder for it, and ffmpeg has no ATRAC9 encoder either (confirmed:
          `ffmpeg -encoders` lists no `atrac9`/`at9`). A real encoder
          (`atrac9tool`, from the official PS4/Vita SDK) has to come from
          elsewhere -- see `docs/dev-notes/livearea.md`.
    """
    vitasdk_bin = os.path.join((global_cfg or {}).get("vitasdk", ""), "bin") if global_cfg else ""
    for name in _AT9_ENCODER_CANDIDATES:
        if vitasdk_bin:
            candidate = Path(vitasdk_bin) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        found = shutil.which(name)
        if found:
            return found
    return None


def convert_bgm_to_at9(input_path, dest_dir, global_cfg=None):
    """!
    @brief Produce `<dest_dir>/bgm.at9`, the PS Vita LiveArea background
           music file (auto-played by the OS when present -- no `template.xml`
           reference needed).
    @param input_path Source audio file. If already `.at9`, it's copied as-is;
           otherwise an ATRAC9 encoder is required (see `_find_at9_encoder()`).
    @param dest_dir Project's `extras/livearea/` directory (created if missing).
    @param global_cfg Global config dict; reads `vitasdk` to look for an encoder.
    @return `Path` to `bgm.at9` on success, `None` if no encoder is available
            or encoding failed (prints the reason either way).
    """
    input_path = Path(input_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_file = dest_dir / "bgm.at9"

    if input_path.suffix.lower() == ".at9":
        shutil.copy2(input_path, out_file)
        print(f"  [+] {t('livearea.bgm_copied', out_file=out_file)}")
        return out_file

    encoder = _find_at9_encoder(global_cfg)
    if not encoder:
        print(f"  {C.YELLOW}{t('livearea.bgm_encoder_missing')}{C.RESET}")
        return None

    cmd = [encoder, str(input_path), str(out_file)]
    print(t("livearea.bgm_encoding", cmd=" ".join(cmd)))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_file.exists():
        print(f"  {C.RED}{t('livearea.bgm_encode_failed', error=(r.stderr or r.stdout).strip())}{C.RESET}")
        return None
    print(f"  [+] {t('livearea.bgm_saved', out_file=out_file)}")
    return out_file


# ---------------------------------------------------------------------------
# Pre-packaging validation
# ---------------------------------------------------------------------------

def validate_livearea_dir(dest_dir):
    """!
    @brief Check every LiveArea asset (4 PNGs, `template.xml`, optional
           `bgm.at9`) for what would make `vita-pack-vpk` choke or LiveArea
           fail to render on the console.
    @param dest_dir Project's `extras/livearea/` directory.
    @return list of `(name, ok, detail)` tuples, one per asset checked.
    """
    dest_dir = Path(dest_dir)
    checks = []

    for spec in VITA_SPECS.values():
        fpath = dest_dir / spec["filename"]
        if not fpath.exists():
            checks.append((spec["filename"], False, t("livearea.validate_missing")))
            continue
        size_kb = fpath.stat().st_size / 1024
        if size_kb > spec["max_kb"]:
            checks.append((spec["filename"], False,
                            t("livearea.validate_too_big", size_kb=size_kb, max_kb=spec["max_kb"])))
            continue
        try:
            with Image.open(fpath) as im:
                if im.size != (spec["width"], spec["height"]) or im.mode != "P":
                    checks.append((spec["filename"], False, t(
                        "livearea.validate_bad_format", width=im.size[0], height=im.size[1], mode=im.mode)))
                    continue
        except Exception as e:  # noqa: BLE001 -- any unreadable image is a validation failure, not a crash
            checks.append((spec["filename"], False, str(e)))
            continue
        checks.append((spec["filename"], True, t("livearea.validate_ok", size_kb=size_kb)))

    template_path = dest_dir / "template.xml"
    if not template_path.exists():
        checks.append(("template.xml", False, t("livearea.validate_missing")))
    else:
        try:
            root = ET.parse(template_path).getroot()
            bg_img = root.findtext("livearea-background/image")
            startup_img = root.findtext("gate/startup-image")
            missing_refs = [name for name in (bg_img, startup_img) if name and not (dest_dir / name).exists()]
            if missing_refs:
                checks.append(("template.xml", False,
                                t("livearea.validate_template_refs_missing", refs=", ".join(missing_refs))))
            else:
                checks.append(("template.xml", True, t("livearea.validate_ok_generic")))
        except ET.ParseError as e:
            checks.append(("template.xml", False, t("livearea.validate_template_invalid", error=e)))

    bgm_path = dest_dir / "bgm.at9"
    if bgm_path.exists():
        checks.append(("bgm.at9", True, t("livearea.validate_ok_generic")))

    return checks


def print_validation(checks):
    """!
    @brief Print `validate_livearea_dir()`'s results and a pass/fail summary line.
    @param checks Result of `validate_livearea_dir()`.
    @return `True` if every check passed.
    """
    print(f"{C.BOLD}{t('livearea.validate_title')}{C.RESET}")
    all_ok = True
    for name, passed, detail in checks:
        color = C.GREEN if passed else C.RED
        label = "OK" if passed else "FAIL"
        print(f"  [{color}{label}{C.RESET}] {color}{name}{C.RESET} -- {detail}")
        all_ok = all_ok and passed
    print()
    if all_ok:
        print(f"{C.GREEN}{t('livearea.validate_summary_ok')}{C.RESET}")
    else:
        print(f"{C.RED}{t('livearea.validate_summary_fail')}{C.RESET}")
    return all_ok


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
    modes = [
        ("crop", t("livearea.mode_option_crop", bold=C.BOLD, reset=C.RESET)),
        ("fit", t("livearea.mode_option_fit")),
        ("stretch", t("livearea.mode_option_stretch")),
    ]
    chosen = tui.select_list(t("livearea.mode_prompt_title"), modes, label_fn=lambda m: m[1])
    return chosen[0] if chosen else "crop"


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

    def do_template():
        generate_template_xml(dest_dir)

    def do_bgm():
        raw = tui.input_path(t("livearea.bgm_path_prompt"), must_exist=True, allow_blank=True)
        if raw:
            convert_bgm_to_at9(raw, dest_dir)

    def do_validate():
        print_validation(validate_livearea_dir(dest_dir))

    items = [
        (t("livearea.item_bg0", bold=C.BOLD, reset=C.RESET), make_flow("bg0")),
        (t("livearea.item_pic0", bold=C.BOLD, reset=C.RESET), make_flow("pic0")),
        (t("livearea.item_icon0", bold=C.BOLD, reset=C.RESET), make_flow("icon0")),
        (t("livearea.item_startup", bold=C.BOLD, reset=C.RESET), make_flow("startup")),
        (t("livearea.item_batch_all"), batch_flow),
        (t("livearea.item_template", bold=C.BOLD, reset=C.RESET), do_template),
        (t("livearea.item_bgm", bold=C.BOLD, reset=C.RESET), do_bgm),
        (t("livearea.item_validate"), do_validate),
    ]

    def header():
        _render_status(dest_dir)

    tui.run_menu(t("livearea.menu_title", game_name=project_cfg['game_name']), items,
                 breadcrumb=t("livearea.breadcrumb", game_name=project_cfg['game_name']),
                 header_extra=header)
