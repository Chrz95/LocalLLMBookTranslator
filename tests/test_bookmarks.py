"""Chunk bookmarks persist independently of translation and checking state."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from extractors import load_book
from settings_manager import Settings
from translator import Job, TranslationEngine


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'book.txt'
        self.source.write_text('First paragraph\nSecond paragraph\nThird paragraph\n', encoding='utf-8')
        self.settings = Settings(model='test')
        self.job = Job(load_book(self.source), self.settings, self.root/'jobs')
        self.client = Mock()
        self.client.translate.return_value = 'Translated text'
        self.engine = TranslationEngine(self.job, client=self.client)

    def test_bookmarks_labels_reopen_and_remove(self):
        self.assertEqual(self.job.get_bookmarks(), {})
        self.engine.set_bookmarks([0, 2, 2], 'Review this')
        reopened = Job(load_book(self.source), self.settings, self.root/'jobs')
        self.assertEqual(reopened.get_bookmarks(), {0: 'Review this', 2: 'Review this'})
        self.engine.set_bookmarks([2], 'Revised label')
        self.assertEqual(self.job.get_bookmarks()[2], 'Revised label')
        self.engine.set_bookmarks([0], remove=True)
        self.assertEqual(self.job.get_bookmarks(), {2: 'Revised label'})
        self.engine.set_bookmarks([2], remove=True)
        self.assertEqual(self.job.get_bookmarks(), {})

    def test_bookmarks_do_not_skip_translation_and_survive_edits_retranslation(self):
        self.engine.set_bookmarks([1])
        self.assertFalse(self.job.chunk_path(1).exists())
        self.engine.run()
        self.assertEqual(self.client.translate.call_count, 3)
        self.engine.save_chunk_edit(1, 'Edited text')
        self.engine.retranslate_chunks([1])
        self.assertEqual(self.client.translate.call_count, 4)
        self.assertEqual(self.job.get_bookmarks(), {1: ''})
        self.assertFalse(any(p.name == 'resume_progress.json' for p in self.root.rglob('*')))

    def test_invalid_selection_and_corrupt_file_are_not_overwritten(self):
        self.engine.set_bookmarks([1], 'Keep')
        for indices in ([], [-1], [3], [True]):
            with self.assertRaises(ValueError):
                self.engine.set_bookmarks(indices)
        self.assertEqual(self.job.get_bookmarks(), {1: 'Keep'})
        path=self.job.directory/'bookmarks.json'
        path.write_text('broken json', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.engine.set_bookmarks([0])
        self.assertEqual(path.read_text(), 'broken json')


if __name__ == '__main__':
    unittest.main()
