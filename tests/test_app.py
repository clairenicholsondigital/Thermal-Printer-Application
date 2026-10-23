import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app import LocalServer
from converter import ConversionError, Settings, export_result, inspect_pdf, prepare_image, render_page


def example_pdf():
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(400, 300))
    c.setFillColorRGB(1, 0, 0)
    c.rect(100, 100, 200, 70, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(100, 185, "Coloured circuit diagram")
    c.showPage()
    c.showPage()  # Intentionally blank second page.
    c.save()
    return buffer.getvalue()


class ConverterTests(unittest.TestCase):
    def test_reject_invalid_settings(self):
        for data in ({"dpi": 600}, {"page": 3}, {"margin": -1}, {"contrast": float('nan')},
                     {"paper": []}, {"mode": "bad"}, {"crop": "yes"}, {"page": True}, {"bogus": 1}):
            with self.subTest(data=data), self.assertRaises(ConversionError):
                Settings.parse(data, 2)

    def test_true_monochrome_and_paper_sizes(self):
        for paper, dpi, expected in [("a4", 203, (1678,2374)), ("a4",300,(2480,3508)),
                                     ("letter",203,(1726,2233))]:
            with self.subTest(paper=paper,dpi=dpi):
                im = Image.new("RGB", (90,100), (120,120,120))
                mono, meta = prepare_image(im, Settings(paper=paper,dpi=dpi,orientation="portrait"))
                self.assertEqual(mono.mode,"1")
                self.assertEqual(mono.size,expected)
                self.assertEqual({v for _, v in mono.convert("L").getcolors()}, {0,255})
                self.assertGreater(meta["black_pixels"],0)
                self.assertEqual(mono.getpixel((0,0)),255)

    def test_blank_and_faint_page_warnings(self):
        blank, data = prepare_image(Image.new("RGB",(200,100),"white"), Settings())
        self.assertEqual(data["black_pixels"],0)
        self.assertTrue(data["warnings"])
        _, data = prepare_image(Image.new("RGB",(200,100),(230,230,230)),
                                Settings(mode="threshold",threshold=170))
        self.assertEqual(data["black_pixels"],0)
        self.assertTrue(data["warnings"])

    def test_crop_preserves_bright_colours_and_enlarges(self):
        im=Image.new("RGB",(1000,1000),"white")
        ImageDraw.Draw(im).rectangle((400,480,600,520),fill="yellow")
        _, cropped=prepare_image(im,Settings(coloured_lines=True))
        _, full=prepare_image(im,Settings(crop=False,coloured_lines=True))
        self.assertGreater(cropped["black_pixels"],full["black_pixels"]*5)
        self.assertEqual(cropped["orientation"],"landscape")

    def test_rotation_changes_best_fit(self):
        im=Image.new("RGB",(600,100),"black")
        _,a=prepare_image(im,Settings())
        _,b=prepare_image(im,Settings(rotation=90))
        self.assertEqual(a["orientation"],"landscape")
        self.assertEqual(b["orientation"],"portrait")

    def test_pdf_matches_exported_pixels_and_exact_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            im=Image.new("RGB",(140,90),"white")
            d=ImageDraw.Draw(im);d.rectangle((10,10,90,50),fill=(120,160,200));d.text((12,60),"Test 123",fill="black")
            export_result(im,Settings(),path)
            with Image.open(path/"thermal.png") as png:
                reader=PdfReader(path/"thermal.pdf")
                self.assertEqual(len(reader.pages),1)
                page=reader.pages[0]
                self.assertAlmostEqual(float(page.mediabox.width),297/25.4*72,places=3)
                self.assertAlmostEqual(float(page.mediabox.height),210/25.4*72,places=3)
                image=list(page.images)[0].image.convert("L")
                from printer_driver import render_prepared_pdf
                expected,_=render_prepared_pdf((path/"thermal.pdf").read_bytes(),path,threading.Event(),lambda _:None)
                with Image.open(io.BytesIO(expected)) as final:
                    self.assertEqual(final.size,png.size)
                    self.assertEqual(final.tobytes(),png.tobytes())
                self.assertAlmostEqual(png.info["dpi"][0],203,delta=.1)

    def test_multipage_render_and_encrypted_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);source=path/"source.pdf";source.write_bytes(example_pdf())
            self.assertEqual(inspect_pdf(source),2)
            first=render_page(source,1,path)
            second=render_page(source,2,path)
            self.assertEqual(first.size,second.size)
            self.assertIsNotNone(ImageChops.difference(first,second).getbbox())
            self.assertEqual(second.getextrema(),((255,255),)*3)
            reader=PdfReader(source);writer=PdfWriter();writer.append_pages_from_reader(reader);writer.encrypt("password")
            locked=path/"locked.pdf"
            with locked.open("wb") as out:writer.write(out)
            with self.assertRaises(ConversionError):inspect_pdf(locked)
            source.write_text("This is not a PDF")
            with self.assertRaises(ConversionError):inspect_pdf(source)


class HTTPTests(unittest.TestCase):
    def test_image_upload_prepares_exported_pdf(self):
        for fmt in ('JPEG','PNG'):
            with self.subTest(fmt=fmt):
                image=Image.new('RGB',(240,120),'white')
                image.paste('black',(40,30,180,90))
                data=io.BytesIO();image.save(data,format=fmt)
                status,_,body=self.request('POST','/api/upload',data.getvalue(),self.headers)
                self.assertEqual(status,200,body);job=json.loads(body)
                self.assertEqual((job['kind'],job['pages']),('image',1))
                status,_,body=self.request('POST','/api/convert',json.dumps({'id':job['id']}),self.headers)
                self.assertEqual(status,200,body);result=json.loads(body)
                self.assertGreater(result['black_pixels'],0)
                status,_,pdf=self.request('GET',result['base']+'thermal.pdf',headers=self.headers)
                self.assertEqual(status,200)
                self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages),1)

    def test_image_orientation_transparency_and_invalid_content(self):
        image=Image.new('RGBA',(100,50),(0,0,0,0));image.putpixel((20,20),(0,0,0,255))
        data=io.BytesIO();image.save(data,format='PNG')
        status,_,_=self.request('POST','/api/upload',data.getvalue(),self.headers)
        self.assertEqual(status,200)
        with Image.open(self.server.job['dir']/'source.png') as normal:
            self.assertEqual(normal.getpixel((0,0)),(255,255,255))
            self.assertEqual(normal.getpixel((20,20)),(0,0,0))
        image=Image.new('RGB',(100,50),'black');exif=image.getexif();exif[274]=6
        data=io.BytesIO();image.save(data,format='JPEG',exif=exif)
        status,_,_=self.request('POST','/api/upload',data.getvalue(),self.headers)
        self.assertEqual(status,200)
        with Image.open(self.server.job['dir']/'source.png') as normal:
            self.assertEqual(normal.size,(50,100))
        for fmt in ('GIF','BMP'):
            data=io.BytesIO();image.save(data,format=fmt)
            status,_,_=self.request('POST','/api/upload',data.getvalue(),self.headers|{'X-Filename':'fake.png'})
            self.assertEqual(status,400)

    def setUp(self):
        self.server=LocalServer(("127.0.0.1",0))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        status,headers,body=self.request("GET","/")
        self.assertEqual(status,200)
        self.cookie=headers["Set-Cookie"].split(";",1)[0]
        self.headers={"Cookie":self.cookie,"X-Thermal-Token":self.server.token}

    def tearDown(self):
        tmp=self.server.temp.name
        self.server.shutdown();self.thread.join();self.server.server_close()
        self.assertFalse(Path(tmp).exists())

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=75)
        connection.request(method,path,body,headers or {})
        response=connection.getresponse();payload=response.read()
        result=(response.status,dict(response.getheaders()),payload)
        connection.close();return result

    def upload(self):
        status,_,body=self.request("POST","/api/upload",example_pdf(),self.headers|{"X-Filename":"diagram.pdf"})
        self.assertEqual(status,200,body);return json.loads(body)

    def test_roundtrip_download_reset_and_bad_upload(self):
        job=self.upload()
        status,_,body=self.request("POST","/api/convert",json.dumps({"id":job["id"],"settings":{}}),self.headers)
        self.assertEqual(status,200,body);result=json.loads(body)
        self.assertGreater(result["black_pixels"],0)
        status,headers,png=self.request("GET",result["base"]+"thermal.png?download=1",headers=self.headers)
        self.assertEqual(status,200);self.assertIn("attachment",headers["Content-Disposition"])
        with Image.open(io.BytesIO(png)) as image:self.assertEqual(image.mode,"1")
        status,_,pdf=self.request("GET",result["base"]+"thermal.pdf?download=1",headers=self.headers)
        self.assertEqual(status,200);self.assertTrue(pdf.startswith(b"%PDF"))
        status,_,_=self.request("POST","/api/upload",b"broken",self.headers)
        self.assertEqual(status,400)
        self.assertEqual(self.server.job["id"],job["id"])
        directory=self.server.job["dir"]
        status,_,_=self.request("POST","/api/reset",b"",self.headers)
        self.assertEqual(status,200);self.assertFalse(directory.exists())
        status,_,_=self.request("GET",result["base"]+"thermal.png",headers=self.headers)
        self.assertEqual(status,404)

    def test_host_cookie_and_token_protection(self):
        for headers in ({}, {"Cookie":self.cookie}, self.headers|{"X-Thermal-Token":"wrong"},
                        self.headers|{"Host":"malicious.example"}):
            status,_,_=self.request("POST","/api/upload",example_pdf(),headers)
            self.assertEqual(status,403)
        status,_,_=self.request("GET","/",headers={"Host":"malicious.example"})
        self.assertEqual(status,403)

    def test_stale_and_invalid_settings(self):
        old=self.upload();new=self.upload()
        self.assertNotEqual(old["id"],new["id"])
        status,_,_=self.request("POST","/api/convert",json.dumps({"id":old["id"]}),self.headers)
        self.assertEqual(status,404)
        status,_,_=self.request("POST","/api/convert",json.dumps({"id":new["id"],"settings":{"paper":[]}}),self.headers)
        self.assertEqual(status,400)
        status,_,_=self.request("POST","/api/convert",b"invalid-json",self.headers)
        self.assertEqual(status,400)
        with self.server.lock:
            status,_,_=self.request("POST","/api/convert",b"{}",self.headers)
            self.assertEqual(status,409)


if __name__ == "__main__":
    unittest.main()
