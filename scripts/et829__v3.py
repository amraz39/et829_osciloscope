"""
ET829 / MDS8209 — Protocol Probe v3
=====================================
Key insight from v2 results:
  - CRLF only ever returns 1-byte "mode ping" — not measurement data
  - A5 01 / 0D 01 return A5 21 01 39 — a different handshake/ACK
  - Frame NEVER changes even while measuring → device streams after handshake
  - Constant polling likely SUPPRESSES the auto-stream

Strategy v3:
  1. Send wake command ONCE, then LISTEN silently for streaming data
  2. Try different init sequences (CRLF, A5 01, 0D 01) then listen
  3. Try baud rate 9600 (many DMMs use this) after handshake
  4. Brute scan second byte variants of 0D xx that haven't been tried

Usage:
  python et829_v3.py              # wake-and-listen (main test)
  python et829_v3.py --init       # try all init sequences then listen
  python et829_v3.py --baud9600   # test at 9600 baud
  python et829_v3.py --shell      # interactive shell
  python et829_v3.py --brute2     # brute second byte of 0D xx
"""

import argparse, serial, serial.tools.list_ports
import time, sys, os, threading
from datetime import datetime

if sys.platform == "win32":
    os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; GRY="\033[90m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST)

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

def open_port(port, baud, timeout=0.5):
    return serial.Serial(port=port, baudrate=baud,
        bytesize=8, parity='N', stopbits=1,
        timeout=timeout, write_timeout=2,
        xonxoff=False, rtscts=False, dsrdtr=False)

DMM_MODES={
    0x30:"DC Voltage",0x31:"AC Voltage",0x32:"DC Current",
    0x33:"AC Current",0x34:"Resistance",0x35:"Capacitance",
    0x36:"Frequency",0x37:"Diode",0x38:"Continuity",
    0x39:"Temperature",0x3A:"NCV",
}

def describe(raw):
    """Quick human-readable description of any received bytes."""
    if not raw: return "(empty)"
    if len(raw)>=4 and raw[0]==0xA5:
        cmd=raw[1]
        # Try 2-byte length field
        plen2=int.from_bytes(raw[2:4],"little")
        payload2=raw[4:4+plen2] if len(raw)>=4+plen2 else raw[4:]
        # Try 1-byte length field
        plen1=raw[2]
        payload1=raw[3:3+plen1] if len(raw)>=3+plen1 else raw[3:]

        # Pick whichever length makes more sense
        if 4+plen2<=len(raw)<=4+plen2+2:
            payload=payload2; plen=plen2; hdr="2-byte len"
        elif 3+plen1<=len(raw)<=3+plen1+2:
            payload=payload1; plen=plen1; hdr="1-byte len"
        else:
            payload=raw[2:]; plen=len(payload); hdr="(unknown len)"

        mode_str=""
        if payload and payload[0] in DMM_MODES:
            mode_str=f"  mode={DMM_MODES[payload[0]]}"
        if plen>4:
            # Attempt value decode: int16 BE at offset 1
            import struct
            try:
                v16=struct.unpack_from(">h",payload,1)[0]
                dec=payload[3] if len(payload)>3 else 0
                val=v16/(10**dec) if dec<=6 else v16
                mode_str+=f"  VALUE={val}"
            except: pass
        return (f"A5-frame cmd=0x{cmd:02X} {hdr} plen={plen}"
                f"{mode_str}  payload={payload.hex().upper()}")
    return f"raw={raw.hex().upper()}"

# ─────────────────────────────────────────────────────────────────────────────
# WAKE AND LISTEN
# Send the wake command ONCE, then listen silently for streaming data.
# This is the key test — constant polling may suppress the data stream.
# ─────────────────────────────────────────────────────────────────────────────
WAKE_SEQUENCES = [
    ("CRLF",          b"\x0D\x0A"),
    ("0D 01",         b"\x0D\x01"),
    ("A5 01 00 00 01",b"\xA5\x01\x00\x00\x01"),
    ("CRLF + 0D 01",  b"\x0D\x0A\x0D\x01"),
]

def wake_and_listen(port, baud, wake_cmd, duration=30, label=""):
    cp(CYN, f"\n[WAKE+LISTEN] Sending {hx(wake_cmd)} once, then silent for {duration}s")
    cp(YLW, "Make sure meter is ON and measuring something. Press Enter...")
    input()

    total=b""; chunks=[]; prev=None
    try:
        with open_port(port, baud, timeout=0.1) as ser:
            ser.reset_input_buffer()
            time.sleep(0.05)
            ser.write(wake_cmd)
            ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
            cp(YLW, f"[{ts}] Sent {hx(wake_cmd)} — now listening silently...")

            deadline=time.time()+duration
            while time.time()<deadline:
                chunk=ser.read(256)
                if chunk:
                    total+=chunk
                    if chunk!=prev:
                        ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        cp(BLD+GRN, f"[{ts}] {len(chunk)} bytes:  {describe(chunk)}")
                        print(hexdump(chunk,"  "))
                        chunks.append(chunk)
                    prev=chunk
                rem=int(deadline-time.time())
                sys.stdout.write(f"\r  listening... {rem:3d}s  total={len(total)}B  ")
                sys.stdout.flush()

    except KeyboardInterrupt:
        pass

    print()
    if total:
        cp(GRN, f"✓ Received {len(total)} bytes total in {len(chunks)} chunks!")
        cp(GRN, "THIS IS THE AUTO-STREAM! Paste output here for full decoding.")
    else:
        cp(YLW, f"No data after {hx(wake_cmd)} wake + silence.")
    return bool(total)

def try_all_inits(port, baud):
    cp(CYN, "\n[INIT SEQUENCES] Trying all wake sequences then listening 15s each")
    for label, cmd in WAKE_SEQUENCES:
        cp(CYN, f"\n── Testing: {label} ──")
        found=wake_and_listen(port, baud, cmd, duration=15, label=label)
        if found:
            cp(BLD+GRN, f"SUCCESS with: {label}  ({hx(cmd)})")
            break
        else:
            cp(YLW, "No stream. Trying next...")

# ─────────────────────────────────────────────────────────────────────────────
# BRUTE SECOND BYTE  (0D 00 → 0D FF, skipping known ones)
# ─────────────────────────────────────────────────────────────────────────────
SKIP_BYTES={0x0A, 0x00, 0x01, 0x02, 0x03, 0x10, 0x20, 0x30, 0x40, 0x50, 0xA5, 0x2A, 0xFF}

def brute_second_byte(port, baud):
    cp(CYN, f"\n[BRUTE 0D xx] Testing remaining second bytes on {port} @ {baud}")
    cp(YLW, "Press Enter to start...")
    input()
    hits=[]
    try:
        with open_port(port, baud, timeout=0.4) as ser:
            for b in range(256):
                if b in SKIP_BYTES: continue
                cmd=bytes([0x0D, b])
                ser.reset_input_buffer()
                ser.write(cmd)
                time.sleep(0.3)
                resp=ser.read(512)
                if resp:
                    cp(GRN, f"  0D {b:02X} → {resp.hex().upper()}  {describe(resp)}")
                    hits.append((b,resp))
                else:
                    sys.stdout.write(f"\r  0D {b:02X} → (no response)  ")
                    sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    print()
    cp(GRN if hits else YLW,
       f"{len(hits)} hits" if hits else "No additional responses found.")

# ─────────────────────────────────────────────────────────────────────────────
# BAUD RATE TEST  — try 9600 (very common for DMMs)
# ─────────────────────────────────────────────────────────────────────────────
def test_baud(port):
    cp(CYN, "\n[BAUD TEST] Testing 9600, 19200, 38400, 57600, 115200, 230400")
    cp(YLW, "Press Enter to start...")
    input()
    for baud in [9600, 19200, 38400, 57600, 115200, 230400]:
        cp(GRY, f"\n  Trying {baud}...")
        try:
            with open_port(port, baud, timeout=0.5) as ser:
                ser.reset_input_buffer()
                # First just listen (maybe device auto-sends at this baud)
                passive=ser.read(128)
                if passive:
                    cp(GRN, f"  {baud}: UNSOLICITED data! {passive.hex().upper()}")
                    continue
                # Then try CRLF
                ser.write(b"\x0D\x0A")
                time.sleep(0.3)
                r=ser.read(256)
                if r:
                    cp(GRN, f"  {baud}: CRLF response! {r.hex().upper()}  {describe(r)}")
                    # Now listen silently for 5s
                    dl=time.time()+5
                    while time.time()<dl:
                        c=ser.read(128)
                        if c:
                            cp(GRN, f"  {baud}: stream data! {c.hex().upper()}")
                else:
                    cp(GRY, f"  {baud}: no response")
        except serial.SerialException as e:
            cp(RED, f"  {baud}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE SHELL
# ─────────────────────────────────────────────────────────────────────────────
def shell(port, baud):
    cp(CYN, f"\n[SHELL] {port} @ {baud}")
    print("Formats: AA BB 0D | AABB0D")
    print("Cmds: wake | listen <sec> | baud <n> | quit")
    print("─"*60)
    stop=threading.Event()
    try:
        ser=open_port(port, baud, timeout=0.05)
    except serial.SerialException as e:
        cp(RED, f"Cannot open {port}: {e}"); return

    def reader():
        buf=b""; lt=time.time()
        while not stop.is_set():
            try:
                ch=ser.read(128)
                if ch: buf+=ch; lt=time.time()
                elif buf and time.time()-lt>0.12:
                    ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    cp(GRN, f"\n[{ts}] RX {len(buf)}B  {describe(buf)}")
                    print(hexdump(buf,"  "))
                    buf=b""
            except: pass

    threading.Thread(target=reader, daemon=True).start()
    cb=baud
    while True:
        try:
            line=input(f"\n[{cb}] TX> ").strip()
        except (EOFError, KeyboardInterrupt): break
        if not line: continue
        lo=line.lower()
        if lo=="quit": break
        elif lo=="wake":
            cp(YLW, "Sending CRLF wake, then silent...")
            ser.reset_input_buffer()
            ser.write(b"\x0D\x0A")
        elif lo.startswith("listen"):
            try: secs=int(lo.split()[1])
            except: secs=30
            cp(CYN, f"Listening {secs}s (no TX)...")
            time.sleep(secs)
        elif lo.startswith("baud "):
            try:
                nb=int(lo.split()[1])
                ser.close(); ser=open_port(port,nb,timeout=0.05); cb=nb
                cp(GRN, f"→ {nb} baud")
            except Exception as e: cp(RED,str(e))
        else:
            try:
                tx=parse_hex(line)
                ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cp(YLW, f"[{ts}] TX {len(tx)}B: {tx.hex().upper()}")
                ser.reset_input_buffer(); ser.write(tx)
            except Exception as e:
                cp(RED, f"Parse error: {e}")
    stop.set()
    try: ser.close()
    except: pass

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap=argparse.ArgumentParser(description="ET829 v3 - Wake & Listen Protocol Probe")
    ap.add_argument("--port",    default="COM8")
    ap.add_argument("--baud",    type=int, default=115200)
    ap.add_argument("--wake",    metavar="HEX", default="0D0A",
                    help="Wake command hex (default 0D0A)")
    ap.add_argument("--duration",type=int, default=30,
                    help="Listen duration after wake (default 30s)")
    ap.add_argument("--init",    action="store_true",
                    help="Try all init sequences then listen")
    ap.add_argument("--brute2",  action="store_true",
                    help="Brute second byte of 0D xx")
    ap.add_argument("--baudtest",action="store_true",
                    help="Test all common baud rates")
    ap.add_argument("--shell",   action="store_true")
    ap.add_argument("--list",    action="store_true")
    args=ap.parse_args()

    if args.list:
        for p in serial.tools.list_ports.comports():
            print(f"  {p.device:<10} {p.description}")
        return

    if args.init:
        try_all_inits(args.port, args.baud)
    elif args.brute2:
        brute_second_byte(args.port, args.baud)
    elif args.baudtest:
        test_baud(args.port)
    elif args.shell:
        shell(args.port, args.baud)
    else:
        # Default: wake with specified command, then listen silently
        try:
            wake_cmd=parse_hex(args.wake)
        except Exception:
            wake_cmd=b"\x0D\x0A"
        wake_and_listen(args.port, args.baud, wake_cmd, args.duration)

if __name__=="__main__":
    main()