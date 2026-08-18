import os
import zlib
import json
import struct
import string

def extract_gxl(file_path):
    with open(file_path, 'rb') as f:
        data = f.read()
    
    if len(data) < 8:
        return None
        
    try:
        decomp = zlib.decompress(data[8:])
    except Exception as e:
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
        
        # Heuristic to find strings: look for sequences of printable characters ended by null
        # We will extract strings that are at least 2 chars long and start with some known prefix or just printable
        # For simplicity, we just extract any null-terminated printable string >= 3 chars
        
        strings_in_record = []
        current_str = b''
        str_offset = -1
        
        for j in range(rec_size):
            b = rec_data[j:j+1]
            if b == b'\x00':
                if len(current_str) >= 2:
                    try:
                        text = current_str.decode('utf-8')
                        # Check if mostly printable
                        if all(c in string.printable for c in text):
                            # find how much space this string has (until next non-null or end of record)
                            # Wait, the space allocated is from str_offset to the end of the null padding
                            max_len = len(current_str)
                            k = j
                            while k < rec_size and rec_data[k] == 0:
                                k += 1
                            max_len = k - str_offset - 1 # max length excluding the final null byte
                            
                            strings_in_record.append({
                                'text': text,
                                'offset': str_offset,
                                'max_length': max_len
                            })
                    except:
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

def main():
    base_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/ux0_data/zenonia-2/assets"
    out_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_translation"
    
    # Search for all .zt1 files
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.startswith('Xls') and file.endswith('.zt1'):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, base_dir)
                
                records = extract_gxl(full_path)
                if records:
                    # Filter records with strings
                    has_strings = [r for r in records if r['strings']]
                    if not has_strings:
                        continue
                        
                    out_json = os.path.join(out_dir, rel_path + '.json')
                    os.makedirs(os.path.dirname(out_json), exist_ok=True)
                    
                    with open(out_json, 'w', encoding='utf-8') as f:
                        json.dump(has_strings, f, indent=4, ensure_ascii=False)
                        
                    print(f"Extracted GXL {rel_path} -> {len(has_strings)} records with text")

if __name__ == '__main__':
    main()
