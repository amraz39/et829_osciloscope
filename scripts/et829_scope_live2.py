"""
ET829 — Live scope capture (ring buffer fast poll)

The device has a 7-slot ring buffer (pages 0-6).
After filling page 6 it wraps to page 0 and overwrites with new data.
Each new write briefly changes the page's seq counter.
We poll all 7 pages as fast as possible and grab any that changed.

Usage:
  python et829_scope_live2.py          # single capture
  python et829_scope_live2.py --loop   # continuous
  python et829_scope_live2.py --raw    # show seq/header info
  python et829_scope_live2.py --ch 2   # CH2
"""
import usb.core, usb.util, time, struct, os, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; CYN="\033[96m"
BLD="\033[1m"; GRY="\033[90m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT, EP_BULK = 0x05, 0x84
PAGES = list(range(7))  # pages 0-6

VDIV_VOLTS = {0:0.01,1:0.02,2:0.05,3:0.1,4:0.2,5:0.5,6:1.0,7:2.0,8:5.0,9:10.0}
VDIV_TABLE = {0:"10mV",1:"20mV",2:"50mV",3:"100mV",4:"200mV",
              5:"500mV",6:"1V",7:"2V",8:"5V",9:"10V"}

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None: raise RuntimeError("Device not found!")
    for i in range(3):
        try:
            if dev.is_kernel_driver_active(i): dev.detach_kernel_driver(i)
        except: pass
    try: dev.set_configuration()
    except: pass
    return dev

def drain(dev):
    while True:
        try: dev.read(EP_BULK, 512, timeout=30)
        except: break

def seek_read(dev, ch, page):
    """Seek to page, read channel. Returns (seq, hdr, samples) or None."""
    cmd_seek = 0x22 if ch == 1 else 0x23
    cmd_read = bytes([0x00, 0x02]) if ch == 1 else bytes([0x00, 0x03])
    exp = cmd_seek
    drain(dev)
    dev.write(EP_OUT, bytes([0xA5, cmd_seek, page]), timeout=500)
    time.sleep(0.04)
    try: dev.read(EP_BULK, 64, timeout=80)
    except: pass
    drain(dev)
    dev.write(EP_OUT, cmd_read, timeout=500)
    time.sleep(0.12)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=180))
        except: break
    if len(buf) < 10 or buf[0] != 0xA5 or buf[1] != exp: return None
    plen = struct.unpack_from('<H', buf, 2)[0]
    p = buf[4:4+plen]
    if len(p) < 7: return None
    seq = struct.unpack_from('<H', p, 0)[0]
    return seq, p[:6], list(p[6:])

def ascii_waveform(samples, vdiv=1.0, width=100, height=20, label=""):
    if not samples: return
    step = max(1, len(samples) // width)
    ds = [samples[i*step] for i in range(min(width, len(samples)//max(1,step)))]
    fullscale = vdiv * 4  # 8 divs total, 4 each side
    grid = [[' ']*len(ds) for _ in range(height)]
    zero_row = height // 2
    for x in range(len(ds)):
        grid[zero_row][x] = '─'
    for x, val in enumerate(ds):
        row = int((255 - val) / 255.0 * (height - 1))
        grid[max(0, min(height-1, row))][x] = '█'
    vdiv_str = VDIV_TABLE.get(
        next((k for k,v in VDIV_VOLTS.items() if abs(v-vdiv)<1e-9), -1), f"{vdiv}V")
    print()
    cp(BLD+CYN, f"  {label}  ({len(samples)} samples, {vdiv_str}/div)")
    for r in range(height):
        v = fullscale * (1 - 2*r/(height-1))
        show = (r % (height//4) == 0)
        prefix = f"{v:+7.3f}V │" if show else "          │"
        col = GRY if r == zero_row else (GRN if r < zero_row else YLW)
        cp(col, prefix + ''.join(grid[r]))
    cp(GRY, "          └" + "─"*len(ds))
    vsamps = [(s-128)/128.0*fullscale for s in samples]
    vpp  = max(vsamps)-min(vsamps)
    vrms = (sum(v**2 for v in vsamps)/len(vsamps))**0.5
    vdc  = sum(vsamps)/len(vsamps)
    cp(CYN, f"  Vpp={vpp:.3f}V  Vrms={vrms:.3f}V  DC={vdc:+.3f}V  "
            f"raw min={min(samples)} max={max(samples)}")
    print()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ch",   type=int, default=1, choices=[1,2])
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--raw",  action="store_true")
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")

    # Snapshot all pages
    cp(YLW, "Snapshotting pages...")
    known_seq = {}
    latest_page = 0
    latest_seq  = -1
    for pg in PAGES:
        r = seek_read(dev, args.ch, pg)
        if r:
            seq, hdr, samp = r
            known_seq[pg] = seq
            if seq > latest_seq:
                latest_seq  = seq
                latest_page = pg
            cp(GRY, f"  page {pg}: seq={seq}")
    if not known_seq:
        cp(RED, "No data. Is device in scope mode?")
        return
    cp(GRN, f"Latest page={latest_page} seq={latest_seq}. Polling all pages for changes...\n")

    # Show latest page immediately
    r = seek_read(dev, args.ch, latest_page)
    if r:
        seq, hdr, samp = r
        vdiv_idx = hdr[3] if args.ch == 1 else hdr[2]
        vdiv = VDIV_VOLTS.get(vdiv_idx, 1.0)
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if args.raw:
            cp(GRY, f"page={latest_page} seq={seq} hdr={hdr.hex().upper()} V/div={VDIV_TABLE.get(vdiv_idx,'?')}")
        ascii_waveform(samp, vdiv, label=f"[{ts}] CH{args.ch} page={latest_page}")

    if not args.loop:
        return

    # Fast poll loop: check all pages, display any that changed
    poll_page = latest_page  # start polling from latest
    displayed = 0
    try:
        while True:
            # Round-robin through pages starting from latest
            for pg in [latest_page] + [p for p in PAGES if p != latest_page]:
                r = seek_read(dev, args.ch, pg)
                if r:
                    seq, hdr, samp = r
                    old_seq = known_seq.get(pg, -1)
                    if seq != old_seq:
                        known_seq[pg] = seq
                        # New data on this page!
                        vdiv_idx = hdr[3] if args.ch == 1 else hdr[2]
                        vdiv = VDIV_VOLTS.get(vdiv_idx, 1.0)
                        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        if args.raw:
                            cp(GRY, f"page={pg} seq={old_seq}→{seq} hdr={bytes(hdr).hex().upper()}")
                        ascii_waveform(samp, vdiv,
                                       label=f"[{ts}] CH{args.ch} page={pg} NEW")
                        displayed += 1
                        latest_page = pg  # prioritize this page next
                        break  # show it, then restart poll
            else:
                # No change found on any page — small yield
                time.sleep(0.05)

    except KeyboardInterrupt: print()
    cp(CYN, f"\nDone. {displayed} live captures.")

if __name__ == "__main__":
    main()
