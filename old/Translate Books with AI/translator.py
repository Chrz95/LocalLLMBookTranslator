import requests
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import json
from pathlib import Path

from lmstudio import restart_lm_studio
import settings_manager

API_URL = "http://localhost:1234/v1/chat/completions"
MAX_CHARS = 4000

_SETTINGS = settings_manager.load_settings()
PREVIEW_LEN = int(_SETTINGS.get("preview_length", 512))
CHUNKS_DIR = _SETTINGS.get("chunks_dir", "chunks")
CHECKS_DIR = _SETTINGS.get("checks_dir", "checks")
MAX_WORKERS = int(_SETTINGS.get("max_workers", 2))
CHECK_ENABLED_DEFAULT = bool(_SETTINGS.get("check_translations", False))
CHECK_MODEL_DEFAULT = _SETTINGS.get("check_model", "qwen2.5-7b-translator")


# ---------------------------------------------------------
# Helper functions
# ---------------------------------------------------------
def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def write_full_chunk_file(index, text):
    ensure_dir(CHUNKS_DIR)
    path = os.path.join(CHUNKS_DIR, f"{index}.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        logging.info(f"Wrote full chunk to {path}")
        return path
    except Exception as e:
        logging.error(f"Failed to write chunk file for {index}: {e}")
        return None


def write_check_file(index, data):
    ensure_dir(CHECKS_DIR)
    path = os.path.join(CHECKS_DIR, f"{index}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"Wrote check result to {path}")
        return path
    except Exception as e:
        logging.error(f"Failed to write check file for {index}: {e}")
        return None


# ---------------------------------------------------------
# Translation Prompt
# ---------------------------------------------------------
def build_prompt(input_lang, output_lang):
    return (
        "You are an expert literary translator working from "
        f"{input_lang} into {output_lang}. "
        "Your task is to produce a translation that is maximally accurate, faithful, and natural. "
        "Preserve meaning, tone, emotional intensity, pacing, stylistic nuance, and narrative voice. "
        "Do NOT summarize, simplify, shorten, or alter the meaning. "
        "Do NOT add new information. "
        "Maintain character names, terminology, and formatting exactly as in the source.\n\n"

        "Be extremely careful with:\n"
        "- grammatical roles (subject vs. object, agent vs. patient)\n"
        "- verb tense, aspect, and mood (past, present, future, perfect, progressive, subjunctive)\n"
        "- pronoun references and who is acting upon whom\n"
        "- number and gender agreement where applicable\n"
        "- word order and emphasis\n"
        "- idioms, metaphors, and figurative language\n"
        "- ambiguous phrasing (preserve ambiguity rather than resolving it)\n\n"

        "Handle expressive spellings, elongated sounds, and emotional outbursts by mapping them "
        "to natural equivalents in the target language while preserving intensity and style.\n"
        "Examples:\n"
        "- 'Neeeiiin!' → 'Nooooo!'\n"
        "- 'Jaaaaa!' → 'Yessss!'\n"
        "- 'Waaaarum?!' → 'Whyyyy?!'\n"
        "- 'Okaaayyyy…' → 'Okaaayyyy…'\n\n"

        "If the source uses informal, dialectal, or playful language, reproduce the same effect "
        "in the target language without changing the meaning.\n\n"

        "Your output must read like a fluent, natural text in the target language, "
        "but remain strictly faithful to the original meaning, grammar, and intent."
    )


# ---------------------------------------------------------
# Chunking
# ---------------------------------------------------------
def chunk_unit(text):
    chunks = []
    while len(text) > MAX_CHARS:
        chunks.append(text[:MAX_CHARS])
        text = text[MAX_CHARS:]
    chunks.append(text)
    return chunks


def chunk_all_units(units):
    all_chunks = []
    index_map = []
    for idx, unit in enumerate(units):
        for ch in chunk_unit(unit):
            all_chunks.append(ch)
            index_map.append(idx)
    return all_chunks, index_map


# ---------------------------------------------------------
# Check system
# ---------------------------------------------------------
def check_translation_with_lmstudio(index, text, gui, lm_path, check_model=None):
    ensure_dir(CHECKS_DIR)
    model = check_model or CHECK_MODEL_DEFAULT

    system_prompt = (
        "You are an expert editor and proofreader for literary translations. "
        "Check the provided translated text for: spelling mistakes, grammar errors, punctuation, "
        "stylistic awkwardness, and any logical or factual inconsistencies introduced during translation. "
        "Return a JSON object with keys: 'issues' (list of short issue descriptions), "
        "'suggested_fix' (optional short corrected excerpt), and 'confidence' (0-1). "
        "If no issues, return an empty 'issues' list."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Please check this translated chunk (index {index}):\n\n{text}"}
        ],
        "temperature": 0.0
    }

    try:
        gui.enqueue_update(index + 1, "Checking...", "pending", None)
        resp = requests.post(API_URL, json=payload, timeout=60)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        try:
            parsed = json.loads(content)
        except Exception:
            parsed = {"raw": content}

        write_check_file(index, parsed)

        issues = parsed.get("issues") if isinstance(parsed, dict) else None
        if issues:
            gui.enqueue_update(index + 1, "Issues found", "issues", None)
        else:
            gui.enqueue_update(index + 1, "Checked", "checked", None)

        return parsed

    except Exception as e:
        logging.error(f"Check failed for chunk {index}: {e}")
        gui.enqueue_update(index + 1, f"Check error: {e}", "error", None)
        return {"error": str(e)}


# ---------------------------------------------------------
# Translate single chunk
# ---------------------------------------------------------
def translate_chunk(chunk, prompt, index, gui, lm_path, model):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": chunk}
        ]
    }

    settings = settings_manager.load_settings()
    check_enabled = bool(settings.get("check_translations", CHECK_ENABLED_DEFAULT))
    check_model = settings.get("check_model", CHECK_MODEL_DEFAULT)

    for attempt in range(1, 6):
        if gui.is_cancelled():
            gui.enqueue_update(index + 1, "Cancelled", "skipped", "")
            return ""

        while gui.is_paused():
            time.sleep(0.5)

        try:
            gui.enqueue_update(index + 1, "Translating...", "pending", None)
            response = requests.post(API_URL, json=payload, timeout=120)
            response.raise_for_status()

            text = response.json()["choices"][0]["message"]["content"]

            write_full_chunk_file(index, text)

            preview = text[:PREVIEW_LEN]
            gui.enqueue_update(index + 1, "Completed", "completed", preview)

            if check_enabled:
                try:
                    check_translation_with_lmstudio(index, text, gui, lm_path, check_model=check_model)
                except Exception as e:
                    logging.error(f"Post-check failed for chunk {index}: {e}")

            return text

        except Exception as e:
            gui.enqueue_update(index + 1, f"Error: {e}", "error", None)

            if "Connection refused" in str(e) or "Failed to establish" in str(e):
                restart_lm_studio(lm_path)

            if attempt < 5:
                gui.enqueue_update(index + 1, "Retrying...", "retry", None)
                time.sleep(60)
            else:
                gui.enqueue_update(index + 1, "Skipped", "skipped", None)
                return ""


# ---------------------------------------------------------
# Retranslate single chunk
# ---------------------------------------------------------
def retranslate_single_chunk(index, chunks, prompt, gui, lm_path, model):
    logging.info(f"Retranslating chunk {index}")

    gui.enqueue_update(index + 1, "Retranslating...", "retry", None)

    result = translate_chunk(chunks[index], prompt, index, gui, lm_path, model)

    preview = result[:PREVIEW_LEN] if result else ""
    gui.enqueue_update(index + 1, "Retranslated", "retranslated", preview)

    logging.info(f"Chunk {index} retranslated")

    return result


# ---------------------------------------------------------
# Translate all chunks (resume via chunk files only)
# ---------------------------------------------------------
def translate_all_chunks(chunks, prompt, gui, lm_path, model, max_workers=None):
    workers = max_workers if max_workers is not None else MAX_WORKERS
    logging.info(f"Starting parallel translation with max_workers={workers}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}

        for i, chunk in enumerate(chunks):
            chunk_path = os.path.join(CHUNKS_DIR, f"{i}.txt")

            if os.path.exists(chunk_path):
                try:
                    with open(chunk_path, "r", encoding="utf-8") as f:
                        full_text = f.read()
                    preview = full_text[:PREVIEW_LEN]
                    gui.enqueue_update(i + 1, "Resumed", "resumed", preview)
                    continue
                except Exception as e:
                    logging.warning(f"Failed to read existing chunk file {chunk_path}: {e}")

            futures[executor.submit(translate_chunk, chunk, prompt, i, gui, lm_path, model)] = i

        for future in as_completed(futures):
            i = futures[future]
            try:
                _ = future.result()
            except Exception as e:
                logging.error(f"Chunk {i} failed in future: {e}")

    logging.info("All chunks processed")
