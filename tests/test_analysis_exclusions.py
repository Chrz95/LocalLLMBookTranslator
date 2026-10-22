"""A rejected suggestion must not erase valid entries or block a novel translation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from analysis_parser import parse_terms
from extractors import load_book
from lmstudio import APIError, LMStudioClient
from novel_analysis import ANALYSIS_PROMPT, excluded_suggestions, exclusion_count
from quality import QualityProfile
from settings_manager import Settings
from translator import Job, TranslationEngine


class AnalysisExclusionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def engine(self, source="Mara enters."):
        path = self.root / "novel.txt"
        path.write_text(source, encoding="utf-8")
        settings = Settings(analyze_novel=True, model="test-model", retries=0)
        job = Job(load_book(path), settings, self.root / "jobs")
        client = LMStudioClient(settings)
        return TranslationEngine(job, client=client)

    def test_entry_25_is_excluded_and_valid_entries_on_both_sides_are_saved(self):
        names = [f"Term{i:02d}" for i in range(24)] + ["Tail"]
        engine = self.engine(" ".join(names))
        values = [{"source":name, "target":name} for name in names[:-1]]
        values += [{"source":"NotInThisPassage", "target":"Wrong"}, {"source":"Tail", "target":"Tail"}]
        engine.client.complete = Mock(side_effect=lambda system, user: json.dumps({"terms":values}) if system == ANALYSIS_PROMPT else "Translation")
        engine.run()
        self.assertEqual(len(engine.job.quality_profile().glossary), 25)
        self.assertNotIn("NotInThisPassage", [e["source"] for e in engine.job.quality_profile().glossary])
        self.assertEqual(engine.job.read_translation(0), "Translation")
        self.assertEqual(engine.client.complete.call_args_list[0].args[0], ANALYSIS_PROMPT)
        self.assertEqual(engine.client.complete.call_count, 2)
        review = excluded_suggestions(engine.job.directory)
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]["entry_number"], 25)
        self.assertEqual(review[0]["entry"]["source"], "NotInThisPassage")
        self.assertIn("does not occur", review[0]["reason"])
        self.assertEqual(exclusion_count(engine.job.directory), 1)

    def test_all_invalid_candidates_are_visible_and_existing_glossary_is_preserved(self):
        engine = self.engine()
        original = QualityProfile(glossary=[{"source":"Mara","target":"Human choice"}])
        original.save(engine.job.directory / "quality.json")
        values = [{"source":"InventedName","target":"Name"}, {"source":"Mara","target":17}, "wrong shape"]
        engine.client.complete = Mock(side_effect=lambda system, user: json.dumps({"terms":values}) if system == ANALYSIS_PROMPT else "Translated")
        engine.run()
        self.assertEqual(engine.job.quality_profile(), original)
        self.assertEqual(engine.job.read_translation(0), "Translated")
        self.assertEqual(len(excluded_suggestions(engine.job.directory)), 3)
        report = json.loads((engine.job.directory / "analysis/report.json").read_text())
        self.assertEqual(report["outcome"], "completed_with_exclusions")
        self.assertEqual(report["added_terms"], 0)
        self.assertEqual(report["excluded_count"], 3)

    def test_cached_exclusions_reappear_and_reanalysis_can_clear_them(self):
        engine = self.engine()
        first = json.dumps({"terms":[{"source":"Absent","target":"Name"}, {"source":"Mara","target":"Mara"}]})
        engine.client.complete = Mock(side_effect=lambda system, user: first if system == ANALYSIS_PROMPT else "Translated")
        engine.run()
        (engine.job.directory / "analysis/report.json").unlink()
        engine.client.complete = Mock(side_effect=AssertionError("Must reuse analysis cache"))
        events = []
        engine.emit = events.append
        report = engine.analyze_novel(force=False)
        self.assertEqual(report["cached_passages"], 1)
        self.assertEqual(report["excluded_count"], 1)
        self.assertTrue(any(e.get("excluded_count") == 1 for e in events))
        engine.client.complete = Mock(return_value='{"terms":[]}')
        engine.analyze_novel(force=True)
        self.assertEqual(exclusion_count(engine.job.directory), 0)
        self.assertEqual(excluded_suggestions(engine.job.directory), [])
        self.assertEqual(engine.job.read_translation(0), "Translated")

    def test_cancelled_scan_caches_exclusions_for_resume(self):
        engine = self.engine("Mara waits. " * 450)
        engine.client.complete = Mock(side_effect=lambda system, user: '{"terms":[{"source":"Absent","target":"Name"}]}' if system == ANALYSIS_PROMPT else "Translated")
        def stop(event):
            if event["type"] == "analysis" and event.get("completed") == 1:
                engine.cancel()
        engine.emit = stop
        engine.run()
        self.assertFalse(list(engine.job.chunks_dir.glob("*.txt")))
        self.assertFalse((engine.job.directory / "quality.json").exists())
        self.assertEqual(len(excluded_suggestions(engine.job.directory)), 1)
        resumed = TranslationEngine(engine.job, client=engine.client)
        resumed.run()
        self.assertTrue(list(engine.job.chunks_dir.glob("*.txt")))
        self.assertEqual(exclusion_count(engine.job.directory), len(excluded_suggestions(engine.job.directory)))

    def test_tolerant_mode_reports_all_entry_errors_but_bad_json_remains_fatal(self):
        rejected = []
        result = parse_terms('{"terms":[{"source":"Missing","target":"Name"}, {"source":"Mara","target":"Mara"}, false]}',
                             "Mara enters.", on_rejected=rejected.append)
        self.assertEqual([e["source"] for e in result], ["Mara"])
        self.assertEqual([e["entry_number"] for e in rejected], [1, 3])
        with self.assertRaises(APIError):
            parse_terms('{"terms":', "Mara", on_rejected=rejected.append)

    def test_legacy_cache_without_exclusion_fields_still_loads(self):
        engine = self.engine()
        engine.client.complete = Mock(side_effect=lambda system, user: '{"terms":[{"source":"Mara","target":"Mara"}]}' if system == ANALYSIS_PROMPT else "Translated")
        engine.run()
        cache = next((engine.job.directory / "analysis/passages").glob("*.json"))
        data = json.loads(cache.read_text())
        data.pop("excluded_entries", None)
        cache.write_text(json.dumps(data))
        (engine.job.directory / "analysis/report.json").unlink()
        engine.client.complete = Mock(side_effect=AssertionError("No new inference"))
        report = engine.analyze_novel(force=False)
        self.assertEqual(report["excluded_count"], 0)
        self.assertEqual(report["outcome"], "completed")

    def test_exclusion_reader_rejects_malformed_review_files(self):
        directory = self.root / "job" / "analysis" / "review"
        directory.mkdir(parents=True)
        (directory / "bad.json").write_text('{"excluded_entries":42}')
        with self.assertRaises(ValueError):
            excluded_suggestions(self.root / "job")


if __name__ == "__main__":
    unittest.main()
