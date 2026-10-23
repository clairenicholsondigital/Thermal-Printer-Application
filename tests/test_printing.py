import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from bluetooth_link import Cancelled, PairingPrompt, PrinterError, RFCOMMLink, address, channel_number
from printer_driver import prepare_job, render_prepared_pdf, run_stage, test_page
from printer_service import DEFAULTS, PrinterService, validate_config
import test_app


class PairingTests(unittest.TestCase):
    def test_validation_and_no_pin_retention(self):
        self.assertEqual(address('e2:bf:fb:66:30:50'),DEFAULTS['address'])
        for mac in ('bad','00:11:22:33:44:55;echo hi',None):
            with self.assertRaises(PrinterError):address(mac)
        for value in (True,0,31,'1'):
            with self.assertRaises(PrinterError):channel_number(value)
        replies=[];rejections=[];prompts=PairingPrompt()
        q=prompts.ask('pin','Enter PIN',replies.append,lambda:rejections.append(True))
        with self.assertRaises(PrinterError):prompts.answer('old','1234')
        with self.assertRaises(PrinterError):prompts.answer(q['id'],'')
        self.assertEqual(prompts.public(),q)
        prompts.answer(q['id'],'1234')
        self.assertEqual(replies,['1234']);self.assertIsNone(prompts.public())
        self.assertNotIn('1234',str(vars(prompts)))

    def test_passkey_confirmation_and_cancel(self):
        replies=[];rejected=[];p=PairingPrompt()
        q=p.ask('passkey','Passkey',replies.append,lambda:rejected.append(True))
        with self.assertRaises(PrinterError):p.answer(q['id'],'1234567')
        p.answer(q['id'],'000123');self.assertEqual(replies,[123])
        q=p.ask('confirm','Confirm',lambda:replies.append(True),lambda:rejected.append(True))
        p.answer(q['id'],False);self.assertEqual(rejected,[True])
        p.ask('pin','PIN',replies.append,lambda:rejected.append(True));p.cancel()
        self.assertIsNone(p.public());self.assertEqual(rejected,[True,True])


class PartialSocket:
    def __init__(self,sock):self.sock=sock
    def fileno(self):return self.sock.fileno()
    def send(self,data):return self.sock.send(data[:7])
    def shutdown(self,how):self.sock.shutdown(how)
    def close(self):self.sock.close()


class TransportTests(unittest.TestCase):
    def test_every_byte_survives_partial_socket_writes(self):
        left,right=socket.socketpair();left.setblocking(False)
        link=RFCOMMLink();link.sock=PartialSocket(left)
        data=bytes(range(256))*5;received=bytearray()
        def read():
            while len(received)<len(data):received.extend(right.recv(1024))
        reader=threading.Thread(target=read);reader.start();progress=[]
        try:
            result=link.send(data,threading.Event(),lambda a,b:progress.append((a,b)),128,0)
            reader.join(3)
            self.assertEqual(bytes(received),data);self.assertEqual(result['sent'],len(data))
            self.assertEqual(progress[-1],(len(data),len(data)))
        finally:link.disconnect();right.close()

    def test_cancel_closes_socket_and_does_not_retry(self):
        left,right=socket.socketpair();left.setblocking(False)
        link=RFCOMMLink();link.sock=left;cancel=threading.Event();progress=[]
        def stop(a,b):progress.append(a);cancel.set()
        with self.assertRaises(Cancelled):link.send(bytes(range(256))*10,cancel,stop,128,0)
        self.assertEqual(progress,[128]);self.assertFalse(link.state()['connected'])
        self.assertEqual(right.recv(4096),bytes(range(128)));right.close()

    def test_remote_close_and_bounded_raw_replies(self):
        left,right=socket.socketpair();left.setblocking(False)
        link=RFCOMMLink();link.sock=left
        reader=threading.Thread(target=link._receive,args=(left,));reader.start()
        right.sendall(b'x'*6000);right.shutdown(socket.SHUT_WR)
        reader.join(3)
        self.assertFalse(reader.is_alive());self.assertFalse(link.state()['connected'])
        self.assertEqual(link.state()['received_bytes'],6000)
        self.assertEqual(len(link.diagnostics()['reply_tail_hex']),8192);right.close()


class FakeLink:
    def __init__(self):self.connected=True;self.sent=[];self.entered=threading.Event();self.release=threading.Event();self.connections=[];self.send_settings=[]
    def state(self):return {'connected':self.connected,'address':DEFAULTS['address'],'channel':1}
    def connect(self,mac,channel):self.connected=True;self.connections.append((mac,channel))
    def send(self,data,cancel,progress,*args):
        self.send_settings.append(args)
        self.entered.set();self.release.wait(2)
        if cancel.is_set():raise Cancelled('Cancelled')
        self.sent.append(data);progress(len(data),len(data));return {'sent':len(data)}
    def disconnect(self):self.connected=False
    def diagnostics(self):return self.state()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.link=FakeLink()
        self.service=PrinterService(self.root/'jobs',config_path=self.root/'config.json',link=self.link)
        self.snapshot=(b'preview',{'dpi':203,'settings':{'paper':'a4'}},b'pdf')
        render=patch('printer_service.render_prepared_pdf',return_value=self.snapshot[:2])
        self.render=render.start();self.addCleanup(render.stop)
    def tearDown(self):self.service.close();self.tmp.cleanup()
    def wait(self):
        self.service.worker.join(4);self.assertFalse(self.service.worker.is_alive())

    def test_old_settings_upgrade_to_working_profile(self):
        self.service.config_path.write_text(json.dumps(DEFAULTS | {'density':15,'pause_ms':25,'media':103}))
        upgraded=PrinterService(self.root/'upgrade',config_path=self.service.config_path)
        try:
            self.assertEqual(upgraded.config,DEFAULTS)
        finally:
            upgraded.close()

    def test_quality_choices_survive_restart_without_changing_transport(self):
        self.service.save(DEFAULTS | {'density':15,'media':103})
        reopened=PrinterService(self.root/'reopened',config_path=self.service.config_path)
        try:
            self.assertEqual(reopened.config['density'],15)
            self.assertEqual(reopened.config['media'],103)
            self.assertEqual(reopened.config['pause_ms'],50)
            self.assertEqual(reopened.config['chunk_size'],256)
        finally:
            reopened.close()
        for density in (1,8,15):
            self.assertEqual(validate_config({'density':density})['density'],density)
        for invalid in ({'density':9},{'density':True},{'pause_ms':25},{'print_mode':9}):
            with self.assertRaises(PrinterError):validate_config(invalid)

    def test_connection_failure_closes_without_sending_or_retry(self):
        with patch('printer_service.prepare_job',return_value=(b'raw',{})), patch.object(self.link,'connect',side_effect=PrinterError('offline')) as connect:
            self.service.start('print','a'*32,self.snapshot);self.wait()
        connect.assert_called_once()
        self.assertFalse(self.link.connected)
        self.assertEqual(self.link.sent,[])
        self.assertEqual(self.service.state()['task']['status'],'failed')

    def test_preparation_finishes_before_fresh_connection(self):
        self.link.connected=False
        def prepare(*args):
            self.assertFalse(self.link.connected)
            self.assertEqual(self.link.connections,[])
            self.assertEqual(args[2]['density'],8)
            return b'raw',{}
        with patch('printer_service.prepare_job',side_effect=prepare):
            self.link.release.set()
            self.service.start('print','a'*32,self.snapshot);self.wait()
        self.assertEqual(self.link.sent,[b'raw'])
        self.assertFalse(self.link.connected)

    def test_setup_scans_pairs_then_connects_without_printing(self):
        from unittest.mock import Mock
        bt=Mock()
        bt.devices.return_value={'devices':[],'adapters':[]}
        found={'devices':[{'address':DEFAULTS['address'],'paired':True}],'adapters':[]}
        bt.scan.return_value=found;bt.pair.return_value=found
        with patch.object(self.service,'bluetooth',return_value=bt):
            self.service.start('setup','a'*32);self.wait()
        bt.scan.assert_called_once();bt.pair.assert_called_once()
        self.assertEqual(self.link.connections,[(DEFAULTS['address'],1)])
        self.assertEqual(self.link.sent,[])
        self.assertEqual(self.service.state()['task']['status'],'done')

    def test_duplicate_request_sends_once_and_outcome_is_explicit(self):
        with patch('printer_service.prepare_job',return_value=(b'raw-packet',{'test':True})):
            result=self.service.start('print','a'*32,self.snapshot);self.assertTrue(self.link.entered.wait(2))
            duplicate=self.service.start('print','a'*32,self.snapshot)
            self.assertTrue(duplicate['duplicate']);self.assertEqual(result['task_id'],duplicate['task_id'])
            with self.assertRaises(PrinterError):self.service.start('print','b'*32,self.snapshot)
            with self.assertRaises(PrinterError):self.service.save(DEFAULTS)
            self.link.release.set();self.wait()
        self.assertEqual(self.link.sent,[b'raw-packet']);state=self.service.state()
        self.assertEqual(self.link.send_settings,[(256,50)])
        self.assertEqual(self.link.connections,[(DEFAULTS['address'],1)])
        self.assertFalse(self.link.connected)
        self.render.assert_called_once()
        self.assertEqual(state['task']['status'],'sent');self.assertEqual(state['last_print']['outcome'],'unconfirmed')
        self.service.outcome(result['task_id'],'blank')
        self.assertEqual(self.service.diagnostics()['observed_result']['outcome'],'blank')
        self.assertTrue(self.service.start('print','a'*32,self.snapshot)['duplicate'])
        self.assertEqual(len(self.link.sent),1)

    def test_driver_failure_sends_nothing(self):
        with patch('printer_service.prepare_job',side_effect=PrinterError('bad driver')):
            self.service.start('print','a'*32,self.snapshot);self.wait()
        self.assertEqual(self.service.state()['task']['status'],'failed')
        self.assertEqual(self.link.sent,[]);self.assertIsNone(self.service.state()['last_print'])

    def test_cancellation_and_fresh_connection_on_next_print(self):
        with patch('printer_service.prepare_job',return_value=(b'raw',{})):
            self.service.start('print','a'*32,self.snapshot);self.assertTrue(self.link.entered.wait(2))
            self.service.cancel();self.wait()
        self.assertEqual(self.service.state()['task']['status'],'cancelled');self.assertEqual(self.link.sent,[])
        self.assertFalse(self.link.connected)
        with patch('printer_service.prepare_job',return_value=(b'next',{})):
            self.link.release.set()
            self.service.start('test','b'*32);self.wait()
        self.assertEqual(self.link.sent,[b'next'])
        self.assertEqual(len(self.link.connections),2)
        self.assertFalse(self.link.connected)

    def test_config_persistence_validation_and_address_change_disconnect(self):
        self.service.save(DEFAULTS|{'address':'AA:BB:CC:DD:EE:FF'})
        self.assertFalse(self.link.connected)
        self.assertEqual(json.loads(self.service.config_path.read_text())['media'],101)
        self.assertEqual(self.service.config_path.stat().st_mode & 0o777,0o600)
        for data in ({'channel':True},{'pause_ms':-1},{'media':999},{'unknown':1}):
            with self.assertRaises(PrinterError):validate_config(data)


class DriverTests(unittest.TestCase):
    def test_pdf_render_matches_working_script(self):
        from reportlab.pdfgen import canvas
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            pdf=canvas.Canvas(str(root/'source.pdf'),pagesize=(595.276,841.89))
            pdf.drawString(100,700,'First page');pdf.showPage()
            pdf.drawString(100,700,'Second page');pdf.showPage();pdf.save()
            data,metadata=render_prepared_pdf((root/'source.pdf').read_bytes(),root,threading.Event(),lambda s:None)
            subprocess.run(['pdftoppm','-f','1','-l','1','-singlefile','-r','203','-png',str(root/'source.pdf'),str(root/'reference')],check=True,capture_output=True)
            with Image.open(root/'reference.png') as reference, Image.open(io.BytesIO(data)) as actual:
                self.assertEqual(actual.mode,'1')
                self.assertEqual(actual.size,reference.size)
                self.assertEqual(actual.tobytes(),reference.convert('1').tobytes())
            self.assertEqual(metadata['dpi'],203)
            self.assertEqual(metadata['settings']['paper'],'a4')

    def test_single_page_conversion_rejects_tiling_and_missing_confirmation(self):
        png = io.BytesIO()
        Image.new('1', (20, 30), 0).save(png, format='PNG')
        driver = {'ready': True, 'ppd': 'F11.ppd', 'cupsfilter': 'cupsfilter',
                  'filter': 'vendor-filter', 'information': {'options': {
                      'MediaTracking': ['101'], 'Darkness': ['8','15'],
                      'PrintMode': ['5'], 'Resolution': ['203dpi']}}}
        metadata = {'dpi': 203, 'black_pixels': 600,
                    'settings': {'paper': 'a4'}, 'orientation': 'portrait'}
        for confirmation, accepted in (
            ('xpages = 1x8.3in, ypages = 1x11.7in', True),
            ('xpages = 2x4.14in, ypages = 2x5.85in', False),
            ('', False),
            ('xpages = 1x8.3in, ypages = 1x11.7in\nxpages = 2x4in, ypages = 1x11in', False),
        ):
            with self.subTest(confirmation=confirmation), tempfile.TemporaryDirectory() as directory:
                def stage(command, output, log, *args, **kwargs):
                    if command[0] == 'cupsfilter':
                        self.assertNotIn('ppi=203', command)
                        self.assertIn('print-scaling=fit', command)
                        Path(output).write_bytes(b'RaS2')
                        Path(log).write_text(confirmation)
                    else:
                        Path(output).write_bytes(bytes.fromhex('a31e1c') + b'x' * 30)
                with patch('printer_driver.find_driver', return_value=driver), patch('printer_driver.run_stage', side_effect=stage):
                    if accepted:
                        data, _ = prepare_job(png.getvalue(), metadata, DEFAULTS, directory, threading.Event(), lambda s: None)
                        self.assertTrue(data.startswith(bytes.fromhex('a31e1c')))
                    else:
                        with self.assertRaisesRegex(PrinterError, 'did not confirm one page'):
                            prepare_job(png.getvalue(), metadata, DEFAULTS, directory, threading.Event(), lambda s: None)

    def test_test_pattern_is_nonblank_native_a4(self):
        data,metadata=test_page()
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.mode,'1');self.assertEqual(image.size,(1678,2374))
            self.assertEqual(image.getpixel((150,180)),0);self.assertEqual(image.getpixel((1600,2300)),255)
        self.assertEqual(metadata['dpi'],203)

    def test_bad_preview_stops_before_driver_or_transport(self):
        with patch('printer_driver.find_driver') as find:
            for meta in ({'dpi':300,'black_pixels':1},{'dpi':203,'black_pixels':0}):
                with self.assertRaises(PrinterError):prepare_job(b'',meta,DEFAULTS,'.',threading.Event(),lambda s:None)
            find.assert_not_called()

    def test_cancellable_subprocess_stops(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            cancel=threading.Event();cancel.set()
            started=time.monotonic()
            with self.assertRaises(Cancelled):run_stage([sys.executable,'-c','import time;time.sleep(30)'],Path(tmp)/'out',Path(tmp)/'log',cancel)
            self.assertLess(time.monotonic()-started,4)


class PrinterHTTPTests(unittest.TestCase):
    setUp=test_app.HTTPTests.setUp
    tearDown=test_app.HTTPTests.tearDown
    request=test_app.HTTPTests.request
    upload=test_app.HTTPTests.upload

    def test_printer_routes_require_session_and_mutations_require_token(self):
        for path in ('status','diagnostics','test-page.png'):
            self.assertEqual(self.request('GET','/api/printer/'+path)[0],403)
            self.assertEqual(self.request('GET','/api/printer/'+path,headers=self.headers)[0],200)
        for path in ('action','config','cancel','answer','outcome'):
            self.assertEqual(self.request('POST','/api/printer/'+path,b'{}',{'Cookie':self.cookie})[0],403)

    def test_only_current_preview_is_snapshotted(self):
        job=self.upload();results=[]
        for _ in range(2):
            status,_,body=self.request('POST','/api/convert',json.dumps({'id':job['id']}),self.headers)
            self.assertEqual(status,200);results.append(json.loads(body))
        payload={'action':'print','document_id':job['id'],'result_id':results[0]['base'].split('/')[4],'request_id':'a'*32}
        with patch.object(self.server.printer,'start',return_value={'task_id':'test'}) as start:
            self.assertEqual(self.request('POST','/api/printer/action',json.dumps(payload),self.headers)[0],400)
            start.assert_not_called()
            payload['result_id']=results[1]['base'].split('/')[4]
            self.assertEqual(self.request('POST','/api/printer/action',json.dumps(payload),self.headers)[0],202)
            snapshot=start.call_args.args[2]
            self.request('POST','/api/reset',b'',self.headers)
            self.assertTrue(snapshot[0].startswith(b'\x89PNG'));self.assertEqual(snapshot[1]['dpi'],203)
            self.assertTrue(snapshot[2].startswith(b'%PDF-'))


if __name__=='__main__':unittest.main()
