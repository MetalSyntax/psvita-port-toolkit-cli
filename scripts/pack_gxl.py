import os
import zlib
import json
import struct

def pack_gxl(json_path, original_zt1_path, output_zt1_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        records_json = json.load(f)
        
    with open(original_zt1_path, 'rb') as f:
        orig_data = f.read()
        
    decomp_size = struct.unpack('<I', orig_data[0:4])[0]
    
    decomp = bytearray(zlib.decompress(orig_data[8:]))
    
    rec_size = struct.unpack('<H', decomp[4:6])[0]
    header_size = struct.unpack('<H', decomp[6:8])[0]
    num_recs = struct.unpack('<I', decomp[8:12])[0]
    
    for r in records_json:
        idx = r['record_index']
        start_rec = header_size + idx * rec_size
        
        for s in r['strings']:
            # The translated text could be in a new key "translated_text" or just modifying "text"
            text_to_write = s.get('translated_text', s['text'])
            offset = s['offset']
            max_len = s['max_length']
            
            encoded = text_to_write.encode('utf-8')
            
            if len(encoded) > max_len:
                # Truncate if it exceeds maximum length allowed by the record cell
                encoded = encoded[:max_len]
                print(f"Warning: String '{text_to_write}' truncated to {max_len} bytes.")
                
            # Write to decomp bytearray
            # Fill with nulls first
            for i in range(max_len + 1):
                decomp[start_rec + offset + i] = 0
                
            # Write new string
            for i, b in enumerate(encoded):
                decomp[start_rec + offset + i] = b
                
    compressed = zlib.compress(decomp)
    
    zt1_header = struct.pack('<II', len(decomp), len(compressed))
    
    final_data = zt1_header + compressed
    
    os.makedirs(os.path.dirname(output_zt1_path), exist_ok=True)
    with open(output_zt1_path, 'wb') as f:
        f.write(final_data)


def main():
    json_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_translation"
    base_assets_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/ux0_data/zenonia-2/assets"
    output_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_repacked"
    
    # We will search for json files that correspond to Xls*.zt1
    for root, dirs, files in os.walk(json_dir):
        for file in files:
            if file.startswith('Xls') and file.endswith('.zt1.json'):
                json_path = os.path.join(root, file)
                rel_path = os.path.relpath(json_path, json_dir)
                # rel_path is like data/eng/XlsItem.zt1.json
                
                original_zt1 = os.path.join(base_assets_dir, rel_path.replace('.json', ''))
                output_zt1 = os.path.join(output_dir, rel_path.replace('.json', ''))
                
                if os.path.exists(original_zt1):
                    pack_gxl(json_path, original_zt1, output_zt1)
                    print(f"Repacked GXL {rel_path} -> {output_zt1}")

if __name__ == '__main__':
    main()
