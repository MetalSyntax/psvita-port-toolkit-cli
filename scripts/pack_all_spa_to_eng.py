import os
import sys

# Add imports for pack_gxl and pack_gst
import pack_gxl
import pack_gst

def main():
    json_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_translation/data/spa"
    base_assets_eng_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/ux0_data/zenonia-2/assets/data/eng"
    
    # Check if the output directory exists
    if not os.path.exists(base_assets_eng_dir):
        print(f"Error: {base_assets_eng_dir} not found.")
        sys.exit(1)

    for file in os.listdir(json_dir):
        if not file.endswith('.json') or file.startswith('._'):
            continue
            
        json_path = os.path.join(json_dir, file)
        out_zt1 = os.path.join(base_assets_eng_dir, file.replace('.zt1.json', '.zt1'))
        
        # We need an original ZT1 file for GXL packing (it patches the file in-place basically)
        orig_zt1 = os.path.join(base_assets_eng_dir, file.replace('.zt1.json', '.zt1'))
        
        if file.startswith('Xls'):
            # It's a GXL
            pack_gxl.pack_gxl(json_path, orig_zt1, out_zt1)
            print(f"Packed GXL: {file} -> {out_zt1}")
        elif file.startswith('Str'):
            # It's a GST
            pack_gst.pack_gst(json_path, out_zt1)
            print(f"Packed GST: {file} -> {out_zt1}")

if __name__ == '__main__':
    main()
