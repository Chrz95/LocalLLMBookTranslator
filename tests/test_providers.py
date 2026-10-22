"""Native local HTTP transport, provider routing and persistent book identity."""
import hashlib
import json
import tempfile
import threading
import unittest
import zipfile
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

from benchmark import load_suite, run_benchmark
from extractors import load_book
from lmstudio import APIError, LMStudioClient, TRANSLATION_PROMPT
from novel_analysis import ANALYSIS_PROMPT, NovelAnalyzer
from ollama import OllamaClient, parse_ollama_models
from providers import create_client
from settings_manager import Settings, load_settings, save_settings
from translator import CHUNK_VERSION, Job, TranslationEngine, matching_saved_book, saved_books
from test_application import make_epub


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send_json(self, data, status=200):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                requests.append((self.path, None))
                if self.path == "/api/tags":
                    self.send_json({"models":[{"name":"local:latest"}, {"name":"other:latest"}, {"name":"remote:cloud"}]})
                elif self.path == "/api/ps":
                    self.send_json({"models":[{"name":"local:latest"}]})
                else:
                    self.send_json({}, 404)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, data))
                system, user = [message["content"] for message in data["messages"]]
                try:
                    payload = json.loads(user)
                except ValueError:
                    payload = {}
                if system == ANALYSIS_PROMPT:
                    content = '{"terms":[{"source":"Mara","target":"Mara","category":"character"}]}'
                elif '"issues"' in system:
                    content = '{"issues":[]}'
                elif system.startswith("Revise"):
                    content = "Revised translation"
                elif "target_segments" in payload:
                    content = json.dumps({"translations":{k:v.upper() for k,v in payload["target_segments"].items()}})
                else:
                    content = "Ollama translation" if self.path == "/api/chat" else "LM Studio translation"
                if data["model"] == "empty":
                    content = ""
                if self.path == "/api/chat":
                    self.send_json({"message":{"content":content,"thinking":"Never include reasoning in a book"},
                                    "done":True, "done_reason":"stop"})
                else:
                    self.send_json({"choices":[{"finish_reason":"stop","message":{"content":content}}]})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"
        self.settings = Settings(model="local:latest", provider="ollama", retries=0,
                                 lmstudio_url=self.url, ollama_url=self.url)

    def job(self, settings=None, text="Mara enters.\nMara returns.\n"):
        source = self.root/"book.txt"
        source.write_text(text, encoding="utf-8")
        return Job(load_book(source), settings or self.settings, self.root/"jobs")

    def test_factory_selects_provider_endpoint_and_model_discovery(self):
        client = create_client(self.settings)
        self.assertIsInstance(client, OllamaClient)
        models = client.list_models()
        self.assertEqual([m.identifier for m in models], ["local:latest", "other:latest"])
        self.assertEqual(models[0].state, "Loaded")
        self.assertEqual(models[1].state, "Downloaded")
        lm = create_client(replace(self.settings, provider="lmstudio"))
        self.assertIs(type(lm), LMStudioClient)
        self.assertEqual(lm.endpoint, self.url + "/v1/chat/completions")

    def test_native_payload_completion_and_empty_response_are_valid(self):
        client = create_client(self.settings)
        self.assertEqual(client.translate("Mara enters."), "Ollama translation")
        path, body = self.requests[-1]
        self.assertEqual(path, "/api/chat")
        self.assertFalse(body["stream"])
        self.assertEqual(body["model"], "local:latest")
        self.assertEqual(body["messages"][1]["content"], "Mara enters.")
        self.assertEqual(create_client(replace(self.settings, model="empty")).translate("Source text"), "")

    def test_incomplete_and_nontext_outputs_are_rejected(self):
        client = create_client(self.settings)
        for response in ({"done":False,"message":{"content":"partial"}},
                         {"done":True,"done_reason":"length","message":{"content":"cut off"}},
                         {"done":True,"done_reason":"stop","message":{"content":None}}):
            with self.subTest(response=response):
                client._json_request = Mock(return_value=response)
                with self.assertRaises(APIError):
                    client.complete("System", "Source")

    def test_cloud_named_models_are_rejected_before_sending_source(self):
        client = create_client(replace(self.settings, model="model:cloud"))
        client._json_request = Mock()
        with self.assertRaises(APIError):
            client.translate("Private source")
        client._json_request.assert_not_called()

    def test_model_list_validation_and_optional_running_metadata(self):
        with self.assertRaises(APIError):
            parse_ollama_models({"models":[None]})
        client = create_client(self.settings)
        client._json_request = Mock(side_effect=[{"models":[{"name":"local:latest"}]}, APIError("Not supported", False, 404)])
        self.assertEqual(client.list_models()[0].state, "Downloaded")

    def test_analysis_is_off_by_default_and_upgrade_explicit_opt_in_persists(self):
        self.assertFalse(Settings().analyze_novel)
        path = self.root/"settings.json"
        path.write_text('{"model":"test","analyze_novel":true}')
        self.assertFalse(load_settings(path).analyze_novel)
        save_settings(path, replace(self.settings, analyze_novel=True))
        loaded = load_settings(path)
        self.assertTrue(loaded.analyze_novel)
        self.assertEqual(loaded.provider, "ollama")
        self.assertEqual(loaded.ollama_url, self.url)
        self.assertNotIn("api_key", json.loads(path.read_text()))

    def test_default_pipeline_uses_ollama_without_analysis_and_can_opt_in(self):
        job = self.job(text="Mara enters.")
        TranslationEngine(job).run()
        self.assertEqual(job.read_translation(0), "Ollama translation")
        self.assertEqual(len(self.requests), 1)
        self.assertNotEqual(self.requests[0][1]["messages"][0]["content"], ANALYSIS_PROMPT)
        job.configure_translation(replace(job.settings, analyze_novel=True))
        TranslationEngine(job).retranslate_single_chunk(0)
        self.assertEqual(self.requests[1][1]["messages"][0]["content"], ANALYSIS_PROMPT)
        self.assertEqual(job.quality_profile().glossary[0]["source"], "Mara")

    def test_provider_switch_keeps_chunks_bookmarks_identity_and_catalog(self):
        settings = replace(self.settings, provider="lmstudio", model="original")
        job = self.job(settings)
        engine = TranslationEngine(job)
        engine.run()
        engine.set_bookmarks([0], "Keep")
        original_dir = job.directory
        old = job.chunk_path(0).read_bytes()
        job.configure_translation(self.settings)
        TranslationEngine(job).retranslate_single_chunk(1)
        self.assertEqual(job.directory, original_dir)
        self.assertEqual(job.chunk_path(0).read_bytes(), old)
        self.assertEqual(job.read_translation(1), "Ollama translation")
        self.assertEqual(job.get_bookmarks(), {0:"Keep"})
        saved = saved_books(self.root/"jobs")[0]
        self.assertEqual(saved.settings.provider, "lmstudio")
        self.assertEqual(saved.last_provider, "ollama")
        reopened = Job(load_book(saved.source_path), saved.settings, self.root/"jobs")
        reopened.configure_translation(replace(saved.settings, provider=saved.last_provider, model=saved.last_model))
        self.assertEqual(reopened.directory, original_dir)
        self.assertIsInstance(TranslationEngine(reopened).client, OllamaClient)

    def test_legacy_lmstudio_identity_is_unchanged_and_new_ollama_identity_is_distinct(self):
        settings = replace(self.settings, provider="lmstudio")
        job = self.job(settings)
        identity = [job.book.digest, settings.model, settings.input_lang, settings.output_lang, TRANSLATION_PROMPT, CHUNK_VERSION]
        expected = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(job.directory.name, expected)
        ollama_job = Job(job.book, self.settings, self.root/"jobs")
        self.assertNotEqual(ollama_job.directory, job.directory)
        candidate = matching_saved_book(saved_books(self.root/"jobs"), job.book.digest, self.settings, job.directory)
        self.assertEqual(candidate.directory, job.directory)
        self.assertIsNone(matching_saved_book(saved_books(self.root/"jobs"), job.book.digest,
                                             replace(self.settings, output_lang="Greek")))

    def test_proofreading_cache_does_not_cross_providers(self):
        settings = replace(self.settings, provider="lmstudio", proofread=True)
        job = self.job(settings, "Mara enters.")
        TranslationEngine(job).run()
        self.assertEqual(json.loads(job.check_path(0).read_text())["provider"], "lmstudio")
        job.configure_translation(replace(settings, provider="ollama"))
        self.requests.clear()
        TranslationEngine(job).run()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0][0], "/api/chat")
        self.assertEqual(json.loads(job.check_path(0).read_text())["provider"], "ollama")

    def test_revisions_comparisons_and_analysis_identity_use_selected_provider(self):
        job = self.job(text="Mara enters.")
        job.chunk_path(0).write_text("Draft", encoding="utf-8")
        engine = TranslationEngine(job)
        revision = engine.propose_revision(0)
        self.assertEqual(revision["after"], "Revised translation")
        self.assertEqual(revision["provider"], "ollama")
        self.assertEqual(len(engine.compare_models(0, ["local:latest", "other:latest"])), 2)
        self.assertTrue(all(path == "/api/chat" for path,_ in self.requests))
        first = NovelAnalyzer(engine).signature()
        job.configure_translation(replace(job.settings, provider="lmstudio"))
        self.assertNotEqual(first, NovelAnalyzer(TranslationEngine(job)).signature())

    def test_ollama_epub_paragraph_mapping_preserves_assets_and_tags(self):
        source = self.root/"book.epub"
        originals = make_epub(source)
        job = Job(load_book(source), self.settings, self.root/"jobs")
        TranslationEngine(job).run()
        output = self.root/"output.epub"
        job.export(output)
        with zipfile.ZipFile(output) as archive:
            for name, content in originals.items():
                if not name.endswith(".xhtml"):
                    self.assertEqual(archive.read(name), content)
            self.assertIn("<span class='x'>", archive.read("OPS/one.xhtml").decode())
        self.assertTrue(any("target_segments" in body["messages"][1]["content"] for _,body in self.requests))

    def test_benchmark_can_use_ollama_without_a_custom_endpoint(self):
        suite = load_suite(Path(__file__).resolve().parents[1]/"benchmark_suite.json")
        suite["cases"] = suite["cases"][:1]
        results = run_benchmark(suite, self.settings, ["local:latest"], self.root/"benchmark", modes=("plain",))
        self.assertEqual(results["provider"], "ollama")
        self.assertEqual(results["runs"][0]["translation"], "Ollama translation")


if __name__ == "__main__":
    unittest.main()
