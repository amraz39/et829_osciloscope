"""
ET829 / MDS8209 — Protocol Probe v4
=====================================
What we know:
  0D 0A           → A5 2A 01 00 30 00 00   (status ping, mode=DC Voltage)
  0D 01           → A5 21 01 39             (enters/confirms DMM mode)
  A5 01 00 00 01  → A5 21 01 39             (same as above)
  mode switch OSC → A5 21 00 3A             (unsolicited: entered scope mode)
  mode switch DMM → A5 21 01 39             (unsolicited: entered DMM mode)

Hypothesis:
  Real app init sequence = 0D 01 (enter DMM) THEN poll with unknown cmd
  Changing ranges/values in DMM mode produces no unsolicited stream
  → measurement data must be explicitly requested AFTER proper init

This tool:
  1. Sends 0D 01 to init, waits for A5 21 response
  2. Then brute-forces follow-up poll commands to find measurement data
  3. Also tries sequence: 0D 01 → then every 0D xx variant
  4. Also tries longer A5-framed requests after init

Usage:
  python et829_v4.py            # full targeted search (recommended)
  python et829_v4.py --seq      # try specific known-good sequences
  python et829_v4.py --after    # brute all 0x00-0xFF after 0D 01 init
  python et829_v4.py --shell    # interactive shell with init helper

Requirements: pip install pyserial
"""

import argparse, serial, serial.tools.list_ports
import time, sys, os, struct, threading
from datetime import datetime

if sys.platform == "win32":
    os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; GRY="\033[90m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST)

def hx(b): return " ".join(f"{x:02X}" for x in b)

def hexdump(data, pre=""):
    out=[]
    for i in range(0, len(data), 16):
        ch = data[i:i+16]
        out.append(f"{pre}{i:04X}  {' '.join(f'{b:02X}' for b in ch):<48}  "
                   f"{''.join(chr(b) if 32<=b<127 else '.' for b in ch)}")
    return "\n".join(out)

def parse_hex(s):
    s = s.strip().replace("\\x"," ").replace("0x"," ")
    clean = s.replace(" ","")
    if all(c in "0123456789abcdefABCDEF" for c in clean) and len(clean)%2==0 and " " not in s:
        return bytes(int(clean[i:i+2],16) for i in range(0, len(clean), 2))
    return bytes(int(t,16) for t in s.split() if t)

def open_port(port, baud, timeout=0.5):
    return serial.Serial(port=port, baudrate=baud,
        bytesize=8, parity='N', stopbits=1,
        timeout=timeout, write_timeout=2,
        xonxoff=False, rtscts=False, dsrdtr=False)

def flush_recv(ser, wait=0.25, maxb=4096):
    """Send nothing — just drain any pending bytes."""
    time.sleep(wait)
    return ser.read(maxb)

def transact(ser, cmd, wait=0.25, maxb=4096):
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    buf = b""
    dl = time.time() + 0.4
    while time.time() < dl:
        c = ser.read(256)
        if c: buf += c; dl = time.time() + 0.15
        elif buf: break
    return buf

DMM_MODES = {
    0x30:"DC Voltage", 0x31:"AC Voltage", 0x32:"DC Current",
    0x33:"AC Current", 0x34:"Resistance", 0x35:"Capacitance",
    0x36:"Frequency",  0x37:"Diode",      0x38:"Continuity",
    0x39:"Temperature",0x3A:"NCV",
}

UNITS = {
    0x00:"", 0x01:"mV", 0x02:"V", 0x03:"kV",
    0x10:"µA", 0x11:"mA", 0x12:"A",
    0x20:"Ω",  0x21:"kΩ", 0x22:"MΩ",
    0x30:"nF", 0x31:"µF", 0x32:"mF",
    0x40:"Hz", 0x41:"kHz",0x42:"MHz",
    0x50:"°C", 0x51:"°F",
}

def describe(raw):
    if not raw: return "(empty)"
    if len(raw) >= 4 and raw[0] == 0xA5:
        cmd = raw[1]
        # Try 2-byte len (cmd 0x2A style)
        plen2 = int.from_bytes(raw[2:4], "little")
        if 4 + plen2 <= len(raw) <= 4 + plen2 + 2:
            payload = raw[4:4+plen2]
            hdr = f"cmd=0x{cmd:02X} plen={plen2}"
            return _decode_payload(cmd, payload, hdr, raw)
        # Try 1-byte len (cmd 0x21 style)
        plen1 = raw[2]
        if 3 + plen1 <= len(raw) <= 3 + plen1 + 2:
            payload = raw[3:3+plen1]
            hdr = f"cmd=0x{cmd:02X} plen={plen1}"
            return _decode_payload(cmd, payload, hdr, raw)
    return f"raw {len(raw)}B: {raw.hex().upper()}"

def _decode_payload(cmd, payload, hdr, raw):
    extra = ""
    if payload:
        b0 = payload[0]
        mode = DMM_MODES.get(b0)
        if mode:
            extra += f"  mode={mode}"
        # Try to decode a numeric value if payload >= 5 bytes
        if len(payload) >= 5:
            try:
                v = struct.unpack_from(">h", payload, 1)[0]
                dec = payload[3] if len(payload)>3 else 0
                unit_b = payload[4] if len(payload)>4 else 0
                val = v / (10**dec) if 1<=dec<=6 else v
                unit = UNITS.get(unit_b, f"0x{unit_b:02X}")
                extra += f"  VALUE={val} {unit}  (raw={v} dec={dec})"
            except: pass
        elif len(payload) >= 3:
            try:
                v = struct.unpack_from(">h", payload, 1)[0]
                extra += f"  int16={v}"
            except: pass
        if not extra:
            extra = f"  payload={payload.hex().upper()}"
    return f"A5 {hdr}{extra}"

# ─────────────────────────────────────────────────────────────────────────────
# INIT HELPER — sends 0D 01, waits for A5 21 ACK
# ─────────────────────────────────────────────────────────────────────────────
INIT_CMD    = b"\x0D\x01"
INIT_EXPECT = b"\xA5\x21"   # starts with this

def init_dmm(ser, retries=3) -> bool:
    """Send 0D 01, wait for A5 21 xx xx response. Returns True on success."""
    for i in range(retries):
        ser.reset_input_buffer()
        ser.write(INIT_CMD)
        time.sleep(0.25)
        r = ser.read(32)
        if r and r[:2] == INIT_EXPECT:
            cp(GRN, f"  ✓ DMM init OK: {r.hex().upper()}")
            return True
        time.sleep(0.1)
    cp(YLW, f"  DMM init: no A5 21 response (got {r.hex().upper() if r else 'nothing'})")
    return False

# ─────────────────────────────────────────────────────────────────────────────
# SEQUENCE TESTS — specific promising sequences to try
# ─────────────────────────────────────────────────────────────────────────────
SEQUENCES = [
    # (description,  [list of (tx_bytes, wait_ms), ...],  listen_after_ms)
    ("Init(0D01) + CRLF",
     [(b"\x0D\x01", 300), (b"\x0D\x0A", 250)], 500),

    ("Init(0D01) + 0D 02",
     [(b"\x0D\x01", 300), (b"\x0D\x02", 250)], 500),

    ("Init(0D01) + 0D 03",
     [(b"\x0D\x01", 300), (b"\x0D\x03", 250)], 500),

    ("Init(0D01) + 0D 0A + 0D 01",
     [(b"\x0D\x0A", 200), (b"\x0D\x01", 300), (b"\x0D\x0A", 250)], 500),

    # Mirror the device's own A5 21 frame back at it
    ("Echo A5 21 01 39",
     [(b"\xA5\x21\x01\x39", 300)], 500),

    ("Echo A5 21 00 3A then DMM",
     [(b"\xA5\x21\x00\x3A", 200), (b"\xA5\x21\x01\x39", 300)], 500),

    # A5-framed with checksum variants
    ("A5 2A 00 00 + wait",
     [(b"\xA5\x2A\x00\x00", 400)], 500),

    # Try different cmd bytes after init
    ("Init + A5 03 00 00 03",
     [(b"\x0D\x01", 300), (b"\xA5\x03\x00\x00\x03", 300)], 500),

    ("Init + A5 22 00 00 22",
     [(b"\x0D\x01", 300), (b"\xA5\x22\x00\x00\x22", 300)], 500),

    ("Init + A5 23 00 00 23",
     [(b"\x0D\x01", 300), (b"\xA5\x23\x00\x00\x23", 300)], 500),

    ("Init + A5 24 00 00 24",
     [(b"\x0D\x01", 300), (b"\xA5\x24\x00\x00\x24", 300)], 500),

    ("Init + A5 25 00 00 25",
     [(b"\x0D\x01", 300), (b"\xA5\x25\x00\x00\x25", 300)], 500),

    ("Init + A5 26 00 00 26",
     [(b"\x0D\x01", 300), (b"\xA5\x26\x00\x00\x26", 300)], 500),

    ("Init + A5 2A 00 00 2A",
     [(b"\x0D\x01", 300), (b"\xA5\x2A\x00\x00\x2A", 300)], 500),

    # Maybe checksum is XOR of all bytes after sync
    # A5 2A 01 00 30: XOR of 2A^01^00^30 = 0x1B
    ("A5 2A 01 00 30 1B (XOR chk)",
     [(b"\xA5\x2A\x01\x00\x30\x1B", 300)], 500),

    # Maybe the poll is just faster / shorter timeout needed
    ("Fast CRLF x5 (50ms each)",
     [(b"\x0D\x0A", 50)]*5, 300),
]

def run_sequences(port, baud):
    cp(CYN, f"\n[SEQUENCES] Testing {len(SEQUENCES)} init+poll sequences")
    cp(YLW, "Meter ON, measuring DC voltage on a battery. Press Enter...")
    input()

    best_plen = 1
    hits = []

    try:
        with open_port(port, baud, timeout=0.5) as ser:
            for desc, steps, listen_ms in SEQUENCES:
                ser.reset_input_buffer()

                # Execute all steps
                last_resp = b""
                for tx, wait_ms in steps:
                    ser.write(tx)
                    time.sleep(wait_ms / 1000)
                    r = ser.read(256)
                    if r: last_resp = r

                # Final listen window
                time.sleep(listen_ms / 1000)
                final = ser.read(4096)
                if final: last_resp = final

                plen = 0
                if last_resp and last_resp[0] == 0xA5 and len(last_resp) >= 4:
                    # Try to extract payload length
                    plen2 = int.from_bytes(last_resp[2:4], "little")
                    plen1 = last_resp[2]
                    plen = plen2 if plen2 < 64 else plen1

                label = f"  [{desc:45s}]"
                if last_resp:
                    marker = (BLD+GRN) if plen > best_plen else (GRN if plen == best_plen else GRY)
                    cp(marker, label + f" plen={plen}  {describe(last_resp)}")
                    if plen > best_plen:
                        cp(BLD+GRN, "  *** LONGER PAYLOAD — potential measurement data! ***")
                        best_plen = plen
                        hits.append((desc, last_resp))
                else:
                    cp(GRY, label + " (no response)")

                time.sleep(0.1)

    except serial.SerialException as e:
        cp(RED, f"Port error: {e}")

    print()
    if hits:
        cp(BLD+GRN, f"Best results ({len(hits)} candidates):")
        for desc, resp in hits:
            print(f"  {desc}:")
            print(hexdump(resp, "    "))
    else:
        cp(YLW, "No sequence returned more payload than baseline.")
        cp(YLW, "Try --after to brute all bytes after 0D 01 init.")

# ─────────────────────────────────────────────────────────────────────────────
# BRUTE ALL SINGLE BYTES after 0D 01 init
# ─────────────────────────────────────────────────────────────────────────────
def brute_after_init(port, baud):
    cp(CYN, f"\n[BRUTE AFTER INIT] Init with 0D 01, then try every byte 0x00-0xFF")
    cp(YLW, "Meter ON, measuring something. Press Enter...")
    input()

    hits = []
    try:
        with open_port(port, baud, timeout=0.35) as ser:
            for b in range(256):
                # Re-init before each probe
                ser.reset_input_buffer()
                ser.write(b"\x0D\x01")
                time.sleep(0.2)
                ser.read(32)  # drain init response

                # Probe byte
                probe = bytes([b])
                ser.reset_input_buffer()
                ser.write(probe)
                time.sleep(0.28)
                resp = ser.read(512)

                if resp:
                    plen = 0
                    if resp[0] == 0xA5 and len(resp) >= 4:
                        plen = int.from_bytes(resp[2:4], "little")
                    sys.stdout.write("\r" + " "*80 + "\r")
                    marker = BLD+GRN if plen > 1 else GRN
                    cp(marker, f"  0x{b:02X} ({b:3d}) → {resp.hex().upper():<40} plen={plen}  {describe(resp)}")
                    hits.append((b, resp, plen))
                else:
                    sys.stdout.write(f"\r  0x{b:02X} ({b:3d}) → (no response)")
                    sys.stdout.flush()

    except KeyboardInterrupt:
        pass

    print()
    if hits:
        best = max(hits, key=lambda x: x[2])
        cp(BLD+GRN, f"\nBest: 0x{best[0]:02X} → plen={best[2]}  {best[1].hex().upper()}")
        cp(GRN, f"Commands with payload > 1:")
        for b, r, p in hits:
            if p > 1:
                print(f"  0x{b:02X} → {r.hex().upper()}")
    else:
        cp(YLW, "No bytes triggered a response after init.")

# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE SHELL
# ─────────────────────────────────────────────────────────────────────────────
def shell(port, baud):
    cp(CYN, f"\n[SHELL] {port} @ {baud}")
    print("Formats:  AA BB 0D  |  AABB0D")
    print("Cmds:  init | ping | listen <sec> | baud <n> | quit")
    print("─"*60)
    stop = threading.Event()

    try:
        ser = open_port(port, baud, timeout=0.05)
    except serial.SerialException as e:
        cp(RED, f"Cannot open {port}: {e}"); return

    def reader():
        buf = b""; lt = time.time()
        while not stop.is_set():
            try:
                ch = ser.read(128)
                if ch: buf += ch; lt = time.time()
                elif buf and time.time()-lt > 0.12:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    cp(GRN, f"\n[{ts}] RX {len(buf)}B  {describe(buf)}")
                    print(hexdump(buf, "  "))
                    buf = b""
            except: pass

    threading.Thread(target=reader, daemon=True).start()
    cb = baud

    while True:
        try:
            line = input(f"\n[{cb}] TX> ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not line: continue
        lo = line.lower()
        if lo == "quit": break
        elif lo == "init":
            cp(YLW, f"Sending init: {hx(INIT_CMD)}")
            ser.reset_input_buffer(); ser.write(INIT_CMD)
        elif lo == "ping":
            cp(YLW, "Sending CRLF ping...")
            ser.reset_input_buffer(); ser.write(b"\x0D\x0A")
        elif lo.startswith("listen"):
            try: secs = int(lo.split()[1])
            except: secs = 30
            cp(CYN, f"Silent listen for {secs}s...")
            time.sleep(secs)
        elif lo.startswith("baud "):
            try:
                nb = int(lo.split()[1])
                ser.close(); ser = open_port(port, nb, timeout=0.05); cb = nb
                cp(GRN, f"→ {nb} baud")
            except Exception as e: cp(RED, str(e))
        else:
            try:
                tx = parse_hex(line)
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cp(YLW, f"[{ts}] TX {len(tx)}B: {tx.hex().upper()}")
                ser.reset_input_buffer(); ser.write(tx)
            except Exception as e:
                cp(RED, f"Parse error: {e}")
                print("  Formats:  AA BB 0D   AABB0D   0xAA 0xBB")

    stop.set()
    try: ser.close()
    except: pass

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="ET829 v4 - Init+Poll Discovery")
    ap.add_argument("--port",  default="COM8")
    ap.add_argument("--baud",  type=int, default=115200)
    ap.add_argument("--seq",   action="store_true", help="Test known sequences")
    ap.add_argument("--after", action="store_true", help="Brute all bytes after init")
    ap.add_argument("--shell", action="store_true")
    ap.add_argument("--list",  action="store_true")
    args = ap.parse_args()

    if args.list:
        for p in serial.tools.list_ports.comports():
            print(f"  {p.device:<10} {p.description}")
        return

    if args.after:
        brute_after_init(args.port, args.baud)
    elif args.shell:
        shell(args.port, args.baud)
    else:
        # Default = sequences (most promising)
        run_sequences(args.port, args.baud)

if __name__ == "__main__":
    main()