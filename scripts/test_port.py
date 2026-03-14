"""
ET829 / MDS8209 Serial Protocol Reverse Engineering Tool
=========================================================
Vendor: Xiaohua Semiconductor (HDSC)  VID=0x2E88  PID=0x4603
Port:   COM8  (USB CDC - usbser.sys)

WHAT THIS TOOL DOES
-------------------
1. Auto-detects baud rate by scanning common rates
2. Brute-forces single-byte commands (0x00 - 0xFF) to find polling triggers
3. Tries known multi-byte command patterns from similar HDSC devices
4. Logs everything to a timestamped .log file in hex + ASCII
5. Provides an interactive shell so YOU can send arbitrary bytes

REQUIREMENTS
------------
    pip install pyserial

USAGE
-----
    python et829_probe.py                  # uses COM8 by default
    python et829_probe.py --port COM3      # override port
    python et829_probe.py --baud 115200    # skip baud scan, use fixed rate
    python et829_probe.py --brute          # run single-byte brute-force scan
    python et829_probe.py --listen         # just listen passively for 30s
    python et829_probe.py --shell          # interactive hex shell

NOTES ON THE DEVICE
-------------------
- Device does NOT auto-send; it must be polled (request/response protocol)
- USB descriptor shows: 1x Interrupt IN (status), 2x Bulk IN/OUT (data)
- The ScopeMeter2023022 app works in DMM mode → protocol for DMM is findable
- App crashes in scope mode → scope protocol may differ or need a preamble
- HDSC (华大半导体) is a Huada Semiconductor MCU, commonly HC32F series
- Similar devices (Hantek 2D72, Owon HDS272S) use proprietary binary protocols

STRATEGY
---------
Step 1: Run with --listen while the ScopeMeter app is connected in DMM mode
        Use a second tool (like com0com + Serial Port Monitor) to sniff the
        traffic BETWEEN the app and the device if possible.
        
Step 2: Run --brute to find which byte(s) trigger a response.

Step 3: Use --shell to manually craft multi-byte packets based on findings.
"""

import argparse
import serial
import serial.tools.list_ports
import time
import sys
import os
import threading
from datetime import datetime

# ── colour helpers (work on Windows 10+ with ANSI enabled) ──────────────────
RESET  = "\033[0m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
GREY   = "\033[90m"

def cprint(colour, *args):
    print(colour + " ".join(str(a) for a in args) + RESET)

# ── hex helpers ──────────────────────────────────────────────────────────────
def hexdump(data: bytes, prefix: str = "") -> str:
    """Classic hex dump: offset  hex  ascii"""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{prefix}{i:04X}  {hex_part:<48}  {asc_part}")
    return "\n".join(lines)

def parse_hex_input(s: str) -> bytes:
    """Parse user input like 'AA BB 0D' or '\\xAA\\xBB' or 'AABB0D'"""
    s = s.strip().replace("\\x", " ").replace("0x", " ")
    tokens = s.split()
    return bytes(int(t, 16) for t in tokens if t)

# ── known command candidates ─────────────────────────────────────────────────
# Based on reverse engineering of similar HDSC / handheld scopemeter devices.
# Each entry: (description, bytes_to_send)
KNOWN_COMMANDS = [
    # --- Generic polling / handshake patterns ---
    ("Single 0x00",            b"\x00"),
    ("Single 0x55 (sync)",     b"\x55"),
    ("Single 0xAA (sync)",     b"\xAA"),
    ("Single 0xFF",            b"\xFF"),
    ("Single 0x0D (CR)",       b"\x0D"),
    ("CRLF",                   b"\x0D\x0A"),

    # --- Common SCPI-like ASCII (some hybrid devices accept these) ---
    ("*IDN?",                  b"*IDN?\r\n"),
    ("*IDN?\\n",               b"*IDN?\n"),
    ("MODE?",                  b"MODE?\r\n"),

    # --- Hantek 2D72 / similar HDSC protocol patterns ---
    # Header: AA BB + cmd byte + length + payload + checksum
    ("HDSC handshake AA BB 00",  b"\xAA\xBB\x00\x00\x00"),
    ("HDSC handshake AA BB 01",  b"\xAA\xBB\x01\x00\x01"),
    ("HDSC get mode    AA BB 02",b"\xAA\xBB\x02\x00\x02"),
    ("HDSC DMM poll   AA BB 10", b"\xAA\xBB\x10\x00\x10"),
    ("HDSC OSC poll   AA BB 20", b"\xAA\xBB\x20\x00\x20"),

    # --- Owon HDS-series style (for comparison) ---
    ("OWON start  \xC0\x00",   b"\xC0\x00"),
    ("OWON query  \xC1\x00",   b"\xC1\x00"),

    # --- UT61E / sigrok-style NUL poll ---
    ("NUL x3",                 b"\x00\x00\x00"),
    ("0x60 (UT/Cyrustek poll)",b"\x60"),
    ("0x61",                   b"\x61"),

    # --- Sequence found in some budget HDSC meters ---
    ("5A A5 00 00",            b"\x5A\xA5\x00\x00"),
    ("5A A5 01 00 01",         b"\x5A\xA5\x01\x00\x01"),
    ("5A A5 02 00 02",         b"\x5A\xA5\x02\x00\x02"),
]

COMMON_BAUDS = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]

# ── logger ───────────────────────────────────────────────────────────────────
class Logger:
    def __init__(self, path: str):
        self.f = open(path, "w", encoding="utf-8")
        self.f.write(f"ET829 Probe Log — {datetime.now()}\n{'='*60}\n")

    def log(self, direction: str, data: bytes, note: str = ""):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = f"\n[{ts}] {direction}"
        if note:
            entry += f"  ({note})"
        entry += f"\n{hexdump(data, '  ')}\n"
        self.f.write(entry)
        self.f.flush()

    def write(self, text: str):
        self.f.write(text + "\n")
        self.f.flush()

    def close(self):
        self.f.close()

# ── serial helpers ────────────────────────────────────────────────────────────
def open_port(port: str, baud: int, timeout: float = 0.5) -> serial.Serial:
    return serial.Serial(
        port=port, baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout,
        write_timeout=2,
        xonxoff=False, rtscts=False, dsrdtr=False
    )

def send_recv(ser: serial.Serial, cmd: bytes,
              wait: float = 0.3, max_bytes: int = 4096) -> bytes:
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    return ser.read(max_bytes)

# ── baud rate scanner ─────────────────────────────────────────────────────────
def scan_baud(port: str, logger: Logger) -> int | None:
    cprint(CYAN, f"\n[BAUD SCAN] Testing {len(COMMON_BAUDS)} baud rates on {port}...")
    logger.write(f"\n[BAUD SCAN] port={port}")

    for baud in COMMON_BAUDS:
        cprint(GREY, f"  Trying {baud}...", end=" ")
        try:
            with open_port(port, baud, timeout=0.8) as ser:
                time.sleep(0.1)
                ser.reset_input_buffer()

                # Send a few probe bytes
                for probe in [b"\x00", b"\xAA\xBB\x00\x00\x00", b"*IDN?\r\n"]:
                    ser.write(probe)
                    time.sleep(0.3)
                    data = ser.read(256)
                    if data:
                        cprint(GREEN, f"GOT RESPONSE at {baud} baud!")
                        print(hexdump(data, "    "))
                        logger.write(f"  {baud}: RESPONSE to {probe.hex()}")
                        logger.log("RX", data, f"baud={baud}")
                        return baud

                # Also just listen without sending
                leftover = ser.read(128)
                if leftover:
                    cprint(GREEN, f"UNSOLICITED data at {baud}!")
                    print(hexdump(leftover, "    "))
                    logger.log("RX (unsolicited)", leftover, f"baud={baud}")
                    return baud

                print("no response")
                logger.write(f"  {baud}: no response")

        except serial.SerialException as e:
            cprint(RED, f"ERROR: {e}")

    cprint(YELLOW, "[BAUD SCAN] No response detected at any baud rate.")
    cprint(YELLOW, "           This is expected — device likely needs a specific command.")
    cprint(YELLOW, "           Defaulting to 115200. Run --brute next.\n")
    logger.write("[BAUD SCAN] No response; defaulting to 115200")
    return 115200

# ── known-command prober ──────────────────────────────────────────────────────
def probe_known(port: str, baud: int, logger: Logger):
    cprint(CYAN, f"\n[KNOWN COMMANDS] Testing {len(KNOWN_COMMANDS)} command patterns @ {baud} baud...")
    logger.write(f"\n[KNOWN COMMANDS] port={port} baud={baud}")
    hits = []

    try:
        with open_port(port, baud, timeout=0.6) as ser:
            for desc, cmd in KNOWN_COMMANDS:
                ser.reset_input_buffer()
                ser.write(cmd)
                time.sleep(0.4)
                resp = ser.read(4096)

                status = f"  [{desc:40s}] TX: {cmd.hex():20s}"
                if resp:
                    cprint(GREEN, status + f"  ← {len(resp)} bytes!")
                    print(hexdump(resp, "    "))
                    logger.log("TX", cmd, desc)
                    logger.log("RX", resp, desc)
                    hits.append((desc, cmd, resp))
                else:
                    cprint(GREY, status + "  (no response)")
                    logger.write(f"  TX {cmd.hex()} ({desc}): no response")

                time.sleep(0.1)

    except serial.SerialException as e:
        cprint(RED, f"Serial error: {e}")

    if hits:
        cprint(GREEN, f"\n✓ {len(hits)} command(s) triggered a response:")
        for desc, cmd, resp in hits:
            print(f"  {desc}: {cmd.hex()} → {resp.hex()[:64]}{'...' if len(resp)>32 else ''}")
    else:
        cprint(YELLOW, "\nNo known commands triggered a response.")
        cprint(YELLOW, "Run --brute to scan all 256 single-byte values.")

# ── brute-force single-byte scanner ──────────────────────────────────────────
def brute_force(port: str, baud: int, logger: Logger):
    cprint(CYAN, f"\n[BRUTE FORCE] Scanning all 256 single bytes @ {baud} baud...")
    cprint(CYAN, "This will take ~2 minutes. Press Ctrl+C to stop early.\n")
    logger.write(f"\n[BRUTE FORCE] port={port} baud={baud}")
    hits = []

    try:
        with open_port(port, baud, timeout=0.4) as ser:
            for b in range(256):
                cmd = bytes([b])
                try:
                    ser.reset_input_buffer()
                    ser.write(cmd)
                    time.sleep(0.35)
                    resp = ser.read(4096)

                    marker = "  " if not resp else GREEN + "→ "
                    sys.stdout.write(
                        f"\r{marker}0x{b:02X} ({b:3d})" + RESET +
                        (f"  {len(resp)} bytes: {resp.hex()[:32]}" if resp else "")
                    )
                    sys.stdout.flush()

                    if resp:
                        print()  # newline after hit
                        logger.log("TX", cmd, f"byte 0x{b:02X}")
                        logger.log("RX", resp, f"response to 0x{b:02X}")
                        hits.append((b, resp))

                except serial.SerialException as e:
                    cprint(RED, f"\nSerial error at byte 0x{b:02X}: {e}")
                    break

    except KeyboardInterrupt:
        print()
        cprint(YELLOW, "Brute-force interrupted by user.")

    print()
    if hits:
        cprint(GREEN, f"\n✓ {len(hits)} byte(s) triggered a response:")
        for b, resp in hits:
            print(f"  0x{b:02X} → {resp.hex()}")
    else:
        cprint(YELLOW, "No single byte triggered a response.")
        cprint(YELLOW, "The protocol likely requires a multi-byte header.")

# ── passive listener ──────────────────────────────────────────────────────────
def listen_passive(port: str, baud: int, duration: int, logger: Logger):
    cprint(CYAN, f"\n[PASSIVE LISTEN] {port} @ {baud} baud for {duration}s")
    cprint(CYAN, "Waiting for unsolicited data (switch modes on device now)...\n")
    logger.write(f"\n[PASSIVE LISTEN] port={port} baud={baud} duration={duration}s")

    total = b""
    deadline = time.time() + duration

    try:
        with open_port(port, baud, timeout=0.2) as ser:
            while time.time() < deadline:
                chunk = ser.read(4096)
                if chunk:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    total += chunk
                    cprint(GREEN, f"[{ts}] {len(chunk)} bytes received:")
                    print(hexdump(chunk, "  "))
                    logger.log("RX (passive)", chunk)
                remaining = int(deadline - time.time())
                sys.stdout.write(f"\r  Listening... {remaining:3d}s remaining")
                sys.stdout.flush()

    except KeyboardInterrupt:
        pass

    print()
    if total:
        cprint(GREEN, f"\nTotal received: {len(total)} bytes")
        cprint(GREEN, "Check the log file for full dump.")
    else:
        cprint(YELLOW, "No data received passively.")
        cprint(YELLOW, "Device confirmed to be request/response only.")

# ── interactive shell ─────────────────────────────────────────────────────────
def interactive_shell(port: str, baud: int, logger: Logger):
    cprint(CYAN, f"\n[INTERACTIVE SHELL] {port} @ {baud} baud")
    print("Enter hex bytes to send (e.g.:  AA BB 0D  or  AABB0D)")
    print("Commands:  quit | baud <rate> | listen | clear")
    print("─" * 60)
    logger.write(f"\n[SHELL] port={port} baud={baud}")

    # Background reader thread
    stop_event = threading.Event()

    try:
        ser = open_port(port, baud, timeout=0.1)

        def bg_reader():
            buf = b""
            last_print = time.time()
            while not stop_event.is_set():
                try:
                    chunk = ser.read(256)
                    if chunk:
                        buf += chunk
                        last_print = time.time()
                    elif buf and (time.time() - last_print) > 0.15:
                        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        cprint(GREEN, f"\n[{ts}] RX ({len(buf)} bytes):")
                        print(hexdump(buf, "  "))
                        logger.log("RX", buf)
                        buf = b""
                except Exception:
                    pass
            if buf:
                cprint(GREEN, f"\n[final] RX ({len(buf)} bytes):")
                print(hexdump(buf, "  "))

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

            if line.lower() == "quit":
                break
            elif line.lower().startswith("baud "):
                try:
                    new_baud = int(line.split()[1])
                    ser.close()
                    current_baud = new_baud
                    ser = open_port(port, new_baud, timeout=0.1)
                    cprint(GREEN, f"Switched to {new_baud} baud")
                    logger.write(f"[SHELL] baud changed to {new_baud}")
                except Exception as e:
                    cprint(RED, f"Error: {e}")
            elif line.lower() == "listen":
                cprint(CYAN, "Listening for 10s (Ctrl+C to stop)...")
                try:
                    time.sleep(10)
                except KeyboardInterrupt:
                    pass
            elif line.lower() == "clear":
                ser.reset_input_buffer()
                cprint(YELLOW, "Input buffer cleared.")
            else:
                try:
                    tx = parse_hex_input(line)
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    cprint(YELLOW, f"[{ts}] TX ({len(tx)} bytes): {tx.hex().upper()}")
                    ser.reset_input_buffer()
                    ser.write(tx)
                    logger.log("TX", tx, "shell")
                except ValueError as e:
                    cprint(RED, f"Parse error: {e} — use hex like 'AA BB 0D'")

        stop_event.set()
        t.join(timeout=1)
        ser.close()

    except serial.SerialException as e:
        cprint(RED, f"Cannot open port: {e}")

# ── list available ports ──────────────────────────────────────────────────────
def list_ports():
    ports = serial.tools.list_ports.comports()
    if not ports:
        print("No serial ports found.")
        return
    print("\nAvailable serial ports:")
    for p in ports:
        print(f"  {p.device:<10} {p.description}")

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    # Enable ANSI on Windows
    if sys.platform == "win32":
        os.system("")

    parser = argparse.ArgumentParser(
        description="ET829/MDS8209 Serial Protocol Reverse Engineering Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--port",   default="COM8", help="Serial port (default: COM8)")
    parser.add_argument("--baud",   type=int, default=None, help="Fixed baud rate (skips scan)")
    parser.add_argument("--scan",   action="store_true", help="Auto-scan baud rate")
    parser.add_argument("--brute",  action="store_true", help="Brute-force single-byte commands")
    parser.add_argument("--known",  action="store_true", help="Try known command patterns")
    parser.add_argument("--listen", action="store_true", help="Passive listen mode (30s)")
    parser.add_argument("--shell",  action="store_true", help="Interactive hex shell")
    parser.add_argument("--list",   action="store_true", help="List available COM ports")
    parser.add_argument("--all",    action="store_true", help="Run scan + known + brute + shell")
    parser.add_argument("--duration", type=int, default=30, help="Listen duration in seconds")
    args = parser.parse_args()

    if args.list:
        list_ports()
        return

    log_path = f"et829_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger = Logger(log_path)
    cprint(CYAN, f"\nET829 / MDS8209 Protocol Probe  —  logging to {log_path}")
    cprint(GREY,  f"Port: {args.port}")

    # If no mode specified, default to known + shell
    run_scan  = args.scan  or args.all
    run_known = args.known or args.all or (not any([args.scan, args.brute, args.listen, args.shell]))
    run_brute = args.brute or args.all
    run_listen= args.listen
    run_shell = args.shell or args.all or (not any([args.scan, args.brute, args.listen, args.known]))

    # Determine baud rate
    if args.baud:
        baud = args.baud
        cprint(GREY, f"Using fixed baud rate: {baud}")
    elif run_scan:
        baud = scan_baud(args.port, logger)
    else:
        baud = 115200
        cprint(GREY, f"Using default baud rate: {baud}  (use --scan to auto-detect)")

    if run_known:
        probe_known(args.port, baud, logger)

    if run_listen:
        listen_passive(args.port, baud, args.duration, logger)

    if run_brute:
        brute_force(args.port, baud, logger)

    if run_shell:
        interactive_shell(args.port, baud, logger)

    logger.close()
    cprint(CYAN, f"\nDone. Full log saved to: {log_path}")

if __name__ == "__main__":
    main()