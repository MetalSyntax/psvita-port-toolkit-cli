import os
import zlib
import json
import struct
import glob

def pack_gst(json_path, output_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        strings = json.load(f)
        
    count = len(strings)
    
    # Calculate offsets
    # Header: "GST\x01" (4) + count (2) + offset_table_end (2)
    # Offsets table: count * 2
    # So strings start at 8 + count * 2
    
    header = b'GST\x01' + struct.pack('<H', count)
    
    strings_start = 8 + count * 2
    header += struct.pack('<H', strings_start)
    
    offsets = []
    current_offset = strings_start
    
    strings_data = bytearray()
    
    for s in strings:
        offsets.append(current_offset)
        
        # encode the string
        encoded_str = s.encode('utf-8') + b'\x00'
        strings_data.extend(encoded_str)
        current_offset += len(encoded_str)
        
    # Build the uncompressed payload
    payload = bytearray(header)
    for offset in offsets:
        payload.extend(struct.pack('<H', offset))
        
    payload.extend(strings_data)
    
    # Compress with zlib
    compressed = zlib.compress(payload)
    
    decomp_size = len(payload)
    
    # The header of zt1
    # 4 bytes decomp_size, 4 bytes something else (we'll just use 0x00000000 for the second part as it seems to be ignored or a checksum we might not need, wait, let's look at StrHelp.zt1 again: 8a 19 00 00 = 6538. Actually let's just write 0s or original values if we want, but let's try 0 for the unknown).
    # Wait, the second 4 bytes might be compressed size or hash? Let's check: 6538 is not the compressed size (3024 is the file size? No decompressed size was 3024? Wait, let's verify).
    # If the file size is 3024, the decompressed size is 6538? Let's use the file's original logic or just write decompressed size.
    # Actually, the first 4 bytes are decompressed size, the next 4 bytes are uncompressed length?
    
    # Just in case, let's use:
    zt1_header = struct.pack('<II', decomp_size, len(compressed))
    
    final_data = zt1_header + compressed
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(final_data)


def main():
    json_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_translation"
    output_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_repacked"
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    # Search for all .json files
    for root, dirs, files in os.walk(json_dir):
        for file in files:
            if file.endswith('.json'):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, json_dir)
                
                out_zt1 = os.path.join(output_dir, rel_path.replace('.json', ''))
                
                pack_gst(full_path, out_zt1)
                print(f"Packed {rel_path} -> {out_zt1}")

if __name__ == '__main__':
    main()
