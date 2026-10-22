"""Model-format regressions, bounded repair, diagnostics and unchanged book state."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from analysis_parser import parse_terms, validate_terms
from extractors import load_book
from lmstudio import APIError, LMStudioClient
from novel_analysis import ANALYSIS_PROMPT, analyze_passage
from quality import QualityProfile
from settings_manager import Settings
from translator import Job, TranslationEngine


class AnalysisParserTests(unittest.TestCase):
    def test_optional_null_notes_extra_metadata_and_verified_evidence_fallback(self):
        value = {"source":"Mara", "target":"Μάρα", "category":"Character names",
                 "notes":None, "evidence":None, "confidence":0.95, "aliases":["the keeper"]}
        entry = validate_terms({"terms":[value], "summary":"Metadata"}, "Mara enters.")[0]
        self.assertEqual(entry, {"source":"Mara", "target":"Μάρα", "category":"character",
                                 "notes":"", "evidence":"Mara"})

    def test_alias_fields_capitalization_and_list_notes_are_normalized(self):
        value = {"Source Term":"Mara", "Preferred Translation":"Μάρα", "Type":"characters",
                 "Treatment Notes":["Keep consistent", "Inflect where necessary"], "Source Quote":"Mara enters."}
        entry = validate_terms({"Glossary":[value]}, "Mara enters.")[0]
        self.assertEqual(entry["category"], "character")
        self.assertEqual(entry["notes"], "Keep consistent; Inflect where necessary")
        self.assertEqual(set(entry), {"source","target","category","notes","evidence"})

    def test_missing_optional_fields_and_fenced_array_response(self):
        raw = '```JSON\n[{"term":"Mara","translation":"Mara"}]\n```'
        entry = parse_terms(raw, "Mara enters.")[0]
        self.assertEqual(entry["category"], "other")
        self.assertEqual(entry["notes"], "")
        self.assertEqual(entry["evidence"], "Mara")
        self.assertEqual(parse_terms('```\n{"terms": []}\n```', "Source"), [])

    def test_category_labels_and_common_plural_names(self):
        cases = {"Titles & honorifics":"title", "place names":"geography",
                 "magical-abilities":"magical_ability", "organisations":"faction",
                 "Invented languages":"invented_language", "motifs":"recurring_metaphor"}
        for category, expected in cases.items():
            with self.subTest(category=category):
                entry = validate_terms({"terms":[{"source":"Mara","target":"Mara", "category":category}]}, "Mara enters.")[0]
                self.assertEqual(entry["category"], expected)

    def test_unverified_terms_missing_targets_and_bad_types_still_fail(self):
        values = [{"source":"InventedName", "target":"Name"}, {"source":"Mara"},
                  {"source":"Mara","target":["Name1","Name2"]},
                  {"source":"Mara","target":"Mara","notes":{"text":"Keep"}},
                  {"source":"Mara","target":"Mara","category":"not-a-category"}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(APIError):
                validate_terms({"terms":[value]}, "Mara enters.")

    def test_bad_evidence_is_replaced_with_literal_source_without_invented_facts(self):
        for evidence in ("Mara is a queen.", "Mara…", "Someone enters.", ["Mara enters."],
                         {"quote":"Mara enters."}, "x" * 1000, 42, "", None):
            with self.subTest(evidence=evidence):
                value = {"source":"Mara", "target":"Μάρα", "evidence":evidence}
                entry = validate_terms({"terms":[value]}, "Mara enters.")[0]
                self.assertEqual(entry["evidence"], "Mara")
                self.assertEqual(entry["target"], "Μάρα")

    def test_recovered_evidence_preserves_actual_unicode_spelling_and_spacing(self):
        for passage, source, expected in (("MARA enters.", "Mara", "MARA"),
                ("Die Straße ist still.", "STRASSE", "Straße"),
                ("Cafe\u0301 opens.", "Café", "Cafe\u0301"),
                ("Ashen\n  Reach lies north.", "Ashen Reach", "Ashen\n  Reach"),
                ("Μα\u0301ρα φεύγει.", "Μάρα", "Μα\u0301ρα")):
            with self.subTest(passage=passage):
                entry = validate_terms({"terms":[{"source":source,"target":source,"evidence":"Paraphrased"}]}, passage)[0]
                self.assertEqual(entry["evidence"], expected)
                self.assertIn(entry["evidence"], passage)

    def test_valid_evidence_is_kept_and_conflicting_model_quotes_are_recovered(self):
        value = {"source":"Mara", "target":"Mara", "evidence":"Mara enters."}
        self.assertEqual(validate_terms({"terms":[value]}, "Mara enters.")[0]["evidence"], "Mara enters.")
        value["quote"] = "Mara is a queen."
        self.assertEqual(validate_terms({"terms":[value]}, "Mara enters.")[0]["evidence"], "Mara")

    def test_recovered_evidence_does_not_make_unsupported_source_terms_acceptable(self):
        with self.assertRaisesRegex(APIError, "Source term does not occur"):
            validate_terms({"terms":[{"source":"InventedName","target":"Name","evidence":"Mara enters."}]}, "Mara enters.")

    def test_bad_quotation_does_not_require_another_model_request(self):
        client = LMStudioClient(Settings(analyze_novel=True, model="test", retries=0))
        client.complete = Mock(return_value='{"terms":[{"source":"Mara","target":"Mara","evidence":"Mara is a queen."}]}')
        callback = Mock()
        entry = analyze_passage(client, "Mara enters.", [], callback)[0]
        self.assertEqual(entry["evidence"], "Mara")
        client.complete.assert_called_once()
        callback.assert_not_called()

    def test_pipeline_recovers_quote_commits_glossary_and_reuses_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/"book.txt"
            source.write_text("Mara enters.\n", encoding="utf-8")
            settings = Settings(analyze_novel=True, model="test", retries=0)
            job = Job(load_book(source), settings, root/"jobs")
            client = LMStudioClient(settings)
            client.complete = Mock(side_effect=[
                '{"terms":[{"source":"Mara","target":"Μάρα","evidence":"Mara is a queen."}]}',
                "Translated."])
            engine = TranslationEngine(job, client=client)
            engine.run()
            self.assertEqual(job.read_translation(0), "Translated.")
            self.assertEqual(job.quality_profile().glossary[0]["evidence"], "Mara")
            self.assertEqual(job.quality_profile().glossary[0]["target"], "Μάρα")
            self.assertEqual(client.complete.call_count, 2)
            self.assertFalse((job.directory/"analysis/errors").exists())
            cache = next((job.directory/"analysis/passages").glob("*.json"))
            self.assertEqual(json.loads(cache.read_text())["terms"][0]["evidence"], "Mara")
            client.complete = Mock(return_value="New translation.")
            engine.retranslate_single_chunk(0)
            client.complete.assert_called_once()
            self.assertEqual(job.read_translation(0), "New translation.")

    def test_conflicting_aliases_duplicate_keys_and_overlong_notes_fail_clearly(self):
        for raw in ('{"terms":[],"terms":[]}', '{"terms":[],"Terms":[]}',
                    '{"terms":[{"source":"Mara","term":"Other","target":"Mara"}]}'):
            with self.assertRaises(APIError):
                parse_terms(raw, "Mara enters.")
        with self.assertRaisesRegex(APIError, "entry 1.*notes.*2000"):
            validate_terms({"terms":[{"source":"Mara","target":"Mara","notes":"x"*2001}]}, "Mara enters.")

    def test_invalid_entry_identifies_position_and_field_without_dropping_valid_entries(self):
        with self.assertRaisesRegex(APIError, "entry 2.*target.*int"):
            validate_terms({"terms":[{"source":"Mara","target":"Mara"},
                                      {"source":"enters","target":42}]}, "Mara enters.")

    def test_repair_request_contains_source_and_error_then_validates_correction(self):
        client = LMStudioClient(Settings(analyze_novel=True, model="test", retries=0))
        client.complete = Mock(side_effect=['{"terms":[{"source":"Mara","target":42}]}',
                                           '{"terms":[{"source":"Mara","target":"Mara"}]}'])
        callback = Mock()
        result = analyze_passage(client, "Mara enters.", [], callback)
        self.assertEqual(result[0]["source"], "Mara")
        self.assertEqual(client.complete.call_count, 2)
        repair_prompt, repair_payload = client.complete.call_args.args
        self.assertTrue(repair_prompt.startswith(ANALYSIS_PROMPT))
        payload = json.loads(repair_payload)
        self.assertEqual(payload["passage"], "Mara enters.")
        self.assertIn("target", payload["validation_error"])
        self.assertIn('"target":42', payload["invalid_response"])
        callback.assert_called_once()

    def test_pipeline_saves_diagnostic_repairs_then_translates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/"book.txt"
            source.write_text("Mara enters.\n", encoding="utf-8")
            settings = Settings(analyze_novel=True, model="test", retries=2, api_key="not-persisted")
            job = Job(load_book(source), settings, root/"jobs")
            client = LMStudioClient(settings)
            invalid = '{"terms":['
            client.complete = Mock(side_effect=[invalid, '{"terms":[{"source":"Mara","target":"Mara"}]}', "Translated."])
            events = []
            TranslationEngine(job, events.append, client).run()
            self.assertEqual(job.read_translation(0), "Translated.")
            self.assertEqual(client.complete.call_count, 3)
            files = list((job.directory/"analysis/errors").glob("*.json"))
            self.assertEqual(len(files), 1)
            saved = json.loads(files[0].read_text())
            self.assertEqual(saved["response"], invalid)
            self.assertEqual(saved["passage_number"], 1)
            self.assertNotIn("not-persisted", files[0].read_text())
            self.assertTrue(any("Correcting" in e.get("detail", "") for e in events))
            report = json.loads((job.directory/"analysis/report.json").read_text())
            self.assertTrue(report["complete"])

    def test_failed_repair_is_bounded_and_preserves_existing_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/"book.txt"
            source.write_text("Mara enters.\n", encoding="utf-8")
            settings = Settings(analyze_novel=True, model="test", retries=2)
            job = Job(load_book(source), settings, root/"jobs")
            profile = QualityProfile(glossary=[{"source":"Mara","target":"Human choice"}])
            profile.save(job.directory/"quality.json")
            job.chunk_path(0).write_text("My saved edit", encoding="utf-8")
            client = LMStudioClient(settings)
            client.complete = Mock(return_value='{"terms":"not a list"}')
            with self.assertRaisesRegex(APIError, "terms list.*Automatic correction also failed") as caught:
                TranslationEngine(job, client=client).retranslate_single_chunk(0)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(client.complete.call_count, 2)
            self.assertEqual(job.read_translation(0), "My saved edit")
            self.assertEqual(job.quality_profile(), profile)
            self.assertEqual(len(list((job.directory/"analysis/errors").glob("*.json"))), 2)
            self.assertFalse((job.directory/".running").exists())


if __name__ == "__main__":
    unittest.main()
