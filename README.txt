Disable automatic LM Studio startup

1. Close the translator.
2. Extract these files into the application folder containing gui.py and
   settings_manager.py.
3. On Windows, double-click Apply-Startup-Fix.bat.
   On macOS/Linux, run: python3 disable_lm_studio_startup.py
4. Open the translator normally. Start LM Studio or Ollama yourself and use
   Refresh to discover models.

This update changes only the application's startup handling in gui.py and
settings_manager.py. It leaves provider support, model selection, novel analysis,
translations, prompts, bookmarks, glossary, and your data folder in place.
It ignores older saved "start_lm_studio: true" preferences, hides the retired
startup controls, and keeps automatic model discovery available.

Original source files are backed up to a startup_backup_* folder before writing.
Running the update again is safe. No Python packages need to be installed for
the update script itself. Python 3.10 or newer is required, as for the app.

To update a different installation, run:
python disable_lm_studio_startup.py "/path/to/Offline-Literary-Translator"
