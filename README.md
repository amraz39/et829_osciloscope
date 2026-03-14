# ET829 / MDS8209 — USB PC Interface Tool

> **⚠️ BETA SOFTWARE** — This project is the result of independent reverse engineering. The USB protocol was not documented by the manufacturer. While it works reliably in testing, edge cases and untested firmware revisions may produce unexpected behaviour. Use with appropriate caution and please report issues.

---

## Overview

The **ToolTop ET829** (sold under several OEM brands including **MUSTOOL MDS8209**) is a 3-in-1 bench instrument combining:

- 80 MHz dual-channel digital oscilloscope
- 6000-count true-RMS digital multimeter (DMM)
- Arbitrary waveform generator (AWG)

Despite being a capable instrument, the manufacturer provides **no official PC software** and publishes no USB protocol documentation. This project fills that gap through complete reverse engineering of the USB interface.

`et829_v5.py` is a single-file Python tool that provides:

- **Live DMM readings** streamed to terminal and/or CSV log
- **Oscilloscope waveform download** — reads all saved captures from device memory
- **PNG waveform export** with dark oscilloscope-style plots
- **CSV export** of raw ADC values and calibrated voltages
- **Device info query** — firmware date, version, hardware revision, board ID
- Both an **interactive menu** and a **full CLI interface**

---

## Device Identification

| Field | Value |
|---|---|
| Trade names | ToolTop ET829, MUSTOOL MDS8209 |
| Internal board | ET202D |
| USB Vendor ID | `0x2E88` (HDSC / Xiaohua Microelectronics) |
| USB Product ID | `0x4603` |
| USB class | CDC Serial (bulk transfer, not CDC ACM) |
| Firmware date | 202207 |
| Firmware version | 1.0 |
| Hardware revision | 1.17 |

---

## Requirements

### Python packages

```bash
pip install pyusb matplotlib
```

### libusb

On **Windows**, place `libusb-1.0.dll` in the same folder as the script. Download from [libusb.info](https://libusb.info).

On **Linux/macOS**, install via your package manager:
```bash
sudo apt install libusb-1.0-0      # Debian/Ubuntu
brew install libusb                # macOS
```

### Windows USB driver

The device enumerates as a CDC serial port by default. On Windows, the built-in `usbser.sys` driver does **not** support bulk transfers. You must replace it using **[Zadig](https://zadig.akeo.ie/)**:

1. Open Zadig → Options → List All Devices
2. Select `CDC Config (Interface 0)`
3. Replace driver with **WinUSB**

> **Note:** After installing WinUSB, the device will no longer appear as a COM port in Device Manager — this is expected and correct.

---

## Installation

No installation required. Copy `et829_v5.py` and (on Windows) `libusb-1.0.dll` into any folder, then run:

```bash
python et829_v5.py
```

---

## Usage

### Interactive menu

```bash
python et829_v5.py
```

```
╔════════════════════════════════════════════════╗
║   ET829 / MDS8209  -  USB Tool  [v5.0]         ║
╠════════════════════════════════════════════════╣
║  1  |  Device info & firmware                  ║
║  2  |  Live DMM readings                       ║
║  3  |  Download & export scope saves           ║
║  0  |  Exit                                    ║
╚════════════════════════════════════════════════╝
```

### Command-line interface

```bash
# Device information
python et829_v5.py info

# Live DMM — display to terminal
python et829_v5.py dmm

# Live DMM — single reading then exit
python et829_v5.py dmm --once

# Live DMM — log to CSV file
python et829_v5.py dmm --csv

# Download all saved scope waveforms (prompts for V/div)
python et829_v5.py scope

# Scope — apply the same V/div to all slots
python et829_v5.py scope --vdiv 2.0

# Scope — AC couple (remove DC offset to centre waveforms)
python et829_v5.py scope --ac

# Scope — export PNG + CSV to a custom directory
python et829_v5.py scope --out ./my_captures --csv
```

### Capturing waveforms — workflow

The oscilloscope's USB interface is a **save-bank reader**, not a live stream. The device freezes its ring buffer when a channel-select button is pressed.

Recommended capture workflow:
1. Connect device via USB
2. Run `python et829_v5.py` and select option **3**
3. When prompted, ensure the device is in **measurement (DMM) mode** first
4. The tool switches to scope mode automatically
5. If the ring buffer does not freeze automatically, press the **CH1 button** on the device when prompted
6. The tool scans all saved pages and exports PNG/CSV files

---

## Output files

All files are written to the output directory (default: `captures/`), named by timestamp, slot number, sequence counter, and V/div setting.

| File | Description |
|---|---|
| `et829_YYYYMMDD_HHMMSS_slot01_seq222_vdiv2p0V.png` | Individual waveform plot |
| `et829_YYYYMMDD_HHMMSS_slot01_seq222_vdiv2p0V.csv` | Raw ADC + calibrated voltages |
| `et829_YYYYMMDD_HHMMSS_overview.png` | All waveforms in a single grid image |

### CSV format

```
sample_idx, raw_ch1, ch1_volts, raw_ch2, ch2_volts
0, 93, -0.280, 128, 0.000
1, 94, -0.240, 127, -0.040
...
```

`raw_chN` is the raw 8-bit ADC value (0–255, midpoint = 128 = 0 V).  
`chN_volts` is the calibrated voltage using `(raw − 128) / 25 × V/div`.

---

## Supported DMM modes

| Mode | Unit | Notes |
|---|---|---|
| DC Voltage | V | |
| AC Voltage | V | |
| Resistance | Ω | |
| Continuity | Ω | Beeps on device |
| Diode | V | |
| Capacitance | F | |
| Frequency | Hz | |
| Duty Cycle | % | |

The tool automatically detects the active mode from the measurement frame and formats the reading with the correct unit and decimal places. Overload (OL) conditions are shown explicitly.

---

## Reverse-engineered USB protocol

This section documents the full protocol as discovered through packet analysis and experimentation. It is provided here for reference and to allow others to build on this work.

### USB endpoints

| Endpoint | Type | Address | Direction | Notes |
|---|---|---|---|---|
| EP5 | Bulk | `0x05` | OUT (host → device) | All commands sent here |
| EP4 | Bulk | `0x84` | IN (device → host) | All responses read here |
| EP3 | Interrupt | `0x83` | IN | Silent — no data observed |

### Command reference

| TX bytes | RX header | Description |
|---|---|---|
| `0D 0A` | `A5 2A` | Ping / mode status (7 bytes) |
| `0D 00` | `A5 21` | Enter scope mode |
| `0D 01` | `A5 21` | Arm scope mode |
| `0D 02` | — | CH1 select — freezes ring buffer for CH1 saves |
| `0D 21` | `A5 21` | Enter DMM mode |
| `0D 09` | `A5 29` | Device info (49 bytes) |
| `00 05` | `A5 25` | DMM measurement (15 bytes, live) |
| `A5 22 XX` | `A5 22` | Seek save ring buffer to slot `XX` |
| `00 02` | `A5 22` | Read current save slot (waveform frame) |

> **Note on CH2 / dual-channel commands:** The command byte for CH2 and CH1+CH2 modes is **state-dependent** and varies between sessions. The tool sweeps commands `0D 02`–`0D 2F` and deduplicates results by sequence number.

### DMM frame format (TX: `00 05`, RX: 15 bytes)

```
Byte  Field    Description
────  ───────  ──────────────────────────────────────
 0    A5       Sync byte
 1    25       Command ID
2–3   plen     Payload length = 9 (uint16 LE)
 4    2D       Frame subtype
5–8   int32    Measurement value (signed int32 LE)
 9    B5       Range code (affects decimal scaling)
10    B6       Mode code (see table above)
11    B7       Decimal places shown on display
12    00       Padding
13    OL       Overload flag: 0x01 = overload / open lead
14    CHK      Checksum: (0x100 − sum(bytes 0..13)) mod 0x100
```

### Oscilloscope save frame format (RX after seek + `00 02`)

```
Byte  Field      Description
────  ─────────  ──────────────────────────────────────
 0    A5         Sync byte
 1    22         Command ID
2–3   plen       Payload length (uint16 LE)
4–5   seq        Sequence counter (uint16 LE) — increments by 256 per save
 6    —          Session counter byte (not V/div)
 7    —          Session counter byte
 8    tb_idx     Timebase index — always 0x00 in saves (unreliable)
 9    ch_flags   Channel flags: 0x01=CH1, 0x02=CH2, 0x03=CH1+CH2
10…   samples    Raw ADC bytes, uint8, midpoint 0x80 (128) = 0 V
```

**Dual-channel layout:** For `ch_flags = 0x03`, the sample buffer contains CH1 samples followed immediately by CH2 samples (sequential, not interleaved). The buffer is split at the midpoint before processing.

**V/div:** Not stored in the save frame. Must be supplied by the user at export time.

**Voltage formula:**
```
voltage_V = (raw_byte − 128) / 25 × V_per_div
```
The device ADC maps 200 of 256 counts across 8 display divisions = **25 counts/division**.

---

## Code architecture

`et829_v5.py` is a single self-contained file (~1075 lines). The main sections are:

| Section | Functions | Description |
|---|---|---|
| USB core | `open_device`, `drain`, `xfer`, `reopen_device`, `switch_mode` | Device enumeration, bulk I/O, mode switching with auto-reconnect on `[Errno 5]` |
| Device info | `cmd_info` | Queries and displays firmware date, version, board ID |
| DMM | `query_dmm`, `format_dmm`, `cmd_dmm` | Live measurement loop with mode decode and CSV logging |
| Scope I/O | `seek_and_read` | Two-step seek+read protocol with explicit timing |
| Buffer cleaning | `trim_uninit`, `trim_head_zeros`, `trim_interior_zeros`, `trim_noise_tail`, `trim_near_zero_tail`, `trim_channel_bleed`, `clean_single`, `clean_dual` | 7-function pipeline that removes uninitialized SRAM bytes, ADC noise bursts, interior zero blocks, and cross-channel data bleed |
| Frame parsing | `parse_scope_frame`, `raw_to_volts`, `apply_vdiv` | Decodes raw USB frame into structured dict with calibrated voltages |
| Statistics | `wstats`, `estimate_freq` | Per-channel Vpp / DC / Vrms and zero-crossing cycle counter |
| Plotting | `style_ax`, `plot_single`, `plot_overview` | Dark-theme oscilloscope PNG output (matplotlib, Agg backend) |
| Export | `save_scope_csv` | CSV writer for raw + calibrated sample data |
| Scan engine | `cmd_scope` (inner `freeze_and_scan`) | Sweeps all channel-select commands, deduplicates by sequence number, prompts for physical button press if ring buffer is not frozen |
| UI | `interactive_menu`, `main` | Coloured terminal menu and argparse CLI |

### Buffer cleaning pipeline

One of the more complex parts of the codebase. The device's save buffer contains multiple categories of garbage data that must be removed before plotting:

```
clean_single(raw):
  1. trim_head_zeros       — skip leading 0x00 uninit bytes (run ≥ 5)
  2. trim_uninit           — standard edge trim (run ≥ 15 from both ends)
  3. trim_noise_tail       — pass 1: remove large ADC noise burst in last 20% of frame
  4. trim_interior_zeros   — cut at first interior 0x00 run after first 15% of buffer
                             (handles: square wave → 600 zero bytes → noise)
  5. trim_uninit           — edge cleanup after interior zeros removed
  6. trim_noise_tail       — pass 2: end-concentrated bursts only (min_first_pct=0.50)
  7. trim_near_zero_tail   — remove residual near-zero bytes (raw < 20) missed by #2

clean_dual(ch1_half, ch2_half):
  → clean_single(ch1) + trim_channel_bleed  (removes other-channel bleed at tail)
  → clean_single(ch2) + trim_channel_bleed
```

`trim_noise_tail` pass 1 deliberately runs **before** `trim_interior_zeros`. If run after, the shorter buffer's 20% tail window no longer contains the noise, causing it to be missed.

---

## Known limitations and areas for improvement

### Confirmed limitations

| # | Limitation | Detail |
|---|---|---|
| 1 | **No live waveform streaming** | The USB firmware freezes the ring buffer on connect. Only manually saved snapshots (press SAVE on device) can be read. This is a firmware constraint, not a software bug. |
| 2 | **V/div not stored in saves** | The save frame does not include the V/div setting at capture time. The user must supply it. Applying the wrong value will scale the Y-axis incorrectly but will not corrupt the raw data (which is always exported). |
| 3 | **Timebase unreliable in saves** | The timebase index byte (`d[8]`) is always `0x00` in USB save frames regardless of the device display setting. Frequency is estimated from zero-crossing counts only and is displayed as a cycle count without a time reference. |
| 4 | **CH2 / dual-channel command is state-dependent** | The command byte that selects CH2 or CH1+CH2 varies between sessions depending on the device's internal button state. The tool works around this by sweeping commands `0D 02`–`0D 2F`, but this adds 5–10 seconds to scan time. |
| 5 | **Windows only tested** | The tool uses `WinUSB` via PyUSB. Linux and macOS should work with `libusb`, but have not been tested. The `flush_stdin` function has a Windows/Linux branch. |
| 6 | **Ring buffer size unknown** | The maximum number of save slots has been tested up to 16. The hard limit (if any) has not been determined. Old saves are silently overwritten when the buffer is full. |
| 7 | **AWG not implemented** | The arbitrary waveform generator commands have not been reverse engineered. |

### Areas for improvement

| Area | Description |
|---|---|
| **Timebase recovery** | A statistical method (autocorrelation or FFT period detection) could estimate the sample rate from the waveform itself when a known-frequency signal is present, making timebase inference possible without user input. |
| **V/div recovery** | If both AC and DC captures of the same signal are available, the DC offset could be used to infer scaling. Alternatively, the AWG output (known amplitude) could serve as a calibration reference. |
| **CH2 command discovery** | A more targeted state-machine approach to finding the CH2 command — rather than a blind sweep — would reduce scan time and eliminate redundant USB traffic. |
| **Live waveform mode** | It may be possible to trigger rapid repeated saves from the host side by sending SAVE commands via USB. This would simulate a live stream at low frame rate. Not yet investigated. |
| **AWG control** | The AWG command set has not been explored. Reverse engineering these commands would complete the tool's coverage of all three device functions. |
| **Cross-platform testing** | Linux and macOS paths should be tested; the `libusb` backend should work without changes, but the driver installation path differs. |
| **Automated V/div from display** | If the device screen image could be captured (it cannot currently), OCR of the V/div label would eliminate the manual prompt entirely. |
| **Unit tests** | The buffer-cleaning pipeline (`clean_single`, `clean_dual`) has complex branching logic that would benefit from a proper test suite with captured real-device data as fixtures. |
| **GUI frontend** | The tool is terminal-only. A simple tkinter or web-based frontend with real-time DMM display and waveform browser would lower the barrier for non-technical users. |

---

## Project history

This tool was developed through iterative reverse engineering using USB packet capture (Wireshark + USBPcap on Windows). There is no manufacturer SDK, no reference implementation, and no published documentation. Every command, frame format, and timing constraint in this file was determined empirically.

Key discoveries made during development:

- The USB interface is a **save-bank reader**, not a streaming interface — all waveform data is read from the device's internal circular save buffer
- The save frame does **not** encode V/div — this field was confirmed absent after exhaustive byte-by-byte comparison of saves made at different V/div settings
- The device ADC uses **25 counts per division** (not 32) — confirmed by back-calculating from known-amplitude reference signals
- Dual-channel frames store CH1 and CH2 **sequentially** (not interleaved) — the midpoint of the buffer is the CH1/CH2 boundary
- Calling `dispose_resources()` or `release_interface()` on the PyUSB device object **hangs indefinitely** on Windows with WinUSB — the device handle must be abandoned and re-opened via `usb.core.find()` instead
- The buffer contains **four distinct categories of garbage data** at different positions, requiring a 7-stage cleaning pipeline to remove correctly

---

## Contributing

Pull requests, bug reports, and protocol discoveries are welcome. If you have a device with a different firmware version and observe different behaviour, please open an issue with:

- The output of `python et829_v5.py info`
- A description of what differs
- If possible, a USB packet capture (Wireshark + USBPcap)

---

## Disclaimer

This project is not affiliated with, endorsed by, or connected to ToolTop, MUSTOOL, or any related manufacturer. All protocol information was obtained through lawful reverse engineering for interoperability purposes. Use at your own risk.

---

## License

MIT License. See `LICENSE` for details.
