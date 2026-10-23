"""Use the official Rongta filter, with explicit A4 and F11 media settings."""
import hashlib
import io
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time

from PIL import Image, ImageDraw
from bluetooth_link import Cancelled, PrinterError

ROOT = Path(__file__).resolve().parent
MAX_JOB = 16 * 1024 * 1024
DRIVER_URL = "https://file.globalso.com/file_manage/4365/20260717/a4-portable-printer-driver-linux.zip"
DRIVER_SHA256 = "abb7cf47f218754ac509c83962ad453328cd52d6f950c00fdcab2148347c96ab"


def ppd_information(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise PrinterError("The F11 PPD is missing or invalid. Run the updated installer.")
    text = path.read_text(encoding="latin1")
    if not re.search(r'^\*ModelName:\s*"F11"', text, re.M):
        raise PrinterError("This driver definition is not for a Rongta F11.")
    if not re.search(r'^\*cupsFilter:\s*"application/vnd.cups-raster\s+\d+\s+rastertoRtPortable"', text, re.M):
        raise PrinterError("Unexpected F11 filter definition. Run the updated installer.")
    pages = {}
    for code, w, h in re.findall(r'^\*PaperDimension\s+(\w+)(?:/[^:]*)?:\s*"([\d.]+)\s+([\d.]+)"', text, re.M):
        pages[code] = [float(w), float(h)]
    for code, expected in {"A4": (595,842), "USA4": (612,792)}.items():
        if code not in pages or any(abs(a-b) > 1 for a,b in zip(pages[code],expected)):
            raise PrinterError("The F11 paper definitions differ from the checked driver. Printing is disabled to avoid incorrect sizing.")
    options = {}
    for name in ("PrintMode", "MediaTracking", "Darkness", "Resolution"):
        options[name] = re.findall(r'^\*' + name + r'\s+([\w]+)(?:/[^:]*)?\s*:', text, re.M)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": pages, "options": options}


def find_driver():
    ppds = [ROOT / "drivers/F11.ppd", Path('/usr/share/cups/model/RongtaLable/F11.ppd'),
            Path('/usr/share/ppd/rongta/F11.ppd')]
    source = Path.home() / "rongta-driver"
    if source.exists():
        ppds.extend(list(source.glob('**/F11.ppd'))[:20])
    ppd = next((p for p in ppds if p.is_file()), None)
    binaries = [ROOT / "drivers/rastertoRtPortable", Path('/usr/lib/cups/filter/rastertoRtPortable'),
                Path('/usr/libexec/cups/filter/rastertoRtPortable')]
    binary = next((p for p in binaries if p.is_file() and os.access(p, os.X_OK)), None)
    converter = shutil.which("cupsfilter")
    status = {"ready": False, "ppd": str(ppd) if ppd else None, "filter": str(binary) if binary else None,
              "cupsfilter": converter, "message": "Run the updated installer to add the F11 printing dependencies."}
    if not all((ppd, binary, converter)):
        return status
    try:
        info = ppd_information(ppd)
        status.update(ready=True, information=info, message="Official F11 driver found. Native resolution: 203 DPI.")
    except PrinterError as exc:
        status["message"] = str(exc)
    return status


def test_page():
    page = Image.new("L", (1678,2374),255)
    d = ImageDraw.Draw(page)
    # Small marks near the top of one full A4 sheet; no undocumented partial feeds.
    d.text((95,85), "THERMAL PREP / RONGTA F11 TEST / 203 DPI", fill=0, stroke_width=0)
    d.rectangle((95,130,235,270), fill=0)
    d.rectangle((270,130,410,270), outline=0, width=3)
    for n in range(1,5):
        d.line((460,140+n*25,1050,140+n*25),fill=0,width=n)
        d.text((1080,130+n*25),str(n)+" pixel line",fill=0)
    for x in range(600):
        d.line((95+x,330,95+x,430),fill=round(x/599*255))
    d.text((95,470),"Look for a black square, an outline, four lines and a grey ramp.",fill=0)
    mono = page.convert("1",dither=Image.Dither.FLOYDSTEINBERG)
    out=io.BytesIO();mono.save(out,format="PNG",dpi=(203,203))
    return out.getvalue(), {"dpi":203,"settings":{"paper":"a4"},"orientation":"portrait","black_pixels":mono.histogram()[0]}


def run_stage(args, output, log, cancel, env=None, stdin=None, timeout=90):
    with Path(output).open("wb") as out, Path(log).open("ab") as err:
        try:
            process = subprocess.Popen(args, stdout=out, stderr=err, stdin=stdin or subprocess.DEVNULL,
                                       env=env, start_new_session=True)
        except OSError as exc:
            raise PrinterError(f"Could not start {Path(args[0]).name}: {exc.strerror}. Rerun the installer on the Pi to repair the driver and dependencies. No data was sent.") from exc
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel.wait(.1):
                    raise Cancelled("Print preparation cancelled. No data was sent.")
                if time.monotonic() > deadline:
                    raise PrinterError("The printer driver took too long. No data was sent.")
                if Path(output).stat().st_size > MAX_JOB or Path(log).stat().st_size > 2*1024*1024:
                    raise PrinterError("The driver produced unusually large output. No data was sent.")
            if process.returncode:
                raise PrinterError(f"{Path(args[0]).name} failed (exit {process.returncode}). Download diagnostics for its error log; rerun the installer if dependencies are missing. No data was sent.")
            if Path(output).stat().st_size > MAX_JOB or Path(log).stat().st_size > 2*1024*1024:
                raise PrinterError("The driver produced unusually large output. No data was sent.")
            if cancel.is_set():
                raise Cancelled("Print preparation cancelled. No data was sent.")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def prepare_job(png_bytes, metadata, config, directory, cancel, update):
    if metadata.get("dpi") != 203:
        raise PrinterError("The F11 uses 203 DPI. Change the preview resolution to 203 DPI and update it before printing.")
    if not metadata.get("black_pixels"):
        raise PrinterError("This preview is blank. Adjust it before printing.")
    driver = find_driver()
    if not driver["ready"]:
        raise PrinterError(driver["message"])
    paper = metadata["settings"]["paper"]
    page_code = {"a4":"A4", "letter":"USA4"}.get(paper)
    if not page_code:
        raise PrinterError("Unsupported paper size.")
    options = {"PageSize":page_code,"Resolution":"203dpi","MediaTracking":str(config["media"]),
               "Darkness":str(config["density"]),"PrintMode":str(config["print_mode"]),"PrintSpeed":"16"}
    for key in ("MediaTracking","Darkness","PrintMode","Resolution"):
        if options[key] not in driver["information"]["options"][key]:
            raise PrinterError(f"The installed driver does not support the selected {key} option.")
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    input_file, raster, binary = (directory/n for n in ("feed.png","page.raster","job.bin"))
    with Image.open(io.BytesIO(png_bytes)) as image:
        if image.width > image.height:
            image = image.transpose(Image.Transpose.ROTATE_90)
        image.save(input_file,dpi=(203,203))
    options_string = " ".join(f"{key}={value}" for key,value in options.items())
    command = [driver["cupsfilter"],"-i","image/png","-p",driver["ppd"],"-m","application/vnd.cups-raster"]
    for key,value in options.items():
        command.extend(["-o",f"{key}={value}"])
    command.extend(["-o","print-scaling=fit","-o","orientation-requested=3",str(input_file)])
    log = directory/"driver.log"
    update("Preparing the page with the F11 driver. Nothing has been sent yet.")
    env = {**os.environ,"PPD":driver["ppd"],"LC_ALL":"C"}
    run_stage(command,raster,log,cancel,env=env)
    with raster.open('rb') as stream:
        if stream.read(4) not in (b'RaSt',b'tSaR',b'RaS2',b'2SaR',b'RaS3',b'3SaR'):
            raise PrinterError("CUPS did not produce a recognised raster file. No data was sent.")
    with raster.open("rb") as stream:
        run_stage([driver["filter"],"1","thermal-prep","Prepared page","1",options_string],binary,log,cancel,env=env,stdin=stream)
    # Refuse tiled output before any Bluetooth transfer.
    grids = re.findall(r"xpages = (\d+)x.*?ypages = (\d+)x", log.read_text(errors="replace"))
    if not grids or any(grid != ("1", "1") for grid in grids):
        raise PrinterError("CUPS did not confirm one page. Nothing was sent. Check driver.log.")
    data = binary.read_bytes()
    if not 20 <= len(data) <= MAX_JOB or not data.startswith(bytes.fromhex("a31e1c")):
        raise PrinterError("The driver output does not match the F11 packet header observed in our earlier tests. No data was sent.")
    diagnostics = {"driver":driver,"options":options,"bytes":len(data),
                   "preview_sha256":hashlib.sha256(png_bytes).hexdigest(),
                   "job_sha256":hashlib.sha256(data).hexdigest(),"header_hex":data[:64].hex(),
                   "landscape_rotated_for_feed":metadata.get("orientation")=="landscape"}
    return data, diagnostics


def render_prepared_pdf(pdf_bytes, directory, cancel, update):
    """Mirror scripts/test-print.sh: exported page -> 203 DPI PNG -> 1-bit."""
    directory = Path(directory)
    source = directory / "prepared.pdf"
    source.write_bytes(pdf_bytes)
    update("Rendering the prepared PDF at 203 DPI for the Rongta F11…")
    run_stage(["pdftoppm", "-f", "1", "-l", "1", "-singlefile",
               "-r", "203", "-png", str(source), str(directory / "page")],
              directory / "pdf-render.stdout", directory / "pdf-render.log",
              cancel, timeout=60)
    with Image.open(directory / "page.png") as image:
        mono = image.convert("1")
    output = directory / "prepared.png"
    mono.save(output, dpi=(203, 203))
    return output.read_bytes(), {
        "dpi": 203, "settings": {"paper": "a4"},
        "orientation": "landscape" if mono.width > mono.height else "portrait",
        "black_pixels": mono.histogram()[0],
    }
