"""Context mapping, approved guidance, proposals and stale-result protection."""
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET
import zipfile

from extractors import Book, Unit, load_book
from lmstudio import LMStudioClient
from quality import QualityProfile, build_context
from settings_manager import Settings
from translator import Job, TranslationEngine
from test_application import make_epub


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'book.txt'
        self.source.write_text('Mara opens the door.\nShe hears a distant voice.\nThe lighthouse is quiet.\n', encoding='utf-8')
        self.settings = Settings(analyze_novel=False, model='model-one', retries=0)
        self.job = Job(load_book(self.source), self.settings, self.root/'jobs')
        self.client = LMStudioClient(self.settings)
        self.client.complete = Mock(return_value='Translation')
        self.events = []
        self.engine = TranslationEngine(self.job, self.events.append, self.client)

    def test_context_is_source_only_and_bounded(self):
        self.engine.run()
        prompt, user = next(call.args for call in self.client.complete.call_args_list
                            if json.loads(call.args[1])["target_text"] == 'The lighthouse is quiet.')
        payload = json.loads(user)
        self.assertEqual(payload['target_text'], 'The lighthouse is quiet.')
        self.assertIn('target_text', prompt)
        self.assertNotIn('Translation', json.dumps(payload))
        chunks = [replace(c, text=c.text*300) for c in self.job.chunks]
        context = build_context(self.job.book, chunks, 1, QualityProfile(context_chars=1000))
        self.assertLessEqual(sum(len(item['text']) for item in context['source_segments']), 1000)
        self.assertTrue(context['context_truncated'])
        self.assertEqual(build_context(self.job.book, chunks, 1, QualityProfile(use_context=False)), {})

    def test_guidance_persists_and_retrieves_relevant_terms_and_examples(self):
        profile = QualityProfile(glossary=[{'source':'Mara', 'target':'Μάρα', 'notes':'Inflect as needed'},
                                          {'source':'harbor', 'target':'λιμάνι'}],
                                 character_notes='Mara: she/her; lighthouse keeper.', style_guide='Quiet, restrained narration.',
                                 examples=[{'source':'Mara opens the door.', 'translation':'Η Μάρα ανοίγει την πόρτα.'},
                                           {'source':'Apples are ripe.', 'translation':'Τα μήλα είναι ώριμα.'}])
        self.engine.save_quality_profile(profile)
        reopened = Job(load_book(self.source), self.settings, self.root/'jobs')
        self.assertEqual(reopened.directory, self.job.directory)
        self.assertEqual(reopened.quality_profile(), profile)
        guidance = self.engine.reference(0)['book_guidance']
        self.assertEqual(len(guidance['glossary']), 1)
        self.assertEqual(len(guidance['approved_examples']), 1)
        self.assertEqual(guidance['style_guide'], profile.style_guide)
        self.assertFalse(any(p.name=='resume_progress.json' for p in self.root.rglob('*')))

    def test_invalid_profiles_rejected_and_glossary_budget_enforced(self):
        for profile in (QualityProfile(neighboring_chunks=6), QualityProfile(context_chars=999),
                        QualityProfile(glossary=[{'source':'Mara','target':''}]),
                        QualityProfile(glossary=[{'source':'Mara','target':'M'}, {'source':'mara','target':'N'}]),
                        QualityProfile(examples=[{'source':'Text','translation':None}])):
            with self.assertRaises(ValueError):
                self.engine.save_quality_profile(profile)
        profile = QualityProfile(glossary=[{'source':f'Term{i}','target':'x'*1000} for i in range(40)])
        guidance = profile.guidance(' '.join(f'Term{i}' for i in range(40)))
        self.assertLessEqual(sum(len(json.dumps(e, ensure_ascii=False)) for e in guidance['glossary']), 6000)

    def test_approve_correction_guides_future_requests_without_automatic_reuse(self):
        self.engine.save_chunk_edit(0, 'An approved translation')
        self.engine.approve_example(0)
        self.engine.save_chunk_edit(0, 'A better approved translation')
        self.engine.approve_example(0)
        self.assertEqual(len(self.job.quality_profile().examples), 1)
        self.assertEqual(self.engine.reference(0)['book_guidance']['approved_examples'][0]['translation'], 'A better approved translation')
        self.engine.retranslate_single_chunk(0)
        self.client.complete.assert_called_once()
        self.assertEqual(self.job.read_translation(0), 'Translation')
        self.engine.save_chunk_edit(0, '')
        with self.assertRaises(ValueError):
            self.engine.approve_example(0)

    def test_revision_is_proposal_until_accepted_and_history_retained(self):
        self.engine.save_chunk_edit(0, 'Original draft')
        with patch.object(LMStudioClient, 'complete', return_value='Improved translation'):
            proposal = self.engine.propose_revision(0, 'revision-model')
        self.assertEqual(self.job.read_translation(0), 'Original draft')
        self.assertEqual(proposal['model'], 'revision-model')
        self.assertEqual(len(list((self.job.directory/'proposals').glob('*.json'))), 1)
        self.engine.apply_proposal(proposal)
        self.assertEqual(self.job.read_translation(0), 'Improved translation')
        history = list((self.job.directory/'history'/'0').glob('*.txt'))
        self.assertEqual([p.read_text() for p in history], ['Original draft'])

    def test_stale_or_wrong_book_proposal_cannot_overwrite(self):
        self.engine.save_chunk_edit(0, 'Draft')
        with patch.object(LMStudioClient, 'complete', return_value='Revision'):
            proposal = self.engine.propose_revision(0)
        self.engine.save_chunk_edit(0, 'Concurrent edit')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.engine.apply_proposal(proposal)
        wrong = dict(proposal, job_directory=str(self.root/'wrong'))
        with self.assertRaisesRegex(ValueError, 'another book'):
            self.engine.apply_proposal(wrong)
        self.assertEqual(self.job.read_translation(0), 'Concurrent edit')

    def test_comparison_uses_same_context_and_does_not_save_candidates(self):
        with patch.object(LMStudioClient, 'complete', side_effect=['First candidate','Second candidate']) as complete:
            candidates = self.engine.compare_models(1, ['one', 'two'])
        self.assertFalse(self.job.chunk_path(1).exists())
        self.assertEqual({p['model'] for p in candidates}, {'one','two'})
        self.assertEqual(complete.call_args_list[0].args[1], complete.call_args_list[1].args[1])
        self.engine.apply_proposal(candidates[0])
        self.assertEqual(self.job.read_translation(1), candidates[0]['after'])

    def test_explicit_check_and_guidance_changes_invalidate_check_cache(self):
        self.engine.save_chunk_edit(0, 'Translation')
        self.client.complete.return_value = '{"issues": []}'
        self.engine.check_chunks([0])
        self.assertEqual(self.client.complete.call_count, 1)
        self.engine._check(self.job.chunks[0], 'Translation')
        self.assertEqual(self.client.complete.call_count, 1)
        self.engine.save_quality_profile(QualityProfile(style_guide='Preserve formal address.'))
        self.engine._check(self.job.chunks[0], 'Translation')
        self.assertEqual(self.client.complete.call_count, 2)
        prompt, payload = self.client.complete.call_args.args
        self.assertIn('subject/object', prompt)
        self.assertIn('reference', json.loads(payload))
        self.engine.save_chunk_edit(0, '')
        self.engine.check_chunks([0])
        self.assertEqual(self.client.complete.call_count, 2)

    def test_empty_review_and_revision_keep_original_skip_rules(self):
        self.engine.save_chunk_edit(1, '')
        self.assertEqual(self.job.empty_translations(), [(1, 'Saved empty translation (model or manual edit)')])
        self.engine.save_chunk_edit(0, 'Draft')
        with patch.object(LMStudioClient, 'complete', return_value=''):
            proposal = self.engine.propose_revision(0)
        self.assertEqual(self.job.read_translation(0), 'Draft')
        self.engine.apply_proposal(proposal)
        self.assertEqual(self.job.read_translation(0), '')


class ParagraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'book.epub'
        self.members = make_epub(self.source)
        self.job = Job(load_book(self.source), Settings(analyze_novel=False, model='test', retries=0), self.root/'jobs')
        self.client = LMStudioClient(self.job.settings)
        self.client.complete = Mock(side_effect=self.reply)
        self.events = []
        self.engine = TranslationEngine(self.job, self.events.append, self.client)

    @staticmethod
    def reply(prompt, user):
        data = json.loads(user)
        if 'target_segments' in data:
            return json.dumps({'translations':{i:'Translated '+text for i,text in data['target_segments'].items()}})
        return 'Translated '+data['target_text']

    def paragraph_indices(self):
        return next([c.index for c in group] for group in self.job.paragraphs.values() if len(group)>2)

    def test_full_paragraph_mapping_preserves_epub_structure_and_assets(self):
        indices = self.paragraph_indices()
        self.engine.run()
        group_requests = [json.loads(c.args[1]) for c in self.client.complete.call_args_list
                          if 'target_segments' in json.loads(c.args[1])]
        self.assertTrue(any(set(data['target_segments']) == {str(i) for i in indices} for data in group_requests))
        output = self.root/'translated.epub'
        self.job.export(output)
        with zipfile.ZipFile(output) as archive:
            original = ET.fromstring(self.members['OPS/one.xhtml'])
            result = ET.fromstring(archive.read('OPS/one.xhtml'))
            self.assertEqual([(e.tag,e.attrib) for e in original.iter()], [(e.tag,e.attrib) for e in result.iter()])
            for member in ('OPS/book.opf','OPS/style.css','OPS/font.otf','OPS/cover.png','OPS/toc.ncx'):
                self.assertEqual(archive.read(member), self.members[member])
        context = self.engine.reference(indices[1])
        for segment in context['source_segments']:
            unit = self.job.book.units[self.job.chunks[segment['chunk_id']].unit_index]
            self.assertEqual(unit.member, self.job.book.units[self.job.chunks[indices[1]].unit_index].member)

    def test_resume_and_selected_retranslation_do_not_touch_other_nodes(self):
        self.engine.run()
        indices = self.paragraph_indices()
        before = {c.index:self.job.chunk_path(c.index).read_bytes() for c in self.job.chunks}
        self.client.complete.reset_mock()
        self.job.chunk_path(indices[0]).unlink()
        self.engine.run()
        self.assertEqual(self.client.complete.call_count, 1)
        self.client.complete.reset_mock()
        self.engine.retranslate_chunks(indices[:2])
        self.assertEqual(self.client.complete.call_count, 1)
        request = json.loads(self.client.complete.call_args.args[1])
        self.assertEqual(set(request['target_segments']), {str(i) for i in indices[:2]})
        for index in set(before)-set(indices[:2]):
            self.assertEqual(self.job.chunk_path(index).read_bytes(), before[index])

    def test_invalid_mapping_keeps_all_existing_target_files(self):
        self.engine.run()
        indices = self.paragraph_indices()[:2]
        before = {i:self.job.chunk_path(i).read_bytes() for i in indices}
        self.client.complete.side_effect = None
        for raw in ('{"translations": {"999":"wrong"}}',
                    '{"translations": {"1":"a", "1":"b"}}', 'Explanation instead of translation'):
            self.client.complete.return_value = raw
            self.engine.retranslate_chunks(indices)
            self.assertEqual(before, {i:self.job.chunk_path(i).read_bytes() for i in indices})
        self.assertTrue(any(e.get('status')=='Error' for e in self.events))

    def test_group_target_limit_is_four_thousand_characters(self):
        path = self.root/'large.epub'
        original = b'original fixture'
        path.write_bytes(original)
        book = Book(path,'epub',hashlib.sha256(original).hexdigest(),
                    [Unit('word '*700,'one','chapter',paragraph=1), Unit('word '*700,'two','chapter',paragraph=1)], original=original)
        job = Job(book, self.job.settings, self.root/'large-jobs')
        engine = TranslationEngine(job, client=LMStudioClient(job.settings))
        for group in engine._work_items(job.chunks, False):
            self.assertLessEqual(sum(len(c.text) for c in group), 4000)


if __name__=='__main__':
    unittest.main()
