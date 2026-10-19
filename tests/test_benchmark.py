"""Benchmark transport, safe review reports and truthful failure handling."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

from benchmark import evaluate_case, load_suite, reference_context, run_benchmark
from lmstudio import APIError
from quality import QualityProfile
from settings_manager import Settings


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.suite = load_suite(Path(__file__).resolve().parents[1] / "benchmark_suite.json")

    def test_required_skips_do_not_call_translation_or_checker(self):
        client, checker = Mock(), Mock()
        for text in ("", "Ja.", "...?!", "x"):
            result = evaluate_case(client, checker, {"source": text}, {})
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["translation"], "")
        client.translate.assert_not_called()
        checker.check.assert_not_called()

    def test_empty_errors_and_failed_checker_have_distinct_results(self):
        client, checker = Mock(), Mock()
        client.translate.return_value = ""
        self.assertEqual(evaluate_case(client, checker, {"source": "Unclear source"}, None)["status"], "empty")
        checker.check.assert_not_called()
        client.translate.side_effect = APIError("Connection failure")
        failed = evaluate_case(client, None, {"source": "Clear source"}, None)
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["error"], "Connection failure")
        client.translate.side_effect = None
        client.translate.return_value = "Retained translation"
        checker.check.side_effect = APIError("Bad schema")
        reviewed = evaluate_case(client, checker, {"source": "Clear source"}, None)
        self.assertEqual(reviewed["status"], "translated")
        self.assertEqual(reviewed["translation"], "Retained translation")
        self.assertEqual(reviewed["check_error"], "Bad schema")

    def test_suite_rejects_duplicate_ids_and_oversize_targets(self):
        self.suite["cases"].append(self.suite["cases"][0])
        path = self.root / "suite.json"
        path.write_text(json.dumps(self.suite))
        with self.assertRaises(ValueError):
            load_suite(path)
        self.suite["cases"].pop()
        self.suite["cases"][0]["source"] = "x" * 4001
        path.write_text(json.dumps(self.suite))
        with self.assertRaises(ValueError):
            load_suite(path)

    def test_context_budget_omits_review_answer(self):
        case = {"source": "Target", "before": "A" * 1500, "after": "B" * 1000,
                "reference": "SECRET_REFERENCE", "review_notes": "SECRET_CRITERIA"}
        context = reference_context(case, QualityProfile(context_chars=1000))
        self.assertEqual(sum(len(s["text"]) for s in context["source_segments"]), 1000)
        self.assertTrue(context["context_truncated"])
        self.assertNotIn("SECRET", json.dumps(context))

    def test_real_http_pipeline_saves_report_without_reference_leak(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                response = {"choices": [{"finish_reason": "stop", "message": {
                    "content": "<script>NOT_EXECUTABLE</script>"}}]}
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        suite = dict(self.suite)
        suite["cases"] = [dict(self.suite["cases"][0]), self.suite["cases"][-1]]
        suite["cases"][0]["reference"] = "SECRET_REFERENCE"
        suite["cases"][0]["review_notes"] = "SECRET_CRITERIA"
        suite["cases"][0]["before"] = "Previous source sentence."
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        settings = Settings(analyze_novel=False, input_lang="German", output_lang="English", api_key="not-saved")
        results = run_benchmark(suite, settings, ["local-model"], self.root, endpoint)
        self.assertEqual(results["status"], "completed")
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(results["runs"]), 4)
        self.assertEqual(requests[0]["messages"][1]["content"], suite["cases"][0]["source"])
        self.assertIn("reference", json.loads(requests[1]["messages"][1]["content"]))
        self.assertNotIn("SECRET", json.dumps(requests))
        report = (self.root / "report.html").read_text()
        self.assertIn("&lt;script&gt;NOT_EXECUTABLE&lt;/script&gt;", report)
        self.assertNotIn("<script>", report)
        saved = (self.root / "results.json").read_text()
        self.assertNotIn("not-saved", saved)
        self.assertEqual(json.loads(saved)["runs"], results["runs"])


if __name__ == "__main__":
    unittest.main()
