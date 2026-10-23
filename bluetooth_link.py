"""BlueZ pairing and a byte-preserving RFCOMM connection, without a TTY."""
import concurrent.futures
import re
import select
import socket
import threading
import time
import uuid


class PrinterError(ValueError):
    pass


class Cancelled(PrinterError):
    pass


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", value):
        raise PrinterError("Choose a printer or enter its Bluetooth address, for example E2:BF:FB:66:30:50.")
    return value.upper()


def channel_number(value):
    if type(value) is not int or not 1 <= value <= 30:
        raise PrinterError("The Bluetooth channel must be between 1 and 30.")
    return value


class PairingPrompt:
    """Never saves a PIN. Only one explicitly requested pairing can ask for input."""
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = None

    def ask(self, kind, message, reply, reject):
        with self.lock:
            if self.pending:
                raise PrinterError("A pairing question is already waiting for an answer.")
            public = {"id": uuid.uuid4().hex, "kind": kind, "message": message}
            self.pending = (public, reply, reject)
            return public

    def public(self):
        with self.lock:
            return dict(self.pending[0]) if self.pending else None

    def answer(self, identifier, value):
        with self.lock:
            if not self.pending or self.pending[0]["id"] != identifier:
                raise PrinterError("That pairing question has expired.")
            prompt, reply, reject = self.pending
            if value is not None:
                if prompt["kind"] == "pin" and (not isinstance(value, str) or not 1 <= len(value) <= 16 or not value.isascii() or not value.isprintable()):
                    raise PrinterError("Enter the printer's PIN, using 1 to 16 printable characters.")
                if prompt["kind"] == "passkey" and (not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}", value)):
                    raise PrinterError("Enter a numeric passkey containing up to six digits.")
                if prompt["kind"] == "confirm" and type(value) is not bool:
                    raise PrinterError("Confirm or reject the displayed pairing code.")
            self.pending = None
        if value is None or value is False:
            reject()
        elif prompt["kind"] == "confirm":
            reply()
        else:
            reply(int(value) if prompt["kind"] == "passkey" else value)

    def cancel(self):
        with self.lock:
            pending, self.pending = self.pending, None
        if pending:
            pending[2]()


class BlueZ:
    """Keep the D-Bus connection and pairing agent on one GLib thread."""
    def __init__(self, prompts):
        self.prompts = prompts
        self.ready = threading.Event()
        self.error = None
        self.target = None
        self.display = None
        self.loop = None
        self.GLib = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
            self.dbus, self.GLib = dbus, GLib
            DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus(private=True)
            self.loop = GLib.MainLoop()
            parent = self

            class Rejected(dbus.DBusException):
                _dbus_error_name = "org.bluez.Error.Rejected"

            self.Rejected = Rejected

            class Agent(dbus.service.Object):
                def check(self, device):
                    if not parent.target or str(device) != parent.target:
                        raise Rejected("No pairing requested for this device.")

                def question(self, device, kind, message, ok, err):
                    self.check(device)
                    parent.prompts.ask(kind, message, ok, lambda: err(Rejected("Pairing cancelled.")))

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Release(self):
                    parent.prompts.cancel()

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s", async_callbacks=("ok", "err"))
                def RequestPinCode(self, device, ok, err):
                    self.question(device, "pin", "Enter the PIN supplied with your printer. No PIN is guessed or stored.", ok, err)

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u", async_callbacks=("ok", "err"))
                def RequestPasskey(self, device, ok, err):
                    self.question(device, "passkey", "Enter the printer's numeric passkey.", ok, err)

                @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="", async_callbacks=("ok", "err"))
                def RequestConfirmation(self, device, passkey, ok, err):
                    self.question(device, "confirm", f"Confirm that {int(passkey):06d} matches the code shown by your printer.", ok, err)

                @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
                def RequestAuthorization(self, device):
                    self.check(device)

                @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
                def AuthorizeService(self, device, service_uuid):
                    self.check(device)
                    if str(service_uuid).lower() != "00001101-0000-1000-8000-00805f9b34fb":
                        raise Rejected("Only the requested serial service is allowed.")

                @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
                def DisplayPinCode(self, device, pincode):
                    self.check(device)
                    parent.display = "Printer pairing code: " + str(pincode)

                @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
                def DisplayPasskey(self, device, passkey, entered):
                    self.check(device)
                    parent.display = f"Printer pairing code: {int(passkey):06d}"

                @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
                def Cancel(self):
                    parent.prompts.cancel()
                    parent.display = None

            self.agent = Agent(self.bus, "/org/thermalprep/agent")
            self.manager = self._interface("/org/bluez", "org.bluez.AgentManager1")
            self.manager.RegisterAgent("/org/thermalprep/agent", "KeyboardDisplay", timeout=5)
            self.ready.set()
            self.loop.run()
        except Exception as exc:
            self.error = "Bluetooth is unavailable: " + str(exc) + ". Run the updated installer and check that Bluetooth is enabled on the Pi."
            self.ready.set()
        finally:
            if hasattr(self, "bus"):
                self.bus.close()

    def _interface(self, path, interface):
        return self.dbus.Interface(self.bus.get_object("org.bluez", path), interface)

    def call(self, function, timeout=12):
        if not self.ready.wait(6) or self.error:
            raise PrinterError(self.error or "Bluetooth service did not respond.")
        future = concurrent.futures.Future()
        def invoke():
            if future.cancelled():
                return False
            try:
                result = function()
                if not future.cancelled():
                    future.set_result(result)
            except Exception as exc:
                if not future.cancelled():
                    future.set_exception(exc)
            return False
        self.GLib.idle_add(invoke)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise PrinterError("Bluetooth did not respond in time. Check the Pi's Bluetooth settings.") from exc

    def _objects(self):
        return self._interface("/", "org.freedesktop.DBus.ObjectManager").GetManagedObjects(timeout=5)

    def _adapter(self):
        candidates = [(str(p), interfaces["org.bluez.Adapter1"]) for p, interfaces in self._objects().items() if "org.bluez.Adapter1" in interfaces]
        if not candidates:
            raise PrinterError("No Bluetooth adapter was found. Check that Bluetooth is enabled on the Pi.")
        return next((p for p, v in candidates if v.get("Powered")), candidates[0][0])

    def devices(self):
        def collect():
            found = []
            adapters = []
            for path, interfaces in self._objects().items():
                if "org.bluez.Adapter1" in interfaces:
                    p = interfaces["org.bluez.Adapter1"]
                    adapters.append({"name": str(p.get("Alias", "Bluetooth")), "powered": bool(p.get("Powered"))})
                if "org.bluez.Device1" in interfaces:
                    p = interfaces["org.bluez.Device1"]
                    found.append({"address": str(p["Address"]), "name": str(p.get("Alias", p.get("Name", "Unnamed device"))),
                                  "paired": bool(p.get("Paired")), "trusted": bool(p.get("Trusted")),
                                  "connected": bool(p.get("Connected")), "rssi": int(p.get("RSSI", -127))})
            return {"adapters": adapters, "devices": sorted(found, key=lambda d: (not d["paired"], d["name"].lower()))}
        return self.call(collect)

    def scan(self, cancel, update):
        def begin():
            path = self._adapter()
            self._interface(path, "org.freedesktop.DBus.Properties").Set("org.bluez.Adapter1", "Powered", self.dbus.Boolean(True), timeout=5)
            adapter = self._interface(path, "org.bluez.Adapter1")
            adapter.SetDiscoveryFilter({"Transport": "bredr"}, timeout=5)
            adapter.StartDiscovery(timeout=5)
            return path
        path = self.call(begin)
        try:
            update("Looking for nearby Bluetooth printers for 10 seconds…")
            if cancel.wait(10):
                raise Cancelled("Discovery cancelled.")
        finally:
            self.call(lambda: self._interface(path, "org.bluez.Adapter1").StopDiscovery(timeout=5))
        return self.devices()

    def _device(self, mac):
        for path, interfaces in self._objects().items():
            props = interfaces.get("org.bluez.Device1", {})
            if str(props.get("Address", "")).upper() == mac:
                return str(path), props
        raise PrinterError("This printer is not known to the Pi yet. Turn it on, scan, then select it.")

    def pair(self, mac, cancel, update):
        mac = address(mac)
        finished = threading.Event()
        outcome = []
        def start():
            path, props = self._device(mac)
            self.target = path
            self.display = None
            if props.get("Paired"):
                finished.set()
            else:
                self._interface(path, "org.bluez.Device1").Pair(
                    reply_handler=lambda: finished.set(),
                    error_handler=lambda exc: (outcome.append(exc), finished.set()), timeout=120)
            return path
        path = self.call(start)
        update("Pairing with the selected printer. Answer any pairing question below.")
        deadline = time.monotonic() + 120
        try:
            while not finished.wait(.15):
                if cancel.is_set() or time.monotonic() > deadline:
                    self.cancel_pairing()
                    raise Cancelled("Pairing cancelled or timed out. No PIN was saved.")
            if outcome:
                raise PrinterError("Pairing failed: " + str(outcome[0]))
            if cancel.is_set():
                raise Cancelled("Pairing cancelled.")
            # The button explicitly says Pair & remember, authorising this property.
            self.call(lambda: self._interface(path, "org.freedesktop.DBus.Properties").Set(
                "org.bluez.Device1", "Trusted", self.dbus.Boolean(True), timeout=5))
            return self.devices()
        finally:
            def clear():
                self.prompts.cancel()
                self.target = None
                self.display = None
            self.call(clear)

    def answer(self, identifier, value):
        return self.call(lambda: self.prompts.answer(identifier, value))

    def cancel_pairing(self):
        def cancel():
            self.prompts.cancel()
            if self.target:
                try:
                    self._interface(self.target, "org.bluez.Device1").CancelPairing(timeout=5)
                except Exception:
                    pass
        self.call(cancel)

    def close(self):
        if self.loop and self.GLib:
            self.GLib.idle_add(self.loop.quit)
            self.thread.join(timeout=2)


class RFCOMMLink:
    def __init__(self):
        self.lock = threading.RLock()
        self.sock = None
        self.mac = None
        self.channel = None
        self.received = bytearray()
        self.received_count = 0
        self.last_error = None

    def connect(self, mac, channel):
        mac, channel = address(mac), channel_number(channel)
        self.disconnect()
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise PrinterError("This Python installation has no Bluetooth socket support. Use Raspberry Pi OS.")
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        try:
            sock.settimeout(12)
            sock.connect((mac, channel))
            sock.setblocking(False)
        except OSError as exc:
            sock.close()
            raise PrinterError("Bluetooth connection failed: " + str(exc) + ". Close the phone app and any old rfcomm terminal, then try again.") from exc
        with self.lock:
            self.sock, self.mac, self.channel = sock, mac, channel
            self.received.clear()
            self.received_count = 0
            self.last_error = None
        threading.Thread(target=self._receive, args=(sock,), daemon=True).start()

    def _receive(self, sock):
        try:
            while True:
                with self.lock:
                    if self.sock is not sock:
                        return
                readable, _, _ = select.select([sock], [], [], .5)
                if not readable:
                    continue
                try:
                    data = sock.recv(4096)
                except BlockingIOError:
                    continue
                if not data:
                    raise OSError("The printer closed the connection.")
                with self.lock:
                    self.received_count += len(data)
                    self.received.extend(data)
                    if len(self.received) > 4096:
                        del self.received[:-4096]
        except (OSError, ValueError) as exc:
            with self.lock:
                if self.sock is sock:
                    self.last_error = str(exc)
                    self.sock = None
            sock.close()

    def state(self):
        with self.lock:
            return {"connected": self.sock is not None, "address": self.mac, "channel": self.channel,
                    "received_bytes": self.received_count, "last_error": self.last_error}

    def send(self, data, cancel, progress, chunk_size=256, pause_ms=25):
        with self.lock:
            sock = self.sock
        if sock is None:
            raise PrinterError("Connect to the printer before printing.")
        offset = 0
        deadline = time.monotonic() + 300
        try:
            while offset < len(data):
                if cancel.is_set():
                    raise Cancelled("Transfer cancelled. Data already buffered by the printer may still print.")
                if time.monotonic() > deadline:
                    raise PrinterError("Transfer timed out. A partial job may have reached the printer. It will not be retried automatically.")
                with self.lock:
                    if self.sock is not sock:
                        raise PrinterError("The printer disconnected during transfer. The job will not be retried automatically.")
                _, writable, _ = select.select([], [sock], [], .5)
                if not writable:
                    continue
                try:
                    count = sock.send(data[offset:offset+chunk_size])
                except BlockingIOError:
                    continue
                if count == 0:
                    raise PrinterError("The connection closed during transfer.")
                offset += count
                progress(offset, len(data))
                if cancel.wait(pause_ms / 1000):
                    raise Cancelled("Transfer cancelled. Buffered data may still print.")
            # Receive diagnostics for two seconds; their meaning is not guessed.
            if cancel.wait(2):
                raise Cancelled("Transfer stopped after sending. The printer may still finish its buffered page.")
            return {"sent": offset, "total": len(data), "received_bytes": self.state()["received_bytes"]}
        except (OSError, ValueError) as exc:
            self.disconnect()
            if cancel.is_set():
                raise Cancelled("Transfer cancelled. Data already buffered by the printer may still print.") from exc
            if isinstance(exc, PrinterError):
                raise
            raise PrinterError(f"Transfer stopped after {offset} bytes: {exc}. No automatic retry.") from exc

    def disconnect(self):
        with self.lock:
            sock, self.sock = self.sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def diagnostics(self):
        with self.lock:
            return {**self.state(), "reply_tail_hex": bytes(self.received).hex(),
                    "reply_interpretation": "Raw replies only. No F11 completion/status decoder is implemented."}
