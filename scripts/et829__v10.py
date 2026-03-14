"""
ET829 / MDS8209 — Direct USB Endpoint Probe  v10
=================================================
We now have direct USB access. EP3 is reachable but silent.
Strategy: brute-force commands via EP5 OUT, read responses on
BOTH EP3 (Interrupt) and EP4 (Bulk) simultaneously.

Usage:
  python et829_v10.py           # brute all 256 single bytes via EP5, read EP3+EP4
  python et829_v10.py --listen  # just listen on EP3+EP4 for 60s
  python et829_v10.py --shell   # interactive USB shell

Requirements: pip install pyusb  +  libusb-1.0.dll in script folder
"""

import usb.core, usb.util
import time, sys, os, struct, threading, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; GRY="\033[90m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)
def hx(b): return " ".join(f"{x:02X}" for x in b)
def hexdump(data, pre=""):
    out=[]
    for i in range(0,len(data),16):
        ch=data[i:i+16]
        out.append(f"{pre}{i:04X}  {' '.join(f'{b:02X}' for b in ch):<48}  "
                   f"{''.join(chr(b) if 32<=b<127 else '.' for b in ch)}")
    return "\n".join(out)

VID, PID = 0x2E88, 0x4603
EP_OUT  = 0x05   # Bulk OUT
EP_BULK_IN  = 0x84   # Bulk IN
EP_INT_IN   = 0x83   # Interrupt IN  ← the mystery endpoint

DMM_MODES = {
    0x30:"DC-V",0x31:"AC-V",0x32:"DC-A",0x33:"AC-A",
    0x34:"Ohm", 0x35:"Cap", 0x36:"Hz",  0x37:"Diode",
    0x38:"Buzz",0x39:"Temp",0x3A:"NCV",
}

def describe(raw):
    if not raw: return ""
    if raw[0]==0xA5 and len(raw)>=2:
        cmd=raw[1]
        s=f"A5 cmd=0x{cmd:02X}"
        if len(raw)>=5: s+=f" mode={DMM_MODES.get(raw[4],'?')}"
        if len(raw)>5: s+=f" extra={raw[5:].hex().upper()}"
        return s
    # Try raw value interpretations for 8-byte EP3 data
    if len(raw)==8:
        v16 = struct.unpack_from('>H', raw, 0)[0]
        v32 = struct.unpack_from('>I', raw, 0)[0]
        return f"EP3-8B: raw={raw.hex().upper()} int16BE={v16} int32BE={v32}"
    return f"raw={raw.hex().upper()}"

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Device not found!")
    # Detach kernel drivers
    for intf in range(2):
        try:
            if dev.is_kernel_driver_active(intf):
                dev.detach_kernel_driver(intf)
        except Exception:
            pass
    try:
        dev.set_configuration()
    except Exception:
        pass
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")
    return dev

def usb_write(dev, data, timeout=1000):
    try:
        n = dev.write(EP_OUT, data, timeout=timeout)
        return n
    except Exception as e:
        cp(RED, f"Write error: {e}")
        return 0

def usb_read_ep3(dev, timeout=300):
    try:
        data = dev.read(EP_INT_IN, 8, timeout=timeout)
        return bytes(data)
    except usb.core.USBTimeoutError:
        return None
    except Exception as e:
        return None

def usb_read_ep4(dev, timeout=200):
    try:
        data = dev.read(EP_BULK_IN, 64, timeout=timeout)
        return bytes(data)
    except usb.core.USBTimeoutError:
        return None
    except Exception as e:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# LISTEN ONLY — no TX, just read both endpoints
# ─────────────────────────────────────────────────────────────────────────────
def listen_all(duration=60):
    cp(CYN, f"\n[LISTEN] Reading EP3+EP4 for {duration}s (no TX)")
    cp(YLW, "Change meter ranges and modes now!")
    cp(YLW, "Press Enter to start...")
    input()

    dev = open_device()
    seen = {}
    stop = threading.Event()

    def reader(ep_addr, ep_name, pkt_size, timeout_ms):
        while not stop.is_set():
            try:
                data = bytes(dev.read(ep_addr, pkt_size, timeout=timeout_ms))
                if data:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    is_new = data not in seen
                    seen[data] = seen.get(data,0)+1
                    marker = BLD+GRN if is_new else GRN
                    cp(marker, f"[{ts}] {ep_name} {len(data)}B: {hx(data)}  {describe(data)}")
                    if len(data) > 4:
                        print(hexdump(data, "  "))
            except usb.core.USBTimeoutError:
                pass
            except Exception as e:
                if not stop.is_set():
                    time.sleep(0.1)

    t3 = threading.Thread(target=reader, args=(EP_INT_IN,  "EP3-INT ", 8,  500), daemon=True)
    t4 = threading.Thread(target=reader, args=(EP_BULK_IN, "EP4-BULK", 64, 200), daemon=True)
    t3.start(); t4.start()

    try:
        dl = time.time() + duration
        while time.time() < dl:
            rem = int(dl-time.time())
            sys.stdout.write(f"\r  EP3+EP4 listening... {rem:3d}s  unique={len(seen)}  ")
            sys.stdout.flush()
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

    stop.set()
    print()
    cp(CYN, f"Saw {len(seen)} unique frames")
    for raw, cnt in seen.items():
        cp(GRN, f"  x{cnt:3d}  {raw.hex().upper()}  {describe(raw)}")

# ─────────────────────────────────────────────────────────────────────────────
# BRUTE FORCE — try every byte via EP5, read EP3 AND EP4 after each
# ─────────────────────────────────────────────────────────────────────────────
def brute_all(skip_known=True):
    cp(CYN, "\n[BRUTE USB] Sending every byte via EP5, reading EP3+EP4")
    cp(YLW, "Meter ON and measuring. Press Enter...")
    input()

    dev = open_device()

    # Known commands that give boring responses
    BORING = {b"\xA5\x21\x01\x39", b"\xA5\x2A\x01\x00\x30\x00\x00"}

    hits_ep3 = []
    hits_ep4 = []

    # First send known init
    usb_write(dev, b"\x0D\x0A")
    time.sleep(0.1)
    usb_read_ep4(dev)  # drain

    cp(CYN, "Scanning 0x00 - 0xFF on EP5 OUT...\n")

    for b in range(256):
        cmd = bytes([b])

        # Drain first
        usb_read_ep4(dev, timeout=50)
        usb_read_ep3(dev, timeout=50)

        # Send
        usb_write(dev, cmd, timeout=500)

        # Read EP4 (Bulk IN)
        r4 = usb_read_ep4(dev, timeout=300)
        # Read EP3 (Interrupt IN)
        r3 = usb_read_ep3(dev, timeout=300)

        ep4_str = r4.hex().upper() if r4 else ""
        ep3_str = r3.hex().upper() if r3 else ""
        
        interesting4 = r4 and bytes(r4) not in BORING
        interesting3 = r3 is not None  # ANY EP3 data is interesting

        if interesting3 or interesting4:
            sys.stdout.write("\r" + " "*80 + "\r")
            if interesting3:
                cp(BLD+GRN, f"  0x{b:02X} EP3: {ep3_str}  {describe(r3)}")
                hits_ep3.append((b, r3))
            if interesting4:
                cp(GRN if interesting4 else GRY,
                   f"  0x{b:02X} EP4: {ep4_str}  {describe(r4)}")
                hits_ep4.append((b, r4))
        else:
            sys.stdout.write(f"\r  0x{b:02X}: EP4={ep4_str or '(none)':<20} EP3={ep3_str or '(none)'}")
            sys.stdout.flush()

        time.sleep(0.05)

    print()
    cp(CYN, f"\nEP3 hits: {len(hits_ep3)}  EP4 hits: {len(hits_ep4)}")
    for b, r in hits_ep3:
        cp(GRN, f"  EP3 byte=0x{b:02X}: {r.hex().upper()}")
    for b, r in hits_ep4:
        cp(GRN, f"  EP4 byte=0x{b:02X}: {r.hex().upper()}")

# ─────────────────────────────────────────────────────────────────────────────
# KNOWN MULTI-BYTE COMMANDS via USB
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_CMDS = [
    ("CRLF",               b"\x0D\x0A"),
    ("ENTER DMM",          b"\x0D\x01"),
    ("ENTER SCOPE",        b"\x0D\x00"),
    ("A5 01",              b"\xA5\x01\x00\x00\x01"),
    ("A5 2A",              b"\xA5\x2A\x00\x00\x2A"),
    # CDC SET_LINE_CODING via control endpoint
]

def probe_known():
    cp(CYN, "\n[KNOWN CMDS] Sending known commands, reading EP3+EP4")
    cp(YLW, "Press Enter...")
    input()

    dev = open_device()

    for desc, cmd in KNOWN_CMDS:
        # Drain
        usb_read_ep4(dev, 50); usb_read_ep3(dev, 50)
        # Send
        usb_write(dev, cmd)
        time.sleep(0.3)
        r4 = usb_read_ep4(dev, 500)
        r3 = usb_read_ep3(dev, 500)

        cp(GRY if not r3 and not r4 else GRN,
           f"  [{desc:20s}] TX={cmd.hex():<20}"
           f"  EP4={r4.hex().upper() if r4 else '(none)':<20}"
           f"  EP3={r3.hex().upper() if r3 else '(none)'}")

    # Also try CDC control request: SET_CONTROL_LINE_STATE
    # bmRequestType=0x21, bRequest=0x22, wValue=0x0003 (DTR+RTS), wIndex=0, wLength=0
    cp(CYN, "\nSending CDC SET_CONTROL_LINE_STATE (DTR+RTS=1)...")
    try:
        dev.ctrl_transfer(0x21, 0x22, 0x0003, 0, None)
        cp(GRN, "Sent! Reading EP3...")
        time.sleep(0.3)
        r3 = usb_read_ep3(dev, 1000)
        r4 = usb_read_ep4(dev, 500)
        cp(GRN if r3 or r4 else GRY,
           f"  EP3={r3.hex().upper() if r3 else '(none)'}  "
           f"EP4={r4.hex().upper() if r4 else '(none)'}")
    except Exception as e:
        cp(RED, f"ctrl_transfer error: {e}")

    # Try SET_LINE_CODING to set baud
    cp(CYN, "\nSending CDC SET_LINE_CODING (115200, 8N1)...")
    try:
        # dwDTERate=115200, bCharFormat=0, bParityType=0, bDataBits=8
        line_coding = struct.pack('<IBBB', 115200, 0, 0, 8)
        dev.ctrl_transfer(0x21, 0x20, 0, 0, line_coding)
        cp(GRN, "Sent! Now sending CRLF and reading...")
        usb_write(dev, b"\x0D\x0A")
        time.sleep(0.4)
        r3 = usb_read_ep3(dev, 1000)
        r4 = usb_read_ep4(dev, 500)
        cp(GRN if r3 or r4 else GRY,
           f"  EP3={r3.hex().upper() if r3 else '(none)'}  "
           f"EP4={r4.hex().upper() if r4 else '(none)'}")
    except Exception as e:
        cp(RED, f"SET_LINE_CODING error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE SHELL
# ─────────────────────────────────────────────────────────────────────────────
def shell():
    cp(CYN, "\n[USB SHELL] Direct USB access")
    print("Commands: tx <hex> | ep3 | ep4 | both | init | ctrl | listen <s> | quit")
    print("─"*60)

    dev = open_device()
    stop = threading.Event()

    # Background EP3 reader
    def ep3_bg():
        while not stop.is_set():
            r = usb_read_ep3(dev, 500)
            if r:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cp(BLD+GRN, f"\n[{ts}] EP3 {len(r)}B: {hx(r)}  {describe(r)}")

    threading.Thread(target=ep3_bg, daemon=True).start()

    while True:
        try:
            line = input("\n[USB] TX> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line: continue
        lo = line.lower()
        if lo == "quit": break
        elif lo == "ep3":
            r = usb_read_ep3(dev, 1000)
            cp(GRN if r else GRY, f"EP3: {r.hex().upper() if r else '(timeout)'}")
        elif lo == "ep4":
            r = usb_read_ep4(dev, 500)
            cp(GRN if r else GRY, f"EP4: {r.hex().upper() if r else '(timeout)'}")
        elif lo == "both":
            r3 = usb_read_ep3(dev, 500); r4 = usb_read_ep4(dev, 300)
            cp(GRN, f"EP3: {r3.hex().upper() if r3 else '(none)'}  EP4: {r4.hex().upper() if r4 else '(none)'}")
        elif lo == "init":
            usb_write(dev, b"\x0D\x01")
            cp(YLW, "Sent ENTER DMM")
        elif lo == "ctrl":
            try:
                dev.ctrl_transfer(0x21, 0x22, 0x0003, 0, None)
                cp(GRN, "CDC SET_CONTROL_LINE_STATE sent")
            except Exception as e:
                cp(RED, str(e))
        elif lo.startswith("listen"):
            try: secs = int(lo.split()[1])
            except: secs = 10
            cp(CYN, f"Listening {secs}s...")
            time.sleep(secs)
        else:
            try:
                tx = bytes.fromhex(line.replace(" ",""))
                n = usb_write(dev, tx)
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cp(YLW, f"[{ts}] TX {n}B: {tx.hex().upper()}")
                time.sleep(0.2)
                r4 = usb_read_ep4(dev, 300)
                if r4: cp(GRN, f"  EP4: {r4.hex().upper()}  {describe(r4)}")
            except Exception as e:
                cp(RED, f"Error: {e}")

    stop.set()

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="ET829 v10 - Direct USB Probe")
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--brute",  action="store_true")
    ap.add_argument("--known",  action="store_true")
    ap.add_argument("--shell",  action="store_true")
    ap.add_argument("--duration", type=int, default=60)
    args = ap.parse_args()

    if args.listen:
        listen_all(args.duration)
    elif args.brute:
        brute_all()
    elif args.shell:
        shell()
    else:
        # Default: known commands first (quick), then listen
        probe_known()
        cp(CYN, "\nNow listening on EP3+EP4...")
        listen_all(args.duration)

if __name__ == "__main__":
    main()