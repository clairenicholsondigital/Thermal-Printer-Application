#!/usr/bin/env python3
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8877"


def ready():
    try:
        with build_opener(ProxyHandler({})).open(URL + "/health", timeout=1) as response:
            data = json.load(response)
            return data.get("version") if data.get("app") == "thermal-prep" else None
    except Exception:
        return False


def main():
    state = Path.home() / ".cache/thermal-prep"
    state.mkdir(parents=True, exist_ok=True)
    # Serialise double-clicks to avoid starting two app processes.
    with (state / "launcher.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        version = ready()
        if version and version != "1.1.1":
            message = "An older Thermal Prep is still running. Restart the Pi once to load the Bluetooth update."
            print(message,file=sys.stderr)
            try:
                subprocess.run(["notify-send","Thermal Prep",message],check=False)
            except FileNotFoundError:
                pass
            return 1
        if not ready():
            with (state / "app.log").open("w") as log:
                process = subprocess.Popen([sys.executable, str(ROOT / "app.py")], cwd=ROOT,
                                           stdout=log, stderr=log, start_new_session=True)
            for _ in range(50):
                if ready():
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            if not ready():
                message = f"Thermal Prep could not start. See {state / 'app.log'} for details."
                print(message, file=sys.stderr)
                try:
                    subprocess.run(["notify-send", "Thermal Prep", message], check=False)
                except FileNotFoundError:
                    pass
                return 1
    webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
