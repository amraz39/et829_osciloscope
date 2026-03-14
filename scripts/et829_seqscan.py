"""
ET829 — SEQ SCANNER
====================
Instead of seeking to page index 0-6, seeks to seq counter values
BEYOND our known range (>1758) to find where the device is currently
writing. If the ring buffer is live, higher seq values should exist.

Also scans for what happens when we seek to very large values.
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

def xfer(dev, tx, max_bytes=8192):
    drain(dev)
    dev.write(EP_OUT, tx, timeout=500)
    time.sleep(0.15)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=250))
        except: break
    return bytes(buf) if buf else None

def parse(data):
    if not data or len(data) < 6 or data[0] != 0xA5: return None, None
    try:
        plen = struct.unpack_from('<H', data, 2)[0]
        if plen > 2:
            seq = struct.unpack_from('<H', data, 4)[0]
            return seq, data
    except: pass
    return None, None

dev = find_dev()
print(f"Connected: {dev.manufacturer} — {dev.product}\n")

# Enter scope mode
xfer(dev, bytes([0x0D, 0x00]))
time.sleep(0.3)
drain(dev)

print("Known pages: seqs 222, 478, 734, 990, 1246, 1502, 1758 (step=256, pages 0-6)")
print("Scanning for seqs BEYOND 1758 (up to +2048 more)...\n")

# Scan beyond known range - try A5 22 with values 7..255
# (currently we only tried 0-6; the seek byte might be a seq index not a page number)
found_new = []

print("--- Scanning seek byte 0x07 to 0xFF ---")
for seek_val in range(7, 256, 1):
    resp = xfer(dev, bytes([0xA5, 0x22, seek_val]), max_bytes=64)
    time.sleep(0.03)
    data = xfer(dev, bytes([0x00, 0x02]))
    seq, _ = parse(data)
    if seq is not None and seq not in (222, 478, 734, 990, 1246, 1502, 1758):
        print(f"  seek=0x{seek_val:02X} ({seek_val:3d})  -> seq={seq}  *** NEW SEQ ***")
        found_new.append((seek_val, seq))
    elif seq is not None:
        print(f"  seek=0x{seek_val:02X} ({seek_val:3d})  -> seq={seq}")
    else:
        print(f"  seek=0x{seek_val:02X} ({seek_val:3d})  -> [no data]")

print(f"\nNew seqs found: {found_new}")
