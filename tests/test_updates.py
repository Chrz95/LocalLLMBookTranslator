"""Regression tests for skip rules, model discovery and dropdown state."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

from extractors import load_book
try:
    import tkinter as tk
    from gui import TranslatorGUI
except ImportError:
    tk = None
    TranslatorGUI = None
from lmstudio import APIError, LMStudioClient, LocalModel
from settings_manager import Settings
from translator import Job, TranslationEngine, should_skip


class UpdateTests(unittest.TestCase):
    def test_skip_rule_handles_unicode_and_real_text(self):
        for value in ("", " \t\n", "A", "é", "e\u0301", "字", ".", ",", "...", "—", "?!", "★", "7"):
            with self.subTest(value=value):
                self.assertTrue(should_skip(value))
        for value in ("Hi", "No!", "Hello, world.", "你好", "12", "A."):
            self.assertFalse(should_skip(value))

    def test_skips_write_empty_files_without_translation_or_proofreading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text(" \nA\n.\n,\n…?!\ne\u0301\nWord\n", encoding="utf-8")
            settings = Settings(model="local-test", proofread=True)
            job = Job(load_book(source), settings, root / "jobs")
            client = Mock()
            client.translate.return_value = "Translated"
            client.check.return_value = {"issues": []}
            events = []
            # A pre-update cache must not leak content into a newly skipped chunk.
            job.chunk_path(1).write_text("OLD TRANSLATION", encoding="utf-8")
            job.check_path(1).write_text('{"issues": []}', encoding="utf-8")
            engine = TranslationEngine(job, events.append, client)
            engine.run()
            client.translate.assert_called_once_with("Word")
            client.check.assert_called_once_with("Word", "Translated")
            self.assertEqual(len([e for e in events if e.get("status") == "Skipped"]), 6)
            for index in range(6):
                self.assertEqual(job.chunk_path(index).read_bytes(), b"")
                self.assertFalse(job.check_path(index).exists())
                self.assertEqual(job.read_translation(index), "")
            engine.run()
            engine.retranslate_single_chunk(1)
            self.assertEqual(client.translate.call_count, 1)
            self.assertEqual(client.check.call_count, 1)
            out = root / "out.txt"
            job.export(out)
            self.assertEqual(out.read_text(), " \n\n\n\n\n\nTranslated\n")

    def model_server(self, routes):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                status, payload = routes.get(self.path, (404, {"error": "missing endpoint"}))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, format, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(stop)
        client = LMStudioClient(Settings(api_key="test-token"),
                                f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
        return client, requests

    def test_native_models_include_downloads_loaded_instances_and_auth(self):
        client, requests = self.model_server({"/api/v1/models": (200, {"models": [
            {"key": "downloaded/key", "display_name": "Offline novel model", "type": "llm", "loaded_instances": []},
            {"key": "loaded/key", "display_name": "Ready model", "type": "llm", "loaded_instances": [{"id": "custom-instance"}]},
            {"key": "embeddings", "type": "embedding", "loaded_instances": []},
        ]})})
        models = client.list_models()
        self.assertEqual([model.identifier for model in models], ["custom-instance", "downloaded/key"])
        self.assertEqual([model.state for model in models], ["Loaded", "Downloaded"])
        self.assertEqual(requests, [("/api/v1/models", "Bearer test-token")])

    def test_legacy_fallback_excludes_embeddings(self):
        client, requests = self.model_server({"/api/v0/models": (200, {"data": [
            {"id": "old-model", "type": "vlm", "state": "loaded"},
            {"id": "embedding", "type": "embeddings", "state": "not-loaded"},
        ]})})
        models = client.list_models()
        self.assertEqual([m.identifier for m in models], ["old-model"])
        self.assertEqual(len(requests), 2)

    def test_openai_fallback_and_empty_list(self):
        client, requests = self.model_server({"/v1/models": (200, {"data": [{"id": "openai-id"}]})})
        self.assertEqual(client.list_models()[0].identifier, "openai-id")
        self.assertEqual([r[0] for r in requests], ["/api/v1/models", "/api/v0/models", "/v1/models"])
        empty, _ = self.model_server({"/api/v1/models": (200, {"models": []})})
        self.assertEqual(empty.list_models(), [])

    def test_auth_and_malformed_lists_fail_clearly(self):
        client, requests = self.model_server({"/api/v1/models": (401, {"error": "auth"})})
        with self.assertRaisesRegex(APIError, "API key"):
            client.list_models()
        self.assertEqual(len(requests), 1)
        broken, _ = self.model_server({"/api/v1/models": (200, {"models": [None]})})
        with self.assertRaisesRegex(APIError, "Malformed"):
            broken.list_models()

    def dropdown_controller(self):
        interpreter = tk.Tcl()  # No display needed for StringVar and event-state tests.
        gui = TranslatorGUI.__new__(TranslatorGUI)
        gui.models_loading = True
        gui.busy = False
        gui.model_choices = {}
        gui.model_combo = Mock()
        gui.refresh_button = Mock()
        gui.model_selection = tk.StringVar(interpreter, value="saved-model")
        gui.values = {"model": tk.StringVar(interpreter, value="saved-model")}
        gui.connection = tk.StringVar(interpreter)
        gui.model_help = tk.StringVar(interpreter)
        return gui

    @unittest.skipIf(tk is None, "Tk/Tcl runtime is not installed")
    def test_model_refresh_preserves_selection_and_maps_exact_identifier(self):
        gui = self.dropdown_controller()
        models = [LocalModel("first-model", "First", "Loaded"),
                  LocalModel("saved-model", "Saved", "Downloaded")]
        gui._models_received({"models": models})
        self.assertEqual(gui.model_selection.get(), models[1].label)
        self.assertEqual(gui.values["model"].get(), "saved-model")
        gui.model_selection.set(models[0].label)
        gui._model_selected()
        self.assertEqual(gui.values["model"].get(), "first-model")
        gui.busy = True
        gui._models_received({"models": [LocalModel("first-model", "First", "Downloaded")]})
        gui._model_selected()
        self.assertEqual(gui.values["model"].get(), "first-model")
        gui.busy = False
        gui._models_received({"models": []})
        self.assertEqual(gui.model_selection.get(), "first-model")
        self.assertEqual(gui.values["model"].get(), "first-model")
        gui.model_selection.set("manual-identifier")
        gui._model_selected()
        self.assertEqual(gui.values["model"].get(), "manual-identifier")

    @unittest.skipIf(tk is None, "Tk/Tcl runtime is not installed")
    def test_refresh_does_not_change_active_job_or_selection_on_failure(self):
        gui = self.dropdown_controller()
        gui.busy = True
        gui._models_received({"models": [LocalModel("new", "New", "Loaded")]})
        self.assertEqual(gui.values["model"].get(), "saved-model")
        self.assertEqual(gui.model_selection.get(), "saved-model")
        gui._models_received({"error": "Server unavailable"})
        self.assertEqual(gui.values["model"].get(), "saved-model")
        self.assertIn("Server unavailable", gui.model_help.get())


if __name__ == "__main__":
    unittest.main()
