#!/usr/bin/env python3
"""A loopback-only PDF preparation and explicit Bluetooth printing app."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

from converter import ConversionError, Settings, export_result, inspect_pdf, render_page
from bluetooth_link import PrinterError
from printer_driver import test_page
from printer_service import PrinterService

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 25 * 1024 * 1024


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, preview=False):
        super().__init__(address, Handler)
        self.preview = preview
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.temp = tempfile.TemporaryDirectory(prefix="thermal-prep-")
        self.job = None
        self.printer = PrinterService(Path(self.temp.name) / "printer")

    def server_close(self):
        super().server_close()
        self.printer.close()
        self.temp.cleanup()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(75)

    def log_message(self, fmt, *args):
        # Avoid logging source names or upload contents.
        pass

    def send_bytes(self, body, kind="application/json", status=200, download=None, cookie=False):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", f"thermal_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def json_response(self, payload, status=200):
        self.send_bytes(json.dumps(payload).encode(), status=status)

    def error(self, message, status=400):
        self.json_response({"error": message}, status)

    def allowed_host(self):
        host = self.headers.get("Host", "").lower()
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.server.preview:
            allowed |= {"terminal.local:4173", "terminal.local"}
        return host in allowed

    def authorised(self, mutate=False):
        if not self.allowed_host():
            self.error("Open the app using its local address.", 403)
            return False
        cookie = self.headers.get("Cookie", "")
        expected = "thermal_session=" + self.server.token
        if expected not in [item.strip() for item in cookie.split(";")]:
            self.error("Reopen the app tab and try again.", 403)
            return False
        if mutate and not secrets.compare_digest(self.headers.get("X-Thermal-Token", ""), self.server.token):
            self.error("Reload the app before uploading or changing settings.", 403)
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed_host():
            return self.error("Open the app using its local address.", 403)
        if path == "/":
            html = (ROOT / "static/index.html").read_text().replace("__TOKEN__", self.server.token)
            return self.send_bytes(html.encode(), "text/html; charset=utf-8", cookie=True)
        if path == "/health":
            return self.json_response({"app": "thermal-prep", "version": "1.1.1"})
        if path in ("/static/app.js", "/static/printer.js", "/static/style.css"):
            file = ROOT / path.lstrip("/")
            return self.send_bytes(file.read_bytes(), mimetypes.guess_type(str(file))[0])
        if not self.authorised():
            return
        if path == "/api/printer/status":
            return self.json_response(self.server.printer.state())
        if path == "/api/printer/diagnostics":
            return self.send_bytes(json.dumps(self.server.printer.diagnostics(),indent=2).encode(),download="thermal-prep-diagnostics.json")
        if path == "/api/printer/test-page.png":
            return self.send_bytes(test_page()[0],"image/png")
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["api", "files"]:
            job_id, result_id, name = parts[2:]
            if name not in ("original.png", "thermal.png", "thermal.pdf", "settings.json"):
                return self.error("File not found.", 404)
            with self.server.lock:
                job = self.server.job
                if not job or job["id"] != job_id or result_id not in job["results"]:
                    return self.error("This preview has been replaced. Update the preview again.", 404)
                data = (job["dir"] / result_id / name).read_bytes()
            download = "thermal-prepared." + name.rsplit(".", 1)[1] if "download=1" in self.path else None
            return self.send_bytes(data, mimetypes.guess_type(name)[0], download=download)
        self.error("Not found.", 404)

    def read_body(self, limit):
        if self.headers.get("Transfer-Encoding"):
            raise ConversionError("Chunked uploads are not supported.")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ConversionError("Invalid upload size.") from exc
        if not 0 < size <= limit:
            raise ConversionError("Choose a PDF under 25 MB." if limit == MAX_UPLOAD else "Invalid request size.")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ConversionError("The upload was interrupted. Please try again.")
        return body

    def do_POST(self):
        if not self.authorised(mutate=True):
            return
        path = urlsplit(self.path).path
        if path.startswith("/api/printer/"):
            return self.printer_request(path)
        if path not in ("/api/upload", "/api/convert", "/api/reset"):
            return self.error("Not found.", 404)
        # Serialise expensive work and limit peak memory on a Pi.
        if not self.server.lock.acquire(blocking=False):
            return self.error("Another conversion is running. Try again when it finishes.", 409)
        try:
            if path == "/api/reset":
                self.remove_job()
                return self.json_response({"ok": True})
            if path == "/api/upload":
                body = self.read_body(MAX_UPLOAD)
                identifier = uuid.uuid4().hex
                directory = Path(self.server.temp.name) / identifier
                directory.mkdir()
                try:
                    source = directory / "source.pdf"
                    source.write_bytes(body)
                    pages = inspect_pdf(source)
                except Exception:
                    shutil.rmtree(directory)
                    raise
                self.remove_job()
                name = Path(unquote(self.headers.get("X-Filename", "document.pdf"))).name[:150]
                self.server.job = {"id": identifier, "dir": directory, "pages": pages,
                                   "name": name, "results": []}
                return self.json_response({"id": identifier, "pages": pages, "name": name})
            body = json.loads(self.read_body(8192))
            if not isinstance(body, dict):
                raise ConversionError("Invalid conversion request.")
            job = self.server.job
            if not job or body.get("id") != job["id"]:
                return self.error("Upload your PDF again to continue.", 404)
            settings = Settings.parse(body.get("settings", {}), job["pages"])
            original = render_page(job["dir"] / "source.pdf", settings.page, job["dir"])
            result_id = uuid.uuid4().hex
            directory = job["dir"] / result_id
            try:
                metadata = export_result(original, settings, directory)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            job["results"].append(result_id)
            while len(job["results"]) > 2:
                shutil.rmtree(job["dir"] / job["results"].pop(0))
            base = f'/api/files/{job["id"]}/{result_id}/'
            return self.json_response({**metadata, "base": base})
        except (ConversionError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.error(str(exc), 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.error("Conversion failed. Check available disk space and try a different PDF.", 500)
        finally:
            self.server.lock.release()

    def remove_job(self):
        if self.server.job:
            shutil.rmtree(self.server.job["dir"], ignore_errors=True)
            self.server.job = None

    def printer_request(self,path):
        try:
            data = json.loads(self.read_body(16384))
            if not isinstance(data,dict):
                raise PrinterError("Invalid printer request.")
            service = self.server.printer
            if path == "/api/printer/config":
                return self.json_response(service.save(data))
            if path == "/api/printer/cancel":
                return self.json_response(service.cancel())
            if path == "/api/printer/answer":
                service.answer(data)
                return self.json_response({"ok":True})
            if path == "/api/printer/outcome":
                service.outcome(data.get("task_id"),data.get("outcome"))
                return self.json_response({"ok":True})
            if path != "/api/printer/action":
                return self.error("Not found.",404)
            snapshot = None
            if data.get("action") == "print":
                if not self.server.lock.acquire(blocking=False):
                    return self.error("Wait for the page conversion to finish before printing.",409)
                try:
                    job = self.server.job
                    if not job or data.get("document_id") != job["id"] or not job["results"] or data.get("result_id") != job["results"][-1]:
                        raise PrinterError("This preview is no longer current. Update the preview before printing.")
                    folder = job["dir"] / job["results"][-1]
                    snapshot = ((folder / "thermal.png").read_bytes(),json.loads((folder / "settings.json").read_text()))
                finally:
                    self.server.lock.release()
            return self.json_response(service.start(data.get("action"),data.get("request_id"),snapshot),202)
        except (ValueError,TypeError) as exc:
            self.error(str(exc),400)
        except Exception:
            self.error("Printer operation failed. Check the printer status and try again.",500)


def main():
    parser = argparse.ArgumentParser(description="Thermal Prep, a local PDF preparation app")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--preview", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host", choices=["0.0.0.0"], help=argparse.SUPPRESS)
    parser.add_argument("--strictPort", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.host and not args.preview:
        parser.error("Host overrides are available only in the development preview.")
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    # Preview binding is for the development supervisor only. Installers never use it.
    address = "0.0.0.0" if args.preview else "127.0.0.1"
    try:
        server = LocalServer((address, args.port), preview=args.preview)
    except OSError:
        parser.exit(1, f"Port {args.port} is already in use. Close the other instance or choose --port.\n")
    print(f"Thermal Prep: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
