"""
ET829 / MDS8209 — PASSIVE LISTENER
===================================
Sends ONE arm command (0D 01) then just READS EP4 with no further writes.
If the device pushes waveform data spontaneously (e.g. after trigger fires),
we'll catch it here. Run, then press AUTO on the device a few times.

Usage:
  python et829_listen.py
"""

import usb.core, usb.util, time, sys

VID, PID    = 0x2E88, 0x4603
EP_OUT      = 0x05
EP_BULK     = 0x84
LISTEN_SECS = 120      # listen for this long

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("Device not found")
    try:
        dev.set_configuration()
    except:
        pass
    try:
        usb.util.claim_interface(dev, 0)
    except:
        pass
    return dev

def drain(dev):
    while True:
        try: dev.read(EP_BULK, 512, timeout=50)
        except: break

def main():
    dev = find_dev()
    print(f"Connected: {dev.manufacturer} — {dev.product}")

    # Drain any stale data
    drain(dev)

    # Send ONE arm command then go silent
    print("Sending arm (0D 01) then going silent — press AUTO on device...")
    dev.write(EP_OUT, bytes([0x0D, 0x01]), timeout=1000)
    time.sleep(0.1)
    drain(dev)

    # Also try scope mode command
    dev.write(EP_OUT, bytes([0x0D, 0x00]), timeout=1000)
    time.sleep(0.1)
    drain(dev)

    print(f"Listening on EP4 for {LISTEN_SECS}s — any spontaneous pushes will be shown\n")

    total = 0
    deadline = time.time() + LISTEN_SECS
    while time.time() < deadline:
        try:
            chunk = bytes(dev.read(EP_BULK, 4096, timeout=2000))
            if chunk:
                ts = time.strftime("%H:%M:%S")
                total += len(chunk)
                print(f"[{ts}] Got {len(chunk)} bytes  hex: {chunk[:20].hex()}  (total {total})")
                # Try to decode if it's a waveform frame
                if len(chunk) >= 6 and chunk[0] == 0xA5:
                    cmd  = chunk[1]
                    plen = int.from_bytes(chunk[2:4], 'little')
                    print(f"         A5 {cmd:02X}  plen={plen}")
                    if plen > 2 and len(chunk) >= 10:
                        seq = int.from_bytes(chunk[4:6], 'little')
                        print(f"         seq={seq}")
        except usb.core.USBTimeoutError:
            remaining = int(deadline - time.time())
            print(f"\r  [waiting... {remaining}s left]       ", end='', flush=True)
        except Exception as e:
            print(f"\nError: {e}")
            break

    print(f"\n\nDone. Total bytes received passively: {total}")

if __name__ == '__main__':
    main()
