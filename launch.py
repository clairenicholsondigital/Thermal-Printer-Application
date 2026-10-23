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
if Path(__file__).name == "thermal-prep-launch.py":
    ROOT = ROOT / "thermal-prep"
MANAGER = ROOT.parent / "thermal-prep-manager.py"
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
        if MANAGER.is_file():
            subprocess.run([sys.executable,str(MANAGER),"--recover",str(ROOT)],check=True)
        expected = json.loads((ROOT / "release.json").read_text())["version"]
        version = ready()
        if version and version != expected:
            message = "An older Thermal Prep is still running. Close the old app process or restart the Pi once to finish this initial upgrade."
            print(message,file=sys.stderr)
            try:
                subprocess.run(["notify-send","Thermal Prep",message],check=False)
            except FileNotFoundError:
                pass
            return 1
        if not ready():
            with (state / "app.log").open("w") as log:
                manager = MANAGER if MANAGER.is_file() else ROOT / "update_manager.py"
                process = subprocess.Popen([sys.executable, str(manager), "--supervise", str(ROOT)], cwd=ROOT.parent,
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
