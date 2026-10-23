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
from printer_driver import prepare_job, run_stage, test_page
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
    def __init__(self):self.connected=True;self.sent=[];self.entered=threading.Event();self.release=threading.Event()
    def state(self):return {'connected':self.connected,'address':DEFAULTS['address'],'channel':1}
    def send(self,data,cancel,progress,*args):
        self.entered.set();self.release.wait(2)
        if cancel.is_set():raise Cancelled('Cancelled')
        self.sent.append(data);progress(len(data),len(data));return {'sent':len(data)}
    def disconnect(self):self.connected=False;self.release.set()
    def diagnostics(self):return self.state()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.link=FakeLink()
        self.service=PrinterService(self.root/'jobs',config_path=self.root/'config.json',link=self.link)
    def tearDown(self):self.service.close();self.tmp.cleanup()
    def wait(self):
        self.service.worker.join(4);self.assertFalse(self.service.worker.is_alive())

    def test_duplicate_request_sends_once_and_outcome_is_explicit(self):
        with patch('printer_service.prepare_job',return_value=(b'raw-packet',{'test':True})):
            result=self.service.start('print','a'*32,(b'preview',{}));self.assertTrue(self.link.entered.wait(2))
            duplicate=self.service.start('print','a'*32,(b'preview',{}))
            self.assertTrue(duplicate['duplicate']);self.assertEqual(result['task_id'],duplicate['task_id'])
            with self.assertRaises(PrinterError):self.service.start('print','b'*32,(b'preview',{}))
            with self.assertRaises(PrinterError):self.service.save(DEFAULTS)
            self.link.release.set();self.wait()
        self.assertEqual(self.link.sent,[b'raw-packet']);state=self.service.state()
        self.assertEqual(state['task']['status'],'sent');self.assertEqual(state['last_print']['outcome'],'unconfirmed')
        self.service.outcome(result['task_id'],'blank')
        self.assertEqual(self.service.diagnostics()['observed_result']['outcome'],'blank')
        self.assertTrue(self.service.start('print','a'*32,(b'preview',{}))['duplicate'])
        self.assertEqual(len(self.link.sent),1)

    def test_driver_failure_sends_nothing(self):
        with patch('printer_service.prepare_job',side_effect=PrinterError('bad driver')):
            self.service.start('print','a'*32,(b'preview',{}));self.wait()
        self.assertEqual(self.service.state()['task']['status'],'failed')
        self.assertEqual(self.link.sent,[]);self.assertIsNone(self.service.state()['last_print'])

    def test_cancellation_and_disconnected_print_guard(self):
        with patch('printer_service.prepare_job',return_value=(b'raw',{})):
            self.service.start('print','a'*32,(b'preview',{}));self.assertTrue(self.link.entered.wait(2))
            self.service.cancel();self.wait()
        self.assertEqual(self.service.state()['task']['status'],'cancelled');self.assertEqual(self.link.sent,[])
        with self.assertRaises(PrinterError):self.service.start('test','b'*32)

    def test_config_persistence_validation_and_address_change_disconnect(self):
        self.service.save(DEFAULTS|{'address':'AA:BB:CC:DD:EE:FF','media':103})
        self.assertFalse(self.link.connected)
        self.assertEqual(json.loads(self.service.config_path.read_text())['media'],103)
        self.assertEqual(self.service.config_path.stat().st_mode & 0o777,0o600)
        for data in ({'channel':True},{'pause_ms':-1},{'media':999},{'unknown':1}):
            with self.assertRaises(PrinterError):validate_config(data)


class DriverTests(unittest.TestCase):
    def test_single_page_conversion_rejects_tiling_and_missing_confirmation(self):
        png = io.BytesIO()
        Image.new('1', (20, 30), 0).save(png, format='PNG')
        driver = {'ready': True, 'ppd': 'F11.ppd', 'cupsfilter': 'cupsfilter',
                  'filter': 'vendor-filter', 'information': {'options': {
                      'MediaTracking': ['101'], 'Darkness': ['8'],
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


if __name__=='__main__':unittest.main()
