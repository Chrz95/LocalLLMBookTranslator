"""Default-on full-book preparation, source grounding, cancellation and file-only resume."""
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from extractors import load_book
from lmstudio import APIError, LMStudioClient
from novel_analysis import ANALYSIS_PROMPT, analyze_passage, source_batches, validate_terms
from quality import GLOSSARY_CATEGORIES, QualityProfile
from settings_manager import Settings, load_settings, save_settings
from translator import Job, TranslationEngine
from test_application import make_epub


def term(source="Mara", category="character", target="Mara"):
    return {"source": source, "target": target, "category": category,
            "notes": "Keep consistent; verify the proposed treatment.", "evidence": source}


class NovelAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(analyze_novel=True, model="local-test", retries=0)

    def engine(self, text="Mara sees the silver sea.\nMara returns to the silver sea.\n", emit=None):
        source = self.root / "novel.txt"
        source.write_text(text, encoding="utf-8")
        job = Job(load_book(source), self.settings, self.root / "jobs")
        client = LMStudioClient(self.settings)
        client.complete = Mock(side_effect=lambda system, user: json.dumps({"terms": [term()]})
                               if system == ANALYSIS_PROMPT else "Translation")
        return TranslationEngine(job, emit=emit, client=client)

    def test_default_and_legacy_preferences_are_disabled_and_explicit_choice_persists(self):
        self.assertFalse(Settings().analyze_novel)
        path = self.root / "settings.json"
        path.write_text('{"model":"test","analyze_novel":true}')
        self.assertFalse(load_settings(path).analyze_novel)
        save_settings(path, replace(self.settings, analyze_novel=False))
        self.assertFalse(load_settings(path).analyze_novel)
        save_settings(path, replace(self.settings, analyze_novel=True))
        self.assertTrue(load_settings(path).analyze_novel)
        with self.assertRaises(ValueError):
            replace(self.settings, analyze_novel="yes").validate()

    def test_all_categories_are_applied_before_any_translation(self):
        names = ("Mara", "Archon", "Ashen Reach", "Emberwalk", "Veil Order", "Sha’ren", "silver sea", "Oathstone")
        engine = self.engine("Mara sees " + ", ".join(names) + ".\nMara returns to the silver sea.")
        entries = [term(source, category) for source, category in zip(names, GLOSSARY_CATEGORIES)]
        engine.client.complete.side_effect = lambda system, user: json.dumps({"terms": entries}) if system == ANALYSIS_PROMPT else "Translation"
        engine.run()
        calls = engine.client.complete.call_args_list
        self.assertEqual(calls[0].args[0], ANALYSIS_PROMPT)
        for call in calls[1:]:
            sent = json.loads(call.args[1])
            self.assertIn("book_guidance", sent["reference"])
            self.assertIn("Mara", [e["source"] for e in sent["reference"]["book_guidance"]["glossary"]])
        profile = engine.job.quality_profile()
        self.assertEqual({e["category"] for e in profile.glossary}, set(GLOSSARY_CATEGORIES))
        metaphor = next(e for e in profile.glossary if e["source"] == "silver sea")
        self.assertEqual(metaphor["occurrences"], "2")
        self.assertEqual(metaphor["origin"], "ai")
        self.assertEqual(engine.job.read_translation(0), "Translation")
        report = json.loads((engine.job.directory / "analysis/report.json").read_text())
        self.assertTrue(report["complete"])
        self.assertFalse(list(engine.job.directory.rglob("resume_progress.json")))

    def test_existing_glossary_is_never_overwritten_and_deleted_entries_stay_deleted(self):
        engine = self.engine()
        QualityProfile(glossary=[{"source":"Mara", "target":"Μάρα", "notes":"Human choice"}]).save(engine.job.directory / "quality.json")
        engine.run()
        entry = engine.job.quality_profile().glossary[0]
        self.assertEqual(entry, {"source":"Mara", "target":"Μάρα", "notes":"Human choice"})
        engine.save_quality_profile(QualityProfile())
        engine.client.complete.reset_mock()
        engine.retranslate_single_chunk(0)
        self.assertFalse(engine.job.quality_profile().glossary)
        self.assertTrue(all(call.args[0] != ANALYSIS_PROMPT for call in engine.client.complete.call_args_list))

    def test_disable_analysis_and_fully_resumed_or_short_only_jobs_make_no_analysis_calls(self):
        engine = self.engine()
        engine.job.configure_translation(replace(self.settings, analyze_novel=False))
        engine.run()
        self.assertFalse((engine.job.directory / "analysis").exists())
        engine.job.configure_translation(self.settings)
        engine.client.complete.reset_mock()
        engine.run()
        engine.client.complete.assert_not_called()
        short = self.engine("Ja.\n...?!\nx\n")
        short.run()
        short.client.complete.assert_not_called()
        self.assertTrue(all(short.job.read_translation(c.index) == "" for c in short.job.chunks))

    def test_selected_retranslation_scans_whole_novel_and_writes_only_selected_chunk(self):
        engine = self.engine("Mara " + "visits the silver sea. " * 230 + "\nMara returns home.\n")
        for chunk in engine.job.chunks:
            engine.job.chunk_path(chunk.index).write_text("Old", encoding="utf-8")
        final = len(engine.job.chunks) - 1
        engine.retranslate_single_chunk(final)
        calls = engine.client.complete.call_args_list
        analyses = [call for call in calls if call.args[0] == ANALYSIS_PROMPT]
        self.assertEqual(len(analyses), len(source_batches(engine.job.chunks)))
        self.assertGreater(len(analyses), 1)
        self.assertTrue(all(call.args[0] == ANALYSIS_PROMPT for call in calls[:-1]))
        self.assertEqual(engine.job.read_translation(final), "Translation")
        self.assertTrue(all(engine.job.read_translation(i) == "Old" for i in range(final)))

    def test_cancelled_scan_reuses_passage_cache_without_partial_glossary_or_translation(self):
        engine = self.engine(("Mara waits by the silver sea. " * 180) + "\nMara returns home.")
        def cancel_after_first(event):
            if event["type"] == "analysis" and event.get("completed") == 1:
                engine.cancel()
        engine.emit = cancel_after_first
        engine.run()
        self.assertFalse((engine.job.directory / "quality.json").exists())
        self.assertFalse(list(engine.job.chunks_dir.glob("*.txt")))
        self.assertEqual(len(list((engine.job.directory / "analysis/passages").glob("*.json"))), 1)
        resumed = TranslationEngine(engine.job, client=engine.client)
        engine.client.complete.reset_mock()
        resumed.run()
        analyses = [c for c in engine.client.complete.call_args_list if c.args[0] == ANALYSIS_PROMPT]
        self.assertEqual(len(analyses), len(source_batches(engine.job.chunks)) - 1)
        self.assertEqual(resumed.job.quality_profile().glossary[0]["source"], "Mara")

    def test_unsupported_suggestion_is_excluded_without_blocking_translation(self):
        engine = self.engine()
        original = QualityProfile(glossary=[{"source":"sea", "target":"sea"}])
        original.save(engine.job.directory / "quality.json")
        engine.client.complete.side_effect = lambda system, user: json.dumps({"terms": [term("InventedName")]}) if system == ANALYSIS_PROMPT else "Translation"
        engine.run()
        self.assertEqual(engine.job.quality_profile(), original)
        self.assertTrue(all(engine.job.read_translation(c.index) == "Translation" for c in engine.job.chunks))
        report = json.loads((engine.job.directory / "analysis/report.json").read_text())
        self.assertEqual(report["excluded_count"], 1)
        self.assertEqual(report["outcome"], "completed_with_exclusions")
        self.assertFalse((engine.job.directory / ".running").exists())

    def test_reanalysis_preserves_manual_choices_and_uses_current_model(self):
        engine = self.engine()
        engine.run()
        profile = engine.job.quality_profile()
        engine.save_quality_profile(replace(profile, glossary=[dict(profile.glossary[0], target="My choice")]))
        engine.client.complete.reset_mock()
        engine.analyze_novel(force=True)
        self.assertEqual(engine.job.quality_profile().glossary[0]["target"], "My choice")
        self.assertTrue(any(c.args[0] == ANALYSIS_PROMPT for c in engine.client.complete.call_args_list))
        self.assertEqual(engine.job.read_translation(0), "Translation")

    def test_batches_are_bounded_and_include_short_character_names(self):
        engine = self.engine("Li\nMara " + "waits. " * 1400)
        passages = source_batches(engine.job.chunks)
        self.assertTrue(all(len(p) <= 4000 for p in passages))
        self.assertIn("Li\nMara", passages[0])

    def test_grounding_and_category_validation_reject_unsupported_suggestions(self):
        for value in (term("Unknown"), dict(term(), category="invalid")):
            with self.assertRaises(APIError):
                validate_terms({"terms":[value]}, "Mara returns.")
        recovered = validate_terms({"terms":[dict(term(), evidence="Another passage")]}, "Mara returns.")
        self.assertEqual(recovered[0]["evidence"], "Mara")
        self.assertEqual(validate_terms({"terms": []}, "Ordinary source."), [])
        self.assertEqual(QualityProfile(glossary=[term()]).guidance("Mara returns.")["glossary"][0]["category"], "character")
        self.assertNotIn("evidence", QualityProfile(glossary=[term()]).guidance("Mara returns.")["glossary"][0])

    def test_duplicate_json_fields_are_rejected_and_empty_term_list_is_valid(self):
        client = LMStudioClient(self.settings)
        client.complete = Mock(return_value='{"terms": [], "terms": []}')
        with self.assertRaises(APIError):
            analyze_passage(client, "Mara returns.", [])
        client.complete.return_value = '{"terms": []}'
        self.assertEqual(analyze_passage(client, "Ordinary source.", []), [])

    def test_analysis_and_translation_keep_epub_assets_and_original_structure(self):
        source = self.root / "novel.epub"
        originals = make_epub(source)
        job = Job(load_book(source), self.settings, self.root / "jobs")
        client = LMStudioClient(self.settings)
        def complete(system, user):
            if system == ANALYSIS_PROMPT:
                return json.dumps({"terms": [term("Hallo", "other")]})
            payload = json.loads(user)
            if "target_segments" in payload:
                return json.dumps({"translations": {k: v.upper() for k, v in payload["target_segments"].items()}})
            return payload["target_text"].upper()
        client.complete = Mock(side_effect=complete)
        TranslationEngine(job, client=client).run()
        output = self.root / "translated.epub"
        job.export(output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), set(originals))
            for name, data in originals.items():
                if not name.endswith(".xhtml"):
                    self.assertEqual(archive.read(name), data)
            chapter = archive.read("OPS/one.xhtml").decode()
            self.assertIn("HALLO", chapter)
            self.assertIn("<span class='x'>", chapter)
            self.assertIn('<img src="cover.png" alt="Unchanged"/>', chapter)
            self.assertIn('<svg xmlns="http://www.w3.org/2000/svg">', chapter)

    def test_explicit_analysis_skips_short_only_source_and_cancels_cleanly(self):
        engine = self.engine("Ja.\n...?!\n")
        self.assertTrue(engine.analyze_novel()["skipped"])
        engine.client.complete.assert_not_called()
        engine = self.engine("Mara waits.")
        def cancel(system, user):
            engine.cancel()
            return '{"terms": []}'
        engine.client.complete.side_effect = cancel
        self.assertTrue(engine.analyze_novel()["cancelled"])
        self.assertFalse((engine.job.directory / ".running").exists())


if __name__ == "__main__":
    unittest.main()
