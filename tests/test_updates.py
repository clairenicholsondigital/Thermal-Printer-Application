import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

import update_manager as updates
import test_app

ROOT=Path(__file__).resolve().parent.parent


def package(number='1.3.1',extra=None):
    output=io.BytesIO()
    with ZipFile(output,'w') as bundle:
        for name in updates.FILES:
            data=(ROOT/name).read_bytes()
            if name=='release.json':
                meta=json.loads(data);meta['version']=number;data=json.dumps(meta).encode()
            bundle.writestr(name,data)
        if extra:bundle.writestr(extra,b'invalid')
    return output.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'thermal-prep';self.root.mkdir()
        meta=json.loads((ROOT/'release.json').read_text());meta['version']='1.3.0'
        (self.root/'release.json').write_text(json.dumps(meta))
        (self.root/'drivers').mkdir();(self.root/'drivers/F11.ppd').write_text('existing driver')
        self.state=self.root.parent/'.thermal-prep-updates';self.state.mkdir()
    def tearDown(self):self.temp.cleanup()
    def stage(self,number='1.3.1',extra=None):
        data=package(number,extra)
        release={'version':number,'url':'test','sha256':hashlib.sha256(data).hexdigest()}
        with patch.object(updates,'download',return_value=data):return updates.stage(self.root,release)

    def test_verified_stage_preserves_driver_and_leaves_running_app_untouched(self):
        candidate=self.stage()
        self.assertEqual(updates.metadata(candidate)['version'],'1.3.1')
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')
        self.assertEqual((candidate/'drivers/F11.ppd').read_text(),'existing driver')

    def test_checksum_and_unexpected_archive_files_rejected(self):
        with patch.object(updates,'download',return_value=b'changed'):
            with self.assertRaisesRegex(ValueError,'checksum'):updates.stage(self.root,{'url':'test','sha256':'0'*64})
        for extra in ('../outside','static/other.js'):
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.stage(extra=extra)
        self.assertEqual(list(self.state.glob('candidate-*')),[])
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')

    def test_new_dependencies_rejected_without_switch(self):
        data=package();out=io.BytesIO()
        with ZipFile(io.BytesIO(data)) as source,ZipFile(out,'w') as target:
            for name in source.namelist():
                content=source.read(name)
                if name=='release.json':
                    meta=json.loads(content);meta['dependencies']=2;content=json.dumps(meta).encode()
                target.writestr(name,content)
        data=out.getvalue()
        with patch.object(updates,'download',return_value=data):
            with self.assertRaisesRegex(ValueError,'installer update'):
                updates.stage(self.root,{'url':'test','version':'1.3.1','sha256':hashlib.sha256(data).hexdigest()})
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')

    def test_interrupted_directory_switch_recovers(self):
        candidate=self.stage();updates.switch(self.root,candidate)
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.1')
        updates.recover(self.root)
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')
        self.assertFalse((self.state/'journal.json').exists())

    def test_power_loss_between_directory_renames_recovers(self):
        updates.atomic_json(self.state/'journal.json',{'candidate':'candidate-test'})
        self.root.rename(self.state/'recover')
        updates.recover_locked(self.root)
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')

    def test_missing_staged_update_keeps_old_app_runnable(self):
        updates.atomic_json(self.state/'pending.json',{'candidate':'candidate-missing','version':'1.3.1'})
        updates.apply_pending(self.root)
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')
        self.assertFalse((self.state/'pending.json').exists())
        self.assertEqual(json.loads((self.state/'result.json').read_text())['status'],'failed')

    def test_supervisor_success_and_manual_restore(self):
        candidate=self.stage()
        updates.atomic_json(self.state/'pending.json',{'candidate':candidate.name,'version':'1.3.1'})
        with patch.object(updates.subprocess,'Popen') as process,patch.object(updates,'healthy',return_value=True):
            process.return_value.wait.return_value=0
            updates.supervise(self.root)
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.1')
        self.assertEqual(updates.metadata(self.state/'previous')['version'],'1.3.0')
        updates.atomic_json(self.state/'pending.json',{'candidate':'previous','version':'1.3.0'})
        with patch.object(updates.subprocess,'Popen'),patch.object(updates,'healthy',return_value=True):updates.supervise(self.root)
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')
        self.assertEqual(updates.metadata(self.state/'previous')['version'],'1.3.1')

    def test_failed_start_automatically_restores_and_restarts_old_app(self):
        candidate=self.stage()
        updates.atomic_json(self.state/'pending.json',{'candidate':candidate.name,'version':'1.3.1'})
        with patch.object(updates.subprocess,'Popen') as process,patch.object(updates,'healthy',return_value=False):
            updates.supervise(self.root)
            self.assertEqual(process.call_count,2)
            process.return_value.terminate.assert_called_once()
        self.assertEqual(updates.metadata(self.root)['version'],'1.3.0')
        self.assertIn('restored',json.loads((self.state/'result.json').read_text())['message'])

    def test_latest_uses_only_named_repository_asset_and_digest(self):
        release={'tag_name':'v1.3.1','body':'Changes','assets':[{'name':updates.ASSET,'digest':'sha256:'+'a'*64,
                 'browser_download_url':f'https://github.com/{updates.REPO}/releases/download/v1.3.1/{updates.ASSET}'}]}
        with patch.object(updates,'download',return_value=json.dumps(release).encode()):
            self.assertEqual(updates.latest()['version'],'1.3.1')
        release['assets'][0]['browser_download_url']='https://example.com/untrusted.zip'
        with patch.object(updates,'download',return_value=json.dumps(release).encode()):
            with self.assertRaises(ValueError):updates.latest()


class UpdateHTTPTests(unittest.TestCase):
    setUp=test_app.HTTPTests.setUp
    tearDown=test_app.HTTPTests.tearDown
    request=test_app.HTTPTests.request

    def test_update_endpoints_require_session_and_token(self):
        self.assertEqual(self.request('GET','/api/updates/status')[0],403)
        self.assertEqual(self.request('GET','/api/updates/status',headers=self.headers)[0],200)
        self.assertEqual(self.request('POST','/api/updates/action',b'{"action":"install"}',{'Cookie':self.cookie})[0],403)

    def test_no_update_during_print_or_conversion_and_no_print_during_update(self):
        service=self.server.updates;service.enabled=True;service.release={'version':'9.0.0'}
        self.server.printer.task={'status':'running'}
        self.assertEqual(self.request('POST','/api/updates/action',b'{"action":"install"}',self.headers)[0],400)
        self.server.printer.task=None
        with self.server.operation_gate:
            self.assertEqual(self.request('POST','/api/updates/action',b'{"action":"install"}',self.headers)[0],409)
        self.server.updating=True
        self.assertEqual(self.request('POST','/api/printer/action',b'{"action":"print"}',self.headers)[0],409)
        self.assertEqual(self.request('POST','/api/upload',b'example',self.headers)[0],409)

    def test_download_failure_unlocks_app_without_restart(self):
        service=self.server.updates;service.enabled=True;service.release={'version':'9.0.0'}
        with patch.object(updates,'stage',side_effect=OSError('offline')),patch.object(self.server,'shutdown') as stop:
            self.server.updating=True;service.work('install')
            self.assertFalse(self.server.updating);stop.assert_not_called()
            self.assertEqual(service.state['status'],'failed')

if __name__=='__main__':unittest.main()
