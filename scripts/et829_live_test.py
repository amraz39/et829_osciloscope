"""
ET829 — Live page test
A5 22 XX seeks to page XX. Page 6 = newest.
Test if re-seeking to page 6 gives fresh live data.
"""
import usb.core, usb.util, time, struct, os

os.system("")
GRN="\033[92m"; YLW="\033[93m"; GRY="\033[90m"; CYN="\033[96m"; RST="\033[0m"; BLD="\033[1m"
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

def seek_and_read(page):
    """Seek to page, then read CH1. Returns (seq, samples) or None."""
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
    return struct.unpack_from('<H', p, 0)[0], list(p[6:p[6:].index(p[6])+30] if False else p[6:36])

# First: find max valid page
cp(YLW, "Finding max valid page...")
max_page = 0
for pg in range(0, 20):
    drain()
    dev.write(EP_OUT, bytes([0xA5, 0x22, pg]), timeout=500)
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
    ok = len(buf) > 100 and buf[0] == 0xA5 and buf[1] == 0x22
    plen = struct.unpack_from('<H', buf, 2)[0] if ok else 0
    seq = struct.unpack_from('<H', buf[4:4+plen], 0)[0] if ok and plen > 2 else None
    cp(GRY if not ok else GRN, f"  page {pg:2d}: {'OK' if ok else 'NO'}  seq={seq}  bytes={len(buf)}")
    if ok: max_page = pg
    else:
        if pg > 2: break  # stop after first miss

cp(YLW, f"\nMax valid page = {max_page}. Now reading page {max_page} repeatedly...")
cp(YLW, "CHANGE YOUR AWG SIGNAL while this runs!\n")

prev_samples = None
for i in range(20):
    seq, samples = seek_and_read(max_page)
    if samples:
        s5 = samples[:5]
        changed = s5 != prev_samples if prev_samples else False
        cp(GRN if changed else GRY,
           f"  [{i:2d}] seq={seq}  samples={s5}  {'*** DATA CHANGED! ***' if changed else ''}")
        prev_samples = s5
    else:
        cp(GRY, f"  [{i:2d}] no data")
    time.sleep(0.5)

cp(CYN, "\n=== If samples never changed: try reading page that doesn't exist yet ===")
cp(YLW, f"Trying page {max_page+1} (beyond buffer)...")
seq, samples = seek_and_read(max_page + 1)
cp(GRY, f"  page {max_page+1}: seq={seq} samples={samples[:5] if samples else None}")

cp(CYN, "\n=== Try 0xFF = 'live' page? ===")
seq, samples = seek_and_read(0xFF)
cp(GRY, f"  page FF: seq={seq} samples={samples[:5] if samples else None}")
