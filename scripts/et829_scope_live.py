"""
ET829 — Live scope capture (correct protocol)

PROTOCOL DISCOVERY:
  A5 22 XX = read page XX of CH1 ring buffer directly (returns waveform)
  A5 23 XX = read page XX of CH2 ring buffer
  A5 24 XX = read page XX of both channels
  00 02    = re-read current page (same as last A5 22 XX)

  The device continuously captures and writes new pages while running.
  Pages advance: 0, 1, 2, ... N (each ~256 frames apart in seq counter)
  To get live data: poll page N+1 until it appears, then read it.
  
  Page XX wraps at 0xFF (255 max).
"""
import usb.core, usb.util, time, struct, os, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; CYN="\033[96m"
BLD="\033[1m"; GRY="\033[90m"; MAG="\033[95m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT, EP_BULK = 0x05, 0x84

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
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def read_page(dev, ch, page):
    """Seek to page XX then read waveform with 00 02/03.
    A5 22 XX = seek CH1 to page XX (ack discarded)
    A5 23 XX = seek CH2 to page XX
    00 02 / 00 03 = read current page
    Returns (sub_header, samples) or (None, None).
    """
    cmd_byte = 0x22 if ch == 1 else 0x23
    read_cmd = bytes([0x00, 0x02]) if ch == 1 else bytes([0x00, 0x03])
    exp_byte = cmd_byte
    # Step 1: seek
    drain(dev)
    dev.write(EP_OUT, bytes([0xA5, cmd_byte, page]), timeout=500)
    time.sleep(0.05)
    try: dev.read(EP_BULK, 64, timeout=100)   # discard seek ack
    except: pass
    # Step 2: read
    drain(dev)
    dev.write(EP_OUT, read_cmd, timeout=500)
    time.sleep(0.15)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=200))
        except: break
    if len(buf) < 10 or buf[0] != 0xA5 or buf[1] != exp_byte:
        return None, None
    plen = struct.unpack_from('<H', buf, 2)[0]
    payload = buf[4:4+plen]
    if len(payload) < 7: return None, None
    return payload[:6], list(payload[6:])

def find_latest_page(dev, ch=1):
    """Scan all pages, return the one with the highest seq counter."""
    best_page = None
    best_seq  = -1
    print("  Scanning pages", end="", flush=True)
    for pg in range(0, 256):
        hdr, samp = read_page(dev, ch, pg)
        if hdr is not None:
            seq = struct.unpack_from('<H', bytes(hdr), 0)[0]
            print(".", end="", flush=True)
            if seq > best_seq:
                best_seq  = seq
                best_page = pg
        else:
            if best_page is not None and pg > best_page + 3:
                break  # 3 misses past best = done
            if best_page is None and pg > 10:
                break  # no data at all
    print()
    return best_page, best_seq

def ascii_waveform(samples, vdiv=1.0, width=100, height=20, label=""):
    if not samples: return
    step = max(1, len(samples) // width)
    ds = [samples[i*step] for i in range(min(width, len(samples)//max(1,step)))]
    ndivs = 8
    fullscale = vdiv * ndivs / 2
    grid = [[' ']*len(ds) for _ in range(height)]
    zero_row = height // 2
    for x in range(len(ds)):
        grid[zero_row][x] = '─'
    for x, val in enumerate(ds):
        row = int((255 - val) / 255.0 * (height - 1))
        row = max(0, min(height-1, row))
        grid[row][x] = '█'
    vdiv_str = VDIV_TABLE.get(next((k for k,v in VDIV_VOLTS.items() if v==vdiv), -1), f"{vdiv}V")
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
    mn, mx = min(samples), max(samples)
    cp(CYN, f"  Vpp={vpp:.3f}V  Vrms={vrms:.3f}V  DC={vdc:+.3f}V  raw min={mn} max={mx}")
    print()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ch",   type=int, default=1, choices=[1,2])
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--raw",  action="store_true")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")

    cp(YLW, "Finding latest page...")
    cur_page, cur_seq = find_latest_page(dev, args.ch)
    if cur_page is None:
        cp(RED, "No data found. Is device in scope mode? Try replugging USB.")
        return
    cp(GRN, f"Latest page = {cur_page}  seq={cur_seq}. Polling for new pages...\n")

    count = 0
    saved = False
    try:
        while True:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            next_page = (cur_page + 1) & 0xFF

            hdr, samples = read_page(dev, args.ch, next_page)
            if samples:
                cur_page = next_page
                vdiv_idx = hdr[3] if args.ch == 1 else hdr[2]
                vdiv = VDIV_VOLTS.get(vdiv_idx, 1.0)
                seq = struct.unpack_from('<H', bytes(hdr), 0)[0]
                if args.raw:
                    cp(GRY, f"page={cur_page}  seq={seq}  "
                            f"hdr={' '.join(f'{b:02X}' for b in hdr)}  "
                            f"V/div={VDIV_TABLE.get(vdiv_idx,'?')}")
                ascii_waveform(samples, vdiv, label=f"[{ts}] CH{args.ch} page={cur_page}")
                count += 1
                if args.save and not saved:
                    fname = f"et829_ch{args.ch}_{datetime.now().strftime('%H%M%S')}.csv"
                    with open(fname,'w') as f:
                        f.write("index,raw,voltage\n")
                        for i,s in enumerate(samples):
                            v = (s-128)/128.0*vdiv*4
                            f.write(f"{i},{s},{v:.5f}\n")
                    cp(GRN, f"Saved: {fname}")
                    saved = True
                if not args.loop:
                    break
            else:
                # No new page yet — re-read current page to show latest
                hdr, samples = read_page(dev, args.ch, cur_page)
                if samples:
                    vdiv_idx = hdr[3] if args.ch == 1 else hdr[2]
                    vdiv = VDIV_VOLTS.get(vdiv_idx, 1.0)
                    if args.raw:
                        cp(GRY, f"[{ts}] page={cur_page} (waiting for page {next_page}...)")
                    ascii_waveform(samples, vdiv,
                                   label=f"[{ts}] CH{args.ch} page={cur_page} (waiting...)")
                    count += 1
                else:
                    cp(RED, f"[{ts}] No data")
                if not args.loop:
                    break
                time.sleep(0.2)

    except KeyboardInterrupt: print()
    cp(CYN, f"\nDone. {count} captures.")

if __name__ == "__main__":
    main()
