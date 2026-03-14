"""
ET829 / MDS8209 — LIVE CAPTURE VIA MODE TOGGLE
===============================================
Instead of USB reset (which kills the device), this forces a fresh
ring buffer snapshot by cycling the device through DMM mode and back
to scope mode. The listen test showed the device pushes a status packet
when mode changes — this likely also re-arms the capture buffer.

Usage:
  python et829_modetoggle.py           # single capture
  python et829_modetoggle.py --loop    # continuous (~2-3s per frame)
  python et829_modetoggle.py --save out.csv
"""

import usb.core, usb.util, time, sys, struct, argparse, csv, datetime

VID, PID  = 0x2E88, 0x4603
EP_OUT    = 0x05
EP_BULK   = 0x84

VDIV_TABLE = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]

# ── USB helpers ──────────────────────────────────────────────────────────────

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("Device not found — check USB connection")
    try: dev.set_configuration()
    except: pass
    try: usb.util.claim_interface(dev, 0)
    except: pass
    return dev

def drain(dev, ms=50):
    while True:
        try: dev.read(EP_BULK, 512, timeout=ms)
        except: break

def xfer(dev, tx, max_bytes=8192, wait=0.15, read_timeout=300):
    try:
        drain(dev)
        dev.write(EP_OUT, tx, timeout=1000)
        time.sleep(wait)
        buf = bytearray()
        while True:
            try:
                buf.extend(dev.read(EP_BULK, 512, timeout=read_timeout))
                if len(buf) >= max_bytes: break
            except: break
        return bytes(buf) if buf else None
    except Exception as e:
        return None

# ── Capture logic ─────────────────────────────────────────────────────────────

def force_fresh_snapshot(dev):
    """
    Cycle: scope → DMM → scope.
    Each mode entry causes the device to reinitialise its state.
    After returning to scope we then immediately seek+read page 6.
    """
    drain(dev)

    # 1. Enter DMM mode
    r = xfer(dev, bytes([0x0D, 0x21]), wait=0.2, read_timeout=200)

    # 2. Short pause — device is now in DMM mode
    time.sleep(0.3)
    drain(dev)

    # 3. Re-enter scope mode  ← this is the key step
    r = xfer(dev, bytes([0x0D, 0x00]), wait=0.3, read_timeout=300)

    # 4. Also try arm command
    xfer(dev, bytes([0x0D, 0x01]), wait=0.2, read_timeout=200)

    drain(dev)

def read_page(dev, page=6):
    """Seek to page N and read it."""
    xfer(dev, bytes([0xA5, 0x22, page]), max_bytes=64, wait=0.1)
    time.sleep(0.05)
    return xfer(dev, bytes([0x00, 0x02]))

def read_all_pages(dev):
    """Read all 7 pages, return list of (page, seq, data)."""
    pages = []
    for p in range(7):
        xfer(dev, bytes([0xA5, 0x22, p]), max_bytes=64, wait=0.08)
        time.sleep(0.04)
        data = xfer(dev, bytes([0x00, 0x02]))
        seq  = None
        if data and len(data) >= 6 and data[0] == 0xA5:
            try:
                plen = struct.unpack_from('<H', data, 2)[0]
                if plen > 2:
                    seq = struct.unpack_from('<H', data, 4)[0]
            except: pass
        pages.append((p, seq, data))
    return pages

# ── Frame parsing ─────────────────────────────────────────────────────────────

def parse_frame(data):
    if not data or len(data) < 10 or data[0] != 0xA5:
        return None
    plen = struct.unpack_from('<H', data, 2)[0]
    if plen < 6: return None
    seq      = struct.unpack_from('<H', data, 4)[0]
    vdiv_ch2 = data[6]
    vdiv_ch1 = data[7]
    tb_idx   = data[8]
    ch_flags = data[9]
    samples  = data[10:]
    ch1_vdiv = VDIV_TABLE[vdiv_ch1] if vdiv_ch1 < len(VDIV_TABLE) else 1.0
    ch2_vdiv = VDIV_TABLE[vdiv_ch2] if vdiv_ch2 < len(VDIV_TABLE) else 1.0

    def to_v(raw, vdiv): return (raw - 128) / 128.0 * (vdiv * 4)
    ch1_v = [to_v(s, ch1_vdiv) for s in samples]

    return dict(seq=seq, ch1_vdiv=ch1_vdiv, ch2_vdiv=ch2_vdiv,
                tb_idx=tb_idx, ch_flags=ch_flags,
                samples=samples, ch1_volts=ch1_v, n=len(samples))

def stats(volts):
    if not volts: return 0, 0, 0
    vpp = max(volts) - min(volts)
    dc  = sum(volts) / len(volts)
    rms = (sum(v*v for v in volts) / len(volts)) ** 0.5
    return vpp, dc, rms

WIDTH, HEIGHT = 100, 20

def ascii_plot(volts, vdiv, title="CH1"):
    top, bot = vdiv * 4, -(vdiv * 4)
    step    = max(1, len(volts) // WIDTH)
    sampled = [volts[i*step] for i in range(min(WIDTH, len(volts)//step))]
    print(f"\n  {title}  ({len(volts)} samples, {vdiv}V/div)")
    for row in range(HEIGHT):
        row_v = top - (top - bot) * row / (HEIGHT - 1)
        line  = ""
        for v in sampled:
            norm = (v - bot) / (top - bot)
            r    = int((1.0 - norm) * (HEIGHT - 1))
            line += "█" if r == row else " "
        if   row == 0:        label = f" {top:+.3f}V │"
        elif row == HEIGHT-1: label = f" {bot:+.3f}V │"
        elif row == HEIGHT//2:label = f" {(top+bot)/2:+.3f}V │"
        else:                 label = "          │"
        print(label + line)
    print("          └" + "─" * len(sampled))
    vpp, dc, rms = stats(volts)
    print(f"  Vpp={vpp:.3f}V  Vrms={rms:.3f}V  DC={dc:.3f}V")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--loop',  action='store_true')
    ap.add_argument('--save',  metavar='FILE')
    ap.add_argument('--count', type=int, default=0)
    ap.add_argument('--page',  type=int, default=6, help='Ring buffer page to read (0-6)')
    ap.add_argument('--allpages', action='store_true', help='Show all 7 pages per capture')
    ap.add_argument('--delay', type=float, default=0.0, help='Extra delay between captures (s)')
    args = ap.parse_args()

    dev = find_dev()
    print(f"Connected: {dev.manufacturer} — {dev.product}")

    writer = None
    if args.save:
        f = open(args.save, 'w', newline='')
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'capture', 'seq', 'sample_idx', 'ch1_v'])

    n = 0
    last_seq = None
    try:
        while True:
            n += 1
            ts = time.strftime("%H:%M:%S")

            # Force fresh snapshot via mode toggle
            force_fresh_snapshot(dev)

            if args.allpages:
                pages = read_all_pages(dev)
                print(f"\n[{ts}] Capture #{n} — all pages:")
                for p, seq, data in pages:
                    frame = parse_frame(data) if data else None
                    if frame:
                        vpp, dc, rms = stats(frame['ch1_volts'])
                        marker = " *** NEW ***" if seq != last_seq else ""
                        print(f"  Page {p}: seq={seq}{marker}  Vpp={vpp:.3f}V  DC={dc:.3f}V")
                    else:
                        print(f"  Page {p}: seq={seq}  [no frame]")
            else:
                data  = read_page(dev, args.page)
                frame = parse_frame(data)
                if frame is None:
                    print(f"[{ts}] #{n}  [No frame]")
                else:
                    seq = frame['seq']
                    marker = "  *** NEW ***" if seq != last_seq else "  (same seq)"
                    print(f"\n[{ts}] Capture #{n}  seq={seq}{marker}  flags={frame['ch_flags']:02X}")
                    last_seq = seq
                    ascii_plot(frame['ch1_volts'], frame['ch1_vdiv'])

                    if writer:
                        now = datetime.datetime.now().isoformat()
                        for i, v in enumerate(frame['ch1_volts']):
                            writer.writerow([now, n, seq, i, f"{v:.4f}"])

            if args.delay > 0:
                time.sleep(args.delay)

            if not args.loop:
                break
            if args.count > 0 and n >= args.count:
                break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if writer:
            f.close()

if __name__ == '__main__':
    main()
