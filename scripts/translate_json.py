import os
import json
import re
import time
from deep_translator import GoogleTranslator

# Pattern for color codes and newline/other tags
TAG_PATTERN = re.compile(r'(!c[0-9A-Fa-f]{6}|![a-zA-Z])')

def replace_tags_with_placeholders(text):
    tags = []
    def replacer(match):
        tags.append(match.group(0))
        return f" <T{len(tags)-1}> "
    
    modified_text = TAG_PATTERN.sub(replacer, text)
    # clean up extra spaces
    modified_text = re.sub(r'\s+', ' ', modified_text).strip()
    return modified_text, tags

def restore_tags(text, tags):
    restored = text
    for i, tag in enumerate(tags):
        # The translator might mess up spaces around the placeholder or lowercase it
        # Try to find <T0>, <t0>, < T0 >, etc.
        pattern = re.compile(r'\s*<\s*[tT]\s*' + str(i) + r'\s*>\s*')
        restored = pattern.sub(tag, restored)
    return restored

def translate_strings(strings):
    translator = GoogleTranslator(source='en', target='es')
    translated_strings = []
    
    for i, s in enumerate(strings):
        if not s.strip():
            translated_strings.append(s)
            continue
            
        try:
            modified_text, tags = replace_tags_with_placeholders(s)
            
            # If the modified text is empty (only tags), don't translate
            if modified_text.strip():
                trans_text = translator.translate(modified_text)
            else:
                trans_text = modified_text
                
            final_text = restore_tags(trans_text, tags)
            translated_strings.append(final_text)
            
            if i % 10 == 0 and i > 0:
                print(f"  Translated {i}/{len(strings)} strings...")
                time.sleep(0.5) # Avoid rate limits
        except Exception as e:
            print(f"  Error translating string {i}: {e}. Keeping original.")
            translated_strings.append(s)
            
    return translated_strings

def main():
    base_dir = "/Volumes/Seagate/PSVITA Develop/Zenonia2-vita/zenonia2_translation/data"
    eng_dir = os.path.join(base_dir, "eng")
    spa_dir = os.path.join(base_dir, "spa")
    
    if not os.path.exists(spa_dir):
        os.makedirs(spa_dir)
        
    for file in os.listdir(eng_dir):
        if not file.endswith(".json"):
            continue
            
        eng_file = os.path.join(eng_dir, file)
        spa_file = os.path.join(spa_dir, file)
        
        if os.path.exists(spa_file):
            print(f"Skipping {file}, already exists in spa.")
            continue
            
        print(f"Processing {file}...")
        with open(eng_file, 'r', encoding='utf-8') as f:
            strings = json.load(f)
            
        translated = translate_strings(strings)
        
        with open(spa_file, 'w', encoding='utf-8') as f:
            json.dump(translated, f, indent=4, ensure_ascii=False)
            
        print(f"Finished {file}.")

if __name__ == '__main__':
    main()
