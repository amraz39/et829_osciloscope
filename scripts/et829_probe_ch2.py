"""
ET829 CH2-button command finder.
Run with ONE waveform saved on CH2 (flags=0x02).

Procedure:
  1. On device: measure mode → scope → save one CH2 waveform → stay in scope mode
  2. Run this script immediately (do NOT press CH1)
  3. Script tries every 0D XX command and checks if seek returns CH2 data (flags=0x02)

Known commands (skipped): 0D 00=scope, 0D 01=arm, 0D 02=CH1, 0D 09=info, 0D 21=DMM
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

def seek_and_check(dev):
    """
    Scan pages 0-15 looking for any frame with ch_flags=0x02 (CH2).
    Returns (found_ch2, ch_flags_seen, n_bytes).
    """
    for page in range(16):
        try:
            drain(dev)
            dev.write(EP_OUT, bytes([0xA5, 0x22, page]), timeout=500)
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
                    ch_flags = buf[9] if len(buf) > 9 else 0xFF
                    if ch_flags == 0x02:
                        return True, ch_flags, len(buf)
                    return False, ch_flags, len(buf)
        except: pass
    return False, 0xFF, 0

dev = open_dev()
print(f"Connected: {dev.manufacturer} — {dev.product}")
print("Ensure ONE CH2 waveform is saved. Device should be in scope mode.\n")

# Baseline
ch2_found, flags, nbytes = seek_and_check(dev)
print(f"Baseline: ch2_found={ch2_found}  flags=0x{flags:02X}  bytes={nbytes}")
print()

skip = {0x00, 0x01, 0x02, 0x09, 0x21, 0x22}
for cmd_byte in range(0x02, 0x30):
    if cmd_byte in skip:
        continue
    try:
        drain(dev)
        dev.write(EP_OUT, bytes([0x0D, cmd_byte]), timeout=500)
        time.sleep(0.4)
        resp = bytearray()
        for _ in range(3):
            try: resp.extend(dev.read(EP_BULK, 512, timeout=200))
            except: break

        ch2_found, flags, nbytes = seek_and_check(dev)
        resp_hex = resp[:8].hex().upper() if resp else "(none)"

        marker = ""
        if ch2_found:
            marker = " ◄◄◄ CH2 READY!"
        elif flags != 0xFF and nbytes > 0:
            marker = f" (got data, flags=0x{flags:02X})"

        print(f"  0D {cmd_byte:02X} → resp={resp_hex}  ch2={'YES' if ch2_found else 'no '}{marker}")

        if ch2_found:
            print(f"\n  *** FOUND IT: 0D {cmd_byte:02X} is the CH2 command! ***")
            break

        time.sleep(0.1)
    except Exception as e:
        print(f"  0D {cmd_byte:02X} → ERROR: {e}")

print("\nDone.")
