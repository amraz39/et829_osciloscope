"""
ET829 — PAGE COUNT WATCHER
===========================
Continuously checks seek bytes 0x00 through 0x1F and reports
which ones return data. If you save a new capture on the device,
a new page should appear (seek=0x0C → seq=3294 if the pattern holds).

Run this, then press the SAVE button on the device, and watch for
"NEW PAGE FOUND" to appear.
"""

import usb.core, usb.util, time, sys, struct

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None: sys.exit("Not found")
    try: dev.set_configuration()
    except: pass
    try: usb.util.claim_interface(dev, 0)
    except: pass
    return dev

def drain(dev):
    while True:
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def xfer(dev, tx, max_bytes=512):
    drain(dev)
    dev.write(EP_OUT, tx, timeout=500)
    time.sleep(0.12)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=200))
        except: break
    return bytes(buf) if buf else None

def get_seq(data):
    if data and len(data) >= 6 and data[0] == 0xA5:
        try:
            plen = struct.unpack_from('<H', data, 2)[0]
            if plen > 2:
                return struct.unpack_from('<H', data, 4)[0]
        except: pass
    return None

def scan_pages(dev, max_page=0x1F):
    found = {}
    for p in range(max_page + 1):
        xfer(dev, bytes([0xA5, 0x22, p]), max_bytes=64)
        time.sleep(0.04)
        data = xfer(dev, bytes([0x00, 0x02]))
        seq  = get_seq(data)
        if seq is not None:
            found[p] = seq
    return found

dev = find_dev()
print(f"Connected: {dev.manufacturer} — {dev.product}")
print("\nScanning initial page map...")

baseline = scan_pages(dev)
print(f"\nFound {len(baseline)} pages:")
for p, seq in sorted(baseline.items()):
    print(f"  seek=0x{p:02X} ({p:2d})  seq={seq}")

print(f"\nExpected next page: seek=0x{len(baseline):02X}, seq={max(baseline.values())+256}")
print("\nNow PRESS SAVE on the device — watching for new pages...\n")

iteration = 0
while True:
    iteration += 1
    current = scan_pages(dev)
    new_pages = {p: s for p, s in current.items() if p not in baseline}
    lost_pages = {p: s for p, s in baseline.items() if p not in current}

    ts = time.strftime("%H:%M:%S")
    if new_pages:
        print(f"\n*** [{ts}] NEW PAGE(S) FOUND! ***")
        for p, seq in new_pages.items():
            print(f"  seek=0x{p:02X} ({p})  seq={seq}")
        baseline = current
    elif lost_pages:
        print(f"[{ts}] iter={iteration}  pages={len(current)}  (lost: {list(lost_pages.keys())})", end='\r')
    else:
        print(f"[{ts}] iter={iteration}  pages={len(current)}  seqs={list(current.values())}  (no change)", end='\r')
    
    time.sleep(1.0)
