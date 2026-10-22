"""Provider controls with real Tcl variables and no graphical display."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock

from gui import TranslatorGUI
from lmstudio import LocalModel
from settings_manager import load_settings


class ProviderGUITests(unittest.TestCase):
    def controller(self):
        master = tk.Tcl()
        app = TranslatorGUI.__new__(TranslatorGUI)
        app.active_provider = "lmstudio"
        app.provider = tk.StringVar(master, value="LM Studio")
        app.server_urls = {"lmstudio":"http://localhost:1234", "ollama":"http://localhost:11434"}
        app.server_url = tk.StringVar(master, value=app.server_urls["lmstudio"])
        app.provider_models, app.provider_keys, app.model_choices = {}, {}, {}
        app.model_selection = tk.StringVar(master, value="lm-model")
        app.values = {"model":tk.StringVar(master, value="lm-model"), "api_key":tk.StringVar(master, value="lm-secret")}
        app.discovery_generation, app.models_loading, app.busy = 1, True, False
        for name in ("provider_combo", "server_entry", "launch_option", "launch_path_button", "model_combo", "refresh_button"):
            setattr(app, name, Mock())
        app.model_help, app.connection = tk.StringVar(master), tk.StringVar(master)
        app.refresh_models = Mock()
        return app

    def test_switching_clears_model_mapping_and_keeps_credentials_separate(self):
        app = self.controller()
        with tempfile.TemporaryDirectory() as folder:
            app.settings_path = Path(folder)/"settings.json"
            app.provider.set("Ollama")
            app._provider_selected()
            self.assertEqual(app.active_provider, "ollama")
            self.assertEqual(app.server_url.get(), "http://localhost:11434")
            self.assertEqual(app.values["api_key"].get(), "")
            self.assertEqual(app.model_selection.get(), "")
            self.assertEqual(load_settings(app.settings_path).provider, "ollama")
            self.assertFalse(load_settings(app.settings_path).analyze_novel)
            app.model_selection.set("ollama-model:latest")
            app.values["api_key"].set("ollama-secret")
            app.provider.set("LM Studio")
            app._provider_selected()
            self.assertEqual(app.model_selection.get(), "lm-model")
            self.assertEqual(app.values["api_key"].get(), "lm-secret")
            self.assertEqual(app.provider_keys["ollama"], "ollama-secret")
            self.assertNotIn("secret", app.settings_path.read_text())

    def test_stale_model_discovery_cannot_overwrite_current_provider(self):
        app = self.controller()
        app.active_provider = "ollama"
        app.discovery_generation = 3
        app.model_selection.set("ollama-model")
        for event in ({"provider":"lmstudio", "generation":3}, {"provider":"ollama", "generation":2}):
            app._models_received(dict(event, models=[LocalModel("wrong", "Wrong", "Loaded")]))
        self.assertEqual(app.model_selection.get(), "ollama-model")
        app.model_combo.configure.assert_not_called()
        self.assertTrue(app.models_loading)

    def test_provider_controls_are_disabled_during_run_and_lm_launch_only_for_lm(self):
        app = self.controller()
        app.active_provider = "ollama"
        app._provider_controls()
        app.launch_option.configure.assert_called_with(state="disabled")
        app.provider_combo.configure.assert_called_with(state="readonly")
        app.busy = True
        app._provider_controls()
        app.provider_combo.configure.assert_called_with(state="disabled")
        app.server_entry.configure.assert_called_with(state="disabled")

    def test_ollama_selection_does_not_launch_lm_studio_on_startup(self):
        app = self.controller()
        app.active_provider = "ollama"
        app.start_local_studio()
        app.refresh_models.assert_called_once()

    def test_start_reuses_opened_book_when_provider_and_model_change(self):
        # Exercise the actual GUI coordinator without native widgets or inference.
        from extractors import load_book
        from settings_manager import Settings
        from translator import Job
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/"book.txt"
            source.write_text("Mara enters.", encoding="utf-8")
            original = Job(load_book(source), Settings(model="lm-original"), root/"data/jobs")
            original.chunk_path(0).write_text("Saved edit", encoding="utf-8")
            app = self.controller()
            app.job, app.data_directory = original, root/"data"
            app.source = Mock(get=Mock(return_value=str(source)))
            app.summary, app.events = Mock(), Mock()
            app.provider_keys = {"ollama":""}
            app.get_settings = Mock(return_value=Settings(provider="ollama", model="ollama-model"))
            app._background = lambda action: action()
            app._load_book(True)
            loaded = next(call.args[0] for call in app.events.put.call_args_list if call.args[0]["type"] == "loaded")
            self.assertEqual(loaded["job"].directory, original.directory)
            self.assertEqual(loaded["job"].settings.provider, "ollama")
            self.assertEqual(original.read_translation(0), "Saved edit")


if __name__ == "__main__":
    unittest.main()
