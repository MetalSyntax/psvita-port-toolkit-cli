"""!
@file asset_transcoder.py
@brief Batch texture/audio transcoding to formats a PS Vita loads and holds
       in RAM more cheaply than the Android originals.

@details
Two independent pipelines, both honest about where this toolkit's control
ends:

1. **Textures.** Pillow (this project's one hard dependency) can decode
   PNG/JPEG/BMP/etc. just fine, but NOT Android's compressed texture
   containers (ETC1/ETC2/ASTC/KTX) -- there's no pure-Python decoder for
   those, and faking one would be dishonest. So this pipeline only ever
   claims to handle whatever Pillow can actually open. For every such
   texture it ALWAYS produces a `.rawtex` (this toolkit's own tiny,
   fully-documented container -- see `_write_rawtex()`) with a precomputed
   mip chain. `generate_rawtex_loader()` also writes real, reviewable C
   (`rawtex_loader.c`/`.h`) that reads that exact container and calls
   `sceGxmTextureInitLinear()` with it -- the same "generate C the porter
   links in" pattern every other module in this toolkit uses
   (`so_patcher.py`'s stubs, `mem_profiler.py`/`perf_telemetry.py`'s hooks),
   so the format goes from "documented" to "documented AND has working
   loading code", not left as a spec with nothing reading it. If a real
   GPU-texture-compression tool (`PVRTexToolCLI` or `compressonatorcli` --
   both real, independently downloadable tools, never bundled) is found on
   `PATH`, it ALSO attempts a genuinely hardware-compressed `.pvr`/`.dds` as
   a bonus -- best-effort, because this toolkit can't pin an exact CLI flag
   set across every version of a third-party tool it doesn't ship. See
   `docs/dev-notes/asset_transcoder.md`.
2. **Audio.** Reuses `livearea._find_at9_encoder()` unchanged (same
   "there's no free ATRAC9 encoder, bring your own" honesty already
   documented there) to batch-convert a whole folder instead of just
   `bgm.at9`.
"""

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from . import i18n
from . import livearea
from . import tui
from .i18n import t
from .tui import C

STRINGS = {
    "asset_transcoder.menu_title": {
        "es": "Transcodificador de Assets Nativos",
        "en": "Native Asset Transcoder",
        "pt": "Transcodificador de Assets Nativos",
    },
    "asset_transcoder.menu_textures": {
        "es": "Transcodificar texturas (carpeta -> .rawtex + mipmaps, PVR/DDS si hay encoder)",
        "en": "Transcode textures (folder -> .rawtex + mipmaps, PVR/DDS if an encoder is present)",
        "pt": "Transcodificar texturas (pasta -> .rawtex + mipmaps, PVR/DDS se houver encoder)",
    },
    "asset_transcoder.menu_audio": {
        "es": "Transcodificar audio en lote (carpeta -> .at9)",
        "en": "Batch-transcode audio (folder -> .at9)",
        "pt": "Transcodificar áudio em lote (pasta -> .at9)",
    },
    "asset_transcoder.menu_gen_loader": {
        "es": "Generar rawtex_loader.c/.h (código real para cargar .rawtex con sceGxmTextureInitLinear)",
        "en": "Generate rawtex_loader.c/.h (real code to load .rawtex via sceGxmTextureInitLinear)",
        "pt": "Gerar rawtex_loader.c/.h (código real para carregar .rawtex com sceGxmTextureInitLinear)",
    },
    "asset_transcoder.loader_generated": {
        "es": "[+] Loader de .rawtex generado en {header}/{source}",
        "en": "[+] .rawtex loader generated at {header}/{source}",
        "pt": "[+] Loader de .rawtex gerado em {header}/{source}",
    },
    "asset_transcoder.src_prompt": {
        "es": "Carpeta de origen (relativa al proyecto) [assets]: ",
        "en": "Source folder (relative to the project) [assets]: ",
        "pt": "Pasta de origem (relativa ao projeto) [assets]: ",
    },
    "asset_transcoder.no_images": {
        "es": "[*] No se encontraron imágenes que Pillow pueda abrir en {src_dir}.",
        "en": "[*] No images Pillow can open were found in {src_dir}.",
        "pt": "[*] Nenhuma imagem que o Pillow consiga abrir foi encontrada em {src_dir}.",
    },
    "asset_transcoder.no_audio": {
        "es": "[*] No se encontraron .wav/.mp3/.ogg/.at9 en {src_dir}.",
        "en": "[*] No .wav/.mp3/.ogg/.at9 files were found in {src_dir}.",
        "pt": "[*] Nenhum .wav/.mp3/.ogg/.at9 foi encontrado em {src_dir}.",
    },
    "asset_transcoder.no_at9_encoder": {
        "es": "[!] No hay encoder ATRAC9 disponible -- ver docs/dev-notes/livearea.md. Se omite el lote de audio.",
        "en": "[!] No ATRAC9 encoder available -- see docs/dev-notes/livearea.md. Skipping the audio batch.",
        "pt": "[!] Nenhum encoder ATRAC9 disponível -- veja docs/dev-notes/livearea.md. Pulando o lote de áudio.",
    },
    "asset_transcoder.texture_done": {
        "es": "  [+] {name}: {mips} mip(s), {raw_kb} KB rawtex{compressed}",
        "en": "  [+] {name}: {mips} mip(s), {raw_kb} KB rawtex{compressed}",
        "pt": "  [+] {name}: {mips} mip(s), {raw_kb} KB rawtex{compressed}",
    },
    "asset_transcoder.texture_failed": {
        "es": "  [-] {name}: {error}",
        "en": "  [-] {name}: {error}",
        "pt": "  [-] {name}: {error}",
    },
    "asset_transcoder.audio_done": {
        "es": "  [+] {name} -> {out_name}",
        "en": "  [+] {name} -> {out_name}",
        "pt": "  [+] {name} -> {out_name}",
    },
    "asset_transcoder.audio_failed": {
        "es": "  [-] {name}: {error}",
        "en": "  [-] {name}: {error}",
        "pt": "  [-] {name}: {error}",
    },
    "asset_transcoder.summary": {
        "es": "[*] {ok} archivo(s) transcodificado(s), {failed} fallido(s).",
        "en": "[*] {ok} file(s) transcoded, {failed} failed.",
        "pt": "[*] {ok} arquivo(s) transcodificado(s), {failed} falharam.",
    },
    "asset_transcoder.compressed_backend": {
        "es": " + {name} (compresión GPU real, ver stderr si falla)",
        "en": " + {name} (real GPU compression, check stderr if it fails)",
        "pt": " + {name} (compressão GPU real, veja o stderr se falhar)",
    },
}
i18n.register(STRINGS)

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".gif")
_AUDIO_EXTENSIONS = (".wav", ".mp3", ".ogg", ".at9")

# Real, independently-downloadable GPU texture compression CLI tools -- never
# bundled with this toolkit. Checked in this order; the first one found on
# PATH is used. See the module docstring for why this stays best-effort.
_TEXTURE_ENCODER_CANDIDATES = ("PVRTexToolCLI", "compressonatorcli")

_RAWTEX_MAGIC = b"PVXR"
_MIN_MIP_SIZE = 4


def _find_texture_encoder():
    """!
    @brief Look for a real GPU texture-compression CLI tool on `PATH`.
    @return `(tool_name, full_path)`, or `(None, None)` if none found.
    """
    for name in _TEXTURE_ENCODER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return name, found
    return None, None


def _mip_chain(img):
    """!
    @brief Build a full mip chain (box-filter downscale by half each level)
           down to `_MIN_MIP_SIZE` pixels on the longest side.
    @param img RGBA `PIL.Image`.
    @return list of RGBA `PIL.Image`s, level 0 (full size) first.
    """
    levels = [img]
    w, h = img.size
    while max(w, h) > _MIN_MIP_SIZE:
        w, h = max(1, w // 2), max(1, h // 2)
        levels.append(levels[-1].resize((w, h), Image.BOX))
    return levels


def _write_rawtex(levels, out_path):
    """!
    @brief Write this toolkit's own `.rawtex` container: a small binary
           header followed by every mip level's raw RGBA8888 bytes back to
           back, plus a `.json` sidecar describing each level's offset --
           directly loadable via `sceGxmTextureInitLinear()` with
           `SCE_GXM_TEXTURE_FORMAT_U8U8U8U8_ABGR` and no runtime decode.
    @details Format: 4-byte magic `PVXR`, u32 mip count, then per mip a
           `(u32 width, u32 height, u32 byte_offset, u32 byte_length)`
           record, then the concatenated raw pixel bytes for every level in
           order. Documented in full in `docs/dev-notes/asset_transcoder.md`
           -- this is NOT Sony's proprietary GXT format, it's this
           toolkit's own, since GXT's real binary layout isn't public and
           GXM never actually requires it for homebrew (raw linear
           textures load fine via `sceGxmTextureInitLinear`).
    @param levels Result of `_mip_chain()`.
    @param out_path Destination `.rawtex` path.
    @return Total file size in bytes.
    """
    records = []
    blobs = []
    offset = 0
    for lvl in levels:
        data = lvl.tobytes("raw", "RGBA")
        records.append((lvl.width, lvl.height, offset, len(data)))
        blobs.append(data)
        offset += len(data)

    with open(out_path, "wb") as f:
        f.write(_RAWTEX_MAGIC)
        f.write(len(levels).to_bytes(4, "little"))
        for w, h, off, length in records:
            f.write(w.to_bytes(4, "little"))
            f.write(h.to_bytes(4, "little"))
            f.write(off.to_bytes(4, "little"))
            f.write(length.to_bytes(4, "little"))
        for blob in blobs:
            f.write(blob)

    sidecar = {
        "format": "rawtex-v1",
        "pixel_format": "RGBA8888",
        "gxm_format_hint": "SCE_GXM_TEXTURE_FORMAT_U8U8U8U8_ABGR",
        "mip_count": len(levels),
        "levels": [
            {"width": w, "height": h, "byte_offset": off, "byte_length": length}
            for w, h, off, length in records
        ],
    }
    Path(str(out_path) + ".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return out_path.stat().st_size


def _try_compressed_backend(src_path, dest_dir, encoder_name, encoder_path):
    """!
    @brief Best-effort bonus: also ask a real GPU-texture-compression tool
           for a hardware-compressed container, if one is installed.
    @param src_path Source image path.
    @param dest_dir Output directory.
    @param encoder_name One of `_TEXTURE_ENCODER_CANDIDATES`.
    @param encoder_path Full path to the encoder binary.
    @return `True` if the tool ran and produced output, `False` otherwise
            (never raises -- a failed/unsupported flag set on this
            particular tool version just means no bonus file, the
            `.rawtex` this function's caller already wrote stays valid).
    """
    out_path = Path(dest_dir) / f"{Path(src_path).stem}.pvr"
    if encoder_name == "PVRTexToolCLI":
        cmd = [encoder_path, "-i", str(src_path), "-o", str(out_path), "-f", "PVRTC1_4,UBN,lRGB", "-m"]
    else:  # compressonatorcli
        out_path = out_path.with_suffix(".dds")
        cmd = [encoder_path, "-fd", "BC1", str(src_path), str(out_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and out_path.exists()


def transcode_texture(src_path, dest_dir):
    """!
    @brief Transcode one image: always a `.rawtex` + mip chain, plus a
           best-effort real hardware-compressed file if an encoder is present.
    @param src_path Source image path (anything Pillow can open).
    @param dest_dir Output directory (created if missing).
    @return `(raw_size_bytes, mip_count, compressed_backend_name_or_None)`.
    @raise Exception Whatever Pillow raises for a file it can't decode --
           the caller (`transcode_texture_dir()`) catches this per-file so
           one bad asset doesn't abort the whole batch.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(src_path).convert("RGBA")
    levels = _mip_chain(img)
    out_path = dest_dir / f"{Path(src_path).stem}.rawtex"
    raw_size = _write_rawtex(levels, out_path)

    encoder_name, encoder_path = _find_texture_encoder()
    compressed = None
    if encoder_name and _try_compressed_backend(src_path, dest_dir, encoder_name, encoder_path):
        compressed = encoder_name
    return raw_size, len(levels), compressed


def transcode_texture_dir(src_dir, dest_dir):
    """!
    @brief Batch-transcode every Pillow-openable image in `src_dir`.
    @param src_dir Source directory (non-recursive).
    @param dest_dir Output directory.
    @return `(ok_count, failed_count)`.
    """
    src_dir = Path(src_dir)
    images = sorted(p for p in src_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTENSIONS)
    if not images:
        print(t("asset_transcoder.no_images", src_dir=src_dir))
        return 0, 0

    ok = failed = 0
    for img_path in images:
        try:
            raw_size, mips, compressed = transcode_texture(img_path, dest_dir)
        except Exception as e:  # noqa: BLE001 -- one bad/undecodable asset shouldn't abort the batch
            print(t("asset_transcoder.texture_failed", name=img_path.name, error=e))
            failed += 1
            continue
        suffix = t("asset_transcoder.compressed_backend", name=compressed) if compressed else ""
        print(t("asset_transcoder.texture_done", name=img_path.name, mips=mips,
                 raw_kb=round(raw_size / 1024, 1), compressed=suffix))
        ok += 1
    print(t("asset_transcoder.summary", ok=ok, failed=failed))
    return ok, failed


def transcode_audio_dir(src_dir, dest_dir, global_cfg=None):
    """!
    @brief Batch-convert every `.wav`/`.mp3`/`.ogg` in `src_dir` to `.at9`
           (already-`.at9` files are copied as-is), reusing
           `livearea._find_at9_encoder()`/its exact invocation convention.
    @param src_dir Source directory (non-recursive).
    @param dest_dir Output directory.
    @param global_cfg Global config dict; reads `vitasdk` to look for an encoder.
    @return `(ok_count, failed_count)`.
    """
    src_dir = Path(src_dir)
    dest_dir = Path(dest_dir)
    audio_files = sorted(p for p in src_dir.iterdir() if p.suffix.lower() in _AUDIO_EXTENSIONS)
    if not audio_files:
        print(t("asset_transcoder.no_audio", src_dir=src_dir))
        return 0, 0

    encoder = livearea._find_at9_encoder(global_cfg)
    if not encoder and any(p.suffix.lower() != ".at9" for p in audio_files):
        print(f"{C.YELLOW}{t('asset_transcoder.no_at9_encoder')}{C.RESET}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    for audio_path in audio_files:
        out_path = dest_dir / f"{audio_path.stem}.at9"
        try:
            if audio_path.suffix.lower() == ".at9":
                shutil.copy2(audio_path, out_path)
            else:
                if not encoder:
                    raise RuntimeError(t("asset_transcoder.no_at9_encoder"))
                r = subprocess.run([encoder, str(audio_path), str(out_path)], capture_output=True, text=True)
                if r.returncode != 0 or not out_path.exists():
                    raise RuntimeError((r.stderr or r.stdout).strip())
        except Exception as e:  # noqa: BLE001 -- one bad file shouldn't abort the batch
            print(t("asset_transcoder.audio_failed", name=audio_path.name, error=e))
            failed += 1
            continue
        print(t("asset_transcoder.audio_done", name=audio_path.name, out_name=out_path.name))
        ok += 1
    print(t("asset_transcoder.summary", ok=ok, failed=failed))
    return ok, failed


# ---------------------------------------------------------------------------
# .rawtex loader generation -- real, reviewable C reading this module's own
# container format, closing the loop from "documented" to "loadable".
# ---------------------------------------------------------------------------

def _rawtex_loader_header_lines():
    """!
    @brief Shared header comment block for both generated files.
    @return list of comment lines (no trailing newline).
    """
    return [
        "/* Auto-generated by psvita-toolkit -- .rawtex loader (see asset_transcoder.py). */",
        "/* Reads this toolkit's own documented .rawtex container (magic + per-mip offset  */",
        "/* table + raw RGBA8888 bytes) and wires it into a real SceGxmTexture. GXM API    */",
        "/* usage here is best-effort -- verify sceGxmTextureInitLinear()'s exact signature */",
        "/* and SCE_KERNEL_MEMBLOCK_TYPE_USER_RW_UNCACHE against YOUR vitasdk headers       */",
        "/* version before trusting this blindly. See docs/dev-notes/asset_transcoder.md.   */",
    ]


def generate_rawtex_loader(project_cfg, out_dir=None):
    """!
    @brief Generate `rawtex_loader.c` + `.h`: real C that reads a `.rawtex`
           file this module's own `_write_rawtex()` produced and initializes
           a `SceGxmTexture` from it via `sceGxmTextureInitLinear()`.
    @param project_cfg Per-project config dict.
    @param out_dir Directory to write the two files into; defaults to
           `<project_dir>/source` if it exists, else the project root (same
           convention as every other hook/stub generator in this toolkit).
    @return `Path` to the written `.c` file.
    """
    project_dir = Path(project_cfg["_project_dir"])

    header_lines = _rawtex_loader_header_lines() + [
        "",
        "#pragma once",
        "#include <psp2/gxm.h>",
        "#include <stddef.h>",
        "",
        "typedef struct {",
        "    void *gpu_mem;          /* SceKernelAllocMemBlock-backed, free with sceKernelFreeMemBlock */",
        "    SceUID mem_block_uid;",
        "} rawtex_handle_t;",
        "",
        "/* On success, fills *out_texture (ready for sceGxmSetFragmentTexture()) and",
        " * *out_handle (keep it alive as long as the texture is used; free with",
        " * rawtex_free()). Returns 0 on success, negative on failure. */",
        "int rawtex_load(const char *path, SceGxmTexture *out_texture, rawtex_handle_t *out_handle);",
        "void rawtex_free(rawtex_handle_t *handle);",
        "",
    ]

    source_lines = _rawtex_loader_header_lines() + [
        "",
        '#include "rawtex_loader.h"',
        "#include <psp2/kernel/sysmem.h>",
        "#include <stdio.h>",
        "#include <string.h>",
        "",
        "int rawtex_load(const char *path, SceGxmTexture *out_texture, rawtex_handle_t *out_handle) {",
        "    FILE *f = fopen(path, \"rb\");",
        "    if (!f) return -1;",
        "",
        "    char magic[4];",
        "    unsigned int mip_count;",
        "    fread(magic, 1, 4, f);",
        "    fread(&mip_count, 4, 1, f);",
        "    if (memcmp(magic, \"PVXR\", 4) != 0 || mip_count == 0) { fclose(f); return -2; }",
        "",
        "    unsigned int width0, height0, offset0, length0;",
        "    fread(&width0, 4, 1, f);",
        "    fread(&height0, 4, 1, f);",
        "    fread(&offset0, 4, 1, f);",
        "    fread(&length0, 4, 1, f);",
        "    (void)offset0; (void)length0; /* level 0 always starts right after the mip table -- unused here */",
        "",
        "    /* Skip the remaining mip records (12 bytes header + 16 bytes per record already",
        "     * read once for level 0) to find the total pixel-data size that follows them. */",
        "    fseek(f, 0, SEEK_END);",
        "    long file_size = ftell(f);",
        "    long pixel_data_start = 8 /* magic + mip_count */ + (long)mip_count * 16;",
        "    long pixel_data_size = file_size - pixel_data_start;",
        "",
        "    SceUID mem_uid = sceKernelAllocMemBlock(\"rawtex\", SCE_KERNEL_MEMBLOCK_TYPE_USER_RW_UNCACHE,",
        "                                             (pixel_data_size + 0xFFF) & ~0xFFF, NULL);",
        "    if (mem_uid < 0) { fclose(f); return -3; }",
        "    void *gpu_mem = NULL;",
        "    sceKernelGetMemBlockBase(mem_uid, &gpu_mem);",
        "",
        "    fseek(f, pixel_data_start, SEEK_SET);",
        "    fread(gpu_mem, 1, pixel_data_size, f);",
        "    fclose(f);",
        "",
        "    /* SCE_GXM_TEXTURE_FORMAT_U8U8U8U8_ABGR matches _write_rawtex()'s RGBA8888 byte",
        "     * order -- adjust if you changed the transcoder's pixel format. */",
        "    sceGxmTextureInitLinear(out_texture, gpu_mem, SCE_GXM_TEXTURE_FORMAT_U8U8U8U8_ABGR,",
        "                            width0, height0, mip_count);",
        "",
        "    out_handle->gpu_mem = gpu_mem;",
        "    out_handle->mem_block_uid = mem_uid;",
        "    return 0;",
        "}",
        "",
        "void rawtex_free(rawtex_handle_t *handle) {",
        "    if (handle->mem_block_uid >= 0) sceKernelFreeMemBlock(handle->mem_block_uid);",
        "    handle->gpu_mem = NULL;",
        "    handle->mem_block_uid = -1;",
        "}",
        "",
    ]

    dest = Path(out_dir) if out_dir else (project_dir / "source" if (project_dir / "source").is_dir() else project_dir)
    dest.mkdir(parents=True, exist_ok=True)
    header_path = dest / "rawtex_loader.h"
    source_path = dest / "rawtex_loader.c"
    header_path.write_text("\n".join(header_lines) + "\n", encoding="utf-8")
    source_path.write_text("\n".join(source_lines) + "\n", encoding="utf-8")

    print(t("asset_transcoder.loader_generated", header=header_path.name, source=source_path.name))
    return source_path


def transcoder_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: batch-transcode textures or audio from a
           porter-chosen source folder.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict.
    """
    project_dir = Path(project_cfg["_project_dir"])
    dest_dir = project_dir / "extras" / "native_assets"

    def _ask_src_dir():
        raw = input(t("asset_transcoder.src_prompt")).strip().strip("'\"") or "assets"
        return project_dir / raw

    def _textures():
        transcode_texture_dir(_ask_src_dir(), dest_dir)

    def _audio():
        transcode_audio_dir(_ask_src_dir(), dest_dir, global_cfg)

    tui.run_menu(
        t("asset_transcoder.menu_title"),
        [
            (t("asset_transcoder.menu_textures"), _textures),
            (t("asset_transcoder.menu_audio"), _audio),
            (t("asset_transcoder.menu_gen_loader"), lambda: generate_rawtex_loader(project_cfg)),
        ],
    )
