"""Launch preference and runtime model changes must preserve existing work."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from extractors import load_book
from lmstudio import APIError, LMStudioClient, launch_lm_studio, lmstudio_launch_command
from settings_manager import Settings, load_settings, save_settings
from translator import Job, TranslationEngine, saved_books


class LaunchAndModelSwitchTests(unittest.TestCase):
    def test_default_launch_preference_and_disable_persist_without_model(self):
        self.assertTrue(Settings(analyze_novel=False, ).start_lm_studio)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            settings=Settings(analyze_novel=False, start_lm_studio=False, lmstudio_path='/custom/path')
            save_settings(path, settings, require_model=False)
            self.assertEqual(load_settings(path),settings)

    def test_running_server_does_not_launch_duplicate_process(self):
        with patch.object(LMStudioClient,'_json_request',return_value={'data':[]}), patch('subprocess.Popen') as spawn:
            self.assertIn('already running',launch_lm_studio())
            spawn.assert_not_called()
        with patch.object(LMStudioClient,'_json_request',side_effect=APIError('Authentication',False,401)), patch('subprocess.Popen') as spawn:
            self.assertIn('API key',launch_lm_studio())
            spawn.assert_not_called()

    def test_launch_command_preserves_path_spaces_and_starts_loopback_api(self):
        with tempfile.TemporaryDirectory() as folder:
            executable=Path(folder)/'LM Studio executable'
            executable.write_text('test fixture',encoding='utf-8')
            with patch('platform.system',return_value='Linux'):
                self.assertEqual(lmstudio_launch_command(str(executable)),[str(executable)])
            with self.assertRaises(ValueError):
                lmstudio_launch_command(str(executable)+'missing')
        with patch.object(LMStudioClient,'_json_request',side_effect=APIError('Offline')), \
             patch('lmstudio.lmstudio_launch_command',return_value=['/some path/LM Studio']), \
             patch('shutil.which',return_value='/some path/lms'), \
             patch('subprocess.Popen') as spawn, \
             patch('subprocess.run',return_value=Mock(returncode=0)) as run:
            self.assertIn('server started',launch_lm_studio())
            self.assertEqual(spawn.call_args.args[0],['/some path/LM Studio'])
            self.assertEqual(run.call_args.args[0],['/some path/lms','server','start','--port','1234','--bind','127.0.0.1'])
            self.assertNotIn('shell',spawn.call_args.kwargs)

    def test_new_model_retranslates_selected_chunks_without_forking_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/'book.txt'
            source.write_text('First paragraph\nSecond paragraph\n',encoding='utf-8')
            settings=Settings(analyze_novel=False, model='original-model')
            job=Job(load_book(source),settings,root/'data'/'jobs')
            initial=Mock()
            initial.translate.return_value='Old translation'
            engine=TranslationEngine(job,client=initial)
            engine.run()
            engine.set_bookmarks([0],'Important')
            original_directory=job.directory
            job.configure_translation(replace(settings,model='new-model'))
            client=LMStudioClient(job.settings)
            client.complete=Mock(return_value='New model translation')
            TranslationEngine(job,client=client).retranslate_chunks([1])
            self.assertEqual(job.directory,original_directory)
            self.assertEqual(client.settings.model,'new-model')
            self.assertEqual(job.read_translation(0),'Old translation')
            self.assertEqual(job.read_translation(1),'New model translation')
            self.assertEqual(job.get_bookmarks(),{0:'Important'})
            saved=saved_books(root/'data'/'jobs')[0]
            self.assertEqual(saved.settings.model,'original-model')
            self.assertEqual(saved.last_model,'new-model')
            reopened=Job(load_book(saved.source_path),saved.settings,root/'data'/'jobs')
            reopened.configure_translation(replace(saved.settings,model=saved.last_model))
            self.assertEqual(reopened.directory,original_directory)
            self.assertEqual(reopened.settings.model,'new-model')
            with self.assertRaises(ValueError):
                reopened.configure_translation(replace(reopened.settings,output_lang='Greek'))
            self.assertEqual(reopened.settings.output_lang,'English')


if __name__=='__main__':
    unittest.main()
