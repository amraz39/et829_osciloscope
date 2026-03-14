"""
ET829 Scope Mode Command Scanner
=================================
Scan all 00 XX and A5 XX commands while device is in SCOPE mode.
We know 00 05 = DMM data. Scope data is probably another short command.
Should complete in ~3 minutes.
"""
import usb.core, usb.util, time, sys, os
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; GRY="\033[90m"; BLD="\033[1m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84

BORING = {
    bytes.fromhex("A52A0100300000"),
    bytes.fromhex("A5210139"),
    bytes.fromhex("A521003A"),
    bytes.fromhex("A52509002D000000000005010000FA"),
}

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None: print("Not found!"); exit()
for i in range(3):
    try:
        if dev.is_kernel_driver_active(i): dev.detach_kernel_driver(i)
    except: pass
try: dev.set_configuration()
except: pass
cp(GRN, f"Connected: {dev.manufacturer}")
cp(YLW, "Make sure device is in SCOPE mode with AWG connected to CH1!\n")

def txrx(cmd, wait=0.15):
    try:
        try: dev.read(EP_BULK, 512, timeout=30)
        except: pass
        dev.write(EP_OUT, cmd, timeout=500)
        time.sleep(wait)
        # Try to read up to 4096 bytes (waveform data could be large)
        chunks = []
        while True:
            try:
                chunk = bytes(dev.read(EP_BULK, 512, timeout=150))
                chunks.append(chunk)
            except: break
        if chunks:
            return b''.join(chunks)
        return None
    except: return None

hits = []

def scan(label, commands):
    cp(f"\033[96m", f"\n[{label}] {len(commands)} commands")
    for desc, cmd in commands:
        r = txrx(cmd)
        if r and r not in BORING:
            note = f"  *** {len(r)}B {'LONG=SCOPE DATA?' if len(r)>20 else ''} ***"
            cp(BLD+GRN, f"\n  HIT! {desc:<20} TX={cmd.hex().upper():<20} RX={r[:32].hex().upper()}{note}")
            hits.append((desc, cmd, r))
        else:
            sys.stdout.write(f"\r  {desc:<20} → {r.hex()[:20].upper() if r else '(none)':<22}")
            sys.stdout.flush()
        time.sleep(0.04)
    print()

# Phase 1: All 00 XX (DMM hit was here at 00 05)
scan("00 XX all 256", [(f"00 {b:02X}", bytes([0x00, b])) for b in range(256)])

# Phase 2: All A5 XX
scan("A5 XX all 256", [(f"A5 {b:02X}", bytes([0xA5, b])) for b in range(256)])

# Phase 3: 0D XX (we know 0D 0A and 0D 01 work)
scan("0D XX all 256", [(f"0D {b:02X}", bytes([0x0D, b])) for b in range(256)])

# Phase 4: A5 2A XX variations (our known data frame header)
scan("A5 2A XX", [(f"A5 2A {b:02X}", bytes([0xA5, 0x2A, b])) for b in range(256)])

if hits:
    cp(BLD+GRN, f"\n{'='*60}")
    cp(BLD+GRN, f"TOTAL HITS: {len(hits)}")
    for desc, cmd, r in hits:
        cp(GRN, f"  {desc}: {cmd.hex().upper()} -> {r[:32].hex().upper()} ({len(r)}B)")
else:
    cp(YLW, "\nNo new responses in scope mode.")
    cp(YLW, "Scope data may require a different trigger sequence first.")