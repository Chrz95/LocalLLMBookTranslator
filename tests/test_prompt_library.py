"""Named prompts must persist independently of any book or running model."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lmstudio import TRANSLATION_PROMPT
from settings_manager import PromptStore


class PromptLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)/'prompts.json'
        self.store = PromptStore(self.path, TRANSLATION_PROMPT)

    def reopen(self):
        return PromptStore(self.path, TRANSLATION_PROMPT)

    def test_create_edit_rename_select_and_delete_survive_restart(self):
        first = self.store.create('Literary Greek', 'Translate <input_lang> into Greek.\nPreserve tone.')
        second = self.store.create('Dialogue', 'Preserve informal dialogue.')
        loaded = self.reopen()
        self.assertEqual(loaded.selected_id, second.identifier)
        loaded.select(first.identifier)
        updated = loaded.update(first.identifier, 'Greek revised', 'Keep names and emotional intensity.')
        loaded = self.reopen()
        self.assertEqual(loaded.selected_id, first.identifier)
        self.assertEqual(loaded.get(first.identifier), updated)
        loaded.delete(second.identifier)
        self.assertEqual(loaded.selected_id, first.identifier)
        loaded.delete(first.identifier)
        loaded = self.reopen()
        self.assertEqual(loaded.selected_id, PromptStore.DEFAULT_ID)
        self.assertEqual(loaded.all(), (loaded.default,))
        self.assertEqual(loaded.default.text, TRANSLATION_PROMPT)

    def test_default_is_always_available_and_cannot_be_edited_or_deleted(self):
        self.assertEqual(self.store.get(PromptStore.DEFAULT_ID).text, TRANSLATION_PROMPT)
        with self.assertRaises(ValueError):
            self.store.update(PromptStore.DEFAULT_ID, 'Changed', 'Changed instructions')
        with self.assertRaises(ValueError):
            self.store.delete(PromptStore.DEFAULT_ID)
        self.store.select(PromptStore.DEFAULT_ID)
        self.assertEqual(self.reopen().all()[0].text, TRANSLATION_PROMPT)

    def test_duplicate_names_empty_text_and_missing_ids_are_rejected(self):
        self.store.create('My prompt', 'Valid instructions')
        for name, text in (('MY PROMPT', 'Another'), (' ', 'Another'), ('Name', ' '),
                           (PromptStore.DEFAULT_NAME, 'Replacement'), ('x'*101, 'Text')):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.create(name, text)
        for action in (self.store.get, self.store.select, self.store.delete):
            with self.assertRaises(ValueError):
                action('missing')
        self.assertEqual(len(self.reopen().all()), 2)

    def test_write_failure_preserves_memory_and_disk(self):
        prompt = self.store.create('Keep', 'Original text')
        before = self.path.read_bytes()
        with patch('settings_manager.atomic_write', side_effect=OSError('Disk unavailable')):
            with self.assertRaises(OSError):
                self.store.update(prompt.identifier, 'New name', 'New text')
            with self.assertRaises(OSError):
                self.store.delete(prompt.identifier)
        self.assertEqual(self.store.get(prompt.identifier), prompt)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_library_is_not_overwritten_and_default_fallback_is_read_only(self):
        self.path.write_text('broken json', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.reopen()
        fallback = PromptStore(self.path, TRANSLATION_PROMPT, read_only=True)
        self.assertEqual(fallback.default.text, TRANSLATION_PROMPT)
        with self.assertRaises(ValueError):
            fallback.create('New', 'Text')
        self.assertEqual(self.path.read_text(), 'broken json')


if __name__ == '__main__':
    unittest.main()
