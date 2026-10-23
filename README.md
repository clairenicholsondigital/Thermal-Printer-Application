# Thermal Prep 1.1.1

A local PDF preparation and experimental Rongta F11 Bluetooth printing app for a Raspberry Pi 5 running **Raspberry Pi OS 64-bit with a desktop**. Upload a PDF, adjust and inspect a monochrome preview, pair/connect the printer, then explicitly print one page. PNG and PDF downloads remain available.

**Visible Bluetooth printing has been reported on the user's Pi 5 and F11.** Removing the explicit CUPS `ppi=203` option fixed a four-tile output problem, and the user described the resulting page as basically perfect. This version includes that fix and refuses output unless CUPS confirms a single-page grid. A later transfer at density 15 disconnected partway through; the cause is unconfirmed. “Data sent” is not a physical-print acknowledgement. Mid-page resume is not supported.

## Install or update

Clone this repository (or extract its ZIP), open a terminal in the folder containing `install.sh`, and run:

```bash
bash install.sh
```

Run it as your **normal desktop user**, without putting `sudo` before the command. It requests sudo for Debian packages and enabling the Bluetooth service. Internet is required during installation. Allow time for the packages to download.

The installer:

- Installs Python, Pillow, ReportLab, Poppler, BlueZ, Python D-Bus/GLib, CUPS filters and the CUPS image library. It handles the `libcupsimage2` / `libcupsimage2t64` package-name difference.
- Copies the app to `~/.local/share/thermal-prep` and adds a **Thermal Prep** applications-menu entry.
- Downloads the checked official Rongta driver archive, verifies its SHA-256, and installs only its F11 PPD and ARM64 filter in the app's `drivers` folder. Vendor binaries are not included in this source ZIP.
- Keeps the app running as your desktop user. It does not create a CUPS printer queue or send a print during installation.

**If an older version is already running, restart the Pi once after installation.** Closing its browser tab alone does not stop the old server. The launcher detects an old running version and asks you to restart.

Open **Thermal Prep** from your applications menu, or run:

```bash
/usr/bin/python3 "$HOME/.local/share/thermal-prep/launch.py"
```

Use the browser on the Pi at <http://127.0.0.1:8877>. The installed app listens on loopback only. Conversion and Bluetooth printing work without internet once dependencies are installed. No account or subscription is needed.

If the automatic driver download fails, the app still installs and shows which printing dependencies are missing. Rerun the installer, or supply the same checked official archive:

```bash
bash install.sh --driver-archive "/path/to/a4-portable-printer-driver-linux.zip"
```

`--skip-driver` keeps an existing driver installation and skips the vendor download; Debian package installation still runs. A changed archive is rejected rather than silently executing a different binary. The checked download URL and fingerprint are in `driver_install.py`.

## Pair, connect and run the first test

1. Turn on the F11 and load A4 thermal paper with its printable side facing the print head. Close the Rongta phone app and any previous terminal connection using the printer.
2. In **Your Bluetooth printer**, choose **Scan for printers**, then select the F11. The address from the earlier tests, `E2:BF:FB:66:30:50`, and RFCOMM channel `1` are prefilled. Change these if the device differs.
3. Choose **Pair & remember**. Answer any PIN, passkey or confirmation prompt displayed in the app. Use the PIN supplied for the printer. The app does not guess or save PINs. BlueZ retains the pairing and trust after this explicit action.
4. Choose **Connect**. The connected badge means an RFCOMM serial socket is open. Pairing by itself does not mean this socket is open.
5. Select **Single sheet** or **Folded paper** to match what is loaded. Start with **Moderate** density and the default driver/transfer settings.
6. Choose **Print test page** once. It uses **one full A4 sheet**, with a black square, outline, four line thicknesses and a grey ramp near the top. **View test pattern** shows its source image before printing.
7. After sending, record **Visible print**, **Blank sheet**, **No response** or **Distorted print**. If it is blank, keep the result and download diagnostics before changing several settings at once.

If the printer is already paired, **Pair & remember** retains that pairing and marks the selected device trusted. **Disconnect** closes this app's socket and keeps the pairing. **Refresh status** updates remembered devices and driver detection without sending a print. Scanning lasts about ten seconds and enables the selected Bluetooth adapter if it is off.

The connection buttons use Linux BlueZ, not browser Web Bluetooth. The web page does not need a browser Bluetooth permission prompt. Only the printer explicitly selected for pairing may ask this app for a PIN or confirmation.

## Prepare and print your PDF

1. Choose or drop a PDF up to 25 MB and 100 pages. Select the page to prepare. Printing and downloads contain **that page only**.
2. Start with **Detailed diagram**, **A4**, **203 DPI**, **Automatic best fit** and **Remove white margins**. The initial preview is automatic.
3. Compare the original and prepared image. Use **1:1 pixels** or **2×** to inspect fine detail and scroll within the image. These are screen-pixel zoom modes, not physical-size measurements.
4. Adjust settings if needed, then choose **Update preview**. Printing and downloading the page are disabled while its settings are awaiting conversion. Blank previews are blocked from printing. The F11 print path requires 203 DPI; 300 DPI remains available for exported artwork.
5. With the printer connected and matching paper loaded, choose **Print this page**. The server snapshots the current prepared PNG before starting. A later upload or conversion cannot change that in-progress print.
6. Check the actual paper. A transfer completion is not a printer acknowledgement or a claim about print quality.

A4 uses the driver's genuine `A4` option. The misleading `USA4` option is **US Letter** (`612 × 792` points). Landscape artwork is rotated to pass through the portrait-width print head; it does not request a wider landscape sheet. Use Letter only with matching paper supported by your printer.

The browser displays the exact exported PNG; the downloadable PDF embeds those same monochrome pixels losslessly. The printer path then passes that PNG through CUPS and the vendor filter. Those stages may resample or process the pixels. The checked CUPS build rounded A4's native raster to 1677 × 2373, while the exported PNG is 1678 × 2374. The screen preview is therefore an artwork preview, not a device-raster or physical print guarantee.

## Image controls

| Control | Effect |
| --- | --- |
| Detailed diagram | Dithers shading into black and white dots; gently sharpens edges. |
| Text and line drawings | Uses a brightness cut-off for solid black/white. Pale lines may disappear. |
| Photos and shading | Dithers without sharpening. |
| Remove white margins | Automatically trims near-white background and fits the content within the border. Disable if pale edges are lost. |
| Automatic best fit | Chooses portrait or landscape to make the artwork larger. |
| Border | White space from 3 to 25 mm; default 8 mm. |
| Brightness / contrast | Adjusts tones before monochrome conversion. |
| Darken coloured lines | Uses the darkest colour channel; may also darken coloured fills. |

The app fits and enlarges artwork for readable reference. It is not a physically accurate PCB-transfer template. Black coverage and blank-output checks cannot establish that all labels remain legible.

## Print controls and cancellation

Driver options are checked against the F11 PPD. Density maps to Light/Moderate/Dark (`1/8/15`), paper feed to Single/Folded (`101/103`), and image processing to Default/Mixed/Text (`5/9/10`). The default transfer writes 256 bytes followed by a 25 ms pause. Advanced controls allow small pacing changes for investigation.

Only one printer operation runs at once. Every action has an identifier to prevent the same request being sent twice. The app never automatically retries or reconnects a print. If a browser request fails after a click, check the status and paper before clicking again.

**Cancel operation** stops preparation or closes the sending socket. Bytes already buffered by the printer may still print. Cancellation cannot retract them. After an interrupted job, let the printer settle, check its state and reconnect explicitly before any new print. A single job has a five-minute transfer limit; each preparation stage has a 90-second limit.

## If something fails

- **No Bluetooth adapter / service unavailable:** rerun the installer, enable Bluetooth in the Pi desktop and restart the Pi if needed. A radio-blocked adapter must be unblocked through the Pi's Bluetooth controls.
- **Pairing fails:** put the printer in pairing mode, scan again and use the printer's documented PIN if asked. A stale bond can be removed through the Pi desktop's Bluetooth settings before pairing again. The app does not remove existing pairings automatically.
- **Connect fails:** close the phone app and any old `/dev/rfcomm0` process. If an old binding remains from previous terminal tests, inspect it with `rfcomm -a` and release that specific binding with `sudo rfcomm release 0`. Only use `0` if it is the old F11 binding. This app connects directly and does not need a new `/dev/rfcomm0` binding.
- **Driver missing / failed:** rerun the installer, then refresh status or restart the app. Download diagnostics for the actual filter error. A driver failure prevents sending any bytes.
- **Blank test page:** check the paper's thermal side and whether the printer's own documented self-test prints visibly. Record the blank result and download diagnostics. A valid stream and open Bluetooth socket do not prove the printer accepts this Linux-driver protocol over Bluetooth. The earlier issue may require a supported cable route or further protocol investigation.
- **Distorted print:** check A4 versus Letter, paper feed and driver settings. Avoid changing density, processing and pacing all at once.
- **App will not start:** inspect `~/.cache/thermal-prep/app.log`, or start `app.py` in a terminal. If port 8877 is occupied, `app.py --port 8878` uses <http://127.0.0.1:8878>; the menu launcher always uses 8877.
- **Invalid/encrypted PDF:** save an unlocked new PDF from its original application. Complex PDFs have a 60-second rendering limit.

## Local files and diagnostics

One uploaded document and two recent output versions are kept in a private temporary directory. A new successful upload replaces the old document. The × control clears the uploaded document and its conversion previews. The most recent print's prepared image, raster and command bytes remain in the app's private temporary folder until the next print or normal shutdown, so diagnostics can be investigated.

Closing a browser tab does not stop the background app. A normal foreground shutdown with Ctrl+C removes the temporary files. An abrupt power loss or forced kill may leave a `thermal-prep-*` temporary directory. Downloaded PNGs, PDFs and diagnostic reports remain wherever you save them.

Printer preferences persist in `~/.config/thermal-prep/printer.json`; BlueZ stores its own pairing separately. The app's preferences do not contain a PIN. Diagnostics include the printer address, driver paths/options, hashes, transfer counts, a filter-log tail and bounded raw reply bytes. They exclude the source PDF and full page/command files. Raw replies are not decoded as success/failure acknowledgements. Review the diagnostic file before sharing it.

There are no remote fonts, analytics, accounts or automatic printing. Use one browser tab at a time because tabs share the active document and printer. Keep Raspberry Pi OS updated. Do not expose the development `--preview` option to a network.

## Development and validation

The runtime uses Python's standard HTTP server, Pillow, Poppler, ReportLab, BlueZ D-Bus and a native Linux RFCOMM socket. It needs no pip environment. Conversions and print operations are serialised independently, and source rendering is bounded to 4200 pixels on the long side.

To run the tests on a system with the runtime dependencies:

```bash
sudo apt-get install python3-pypdf
python3 -m unittest discover -s tests -v
```

See `VALIDATION.md` for the checks performed and hardware limitations. This package contains source, tests and installation instructions. It is not a Raspberry Pi disk image.

Primary references:

- [Rongta F11 driver/manual page](https://rongtaglobal.com/pages/f11-portable-printer-driver-manual)
- [Rongta driver downloads](https://www.rongtatech.com/driver-download/2/)
- [BlueZ Agent API](https://bluez.readthedocs.io/en/latest/agent-api/), [Device API](https://bluez.readthedocs.io/en/latest/device-api/), [Adapter API](https://bluez.readthedocs.io/en/latest/adapter-api/)
- [CUPS cupsfilter](https://www.cups.org/doc/man-cupsfilter.html)
- [Python Bluetooth sockets](https://docs.python.org/3.11/library/socket.html)

## Remove the app

Stop its running `app.py` process, then remove these app-owned locations if no longer needed: `~/.local/share/thermal-prep`, `~/.local/share/applications/thermal-prep.desktop`, `~/.cache/thermal-prep` and `~/.config/thermal-prep`. Shared system packages can remain installed. Remove the F11 pairing separately in the Pi's Bluetooth settings only if you want to forget it.


## Terminal printing and recovery

After installation, run the existing terminal helper from the repository root:

```bash
bash scripts/test-print.sh
```

It prints page 1 of `/home/nicho22/Downloads/thermal-prepared.pdf` to the configured example F11 address, using density 15 and 256-byte writes with 50 ms pauses. Edit those values in the script for another file or printer. The app itself retains moderate density 8 and 25 ms pacing by default.

The helper records transfer progress and diagnostics in a temporary `f11-print-*` directory. After a disconnect it requires the exact response `RETRY` before starting the whole page again on fresh paper. It cannot resume at the last printed row, and socket byte counts are not printer acknowledgements. Slower pacing is an investigative setting, not a proven disconnect fix. Disconnect the browser app before using the helper.

## Development checks

```bash
python3 -m unittest discover -s tests -v
```
