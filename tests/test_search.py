"""Search must cover uncached source chunks and the latest saved translations."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from extractors import load_book
from settings_manager import Settings
from translator import Job, TranslationEngine


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        source = root/'book.txt'
        source.write_text('Alpha alpha [literal].\nA Greek phrase: Καλημέρα κόσμε.\nNo cached translation here.\n', encoding='utf-8')
        self.job = Job(load_book(source), Settings(analyze_novel=False, model='test'), root/'jobs')
        self.job.chunk_path(0).write_text('Alpha in translation.', encoding='utf-8')
        self.job.chunk_path(1).write_text('Good morning world.', encoding='utf-8')

    def test_returns_chunk_indices_fields_counts_and_exact_spans(self):
        found = self.job.search('alpha')
        self.assertEqual([(m.chunk_index, m.field, len(m.spans)) for m in found.matches],
                         [(0, 'Source', 2), (0, 'Translation', 1)])
        self.assertEqual(found.matches[0].spans, ((0, 5), (6, 11)))
        self.assertIn('Alpha alpha', found.matches[0].snippet)
        self.assertEqual(found.errors, ())

    def test_scope_case_unicode_and_literal_punctuation(self):
        self.assertEqual(len(self.job.search('Alpha', 'Source', True).matches[0].spans), 1)
        self.assertEqual(self.job.search('alpha', 'Translation', True).matches, ())
        self.assertEqual(self.job.search('[literal]', 'Source').matches[0].chunk_index, 0)
        self.assertEqual(self.job.search('καλημέρα').matches[0].chunk_index, 1)
        self.assertEqual(self.job.search('cached', 'Source').matches[0].chunk_index, 2)
        self.assertEqual(self.job.search('missing phrase').matches, ())

    def test_search_reads_latest_manual_edits_and_reports_bad_files(self):
        TranslationEngine(self.job, client=Mock()).save_chunk_edit(1, 'A revised searchable translation.')
        found = self.job.search('searchable', 'Translation')
        self.assertEqual(found.matches[0].chunk_index, 1)
        self.job.chunk_path(0).write_bytes(b'\xff')
        result = self.job.search('alpha')
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.matches[0].field, 'Source')
        self.assertEqual(len(result.errors), 1)
        self.assertIn('Chunk 0', result.errors[0])

    def test_empty_queries_and_invalid_scopes_are_rejected(self):
        for query, scope in (('', 'Both'), ('  ', 'Source'), ('text', 'Unknown')):
            with self.assertRaises(ValueError):
                self.job.search(query, scope)


if __name__ == '__main__':
    unittest.main()
