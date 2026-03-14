"""
ET829 CH1-button command finder.
Run while device is in scope mode. Tries 0D 02..0D 1F and reports
any response that looks like it initialises the ring buffer
(i.e. a seek to page 0 returns waveform data afterwards).

Usage: python et829_probe_ch1.py
"""
import usb.core, usb.util, time, struct, sys

VID, PID = 0x2E88, 0x4603
EP_OUT = 0x05; EP_BULK = 0x84

def open_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if not dev: sys.exit("Device not found")
    try: dev.set_configuration()
    except: pass
    try: usb.util.claim_interface(dev, 0)
    except: pass
    return dev

def drain(dev):
    for _ in range(16):
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def seek0(dev):
    """Try seek to page 0, return True if we get waveform data back."""
    try:
        drain(dev)
        dev.write(EP_OUT, bytes([0xA5, 0x22, 0x00]), timeout=500)
        time.sleep(0.15)
        try: dev.read(EP_BULK, 64, timeout=200)
        except: pass
        dev.write(EP_OUT, bytes([0x00, 0x02]), timeout=500)
        time.sleep(0.15)
        buf = bytearray()
        for _ in range(4):
            try: buf.extend(dev.read(EP_BULK, 512, timeout=300))
            except: break
        if buf and len(buf) > 10 and buf[0] == 0xA5 and buf[1] == 0x22:
            plen = struct.unpack_from('<H', buf, 2)[0]
            if plen > 6:
                return True, bytes(buf)
        return False, bytes(buf)
    except Exception as e:
        return False, b''

dev = open_dev()
print(f"Connected: {dev.manufacturer} — {dev.product}")
print("Make sure device is showing scope mode (CH1 not pressed yet).\n")

# First check baseline — does seek work already?
ok, data = seek0(dev)
print(f"Baseline seek0: {'WORKS' if ok else 'no data'} ({len(data)}B)")
print()

# Try each candidate command
skip = {0x00, 0x01, 0x09, 0x21, 0x22}  # known commands
for cmd_byte in range(0x02, 0x30):
    if cmd_byte in skip:
        continue
    try:
        drain(dev)
        dev.write(EP_OUT, bytes([0x0D, cmd_byte]), timeout=500)
        time.sleep(0.4)
        # read any response
        resp = bytearray()
        for _ in range(3):
            try: resp.extend(dev.read(EP_BULK, 512, timeout=200))
            except: break

        # Now try seek0
        ok, wf = seek0(dev)
        marker = " ◄◄◄ RING BUFFER READY!" if ok else ""
        resp_hex = resp[:8].hex().upper() if resp else "(no resp)"
        print(f"  0D {cmd_byte:02X} → resp={resp_hex}  seek0={'YES' if ok else 'no'}{marker}")

        if ok:
            print(f"\n  *** FOUND IT: 0D {cmd_byte:02X} triggers ring buffer freeze! ***")
            print(f"  Waveform data ({len(wf)}B): {wf[:16].hex().upper()}...")
            break

        time.sleep(0.1)
    except Exception as e:
        print(f"  0D {cmd_byte:02X} → ERROR: {e}")

print("\nDone.")
