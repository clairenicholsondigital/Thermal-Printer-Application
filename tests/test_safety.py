"""Regression checks for paper-saving guards; no printer is contacted."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from PIL import Image

from bluetooth_link import RFCOMMLink, PrinterError, Cancelled
from converter import comparison_page, prepare_image, Settings, ConversionError
from printer_service import PrinterService, DEFAULTS
import update_manager as updates
import test_app
from test_printing import FakeLink


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.link=FakeLink();self.link.release.set()
        self.service=PrinterService(self.root/'jobs',self.root/'config.json',link=self.link)
        self.snapshot=(b'cached pixels',{'dpi':203,'settings':{'paper':'a4'},'warnings':[]},b'PDF')
    def tearDown(self):self.service.close();self.tmp.cleanup()
    def start(self, identifier='a', **kwargs):
        return self.service.start('print',identifier*32,self.snapshot,config=DEFAULTS,confirmed=True,**kwargs)
    def wait(self):
        self.service.worker.join(3);self.assertFalse(self.service.worker.is_alive())

    def test_unconfirmed_print_blocks_new_request_after_connect_and_restart(self):
        with patch('printer_service.prepare_job',return_value=(b'raw',{})) as prepare:
            self.start();self.wait()
            self.assertEqual(prepare.call_args.args[0],b'cached pixels')
            with self.assertRaises(PrinterError):self.start('b')
            self.service.start('connect','c'*32);self.wait()
            with self.assertRaises(PrinterError):self.start('d')
            reopened=PrinterService(self.root/'other',self.service.config_path,link=FakeLink())
            try:
                with self.assertRaises(PrinterError):reopened.start('print','e'*32,self.snapshot,config=DEFAULTS,confirmed=True)
                with self.assertRaises(PrinterError):reopened.paper_ready('stale',True)
                reopened.paper_ready(reopened.paper_check['task_id'],True)
                self.assertIsNone(reopened.paper_check)
            finally:reopened.close()

    def test_partial_failure_persists_recovery_but_preparation_failure_does_not(self):
        with patch('printer_service.prepare_job',side_effect=PrinterError('driver failed')):
            self.start();self.wait()
        self.assertIsNone(self.service.paper_check)
        self.assertNotIn('fresh sheet',self.service.task['message'])
        with patch('printer_service.prepare_job',return_value=(b'raw',{})),patch.object(self.link,'send',side_effect=Cancelled('stopped')):
            self.start('b');self.wait()
        self.assertEqual(self.service.paper_check['status'],'uncertain')
        self.assertEqual(json.loads(self.service.paper_path.read_text())['status'],'uncertain')
        self.service.start('connect','c'*32);self.wait()
        with self.assertRaises(PrinterError):self.start('d')

    def test_preflight_and_quality_ack_required_and_settings_are_atomic(self):
        with self.assertRaises(PrinterError):self.service.start('print','a'*32,self.snapshot)
        self.snapshot[1]['warnings']=['Heavy coverage']
        with self.assertRaises(PrinterError):self.start('b')
        with patch('printer_service.prepare_job',return_value=(b'raw',{})) as prepare:
            self.service.start('print','c'*32,self.snapshot,config=DEFAULTS|{'density':15},confirmed=True,warnings_confirmed=True)
            self.wait()
        self.assertEqual(prepare.call_args.args[2]['density'],15)
        self.assertEqual(self.service.config['density'],15)

    def test_corrupt_preferences_are_preserved_and_reported(self):
        for value in (None,'text',42,[],{'density':999,'settings_version':2}):
            self.service.config_path.write_text(json.dumps(value))
            reopened=PrinterService(self.root/'other',self.service.config_path)
            try:
                self.assertEqual(reopened.config,DEFAULTS)
                self.assertTrue(reopened.state()['config_warning'])
                self.assertTrue(list(self.root.glob('config.json.invalid-*')))
            finally:reopened.close()

    def test_long_progressing_transfer_and_partial_writes_finish(self):
        clock=[0.0];data=b'x'*1_600_000
        class Socket:
            def send(self,part):return min(127,len(part))
        class Event:
            def is_set(self):return False
            def wait(self,seconds):clock[0]+=seconds;return False
        link=RFCOMMLink();link.sock=Socket()
        with patch('bluetooth_link.time.monotonic',side_effect=lambda:clock[0]),patch('bluetooth_link.select.select',side_effect=lambda r,w,e,t:([],w,[])):
            result=link.send(data,Event(),lambda *_:None)
        self.assertEqual(result['sent'],len(data))
        self.assertGreater(clock[0],300)
        self.assertLess(clock[0],320)

    def test_stalled_transfer_stops_without_retry(self):
        clock=[0.0];link=RFCOMMLink();link.sock=Mock()
        def blocked(*args):clock[0]+=1;return [],[],[]
        with patch('bluetooth_link.time.monotonic',side_effect=lambda:clock[0]),patch('bluetooth_link.select.select',side_effect=blocked):
            with self.assertRaisesRegex(PrinterError,'stalled'):link.send(b'raw',threading.Event(),lambda *_:None)
        link.sock is None or link.sock.send.assert_not_called()

    def test_comparison_crops_keep_native_scale(self):
        original=Image.linear_gradient('L').resize((300,300)).convert('RGB')
        settings=Settings(crop=False)
        full,_=prepare_image(original,settings)
        sheet,meta=comparison_page(original,settings,{'x':50,'y':50})
        x,y,w,h=meta['comparison']['crop_pixels']
        self.assertEqual(sheet.crop((64,190,64+w,190+h)).tobytes(),full.crop((x,y,x+w,y+h)).tobytes())
        self.assertEqual(sheet.size,(1678,2374))
        for selection in ({'x':-1,'y':50},{'x':float('nan'),'y':50}):
            with self.assertRaises(ConversionError):comparison_page(original,settings,selection)

    def test_atomic_write_syncs_before_rename_and_syncs_directory(self):
        events=[];real_replace=Path.replace
        with patch('update_manager.os.fsync',side_effect=lambda fd:events.append('sync')),patch.object(Path,'replace',autospec=True,side_effect=lambda a,b:(events.append('rename'),real_replace(a,b))[1]):
            updates.atomic_json(self.root/'journal.json',{'stage':'pending'})
        self.assertEqual(events[0],'sync');self.assertIn('sync',events[events.index('rename')+1:])

    def test_old_supervisor_installs_durable_bootstrap_and_requires_restart(self):
        root=self.root/'.local/share/thermal-prep';root.mkdir(parents=True)
        (root/'update_manager.py').write_text('# new bootstrap')
        with patch('update_manager.Path.home',return_value=self.root),patch('update_manager.platform.machine',return_value='aarch64'),patch.dict('os.environ',{'THERMAL_MANAGED':'1','THERMAL_DURABLE_SUPERVISOR':''}):
            manager=updates.Updates(Mock(preview=False),root)
        self.assertTrue(manager.restart_required);self.assertFalse(manager.enabled)
        self.assertEqual((root.parent/'thermal-prep-manager.py').read_text(),'# new bootstrap')


class CancelHTTPTests(unittest.TestCase):
    setUp=test_app.HTTPTests.setUp
    tearDown=test_app.HTTPTests.tearDown
    request=test_app.HTTPTests.request
    def test_cancel_bypasses_conversion_gate_but_not_authentication(self):
        with self.server.operation_gate,patch.object(self.server.printer,'cancel',return_value={'cancelled':True}) as cancel:
            self.assertEqual(self.request('POST','/api/printer/cancel',b'{}',{})[0],403)
            cancel.assert_not_called()
            self.assertEqual(self.request('POST','/api/printer/cancel',b'{}',self.headers)[0],200)
            cancel.assert_called_once()

if __name__=='__main__':unittest.main()
