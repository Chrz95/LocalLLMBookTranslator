"""Selected retranslation must never modify unrelated cached chunks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from extractors import load_book
from settings_manager import Settings
from translator import Job, TranslationEngine, parse_chunk_selection


class SelectionTests(unittest.TestCase):
    def test_ranges_and_validation(self):
        self.assertEqual(parse_chunk_selection('3, 1–3, 0, 3', 5), [0, 1, 2, 3])
        for text in ('', '1,', '-1', '3-1', '0-5', 'abc', '1.5', '1 2'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_chunk_selection(text, 5)

    def test_only_selected_files_are_overwritten_and_duplicates_run_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'book.txt'
            source.write_text('First\nSecond\nThird\nFourth\n', encoding='utf-8')
            job = Job(load_book(source), Settings(analyze_novel=False, model='test'), root / 'jobs')
            for index in range(4):
                job.chunk_path(index).write_text(f'old-{index}', encoding='utf-8')
            client = Mock()
            client.translate.side_effect = lambda text: 'new-' + text
            events = []
            engine = TranslationEngine(job, events.append, client)
            engine.retranslate_chunks([3, 1, 1])
            self.assertEqual(client.translate.call_count, 2)
            self.assertEqual([job.read_translation(i) for i in range(4)],
                             ['old-0', 'new-Second', 'old-2', 'new-Fourth'])
            self.assertEqual({e['index'] for e in events if e.get('status') == 'Retranslated'}, {1, 3})
            self.assertEqual(sum(e['type'] == 'finished' for e in events), 1)
            for indices in ([], [0, 9], [False], ['1']):
                with self.assertRaises((ValueError, IndexError)):
                    engine.retranslate_chunks(indices)
            self.assertEqual(client.translate.call_count, 2)


if __name__ == '__main__':
    unittest.main()
