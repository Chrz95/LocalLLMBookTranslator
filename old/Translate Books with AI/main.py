import argparse
import sys
import logging
import threading
import os
from pathlib import Path

from extractors import extract_units, write_output
from translator import (
    chunk_all_units,
    translate_all_chunks,
    build_prompt,
    retranslate_single_chunk,
    check_translation_with_lmstudio
)
from gui import ProgressGUI
from lmstudio import find_lm_studio_path
import settings_manager

logging.basicConfig(
    filename="translator.log",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

_SETTINGS = settings_manager.load_settings()
CHUNKS_DIR = _SETTINGS.get("chunks_dir", "chunks")
PREVIEW_LEN = int(_SETTINGS.get("preview_length", 512))
MAX_WORKERS = int(_SETTINGS.get("max_workers", 2))


def reassemble_units_from_files(index_map):
    """
    Reassemble translated units by reading chunk files from CHUNKS_DIR.
    index_map: list mapping chunk_index -> unit_index
    Returns list of units (strings) in original unit order.
    """
    units = {}
    for chunk_idx, unit_idx in enumerate(index_map):
        path = os.path.join(CHUNKS_DIR, f"{chunk_idx}.txt")
        text = ""
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                text = ""
        units.setdefault(unit_idx, "")
        units[unit_idx] += text
    return [units[i] for i in sorted(units.keys())]


def list_chunk_files_sorted(chunks_dir):
    """
    Return list of chunk file paths sorted by numeric index (0.txt, 1.txt, ...).
    """
    p = Path(chunks_dir)
    if not p.exists() or not p.is_dir():
        return []
    files = []
    for f in p.iterdir():
        if f.is_file() and f.suffix == ".txt":
            try:
                idx = int(f.stem)
                files.append((idx, str(f)))
            except Exception:
                continue
    files.sort(key=lambda x: x[0])
    return [fp for idx, fp in files]


def main():
    parser = argparse.ArgumentParser(description="Offline AI Book Translator using LM Studio")
    parser.add_argument("input_file", nargs="?", help="Input file path (optional for check-only)")
    parser.add_argument("output_file", nargs="?", help="Output file path (optional for check-only)")
    parser.add_argument("--file-type", choices=["epub", "pdf", "docx", "txt"])
    parser.add_argument("--input-lang", help="Source language code")
    parser.add_argument("--output-lang", help="Target language code", default="en")
    parser.add_argument("--lm-path", help="Manual LM Studio path")
    parser.add_argument("--model", help="Model name to use for translation", default="qwen2.5-7b-translator")
    parser.add_argument("--check-model", help="Model name to use for checks", default=_SETTINGS.get("check_model", "qwen2.5-7b-translator"))
    parser.add_argument("--check-only", action="store_true", help="Only run checks on existing chunk files; do not translate")
    args = parser.parse_args()

    lm_path = args.lm_path or find_lm_studio_path()
    if lm_path is None:
        print("LM Studio path not found. Provide --lm-path or start LM Studio.")
        sys.exit(1)

    # ---------------------------------------------------------
    # CHECK-ONLY MODE
    # ---------------------------------------------------------
    if args.check_only:
        chunk_files = list_chunk_files_sorted(CHUNKS_DIR)
        total = len(chunk_files)
        if total == 0:
            print("No chunk files found in chunks/. Nothing to check.")
            sys.exit(1)

        gui = ProgressGUI(total)

        def worker_check_only():
            check_model = args.check_model
            for idx, path in enumerate(chunk_files):
                if gui.is_cancelled():
                    break

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        text = f.read()
                except Exception as e:
                    gui.enqueue_update(idx + 1, f"Read error: {e}", "error", None)
                    continue

                preview = text[:PREVIEW_LEN]
                gui.enqueue_update(idx + 1, "Loaded", "resumed", preview)

                try:
                    check_translation_with_lmstudio(idx, text, gui, lm_path, check_model=check_model)
                except Exception as e:
                    gui.enqueue_update(idx + 1, f"Check error: {e}", "error", None)

            gui.root.after(0, lambda: gui.force_complete())

        threading.Thread(target=worker_check_only, daemon=True).start()
        gui.root.mainloop()
        return

    # ---------------------------------------------------------
    # NORMAL TRANSLATION MODE
    # ---------------------------------------------------------
    if not args.input_file or not args.output_file:
        print("Input and output files required for translation mode.")
        sys.exit(1)

    input_path = args.input_file
    output_path = args.output_file
    file_type = args.file_type
    input_lang = args.input_lang
    output_lang = args.output_lang
    model = args.model

    # Extract units + cover
    units, cover = extract_units(input_path, file_type)

    all_chunks, index_map = chunk_all_units(units)
    prompt = build_prompt(input_lang, output_lang)

    gui = ProgressGUI(
        len(all_chunks),
        retranslate_callback=lambda idx: gui.root.after(
            0,
            lambda: retranslate_single_chunk(idx, all_chunks, prompt, gui, lm_path, model)
        )
    )

    def worker_translate():
        translate_all_chunks(all_chunks, prompt, gui, lm_path, model, max_workers=MAX_WORKERS)
        translated_units = reassemble_units_from_files(index_map)

        try:
            write_output(translated_units, output_path, file_type, output_lang, cover)
        except Exception as e:
            logging.error(f"Failed to write output: {e}")

        gui.root.after(0, lambda: gui.force_complete())

    threading.Thread(target=worker_translate, daemon=True).start()
    gui.root.mainloop()


if __name__ == "__main__":
    main()
