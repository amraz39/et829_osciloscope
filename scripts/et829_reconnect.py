"""
ET829 / MDS8209 — CAPTURE BY USB RESET
=======================================
The device freezes its ring buffer at the moment of USB connection.
This tool exploits that: it resets the USB device, waits for the
device to re-enumerate and capture a fresh buffer, then reads page 6
(the newest capture), then resets again.

This is NOT true real-time streaming — it's ~1 capture per 3-5 seconds —
but it IS genuinely fresh data each time.

Usage:
  python et829_reconnect.py [--ch1 | --ch2 | --both]
  python et829_reconnect.py --loop
  python et829_reconnect.py --save captures.csv
"""

import usb.core, usb.util, time, sys, struct, argparse, csv, datetime

VID, PID  = 0x2E88, 0x4603
EP_OUT    = 0x05
EP_BULK   = 0x84

VDIV_TABLE = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]

def find_dev(timeout=10.0):
    """Wait for device to appear, return it or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is not None:
            return dev
        time.sleep(0.2)
    return None

def connect(dev):
    try: dev.set_configuration()
    except: pass
    try: usb.util.claim_interface(dev, 0)
    except: pass

def drain(dev):
    while True:
        try: dev.read(EP_BULK, 512, timeout=50)
        except: break

def xfer(dev, tx, max_bytes=8192):
    try:
        drain(dev)
        dev.write(EP_OUT, tx, timeout=1000)
        time.sleep(0.15)
        buf = bytearray()
        while True:
            try:
                buf.extend(dev.read(EP_BULK, 512, timeout=300))
                if len(buf) >= max_bytes: break
            except: break
        return bytes(buf) if buf else None
    except: return None

def reset_device(dev):
    """Reset the USB device so it re-enumerates with a fresh buffer."""
    try:
        dev.reset()
    except:
        pass
    usb.util.dispose_resources(dev)

def read_latest_page(dev):
    """Seek to page 6 (newest) and read it."""
    # Seek to page 6
    xfer(dev, bytes([0xA5, 0x22, 0x06]), max_bytes=64)
    time.sleep(0.1)
    # Read it
    resp = xfer(dev, bytes([0x00, 0x02]))
    return resp

def parse_frame(data, channel_hint=1):
    if not data or len(data) < 10 or data[0] != 0xA5:
        return None
    cmd  = data[1]
    plen = struct.unpack_from('<H', data, 2)[0]
    if plen < 6:
        return None
    seq      = struct.unpack_from('<H', data, 4)[0]
    vdiv_ch2 = data[6]
    vdiv_ch1 = data[7]
    tb_idx   = data[8]
    ch_flags = data[9]
    samples  = data[10:]

    ch1_vdiv = VDIV_TABLE[vdiv_ch1] if vdiv_ch1 < len(VDIV_TABLE) else 1.0
    ch2_vdiv = VDIV_TABLE[vdiv_ch2] if vdiv_ch2 < len(VDIV_TABLE) else 1.0

    def to_volts(raw, vdiv):
        return (raw - 128) / 128.0 * (vdiv * 4)

    ch1_volts = [to_volts(s, ch1_vdiv) for s in samples]
    ch2_volts = None

    return {
        'seq': seq, 'cmd': cmd,
        'ch1_vdiv': ch1_vdiv, 'ch2_vdiv': ch2_vdiv,
        'tb_idx': tb_idx, 'ch_flags': ch_flags,
        'samples': samples,
        'ch1_volts': ch1_volts,
        'ch2_volts': ch2_volts,
        'n': len(samples),
    }

def stats(volts):
    if not volts: return None, None, None
    vmin = min(volts)
    vmax = max(volts)
    vpp  = vmax - vmin
    dc   = sum(volts) / len(volts)
    rms  = (sum(v*v for v in volts) / len(volts)) ** 0.5
    return vpp, dc, rms

WIDTH = 100
HEIGHT = 20

def ascii_plot(volts, vdiv, title="CH1"):
    if not volts: return
    vmin, vmax = min(volts), max(volts)
    fullscale = vdiv * 4
    top    =  fullscale
    bottom = -fullscale

    step = len(volts) // WIDTH
    sampled = [volts[i*step] for i in range(WIDTH)]

    print(f"\n  {title}  ({len(volts)} samples, {vdiv}V/div)")
    for row in range(HEIGHT):
        row_v = top - (top - bottom) * row / (HEIGHT - 1)
        line = ""
        for v in sampled:
            norm = (v - bottom) / (top - bottom)
            r    = int((1.0 - norm) * (HEIGHT - 1))
            line += "█" if r == row else " "
        if row == 0:
            label = f" {top:+.3f}V │"
        elif row == HEIGHT - 1:
            label = f" {bottom:+.3f}V │"
        elif row == HEIGHT // 2:
            mid = (top + bottom) / 2
            label = f" {mid:+.3f}V │"
        else:
            label = "          │"
        print(label + line)
    print("          └" + "─" * WIDTH)

    vpp, dc, rms = stats(volts)
    print(f"  Vpp={vpp:.3f}V  Vrms={rms:.3f}V  DC={dc:.3f}V")

def capture_once(args):
    """Full cycle: reset → wait → connect → read → return frame."""
    # Find and reset current device if present
    dev = find_dev(timeout=3.0)
    if dev:
        reset_device(dev)
        time.sleep(1.5)  # wait for device to re-enumerate

    # Wait for device to come back
    dev = find_dev(timeout=8.0)
    if dev is None:
        print("  [Device not found after reset]")
        return None, None

    connect(dev)
    time.sleep(0.3)  # let device finish buffer fill

    # Read page 6 (latest capture)
    data = read_latest_page(dev)
    ts   = datetime.datetime.now()
    frame = parse_frame(data)
    return frame, ts

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--loop',  action='store_true', help='Loop continuously')
    p.add_argument('--save',  metavar='FILE',      help='Save CSV of ch1 samples')
    p.add_argument('--count', type=int, default=0, help='Number of captures (0=infinite)')
    args = p.parse_args()

    writer = None
    csvfile = None
    if args.save:
        csvfile = open(args.save, 'w', newline='')
        writer  = csv.writer(csvfile)
        writer.writerow(['timestamp', 'seq', 'sample_idx', 'ch1_volts'])

    n = 0
    last_seq = None
    try:
        while True:
            n += 1
            ts_str = time.strftime("%H:%M:%S")
            print(f"\n[{ts_str}] Capture #{n} — resetting device...", flush=True)

            frame, ts = capture_once(args)

            if frame is None:
                print("  [No frame]")
            else:
                seq = frame['seq']
                changed = "  *** NEW ***" if seq != last_seq else "  (same seq)"
                print(f"  seq={seq}{changed}  ch_flags={frame['ch_flags']:02X}")
                last_seq = seq

                ascii_plot(frame['ch1_volts'], frame['ch1_vdiv'], "CH1")

                if writer:
                    for i, v in enumerate(frame['ch1_volts']):
                        writer.writerow([ts.isoformat(), seq, i, f"{v:.4f}"])
                    csvfile.flush()

            if not args.loop and (args.count == 0 or n >= args.count):
                if not args.loop and n >= 1 and args.count == 0:
                    break  # single capture by default
            if args.count > 0 and n >= args.count:
                break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if csvfile:
            csvfile.close()

if __name__ == '__main__':
    main()
