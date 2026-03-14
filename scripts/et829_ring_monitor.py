"""
ET829 — Ring buffer monitor
Theory: after page 6, device wraps to page 0 and overwrites with new seq.
Monitor all 7 pages continuously. When ANY page's seq changes = new data!
"""
import usb.core, usb.util, time, struct, os
from datetime import datetime

os.system("")
GRN="\033[92m"; YLW="\033[93m"; GRY="\033[90m"; CYN="\033[96m"
BLD="\033[1m"; RED="\033[91m"; RST="\033[0m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT, EP_BULK = 0x05, 0x84

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev: print("Not found!"); exit()
for i in range(3):
    try:
        if dev.is_kernel_driver_active(i): dev.detach_kernel_driver(i)
    except: pass
try: dev.set_configuration()
except: pass
cp(GRN, "Connected.\n")

def drain():
    while True:
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def seek_read(page):
    """Seek page, read CH1. Returns (seq, samples[:5]) or (None, None)."""
    drain()
    dev.write(EP_OUT, bytes([0xA5, 0x22, page]), timeout=500)
    time.sleep(0.05)
    try: dev.read(EP_BULK, 64, timeout=100)
    except: pass
    drain()
    dev.write(EP_OUT, bytes([0x00, 0x02]), timeout=500)
    time.sleep(0.15)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=200))
        except: break
    if len(buf) < 10 or buf[0] != 0xA5 or buf[1] != 0x22: return None, None
    plen = struct.unpack_from('<H', buf, 2)[0]
    p = buf[4:4+plen]
    seq = struct.unpack_from('<H', p, 0)[0]
    return seq, list(p[6:11])

# Snapshot all pages
cp(YLW, "Snapshotting all pages 0-8...")
known = {}
for pg in range(9):
    seq, samp = seek_read(pg)
    if seq is not None:
        known[pg] = seq
        cp(GRY, f"  page {pg}: seq={seq}")
    else:
        cp(GRY, f"  page {pg}: no data")

cp(YLW, f"\nMonitoring {len(known)} pages for any seq change...")
cp(YLW, "CHANGE AWG SIGNAL NOW and watch for changes!\n")

iteration = 0
while True:
    iteration += 1
    ts = datetime.now().strftime("%H:%M:%S")
    changes = []
    for pg in sorted(known.keys()):
        seq, samp = seek_read(pg)
        if seq is not None and seq != known[pg]:
            changes.append((pg, known[pg], seq, samp))
            known[pg] = seq
        # Also check pages just beyond known range
    # Check page 7 and 8 too
    for pg in [7, 8]:
        if pg not in known:
            seq, samp = seek_read(pg)
            if seq is not None:
                known[pg] = seq
                changes.append((-1, -1, seq, samp))  # new page!
                cp(GRN+BLD, f"  NEW PAGE {pg} appeared! seq={seq} samp={samp}")

    if changes:
        for pg, old_seq, new_seq, samp in changes:
            if pg >= 0:
                cp(GRN+BLD, f"[{ts}] PAGE {pg} CHANGED: seq {old_seq}→{new_seq}  samp={samp}")
    else:
        seqs = [f"p{pg}:{known[pg]}" for pg in sorted(known)]
        print(f"\r[{ts}] iter={iteration}  {' '.join(seqs)}  (no change)", end="", flush=True)

    time.sleep(0.5)
