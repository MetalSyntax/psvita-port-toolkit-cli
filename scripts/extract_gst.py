import os
import zlib
import json
import struct
import glob

def extract_gst(file_path):
    with open(file_path, 'rb') as f:
        data = f.read()
    
    # Check if it's zt1
    if len(data) < 8:
        return None
        
    decomp_size = struct.unpack('<I', data[0:4])[0]
    
    try:
        decomp = zlib.decompress(data[8:])
    except Exception as e:
        print(f"Failed to decompress {file_path}: {e}")
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
        if i < count - 1:
            end = offsets[i+1]
        else:
            end = len(decomp)
            
        str_data = decomp[start:end]
        
        # usually null terminated? Let's strip trailing nulls
        while str_data and str_data[-1] == 0:
            str_data = str_data[:-1]
            
        # Try to decode as utf-8 or cp1252? Usually these games use utf-8 or cp1252
        try:
            text = str_data.decode('utf-8')
        except UnicodeDecodeError:
            text = str_data.decode('cp1252', errors='replace')
            
        strings.append(text)
        
    return strings

def main():
    base_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/ux0_data/zenonia-2/assets"
    out_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_translation"
    
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
        
    # Search for all .zt1 files
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith('.zt1'):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, base_dir)
                
                strings = extract_gst(full_path)
                if strings:
                    # Save to json
                    out_json = os.path.join(out_dir, rel_path + '.json')
                    os.makedirs(os.path.dirname(out_json), exist_ok=True)
                    
                    with open(out_json, 'w', encoding='utf-8') as f:
                        json.dump(strings, f, indent=4, ensure_ascii=False)
                        
                    print(f"Extracted {rel_path} -> {len(strings)} strings")

if __name__ == '__main__':
    main()
