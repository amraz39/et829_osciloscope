"""
ET829 / MDS8209 — Direct USB Endpoint Reader  v6
==================================================
THEORY
------
All previous probing used the COM port (usbser.sys) which only exposes
the Bulk IN/OUT endpoints. But the device has THREE endpoints:

  EP3 IN  Interrupt  8 bytes max  every 255ms  ← NEVER READ YET
  EP5 OUT Bulk       64 bytes                   ← what we've been sending to
  EP4 IN  Bulk       64 bytes                   ← what we've been reading from

The measurement data may flow on EP3 (Interrupt IN), which the COM port
driver silently consumes or ignores.

This tool reads ALL three endpoints directly via libusb, bypassing COM port.

SETUP (one-time)
-----------------
1. Install pyusb and libusb:
   pip install pyusb

2. Install libusb Windows driver for the device:
   - Download Zadig: https://zadig.akeo.ie/
   - Run Zadig, select "CDC Device" (VID=2E88 PID=4603)
   - Change driver from "usbser" to "WinUSB" or "libusb-win32"
   - Click "Replace Driver"
   
   *** IMPORTANT: This removes the COM port. To get COM8 back afterwards,
   go to Device Manager → right-click the device → Update Driver →
   Browse → Let me pick → select "USB Serial Device (COM port)" ***

3. Run this tool:
   python et829_v6.py              # read all endpoints
   python et829_v6.py --ep3        # Interrupt IN only (most likely measurement data)
   python et829_v6.py --bulk       # Bulk endpoints only (like COM port)
   python et829_v6.py --all        # all endpoints simultaneously (3 threads)

IF YOU DON'T WANT TO CHANGE DRIVERS
-------------------------------------
Run: python et829_v6.py --comspy
This uses the existing COM8 port but reads much more aggressively
with zero gaps, trying to catch frames we've been missing.

Requirements:
  pip install pyusb pyserial
"""

import argparse, sys, os, time, threading, struct
from datetime import datetime

if sys.platform == "win32":
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

def parse_hex(s):
    s=s.strip().replace("\\x"," ").replace("0x"," ")
    clean=s.replace(" ","")
    if all(c in "0123456789abcdefABCDEF" for c in clean) and len(clean)%2==0 and " " not in s:
        return bytes(int(clean[i:i+2],16) for i in range(0,len(clean),2))
    return bytes(int(t,16) for t in s.split() if t)

DMM_MODES = {
    0x30:"DC-V",0x31:"AC-V",0x32:"DC-A",0x33:"AC-A",
    0x34:"Ohm", 0x35:"Cap", 0x36:"Hz",  0x37:"Diode",
    0x38:"Buzz",0x39:"Temp",0x3A:"NCV",
}

def describe(raw):
    if not raw: return "(empty)"
    if raw[0] == 0xA5 and len(raw) >= 2:
        cmd = raw[1]
        payload = raw[4:] if len(raw) > 4 else raw[2:]
        note = f"A5 cmd=0x{cmd:02X} payload={raw[2:].hex().upper()}"
        if cmd == 0x2A and len(raw) >= 5:
            note += f"  mode={DMM_MODES.get(raw[4], '?')}"
        elif cmd == 0x21:
            note += "  MODE-CHANGE"
        return note
    # Try raw value decode
    if len(raw) == 8:
        note = "8-byte int EP3: "
        note += f"int16BE={struct.unpack_from('>h',raw,0)[0]}  "
        note += f"int32BE={struct.unpack_from('>i',raw,0)[0]}  "
        note += f"bytes={raw.hex().upper()}"
        return note
    return f"raw {len(raw)}B: {raw.hex().upper()}"

# ─────────────────────────────────────────────────────────────────────────────
# USB DIRECT  (requires libusb driver via Zadig)
# ─────────────────────────────────────────────────────────────────────────────
VID = 0x2E88
PID = 0x4603

def usb_read_all(ep3_only=False, bulk_only=False):
    try:
        import usb.core, usb.util
    except ImportError:
        cp(RED, "pyusb not installed. Run: pip install pyusb")
        return

    cp(CYN, f"\n[USB DIRECT] Connecting to VID={VID:04X} PID={PID:04X}")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        cp(RED, "Device not found!")
        cp(YLW, "Make sure Zadig has replaced the driver with WinUSB/libusb-win32")
        cp(YLW, "OR device is connected.")
        return

    cp(GRN, f"Found: {dev.manufacturer} — {dev.product}")

    # Detach kernel driver if needed (Linux/Mac)
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
        if dev.is_kernel_driver_active(1):
            dev.detach_kernel_driver(1)
    except Exception:
        pass

    try:
        dev.set_configuration()
    except Exception as e:
        cp(YLW, f"set_configuration: {e} (may be OK)")

    cfg = dev.get_active_configuration()
    cp(GRN, "Configuration active. Endpoints:")
    for intf in cfg:
        for ep in intf:
            direction = "IN" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else "OUT"
            ep_type = ["Control","Isochronous","Bulk","Interrupt"][ep.bmAttributes & 0x03]
            cp(GRY, f"  EP{ep.bEndpointAddress & 0x7F} {direction:3s} {ep_type:10s} "
                    f"addr=0x{ep.bEndpointAddress:02X}  maxpkt={ep.wMaxPacketSize}")

    stop = threading.Event()
    seen = {}

    def read_ep(addr, name, size, timeout_ms=500):
        cp(CYN, f"  Starting reader: {name} (addr=0x{addr:02X})")
        while not stop.is_set():
            try:
                data = dev.read(addr, size, timeout=timeout_ms)
                if data:
                    raw = bytes(data)
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    is_new = raw not in seen
                    seen[raw] = seen.get(raw, 0) + 1
                    marker = BLD+GRN if is_new else GRN
                    cp(marker, f"[{ts}] {name} {len(raw)}B: {hx(raw)}")
                    cp(GRY,   f"         {describe(raw)}")
                    if len(raw) > 4:
                        print(hexdump(raw, "         "))
            except Exception as e:
                err = str(e)
                if "timeout" not in err.lower() and "110" not in err:
                    cp(RED, f"{name} error: {e}")
                    time.sleep(0.1)

    threads = []

    if not bulk_only:
        # EP3 IN Interrupt (0x83)
        t = threading.Thread(target=read_ep,
                             args=(0x83, "EP3-INT-IN", 8, 1000),
                             daemon=True, name="ep3")
        threads.append(t)

    if not ep3_only:
        # EP4 IN Bulk (0x84)
        t = threading.Thread(target=read_ep,
                             args=(0x84, "EP4-BULK-IN", 64, 500),
                             daemon=True, name="ep4")
        threads.append(t)

    for t in threads:
        t.start()

    # Also send init command on EP5 OUT Bulk
    cp(YLW, "\nSending init sequence on EP5 OUT...")
    try:
        dev.write(0x05, b"\x0D\x0A")
        time.sleep(0.3)
        dev.write(0x05, b"\x0D\x01")
        cp(GRN, "Init sent. Listening on all endpoints...")
        cp(YLW, "Change meter ranges and modes now! Press Ctrl+C to stop.\n")
    except Exception as e:
        cp(RED, f"Write error: {e}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    stop.set()
    cp(YLW, f"\nStopped. Unique frames seen: {len(seen)}")
    for raw, count in seen.items():
        cp(GRN, f"  {count:4d}x  {hx(raw)}  {describe(raw)}")

# ─────────────────────────────────────────────────────────────────────────────
# COM PORT AGGRESSIVE READER (no driver change needed)
# Instead of polling, we open COM8, send init, then read with tiny timeout
# in a tight loop — catching every byte the device auto-sends
# ─────────────────────────────────────────────────────────────────────────────
def com_aggressive(port, baud, duration):
    import serial
    cp(CYN, f"\n[COM AGGRESSIVE] {port} @ {baud}  {duration}s")
    cp(CYN, "No driver change needed — using existing COM port")
    cp(YLW, "Sends init ONCE, then reads with 10ms timeout in tight loop")
    cp(YLW, "Change ranges and modes on the meter while this runs!")
    cp(YLW, "Press Enter to start...")
    input()

    seen = {}
    total_bytes = 0
    buf = b""
    last_byte_t = time.time()
    frame_gap = 0.05   # 50ms gap = new frame

    try:
        ser = serial.Serial(port=port, baudrate=baud,
            bytesize=8, parity='N', stopbits=1,
            timeout=0.01,    # 10ms — very tight
            write_timeout=1,
            xonxoff=False, rtscts=False, dsrdtr=False)

        ser.reset_input_buffer()
        # Send init
        cp(YLW, "Sending 0D 0A...")
        ser.write(b"\x0D\x0A")
        time.sleep(0.2)
        cp(YLW, "Sending 0D 01...")
        ser.write(b"\x0D\x01")
        time.sleep(0.2)
        cp(GRN, "Init done. Listening tightly...\n")

        deadline = time.time() + duration

        while time.time() < deadline:
            chunk = ser.read(256)
            now = time.time()

            if chunk:
                buf += chunk
                last_byte_t = now
                total_bytes += len(chunk)
            elif buf and (now - last_byte_t) > frame_gap:
                # Frame complete
                raw = bytes(buf)
                buf = b""
                is_new = raw not in seen
                seen[raw] = seen.get(raw, 0) + 1

                if is_new:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    cp(BLD+GRN, f"[{ts}] NEW #{len(seen)} ({len(raw)}B): {raw.hex().upper()}")
                    cp(GRY,     f"         {describe(raw)}")
                    print(hexdump(raw, "         "))

            rem = int(deadline - time.time())
            sys.stdout.write(
                f"\r  unique={len(seen)}  total={total_bytes}B  {rem:3d}s  "
                f"{'CHANGE RANGES NOW!' if rem%4<2 else '                  '}")
            sys.stdout.flush()

        ser.close()

    except serial.SerialException as e:
        cp(RED, f"Serial error: {e}")
    except KeyboardInterrupt:
        pass

    print()
    cp(CYN, f"\n{'─'*60}")
    cp(CYN, f"RESULT: {len(seen)} unique frames, {total_bytes} bytes total")
    for i, (raw, count) in enumerate(seen.items(), 1):
        cp(GRN, f"  Frame {i} (x{count}): {raw.hex().upper()}")
        cp(GRY, f"           {describe(raw)}")
        print(hexdump(raw, "    "))

    if len(seen) <= 2:
        cp(YLW, "\nOnly basic frames found via COM port.")
        cp(YLW, "NEXT STEP: Use Zadig to install WinUSB driver, then run:")
        cp(YLW, "  python et829_v6.py --ep3")
        cp(YLW, "This will read the Interrupt endpoint directly.")
        cp(YLW, "\nZadig download: https://zadig.akeo.ie/")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="ET829 v6 - Direct USB + Aggressive COM reader",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port",     default="COM8")
    ap.add_argument("--baud",     type=int, default=115200)
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--ep3",    action="store_true",
                    help="USB: read Interrupt IN endpoint only (needs Zadig)")
    ap.add_argument("--bulk",   action="store_true",
                    help="USB: read Bulk endpoints only (needs Zadig)")
    ap.add_argument("--all",    action="store_true",
                    help="USB: read all endpoints (needs Zadig)")
    ap.add_argument("--comspy", action="store_true",
                    help="Aggressive COM port reader — no driver change needed")
    args = ap.parse_args()

    if args.ep3:
        usb_read_all(ep3_only=True)
    elif args.bulk:
        usb_read_all(bulk_only=True)
    elif args.all:
        usb_read_all()
    else:
        # Default = COM aggressive (no driver change)
        com_aggressive(args.port, args.baud, args.duration)

if __name__ == "__main__":
    main()