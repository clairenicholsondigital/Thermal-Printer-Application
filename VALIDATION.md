# Version 1.3.1 safety review fixes

58 automated tests passed. New regression checks cover cancellation during conversion without bypassing authentication; atomic print settings and mandatory preflight/quality acknowledgments; blocking repeated prints after a successful transfer; recovery guards surviving reconnects and restarts; distinguishing preparation failures from uncertain transmissions; corrupt settings preservation; native-scale comparison crops; transfers longer than five minutes with partial socket writes; stalled transfers; durable journal writes; and migration of the older updater supervisor. The final cached preview is checked against real Poppler output and passed unchanged into preparation.

A Chromium walkthrough passed upload, automatic conversion, adjustment, one-sheet comparison generation, mandatory paper confirmation, one explicit simulated print, blocking the next print until fresh-paper confirmation, and desktop/mobile layouts without JavaScript errors. The real-process updater check passed startup, install, app restart, deliberate startup failure and automatic rollback/restart. JavaScript syntax and whitespace checks passed.

Bluetooth and driver output were simulated. No physical printer was contacted. Raspberry Pi/F11 radio reliability, loose-sheet feeding and actual print quality still need hardware validation. CUPS/vendor processing can still resample the cached driver-input image. Disk synchronization reduces update interruption risk but cannot guarantee recovery from storage failure. Existing installations with the old supervisor need the one-time Pi restart shown by the app after upgrading.

# Version 1.3.0 update validation

48 automated tests passed, including checksum rejection, unexpected/path-traversal archive entries, incompatible dependencies, preserved driver files, successful update/manual restore, interrupted folder-switch recovery, failed-start rollback, protected update endpoints and blocking updates during printer/conversion operations. Existing conversion and printing tests also passed.

A separate real-process integration check ran temporary copies of the actual application: initial startup, switching to a newer version, app-only restart, a deliberately broken next version, and automatic restoration/restart of the preceding version all passed. These checks used the VPS, not Raspberry Pi hardware. No real printer was contacted. The browser walkthrough also passed update checking/release-note rendering, disabled installation outside a managed Pi installation, and the existing upload-to-print flow without JavaScript errors.

The application is enabled for routine installation only under the installed per-user launcher on ARM64. GitHub release retrieval and download verification are checked separately when publishing this release. The initial installer still requires a one-time run on the Pi; no update can retrofit itself into an older app without that step.

# Version 1.2.1 validation

All 36 automated tests passed. Added regression coverage for upgrading the former fixed-density settings once, persisting explicit density/media choices across restarts, and retaining 256-byte/50 ms delivery while rejecting unsupported settings.

The Chromium flow passed with the new defaults: detailed-image dithering and sharpening, density 8, lighter-shading preset at brightness 1.10, automatic text-preset conversion, selection and saving of density 15/folded paper, connection without printing, exactly one explicit print and physical-result confirmation. Bluetooth and the vendor driver output were simulated; PDF/image preparation was real. No JavaScript errors or mobile horizontal overflow were observed. Syntax, compilation and whitespace checks passed.

Research-aligned defaults are not a claim of optimal or verified physical F11 output. The terminal script is unchanged; compare actual paper at density 8 and 15 on the Pi. The final PDF/CUPS path still may resample prepared artwork.

# Version 1.2.0 validation

All 35 automated tests passed on the VPS. New tests cover JPEG/PNG upload through PDF export, EXIF orientation, PNG transparency on white paper, rejection of disguised GIF/BMP files, and discovery/pairing/connection without sending a print. Existing script-parity, transfer, cancellation and request validation tests also pass. Physical Raspberry Pi/F11 validation remains outstanding.

A Chromium walkthrough with simulated Bluetooth/driver output passed PNG upload, automatic conversion, automatic preset changes, Connect F11 without printing, one explicit print and user result confirmation. No JavaScript errors occurred; a 390-pixel mobile viewport had no horizontal overflow. The real PDF rendering and image conversion ran during this walkthrough. JavaScript syntax, Python compilation and whitespace checks also passed.

# Version 1.1.2 validation

The user clarified on 23 October 2026 that only the terminal script printed successfully on the Raspberry Pi/F11. The application has not been hardware-validated. Version 1.1.2 reproduces its PDF rendering, density and pacing, and opens/closes a connection per print.

All 32 automated tests passed on the VPS using an isolated Python environment with Pillow, ReportLab and pypdf, with local sockets enabled for HTTP and transport tests. JavaScript syntax checks, Python compilation and diff whitespace checks also passed.

New checks verify pixel-for-pixel equivalence with the script’s real Poppler rendering and monochrome conversion, migration from old settings, preparation before connection, fresh connection and disconnect for each print, fixed 256-byte/50 ms transfer arguments, and no send or retry after connection failure. HTTP tests verify the exported PDF is included in the immutable print snapshot. Existing cancellation, partial-write, duplicate-request and single-page driver checks remain covered.

The current vendor driver invocation was tested with mocks; the vendor binary, browser interaction and physical Pi/F11 printing were not tested in this update. Prior measurements below are historical and were not rerun for this change.

## Historical version 1.1.1 checks

Checked in a Linux x86_64 development environment on 23 October 2026. This is not a physical Raspberry Pi or printer test.

## Automated checks

All **28 automated tests passed** with `python3 -m unittest discover -s tests -v`.

- Real Poppler rendering and PDF extraction verify page selection, rejection of invalid/encrypted inputs, colour handling, crop/rotation, A4/Letter dimensions, and 203/300 DPI output.
- The downloadable PDF's embedded image is pixel-identical to the monochrome PNG. Blank/faint-output checks pass.
- HTTP tests cover uploads/downloads, session and request-token protection, stale document/result rejection, cleanup, and taking an immutable snapshot of the latest preview before a print.
- Pairing-prompt tests cover PIN/passkey validation, rejection, stale answers and clearing the PIN from pending state. They do not exercise an actual BlueZ pairing exchange.
- Socket-pair tests transmit every possible byte, including partial writes, and verify exact received bytes. Cancellation closes the socket without retransmission. Remote disconnect and bounded incoming-reply storage are checked. These exercise the stream transport logic, not a Bluetooth radio.
- Service tests use a simulated transport to verify duplicate-request suppression, one operation at a time, no send after driver failure, explicit result reporting, cancellation, disconnected-print rejection and settings persistence.
- The installer was dry-run with package operations and vendor installation mocked. New app modules, Bluetooth dependencies, desktop-entry quoting and copied interface files are checked. Wrong-architecture and altered-archive rejection are checked.
- Python compilation, both JavaScript syntax checks and shell syntax checks passed.

## Actual vendor filter checks

The official Rongta archive was downloaded from its published HTTPS URL and verified against SHA-256:

`abb7cf47f218754ac509c83962ad453328cd52d6f950c00fdcab2148347c96ab`

Its F11 PPD and `debian12_x64` filter were installed into an isolated test folder using `driver_install.py`. CUPS 2.4.7 and cups-filters 2.0 packages were extracted into an isolated test environment. A wrapper supplied their local library/configuration paths; no printer queue or print device was used. The production installer uses normal distribution package locations.

The app's real `prepare_job` pipeline produced these streams, all with the previously observed F11 `a3 1e 1c` prefix:

| Input | F11 output bytes | Check |
| --- | ---: | --- |
| A4 test pattern, single sheet | 135,412 | Nonempty official-driver stream |
| Same pattern, folded paper | 135,412 | Different command hash from single-sheet mode |
| Same pattern, mixed image/text | 135,412 | Driver accepts the option; identical stream for this monochrome pattern |
| Supplied Bench PSU schematic | 156,738 | Landscape source rotates for portrait feed; genuine A4 option |
| Blank control, deliberately bypassing the app's blank guard | 125,921 | Different data/hash from the marked test pattern |

The previous pipeline used the same prepared PNG as the preview. CUPS rounded the A4 raster to 1677 × 2373 at 203 DPI, versus the app's 1678 × 2374 export. This small driver-stage resampling is documented, rather than claiming device-raster pixel identity. A missing system-bus warning in the isolated CUPS colour-management check did not prevent either conversion stage from completing.

These checks establish that the driver runs on this x86_64 environment and responds to image/paper-feed changes. They do not establish that the ARM64 binary runs on the user's Pi or that the F11 accepts this stream over Bluetooth. Vendor binaries and the user's test documents are not bundled in the deliverable.

## Browser checks

The updated app was opened through the managed development preview. Checks confirmed PDF upload, automatic monochrome conversion, preview/download controls, the printer settings panel, advanced transfer controls, saving preferences, disabled printing when no printer/driver is available, and a useful error when Bluetooth libraries are absent. No application JavaScript error was observed during those actions.

Version 1.0 browser checks also covered changing orientation, disabling downloads until changes are applied, and pixel zoom. Native browser Save-to-disk completion remains unverified in this cloud browser; download headers, responses and file contents pass HTTP integration tests.

## Still requiring the Pi and F11

- A real apt/driver installation on Raspberry Pi OS ARM64.
- Bluetooth adapter discovery, pairing/PIN interaction, trust persistence and RFCOMM connection on the Pi.
- Sending the generated stream over a real Bluetooth radio and observing the paper.
- Printer-specific replies, disconnect behaviour, buffer limits, paper feed, physical dimensions and image quality.

The app does not decode undocumented F11 status packets or guess an initialisation handshake. “Data sent” is not a successful-print acknowledgement. Retain the diagnostic report when investigating interrupted or poor-quality output.


## User hardware results and single-page regression (23 October 2026)

The user confirmed terminal-script printing on the Pi/F11, not application printing. Their CUPS log initially showed a 2 by 2 page grid for the 1679 by 2374 PNG with explicit `ppi=203`. Removing that option while retaining fit scaling produced a page the user described as basically perfect. These are user-reported hardware results, separate from the local x86_64 checks above.

Version 1.1.1 removes the explicit PPI option and rejects missing, tiled, or mixed grid confirmations before returning any printer data. A mocked pipeline regression checks acceptance of a 1 by 1 grid and rejection of the observed 2 by 2 grid and missing diagnostics; it also checks the conversion command excludes explicit PPI. Earlier real-filter measurements above predate this fix.

Only density values 1, 8 and 15 are supported by the supplied PPD. A later density-15 attempt disconnected during transfer. Its cause and the reliability of slower pacing remain unverified. The terminal helper offers an explicit whole-page retry, not mid-page continuation. UI pairing and long-run radio reliability still need hardware testing.
