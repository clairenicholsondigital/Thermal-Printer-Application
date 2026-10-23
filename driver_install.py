"""Fetch a checksum-pinned official archive; install only the two required files."""
import hashlib
import io
import json
from pathlib import Path
import platform
from urllib.request import urlopen
from zipfile import ZipFile

URL = "https://file.globalso.com/file_manage/4365/20260717/a4-portable-printer-driver-linux.zip"
SHA256 = "abb7cf47f218754ac509c83962ad453328cd52d6f950c00fdcab2148347c96ab"
PREFIX = "A4 Portable Printer Driver (Linux)/Rongta_Portable_Printer_Linux_Driver/"


def install_driver(target, archive=None):
    machine = platform.machine().lower()
    folder = {"aarch64":"aarch64","arm64":"aarch64","x86_64":"debian12_x64"}.get(machine)
    if folder is None:
        raise ValueError("The bundled installation route requires 64-bit Raspberry Pi OS (aarch64), or Debian x86_64.")
    if archive:
        path = Path(archive).expanduser()
        if path.stat().st_size > 16*1024*1024:
            raise ValueError("The driver archive is larger than the checked release.")
        data = path.read_bytes()
    else:
        with urlopen(URL,timeout=45) as response:
            data = response.read(16*1024*1024+1)
    if hashlib.sha256(data).hexdigest()!=SHA256:
        raise ValueError("The downloaded Rongta archive differs from the checked version. It has not been installed.")
    with ZipFile(io.BytesIO(data)) as bundle:
        ppd = bundle.read(PREFIX+"ppd/F11.ppd")
        binary = bundle.read(PREFIX+f"filter/{folder}/rastertoRtPortable")
    if not binary.startswith(b"\x7fELF") or b'*ModelName:             "F11"' not in ppd:
        raise ValueError("Unexpected contents in the Rongta driver archive.")
    target = Path(target)/"drivers"
    target.mkdir(parents=True,exist_ok=True)
    for name,content,mode in (("F11.ppd",ppd,0o644),("rastertoRtPortable",binary,0o755)):
        temporary = target/(name+".tmp")
        temporary.write_bytes(content)
        temporary.chmod(mode)
        temporary.replace(target/name)
    (target/"source.json").write_text(json.dumps({"url":URL,"archive_sha256":SHA256,"architecture":folder},indent=2)+"\n")
    return target
