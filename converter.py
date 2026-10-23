"""Local, deterministic PDF to monochrome artwork conversion."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math
import os
import re
import subprocess

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

MAX_PAGES = 100
RENDER_SIDE = 4200
PAPER_MM = {"a4": (210, 297), "letter": (215.9, 279.4)}


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    page: int = 1
    dpi: int = 203
    paper: str = "a4"
    orientation: str = "auto"
    rotation: int = 0
    crop: bool = True
    margin: float = 8
    mode: str = "dither"
    threshold: int = 170
    brightness: float = 1
    contrast: float = 1
    coloured_lines: bool = False
    sharpen: bool = True

    @classmethod
    def parse(cls, data, pages):
        if not isinstance(data, dict):
            raise ConversionError("Settings must be an object.")
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise ConversionError("Unknown conversion setting.")
        values = asdict(cls()) | data
        for key in ("page", "dpi", "rotation", "threshold"):
            if type(values[key]) is not int:
                raise ConversionError(f"Invalid {key}.")
        for key in ("margin", "brightness", "contrast"):
            v = values[key]
            if type(v) not in (int, float) or not math.isfinite(v):
                raise ConversionError(f"Invalid {key}.")
        for key in ("crop", "coloured_lines", "sharpen"):
            if type(values[key]) is not bool:
                raise ConversionError(f"Invalid {key}.")
        for key in ("paper", "orientation", "mode"):
            if type(values[key]) is not str:
                raise ConversionError(f"Invalid {key}.")
        enums = {"dpi": (203, 300), "paper": PAPER_MM,
                 "orientation": ("auto", "portrait", "landscape"),
                 "rotation": (0, 90, 180, 270), "mode": ("dither", "threshold")}
        for key, options in enums.items():
            if values[key] not in options:
                raise ConversionError(f"Invalid {key}.")
        ranges = {"page": (1, pages), "threshold": (1, 254),
                  "margin": (3, 25), "brightness": (0.6, 1.6), "contrast": (0.6, 1.8)}
        for key, (low, high) in ranges.items():
            if not low <= values[key] <= high:
                raise ConversionError(f"{key.capitalize()} must be between {low} and {high}.")
        return cls(**values)


def run_poppler(args, timeout):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"})
    except FileNotFoundError as exc:
        raise ConversionError("Poppler is missing. Run the installer to add poppler-utils.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError("This PDF took too long to process. Try a smaller or simpler PDF.") from exc
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace")
        if "password" in error.lower() or "encrypted" in error.lower():
            raise ConversionError("Open this PDF in your usual viewer and save an unlocked copy first.")
        raise ConversionError("The PDF could not be read. Try exporting a fresh PDF from the original application.")
    return result.stdout


def inspect_pdf(path):
    with Path(path).open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise ConversionError("Please choose a PDF file.")
    info = run_poppler(["pdfinfo", str(path)], 15).decode("utf-8", errors="replace")
    match = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if not match or not 1 <= int(match[1]) <= MAX_PAGES:
        raise ConversionError(f"Choose a PDF containing 1 to {MAX_PAGES} pages.")
    if re.search(r"^Encrypted:\s+yes", info, re.M):
        raise ConversionError("Please save an unlocked copy of this PDF first.")
    return int(match[1])


def render_page(source, page, directory):
    """Cache only the last rendered page, bounding disk use for long documents."""
    directory = Path(directory)
    cache = directory / "rendered-page.txt"
    image_path = directory / "rendered.png"
    if not (cache.exists() and cache.read_text() == str(page) and image_path.exists()):
        cache.unlink(missing_ok=True)
        run_poppler(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile",
                     "-scale-to", str(RENDER_SIDE), "-png", str(source),
                     str(directory / "rendered")], 60)
        cache.write_text(str(page))
    with Image.open(image_path) as im:
        return im.convert("RGB")


def prepare_image(original, settings):
    image = original.convert("RGB")
    warnings = []
    # Minimum channel retains bright saturated colours during white-margin detection.
    r, g, b = image.split()
    minimum = ImageChops.darker(ImageChops.darker(r, g), b)
    bounds = minimum.point(lambda p: 255 if p < 245 else 0).getbbox()
    if bounds is None:
        warnings.append("The source page appears blank or extremely faint. Check the original before printing.")
    if settings.crop and bounds:
        pad = max(2, round(max(image.size) * 0.004))
        l, t, r, b = bounds
        image = image.crop((max(0, l-pad), max(0, t-pad),
                            min(image.width, r+pad), min(image.height, b+pad)))
    if settings.rotation:
        image = image.rotate(-settings.rotation, expand=True)

    paper_w, paper_h = PAPER_MM[settings.paper]
    orientation = settings.orientation
    if orientation == "auto":
        m = settings.margin * 2
        portrait = min((paper_w-m)/image.width, (paper_h-m)/image.height)
        landscape = min((paper_h-m)/image.width, (paper_w-m)/image.height)
        orientation = "landscape" if landscape > portrait else "portrait"
    if orientation == "landscape":
        paper_w, paper_h = paper_h, paper_w
    width = round(paper_w / 25.4 * settings.dpi)
    height = round(paper_h / 25.4 * settings.dpi)
    margin = round(settings.margin / 25.4 * settings.dpi)
    fit = min((width-2*margin)/image.width, (height-2*margin)/image.height)
    size = (max(1, round(image.width*fit)), max(1, round(image.height*fit)))
    image = image.resize(size, Image.Resampling.LANCZOS)
    if settings.coloured_lines:
        r, g, b = image.split()
        grey = ImageChops.darker(ImageChops.darker(r, g), b)
    else:
        grey = ImageOps.grayscale(image)
    grey = ImageEnhance.Brightness(grey).enhance(settings.brightness)
    grey = ImageEnhance.Contrast(grey).enhance(settings.contrast)
    if settings.sharpen:
        grey = grey.filter(ImageFilter.UnsharpMask(radius=0.7, percent=90, threshold=3))
    page = Image.new("L", (width, height), 255)
    page.paste(grey, ((width-grey.width)//2, (height-grey.height)//2))
    if settings.mode == "dither":
        mono = page.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    else:
        mono = page.point(lambda p: 255 if p >= settings.threshold else 0, mode="1")
    hist = mono.convert("L").histogram()
    black = hist[0]
    coverage = 100 * black / (width * height)
    if not black:
        warnings.append("The output has no black pixels. Increase darkness or the cut-off, or select detail-preserving dots.")
    elif coverage < 0.05:
        warnings.append("Very little of this page will print. Zoom in and check that the details are still present.")
    if coverage > 45:
        warnings.append("This page has heavy black coverage. Check that shaded components have not merged together.")
    return mono, {"width": width, "height": height, "dpi": settings.dpi,
                  "paper_mm": [paper_w, paper_h], "orientation": orientation,
                  "black_pixels": black, "coverage": round(coverage, 3),
                  "warnings": warnings, "settings": asdict(settings)}


def export_result(original, settings, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    mono, metadata = prepare_image(original, settings)
    mono.save(directory / "thermal.png", dpi=(settings.dpi, settings.dpi), optimize=True)
    # Flate compression of pixel data. No JPEG encoding and no second conversion.
    w, h = [mm / 25.4 * 72 for mm in metadata["paper_mm"]]
    pdf = canvas.Canvas(str(directory / "thermal.pdf"), pagesize=(w, h), pageCompression=1)
    pdf.setTitle("Thermal Prep - prepared artwork")
    pdf.setAuthor("Thermal Prep")
    pdf.drawImage(ImageReader(mono), 0, 0, width=w, height=h)
    pdf.showPage()
    pdf.save()
    preview = original.copy()
    preview.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    preview.save(directory / "original.png")
    (directory / "settings.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata
