"""One explicit operation at a time, with idempotent print requests and cancellation."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import re
import threading
import time
import uuid

from bluetooth_link import BlueZ, Cancelled, PairingPrompt, PrinterError, RFCOMMLink, address, channel_number
from printer_driver import find_driver, prepare_job, test_page
from update_manager import atomic_json, metadata

DEFAULTS = {"address":"E2:BF:FB:66:30:50","channel":1,"media":101,"density":8,
            "print_mode":5,"chunk_size":256,"pause_ms":50}


def validate_config(data):
    if not isinstance(data,dict) or set(data)-set(DEFAULTS):
        raise PrinterError("Invalid printer settings.")
    result = DEFAULTS | data
    result["address"] = address(result["address"])
    channel_number(result["channel"])
    allowed = {key:(DEFAULTS[key],) for key in DEFAULTS if key != "address"}
    allowed.update(density=(1,8,15),media=(101,103))
    for key, options in allowed.items():
        if type(result[key]) is not int or result[key] not in options:
            raise PrinterError(f"Unsupported {key} setting.")
    return result


class PrinterService:
    def __init__(self, directory, config_path=None, bluez_factory=BlueZ, link=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.config_path = Path(config_path) if config_path else Path.home()/".config/thermal-prep/printer.json"
        self.config = dict(DEFAULTS)
        self.config_warning = None
        try:
            saved = json.loads(self.config_path.read_text())
            if not isinstance(saved, dict):
                raise ValueError("Settings must be an object")
            # Old versions forced density 15; start those users on the detail
            # baseline once, but retain explicitly saved quality choices later.
            version = saved.pop("settings_version", 0)
            self.config = validate_config(saved if version == 2 else {"address": saved["address"]})
        except FileNotFoundError:
            pass
        except (OSError,ValueError,TypeError,KeyError):
            self.config_warning = "Saved settings could not be read. Defaults restored; verify the printer address and paper before printing."
            if self.config_path.exists():
                try:
                    self.config_path.rename(self.config_path.with_name(self.config_path.name + ".invalid-" + uuid.uuid4().hex))
                except OSError:
                    pass
        self.lock = threading.RLock()
        self.task = None
        self.worker = None
        self.cancel_event = threading.Event()
        self.prompts = PairingPrompt()
        self.bluez_factory = bluez_factory
        self.bluez = None
        self.link = link or RFCOMMLink()
        self.seen = OrderedDict()
        self.discovery = {"adapters":[],"devices":[]}
        self.discovery_error = None
        self.driver = find_driver()
        self.report = {}
        self.checked_job = None
        self.readiness = None
        self.last_print = None
        self.paper_path = self.config_path.with_name("paper-state.json")
        self.paper_check = None
        try:
            saved = json.loads(self.paper_path.read_text())
            if not isinstance(saved, dict) or saved.get("status") not in ("ready", "uncertain", "check"):
                raise ValueError("Invalid paper state")
            if saved["status"] != "ready":
                if not isinstance(saved.get("task_id"),str):
                    raise ValueError("Missing paper check identifier")
                self.paper_check = saved
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.paper_check = {"status":"uncertain", "task_id":"recovery"}

    def set_paper_check(self, status):
        record = {"status":status, "task_id":self.task["id"]}
        atomic_json(self.paper_path, record)
        self.paper_check = record

    def paper_ready(self, identifier, confirmed):
        with self.lock:
            if self.active() or not self.paper_check or identifier != self.paper_check["task_id"] or confirmed is not True:
                raise PrinterError("Confirm the current paper check after printing has stopped.")
            atomic_json(self.paper_path, {"status":"ready"})
            self.paper_check = None

    def bluetooth(self):
        if self.bluez is None:
            self.bluez = self.bluez_factory(self.prompts)
        return self.bluez

    def state(self):
        with self.lock:
            return deepcopy({"config":self.config,"task":self.task,"driver":self.driver,
                             "discovery":self.discovery,"bluetooth_error":self.discovery_error,
                             "connection":self.link.state(),"prompt":self.prompts.public(),
                             "pairing_display":self.bluez.display if self.bluez else None,
                             "last_print":self.last_print,"paper_check":self.paper_check,"config_warning":self.config_warning,"readiness":self.readiness})

    def active(self):
        with self.lock:
            return bool(self.task and self.task["status"]=="running")

    def save(self,data):
        config = validate_config(data)
        with self.lock:
            if self.active():
                raise PrinterError("Wait for the current printer operation to finish before changing its settings.")
            old = self.config
            self.config_path.parent.mkdir(parents=True,exist_ok=True)
            atomic_json(self.config_path, config | {"settings_version":2})
            self.config_warning = None
            if config != old:
                self.checked_job = None
                self.readiness = None
            self.config = config
            if (old["address"],old["channel"]) != (config["address"],config["channel"]):
                self.link.disconnect()
            self.driver = find_driver()
            return self.state()

    def start(self, kind, request_id, snapshot=None, config=None, confirmed=False, warnings_confirmed=False):
        if kind not in ("refresh","scan","pair","setup","connect","disconnect","test","print","check","check-test"):
            raise PrinterError("Unknown printer action.")
        if not isinstance(request_id,str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}",request_id):
            raise PrinterError("Invalid request identifier. Reload the page.")
        with self.lock:
            if request_id in self.seen:
                return {"task_id":self.seen[request_id],"duplicate":True}
            if self.active():
                raise PrinterError("A printer operation is already running. It will not be started twice.")
            if kind in ("print","check") and snapshot is None:
                raise PrinterError("Update the page preview before printing.")
            if kind in ("check","check-test") and self.paper_check:
                raise PrinterError("Finish the previous paper check before opening another connection.")
            if kind in ("print", "test"):
                if self.paper_check:
                    raise PrinterError("Check the previous print and confirm a fresh sheet before printing again.")
                if confirmed is not True or config is None:
                    raise PrinterError("Review the page and printer settings before printing.")
                if snapshot and snapshot[1].get("warnings") and warnings_confirmed is not True:
                    raise PrinterError("Inspect the detail and acknowledge the quality warnings before printing.")
            if config is not None:
                self.save(config)
            identifier = uuid.uuid4().hex
            self.task = {"id":identifier,"kind":kind,"status":"running","message":"Starting…",
                         "sent":0,"total":0,"started":time.time(),"finished":None}
            self.cancel_event = threading.Event()
            self.seen[request_id] = identifier
            while len(self.seen)>100:
                self.seen.popitem(last=False)
            config = deepcopy(self.config)
            self.worker = threading.Thread(target=self._work,args=(kind,config,snapshot),daemon=True)
            self.worker.start()
            return {"task_id":identifier,"duplicate":False}

    def update(self,message,**fields):
        with self.lock:
            self.task.update(message=message,**fields)

    def finish(self,message,status):
        # Complete the report before another request may start a new worker.
        with self.lock:
            self.task.update(message=message,status=status,finished=time.time())
            self.report["task"] = deepcopy(self.task)

    def _work(self,kind,config,snapshot):
        try:
            if kind in ("refresh","scan","pair"):
                bt = self.bluetooth()
                if kind=="scan":
                    discovery = bt.scan(self.cancel_event,self.update)
                elif kind=="pair":
                    discovery = bt.pair(config["address"],self.cancel_event,self.update)
                else:
                    discovery = bt.devices()
                with self.lock:
                    self.discovery,self.discovery_error = discovery,None
                    self.driver = find_driver()
                message = {"refresh":"Printer setup refreshed.","scan":"Scan complete. Select your printer below.",
                           "pair":"Printer paired and remembered. You can now connect."}[kind]
            elif kind in ("connect","setup"):
                if kind == "setup":
                    bt = self.bluetooth()
                    discovery = bt.devices()
                    if not any(d["address"].upper()==config["address"] for d in discovery["devices"]):
                        discovery = bt.scan(self.cancel_event,self.update)
                    if self.cancel_event.is_set():
                        raise Cancelled("Connection cancelled.")
                    discovery = bt.pair(config["address"],self.cancel_event,self.update)
                    with self.lock:
                        self.discovery,self.discovery_error = discovery,None
                self.update("Opening a Bluetooth serial connection…")
                self.link.connect(config["address"],config["channel"])
                if self.cancel_event.is_set():
                    self.link.disconnect()
                    raise Cancelled("Connection cancelled.")
                message = "F11 connected. Load A4 thermal paper, then press Print."
            elif kind in ("check","check-test"):
                if kind == "check-test":
                    snapshot = test_page()
                self._check(snapshot,config)
                message = "Checks passed: one F11 page prepared and Bluetooth connection opened. No print data sent. Review the preview, then explicitly print one sheet."
            elif kind=="disconnect":
                self.link.disconnect()
                message = "Printer connection closed. Pairing is retained."
            else:
                if kind=="test":
                    snapshot = test_page()
                self._print(snapshot,config,kind)
                message = "Data sent. Check the paper and record the result below; the printer has not confirmed a successful print."
            self.finish(message,"sent" if kind in ("print","test") else "done")
        except Cancelled as exc:
            self.finish(str(exc),"cancelled")
        except Exception as exc:
            if kind in ("refresh","scan","pair"):
                with self.lock:
                    self.discovery_error = str(exc)
            recovery = (" No automatic retry or mid-page resume. Let buffered printing finish, "
                        "power-cycle the F11, load a fresh sheet and close other printer apps "
                        "before printing the whole page again.") if kind in ("print","test") and self.paper_check else ""
            self.finish(str(exc) + recovery,"failed")

    @staticmethod
    def job_key(snapshot,config):
        return hashlib.sha256(snapshot[0]+json.dumps(config,sort_keys=True).encode()).hexdigest()

    def prepare(self,snapshot,config,kind):
        import shutil
        job_dir = self.directory/"last-job"
        shutil.rmtree(job_dir,ignore_errors=True)
        job_dir.mkdir()
        with self.lock:
            self.report = {"app_version":metadata(Path(__file__).resolve().parent)["version"],"system":platform.platform(),"config":config,"kind":kind}
        if snapshot[1].get("dpi") != 203 or snapshot[1].get("settings",{}).get("paper") != "a4":
            raise PrinterError("Use A4 at 203 DPI for the Rongta F11, then update the preview.")
        data,info = prepare_job(snapshot[0],snapshot[1],config,job_dir,self.cancel_event,self.update)
        with self.lock:
            self.report["preparation"] = info
        return data,info

    def _check(self,snapshot,config):
        with self.lock:
            self.checked_job = None
            self.readiness = None
        self.link.disconnect()
        data,info = self.prepare(snapshot,config,"check")
        try:
            if self.cancel_event.is_set():
                raise Cancelled("Check cancelled. No print data sent.")
            self.update("Driver check passed. Checking Bluetooth without sending print data…")
            self.link.connect(config["address"],config["channel"])
            if self.cancel_event.is_set():
                raise Cancelled("Check cancelled. No print data sent.")
            if self.cancel_event.wait(1):
                raise Cancelled("Check cancelled. No print data sent.")
            if not self.link.state()["connected"]:
                raise PrinterError("Bluetooth disconnected during the check. No print data sent.")
        finally:
            self.link.disconnect()
        with self.lock:
            self.checked_job = (self.job_key(snapshot,config),data,info)
            self.readiness = {"preview_sha256":hashlib.sha256(snapshot[0]).hexdigest(),"config":config,
                              "bytes":len(data),"minimum_seconds":round(((len(data)+255)//256)*config["pause_ms"]/1000,1),
                              "checked_at":time.time(),"print_data_sent":False}
            self.report["readiness"] = deepcopy(self.readiness)

    def _print(self,snapshot,config,kind):
        self.link.disconnect()
        with self.lock:
            cached = self.checked_job
            self.checked_job = None
            self.readiness = None
            self.last_print = None
        if cached and cached[0] == self.job_key(snapshot,config):
            _,data,info = cached
            with self.lock:
                self.report.update(kind=kind,preparation=info,used_checked_job=True)
        else:
            data,info = self.prepare(snapshot,config,kind)
        self.update("Sending printer data over Bluetooth. Do not start another print job.",total=len(data))
        def progress(sent,total):
            self.update(f"Sending printer data: {sent:,} of {total:,} bytes.",sent=sent,total=total)
        try:
            if self.cancel_event.is_set():
                raise Cancelled("Print cancelled. No data was sent.")
            self.update("Connecting to the Rongta F11 for this page…")
            self.link.connect(config["address"],config["channel"])
            if self.cancel_event.is_set():
                raise Cancelled("Print cancelled. No data was sent.")
            # Persist before the first possible byte; a crash must never permit a blind retry.
            with self.lock:
                self.set_paper_check("uncertain")
                self.last_print = {"task_id":self.task["id"],"kind":kind,"outcome":"unconfirmed"}
            transfer = self.link.send(data,self.cancel_event,progress,config["chunk_size"],config["pause_ms"])
        finally:
            self.link.disconnect()
        with self.lock:
            self.report["transfer"] = transfer
            self.set_paper_check("check")

    def cancel(self):
        with self.lock:
            if not self.active():
                return self.state()
            self.cancel_event.set()
            kind = self.task["kind"]
        if kind in ("test","print","connect","setup","check","check-test"):
            self.link.disconnect()
        if kind in ("pair","setup") and self.bluez:
            self.bluez.cancel_pairing()
        return self.state()

    def answer(self, data):
        if not isinstance(data,dict) or not self.bluez:
            raise PrinterError("No pairing question is waiting.")
        self.bluez.answer(data.get("id"),data.get("value"))

    def outcome(self,identifier,outcome):
        if outcome not in ("visible","blank","no-response","distorted"):
            raise PrinterError("Choose one of the displayed print results.")
        with self.lock:
            if self.active() or not self.last_print or identifier!=self.last_print["task_id"]:
                raise PrinterError("That print result is no longer current.")
            if outcome != "visible":
                self.set_paper_check("uncertain")
            self.last_print["outcome"] = outcome
            self.report["observed_result"] = deepcopy(self.last_print)

    def diagnostics(self):
        with self.lock:
            report = deepcopy(self.report)
            report.update(task=deepcopy(self.task),connection=self.link.diagnostics(),driver=self.driver,
                          bluetooth_error=self.discovery_error)
        log = self.directory/"last-job/driver.log"
        if log.exists():
            with log.open("rb") as stream:
                stream.seek(max(0,log.stat().st_size-48000))
                report["driver_log_tail"] = stream.read(48000).decode("utf-8",errors="replace")
        return report

    def close(self):
        try:
            self.cancel()
        except Exception:
            self.cancel_event.set()
        self.link.disconnect()
        if self.worker:
            self.worker.join(timeout=15)
        if self.bluez:
            self.bluez.close()
