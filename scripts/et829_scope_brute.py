"""
ET829 Scope Mode Brute Force
============================
In DMM mode, TX=00 05 gave measurement data.
In scope mode, we need to find the equivalent command(s).

Strategy:
  Phase 1: All 256 single-byte commands
  Phase 2: All 256 two-byte sequences starting with 00 (like DMM's 00 05)
  Phase 3: All 256 two-byte sequences starting with A5
  Phase 4: All 10-byte 00 0A XX commands (Hantek-style, all func/cmd combos)
  Phase 5: Full 256x256 if needed

IMPORTANT: Run with device in SCOPE MODE (physical switch).

Usage:
  python et829_scope_brute.py              # phases 1+2+3+4
  python et829_scope_brute.py --phase 1    # single bytes only
  python et829_scope_brute.py --phase 2    # 00 XX
  python et829_scope_brute.py --phase 3    # A5 XX
  python et829_scope_brute.py --phase 4    # 00 0A func cmd combos
  python et829_scope_brute.py --full       # full 256x256
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

def drain(dev):
    while True:
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def txrx(dev, cmd, wait_ms=150, read_size=512):
    drain(dev)
    try: dev.write(EP_OUT, cmd, timeout=500)
    except: return []
    time.sleep(wait_ms / 1000)
    results = []
    while True:
        try:
            r = bytes(dev.read(EP_BULK, read_size, timeout=200))
            results.append(r)
        except: break
    return results

def is_hit(results):
    return len(results) > 0

def log_hit(cmd, results):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    for r in results:
        note = " *** LONG/WAVEFORM ***" if len(r) > 15 else ""
        cp(BLD+GRN, f"[{ts}] HIT! TX={cmd.hex().upper():<24} RX({len(r)}B)={r[:32].hex().upper()}{note}")

def scan(dev, label, commands, wait_ms=150, log_file=None):
    cp(CYN, f"\n[{label}] {len(commands)} commands")
    hits = []
    for i, (desc, cmd) in enumerate(commands):
        results = txrx(dev, cmd, wait_ms)
        if is_hit(results):
            log_hit(cmd, results)
            hits.append((cmd, results))
            if log_file:
                for r in results:
                    log_file.write(f"HIT: {cmd.hex().upper()} -> {r.hex().upper()}\n")
                log_file.flush()
        else:
            pct = (i+1)*100//len(commands)
            sys.stdout.write(f"\r  [{pct:3d}%] {desc:<28} -> (none)   ")
            sys.stdout.flush()
        time.sleep(0.02)
    print()
    cp(CYN, f"{label}: {len(hits)} hits")
    return hits

def phase1(dev, f):
    """All single bytes 0x00-0xFF"""
    cmds = [(f"0x{b:02X}", bytes([b])) for b in range(256)]
    return scan(dev, "PHASE1 single-byte", cmds, wait_ms=100, log_file=f)

def phase2(dev, f):
    """00 XX — like DMM's 00 05"""
    cmds = [(f"00 {b:02X}", bytes([0x00, b])) for b in range(256)]
    return scan(dev, "PHASE2 00-XX", cmds, wait_ms=150, log_file=f)

def phase3(dev, f):
    """A5 XX"""
    cmds = [(f"A5 {b:02X}", bytes([0xA5, b])) for b in range(256)]
    return scan(dev, "PHASE3 A5-XX", cmds, wait_ms=150, log_file=f)

def phase4(dev, f):
    """00 0A func cmd — Hantek 10-byte format, all func/cmd combos"""
    funcs = [0x0000, 0x0001, 0x0002, 0x0003, 0x0100, 0x0101, 0x0200, 0x0300]
    cmds = []
    for func in funcs:
        for cmd_byte in range(0x20):  # 0-31 covers all Hantek commands
            for val in [0, 1, 2]:
                pkt = bytes([0x00, 0x0A]) + struct.pack('<H', func) + bytes([cmd_byte])
                pkt += struct.pack('<I', val) + bytes([0x00])
                cmds.append((f"0A func={func:04X} cmd={cmd_byte:02X} val={val}", pkt))
    return scan(dev, "PHASE4 Hantek-10byte", cmds, wait_ms=200, log_file=f)

def phase_full(dev, f):
    """Full 256x256 brute force"""
    cp(CYN, f"\n[FULL 256x256] ~{256*256*0.12/60:.0f}min. Log: et829_scope_brute.log")
    hits = []
    total = 0
    start = time.time()
    for b1 in range(256):
        for b2 in range(256):
            cmd = bytes([b1, b2])
            results = txrx(dev, cmd, 100)
            total += 1
            if is_hit(results):
                log_hit(cmd, results)
                hits.append((cmd, results))
                if f:
                    for r in results:
                        f.write(f"HIT: {cmd.hex().upper()} -> {r.hex().upper()}\n")
                    f.flush()
            elapsed = time.time()-start
            rate = total/elapsed if elapsed else 0
            eta = (65536-total)/rate/60 if rate else 0
            sys.stdout.write(f"\r  {b1:02X}{b2:02X}  {total}/65536  "
                             f"{rate:.0f}/s  ETA={eta:.0f}min  hits={len(hits)}")
            sys.stdout.flush()
            time.sleep(0.01)
    print()
    return hits

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, choices=[1,2,3,4], help="Run single phase")
    ap.add_argument("--full",  action="store_true")
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")
    cp(YLW, "CONFIRM: Device is in SCOPE MODE with signal on CH1!\n")

    logname = "et829_scope_brute.log"
    all_hits = []

    with open(logname, "w", encoding="utf-8") as f:
        f.write(f"ET829 Scope Brute Force {datetime.now()}\n\n")
        try:
            if args.full:
                all_hits += phase_full(dev, f)
            elif args.phase == 1:
                all_hits += phase1(dev, f)
            elif args.phase == 2:
                all_hits += phase2(dev, f)
            elif args.phase == 3:
                all_hits += phase3(dev, f)
            elif args.phase == 4:
                all_hits += phase4(dev, f)
            else:
                all_hits += phase1(dev, f)
                all_hits += phase2(dev, f)
                all_hits += phase3(dev, f)
                all_hits += phase4(dev, f)
        except KeyboardInterrupt:
            print()

    if all_hits:
        cp(BLD+GRN, f"\nTOTAL HITS: {len(all_hits)}")
        for cmd, results in all_hits:
            for r in results:
                cp(GRN, f"  {cmd.hex().upper()} -> {r[:32].hex().upper()}")
        cp(YLW, f"\nLongest response: {max(len(r) for _,rs in all_hits for r in rs)} bytes")
    else:
        cp(YLW, "\nNo responses found in scope mode.")
        cp(YLW, "Try --full for exhaustive 256x256 scan.")
    cp(GRY, f"Log saved: {logname}")

if __name__ == "__main__":
    main()