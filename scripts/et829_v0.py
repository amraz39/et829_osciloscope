"""
ET829 / MDS8209 — Unified USB Tool
====================================
Single script covering all USB functionality:

  python et829.py                     # interactive menu
  python et829.py info                # device board info & firmware
  python et829.py dmm                 # live DMM readings
  python et829.py dmm --csv           # DMM logging to CSV
  python et829.py dmm --once          # single DMM reading
  python et829.py scope               # download & export all saved waveforms
  python et829.py scope --vdiv 1.0    # scope with known V/div for all slots
  python et829.py scope --ac          # scope, remove DC offset (centre waveforms)
  python et829.py scope --out ./out   # scope, custom output directory
  python et829.py scope --csv         # scope + export raw CSV files

Requires: pip install pyusb matplotlib  +  libusb-1.0.dll in same folder
"""

import usb.core, usb.util
import time, sys, os, struct, argparse, csv, datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime as dt

def flush_stdin():
    """Discard any characters left in stdin buffer (e.g. after Ctrl+C in DMM)."""
    try:
        import msvcrt                    # Windows
        while msvcrt.kbhit():
            msvcrt.getwch()
    except ImportError:
        import termios                   # Linux/Mac
        try: termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except: pass

# ── Colour helpers ────────────────────────────────────────────────────────────
os.system("")  # enable ANSI on Windows
RST = "\033[0m";  BLD = "\033[1m"
GRN = "\033[92m"; YLW = "\033[93m"; CYN = "\033[96m"
GRY = "\033[90m"; MAG = "\033[95m"; RED = "\033[91m"
WHT = "\033[97m"; BLU = "\033[94m"
def cp(c, *a): print(c + " ".join(str(x) for x in a) + RST, flush=True)
def hr(c=GRY, w=56): cp(c, "─" * w)

# ── USB constants ─────────────────────────────────────────────────────────────
VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84
MAX_SCAN = 64

# ── USB core ──────────────────────────────────────────────────────────────────
def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit(RED + "Device not found.\n"
                 "  • Check USB cable\n"
                 "  • Run Zadig: replace driver on 'CDC Config (Interface 0)' with WinUSB" + RST)
    for intf in range(3):
        try:
            if dev.is_kernel_driver_active(intf):
                dev.detach_kernel_driver(intf)
        except: pass
    try: dev.set_configuration()
    except: pass
    try: usb.util.claim_interface(dev, 0)
    except: pass
    return dev

def drain(dev):
    """Flush any pending data from the bulk IN endpoint. Swallows all errors."""
    for _ in range(16):   # cap iterations — don't loop forever
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def xfer(dev, tx, max_bytes=8192, delay=0.15):
    drain(dev)
    dev.write(EP_OUT, tx, timeout=1000)
    time.sleep(delay)
    buf = bytearray()
    while True:
        try:
            buf.extend(dev.read(EP_BULK, 512, timeout=300))
            if len(buf) >= max_bytes: break
        except: break
    return bytes(buf) if buf else None

# ═══════════════════════════════════════════════════════════════════════════════
# INFO — device board / firmware query
# ═══════════════════════════════════════════════════════════════════════════════

INFO_FIELDS = [
    # (byte_start, byte_end, label, type)
    # Response to 0D 09 is A5 29 ... (49 bytes total, payload starts at byte 4)
    # Layout decoded from captures: ASCII strings packed in payload
]

def cmd_info(dev):
    """Query device info (0D 09 → A5 29) and print a formatted summary."""
    cp(BLD+WHT, "\n  Querying device info...")

    raw = xfer(dev, bytes([0x0D, 0x09]), max_bytes=256, delay=0.3)
    if not raw:
        cp(RED, "  No response to info query.")
        return

    cp(GRY, f"  Raw ({len(raw)} bytes): {raw.hex().upper()}")
    hr()

    if len(raw) >= 2 and raw[0] == 0xA5 and raw[1] == 0x29:
        payload = raw[4:]  # skip A5 29 len_lo len_hi
        cp(BLD+CYN, "\n  ┌─ Device Information ──────────────────────────┐")

        # Try to extract printable ASCII strings from payload
        strings = []
        current = []
        for b in payload:
            if 0x20 <= b <= 0x7E:
                current.append(chr(b))
            else:
                if len(current) >= 3:
                    strings.append("".join(current))
                current = []
        if len(current) >= 3:
            strings.append("".join(current))

        known_labels = {
            0: "Board Model",
            1: "Firmware Date",
            2: "Firmware Ver",
            3: "HW Revision",
        }
        for i, s in enumerate(strings):
            label = known_labels.get(i, f"Field {i}")
            cp(CYN, f"  │  {label:<16} {WHT}{s}")

        # Show all bytes as a named hex dump
        cp(CYN, f"  │")
        cp(CYN, f"  │  {'Offset':<8} {'Hex':<40} {'ASCII'}")
        for off in range(0, len(payload), 16):
            chunk = payload[off:off+16]
            hexpart  = " ".join(f"{b:02X}" for b in chunk)
            ascpart  = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in chunk)
            cp(GRY, f"  │  {off:<8} {hexpart:<40} {ascpart}")

        cp(CYN, "  └────────────────────────────────────────────────┘")
    else:
        cp(YLW, "  Unexpected response format.")
        cp(GRY, f"  Hex: {raw.hex().upper()}")

    # Also query mode status ping
    cp(BLD+WHT, "\n  Mode status (0D 0A ping)...")
    raw2 = xfer(dev, bytes([0x0D, 0x0A]), max_bytes=64, delay=0.2)
    if raw2:
        cp(GRY, f"  Raw: {raw2.hex().upper()}")
        if len(raw2) >= 3:
            cp(CYN, f"  Status bytes: cmd=0x{raw2[1]:02X}  b2=0x{raw2[2]:02X}  "
                    f"→ {'scope' if raw2[2] in (0x00,0x01) else 'DMM' if raw2[2]==0x21 else 'unknown'} mode")

# ═══════════════════════════════════════════════════════════════════════════════
# DMM — live multimeter reader
# ═══════════════════════════════════════════════════════════════════════════════

DMM_CMD = bytes([0x00, 0x05])

MODE_NAMES = {
    5: "DC-V", 6: "AC-V", 7: "Resistance", 9: "Continuity",
    10: "Diode", 11: "Capacitance", 18: "Frequency", 19: "Duty"
}

def query_dmm(dev):
    try:
        try: dev.read(EP_BULK, 64, timeout=30)
        except: pass
        dev.write(EP_OUT, DMM_CMD, timeout=500)
        time.sleep(0.12)
        raw = bytes(dev.read(EP_BULK, 64, timeout=500))
    except: return None

    if len(raw) < 15 or raw[0] != 0xA5 or raw[1] != 0x25:
        return None

    plen       = struct.unpack_from('<H', raw, 2)[0]
    payload    = raw[4:4+plen]
    ol_flag    = raw[13]
    checksum   = raw[14]
    expected   = (0x100 - sum(raw[:14]) % 0x100) % 0x100

    mode_code  = payload[6] if len(payload) > 6 else 0
    dec_places = payload[7] if len(payload) > 7 else 3
    b5         = payload[5] if len(payload) > 5 else 0
    raw_int32  = struct.unpack_from('<i', payload, 1)[0]
    raw_uint32 = struct.unpack_from('<I', payload, 1)[0]
    overload   = (ol_flag == 0x01)

    if mode_code in (7, 9):
        value, unit = raw_int32 * (10 ** (b5 - 1)), "Ohm"
    elif mode_code == 19:
        value, unit = raw_uint32 / 100.0, "%"
    elif mode_code == 5:   value, unit = raw_int32 / 1000.0, "V"
    elif mode_code == 6:   value, unit = raw_int32 / 1000.0, "V"
    elif mode_code == 10:  value, unit = raw_int32 / 1000.0, "V"
    elif mode_code == 11:  value, unit = raw_int32 * (10 ** (b5 - 1)) * 1e-9, "F"
    elif mode_code == 18:  value, unit = raw_int32 / 1000.0, "Hz"
    else:                  value, unit = raw_int32 / 1000.0, "?"

    return dict(value=value, raw_int32=raw_int32, raw_uint32=raw_uint32,
                mode_code=mode_code, mode_name=MODE_NAMES.get(mode_code, f"mode{mode_code}"),
                unit=unit, dec_places=dec_places, b5=b5,
                overload=overload, ol_flag=ol_flag,
                chk_ok=(checksum == expected), raw_hex=raw.hex().upper())

def format_dmm(r):
    if r['overload']:
        return f"OL  [{r['unit']}]"
    val, dec, mode = r['value'], r['dec_places'], r['mode_code']
    if mode in (7, 9):
        av = abs(val)
        if av >= 1_000_000: return f"{val/1_000_000:.{dec}f} MOhm"
        if av >= 1_000:     return f"{val/1_000:.{dec}f} kOhm"
        return f"{val:.{dec}f} Ohm"
    if mode == 11:
        av = abs(val)
        if av >= 1:      return f"{val:.{dec}f} F"
        if av >= 1e-3:   return f"{val*1e3:.{dec}f} mF"
        if av >= 1e-6:   return f"{val*1e6:.{dec}f} uF"
        if av >= 1e-9:   return f"{val*1e9:.{dec}f} nF"
        return f"{val*1e12:.{dec}f} pF"
    if mode in (5, 6, 10) and 0 < abs(val) < 1.0:
        return f"{val*1000:.{max(1,dec)}f} mV"
    return f"{val:.{dec}f} {r['unit']}"

def reopen_device():
    """Re-enumerate and re-claim the device after a stale handle (Errno 5)."""
    cp(YLW, "  USB handle stale — re-opening device...")
    time.sleep(0.8)
    try:
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            cp(RED, "  Device not found during re-open.")
            return None
        for intf in range(3):
            try:
                if dev.is_kernel_driver_active(intf):
                    dev.detach_kernel_driver(intf)
            except: pass
        try: dev.set_configuration()
        except: pass
        try: usb.util.claim_interface(dev, 0)
        except: pass
        cp(GRN, "  Device re-opened successfully.")
        return dev
    except Exception as e:
        cp(RED, f"  Re-open failed: {e}")
        return None

def switch_mode(dev, mode_cmd, label, settle=0.6, pre_drain=True):
    """
    Robustly switch device mode.
    On [Errno 5] (stale handle), automatically re-opens the device and retries.
    settle:    seconds to wait after write before draining.
    pre_drain: if True, drain the USB buffer BEFORE writing the mode command.
               Use True when coming from scope mode (physical button presses
               leave data in the buffer that can cause a reboot if not flushed).
    Returns (True, dev) on success, (False, dev) on failure.
    """
    for attempt in range(3):
        try:
            if pre_drain:
                # Flush any pending data from previous mode (e.g. scope heartbeats,
                # CH1 button press response) before sending the new mode command.
                drain(dev)
                time.sleep(0.15)
                drain(dev)
            dev.write(EP_OUT, mode_cmd, timeout=1000)
            time.sleep(settle)
            drain(dev)
            time.sleep(0.2)
            drain(dev)
            cp(GRY, f"  Switched to {label} mode")
            return True, dev
        except Exception as e:
            cp(YLW, f"  Mode switch attempt {attempt+1}/3 failed: {e}")
            if "5" in str(e) or "I/O" in str(e):
                new_dev = reopen_device()
                if new_dev:
                    dev = new_dev
            time.sleep(0.5)
    cp(RED, f"  Could not switch to {label} mode after 3 attempts.")
    return False, dev

def cmd_dmm(dev, args):
    ok, dev = switch_mode(dev, bytes([0x0D, 0x21]), "DMM")
    if not ok:
        return dev

    if args.csv_out:
        print("timestamp,mode,value,formatted,unit,overload,b5,b6,b7,ol_flag,chk_ok,raw_hex")
    else:
        cp(BLD+GRN, "=" * 54)
        cp(BLD+GRN, "  ET829 / MDS8209  —  Live DMM Reader")
        cp(BLD+GRN, "=" * 54)
        cp(GRY, f"  polling every {args.interval}s  |  Ctrl+C to stop\n")

    count, prev_mode, no_resp = 0, None, 0
    try:
        while True:
            r  = query_dmm(dev)
            ts = dt.now().strftime("%H:%M:%S.%f")[:-3]

            if r:
                no_resp = 0
                reading  = format_dmm(r)
                new_mode = (r['mode_code'] != prev_mode)
                prev_mode = r['mode_code']

                if args.csv_out:
                    print(f"{ts},{r['mode_name']},{r['value']:.6f},{reading},"
                          f"{r['unit']},{r['overload']},{r['b5']},"
                          f"{r['mode_code']},{r['dec_places']},"
                          f"{r['ol_flag']},{r['chk_ok']},{r['raw_hex']}")
                else:
                    col = MAG if new_mode else (YLW if r['overload'] else GRN)
                    tag = f"  [{r['mode_name']}]" if new_mode else ""
                    chk = f"  {RED}!CHK{RST}" if not r['chk_ok'] else ""
                    cp(col, f"[{ts}]  {reading:<24}{tag}{chk}")
                    if args.raw:
                        cp(GRY, f"           b5={r['b5']} b6={r['mode_code']} "
                                 f"b7={r['dec_places']} ol={r['ol_flag']}  {r['raw_hex']}")
                count += 1
                if args.once: break
            else:
                no_resp += 1
                if not args.csv_out:
                    sys.stdout.write(f"\r[{ts}]  (no response x{no_resp} — try scope mode?)  ")
                    sys.stdout.flush()

            time.sleep(args.interval)

    except KeyboardInterrupt:
        pass
    if not args.csv_out:
        print()
        cp(CYN, f"\n  Done. {count} readings captured.")
    return dev

# ═══════════════════════════════════════════════════════════════════════════════
# SCOPE — saved waveform download & export
# ═══════════════════════════════════════════════════════════════════════════════

TIMEBASE_TABLE = [
    '5ns','10ns','20ns','50ns','100ns','200ns','500ns',
    '1us','2us','5us','10us','20us','50us',
    '1ms','2ms','5ms','10ms','20ms','50ms',
    '100ms','200ms','500ms','1s','2s','5s','10s'
]
STANDARD_VDIVS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]

COLORS = {
    'bg': '#1a1a2e', 'panel': '#16213e', 'grid': '#2a2a4a',
    'ch1': '#00d4aa', 'ch2': '#ff9500', 'text': '#e0e0e0',
    'subtext': '#888888', 'border': '#0f3460',
}

def seek_and_read(dev, page):
    """
    Two-step protocol: seek to page, then read waveform data.
    Uses explicit timing — do NOT use xfer() here because its drain()
    call on the second step would eat data buffered by the device.
    """
    try:
        # Step 1: seek
        drain(dev)
        dev.write(EP_OUT, bytes([0xA5, 0x22, page]), timeout=1000)
        time.sleep(0.15)
        try: dev.read(EP_BULK, 64, timeout=200)   # consume seek ACK
        except: pass
        time.sleep(0.06)
        # Step 2: read waveform
        dev.write(EP_OUT, bytes([0x00, 0x02]), timeout=1000)
        time.sleep(0.15)
        buf = bytearray()
        while True:
            try:
                buf.extend(dev.read(EP_BULK, 512, timeout=300))
                if len(buf) >= 8192: break
            except: break
        return bytes(buf) if buf else None
    except Exception as e:
        cp(YLW, f"  seek_and_read page={page} error: {e}")
        time.sleep(0.3)
        return None

def trim_uninit(raw, run_len=15):
    """
    Remove uninitialized SRAM bytes (0x00) from BOTH ends of a save frame.
    Uninit bytes read as 0x00 (raw = -4V on scale) and can appear at the
    start (pre-trigger buffer) and end (post-capture buffer).
    We scan inward from each end and stop at the first non-zero-run position.
    Signals that legitimately pass through 0x00 (e.g. ramps) are preserved
    because they only produce SHORT isolated zeros, not a run of 15+.
    """
    n = len(raw)
    if n < run_len:
        return raw

    # Trim from the start
    start = 0
    while start <= n - run_len:
        if all(raw[j] == 0 for j in range(start, start + run_len)):
            start += 1
        else:
            break

    # Trim from the end
    end = n
    while end - run_len >= start:
        if all(raw[j] == 0 for j in range(end - run_len, end)):
            end -= 1
        else:
            break

    return raw[start:end]

def parse_scope_frame(data):
    if not data or len(data) < 10 or data[0] != 0xA5:
        return None
    try:
        plen = struct.unpack_from('<H', data, 2)[0]
        if plen < 6: return None
        seq      = struct.unpack_from('<H', data, 4)[0]
        ch_flags = data[9]
        tb_idx   = data[8]
        raw_all  = list(data[10:])
        if not raw_all: return None
        tb_lbl = TIMEBASE_TABLE[tb_idx] if tb_idx < len(TIMEBASE_TABLE) else f'idx{tb_idx}'

        # Dual-channel (0x03): buffer is CH1_block then CH2_block sequentially.
        # Split FIRST at the midpoint, then trim each half independently —
        # this prevents one channel's content from affecting the other's trim.
        # Single-channel: trim the whole buffer, assign to the correct channel.
        if ch_flags == 0x03 and len(raw_all) >= 2:
            mid     = len(raw_all) // 2
            raw_ch1 = trim_uninit(raw_all[:mid])
            raw_ch2 = trim_uninit(raw_all[mid:])
        elif ch_flags == 0x02:
            raw_ch1 = []
            raw_ch2 = trim_uninit(raw_all)
        else:
            raw_ch1 = trim_uninit(raw_all)
            raw_ch2 = []

        if not raw_ch1 and not raw_ch2: return None

        n = max(len(raw_ch1), len(raw_ch2))
        return dict(seq=seq, ch_flags=ch_flags, tb_label=tb_lbl,
                    raw=raw_all, raw_ch1=raw_ch1, raw_ch2=raw_ch2, n=n)
    except:
        return None

def raw_to_volts(raw_samples, vdiv, ac_couple=False):
    if not raw_samples: return []
    v = [(s - 128) / 128.0 * vdiv * 4 for s in raw_samples]
    if ac_couple:
        dc = sum(v) / len(v)
        v = [x - dc for x in v]
    return v

def apply_vdiv(frame, vdiv, ac_couple=False):
    frame['ch1']       = raw_to_volts(frame.get('raw_ch1', []), vdiv, ac_couple)
    frame['ch2']       = raw_to_volts(frame.get('raw_ch2', []), vdiv, ac_couple)
    frame['ch1_vdiv']  = vdiv
    frame['ac_couple'] = ac_couple
    # n = number of samples per channel (for x-axis scaling)
    frame['n'] = max(len(frame['ch1']), len(frame['ch2']))
    return frame

def wstats(v):
    if not v: return 0, 0, 0, 0, 0
    vmin, vmax = min(v), max(v)
    return vmax-vmin, sum(v)/len(v), (sum(x*x for x in v)/len(v))**.5, vmin, vmax

def prompt_vdiv(slot_num, seq, n_samples):
    print()
    cp(CYN, f"  Slot {slot_num}  (seq={seq}, {n_samples} samples)")
    cp(GRY, f"  Options: {', '.join(str(v) for v in STANDARD_VDIVS)} V/div")
    while True:
        try:
            val = input(f"  V/div for Slot {slot_num} [Enter = 1.0]: ").strip()
            if val == '': return 1.0
            v = float(val)
            if v > 0: return v
            cp(RED, "  Must be > 0")
        except ValueError:
            cp(RED, "  Enter a number e.g. 1.0 or 0.5")
        except (EOFError, KeyboardInterrupt):
            return 1.0

def style_ax(ax, vdiv, n, title='', center=0.0):
    half = vdiv * 4
    top, bot = center + half, center - half
    ax.set_facecolor(COLORS['panel'])
    ax.set_xlim(0, n - 1)
    ax.set_ylim(bot - half*0.05, top + half*0.05)
    for i in range(9):
        ax.axhline(bot + (top-bot)*i/8, color=COLORS['grid'], lw=0.5, zorder=0)
    for i in range(11):
        ax.axvline((n-1)*i/10, color=COLORS['grid'], lw=0.5, zorder=0)
    ax.axhline(0, color=COLORS['subtext'], lw=0.8, linestyle='--', zorder=1)
    yticks = [bot + (top-bot)*i/8 for i in range(9)]
    ax.set_yticks(yticks)
    ax.set_yticklabels([f'{y:+.3f}' for y in yticks], fontsize=6)
    ax.set_xticks([])
    ax.tick_params(colors=COLORS['subtext'])
    ax.spines[:].set_color(COLORS['border'])
    if title:
        ax.set_title(title, color=COLORS['text'], fontsize=9, pad=4)

def plot_single(slot_idx, frame, outpath):
    vdiv     = frame['ch1_vdiv']
    ch1      = frame.get('ch1', [])
    ch2      = frame.get('ch2', [])
    n        = frame['n']
    ac       = frame.get('ac_couple', False)
    flags    = frame['ch_flags']
    has_ch1  = bool(ch1)
    has_ch2  = bool(ch2)

    # Compute display centre from whichever channel(s) are present
    all_volts = ch1 + ch2
    vpp, dc, rms, vmin, vmax = wstats(all_volts)
    sig_center = (vmax + vmin) / 2.0

    fig = plt.figure(figsize=(12, 6), facecolor=COLORS['bg'])
    fig.subplots_adjust(left=0.09, right=0.97, top=0.88, bottom=0.12)
    ax = fig.add_subplot(111)
    style_ax(ax, vdiv, n, center=sig_center)

    if has_ch1:
        ax.plot(range(len(ch1)), ch1, color=COLORS['ch1'], lw=1.0, label='CH1', zorder=3)
    if has_ch2:
        ax.plot(range(len(ch2)), ch2, color=COLORS['ch2'], lw=1.0, label='CH2', zorder=3)

    ch_note = {0x01:'CH1', 0x02:'CH2', 0x03:'CH1+CH2'}.get(flags, f'flags=0x{flags:02X}')
    ac_note = '  [AC coupled]' if ac else ''
    offset_note = f'  centre {sig_center:+.3f}V' if abs(sig_center) > vdiv * 0.1 else ''
    fig.text(0.09, 0.94,
             f"ET829 / MDS8209  —  Saved Waveform  (Slot {slot_idx + 1}){ac_note}",
             color=COLORS['text'], fontsize=12, fontweight='bold')
    fig.text(0.09, 0.90,
             f"seq={frame['seq']}   {ch_note}: {vdiv}V/div   "
             f"Timebase: {frame['tb_label']}   Samples: {n}{offset_note}",
             color=COLORS['subtext'], fontsize=9)

    # Stats box — show per-channel stats
    stats_lines = []
    if has_ch1:
        v1pp, v1dc, v1rms, v1min, v1max = wstats(ch1)
        stats_lines += [f"CH1  Vpp={v1pp:.3f}V  DC={v1dc:.3f}V  Vrms={v1rms:.3f}V"]
    if has_ch2:
        v2pp, v2dc, v2rms, v2min, v2max = wstats(ch2)
        stats_lines += [f"CH2  Vpp={v2pp:.3f}V  DC={v2dc:.3f}V  Vrms={v2rms:.3f}V"]
    ax.text(0.99, 0.97, '\n'.join(stats_lines),
            transform=ax.transAxes, fontsize=8,
            color=COLORS['text'], va='top', ha='right', fontfamily='monospace',
            bbox=dict(facecolor=COLORS['bg'], alpha=0.8,
                      edgecolor=COLORS['border'], boxstyle='round,pad=0.4'))

    ax.set_ylabel(f"Voltage  ({vdiv}V/div)", color=COLORS['subtext'], fontsize=8)
    ax.set_xlabel(f"Sample index  ({n} samples/ch total)", color=COLORS['subtext'], fontsize=8)
    ax.legend(loc='upper left', fontsize=8, facecolor=COLORS['bg'],
              edgecolor=COLORS['border'])
    plt.savefig(outpath, dpi=150, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)

def plot_overview(pages, outpath):
    n_pages = len(pages)
    cols = min(3, n_pages)
    rows = (n_pages + cols - 1) // cols
    fig = plt.figure(figsize=(cols*5.5, rows*3.2+1.0), facecolor=COLORS['bg'])
    fig.suptitle("ET829 / MDS8209  —  All Saved Waveforms",
                 color=COLORS['text'], fontsize=14, fontweight='bold', y=0.98)
    for idx, (seek, frame) in enumerate(pages):
        vdiv  = frame['ch1_vdiv']
        ch1   = frame.get('ch1', [])
        ch2   = frame.get('ch2', [])
        n     = frame['n']
        all_v = ch1 + ch2
        vpp, dc, rms, vmin, vmax = wstats(all_v)
        sig_center = (vmax + vmin) / 2.0
        flags = frame['ch_flags']
        ch_note = {0x01:'CH1', 0x02:'CH2', 0x03:'CH1+CH2'}.get(flags,'?')
        ax = fig.add_subplot(rows, cols, idx + 1)
        style_ax(ax, vdiv, n, center=sig_center,
                 title=f"Slot {idx+1}  {ch_note}  {vdiv}V/div  seq={frame['seq']}")
        if ch1: ax.plot(range(len(ch1)), ch1, color=COLORS['ch1'], lw=0.8, zorder=3)
        if ch2: ax.plot(range(len(ch2)), ch2, color=COLORS['ch2'], lw=0.8, zorder=3)
        ax.text(0.99, 0.97, f"Vpp={vpp:.3f}V  DC={dc:.3f}V",
                transform=ax.transAxes, fontsize=7,
                color=COLORS['text'], va='top', ha='right', fontfamily='monospace',
                bbox=dict(facecolor=COLORS['bg'], alpha=0.7,
                          edgecolor='none', boxstyle='round,pad=0.2'))
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(outpath, dpi=150, bbox_inches='tight', facecolor=COLORS['bg'])
    plt.close(fig)
    cp(CYN, f"  Overview: {outpath}")

def save_scope_csv(slot_idx, frame, outpath):
    with open(outpath, 'w', newline='') as f:
        w = csv.writer(f)
        ch1 = frame.get('ch1', [])
        ch2 = frame.get('ch2', [])
        raw1 = frame.get('raw_ch1', [])
        raw2 = frame.get('raw_ch2', [])
        n = max(len(ch1), len(ch2))
        w.writerow(['sample_idx', 'raw_ch1', 'ch1_volts', 'raw_ch2', 'ch2_volts'])
        for i in range(n):
            r1 = raw1[i] if i < len(raw1) else ''
            v1 = f"{ch1[i]:.6f}" if i < len(ch1) else ''
            r2 = raw2[i] if i < len(raw2) else ''
            v2 = f"{ch2[i]:.6f}" if i < len(ch2) else ''
            w.writerow([i, r1, v1, r2, v2])

def cmd_scope(dev, args):
    # Confirmed USB commands (reverse engineered):
    #   0D 00 = scope mode
    #   0D 02 = CH1 select  → freezes ring buffer showing CH1 saves (flags=0x01)
    #   0D 12 = CH2 select  → freezes ring buffer showing CH2 saves (flags=0x02)
    #   0D 18 = both CH     → freezes ring buffer showing dual saves (flags=0x03)
    # We scan with CH1 first, collect all pages, then re-scan with CH2 and both.
    ok, dev = switch_mode(dev, bytes([0x0D, 0x00]), "scope", settle=1.5)
    if not ok:
        return dev

    def freeze_and_scan(ch_cmd):
        """
        Send a channel-select command then scan all pages the ring buffer exposes.
        Returns ALL frames found (including already-seen ones — dedup is the
        caller's job). Stops only on genuine USB non-responses (end of buffer).
        """
        try:
            dev.write(EP_OUT, bytes([0x0D, ch_cmd]), timeout=1000)
            time.sleep(1.2)   # ring buffer needs time to freeze after channel select
            drain(dev)
            time.sleep(0.2)
            drain(dev)
        except:
            return []
        pages = []
        consecutive_misses = 0
        for p in range(MAX_SCAN):
            data  = seek_and_read(dev, p)
            frame = parse_scope_frame(data)
            if frame:
                pages.append((p, frame))
                consecutive_misses = 0
            else:
                consecutive_misses += 1
                # Show raw bytes for every miss so we can see if data came back
                # but parse_scope_frame rejected it
                if data:
                    cp(YLW, f"      seek={p:02X} got {len(data)}B but parse failed: "
                             f"{data[:12].hex().upper()}")
                else:
                    cp(GRY, f"      seek={p:02X} no response")
                # 4 real USB non-responses = genuine end of ring buffer
                if consecutive_misses >= 4:
                    break
        return pages

    cp(BLD+CYN, "\n  Scanning saved pages...")
    cp(GRY,     "  Trying all channel-select commands (CH2 command varies by device state)...")

    SKIP_CMDS = {0x00, 0x01, 0x09, 0x21, 0x22}   # scope/arm/info/DMM/seek — skip these
    seen_seqs = set()
    raw_pages = []
    dry_runs = 0

    # First, try 0D 02 (CH1). If we get nothing back, the ring buffer
    # is not frozen yet — prompt the user to press CH1 physically.
    first_pages = freeze_and_scan(0x02)
    if not first_pages:
        cp(BLD+YLW, "")
        cp(BLD+YLW, "  ══════════════════════════════════════════════════════")
        cp(BLD+YLW, "   Software CH1 command did not freeze ring buffer.")
        cp(BLD+YLW, "   Please press the  CH1  button on the device NOW,")
        cp(BLD+YLW, "   then press Enter to continue scanning.")
        cp(BLD+YLW, "  ══════════════════════════════════════════════════════")
        try: input("")
        except (EOFError, KeyboardInterrupt): return dev
        time.sleep(0.5)
        drain(dev)
        cp(GRY, "  Resuming scan after CH1 button press...")

    # Main sweep — 0x02 will be tried again (first_pages result discarded if empty,
    # or re-collected if it had data — dedup by seq handles duplicates)
    for ch_cmd in range(0x02, 0x30):
        if ch_cmd in SKIP_CMDS:
            continue
        cp(GRY, f"  Trying 0D {ch_cmd:02X}...")
        pages = freeze_and_scan(ch_cmd)
        new_found = []
        for p, frame in pages:
            ch_lbl = {0x01:'CH1', 0x02:'CH2', 0x03:'CH1+CH2'}.get(frame['ch_flags'],
                      f"0x{frame['ch_flags']:02X}")
            is_new = frame['seq'] not in seen_seqs
            status = "NEW " if is_new else "dup "
            cp(GRN if is_new else GRY,
               f"    seek={p:02X}  seq={frame['seq']:5d}  "
               f"samples={frame['n']:4d}  {ch_lbl:8s}  [{status}]")
            if is_new:
                seen_seqs.add(frame['seq'])
                raw_pages.append((p, frame))
                new_found.append(frame)
        if new_found:
            cp(BLD+GRN, f"  0D {ch_cmd:02X}: {len(new_found)} new / {len(pages)} total")
            dry_runs = 0
        elif pages:
            # Got data back but all duplicates — ring buffer is frozen and
            # fully collected. Stop after 3 consecutive all-duplicate commands.
            cp(GRY, f"  0D {ch_cmd:02X}: {len(pages)} page(s), all duplicates")
            dry_runs += 1
            if dry_runs >= 3:
                cp(GRY, f"  All pages already collected — done scanning.")
                break
        else:
            # No response at all — command not recognised by device in this state.
            cp(GRY, f"  0D {ch_cmd:02X}: no response")
            # Don't count as dry_run — keep going in case CH2 cmd is further ahead.
            # But if we've passed 0x18 (known max CH2 location) and have pages, stop.
            if raw_pages and ch_cmd > 0x18:
                cp(GRY, f"  Past 0x18 with no new commands — done scanning.")
                break

    # Sort chronologically
    raw_pages.sort(key=lambda x: x[1]['seq'])

    if not raw_pages:
        cp(YLW, "\n  No saved pages found. Press SAVE on device first.")
        time.sleep(0.3)
        drain(dev)
        return dev

    cp(BLD+WHT, f"\n  Found {len(raw_pages)} saved waveform(s).")

    # Collect V/div per slot
    if args.vdiv is not None:
        cp(GRY, f"  Applying {args.vdiv}V/div to all slots (--vdiv)")
    else:
        cp(GRY, "\n  V/div is not stored in save frames.")
        cp(GRY, "  Check the device screen or your notes for each slot.")
        cp(GRY, "  (Use --vdiv N to skip prompts and apply same V/div to all slots)\n")

    pages = []
    for idx, (seek, frame) in enumerate(raw_pages):
        vdiv = args.vdiv if args.vdiv is not None else prompt_vdiv(idx+1, frame['seq'], frame['n'])
        apply_vdiv(frame, vdiv, ac_couple=args.ac)
        pages.append((seek, frame))

    os.makedirs(args.out, exist_ok=True)
    cp(BLD+WHT, f"\n  Exporting to: {os.path.abspath(args.out)}\n")
    ts = dt.now().strftime("%Y%m%d_%H%M%S")

    for idx, (seek, frame) in enumerate(pages):
        vdiv   = frame['ch1_vdiv']
        vdiv_s = str(vdiv).replace('.', 'p')
        base   = os.path.join(args.out,
                 f"et829_{ts}_slot{idx+1:02d}_seq{frame['seq']}_vdiv{vdiv_s}V")

        if not args.no_single:
            png = base + ".png"
            plot_single(idx, frame, png)
            vpp, dc, *_ = wstats(frame['ch1'])
            cp(GRN, f"  Slot {idx+1:2d}: {vdiv}V/div  Vpp={vpp:.3f}V  DC={dc:.3f}V  → {png}")

        if args.csv_out:
            save_scope_csv(idx, frame, base + ".csv")

    if not args.no_overview:
        plot_overview(pages, os.path.join(args.out, f"et829_{ts}_overview.png"))

    cp(BLD+GRN, f"\n  Done. {len(pages)} waveform(s) exported.")
    # Drain any pending scope data before returning to menu.
    # Without this, the next switch_mode write can trigger a device reboot.
    time.sleep(0.3)
    drain(dev)
    time.sleep(0.2)
    drain(dev)
    return dev

# ═══════════════════════════════════════════════════════════════════════════════
# INTERACTIVE MENU
# ═══════════════════════════════════════════════════════════════════════════════

def interactive_menu(dev):
    while True:
        print()
        cp(BLD+WHT, "╔══════════════════════════════════════════════╗")
        cp(BLD+WHT, "║   ET829 / MDS8209  —  USB Tool               ║")
        cp(BLD+WHT, "╠══════════════════════════════════════════════╣")
        cp(WHT,     "║  1 │ Device info & firmware                   ║")
        cp(WHT,     "║  2 │ Live DMM readings                        ║")
        cp(WHT,     "║  3 │ Download & export scope saves            ║")
        cp(WHT,     "║  0 │ Exit                                     ║")
        cp(BLD+WHT, "╚══════════════════════════════════════════════╝")

        try:
            choice = input(CYN + "  Choice: " + RST).strip()
        except (EOFError, KeyboardInterrupt):
            break

        if choice == '0':
            break

        elif choice == '1':
            try:
                cmd_info(dev)
            except Exception as e:
                cp(RED, f"  Error: {e}")
                if "5" in str(e) or "I/O" in str(e):
                    new_dev = reopen_device()
                    if new_dev: dev = new_dev

        elif choice == '2':
            cp(GRY, "\n  DMM options (press Enter to accept defaults):")
            try:
                raw_in   = input("  Show raw hex? [y/N]: ").strip().lower() == 'y'
                once_in  = input("  Single reading only? [y/N]: ").strip().lower() == 'y'
                csv_in   = input("  CSV output? [y/N]: ").strip().lower() == 'y'
                intv_str = input("  Poll interval seconds [0.5]: ").strip()
                interval = float(intv_str) if intv_str else 0.5
            except (EOFError, KeyboardInterrupt):
                continue

            class DmmArgs: pass
            a = DmmArgs()
            a.raw = raw_in; a.once = once_in; a.csv_out = csv_in; a.interval = interval
            try:
                result = cmd_dmm(dev, a)
                if result is not None: dev = result
            except KeyboardInterrupt:
                cp(CYN, "  Stopped.")
            except Exception as e:
                cp(RED, f"  DMM error: {e}")
                if "5" in str(e) or "I/O" in str(e):
                    new_dev = reopen_device()
                    if new_dev: dev = new_dev
                else:
                    time.sleep(1.0)
                    drain(dev)
            flush_stdin()

        elif choice == '3':
            print()
            cp(BLD+YLW, "  ┌─ BEFORE CONTINUING ───────────────────────────────┐")
            cp(BLD+YLW, "  │  Make sure device is in MEASUREMENT (DMM) mode.    │")
            cp(BLD+YLW, "  │  The script handles the rest automatically.         │")
            cp(BLD+YLW, "  └────────────────────────────────────────────────────┘")
            print()
            cp(GRY, "  Scope export options (press Enter to accept defaults):")
            try:
                out_dir  = input("  Output directory [captures]: ").strip() or "captures"
                vdiv_str = input("  V/div for ALL slots, or Enter to prompt per slot: ").strip()
                vdiv_val = float(vdiv_str) if vdiv_str else None
                ac_in    = input("  AC couple (remove DC offset)? [y/N]: ").strip().lower() == 'y'
                csv_in   = input("  Also export CSV? [y/N]: ").strip().lower() == 'y'
                ov_in    = input("  Skip overview image? [y/N]: ").strip().lower() == 'y'
                si_in    = input("  Skip individual images? [y/N]: ").strip().lower() == 'y'
            except (EOFError, KeyboardInterrupt):
                continue

            class ScopeArgs: pass
            a = ScopeArgs()
            a.out = out_dir; a.vdiv = vdiv_val; a.ac = ac_in
            a.csv_out = csv_in; a.no_overview = ov_in; a.no_single = si_in
            try:
                result = cmd_scope(dev, a)
                if result is not None: dev = result
            except KeyboardInterrupt:
                cp(CYN, "  Stopped.")
            except Exception as e:
                cp(RED, f"  Scope error: {e}")
                if "5" in str(e) or "I/O" in str(e):
                    new_dev = reopen_device()
                    if new_dev: dev = new_dev
                else:
                    time.sleep(1.0)
                    drain(dev)
            flush_stdin()

        else:
            cp(YLW, "  Unknown option — enter 1, 2, 3 or 0")

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        prog='et829',
        description='ET829 / MDS8209 — Unified USB Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
commands:
  info                query device board info & firmware version
  dmm                 live DMM reader
  scope               download & export all saved waveform pages

examples:
  python et829.py                        interactive menu
  python et829.py info
  python et829.py dmm --once
  python et829.py dmm --csv --interval 1.0
  python et829.py scope --vdiv 1.0 --out captures
  python et829.py scope --ac --csv
""")

    sub = ap.add_subparsers(dest='cmd')

    # info
    sub.add_parser('info', help='Device board info & firmware version')

    # dmm
    p_dmm = sub.add_parser('dmm', help='Live DMM reader')
    p_dmm.add_argument('--once',     action='store_true', help='Single reading then exit')
    p_dmm.add_argument('--csv',      action='store_true', dest='csv_out', help='CSV output')
    p_dmm.add_argument('--raw',      action='store_true', help='Show raw hex bytes')
    p_dmm.add_argument('--interval', type=float, default=0.5, metavar='S',
                       help='Poll interval in seconds (default 0.5)')

    # scope
    p_sc = sub.add_parser('scope', help='Download & export saved scope waveforms')
    p_sc.add_argument('--out',         default='captures', metavar='DIR',
                      help='Output directory (default: captures)')
    p_sc.add_argument('--vdiv',        type=float, default=None, metavar='V',
                      help='V/div for ALL slots (e.g. 1.0). Skips per-slot prompt.')
    p_sc.add_argument('--ac',          action='store_true',
                      help='Remove DC offset — centres waveform on 0V')
    p_sc.add_argument('--csv',         action='store_true', dest='csv_out',
                      help='Also export raw CSV files')
    p_sc.add_argument('--no-overview', action='store_true',
                      help='Skip combined overview image')
    p_sc.add_argument('--no-single',   action='store_true',
                      help='Skip individual slot images')

    args = ap.parse_args()

    dev = open_device()
    cp(BLD+GRN, f"  Connected: {dev.manufacturer} — {dev.product}\n")

    if args.cmd == 'info':
        cmd_info(dev)
    elif args.cmd == 'dmm':
        cmd_dmm(dev, args)
    elif args.cmd == 'scope':
        cmd_scope(dev, args)
    else:
        interactive_menu(dev)

if __name__ == '__main__':
    main()
