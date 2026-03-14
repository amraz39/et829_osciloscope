"""
ET829 / MDS8209 Real-Time Data Logger
======================================
Protocol discovered:  TX 0D 0A  →  RX starts with A5 ...
Frame format (working hypothesis):
  Byte 0    : A5          (sync/header)
  Byte 1    : CMD         (0x2A seen so far — packet type)
  Byte 2-3  : LEN         (uint16 LE — payload length)
  Byte 4..N : PAYLOAD     (measurement data)
  Last byte : CHECKSUM    (TBD — likely XOR or sum of payload)

Run modes:
  python et829_logger.py              # poll at 5 Hz, decode + display
  python et829_logger.py --raw        # show raw hex of every frame
  python et829_logger.py --csv out.csv  # save to CSV
  python et829_logger.py --explore    # send extra commands to probe scope mode
  python et829_logger.py --shell      # interactive hex shell (fixed parser)

Requirements:  pip install pyserial
"""

import argparse
import serial
import serial.tools.list_ports
import time
import sys
import os
import csv
import threading
from datetime import datetime

# ── ANSI colours ─────────────────────────────────────────────────────────────
if sys.platform == "win32":
    os.system("")
RST  = "\033[0m";  RED  = "\033[91m";  GRN = "\033[92m"
YLW  = "\033[93m"; CYN  = "\033[96m";  GRY = "\033[90m"
BLD  = "\033[1m"

def cp(c, *a): print(c + " ".join(str(x) for x in a) + RST)

# ── hex helpers ───────────────────────────────────────────────────────────────
def hexdump(data: bytes, prefix: str = "") -> str:
    out = []
    for i in range(0, len(data), 16):
        ch = data[i:i+16]
        out.append(f"{prefix}{i:04X}  "
                   f"{' '.join(f'{b:02X}' for b in ch):<48}  "
                   f"{''.join(chr(b) if 32<=b<127 else '.' for b in ch)}")
    return "\n".join(out)

def parse_hex(s: str) -> bytes:
    """Accept: 'AA BB 0D'  'AABB0D'  '0xAA 0xBB'  '\\xAA\\xBB'"""
    s = s.strip()
    # Normalise escape styles
    s = s.replace("\\x", " ").replace("0x", " ")
    # If no spaces and even-length, split into pairs
    clean = s.replace(" ", "")
    if " " not in s and len(clean) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in clean):
        tokens = [clean[i:i+2] for i in range(0, len(clean), 2)]
    else:
        tokens = s.split()
    return bytes(int(t, 16) for t in tokens if t)

# ── frame parser ──────────────────────────────────────────────────────────────
# Known CMD IDs (will grow as we learn more)
CMD_NAMES = {
    0x2A: "DMM_DATA",
    0x2B: "DMM_DATA_EX",
    0x10: "OSC_DATA",
    0x20: "MODE",
    0x30: "ACK",
    0x01: "HANDSHAKE",
}

# DMM mode byte mapping (payload[0] of 0x2A frame — hypothesis)
DMM_MODES = {
    0x30: "DC Voltage",
    0x31: "AC Voltage",
    0x32: "DC Current",
    0x33: "AC Current",
    0x34: "Resistance",
    0x35: "Capacitance",
    0x36: "Frequency",
    0x37: "Diode",
    0x38: "Continuity",
    0x39: "Temperature",
    0x00: "Unknown/Off",
}

class Frame:
    """Parsed response frame."""
    def __init__(self, raw: bytes):
        self.raw     = raw
        self.valid   = False
        self.sync    = None
        self.cmd     = None
        self.length  = None
        self.payload = b""
        self.checksum= None
        self._parse()

    def _parse(self):
        if len(self.raw) < 4:
            return
        self.sync   = self.raw[0]
        self.cmd    = self.raw[1]
        self.length = int.from_bytes(self.raw[2:4], "little")
        expected_total = 4 + self.length + 1   # header + payload + checksum
        if len(self.raw) >= 4 + self.length:
            self.payload  = self.raw[4 : 4 + self.length]
            if len(self.raw) >= expected_total:
                self.checksum = self.raw[4 + self.length]
            self.valid = (self.sync == 0xA5)

    def cmd_name(self):
        return CMD_NAMES.get(self.cmd, f"CMD_0x{self.cmd:02X}")

    def decode_dmm(self) -> dict | None:
        """Attempt to decode a DMM_DATA frame. Returns dict or None."""
        if not self.valid or self.cmd not in (0x2A, 0x2B):
            return None
        p = self.payload
        result = {"raw_payload": p.hex().upper(), "cmd": self.cmd_name()}

        # Short frame (1-byte): probably just a mode indicator
        if len(p) == 1:
            result["mode"]  = DMM_MODES.get(p[0], f"0x{p[0]:02X}")
            result["value"] = None
            return result

        # Longer frames: try common layouts seen in HDSC meters
        # Layout A: [mode(1)] [value_int16_BE(2)] [decimal_pos(1)] [unit_flags(1)] ...
        if len(p) >= 4:
            mode_byte   = p[0]
            raw_val     = int.from_bytes(p[1:3], "big", signed=True)
            decimal_pos = p[3] if len(p) > 3 else 0
            unit_flags  = p[4] if len(p) > 4 else 0
            divisor     = 10 ** decimal_pos
            value       = raw_val / divisor if divisor else raw_val

            result["mode"]       = DMM_MODES.get(mode_byte, f"0x{mode_byte:02X}")
            result["raw_int"]    = raw_val
            result["decimal"]    = decimal_pos
            result["value"]      = value
            result["unit_flags"] = f"0x{unit_flags:02X}"
            return result

        # Fallback — just report bytes
        result["mode"]  = DMM_MODES.get(p[0], f"0x{p[0]:02X}") if p else "?"
        result["value"] = None
        return result

    def __str__(self):
        if not self.valid:
            return f"[INVALID] {self.raw.hex().upper()}"
        dec = self.decode_dmm()
        if dec and dec.get("value") is not None:
            return (f"[{self.cmd_name()}] mode={dec['mode']}  "
                    f"value={dec['value']}  flags={dec.get('unit_flags','?')}  "
                    f"payload={self.payload.hex().upper()}")
        elif dec:
            return (f"[{self.cmd_name()}] mode={dec.get('mode','?')}  "
                    f"payload={self.payload.hex().upper()}")
        return (f"[{self.cmd_name()}] len={self.length}  "
                f"payload={self.payload.hex().upper()}")

# ── serial I/O ────────────────────────────────────────────────────────────────
POLL_CMD = b"\x0D\x0A"   # The working poll trigger

def open_port(port: str, baud: int, timeout: float = 0.5) -> serial.Serial:
    return serial.Serial(
        port=port, baudrate=baud,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout, write_timeout=2,
        xonxoff=False, rtscts=False, dsrdtr=False
    )

def poll_once(ser: serial.Serial, cmd: bytes = POLL_CMD,
              wait: float = 0.15) -> bytes:
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    # Read greedily until no more data
    buf = b""
    deadline = time.time() + 0.5
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
        elif buf:
            break
    return buf

# ── explore mode ──────────────────────────────────────────────────────────────
EXPLORE_CMDS = [
    # Variations on the working CRLF trigger
    ("CRLF x2",             b"\x0D\x0A\x0D\x0A"),
    ("LF only",             b"\x0A"),
    ("CR only",             b"\x0D"),
    # Possible scope-mode triggers — vary the second byte
    ("0D 00",               b"\x0D\x00"),
    ("0D 01",               b"\x0D\x01"),
    ("0D 02",               b"\x0D\x02"),
    ("0D 10",               b"\x0D\x10"),
    ("0D 20",               b"\x0D\x20"),
    ("0D 30",               b"\x0D\x30"),
    ("0D FF",               b"\x0D\xFF"),
    # A5-prefixed commands (use discovered sync byte)
    ("A5 00 00 00",         b"\xA5\x00\x00\x00"),
    ("A5 01 00 00",         b"\xA5\x01\x00\x00"),
    ("A5 10 00 00",         b"\xA5\x10\x00\x00"),
    ("A5 20 00 00",         b"\xA5\x20\x00\x00"),
    ("A5 2A 00 00",         b"\xA5\x2A\x00\x00"),
    ("A5 30 00 00",         b"\xA5\x30\x00\x00"),
    ("A5 40 00 00",         b"\xA5\x40\x00\x00"),
    # Re-probe with known sync + cmd 0x2A variations
    ("A5 2A 01 00 00",      b"\xA5\x2A\x01\x00\x00"),
    ("A5 2A 01 00 2A",      b"\xA5\x2A\x01\x00\x2A"),
]

def explore_mode(port: str, baud: int):
    cp(CYN, f"\n[EXPLORE] Testing {len(EXPLORE_CMDS)} additional commands @ {baud} baud")
    cp(YLW, "Switch your device to OSCILLOSCOPE mode NOW, then press Enter...")
    input()

    hits = []
    try:
        with open_port(port, baud, timeout=0.5) as ser:
            for desc, cmd in EXPLORE_CMDS:
                ser.reset_input_buffer()
                ser.write(cmd)
                time.sleep(0.4)
                resp = ser.read(4096)
                label = f"  [{desc:30s}] TX={cmd.hex():<20}"
                if resp:
                    cp(GRN, label + f"  ← {len(resp)} bytes: {resp.hex().upper()}")
                    f = Frame(resp)
                    print(f"         Parsed: {f}")
                    hits.append((desc, cmd, resp))
                else:
                    cp(GRY, label + "  (no response)")
                time.sleep(0.1)

    except serial.SerialException as e:
        cp(RED, f"Port error: {e}")

    if hits:
        cp(GRN, f"\n✓ {len(hits)} scope-mode command(s) responded:")
        for d, c, r in hits:
            print(f"  {d}: {c.hex()} → {r.hex()}")
    else:
        cp(YLW, "\nNo scope-mode commands found.")
        cp(YLW, "The scope mode may use a completely different poll trigger.")
        cp(YLW, "Try running --shell and manually vary the first byte (00→FF).")

# ── real-time logger ──────────────────────────────────────────────────────────
def run_logger(port: str, baud: int, rate_hz: float,
               raw: bool, csv_path: str | None):
    interval = 1.0 / rate_hz
    cp(CYN, f"\n[LOGGER] {port} @ {baud} baud  poll={rate_hz:.1f} Hz")
    cp(CYN,  "Press Ctrl+C to stop.\n")

    csv_file = csv_writer = None
    if csv_path:
        csv_file   = open(csv_path, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["timestamp", "cmd", "mode", "value",
                              "unit_flags", "payload_hex", "raw_hex"])
        cp(GRN, f"CSV logging to: {csv_path}")

    prev_payload = None
    frame_count  = 0
    error_count  = 0

    try:
        with open_port(port, baud, timeout=0.5) as ser:
            while True:
                t0 = time.time()
                try:
                    raw_bytes = poll_once(ser, POLL_CMD, wait=0.1)
                except serial.SerialException as e:
                    cp(RED, f"Serial error: {e}")
                    error_count += 1
                    if error_count > 5:
                        cp(RED, "Too many errors, stopping.")
                        break
                    time.sleep(1)
                    continue

                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                if not raw_bytes:
                    cp(GRY, f"[{ts}] No response")
                else:
                    error_count = 0
                    frame_count += 1
                    frame = Frame(raw_bytes)

                    # Show raw hex if requested or frame changed
                    if raw or (raw_bytes != prev_payload):
                        if raw:
                            cp(GRN, f"[{ts}] {len(raw_bytes)} bytes:")
                            print(hexdump(raw_bytes, "  "))
                        
                        cp(BLD + GRN,
                           f"[{ts}] #{frame_count:04d}  {frame}")

                        # Detailed breakdown on first frame or changes
                        if raw_bytes != prev_payload and not raw:
                            dec = frame.decode_dmm()
                            if dec:
                                print(f"         ├─ sync=0xA5  cmd={frame.cmd_name()}"
                                      f"  len={frame.length}")
                                print(f"         ├─ mode    : {dec.get('mode','?')}")
                                if dec.get('value') is not None:
                                    print(f"         ├─ value   : {dec['value']}")
                                print(f"         └─ payload : {dec.get('raw_payload','?')}")
                        prev_payload = raw_bytes

                    # CSV row
                    if csv_writer:
                        dec = frame.decode_dmm() or {}
                        csv_writer.writerow([
                            datetime.now().isoformat(),
                            frame.cmd_name(),
                            dec.get("mode", ""),
                            dec.get("value", ""),
                            dec.get("unit_flags", ""),
                            frame.payload.hex().upper(),
                            raw_bytes.hex().upper(),
                        ])
                        csv_file.flush()

                elapsed = time.time() - t0
                sleep_t = max(0, interval - elapsed)
                time.sleep(sleep_t)

    except KeyboardInterrupt:
        cp(YLW, "\n\nStopped by user.")

    if csv_file:
        csv_file.close()
        cp(GRN, f"CSV saved: {csv_path}  ({frame_count} frames)")

    cp(CYN, f"Total frames captured: {frame_count}")

# ── interactive shell (fixed parser) ─────────────────────────────────────────
def interactive_shell(port: str, baud: int):
    cp(CYN, f"\n[SHELL] {port} @ {baud} baud")
    print("Enter hex bytes — formats all work:  AA BB 0D  |  AABB0D  |  0xAA 0xBB")
    print("Special:  poll          → send CRLF poll and decode response")
    print("          scope         → switch to scope probe sequence")
    print("          baud <rate>   → change baud rate")
    print("          quit          → exit")
    print("─" * 60)

    stop_evt = threading.Event()
    ser = None

    try:
        ser = open_port(port, baud, timeout=0.1)
    except serial.SerialException as e:
        cp(RED, f"Cannot open {port}: {e}")
        return

    def bg_reader():
        buf = b""
        last_t = time.time()
        while not stop_evt.is_set():
            try:
                chunk = ser.read(128)
                if chunk:
                    buf += chunk
                    last_t = time.time()
                elif buf and (time.time() - last_t) > 0.12:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    f  = Frame(buf)
                    cp(GRN, f"\n[{ts}] RX {len(buf)} bytes  {f}")
                    print(hexdump(buf, "  "))
                    buf = b""
            except Exception:
                pass

    t = threading.Thread(target=bg_reader, daemon=True)
    t.start()

    current_baud = baud
    while True:
        try:
            line = input(f"\n[{current_baud}] TX> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue

        lo = line.lower()
        if lo == "quit":
            break
        elif lo == "poll":
            raw_bytes = poll_once(ser)
            if raw_bytes:
                f = Frame(raw_bytes)
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cp(GRN, f"[{ts}] POLL response ({len(raw_bytes)} bytes)  {f}")
                print(hexdump(raw_bytes, "  "))
            else:
                cp(YLW, "No response to CRLF poll.")
        elif lo == "scope":
            cp(CYN, "Sending scope-probe sequence (switch device to scope mode first)...")
            for desc, cmd in EXPLORE_CMDS[:8]:
                ser.reset_input_buffer()
                ser.write(cmd)
                time.sleep(0.3)
                r = ser.read(4096)
                if r:
                    cp(GRN, f"  {desc}: {cmd.hex()} → {r.hex().upper()}")
        elif lo.startswith("baud "):
            try:
                nb = int(lo.split()[1])
                ser.close()
                ser = open_port(port, nb, timeout=0.1)
                current_baud = nb
                cp(GRN, f"Switched to {nb} baud")
            except Exception as e:
                cp(RED, f"Error: {e}")
        else:
            try:
                tx = parse_hex(line)
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cp(YLW, f"[{ts}] TX {len(tx)} bytes: {tx.hex().upper()}")
                ser.reset_input_buffer()
                ser.write(tx)
            except (ValueError, IndexError) as e:
                cp(RED, f"Parse error: {e}")
                print("  Accepted formats:  AA BB 0D   AABB0D   0xAA 0xBB")

    stop_evt.set()
    t.join(timeout=1)
    try:
        ser.close()
    except Exception:
        pass

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="ET829/MDS8209 Real-Time Logger & Protocol Decoder")
    p.add_argument("--port",    default="COM8")
    p.add_argument("--baud",    type=int, default=115200)
    p.add_argument("--rate",    type=float, default=5.0,
                   help="Poll rate in Hz (default 5)")
    p.add_argument("--raw",     action="store_true",
                   help="Show raw hex dump of every frame")
    p.add_argument("--csv",     metavar="FILE",
                   help="Save measurements to CSV file")
    p.add_argument("--explore", action="store_true",
                   help="Probe additional commands (find scope mode trigger)")
    p.add_argument("--shell",   action="store_true",
                   help="Interactive hex shell")
    p.add_argument("--list",    action="store_true",
                   help="List available COM ports")
    args = p.parse_args()

    if args.list:
        for pt in serial.tools.list_ports.comports():
            print(f"  {pt.device:<10} {pt.description}")
        return

    if args.explore:
        explore_mode(args.port, args.baud)
    elif args.shell:
        interactive_shell(args.port, args.baud)
    else:
        run_logger(args.port, args.baud, args.rate, args.raw, args.csv)

if __name__ == "__main__":
    main()