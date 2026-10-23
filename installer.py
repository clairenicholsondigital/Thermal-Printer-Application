#!/usr/bin/env python3
"""Install per user; only Debian dependencies require sudo."""
import os
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from driver_install import install_driver


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    parser = argparse.ArgumentParser(description="Install or update Thermal Prep and its F11 Bluetooth support")
    parser.add_argument("--driver-archive",help="Use an already downloaded official driver ZIP instead of downloading it")
    parser.add_argument("--skip-driver",action="store_true",help="Keep an existing F11 driver; install the app and Debian packages only")
    args = parser.parse_args()
    if os.geteuid() == 0:
        sys.exit("Run 'bash install.sh' as your normal desktop user, without sudo. It will request sudo for packages.")
    if not shutil.which("apt-get"):
        sys.exit("This installer is for Raspberry Pi OS / Debian. See README.md for manual setup.")
    print("Installing Thermal Prep for this desktop user. Internet is needed to install system packages.", flush=True)
    subprocess.run(["sudo","apt-get","update"],check=True)
    policy = subprocess.run(["apt-cache","policy","libcupsimage2t64"],capture_output=True,text=True,check=False).stdout
    image_library = "libcupsimage2t64" if any(line.strip().startswith("Candidate:") and "(none)" not in line for line in policy.splitlines()) else "libcupsimage2"
    subprocess.run(["sudo","apt-get","install","-y","python3","python3-pil","python3-reportlab",
                    "poppler-utils","xdg-utils","python3-dbus","python3-gi","bluez","cups","cups-filters",
                    "ghostscript",image_library],check=True)
    source = Path(__file__).resolve().parent
    target = Path.home() / ".local/share/thermal-prep"
    target.mkdir(parents=True, exist_ok=True)
    if target != source:
        for name in ("app.py", "converter.py", "launch.py", "README.md", "bluetooth_link.py", "printer_driver.py", "printer_service.py", "VALIDATION.md"):
            shutil.copy2(source / name, target / name)
        shutil.copytree(source / "static", target / "static", dirs_exist_ok=True)
    if not args.skip_driver:
        print("Installing the checked Rongta F11 driver for this user…",flush=True)
        try:
            install_driver(target,args.driver_archive)
        except Exception as exc:
            print(f"Driver setup could not finish: {exc}\nThe app will still install. Rerun the installer or use your existing F11 driver.")
    if Path("/run/systemd/system").exists():
        subprocess.run(["sudo","systemctl","enable","--now","bluetooth.service"],check=True)
    applications = Path.home() / ".local/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop = applications / "thermal-prep.desktop"
    desktop.write_text("[Desktop Entry]\nVersion=1.0\nType=Application\nName=Thermal Prep\n"
                       "Comment=Prepare and preview PDFs for a monochrome thermal printer\n"
                       f"Exec=/usr/bin/python3 {desktop_quote(target / 'launch.py')}\n"
                       "Icon=document-print-preview\nTerminal=false\nCategories=Utility;Graphics;\n")
    desktop.chmod(0o755)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(applications)], check=False)
    print("\nInstalled. Open Thermal Prep from your applications menu, or run:")
    print(f'/usr/bin/python3 "{target / "launch.py"}"')
    print("The app opens at http://127.0.0.1:8877 on this Pi. Start with Scan, Pair & remember, Connect, then Print test page.")
    print("If an older version is still running, restart the Pi once before opening the updated app.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        sys.exit("A package installation step failed. Check your connection and rerun bash install.sh.")
