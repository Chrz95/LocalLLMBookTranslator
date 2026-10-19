"""Headless controller tests using real Tcl variables, without creating a window."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock

from gui import TranslatorGUI
from settings_manager import load_settings


class NovelAnalysisGUITests(unittest.TestCase):
    def test_checkbox_value_is_used_and_saved_even_before_a_model_is_selected(self):
        master = tk.Tcl()
        app = TranslatorGUI.__new__(TranslatorGUI)
        app._model_selected = lambda: None
        app.values = {key: tk.StringVar(master, value=value) for key, value in {
            "model": "test", "input_lang": "German", "output_lang": "English",
            "max_workers": "2", "retries": "0", "timeout": "180", "api_key": ""}.items()}
        app.analyze_novel = tk.BooleanVar(master, value=True)
        app.proofread = tk.BooleanVar(master, value=False)
        app.use_custom_prompt = tk.BooleanVar(master, value=False)
        app.start_lm_studio = tk.BooleanVar(master, value=False)
        app.custom_prompt, app.lmstudio_path = "", ""
        self.assertTrue(app.get_settings().analyze_novel)
        app.analyze_novel.set(False)
        self.assertFalse(app.get_settings().analyze_novel)
        with tempfile.TemporaryDirectory() as folder:
            app.settings_path = Path(folder) / "settings.json"
            app.save_analysis_preference()
            self.assertFalse(load_settings(app.settings_path).analyze_novel)

    def test_analysis_events_show_separate_progress_and_reset_for_translation(self):
        app = TranslatorGUI.__new__(TranslatorGUI)
        app.summary, app.progress = Mock(), Mock()
        app.statuses = {0:"Completed", 1:"Pending", 2:"Skipped"}
        app._handle({"type":"analysis", "detail":"Analyzing 1/5", "total":5, "completed":1})
        app.summary.set.assert_called_with("Analyzing 1/5")
        app.progress.configure.assert_called_with(maximum=5, value=1)
        app._handle({"type":"analysis", "detail":"Ready", "ready":True})
        app.progress.configure.assert_called_with(maximum=3, value=2)


if __name__ == "__main__":
    unittest.main()
