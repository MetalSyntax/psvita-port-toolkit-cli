"""!
@file media.py
@brief Integrate video + audio into any port: probe the game's media files,
       convert the ones the Vita can't play with ffmpeg, and copy the proven
       playback/mixer code from the sibling port that already solved that
       exact format problem.

@details Nothing here is new playback code -- every recipe points at a file
that already works on real hardware in another port under the same
`PSVITA Develop/` root (siblings of the active project):

Video
  - `video-avplayer`  H.264 Baseline MP4 <=30 fps -> SceAvPlayer (hardware).
                      Dungeon-Hunter-2-vita/source/video.cpp
  - `video-avcdec`    H.264 that SceAvPlayer stops on (60 fps etc.), played
                      unmodified via sceAvcdec + own MP4 demuxer.
                      Brothers-in-Arms-2-vita/source/video_avc.c
  - `video-ffmpeg`    Non-H.264 (MPEG-4 Part 2, ...) decoded in software by
                      libavcodec, original files unmodified.
                      Asphalt-6-Vita/source/video.cpp
Audio
  - `audio-audiotrack`    Engine mixes itself and pushes PCM to
                          android.media.AudioTrack -> sceAudioOut.
  - `audio-glmediaplayer` Gameloft GLMediaPlayer JNI, per-file ogg/mp3/wav
                          (stb_vorbis + minimp3 + wav, own mixer).
  - `audio-vorbis`        Generic per-file OGG streaming mixer (Tremor).
  - `audio-opensles`      FMOD/OpenSLES hang in COutputMix_PreDestroy.
  - `audio-coreaudio`     iOS ports: AudioUnit RemoteIO on sceAudioOut.

ffmpeg is the converter for BOTH kinds: video -> H.264 Baseline/AAC 48 kHz
(what SceAvPlayer accepts), audio -> OGG Vorbis or WAV s16 at 48 kHz stereo
(48 kHz because SCE_AUDIO_OUT_PORT_TYPE_MAIN/VOICE reject 44.1 kHz, see
Asphalt-6-Vita gmp_audio.c Bug #036). ATRAC9 (.at9) stays in asset_transcoder.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import i18n, tui
from .i18n import t
from .jni_analyzer import _find_primary_so
from .tui import C

STRINGS = {
    "media.menu_title": {
        "es": "Integrar Video y Audio",
        "en": "Integrate Video and Audio",
        "pt": "Integrar Vídeo e Áudio",
    },
    "media.menu_scan": {
        "es": "Analizar media del port (ffprobe + API de audio del .so)",
        "en": "Scan the port's media (ffprobe + the .so's audio API)",
        "pt": "Analisar mídia do port (ffprobe + API de áudio do .so)",
    },
    "media.menu_convert": {
        "es": "Convertir con ffmpeg lo que la Vita no reproduce",
        "en": "Convert with ffmpeg whatever the Vita can't play",
        "pt": "Converter com ffmpeg o que a Vita não reproduz",
    },
    "media.menu_integrate": {
        "es": "Copiar código de reproducción probado de otro port",
        "en": "Copy proven playback code from another port",
        "pt": "Copiar código de reprodução comprovado de outro port",
    },
    "media.recipe_prompt": {
        "es": "Receta ({choices}): ",
        "en": "Recipe ({choices}): ",
        "pt": "Receita ({choices}): ",
    },
    "media.no_ffprobe": {
        "es": "[!] ffmpeg/ffprobe no está en PATH (brew install ffmpeg).",
        "en": "[!] ffmpeg/ffprobe is not on PATH (brew install ffmpeg).",
        "pt": "[!] ffmpeg/ffprobe não está no PATH (brew install ffmpeg).",
    },
    "media.no_media": {
        "es": "[*] No se encontraron archivos de video/audio en {src_dir}.",
        "en": "[*] No video/audio files found in {src_dir}.",
        "pt": "[*] Nenhum arquivo de vídeo/áudio encontrado em {src_dir}.",
    },
    "media.audio_api": {
        "es": "[*] API de audio detectada en {so}: {apis} -> receta {recipe}",
        "en": "[*] Audio API detected in {so}: {apis} -> recipe {recipe}",
        "pt": "[*] API de áudio detectada em {so}: {apis} -> receita {recipe}",
    },
    "media.convert_done": {
        "es": "  [+] {name} -> {out}",
        "en": "  [+] {name} -> {out}",
        "pt": "  [+] {name} -> {out}",
    },
    "media.convert_failed": {
        "es": "  [-] {name}: {error}",
        "en": "  [-] {name}: {error}",
        "pt": "  [-] {name}: {error}",
    },
    "media.ext_changed": {
        "es": "  [!] {name} cambió de extensión: el juego lo pide como '{orig}', renombralo o redirigí la ruta en io.",
        "en": "  [!] {name} changed extension: the game asks for '{orig}', rename it or redirect the path in io.",
        "pt": "  [!] {name} mudou de extensão: o jogo pede '{orig}', renomeie ou redirecione o caminho no io.",
    },
    "media.nothing_to_convert": {
        "es": "[*] Nada que convertir: todo es reproducible tal cual.",
        "en": "[*] Nothing to convert: everything plays as-is.",
        "pt": "[*] Nada para converter: tudo reproduz como está.",
    },
    "media.unknown_recipe": {
        "es": "[!] Receta desconocida '{recipe}'. Opciones: {choices}",
        "en": "[!] Unknown recipe '{recipe}'. Choices: {choices}",
        "pt": "[!] Receita desconhecida '{recipe}'. Opções: {choices}",
    },
    "media.ref_missing": {
        "es": "[!] No existe el archivo de referencia {path} (¿--ref-root correcto?).",
        "en": "[!] Reference file {path} does not exist (correct --ref-root?).",
        "pt": "[!] O arquivo de referência {path} não existe (--ref-root correto?).",
    },
    "media.exists_skip": {
        "es": "  [=] {path} ya existe, no se sobrescribe.",
        "en": "  [=] {path} already exists, not overwritten.",
        "pt": "  [=] {path} já existe, não será sobrescrito.",
    },
    "media.copied": {
        "es": "  [+] {src} -> {dest}",
        "en": "  [+] {src} -> {dest}",
        "pt": "  [+] {src} -> {dest}",
    },
    "media.missing_include": {
        "es": "  [TODO] {file} incluye \"{inc}\", que no existe en este port: adaptalo o reemplazalo.",
        "en": "  [TODO] {file} includes \"{inc}\", which this port doesn't have: adapt or replace it.",
        "pt": "  [TODO] {file} inclui \"{inc}\", que não existe neste port: adapte ou substitua.",
    },
    "media.cmake_hint": {
        "es": "[*] Agregá a target_link_libraries en CMakeLists.txt: {libs}",
        "en": "[*] Add to target_link_libraries in CMakeLists.txt: {libs}",
        "pt": "[*] Adicione ao target_link_libraries no CMakeLists.txt: {libs}",
    },
}
i18n.register(STRINGS)

_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".3gp", ".avi", ".mkv", ".webm", ".ogv", ".bik", ".mpg", ".mpeg")
_AUDIO_EXTENSIONS = (".ogg", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".opus", ".wma", ".caf", ".aif", ".aiff")
# Formats the reference mixers decode natively (stb_vorbis/Tremor, minimp3, raw wav).
_PLAYABLE_AUDIO_CODECS = ("vorbis", "mp3", "pcm_s16le", "pcm_u8")
_SKIP_DIRS = {".git", "build", "decompiled", "jadx", "node_modules", "__pycache__", "extras"}

# Vita screen + what SceAvPlayer reliably plays (DH2 re-encode, Asphalt-5 port_progress.md).
_AVPLAYER_MAX_W, _AVPLAYER_MAX_H, _AVPLAYER_MAX_FPS = 960, 544, 30
_VITA_AUDIO_RATE = 48000

# key -> (reference port dir, files relative to it, CMake libs, one-line "when").
RECIPES = {
    "video-avplayer": ("Dungeon-Hunter-2-vita", ("source/video.cpp", "source/video.h"),
                       "SceAvPlayer_stub SceAudio_stub",
                       "H.264 Baseline MP4 <=960x544 <=30fps (o convertido con `media convert`)"),
    "video-avcdec": ("Brothers-in-Arms-2-vita", ("source/video_avc.c", "source/video_avc.h"),
                     "SceVideodec_stub SceAudio_stub avcodec swresample avutil mp3lame",
                     "H.264 original que SceAvPlayer no arranca (60fps): sceAvcdec por hardware"),
    "video-ffmpeg": ("Asphalt-6-Vita", ("source/video.cpp", "source/video.h"),
                     "avformat avcodec swresample avutil mp3lame SceAudio_stub",
                     "MPEG-4 Part 2 u otro no-H.264, archivo original sin tocar (libavcodec software; vdpm ffmpeg lame)"),
    "audio-audiotrack": ("Asphalt-6-Vita", ("source/reimpl/audiotrack.c", "source/reimpl/audiotrack.h"),
                         "SceAudio_stub",
                         "el motor mezcla y empuja PCM a android.media.AudioTrack"),
    "audio-glmediaplayer": ("GT-Racing-Motor-Academy-HD-vita", ("source/audio.cpp", "source/audio.h"),
                            "SceAudio_stub (headers minimp3 + stb_vorbis)",
                            "Gameloft GLMediaPlayer JNI con .ogg/.mp3/.wav por archivo"),
    "audio-vorbis": ("Zenonia4-vita", ("loader/audio.c", "loader/audio.h"),
                     "vorbisidec ogg SceAudio_stub",
                     "BGM/SFX .ogg por archivo vía android MediaPlayer/SoundPool (Tremor streaming)"),
    "audio-opensles": ("Badland-2-vita", ("source/reimpl/opensles_fix.c",),
                       "OpenSLES SceAudio_stub + -Wl,--wrap=COutputMix_PreDestroy",
                       "FMOD/OpenSLES se cuelga destruyendo el OutputMix"),
    "audio-coreaudio": ("Blades-of-Fury-vita", ("source/coreaudio.c", "source/coreaudio.h"),
                        "SceAudio_stub",
                        "port iOS: AudioUnit RemoteIO / AudioFile sobre sceAudioOut"),
}

# Byte signatures in the game's .so -> audio recipe. Order = priority.
_AUDIO_API_SIGNATURES = (
    ("audio-opensles", (b"slCreateEngine", b"libOpenSLES.so")),
    ("audio-glmediaplayer", (b"GLMediaPlayer",)),
    ("audio-audiotrack", (b"android/media/AudioTrack",)),
    ("audio-vorbis", (b"android/media/MediaPlayer", b"android/media/SoundPool")),
)


def _ffprobe(path):
    """!
    @brief First video + first audio stream of a file, via ffprobe.
    @return dict with `video`/`audio` stream dicts (or `None`), `{}` on probe failure.
    """
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    streams = json.loads(r.stdout or "{}").get("streams", [])
    pick = lambda kind: next((s for s in streams if s.get("codec_type") == kind), None)
    return {"video": pick("video"), "audio": pick("audio")}


def _fps(stream):
    """!@brief `avg_frame_rate` ("30000/1001") as float, 0 if unknown."""
    num, _, den = (stream.get("avg_frame_rate") or "0/1").partition("/")
    return float(num) / float(den) if float(den or 0) else 0.0


def classify(probe):
    """!
    @brief Pick the recipe for one probed file and whether ffmpeg must convert it.
    @param probe `_ffprobe()` result.
    @return `(kind, recipe_key, needs_convert, reason)`; kind is "video"/"audio"/None.
    """
    v, a = probe.get("video"), probe.get("audio")
    if v and v.get("disposition", {}).get("attached_pic") != 1:
        fps, w, h = _fps(v), v.get("width", 0), v.get("height", 0)
        if v.get("codec_name") != "h264":
            return "video", "video-ffmpeg", True, f"{v.get('codec_name')} no es H.264"
        if fps > _AVPLAYER_MAX_FPS + 0.5:
            return "video", "video-avcdec", True, f"H.264 a {fps:.0f}fps (SceAvPlayer se detiene)"
        if w > _AVPLAYER_MAX_W or h > _AVPLAYER_MAX_H or v.get("profile") not in ("Baseline", "Constrained Baseline", "Main"):
            return "video", "video-avplayer", True, f"H.264 {v.get('profile')} {w}x{h}"
        return "video", "video-avplayer", False, f"H.264 {w}x{h} {fps:.0f}fps OK"
    if a:
        rate = int(a.get("sample_rate") or 0)
        if a.get("codec_name") not in _PLAYABLE_AUDIO_CODECS:
            return "audio", None, True, f"{a.get('codec_name')} no lo decodifica ningún mixer de referencia"
        note = "OK" if rate == _VITA_AUDIO_RATE else f"{rate} Hz (el mixer remuestrea a 48 kHz)"
        return "audio", None, False, f"{a.get('codec_name')} {note}"
    return None, None, False, "sin streams"


def _iter_media(src_dir):
    """!@brief Every video/audio file under `src_dir`, skipping build/decomp dirs."""
    for p in sorted(Path(src_dir).rglob("*")):
        if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS + _AUDIO_EXTENSIONS \
                and not _SKIP_DIRS.intersection(p.relative_to(src_dir).parts[:-1]) and not p.name.startswith("._"):
            yield p


def detect_audio_recipe(project_cfg):
    """!
    @brief Audio recipe implied by the game's binary: iOS -> CoreAudio, else
           the first `_AUDIO_API_SIGNATURES` hit in the primary `.so`.
    @return `(recipe_key | None, so_name | None, matched_signatures)`.
    """
    if project_cfg.get("platform") == "ios":
        return "audio-coreaudio", None, ["iOS"]
    so_path = _find_primary_so(project_cfg["_project_dir"])
    if not so_path:
        return None, None, []
    data = Path(so_path).read_bytes()
    for key, sigs in _AUDIO_API_SIGNATURES:
        hits = [s.decode() for s in sigs if s in data]
        if hits:
            return key, Path(so_path).name, hits
    return None, Path(so_path).name, []


def scan(project_cfg, src_dir=None):
    """!
    @brief Print every media file with its codec, recipe and whether it needs
           ffmpeg, plus the audio recipe the `.so` implies.
    @return list of `(path, kind, recipe, needs_convert, probe)`.
    """
    if not shutil.which("ffprobe"):
        print(f"{C.RED}{t('media.no_ffprobe')}{C.RESET}")
        return []
    src_dir = Path(src_dir or project_cfg["_project_dir"])
    results = []
    for p in _iter_media(src_dir):
        probe = _ffprobe(p)
        kind, recipe, needs, reason = classify(probe)
        if not kind:
            continue
        results.append((p, kind, recipe, needs, probe))
        color = C.YELLOW if needs else C.GREEN
        print(f"  {color}{'CONVERT' if needs else 'OK':7}{C.RESET} {kind:5} {recipe or '':15} "
              f"{p.relative_to(src_dir)}  ({reason})")
    if not results:
        print(t("media.no_media", src_dir=src_dir))
    audio_recipe, so_name, hits = detect_audio_recipe(project_cfg)
    if audio_recipe:
        print(t("media.audio_api", so=so_name or "binary", apis=", ".join(hits), recipe=audio_recipe))
    return results


def _ffmpeg_cmd(src, out, kind, probe, audio_format):
    """!@brief ffmpeg argv for one file (Vita-safe target per kind)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if kind == "video":
        vf = (f"scale='min({_AVPLAYER_MAX_W},iw)':'min({_AVPLAYER_MAX_H},ih)'"
              ":force_original_aspect_ratio=decrease:force_divisible_by=2")
        if _fps(probe["video"]) > _AVPLAYER_MAX_FPS + 0.5:
            vf += f",fps={_AVPLAYER_MAX_FPS}"
        cmd += ["-vf", vf, "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.1",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", str(_VITA_AUDIO_RATE)] if probe.get("audio") else ["-an"]
    elif audio_format == "wav":
        cmd += ["-vn", "-c:a", "pcm_s16le", "-ac", "2", "-ar", str(_VITA_AUDIO_RATE)]
    else:
        encoders = subprocess.run(["ffmpeg", "-v", "error", "-encoders"], capture_output=True, text=True).stdout
        codec = ["libvorbis"] if " libvorbis " in encoders else ["vorbis", "-strict", "-2"]
        cmd += ["-vn", "-c:a", *codec, "-q:a", "4", "-ac", "2", "-ar", str(_VITA_AUDIO_RATE)]
    return cmd + [str(out)]


def convert(project_cfg, src_dir=None, out_dir=None, audio_format="ogg", convert_all=False):
    """!
    @brief Re-encode with ffmpeg every scanned file that needs it (or all with
           `convert_all`), mirroring the source tree under `out_dir`.
           Originals are never touched.
    @return `(ok, failed)` counts.
    """
    project_dir = Path(project_cfg["_project_dir"])
    src_dir = Path(src_dir or project_dir)
    out_dir = Path(out_dir or project_dir / "extras" / "media")
    todo = [r for r in scan(project_cfg, src_dir) if r[3] or convert_all]
    if not todo:
        print(t("media.nothing_to_convert"))
        return 0, 0
    ok = failed = 0
    for path, kind, _recipe, _needs, probe in todo:
        ext = ".mp4" if kind == "video" else f".{audio_format}"
        out = out_dir / path.relative_to(src_dir).with_suffix(ext)
        out.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(_ffmpeg_cmd(path, out, kind, probe, audio_format), capture_output=True, text=True)
        if r.returncode != 0:
            failed += 1
            print(t("media.convert_failed", name=path.name, error=(r.stderr.strip().splitlines() or ["?"])[-1]))
            continue
        ok += 1
        print(t("media.convert_done", name=path.name, out=out.relative_to(project_dir) if out.is_relative_to(project_dir) else out))
        if path.suffix.lower() != ext:
            print(f"{C.YELLOW}{t('media.ext_changed', name=out.name, orig=path.name)}{C.RESET}")
    return ok, failed


def integrate(project_cfg, recipe, ref_root=None):
    """!
    @brief Copy a recipe's reference source files into this port (never
           overwriting), then list the local includes still to adapt and the
           CMake libs to link.
    @param ref_root Folder holding the reference ports; default = the
           project's parent (`PSVITA Develop/`).
    @return list of copied destination paths; `None` on unknown recipe/missing ref.
    """
    if recipe not in RECIPES:
        print(f"{C.RED}{t('media.unknown_recipe', recipe=recipe, choices=', '.join(RECIPES))}{C.RESET}")
        return None
    project_dir = Path(project_cfg["_project_dir"])
    ref_port, files, libs, _when = RECIPES[recipe]
    ref_dir = Path(ref_root or project_dir.parent) / ref_port
    missing = [f for f in files if not (ref_dir / f).is_file()]
    if missing:
        print(f"{C.RED}{t('media.ref_missing', path=ref_dir / missing[0])}{C.RESET}")
        return None
    dest_dir = next((project_dir / d for d in ("source", "loader") if (project_dir / d).is_dir()), project_dir)
    copied = []
    for rel in files:
        dest = dest_dir / Path(rel).name
        if dest.exists():
            print(t("media.exists_skip", path=dest.relative_to(project_dir)))
            continue
        shutil.copy2(ref_dir / rel, dest)
        copied.append(dest)
        print(t("media.copied", src=f"{ref_port}/{rel}", dest=dest.relative_to(project_dir)))
    for dest in copied:
        for inc in re.findall(r'^\s*#\s*include\s+"([^"]+)"', dest.read_text(errors="replace"), re.M):
            if not (dest.parent / inc).exists():
                print(f"{C.YELLOW}{t('media.missing_include', file=dest.name, inc=inc)}{C.RESET}")
    print(t("media.cmake_hint", libs=libs))
    return copied


def print_recipes():
    """!@brief One line per recipe: key, reference port, when to use it."""
    for key, (port, files, _libs, when) in RECIPES.items():
        print(f"  {C.BOLD}{key:20}{C.RESET} {port}/{files[0]}\n  {'':20} {when}")


def media_menu(project_cfg, global_cfg):
    """!
    @brief TUI entry point: scan / convert / integrate.
    @param project_cfg Per-project config dict.
    @param global_cfg Global config dict (unused, kept for the menu signature).
    """
    def _integrate():
        print_recipes()
        integrate(project_cfg, input(t("media.recipe_prompt", choices=", ".join(RECIPES))).strip())

    tui.run_menu(
        t("media.menu_title"),
        [
            (t("media.menu_scan"), lambda: scan(project_cfg)),
            (t("media.menu_convert"), lambda: convert(project_cfg)),
            (t("media.menu_integrate"), _integrate),
        ],
    )
