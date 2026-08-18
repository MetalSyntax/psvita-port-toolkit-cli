"""!
@file zenonia2_tools.py
@brief Herramientas específicas de desarrollo/traducción para Zenonia 2.
@details
Provee integración para extracción, traducción y empaquetado de archivos GST y GXL (formatos de strings y tablas ZT1).
"""

import glob
import json
import os
from pathlib import Path
import re
import struct
import time
import zlib

from . import i18n
from .i18n import t
from . import tui
from .tui import C

STRINGS = {
    "zen2.menu_title": {
        "es": "Herramientas Zenonia 2 (GST/GXL)",
        "en": "Zenonia 2 Tools (GST/GXL)",
        "pt": "Ferramentas Zenonia 2 (GST/GXL)",
    },
    "zen2.extract_all": {
        "es": "📦 Extraer todos los assets (.zt1 -> JSON: GST strings y GXL tablas)",
        "en": "📦 Extract all assets (.zt1 -> JSON: GST strings & GXL tables)",
        "pt": "📦 Extrair todos os assets (.zt1 -> JSON: GST strings e tabelas GXL)",
    },
    "zen2.translate_json": {
        "es": "🌐 Traducir strings JSON (deep-translator EN -> ES)",
        "en": "🌐 Translate JSON strings (deep-translator EN -> ES)",
        "pt": "🌐 Traduzir strings JSON (deep-translator EN -> ES)",
    },
    "zen2.pack_all": {
        "es": "⚙️  Empaquetar traducción completa (JSON SPA -> data/eng/ *.zt1)",
        "en": "⚙️  Pack full translation (JSON SPA -> data/eng/ *.zt1)",
        "pt": "⚙️  Empacotar tradução completa (JSON SPA -> data/eng/ *.zt1)",
    },
}
i18n.register(STRINGS)


# ---------------------------------------------------------------------------
# Core GST / GXL Logic
# ---------------------------------------------------------------------------

def extract_gst(file_path):
    with open(file_path, 'rb') as f:
        data = f.read()

    if len(data) < 8:
        return None

    try:
        decomp = zlib.decompress(data[8:])
    except Exception:
        return None

    if not decomp.startswith(b'GST\x01'):
        return None

    count = struct.unpack('<H', decomp[4:6])[0]
    offsets = []
    for i in range(count):
        offset = struct.unpack('<H', decomp[6 + i*2 : 8 + i*2])[0]
        offsets.append(offset)

    strings = []
    for i in range(count):
        start = offsets[i]
        end = offsets[i+1] if i < count - 1 else len(decomp)
        str_data = decomp[start:end]

        while str_data and str_data[-1] == 0:
            str_data = str_data[:-1]

        try:
            text = str_data.decode('utf-8')
        except UnicodeDecodeError:
            text = str_data.decode('cp1252', errors='replace')

        strings.append(text)

    return strings


def pack_gst(json_path, output_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        strings = json.load(f)

    count = len(strings)
    header = b'GST\x01' + struct.pack('<H', count)
    strings_start = 8 + count * 2
    header += struct.pack('<H', strings_start)

    offsets = []
    current_offset = strings_start
    strings_data = bytearray()

    for s in strings:
        offsets.append(current_offset)
        encoded_str = s.encode('utf-8') + b'\x00'
        strings_data.extend(encoded_str)
        current_offset += len(encoded_str)

    payload = bytearray(header)
    for offset in offsets:
        payload.extend(struct.pack('<H', offset))
    payload.extend(strings_data)

    compressed = zlib.compress(payload)
    decomp_size = len(payload)
    zt1_header = struct.pack('<II', decomp_size, len(compressed))
    final_data = zt1_header + compressed

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(final_data)


def extract_gxl(file_path):
    import string
    with open(file_path, 'rb') as f:
        data = f.read()

    if len(data) < 8:
        return None

    try:
        decomp = zlib.decompress(data[8:])
    except Exception:
        return None

    if not decomp.startswith(b'GXL\x01'):
        return None

    rec_size = struct.unpack('<H', decomp[4:6])[0]
    header_size = struct.unpack('<H', decomp[6:8])[0]
    num_recs = (len(decomp) - header_size) // rec_size

    records = []
    for i in range(num_recs):
        start = header_size + i * rec_size
        end = start + rec_size
        rec_data = decomp[start:end]

        strings_in_record = []
        current_str = b''
        str_offset = -1

        for j in range(rec_size):
            b = rec_data[j:j+1]
            if b == b'\x00':
                if len(current_str) >= 2:
                    try:
                        text = current_str.decode('utf-8')
                        if all(c in string.printable for c in text):
                            k = j
                            while k < rec_size and rec_data[k] == 0:
                                k += 1
                            max_len = k - str_offset - 1
                            strings_in_record.append({
                                'text': text,
                                'offset': str_offset,
                                'max_length': max_len
                            })
                    except Exception:
                        pass
                current_str = b''
                str_offset = -1
            else:
                if str_offset == -1:
                    str_offset = j
                current_str += b

        records.append({
            'record_index': i,
            'strings': strings_in_record
        })

    return records


def pack_gxl(json_path, original_zt1_path, output_zt1_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        records_json = json.load(f)

    with open(original_zt1_path, 'rb') as f:
        orig_data = f.read()

    decomp = bytearray(zlib.decompress(orig_data[8:]))
    rec_size = struct.unpack('<H', decomp[4:6])[0]
    header_size = struct.unpack('<H', decomp[6:8])[0]

    for r in records_json:
        idx = r['record_index']
        start_rec = header_size + idx * rec_size

        for s in r['strings']:
            text_to_write = s.get('translated_text', s['text'])
            offset = s['offset']
            max_len = s['max_length']

            encoded = text_to_write.encode('utf-8')
            if len(encoded) > max_len:
                encoded = encoded[:max_len]
                print(f"Warning: String '{text_to_write}' truncated to {max_len} bytes.")

            for i in range(max_len + 1):
                decomp[start_rec + offset + i] = 0

            for i, b in enumerate(encoded):
                decomp[start_rec + offset + i] = b

    compressed = zlib.compress(decomp)
    zt1_header = struct.pack('<II', len(decomp), len(compressed))
    final_data = zt1_header + compressed

    os.makedirs(os.path.dirname(output_zt1_path), exist_ok=True)
    with open(output_zt1_path, 'wb') as f:
        f.write(final_data)


# ---------------------------------------------------------------------------
# Translation Helpers
# ---------------------------------------------------------------------------

TAG_PATTERN = re.compile(r'(!c[0-9A-Fa-f]{6}|![a-zA-Z])')

def replace_tags_with_placeholders(text):
    tags = []
    def replacer(match):
        tags.append(match.group(0))
        return f" <T{len(tags)-1}> "
    modified_text = TAG_PATTERN.sub(replacer, text)
    modified_text = re.sub(r'\s+', ' ', modified_text).strip()
    return modified_text, tags


def restore_tags(text, tags):
    restored = text
    for i, tag in enumerate(tags):
        pattern = re.compile(r'\s*<\s*[tT]\s*' + str(i) + r'\s*>\s*')
        restored = pattern.sub(tag, restored)
    return restored


def translate_strings(strings):
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print(f"{C.RED}[-] 'deep-translator' no instalado. Instala con: pip install deep-translator{C.RESET}")
        return strings

    translator = GoogleTranslator(source='en', target='es')
    translated_strings = []

    for i, s in enumerate(strings):
        if not s.strip():
            translated_strings.append(s)
            continue
        try:
            modified_text, tags = replace_tags_with_placeholders(s)
            trans_text = translator.translate(modified_text) if modified_text.strip() else modified_text
            final_text = restore_tags(trans_text, tags)
            translated_strings.append(final_text)
            if i % 10 == 0 and i > 0:
                print(f"  Traducidas {i}/{len(strings)} strings...")
                time.sleep(0.3)
        except Exception as e:
            print(f"  Error traduciendo string {i}: {e}. Manteniendo original.")
            translated_strings.append(s)

    return translated_strings


# ---------------------------------------------------------------------------
# High-Level Actions
# ---------------------------------------------------------------------------

def _find_assets_dir(project_dir):
    candidates = [
        project_dir / "ux0_data" / "zenonia-2" / "assets",
        project_dir / "assets",
        project_dir / "apk_extract" / "assets",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return project_dir / "assets"


def do_extract_all(project_cfg):
    pdir = Path(project_cfg["_project_dir"])
    base_dir = _find_assets_dir(pdir)
    out_dir = pdir / "zenonia2_translation"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Extrayendo desde {base_dir} hacia {out_dir}...")
    gst_count = 0
    gxl_count = 0

    for root, _dirs, files in os.walk(base_dir):
        for file in files:
            if not file.endswith('.zt1') or file.startswith('._'):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, base_dir)

            if file.startswith('Xls'):
                records = extract_gxl(full_path)
                if records:
                    has_strings = [r for r in records if r['strings']]
                    if has_strings:
                        out_json = out_dir / (rel_path + '.json')
                        out_json.parent.mkdir(parents=True, exist_ok=True)
                        with open(out_json, 'w', encoding='utf-8') as f:
                            json.dump(has_strings, f, indent=4, ensure_ascii=False)
                        print(f"  [GXL] {rel_path} -> {len(has_strings)} registros con texto")
                        gxl_count += 1
            else:
                strings = extract_gst(full_path)
                if strings:
                    out_json = out_dir / (rel_path + '.json')
                    out_json.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_json, 'w', encoding='utf-8') as f:
                        json.dump(strings, f, indent=4, ensure_ascii=False)
                    print(f"  [GST] {rel_path} -> {len(strings)} strings")
                    gst_count += 1

    print(f"{C.GREEN}[+] Extracción completada: {gst_count} GST, {gxl_count} GXL.{C.RESET}")


def do_translate_json(project_cfg):
    pdir = Path(project_cfg["_project_dir"])
    base_dir = pdir / "zenonia2_translation" / "data"
    eng_dir = base_dir / "eng"
    spa_dir = base_dir / "spa"

    if not eng_dir.is_dir():
        eng_dir = pdir / "zenonia2_translation"
        spa_dir = pdir / "zenonia2_translation" / "spa"

    spa_dir.mkdir(parents=True, exist_ok=True)

    for root, _dirs, files in os.walk(eng_dir):
        for file in files:
            if not file.endswith('.json') or file.startswith('Xls') or file.startswith('._'):
                continue
            eng_file = os.path.join(root, file)
            spa_file = spa_dir / file

            if spa_file.exists():
                print(f"  [-] Saltando {file} (ya existe en spa)")
                continue

            print(f"[*] Traduciendo {file}...")
            with open(eng_file, 'r', encoding='utf-8') as f:
                strings = json.load(f)

            translated = translate_strings(strings)
            with open(spa_file, 'w', encoding='utf-8') as f:
                json.dump(translated, f, indent=4, ensure_ascii=False)
            print(f"  {C.GREEN}[+] Guardado {spa_file}{C.RESET}")


def do_pack_all_spa(project_cfg):
    pdir = Path(project_cfg["_project_dir"])
    json_dir = pdir / "zenonia2_translation" / "data" / "spa"
    if not json_dir.is_dir():
        json_dir = pdir / "zenonia2_translation" / "spa"

    base_assets_eng_dir = _find_assets_dir(pdir) / "data" / "eng"
    if not base_assets_eng_dir.is_dir():
        base_assets_eng_dir = _find_assets_dir(pdir)

    print(f"[*] Empaquetando traducción SPA desde {json_dir} hacia {base_assets_eng_dir}...")
    if not json_dir.is_dir():
        print(f"{C.RED}[-] Directorio de traducciones no encontrado: {json_dir}{C.RESET}")
        return

    packed_count = 0
    for file in os.listdir(json_dir):
        if not file.endswith('.json') or file.startswith('._'):
            continue

        json_path = json_dir / file
        target_name = file.replace('.zt1.json', '.zt1').replace('.json', '')
        out_zt1 = base_assets_eng_dir / target_name
        orig_zt1 = out_zt1

        if file.startswith('Xls'):
            if orig_zt1.exists():
                pack_gxl(str(json_path), str(orig_zt1), str(out_zt1))
                print(f"  [GXL] {file} -> {out_zt1}")
                packed_count += 1
        else:
            pack_gst(str(json_path), str(out_zt1))
            print(f"  [GST] {file} -> {out_zt1}")
            packed_count += 1

    print(f"{C.GREEN}[+] {packed_count} archivo(s) .zt1 empaquetado(s) exitosamente.{C.RESET}")


def zenonia2_menu(project_cfg):
    """!
    @brief Submenú interactivo para herramientas específicas de Zenonia 2.
    """
    items = [
        (t("zen2.extract_all"), lambda: do_extract_all(project_cfg)),
        (t("zen2.translate_json"), lambda: do_translate_json(project_cfg)),
        (t("zen2.pack_all"), lambda: do_pack_all_spa(project_cfg)),
    ]
    tui.run_menu(
        t("zen2.menu_title"),
        items,
        breadcrumb=f"{project_cfg['game_name']} › {t('zen2.menu_title')}",
        icon="⚔️",
    )
