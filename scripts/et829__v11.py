"""
ET829 / MDS8209 — Systematic Command Brute Force  v11
======================================================
Usage:
  python et829_v11.py           # run phase2 + phase3 + three-byte
  python et829_v11.py --phase2  # 0D XX all 256
  python et829_v11.py --phase3  # A5 XX all 256
  python et829_v11.py --three   # 3-byte A5 sequences (most promising)
  python et829_v11.py --full    # full 256x256 overnight

Requirements: pip install pyusb  +  libusb-1.0.dll in script folder
"""

import usb.core, usb.util
import time, sys, os, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84
BORING = {
    bytes.fromhex("A52A0100300000"),
    bytes.fromhex("A5210139"),
    bytes.fromhex("A521003A"),
}

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None: raise RuntimeError("Device not found!")
    for intf in range(3):
        try:
            if dev.is_kernel_driver_active(intf):
                dev.detach_kernel_driver(intf)
        except: pass
    try: dev.set_configuration()
    except: pass
    return dev

def send_recv(dev, cmd, wait_ms=200):
    try:
        try: dev.read(EP_BULK, 64, timeout=30)
        except: pass
        dev.write(EP_OUT, cmd, timeout=500)
        time.sleep(wait_ms / 1000)
        return bytes(dev.read(EP_BULK, 64, timeout=300))
    except usb.core.USBTimeoutError:
        return None
    except:
        return None

def is_interesting(resp):
    if resp is None: return False
    if resp in BORING: return False
    return True

def log_hit(cmd, resp):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    note = " *** LONG - POSSIBLE MEASUREMENT DATA ***" if len(resp) > 7 else ""
    cp(BLD+GRN, f"[{ts}] HIT! TX={cmd.hex().upper():<22} RX={resp.hex().upper()}{note}")

def scan(dev, label, commands, wait_ms=200):
    cp(CYN, f"\n[{label}] {len(commands)} sequences")
    hits = []
    for desc, cmd in commands:
        resp = send_recv(dev, cmd, wait_ms)
        if is_interesting(resp):
            log_hit(cmd, resp); hits.append((cmd, resp))
        else:
            sys.stdout.write(f"\r  {desc:<22} → {resp.hex().upper() if resp else '(none)':<20}")
            sys.stdout.flush()
        time.sleep(0.03)
    print()
    cp(CYN, f"{label}: {len(hits)} hits")
    return hits

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase2", action="store_true")
    ap.add_argument("--phase3", action="store_true")
    ap.add_argument("--three",  action="store_true")
    ap.add_argument("--full",   action="store_true")
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")
    all_hits = []

    try:
        if args.phase2 or not any([args.phase2,args.phase3,args.three,args.full]):
            cmds = [(f"0D {b:02X}", bytes([0x0D, b])) for b in range(256)]
            all_hits += scan(dev, "PHASE2 0D-XX", cmds)

        if args.phase3 or not any([args.phase2,args.phase3,args.three,args.full]):
            cmds = [(f"A5 {b:02X}", bytes([0xA5, b])) for b in range(256)]
            all_hits += scan(dev, "PHASE3 A5-XX", cmds)

        if args.three or not any([args.phase2,args.phase3,args.three,args.full]):
            cmds = []
            for b in range(256): cmds.append((f"A5 2A {b:02X}",    bytes([0xA5,0x2A,b])))
            for b in range(256): cmds.append((f"A5 21 {b:02X}",    bytes([0xA5,0x21,b])))
            for b in range(256): cmds.append((f"A5 {b:02X} 01",    bytes([0xA5,b,0x01])))
            for b in range(256): cmds.append((f"0D 0A {b:02X}",    bytes([0x0D,0x0A,b])))
            for b in range(256): cmds.append((f"A5 01 00 00 {b:02X}", bytes([0xA5,0x01,0x00,0x00,b])))
            all_hits += scan(dev, "THREE-BYTE", cmds, wait_ms=250)

        if args.full:
            cp(CYN, "\n[FULL 256x256] ~3 hours. Log: et829_brute_full.log. Ctrl+C to stop.")
            total = 0; start = time.time()
            with open("et829_brute_full.log","w", encoding="utf-8") as f:
                f.write(f"ET829 Full Brute {datetime.now()}\n\n")
                for b1 in range(256):
                    for b2 in range(256):
                        cmd = bytes([b1,b2])
                        resp = send_recv(dev, cmd, 80)
                        total += 1
                        if is_interesting(resp):
                            line = f"HIT: {cmd.hex().upper()} -> {resp.hex().upper()}\n"
                            f.write(line); f.flush()
                            log_hit(cmd, resp); all_hits.append((cmd,resp))
                        elapsed = time.time()-start
                        rate = total/elapsed if elapsed>0 else 0
                        eta = (65536-total)/rate if rate>0 else 0
                        sys.stdout.write(
                            f"\r  {b1:02X}{b2:02X}  {total}/65536  "
                            f"{rate:.0f}/s  ETA={eta/60:.0f}min  hits={len(all_hits)}")
                        sys.stdout.flush()
                        time.sleep(0.01)
            print()

    except KeyboardInterrupt:
        pass

    if all_hits:
        cp(BLD+GRN, f"\nTOTAL HITS: {len(all_hits)}")
        for cmd, resp in all_hits:
            cp(GRN, f"  {cmd.hex().upper()} → {resp.hex().upper()}")
    else:
        cp(YLW, "\nNo new responses. Try --full for overnight 256x256 scan.")

if __name__ == "__main__":
    main()