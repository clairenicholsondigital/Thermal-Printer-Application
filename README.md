# Thermal Prep 1.3.2

A local PDF/image preparation and experimental Rongta F11 Bluetooth printing app for a Raspberry Pi 5 running **Raspberry Pi OS 64-bit with a desktop**. Upload a PDF, JPG or PNG, inspect the automatically prepared preview, connect the F11, then print one page. PNG and PDF downloads remain available.

**Only `scripts/test-print.sh` has been confirmed by the user to print on their Raspberry Pi and Rongta F11. The application itself has not yet been confirmed on hardware.** The app retains that script’s PDF rendering and delivery path: render the prepared PDF at 203 DPI, convert to monochrome, use the existing official F11 driver, open a fresh RFCOMM connection on channel 1 and send 256-byte chunks with 50 ms pauses. Disconnect after every attempt. Which difference caused the original application failure has not been isolated.

Version 1.2.1 separates print quality from Bluetooth delivery. Start with **density 8**, A4, driver mode 5 and the paper-feed setting matching your paper. Density 1 or 15 can be selected under **Print quality, paper & setup**. The working terminal script still uses density 15. Old unversioned preferences migrate once to the moderate-density baseline; newly saved density and paper choices survive restarts. The single-page CUPS check remains. “Data sent” is not a physical-print acknowledgement. Mid-page resume is not supported.

## Install once, then update in the app

This app targets **Raspberry Pi 5 with 64-bit Raspberry Pi OS and a desktop**. GitHub hosts the source and update packages. The Pi needs internet only when checking/downloading updates; no VPS, update server, GitHub account or token is required for this public repository.

### One-time setup or upgrade to version 1.3.1

Download `thermal-prep-pi64.zip` from the [latest GitHub release](https://github.com/clairenicholsondigital/Thermal-Printer-Application/releases/latest), extract it into a folder, open a terminal there and run:

```bash
bash install.sh
```

Run as your normal Pi desktop user, without `sudo` before the command. The installer requests sudo for system dependencies and Bluetooth setup. It keeps existing app-local F11 driver files; `--skip-driver` also preserves a previously installed system driver. `--driver-archive /path/to/archive.zip` explicitly replaces the driver using the checked vendor archive.

This installs the new update-capable launcher and the app under `~/.local/share`. Existing printer preferences and BlueZ pairing remain in place. If an older app is still running, close that process or restart the Pi **once** after this transition installation. Open **Thermal Prep** from the applications menu. Subsequent routine updates do not need a reboot or administrator password.

### Routine updates

1. Finish any print, conversion or pairing operation.
2. Open **App updates** and click **Check for updates**.
3. Read the release notes and choose **Update & restart app**.
4. Confirm that the current document can be closed. Save a prepared download first if wanted.
5. The app verifies the GitHub download, installs it separately, restarts itself and reloads the browser. Reopen the document you want to print.

Only numbered, published stable releases with the `thermal-prep-pi64.zip` asset are offered. Pushing code to `main` alone does not publish an app update. Updates never install automatically, and the updater never sends a print. **Restore previous version** switches back to the retained application version; saved preferences and printer pairing stay in place.

The lightweight launcher supervises the application process. A failed startup restores and restarts the previous version automatically. If power is lost during a folder switch, the stable applications-menu launcher performs recovery the next time you open the app. No permanent system service or VPS is required.

The updater verifies the SHA-256 digest supplied by GitHub and rejects unexpected archive files or incompatible system-dependency requirements. This verifies integrity against the trusted GitHub repository; it is not an independent publisher signature. Future releases requiring different system packages or a changed updater protocol must use a separate installer upgrade. Routine updates preserve the existing Rongta driver.

The app runs at `http://127.0.0.1:8877`. Keep it on the Pi; do not expose the development preview to a network. For manual launch, use:

```bash
/usr/bin/python3 "$HOME/.local/share/thermal-prep-launch.py"
```

Release recovery files are under `~/.local/share/.thermal-prep-updates`. The stable launcher and manager are `~/.local/share/thermal-prep-launch.py` and `~/.local/share/thermal-prep-manager.py`. Settings remain in `~/.config/thermal-prep/printer.json`.

### Publishing future updates (maintainer)

Update `release.json` and the displayed version, run the tests, commit and push the source, then build the update archive:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build-release.py
```

Create a GitHub release for that exact commit, with tag `v` followed by the version in `release.json`, and upload `dist/thermal-prep-pi64.zip`. Publish it as a stable release. The Pi reads GitHub’s asset digest and checks the manifest before installation. Keep `dependencies` and `updater_protocol` at 1 only while the release remains compatible with the installed runtime and bootstrap manager. Test new releases on the Pi as well as running automated checks.

## Everyday printing

1. Upload or drop a PDF, JPG or PNG (up to 25 MB; images up to 40 megapixels). For a multi-page PDF, select the page you want.
2. The app automatically fits the content to A4 at 203 DPI and prepares a monochrome preview. Both PDFs and images start with detail-preserving dithering, gentle sharpening, brightness/contrast 1.00 and coloured-line preservation off. JPEG orientation is respected and transparent PNG backgrounds become white paper.
3. If needed, open **Adjust the result** to choose text, diagram or photo processing, rotate, or change contrast. Changes update automatically. There is no universal best setting for every original: inspect pale lines and fine detail in the preview.
4. Turn on the F11, load an A4 thermal sheet and close other printer apps. Press **Connect F11**. The app finds the saved address and pairs/remembers it if necessary. Answer any pairing prompt using the printer’s supplied code.
5. Press **Print**. The app prepares the exported PDF using the working script path, opens a fresh connection and sends one page once. It disconnects when finished. Already-paired printers can also be connected automatically by pressing Print directly.
6. Check the physical page and choose **Printed correctly**. Transfer completion alone does not prove the page printed.

The default address is `E2:BF:FB:66:30:50`. **Print quality, paper & setup** contains scanning, the address, pairing, disconnect and the optional test page. **Save a prepared copy** contains the PNG/PDF downloads. Pairing is retained by the Pi. No print happens on upload or connection.

## Prepare and print your PDF

1. Choose or drop a PDF up to 25 MB and 100 pages. Select the page to prepare. Printing and downloads contain **that page only**.
2. Start with **Detailed diagram**, **A4**, **203 DPI**, **Automatic best fit** and **Remove white margins**. The initial preview is automatic.
3. Compare the original and prepared image. Use **1:1 pixels** or **2×** to inspect fine detail and scroll within the image. These are screen-pixel zoom modes, not physical-size measurements.
4. Open **Adjust the result** if needed; changes apply automatically. Printing and downloading the page are disabled while its settings are awaiting conversion. Blank previews are blocked from printing. The app uses A4 at the F11’s native 203 DPI.
5. With A4 paper loaded, choose **Print this page**. Review the filename, page thumbnail, orientation, paper and density, then confirm the loaded paper. The server atomically captures the cached preview and the printer settings you approved. A later upload or conversion cannot change that in-progress print.
6. Check the actual paper. A transfer completion is not a printer acknowledgement or a claim about print quality.

A4 uses the driver’s genuine `A4` option. Landscape artwork is rotated to pass through the portrait-width print head; it does not request a wider landscape sheet.

The browser displays the cached PNG produced by rendering the prepared PDF at 203 DPI and converting it to monochrome, matching the working script. Printing passes those exact cached bytes into the driver without another PDF render. The PNG download contains those final pixels; the PDF download is the single-page source used to prepare them. CUPS and the vendor filter can still resample artwork, so the preview cannot guarantee physical dot identity.

## Image controls

| Control | Effect |
| --- | --- |
| Detailed images & diagrams | Dithers shading into black and white dots; gently sharpens edges. |
| Text and line drawings | Uses a brightness cut-off for solid black/white. Pale lines may disappear. |
| Photos and shading | Dithers with gentle sharpening initially; turn sharpening off if edges look rough. |
| Detailed image · lighter shading | Same dithering, brightness 1.10; compare small labels in shaded parts. |
| Coloured wiring | Darkest-channel preservation plus threshold 170; avoid for shaded components. |
| Remove white margins | Automatically trims near-white background and fits the content within the border. Disable if pale edges are lost. |
| Automatic best fit | Chooses portrait or landscape to make the artwork larger. |
| Border | White space from 3 to 25 mm; default 8 mm. |
| Brightness / contrast | Adjusts tones before monochrome conversion. |
| Darken coloured lines | Uses the darkest colour channel; may also darken coloured fills. |

The app fits and enlarges artwork for readable reference. It is not a physically accurate PCB-transfer template. Black coverage and blank-output checks cannot establish that all labels remain legible.

## Print controls and cancellation

Driver options are checked against the F11 PPD. The quality baseline uses density 8, single-sheet media 101 and print mode 5. Density 1/8/15 and single-sheet/folded media 101/103 are selectable and saved when connecting, printing or pressing Save printer. Driver speed remains 16. Delivery stays at channel 1 and 256-byte writes separated by 50 ms pauses; this pacing is not a printhead-speed control.

Only one printer operation runs at once. Every action has an identifier to prevent the same request being sent twice. The app never automatically retries an interrupted print. If a browser request fails after a click, check the status and paper before clicking again.

**Cancel operation** stops preparation or closes the sending socket. Bytes already buffered by the printer may still print. Cancellation cannot retract them. After an interrupted job, let buffered printing finish, power-cycle the F11, load fresh paper and close other printer apps before explicitly printing the whole page again. The transfer budget scales with job size and pacing, with a separate 30-second no-progress timeout. Partial socket writes do not add extra pacing delays. Preparation stages retain bounded timeouts.

## If something fails

- **No Bluetooth adapter / service unavailable:** rerun the installer, enable Bluetooth in the Pi desktop and restart the Pi if needed. A radio-blocked adapter must be unblocked through the Pi's Bluetooth controls.
- **Pairing fails:** put the printer in pairing mode, scan again and use the printer's documented PIN if asked. A stale bond can be removed through the Pi desktop's Bluetooth settings before pairing again. The app does not remove existing pairings automatically.
- **Connect fails:** close the phone app and any old `/dev/rfcomm0` process. If an old binding remains from previous terminal tests, inspect it with `rfcomm -a` and release that specific binding with `sudo rfcomm release 0`. Only use `0` if it is the old F11 binding. This app connects directly and does not need a new `/dev/rfcomm0` binding.
- **Driver missing / failed:** rerun the installer, then refresh status or restart the app. Download diagnostics for the actual filter error. A driver failure prevents sending any bytes.
- **Blank test page:** check the paper's thermal side and whether the printer's own documented self-test prints visibly. Record the blank result and download diagnostics. A valid stream and open Bluetooth socket do not prove the printer accepts this Linux-driver protocol over Bluetooth. The earlier issue may require a supported cable route or further protocol investigation.
- **Distorted print:** check the A4 sheet, thermal side and feed. Save diagnostics.
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

It prints page 1 of `/home/nicho22/Downloads/thermal-prepared.pdf` to the configured example F11 address, using density 15 and 256-byte writes with 50 ms pauses. Edit those values in the script for another file or printer. The app retains the same pacing, defaults to density 8 for fine detail, and offers density 15 to reproduce the script’s darkness.

The helper records transfer progress and diagnostics in a temporary `f11-print-*` directory. After a disconnect it requires the exact response `RETRY` before starting the whole page again on fresh paper. It cannot resume at the last printed row, and socket byte counts are not printer acknowledgements. The user confirms this script worked; the individual cause of earlier application failures has not been isolated. Disconnect the browser app before using the helper.

## Research-based quality comparison

The user-supplied research recommends a starting recipe, not a hardware-proven optimum. The conversion order already matches its recommendation: crop/rotate, fit to final size, greyscale, brightness/contrast, gentle sharpening, then Floyd–Steinberg dithering or threshold and lossless PNG/PDF export. Keep coloured-line preservation off for shaded components. The script-compatible final PDF/CUPS stage can still resample the prepared artwork; the preview is not a guarantee of physical dot identity.

Compare the same artwork at the same size on the same paper:

| Comparison | Image preset | Density |
| --- | --- | --- |
| Baseline | Detailed images & diagrams; brightness 1.00 | 8 |
| Dark shaded components | Detailed image · lighter shading; brightness 1.10 | 8 |
| Faint print | Detailed images & diagrams; brightness 1.00 | 15 |
| Clean line drawing | Text and line drawings; threshold 170 | 8 |

Inspect small letters, gaps between pins and labels inside shaded components. Choose the most readable result, not simply the darkest. If a label is missing in the preview, fix the image preparation first. Use density 1 if 8 fills gaps. All comparisons require an explicit print; there is no automatic multi-sheet test.

Sources accompanying the supplied research: [Rongta F11 support](https://rongtaglobal.com/pages/f11-portable-printer-driver-manual) and [Pillow image conversion](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.convert). The script’s successful output is user-reported; quality on the actual paper still needs comparison.

## Development checks

```bash
python3 -m unittest discover -s tests -v
```

## Paper-saving safeguards (1.3.1)

After every transmission, the app blocks another print until you check the paper and confirm the next sheet is ready. Interrupted or uncertain transmissions require allowing buffered printing to finish, switching the F11 off and on, and loading a fresh sheet. This requirement survives reconnecting, refreshing and restarting the app. Failures before transmission do not require replacing paper. Cancel remains available during image preparation; no operation automatically retries a print.

Sparse/heavy coverage warnings must be acknowledged in the final print check. Choose **Inspect detail** to return to a 2× preview, or explicitly acknowledge the warnings to print. **Save paper: compare a detail on one sheet** makes three 60 mm crops of the selected page position at the same scale, comparing dithering at brightness 1.00 and 1.10 with threshold 170. It uses one printer density and never automatically starts printing. Return to the full page after choosing your settings.

Malformed preference files are preserved as `printer.json.invalid-*` alongside the settings file. The app starts with defaults and shows a warning to verify the address and paper. Print diagnostics read the current release version.

Updates now flush staged files, the recovery journal and folder changes to storage before proceeding. Existing installations using the older supervisor receive its replacement automatically when this app starts and must restart the Pi once to activate it; further in-app installs are blocked until then. Fresh installs use the new supervisor immediately. This reduces power-loss risk but is not protection against failing storage hardware. Routine updates still need only GitHub, with no VPS service.

## First Pi trial (1.3.2)

This is the recommended next hardware trial; no further feature work is required before trying it.

1. Install the latest release on the Pi, or use **Check for updates → Update & restart app**. Restart the Pi once if requested. Close the terminal printing script and other printer apps.
2. Turn on the F11, select **Loose A4 sheet**, density **8**, and use **Connect F11** to pair if needed.
3. Choose **Check test pattern without printing**. This runs the actual installed CUPS/F11 driver, validates a single-page job and opens/closes Bluetooth without sending print data. If it fails, download diagnostics and resolve that failure before using paper.
4. Only after checks pass, load thermal paper printing side upwards, choose **Print one A4 test sheet**, and confirm the review. The pattern uses one full sheet even though its marks are small. Record the physical result. A successful connection or transfer does not prove a successful print.
5. If the pattern is correct, load your image and try the one-sheet detail comparison at density 8. Choose **Check this page without printing**, inspect the preview, then explicitly print. Stop if the first sheet is blank or distorted; retain diagnostics rather than trying repeated sheets.

The check caches the exact validated printer bytes. Printing the same page with the same settings consumes that cached job; changed artwork/settings cannot reuse it. The check is optional for later routine prints, which still run all preparation and single-page validation before transmission. Checks cannot bypass an outstanding interrupted-print/paper recovery requirement. The reported duration is a minimum based on packet pacing, not a prediction of physical print completion.

A no-paper check catches driver execution/dependency and connection failures. It cannot measure thermal-side loading, feed alignment, print density quality or radio reliability during a full transfer. The existing working terminal script and its driver route are retained.
