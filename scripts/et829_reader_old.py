"""
ET829 / MDS8209 — Live DMM Reader  (Final)
==========================================
Protocol (fully decoded):

  TX:  00 05
  RX:  A5 25 09 00 [B0] [B1 B2 B3 B4] [B5] [B6] [B7] [B8] [00] [CHK]
                    |    |___________|   |    |    |    |
                    |    uint32 LE       |   rng  dec   |
                    mode  value×1000   flags           padding
  
  value = uint32_LE(B1,B2,B3,B4) / 1000.0
  checksum = (0x100 - sum(all_but_last)) % 0x100

Usage:
  python et829_reader.py           # live display, updates every 0.5s
  python et829_reader.py --csv     # CSV output for logging
  python et829_reader.py --once    # single reading

Requirements: pip install pyusb  +  libusb-1.0.dll in script folder
"""

import usb.core, usb.util
import time, sys, os, struct, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"
CYN="\033[96m"; BLD="\033[1m"; GRY="\033[90m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84
CMD      = bytes([0x00, 0x05])

# Known mode codes from CRLF ping response (byte[4] of A5 2A frame)
DMM_MODE_NAMES = {
    0x30: "DC-V",  0x31: "AC-V",  0x32: "DC-A",  0x33: "AC-A",
    0x34: "Ohm",   0x35: "Cap",   0x36: "Hz",    0x37: "Diode",
    0x38: "Buzz",  0x39: "Temp",  0x3A: "NCV",
}

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Device not found! Check WinUSB driver is active (Zadig).")
    for intf in range(3):
        try:
            if dev.is_kernel_driver_active(intf): dev.detach_kernel_driver(intf)
        except: pass
    try: dev.set_configuration()
    except: pass
    return dev

def get_mode(dev):
    """Get current DMM mode via CRLF ping."""
    try:
        try: dev.read(EP_BULK, 64, timeout=30)
        except: pass
        dev.write(EP_OUT, b"\x0D\x0A", timeout=500)
        time.sleep(0.2)
        r = bytes(dev.read(EP_BULK, 64, timeout=300))
        if r[0] == 0xA5 and r[1] == 0x2A and len(r) >= 5:
            return DMM_MODE_NAMES.get(r[4], f"0x{r[4]:02X}")
    except: pass
    return "unknown"

def query(dev):
    """Send 00 05, return decoded measurement or None."""
    try:
        try: dev.read(EP_BULK, 64, timeout=30)
        except: pass
        dev.write(EP_OUT, CMD, timeout=500)
        time.sleep(0.15)
        raw = bytes(dev.read(EP_BULK, 64, timeout=500))
    except usb.core.USBTimeoutError:
        return None
    except:
        return None

    if len(raw) < 5 or raw[0] != 0xA5 or raw[1] != 0x25:
        return None

    plen = struct.unpack_from('<H', raw, 2)[0]
    if len(raw) < 4 + plen + 1:
        return None

    payload  = raw[4:4+plen]
    checksum = raw[4+plen]
    expected = (0x100 - sum(raw[:4+plen]) % 0x100) % 0x100

    value = struct.unpack_from('<I', payload, 1)[0] / 1000.0

    return {
        'raw':      raw.hex().upper(),
        'value':    value,
        'b0':       payload[0],   # mode/type code
        'b5':       payload[5] if len(payload) > 5 else 0,
        'b6':       payload[6] if len(payload) > 6 else 0,
        'b7':       payload[7] if len(payload) > 7 else 0,
        'chk_ok':   checksum == expected,
        'payload':  payload,
    }

def main():
    ap = argparse.ArgumentParser(description="ET829 Live DMM Reader")
    ap.add_argument("--csv",      action="store_true", help="CSV output")
    ap.add_argument("--once",     action="store_true", help="Single reading")
    ap.add_argument("--interval", type=float, default=0.5, help="Poll interval (s)")
    ap.add_argument("--raw",      action="store_true", help="Show raw bytes too")
    args = ap.parse_args()

    dev = open_device()

    if args.csv:
        print("timestamp,value,b0,b5,b6,b7,raw")
    else:
        cp(GRN, f"ET829 Live Reader — polling every {args.interval}s")
        cp(YLW, "Press Ctrl+C to stop\n")
        # Get current mode
        mode = get_mode(dev)
        cp(CYN, f"Current DMM mode: {mode}\n")

    count = 0
    prev_val = None

    try:
        while True:
            r = query(dev)
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            if r:
                val = r['value']
                changed = (prev_val is None or abs(val - prev_val) > 0.0005)
                prev_val = val

                if args.csv:
                    print(f"{ts},{val:.3f},{r['b0']},{r['b5']},{r['b6']},{r['b7']},{r['raw']}")
                elif args.once:
                    cp(GRN, f"Value: {val:.3f}")
                    cp(GRY, f"  b0=0x{r['b0']:02X} b5={r['b5']} b6={r['b6']} b7={r['b7']} chk={'OK' if r['chk_ok'] else 'BAD'}")
                    if args.raw: cp(GRY, f"  raw: {r['raw']}")
                    break
                else:
                    marker = BLD+GRN if changed else GRY
                    cp(marker,
                       f"[{ts}]  {val:12.3f}  "
                       f"| b5={r['b5']:3d} b6={r['b6']:3d} b7={r['b7']:3d} "
                       f"| {'OK' if r['chk_ok'] else 'BAD CHK'}"
                       f"{'  ← changed' if changed else ''}")
                    if args.raw:
                        cp(GRY, f"  {r['raw']}")
                count += 1
            else:
                if not args.csv:
                    sys.stdout.write(f"\r[{ts}]  (no response)  ")
                    sys.stdout.flush()

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print()
        if not args.csv:
            cp(CYN, f"\nDone. {count} readings captured.")

if __name__ == "__main__":
    main()