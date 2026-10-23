/usr/bin/python3 - <<'PY'
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path.home() / ".local/share/thermal-prep"))

from PIL import Image
from printer_driver import prepare_job
from printer_service import DEFAULTS
from bluetooth_link import RFCOMMLink

source = Path("/home/nicho22/Downloads/thermal-prepared.pdf")
work = Path(tempfile.mkdtemp(prefix="f11-print-"))
cancel = threading.Event()
link = RFCOMMLink()
status_file = work / "transfer-status.json"
progress = {"sent": 0, "total": 0}
attempt = 0
last_report = 0.0


def save_status(state, error=None):
    record = {
        "state": state,
        "attempt": attempt,
        "bytes_accepted_by_socket": progress["sent"],
        "total_bytes": progress["total"],
        "note": "Byte counts do not confirm which rows physically printed.",
        "error": error,
    }
    temporary = status_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2))
    temporary.replace(status_file)


def report_progress(sent, total):
    global last_report
    progress.update(sent=sent, total=total)
    now = time.monotonic()
    if now - last_report >= 2 or sent == total:
        print(
            f"Transfer: {sent:,}/{total:,} bytes "
            f"({sent / total:.0%})",
            flush=True
        )
        save_status("sending")
        last_report = now


def ask_retry():
    # Python itself is reading this pasted script from stdin,
    # so read the user's response directly from the terminal.
    try:
        with open("/dev/tty", "r+") as terminal:
            terminal.write(
                "\nNo reliable mid-page resume is available.\n"
                "Before retrying:\n"
                "  1. Let the printer finish any buffered printing.\n"
                "  2. Power it off and on to clear the interrupted job.\n"
                "  3. Load a fresh sheet and close other printer apps.\n\n"
                "Type RETRY to print the WHOLE page again,\n"
                "or press Enter to stop: "
            )
            terminal.flush()
            return terminal.readline().strip() == "RETRY"
    except OSError:
        print("No interactive terminal available. Stopping.")
        return False


try:
    if not source.is_file():
        raise FileNotFoundError(f"PDF not found: {source}")

    print(f"Print files and logs: {work}", flush=True)
    print("Preparing the first PDF page...", flush=True)

    with (work / "pdf-render.log").open("wb") as log:
        subprocess.run(
            [
                "pdftoppm", "-f", "1", "-l", "1", "-singlefile",
                "-r", "203", "-png", str(source), str(work / "page")
            ],
            stdout=subprocess.DEVNULL,
            stderr=log,
            check=True,
            timeout=60
        )

    with Image.open(work / "page.png") as image:
        mono = image.convert("1")

    mono.save(work / "prepared.png", dpi=(203, 203))
    metadata = {
        "dpi": 203,
        "settings": {"paper": "a4"},
        "orientation": (
            "landscape" if mono.width > mono.height else "portrait"
        ),
        "black_pixels": mono.histogram()[0]
    }

    data, info = prepare_job(
        (work / "prepared.png").read_bytes(),
        metadata,
        {**DEFAULTS, "density": 15},
        work,
        cancel,
        lambda message: print(message, flush=True)
    )

    (work / "print-job.bin").write_bytes(data)
    progress["total"] = len(data)
    print(f"Driver prepared {len(data):,} bytes.", flush=True)

    while True:
        attempt += 1
        progress["sent"] = 0
        last_report = 0.0
        save_status("connecting")

        try:
            print(f"\nAttempt {attempt}: connecting to the F11...", flush=True)
            link.connect("E2:BF:FB:66:30:50", 1)

            print("Sending at density 15. Ctrl+C stops the job.", flush=True)
            result = link.send(
                data,
                cancel,
                report_progress,
                chunk_size=256,
                pause_ms=50
            )

            save_status("all_bytes_sent")
            print(f"\nDATA SENT: {result['sent']:,} bytes.", flush=True)
            print("Allow the printer to finish. Check the physical page.")
            break

        except KeyboardInterrupt:
            raise

        except Exception as error:
            link.disconnect()
            save_status("interrupted", str(error))
            print(f"\nTRANSFER INTERRUPTED: {error}", flush=True)
            print(
                f"Socket accepted {progress['sent']:,} of "
                f"{progress['total']:,} bytes. "
                "This is not a confirmed printed position.",
                flush=True
            )

            if not ask_retry():
                print("Stopped. No further data will be sent.")
                break

            link = RFCOMMLink()

        finally:
            link.disconnect()

except KeyboardInterrupt:
    cancel.set()
    save_status("cancelled")
    print("\nStopped. Buffered data may still print.")

except Exception as error:
    print(f"FAILED: {error}")

finally:
    link.disconnect()
    print(f"\nPrint files and error logs: {work}")
PY
