"""
ET829 v13 — Decode measurement command 0x25
============================================
TX: 00 05  →  RX: A5 25 09 00 [9-byte payload] [checksum]

Run this, change the meter reading, and we'll see which bytes change!

Usage:
  python et829_v13.py          # poll continuously, show all bytes
  python et829_v13.py --once   # single reading
"""
import usb.core, usb.util
import time, sys, os, struct, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; CYN="\033[96m"; BLD="\033[1m"; GRY="\033[90m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84
CMD      = bytes([0x00, 0x05])  # THE KEY COMMAND

DMM_MODES = {
    0x30:"DC-V", 0x31:"AC-V", 0x32:"DC-A", 0x33:"AC-A",
    0x34:"Ohm",  0x35:"Cap",  0x36:"Hz",   0x37:"Diode",
    0x38:"Buzz", 0x39:"Temp", 0x3A:"NCV",
}

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None: raise RuntimeError("Device not found!")
    for intf in range(3):
        try:
            if dev.is_kernel_driver_active(intf): dev.detach_kernel_driver(intf)
        except: pass
    try: dev.set_configuration()
    except: pass
    return dev

def query(dev):
    try:
        try: dev.read(EP_BULK, 64, timeout=30)
        except: pass
        dev.write(EP_OUT, CMD, timeout=500)
        time.sleep(0.15)
        return bytes(dev.read(EP_BULK, 64, timeout=500))
    except usb.core.USBTimeoutError:
        return None
    except Exception as e:
        return None

def decode(raw):
    if raw is None or len(raw) < 5: return None
    if raw[0] != 0xA5 or raw[1] != 0x25: return None
    plen = struct.unpack_from('<H', raw, 2)[0]
    if len(raw) < 4 + plen + 1: return None
    payload  = raw[4:4+plen]
    checksum = raw[4+plen]
    body     = raw[:4+plen]
    expected = (0x100 - sum(body) % 0x100) % 0x100
    ok = checksum == expected

    return {
        'raw':      raw,
        'cmd':      raw[1],
        'plen':     plen,
        'payload':  payload,
        'checksum': checksum,
        'chk_ok':   ok,
    }

def show(d, prev_payload=None):
    p = d['payload']
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

    # Which bytes changed?
    changed = []
    if prev_payload and len(prev_payload) == len(p):
        changed = [i for i in range(len(p)) if p[i] != prev_payload[i]]

    # Show all bytes prominently
    byte_strs = []
    for i, b in enumerate(p):
        if i in changed:
            byte_strs.append(f"\033[1;93m[{b:02X}={b:3d}]\033[0m")
        else:
            byte_strs.append(f"{b:02X}={b:3d}")
    
    print(f"\n[{ts}] cmd=0x{d['cmd']:02X} plen={d['plen']} chk={'OK' if d['chk_ok'] else 'BAD'}")
    print(f"  Payload: {' '.join(byte_strs)}")
    if changed:
        print(f"  CHANGED bytes: {changed}")
        for i in changed:
            print(f"    byte[{i}] = 0x{p[i]:02X} = {p[i]}")

    # All possible int16 interpretations of changed or first 4 bytes
    print(f"  Interpretations:")
    for i in range(min(len(p)-1, 8)):
        v = struct.unpack_from('<h', p, i)[0]
        u = struct.unpack_from('<H', p, i)[0]
        mark = " <-- CHANGED" if i in changed or (i+1) in changed else ""
        print(f"    int16LE @{i}: {v:7d}  "
              f"/10={v/10:8.1f}  /100={v/100:8.2f}  /1000={v/1000:8.3f}{mark}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, "Connected. Sending init...")
    for init_cmd in [b"\x0D\x0A", b"\x0D\x01"]:
        try:
            dev.write(EP_OUT, init_cmd, timeout=1000)
            time.sleep(0.3)
            try: dev.read(EP_BULK, 64, timeout=200)
            except: pass
        except: pass
    cp(GRN, "Init done. Polling with TX=00 05...")
    cp(YLW, "Change meter ranges and probe input while this runs!")
    cp(YLW, "Watch which bytes change — those contain the measurement!\n")
    cp(CYN, "Format: byte[0] byte[1] ... byte[8]  (payload of A5 25 response)\n")

    prev = None
    try:
        while True:
            raw = query(dev)
            d = decode(raw)
            if d:
                show(d, prev)
                prev = d['payload']
            else:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                sys.stdout.write(f"\r[{ts}] no response / wrong format: {raw.hex().upper() if raw else '(none)'}")
                sys.stdout.flush()
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
    cp(CYN, "\nDone.")

if __name__ == "__main__":
    main()