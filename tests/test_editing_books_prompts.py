"""Persistent edits, content-based recognition and custom translation prompts."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from extractors import load_book
from lmstudio import APIError, LMStudioClient, TRANSLATION_PROMPT, TRANSLATION_OUTPUT_RULE
from settings_manager import Settings, load_settings, save_settings
from translator import Job, TranslationEngine, saved_books


class EditingBookPromptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'original.txt'
        self.source.write_text('First paragraph.\nSecond paragraph.\n', encoding='utf-8')
        self.settings = Settings(model='test-model')
        self.workspace = self.root / 'data' / 'jobs'

    def job(self, settings=None):
        return Job(load_book(self.source), settings or self.settings, self.workspace)

    def test_manual_edit_persists_invalidates_check_resumes_and_exports(self):
        job = self.job()
        client = Mock()
        client.translate.side_effect = lambda value: value.upper()
        events = []
        engine = TranslationEngine(job, events.append, client)
        engine.run()
        second_before = job.chunk_path(1).read_bytes()
        job.check_path(0).write_text('{"issues": []}', encoding='utf-8')
        engine.save_chunk_edit(0, 'An edited translation — exactly as typed.')
        self.assertFalse(job.check_path(0).exists())
        self.assertEqual(job.chunk_path(1).read_bytes(), second_before)
        self.assertTrue(any(e.get('detail') == 'Edited manually' for e in events))
        reopened = self.job()
        self.assertTrue(reopened.recognized)
        engine2 = TranslationEngine(reopened, client=client)
        engine2.run()
        self.assertEqual(client.translate.call_count, 2)
        out = self.root / 'edited.txt'
        reopened.export(out)
        self.assertEqual(out.read_text(), 'An edited translation — exactly as typed.\nSECOND PARAGRAPH.\n')

    def test_model_empty_translation_is_saved_resumed_and_exported_without_checks(self):
        job = self.job(replace(self.settings, proofread=True))
        client = LMStudioClient(job.settings)
        client._json_request = Mock(return_value={
            'choices': [{'finish_reason': 'stop', 'message': {'content': ''}}]})
        client.check = Mock(return_value={'issues': []})
        events = []
        engine = TranslationEngine(job, events.append, client)
        engine.run()
        self.assertEqual(client._json_request.call_count, 2)
        self.assertEqual(job.chunk_path(0).read_bytes(), b'')
        self.assertEqual(job.chunk_path(1).read_bytes(), b'')
        self.assertTrue(any(e.get('detail') == 'Empty translation returned by model' for e in events))
        self.assertFalse(any(e.get('status') in ('Retry', 'Error') for e in events))
        client.check.assert_not_called()
        engine.run()
        self.assertEqual(client._json_request.call_count, 2)
        output = self.root / 'model-empty.txt'
        job.export(output)
        self.assertEqual(output.read_text(), '\n\n')
        client._json_request.return_value['choices'][0]['message']['content'] = 'Restored translation'
        engine.retranslate_single_chunk(0)
        self.assertEqual(job.read_translation(0), 'Restored translation')

    def test_completion_empty_vs_invalid_or_truncated_output(self):
        client = LMStudioClient(self.settings)
        client._json_request = Mock()
        for value in ('', ' \n\t '):
            client._json_request.return_value = {
                'choices': [{'finish_reason': 'stop', 'message': {'content': value}}]}
            self.assertEqual(client.translate('An unfinished sentence'), '')
            with self.assertRaises(APIError):
                client.check('Source', 'Translation')
        for reason, value in (('length', ''), ('length', 'Partial'), ('stop', None),
                              ('stop', [])):
            client._json_request.return_value = {
                'choices': [{'finish_reason': reason, 'message': {'content': value}}]}
            with self.assertRaises(APIError):
                client.translate('Source')

    def test_empty_edits_resume_export_and_can_be_retranslated(self):
        for value in ('', '   '):
            with self.subTest(value=value):
                job = self.job(replace(self.settings, proofread=True))
                client = Mock()
                client.translate.return_value = 'New translation'
                client.check.return_value = {'issues': []}
                engine = TranslationEngine(job, client=client)
                job.check_path(0).write_text('{"issues": []}', encoding='utf-8')
                engine.save_chunk_edit(0, value)
                engine.save_chunk_edit(1, value)
                self.assertEqual(job.chunk_path(0).read_text(), value)
                self.assertFalse(job.check_path(0).exists())
                reopened = self.job(replace(self.settings, proofread=True))
                events = []
                resumed = TranslationEngine(reopened, events.append, client)
                resumed.run()
                client.translate.assert_not_called()
                client.check.assert_not_called()
                self.assertEqual(reopened.read_translation(0), value)
                self.assertEqual(sum(e.get('status') == 'Resumed' for e in events), 2)
                output = self.root / 'blank.txt'
                reopened.export(output)
                self.assertEqual(output.read_text(), value + '\n' + value + '\n')
                resumed.retranslate_single_chunk(0)
                client.translate.assert_called_once_with('First paragraph.')
                self.assertEqual(reopened.read_translation(0), 'New translation')

    def test_invalid_edits_and_locked_job_do_not_overwrite(self):
        job = self.job()
        job.chunk_path(0).write_text('Keep this', encoding='utf-8')
        engine = TranslationEngine(job, client=Mock())
        for index, text in ((-1, 'text'), (99, 'text'), (0, None)):
            with self.assertRaises((IndexError, ValueError)):
                engine.save_chunk_edit(index, text)
        with engine.operation():
            with self.assertRaises(RuntimeError):
                engine.save_chunk_edit(0, 'Concurrent edit')
        self.assertEqual(job.read_translation(0), 'Keep this')

    def test_renamed_book_and_source_snapshot_share_saved_chunks(self):
        job = self.job()
        job.chunk_path(0).write_text('Saved first translation', encoding='utf-8')
        renamed = self.root / 'renamed.txt'
        self.source.rename(renamed)
        reopened = Job(load_book(renamed), self.settings, self.workspace)
        self.assertTrue(reopened.recognized)
        self.assertEqual(job.directory, reopened.directory)
        self.assertEqual(reopened.read_translation(0), 'Saved first translation')
        renamed.unlink()
        entries = saved_books(self.workspace)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].saved_chunks, 1)
        self.assertEqual(entries[0].title, 'original.txt')
        restored = Job(load_book(entries[0].source_path), entries[0].settings, self.workspace)
        self.assertEqual(restored.directory, job.directory)
        self.assertEqual(restored.read_translation(0), 'Saved first translation')
        catalog = json.loads((job.directory/'book.json').read_text())
        self.assertNotIn('api_key', catalog['settings'])
        self.assertNotIn('completed', catalog)
        self.assertFalse(any(p.name == 'resume_progress.json' for p in self.root.rglob('*')))

    def test_custom_prompt_replaces_system_prompt_and_has_language_placeholders(self):
        settings = replace(self.settings, input_lang='German', output_lang='Greek',
                           use_custom_prompt=True,
                           custom_prompt='Translate <input_lang> into <output_lang>. Preserve names.')
        client = LMStudioClient(settings)
        client.complete = Mock(return_value='Translated')
        self.assertEqual(client.translate('Hallo Welt'), 'Translated')
        client.complete.assert_called_once_with('Translate German into Greek. Preserve names.' + '\n\n' + TRANSLATION_OUTPUT_RULE, 'Hallo Welt')
        default = LMStudioClient(replace(settings, use_custom_prompt=False))
        default.complete = Mock(return_value='Default translation')
        default.translate('Hallo Welt')
        default.complete.assert_called_once_with(TRANSLATION_PROMPT.replace('<input_lang>', 'German').replace('<output_lang>', 'Greek') + '\n\n' + TRANSLATION_OUTPUT_RULE, 'Hallo Welt')

    def test_custom_prompt_keeps_existing_job_and_updates_only_forced_chunks(self):
        job = self.job()
        client = Mock()
        client.translate.return_value = 'Old translation'
        TranslationEngine(job, client=client).run()
        settings = replace(self.settings, use_custom_prompt=True, custom_prompt='My custom instructions')
        updated = self.job(settings)
        self.assertEqual(updated.directory, job.directory)
        custom = LMStudioClient(settings)
        custom.complete = Mock(return_value='Custom result')
        engine = TranslationEngine(updated, client=custom)
        engine.run()
        custom.complete.assert_not_called()
        engine.retranslate_chunks([1])
        custom.complete.assert_called_once_with('My custom instructions' + '\n\n' + TRANSLATION_OUTPUT_RULE, 'Second paragraph.')
        self.assertEqual(updated.read_translation(0), 'Old translation')
        self.assertEqual(updated.read_translation(1), 'Custom result')
        other_language = self.job(replace(settings, output_lang='Greek'))
        self.assertNotEqual(other_language.directory, job.directory)

    def test_prompt_settings_persist_and_empty_enabled_prompt_is_rejected(self):
        settings = replace(self.settings, custom_prompt='Line one.\nLine two.', use_custom_prompt=True)
        path = self.root/'settings.json'
        save_settings(path, settings)
        self.assertEqual(load_settings(path), settings)
        with self.assertRaises(ValueError):
            replace(settings, custom_prompt=' ').validate()


if __name__ == '__main__':
    unittest.main()
