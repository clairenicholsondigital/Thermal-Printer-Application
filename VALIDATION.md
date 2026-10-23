# Version 1.1.1 validation

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

The real pipeline uses the same prepared PNG as the preview. CUPS rounded the A4 raster to 1677 × 2373 at 203 DPI, versus the app's 1678 × 2374 export. This small driver-stage resampling is documented, rather than claiming device-raster pixel identity. A missing system-bus warning in the isolated CUPS colour-management check did not prevent either conversion stage from completing.

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

The user confirmed an RFCOMM connection on channel 1 and visible printing on the Pi 5/F11. Their CUPS log initially showed a 2 by 2 page grid for the 1679 by 2374 PNG with explicit `ppi=203`. Removing that option while retaining fit scaling produced a page the user described as basically perfect. These are user-reported hardware results, separate from the local x86_64 checks above.

Version 1.1.1 removes the explicit PPI option and rejects missing, tiled, or mixed grid confirmations before returning any printer data. A mocked pipeline regression checks acceptance of a 1 by 1 grid and rejection of the observed 2 by 2 grid and missing diagnostics; it also checks the conversion command excludes explicit PPI. Earlier real-filter measurements above predate this fix.

Only density values 1, 8 and 15 are supported by the supplied PPD. A later density-15 attempt disconnected during transfer. Its cause and the reliability of slower pacing remain unverified. The terminal helper offers an explicit whole-page retry, not mid-page continuation. UI pairing and long-run radio reliability still need hardware testing.
