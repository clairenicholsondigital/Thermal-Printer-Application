"""Local, deterministic PDF to monochrome artwork conversion."""
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import hashlib
import io
import threading
import json
import math
import os
import re
import subprocess
import warnings

from PIL import ImageFont, ImageDraw, Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

MAX_PAGES = 100
RENDER_SIDE = 4200
PAPER_MM = {"a4": (210, 297), "letter": (215.9, 279.4)}


class ConversionError(ValueError):
    pass


def inspect_upload(source, directory):
    """Detect content, validate it, and normalise photos before conversion."""
    with Path(source).open("rb") as stream:
        header = stream.read(1024)
    if b"%PDF-" in header:
        return inspect_pdf(source), "pdf"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if image.format not in ("JPEG", "PNG"):
                    raise ConversionError("Choose a PDF, JPG or PNG file.")
                if image.width * image.height > 40_000_000:
                    raise ConversionError("Choose an image under 40 megapixels.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ConversionError("Choose a still PNG image, not an animation.")
                image = ImageOps.exif_transpose(image)
                image.thumbnail((RENDER_SIDE, RENDER_SIDE), Image.Resampling.LANCZOS)
                # Transparent areas represent white paper, never black ink.
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                background.convert("RGB").save(Path(directory) / "source.png")
        return 1, "image"
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        if isinstance(exc, ConversionError):
            raise
        raise ConversionError("This image could not be read. Choose a valid JPG or PNG under 40 megapixels.") from exc


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


def comparison_page(original, settings, selection):
    """Three native-resolution crops, never resized after monochrome conversion."""
    if not isinstance(selection, dict) or set(selection) != {"x", "y"}:
        raise ConversionError("Choose a detail position on the page.")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100 for v in selection.values()):
        raise ConversionError("Detail positions must be between 0 and 100 percent.")
    base, source_meta = prepare_image(original, settings)
    side = round(60 / 25.4 * 203)
    left = round((base.width-side)*selection["x"]/100)
    top = round((base.height-side)*selection["y"]/100)
    sheet = Image.new("1", (1678,2374), 1)
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    draw.text((64,64), "Detail comparison: 60 mm crops, same scale, one density.", fill=0,font=font)
    variants = [("Dither / brightness 1.00", "dither",1.0),
                ("Dither / brightness 1.10", "dither",1.1),
                ("Threshold 170 / brightness 1.00", "threshold",1.0)]
    for i,(label,mode,brightness) in enumerate(variants):
        mono,_ = prepare_image(original, replace(settings,mode=mode,brightness=brightness,threshold=170))
        y = 150 + i*680
        draw.text((64,y),label,fill=0,font=font)
        sheet.paste(mono.crop((left,top,left+side,top+side)),(64,y+40))
    meta = dict(source_meta, width=1678,height=2374,paper_mm=[210,297],orientation="portrait",
                comparison={"selection":selection,"crop_pixels":[left,top,side,side],"size_mm":60},
                warnings=source_meta["warnings"])
    return sheet,meta


def export_result(original, settings, directory, comparison=None):
    if settings.dpi != 203 or settings.paper != "a4":
        raise ConversionError("Prepare A4 at 203 DPI for the Rongta F11.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    mono, metadata = comparison_page(original, settings, comparison) if comparison is not None else prepare_image(original, settings)
    mono.save(directory / "thermal.png", dpi=(settings.dpi, settings.dpi), optimize=True)
    # Flate compression of pixel data. No JPEG encoding and no second conversion.
    w, h = [mm / 25.4 * 72 for mm in metadata["paper_mm"]]
    pdf = canvas.Canvas(str(directory / "thermal.pdf"), pagesize=(w, h), pageCompression=1)
    pdf.setTitle("Thermal Prep - prepared artwork")
    pdf.setAuthor("Thermal Prep")
    pdf.drawImage(ImageReader(mono), 0, 0, width=w, height=h)
    pdf.showPage()
    pdf.save()
    # Match the successful Pi script BEFORE approval. Printing consumes these cached bytes.
    from printer_driver import render_prepared_pdf
    final_png, _ = render_prepared_pdf((directory / "thermal.pdf").read_bytes(), directory,
                                      threading.Event(), lambda message: None)
    (directory / "thermal.png").write_bytes(final_png)
    with Image.open(io.BytesIO(final_png)) as final:
        metadata.update(width=final.width, height=final.height,
                        black_pixels=final.convert("L").histogram()[0])
        metadata["coverage"] = round(100*metadata["black_pixels"]/(final.width*final.height),3)
    metadata["preview_stage"] = "driver-input"
    metadata["preview_sha256"] = hashlib.sha256(final_png).hexdigest()
    preview = original.copy()
    preview.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    preview.save(directory / "original.png")
    (directory / "settings.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata
