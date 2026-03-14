"""
ET829 — RAW POLL: 00 02 with NO seek
======================================
Tests if 00 02 without any A5 22 seek reads the live write head.
If the ring buffer is continuously running, seq should increment here.

Run this and change your AWG frequency/amplitude while it runs.
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

def poll(dev, cmd):
    drain(dev)
    dev.write(EP_OUT, cmd, timeout=500)
    time.sleep(0.12)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=200))
        except: break
    return bytes(buf) if buf else None

def seq_of(data):
    if data and len(data) >= 6 and data[0] == 0xA5:
        try:
            plen = struct.unpack_from('<H', data, 2)[0]
            if plen > 2:
                return struct.unpack_from('<H', data, 4)[0]
        except: pass
    return None

dev = find_dev()
print(f"Connected: {dev.manufacturer} — {dev.product}\n")
print("Polling 00 02 with NO seek. Change AWG signal while running.")
print("Watch for seq to change. Ctrl+C to stop.\n")

# First: enter scope mode cleanly
poll(dev, bytes([0x0D, 0x00]))
time.sleep(0.3)
drain(dev)

last = None
n = 0
while True:
    try:
        # Test 1: plain 00 02
        data = poll(dev, bytes([0x00, 0x02]))
        seq  = seq_of(data)
        n   += 1
        ts   = time.strftime("%H:%M:%S.") + f"{int(time.time()*1000)%1000:03d}"
        mark = "" if seq == last else "  *** CHANGED ***"
        print(f"[{ts}] n={n:4d}  00 02  seq={seq}{mark}")
        last = seq
        time.sleep(0.5)
    except KeyboardInterrupt:
        break

print("\nDone.")
