import json
import os
import logging

SETTINGS_FILE = "settings.json"

# This variable stores the last input EPUB file path
# so write_epub_output() can clone the original EPUB structure.
last_input_file = None


DEFAULT_SETTINGS = {
    "preview_length": 512,
    "chunks_dir": "chunks",
    "checks_dir": "checks",
    "max_workers": 2,
    "check_translations": False,
    "check_model": "qwen2.5-7b-translator"
}


def load_settings():
    """
    Load settings.json if it exists, otherwise return defaults.
    """
    if not os.path.exists(SETTINGS_FILE):
        return DEFAULT_SETTINGS.copy()

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Merge with defaults to avoid missing keys
        merged = DEFAULT_SETTINGS.copy()
        merged.update(data)
        return merged

    except Exception as e:
        logging.error(f"Failed to load settings.json: {e}")
        return DEFAULT_SETTINGS.copy()


def save_settings(settings):
    """
    Save settings.json safely.
    """
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"Failed to save settings.json: {e}")
        return False
