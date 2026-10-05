AI Book Translator — README
Overview
AI Book Translator is a local, offline-first tool that uses a locally hosted LM Studio API to translate books and long documents. It splits input files into chunks, translates each chunk with a chosen model, saves full translated chunks to disk, and shows lightweight previews in a responsive GUI. Optional post‑translation checks (spelling, grammar, logical consistency) run against each chunk using LM Studio and save structured results.

Key features

Responsive GUI launcher with tabs for Translate, Chunks, Settings, and Logs.

Background translation (non‑blocking) with thread‑safe UI updates.

Chunked processing: full chunks are written to disk (chunks/) to minimize RAM usage; GUI shows previews only.

Resume support via resume_progress.json.

Optional post‑check of translated chunks using LM Studio; results saved to checks/.

Check‑only mode to run checks on existing chunk files without re‑translating.

Configurable settings persisted in settings.json.

Quick start
1. Prerequisites
Python 3.9+ with Tkinter available.

LM Studio or compatible local model server running and reachable (default http://localhost:1234).

Write permission in the project folder (for chunks/, checks/, translator.log, settings.json).

2. Install (recommended)
Create and activate a virtual environment (optional but recommended):

bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
No external pip packages are required for the GUI and core scripts (uses standard library). If you add optional dependencies later, install them into the venv.

3. Run the launcher
From the project root (where main_gui.py lives):

bash
python main_gui.py
If nothing appears, run from a terminal to capture errors:

bash
python main_gui.py 2> main_gui_error.log
4. Run headless or check-only
Translate from the command line:

bash
python main.py input.epub output-translated.epub --file-type epub --input-lang de --output-lang en --model qwen2.5-7b-translator
Run checks only on existing chunk files:

bash
python main.py --check-only --check-model qwen2.5-7b-translator
File layout and important files
main_gui.py — modern launcher UI (Translate / Chunks / Settings / Logs).

main.py — CLI entrypoint for translation and check-only modes.

translator.py — chunking, translation, post‑check logic; writes chunks/<index>.txt and checks/<index>.json.

gui.py — progress window used by main.py (thread‑safe updates).

resume.py — resume_progress.json read/write helpers.

settings_manager.py — load/save settings.json.

settings.json — persisted user settings (preview length, chunks dir, checks dir, max_workers, auto_clear_chunks, auto_increment, check_translations, check_model).

chunks/ — full translated chunk files (0.txt, 1.txt, ...).

checks/ — check results per chunk (0.json, 1.json, ...).

translator.log — runtime log for debugging.

Settings (editable via Settings tab or settings.json)
Common keys

preview_length: number of characters shown in GUI preview (default 512).

chunks_dir: directory for full chunk files (default chunks).

checks_dir: directory for check results (default checks).

max_workers: parallel requests to LM Studio (default 2). Lower to reduce memory/CPU.

progress_file: resume file (default resume_progress.json).

auto_clear_chunks: automatically delete old chunks when starting a new translation.

auto_increment: auto-increment output filename if it exists.

check_translations: enable post‑check by default.

check_model: model used for checks.

Edit via the Settings tab in the launcher or directly in settings.json.

Best practices and tips
Start small: test with a short document and max_workers=1 to confirm behavior.

Keep LM Studio local: the app expects a local LM Studio API; ensure it’s running and healthy. Use the launcher’s Refresh/Health indicator.

Use check-only when you already have chunks/ and want to run or re-run checks without re-translating.

Resume: if translation stops, restart the launcher; existing chunks/ and resume_progress.json will be used to resume.

Disk space: full chunks are stored on disk — ensure sufficient space for large books. Consider compressing or archiving old chunks.

Reduce memory: lower max_workers and preview_length in settings.

Troubleshooting
No GUI window appears

Run from a terminal to capture errors: python main_gui.py 2> main_gui_error.log.

Verify Tkinter: python -c "import tkinter; print(tkinter.TkVersion)". Install OS package if missing (python3-tk on Debian/Ubuntu).

Progress bar not updating

Ensure worker threads call gui.enqueue_update(...) (the provided code does).

If using high concurrency, set max_workers to 1 to test ordering.

LM Studio health shows DOWN

Confirm LM Studio is running and reachable at http://localhost:1234.

If LM Studio runs on a different port, update lmstudio.py or provide the correct path in the launcher.

High memory usage

Lower max_workers in settings.

Confirm that full texts are written to chunks/ and not kept in memory.

Permissions

Ensure the process can write to the working directory or change chunks_dir/checks_dir to a writable path.

Development notes
The GUI and translation logic are intentionally separated: the GUI launches main.py as a subprocess for translation/check tasks. main.py opens a ProgressGUI window and runs translation in background threads to avoid blocking the UI.

Chunk files and check results are stored on disk to minimize memory footprint and enable resume.

The post‑check prompt expects LM Studio to return JSON; if your model returns plain text, the check result is stored under raw in the JSON file.

Contributing
Fork the repository.

Create a feature branch: git checkout -b feature/your-feature.

Run tests and linting (if added).

Submit a pull request with a clear description and rationale.

License
Include your preferred license file in the project root (e.g., LICENSE). If none is present, the project is provided as-is.

Contact / Support
If you run into issues, collect:

main_gui_error.log (if present),

last 50 lines of translator.log (tail -n 50 translator.log),

settings.json contents (sanitized if needed),
and share them when asking for help.

Quick commands summary

bash
# Run launcher
python main_gui.py

# Translate from CLI
python main.py input.epub output.epub --file-type epub --input-lang de --output-lang en --model qwen2.5-7b-translator

# Check-only mode
python main.py --check-only --check-model qwen2.5-7b-translator