#!/usr/bin/env python3
import sys
import os
import json

def run():
    if len(sys.argv) < 2:
        print("Usage: pdf-tool <file_path> [format: markdown|json]")
        return
    
    file_path = sys.argv[1]
    fmt = sys.argv[2] if len(sys.argv) > 2 else "markdown"
    if not os.path.exists(file_path):
        return print(f"Error: File {file_path} not found.")

    import pdf_inspector # Lazy load heavy library

    try:
        text = pdf_inspector.extract_text(file_path)
        result = pdf_inspector.process_pdf(file_path)
        is_scanned = getattr(result, 'is_scanned', False) if result else False
        
        if fmt == "json":
            print(json.dumps({"is_scanned": is_scanned, "text": text, "length": len(text.strip())}, ensure_ascii=False, indent=2))
        else:
            if not text.strip(): print("--- [Warning: No text detected. Possible scanned PDF.] ---")
            print(text)
    except Exception as e:
        print(f"Error processing PDF: {e}")

if __name__ == "__main__":
    run()
