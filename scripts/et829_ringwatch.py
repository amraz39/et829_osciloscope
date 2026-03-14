"""
ET829 / MDS8209 — RING BUFFER CHANGE WATCHER
=============================================
Repeatedly reads ALL 7 ring buffer pages and reports if ANY seq counter
changes. If the device ever writes new data to the ring buffer while USB
is connected, we'll see a seq change here.

Also logs the exact moment you press AUTO (you'll see "No data" gaps).
After the gap ends, the new seq values should appear if anything changed.

Usage:
  python et829_ringwatch.py
"""

import usb.core, usb.util, time, sys, struct

VID, PID  = 0x2E88, 0x4603
EP_OUT    = 0x05
EP_BULK   = 0x84

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("Device not found")
    try: dev.set_configuration()
    except: pass
    try: usb.util.claim_interface(dev, 0)
    except: pass
    return dev

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

def seek_page(dev, page):
    """Seek ring buffer to page N via A5 22 NN"""
    resp = xfer(dev, bytes([0xA5, 0x22, page]))
    time.sleep(0.05)

def read_page(dev):
    """Read current ring buffer page via 00 02"""
    resp = xfer(dev, bytes([0x00, 0x02]))
    if resp and len(resp) >= 6 and resp[0] == 0xA5:
        try:
            plen = struct.unpack_from('<H', resp, 2)[0]
            if plen > 2:
                seq = struct.unpack_from('<H', resp, 4)[0]
                return seq, resp
        except: pass
    return None, resp

def snapshot(dev):
    """Read all 7 pages and return list of (page, seq)"""
    result = []
    for page in range(7):
        seek_page(dev, page)
        seq, _ = read_page(dev)
        result.append((page, seq))
    return result

def main():
    dev = find_dev()
    print(f"Connected: {dev.manufacturer} — {dev.product}")
    print("Reading initial ring buffer state...")

    baseline = snapshot(dev)
    print("\nBaseline ring buffer pages:")
    for page, seq in baseline:
        print(f"  Page {page}: seq={seq}")
    
    print("\nNow watching for changes — press AUTO on the device repeatedly")
    print("Ctrl+C to stop\n")

    iteration = 0
    while True:
        iteration += 1
        current = snapshot(dev)
        
        changed = []
        for (page, seq), (_, baseline_seq) in zip(current, baseline):
            if seq != baseline_seq:
                changed.append((page, baseline_seq, seq))
        
        ts = time.strftime("%H:%M:%S")
        if changed:
            print(f"\n*** [{ts}] CHANGE DETECTED! ***")
            for page, old_seq, new_seq in changed:
                print(f"  Page {page}: {old_seq} → {new_seq}")
            baseline = current
        else:
            seqs = [str(s) if s is not None else '?' for _, s in current]
            print(f"[{ts}] iter={iteration}  seqs={seqs}  (no change)", end='\r')
        
        time.sleep(0.5)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nStopped.")
