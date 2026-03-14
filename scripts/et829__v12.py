"""
ET829 / MDS8209 — Pure Passive USB Listen  v12
===============================================
NEW THEORY: Our constant request-response polling may be SUPPRESSING
the auto-stream. The device might auto-stream measurement data on EP4
when nobody is talking to it — but when we send a CRLF every 500ms,
the device switches into request-response mode instead.

Test: open USB, claim interface, read EP4 ONLY, send NOTHING.

Also tests: does device send ANYTHING unsolicited on EP4 after:
  - power-on fresh connect
  - CDC SET_CONTROL_LINE_STATE
  - long silence

Usage:
  python et829_v12.py             # pure passive listen 60s
  python et829_v12.py --init      # send ONE init then go silent
  python et829_v12.py --cdc       # CDC control request only, then silent
  python et829_v12.py --reset     # USB reset then listen

Requirements: pip install pyusb  +  libusb-1.0.dll in script folder
"""

import usb.core, usb.util
import time, sys, os, struct, argparse, threading
from datetime import datetime

os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; GRY="\033[90m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)
def hx(b): return " ".join(f"{x:02X}" for x in b)

VID, PID  = 0x2E88, 0x4603
EP_OUT    = 0x05
EP_BULK   = 0x84
EP_INT    = 0x83

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Device not found!")
    for intf in range(3):
        try:
            if dev.is_kernel_driver_active(intf):
                dev.detach_kernel_driver(intf)
        except Exception:
            pass
    try:
        dev.set_configuration()
    except Exception:
        pass
    return dev

def passive_listen(dev, duration=60, label="PASSIVE"):
    """Read EP4 and EP3 with NO transmissions at all."""
    cp(CYN, f"\n[{label}] Listening {duration}s — sending NOTHING")
    cp(YLW, "Change meter ranges and modes now!")

    seen = {}
    stop = threading.Event()
    lock = threading.Lock()

    def reader(ep_addr, ep_name, pkt_size, timeout_ms):
        buf = b""
        while not stop.is_set():
            try:
                chunk = bytes(dev.read(ep_addr, pkt_size, timeout=timeout_ms))
                if chunk:
                    buf += chunk
                    # Allow short burst accumulation
                    time.sleep(0.01)
                    # Try to read more
                    try:
                        more = bytes(dev.read(ep_addr, pkt_size, timeout=50))
                        if more: buf += more
                    except: pass

                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    with lock:
                        is_new = buf not in seen
                        seen[buf] = seen.get(buf, 0) + 1
                    marker = BLD+GRN if is_new else GRN
                    cp(marker,
                       f"[{ts}] {ep_name} {len(buf)}B: {buf.hex().upper()}"
                       f"{'  ← NEW!' if is_new else ''}")
                    if len(buf) > 7:
                        print(f"  int16_LE={struct.unpack_from('<h',buf,0)[0] if len(buf)>=2 else '?'}")
                    buf = b""
            except usb.core.USBTimeoutError:
                if buf:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    with lock:
                        is_new = buf not in seen
                        seen[buf] = seen.get(buf, 0) + 1
                    cp(BLD+GRN if is_new else GRN,
                       f"[{ts}] {ep_name} {len(buf)}B: {buf.hex().upper()}")
                    buf = b""
            except Exception as e:
                if not stop.is_set() and "timeout" not in str(e).lower():
                    cp(RED, f"{ep_name} error: {e}")
                time.sleep(0.05)

    t4 = threading.Thread(target=reader, args=(EP_BULK, "EP4", 64, 500), daemon=True)
    t3 = threading.Thread(target=reader, args=(EP_INT,  "EP3", 8,  500), daemon=True)
    t4.start()
    t3.start()

    try:
        dl = time.time() + duration
        while time.time() < dl:
            rem = int(dl - time.time())
            sys.stdout.write(
                f"\r  silent listen... {rem:3d}s  unique={len(seen)}  ")
            sys.stdout.flush()
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

    stop.set()
    print()
    return seen

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init",  action="store_true", help="Send one 0D01 then go silent")
    ap.add_argument("--cdc",   action="store_true", help="CDC SET_CONTROL_LINE_STATE then silent")
    ap.add_argument("--reset", action="store_true", help="USB reset then listen")
    ap.add_argument("--duration", type=int, default=60)
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")

    if args.reset:
        cp(YLW, "Sending USB reset...")
        dev.reset()
        time.sleep(2)
        dev = open_device()
        cp(GRN, "Reconnected after reset")

    if args.cdc:
        cp(YLW, "Sending CDC SET_CONTROL_LINE_STATE (DTR+RTS)...")
        try:
            dev.ctrl_transfer(0x21, 0x22, 0x0003, 0, None, timeout=1000)
            cp(GRN, "Sent. Now going silent...")
        except Exception as e:
            cp(RED, f"Error: {e}")
        time.sleep(0.5)

    if args.init:
        cp(YLW, "Sending one 0D 01 (ENTER DMM) then going silent...")
        try:
            dev.write(EP_OUT, b"\x0D\x01", timeout=1000)
            cp(GRN, "Sent. Now going silent...")
        except Exception as e:
            cp(RED, f"Error: {e}")
        time.sleep(0.5)

    # Drain any buffered responses first
    for _ in range(5):
        try: dev.read(EP_BULK, 64, timeout=100)
        except: break

    label = "AFTER-RESET" if args.reset else "AFTER-CDC" if args.cdc else "AFTER-INIT" if args.init else "PURE-PASSIVE"
    seen = passive_listen(dev, args.duration, label)

    print()
    if seen:
        cp(CYN, f"Summary — {len(seen)} unique frames:")
        for raw, cnt in sorted(seen.items(), key=lambda x: -x[1]):
            cp(GRN, f"  x{cnt:3d}  {raw.hex().upper()}  ({len(raw)}B)")
    else:
        cp(YLW, "No data received at all during passive listen.")
        cp(YLW, "The device does NOT auto-stream — it only responds to commands.")
        cp(YLW, "Measurement data must require a specific undiscovered command.")
        cp(YLW, "Next step: run et829_v11.py --three (3-byte sequences)")

if __name__ == "__main__":
    main()