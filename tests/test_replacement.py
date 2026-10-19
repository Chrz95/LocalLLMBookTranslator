"""Bulk replacement must be previewed, validated and recoverable."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from extractors import load_book
from settings_manager import Settings, atomic_write
from translator import Job, TranslationEngine


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        source=self.root/'book.txt'
        source.write_text('First paragraph\nSecond paragraph\nUnchanged paragraph\n', encoding='utf-8')
        self.job=Job(load_book(source), Settings(analyze_novel=False, model='test'), self.root/'jobs')
        for index, text in enumerate(('Cat cat caterpillar.', 'The cat rests.', 'Other text.')):
            self.job.chunk_path(index).write_text(text, encoding='utf-8')
            self.job.check_path(index).write_text('{"issues": []}', encoding='utf-8')
        self.events=[]
        self.engine=TranslationEngine(self.job, self.events.append, Mock())

    def test_preview_is_read_only_whole_word_default_and_case_optional(self):
        before={i:self.job.chunk_path(i).read_bytes() for i in range(3)}
        plan=self.job.preview_replacement('cat','dog')
        self.assertEqual([c.chunk_index for c in plan.replacements],[0,1])
        self.assertEqual(plan.replacements[0].after,'dog dog caterpillar.')
        self.assertEqual(len(plan.replacements[0].before_spans),2)
        self.assertEqual(before,{i:self.job.chunk_path(i).read_bytes() for i in range(3)})
        sensitive=self.job.preview_replacement('cat','dog',case_sensitive=True)
        self.assertEqual(sensitive.replacements[0].after,'Cat dog caterpillar.')
        partial=self.job.preview_replacement('cat','dog',whole_word=False)
        self.assertEqual(partial.replacements[0].after,'dog dog dogerpillar.')

    def test_apply_only_previewed_changes_keeps_backups_and_clears_checks(self):
        self.engine.set_bookmarks([0],'Review')
        plan=self.job.preview_replacement('cat','dog')
        backup=self.engine.apply_replacement(plan)
        self.assertEqual(self.job.read_translation(0),'dog dog caterpillar.')
        self.assertEqual(self.job.read_translation(1),'The dog rests.')
        self.assertEqual(self.job.read_translation(2),'Other text.')
        self.assertEqual((backup/'chunks'/'0.txt').read_text(),'Cat cat caterpillar.')
        self.assertTrue((backup/'checks'/'0.json').exists())
        self.assertFalse(self.job.check_path(0).exists())
        self.assertFalse(self.job.check_path(1).exists())
        self.assertTrue(self.job.check_path(2).exists())
        self.assertEqual(self.job.get_bookmarks(),{0:'Review'})

    def test_stale_preview_never_writes_and_empty_result_is_allowed(self):
        plan=self.job.preview_replacement('cat','dog')
        self.job.chunk_path(1).write_text('Changed after preview', encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'changed since preview'):
            self.engine.apply_replacement(plan)
        self.assertEqual(self.job.read_translation(0),'Cat cat caterpillar.')
        blank=self.job.preview_replacement('Other text.','')
        self.engine.apply_replacement(blank)
        self.assertEqual(self.job.read_translation(2),'')

    def test_failed_batch_rolls_back_translations_checks_and_emits_no_false_updates(self):
        plan=self.job.preview_replacement('cat','dog')
        before={i:self.job.chunk_path(i).read_bytes() for i in range(3)}
        failed=False
        def fail_once(path,data):
            nonlocal failed
            if Path(path)==self.job.chunk_path(1) and data=='The dog rests.' and not failed:
                failed=True
                raise OSError('Simulated disk error')
            return atomic_write(path,data)
        with patch('translator.atomic_write',side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError,'restored'):
                self.engine.apply_replacement(plan)
        self.assertEqual(before,{i:self.job.chunk_path(i).read_bytes() for i in range(3)})
        self.assertTrue(all(self.job.check_path(i).exists() for i in range(3)))
        self.assertEqual(self.events,[])

    def test_literal_replacement_unicode_and_corrupt_file_reporting(self):
        self.job.chunk_path(0).write_text('Καλημέρα [name] [name].', encoding='utf-8')
        plan=self.job.preview_replacement('[name]',r'\literal',whole_word=False)
        self.assertEqual(plan.replacements[0].after,'Καλημέρα \\literal \\literal.')
        self.job.chunk_path(1).write_bytes(b'\xff')
        bad=self.job.preview_replacement('[name]','Value',whole_word=False)
        self.assertEqual(len(bad.errors),1)
        with self.assertRaises(ValueError):
            self.engine.apply_replacement(bad)
        with self.assertRaises(ValueError):
            self.job.preview_replacement('','word')


if __name__ == '__main__':
    unittest.main()
