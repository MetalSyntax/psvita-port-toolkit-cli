import os
import json
import time
from deep_translator import GoogleTranslator

def translate_gxl_records(records):
    translator = GoogleTranslator(source='en', target='es')
    
    for r in records:
        for s in r['strings']:
            original_text = s['text']
            max_len = s['max_length']
            
            # Simple text translation
            try:
                translated = translator.translate(original_text)
                
                # Check byte length to fit in record
                encoded = translated.encode('utf-8')
                if len(encoded) > max_len:
                    # Truncate
                    # Try to truncate at char level to avoid breaking utf-8
                    while len(translated.encode('utf-8')) > max_len:
                        translated = translated[:-1]
                        
                s['translated_text'] = translated
            except Exception as e:
                print(f"Error translating '{original_text}': {e}")
                s['translated_text'] = original_text
                
        time.sleep(0.5) # Avoid rate limits
        
    return records


def main():
    base_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_translation/data"
    eng_dir = os.path.join(base_dir, "eng")
    spa_dir = os.path.join(base_dir, "spa")
    
    if not os.path.exists(spa_dir):
        os.makedirs(spa_dir)
        
    for file in os.listdir(eng_dir):
        if not (file.startswith('Xls') and file.endswith('.zt1.json')):
            continue
            
        eng_file = os.path.join(eng_dir, file)
        spa_file = os.path.join(spa_dir, file)
        
        if os.path.exists(spa_file):
            print(f"Skipping {file}, already exists in spa.")
            continue
            
        print(f"Processing GXL {file}...")
        with open(eng_file, 'r', encoding='utf-8') as f:
            records = json.load(f)
            
        translated = translate_gxl_records(records)
        
        with open(spa_file, 'w', encoding='utf-8') as f:
            json.dump(translated, f, indent=4, ensure_ascii=False)
            
        print(f"Finished GXL {file}.")

if __name__ == '__main__':
    main()
