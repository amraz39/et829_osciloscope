"""
ET829 / MDS8209 — DTR/RTS Signal Trigger Test  v7
===================================================
NEW THEORY: CDC ACM devices use DTR (Data Terminal Ready) as a signal
that the host application is "connected". Many devices only start
streaming measurement data AFTER DTR goes HIGH.

pyserial's default opens with DTR=True, but we've been using
dsrdtr=False which may leave DTR in an undefined state.

This tool explicitly controls DTR and RTS to find the trigger.

Also tries SET_LINE_CODING variations — some devices only respond
at specific baud rates sent via the CDC control channel.

Usage:
  python et829_v7.py            # try all DTR/RTS combinations
  python et829_v7.py --dtr      # just open with DTR=True and listen
  python et829_v7.py --shell    # manual DTR/RTS control shell

Requirements: pip install pyserial
"""

import argparse, serial, serial.tools.list_ports
import time, sys, os, threading, struct
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
    if not raw: return ""
    if raw[0]==0xA5 and len(raw)>=2:
        cmd=raw[1]
        ann = f"A5 cmd=0x{cmd:02X}"
        if len(raw)>=5: ann += f" mode={DMM_MODES.get(raw[4],'?')}"
        if len(raw)>5:
            ann += f" extra={raw[5:].hex().upper()}"
        return ann
    return f"raw={raw.hex().upper()}"

def drain(ser, wait=0.3, maxb=4096):
    time.sleep(wait)
    buf=b""
    dl=time.time()+0.5
    while time.time()<dl:
        c=ser.read(256)
        if c: buf+=c; dl=time.time()+0.15
        elif buf: break
    return buf

# ─────────────────────────────────────────────────────────────────────────────
# Try all DTR/RTS combinations with and without init commands
# ─────────────────────────────────────────────────────────────────────────────
def try_all_combinations(port, baud):
    cp(CYN, f"\n[DTR/RTS TEST] {port} @ {baud}")
    cp(YLW, "Meter ON, measuring DC voltage. Press Enter...")
    input()

    combos = [
        ("DTR=T RTS=T", True,  True),
        ("DTR=T RTS=F", True,  False),
        ("DTR=F RTS=T", False, True),
        ("DTR=F RTS=F", False, False),
    ]

    cmds_after = [
        ("(listen only)",  None),
        ("CRLF ping",      b"\x0D\x0A"),
        ("ENTER DMM",      b"\x0D\x01"),
    ]

    hits = []

    for dtr_label, dtr, rts in combos:
        for cmd_label, cmd in cmds_after:
            cp(GRY, f"\n  [{dtr_label}] {cmd_label}")
            try:
                # Open with explicit DTR/RTS
                ser = serial.Serial()
                ser.port    = port
                ser.baudrate= baud
                ser.bytesize= 8
                ser.parity  = 'N'
                ser.stopbits= 1
                ser.timeout = 0.1
                ser.write_timeout = 1
                ser.xonxoff = False
                ser.rtscts  = False
                ser.dsrdtr  = False
                ser.open()

                # Set DTR/RTS explicitly BEFORE anything else
                ser.dtr = dtr
                ser.rts = rts
                time.sleep(0.2)
                ser.reset_input_buffer()

                # Optionally send a command
                if cmd:
                    ser.write(cmd)

                # Listen for 1.5s
                all_data = b""
                dl = time.time() + 1.5
                while time.time() < dl:
                    chunk = ser.read(256)
                    if chunk:
                        all_data += chunk
                ser.close()

                if all_data:
                    # Check if it's more than our usual 7-byte ping response
                    is_new = len(all_data) > 7 or (all_data != b"\xA5\x2A\x01\x00\x30\x00\x00"
                                                    and all_data[:4] != b"\xA5\x21\x01\x39")
                    marker = BLD+GRN if is_new else GRY
                    cp(marker, f"    → {len(all_data)}B: {all_data.hex().upper()[:80]}")
                    if is_new:
                        print(hexdump(all_data, "      "))
                        hits.append((dtr_label, cmd_label, all_data))
                else:
                    cp(GRY, f"    → (no data)")

            except Exception as e:
                cp(RED, f"    Error: {e}")
            time.sleep(0.2)

    print()
    if hits:
        cp(BLD+GRN, f"INTERESTING RESULTS ({len(hits)}):")
        for dl, cl, data in hits:
            print(f"  {dl} + {cl}: {data.hex().upper()}")
            print(f"  {describe(data)}")
    else:
        cp(YLW, "No new data found with any DTR/RTS combination.")

# ─────────────────────────────────────────────────────────────────────────────
# Try different baud rates WITH proper DTR
# Some CDC devices only send data when baud matches what firmware expects
# ─────────────────────────────────────────────────────────────────────────────
def try_bauds_with_dtr(port):
    bauds = [1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600,
             115200, 230400, 500000, 1000000]
    cp(CYN, f"\n[BAUD+DTR TEST] Testing {len(bauds)} baud rates with DTR=True")
    cp(YLW, "Meter ON and measuring. Press Enter...")
    input()

    for baud in bauds:
        try:
            ser = serial.Serial()
            ser.port=port; ser.baudrate=baud; ser.bytesize=8
            ser.parity='N'; ser.stopbits=1; ser.timeout=0.15
            ser.xonxoff=False; ser.rtscts=False; ser.dsrdtr=False
            ser.open()
            ser.dtr = True
            ser.rts = True
            time.sleep(0.3)
            ser.reset_input_buffer()

            # Send CRLF and listen
            ser.write(b"\x0D\x0A")
            time.sleep(0.3)
            data = ser.read(512)
            if not data:
                # Just listen
                time.sleep(0.5)
                data = ser.read(512)
            ser.close()

            if data:
                cp(GRN if len(data)>7 else GRY,
                   f"  {baud:8d}: {data.hex().upper()[:60]}  ({len(data)}B)")
            else:
                cp(GRY, f"  {baud:8d}: (no response)")
        except Exception as e:
            cp(RED, f"  {baud:8d}: {e}")
        time.sleep(0.1)

# ─────────────────────────────────────────────────────────────────────────────
# DTR listen — simplest possible test
# ─────────────────────────────────────────────────────────────────────────────
def dtr_listen(port, baud, duration=30):
    cp(CYN, f"\n[DTR LISTEN] {port} @ {baud}  DTR=True  {duration}s")
    cp(YLW, "Meter ON and measuring. Press Enter to start...")
    input()

    seen = {}
    total = b""

    try:
        ser = serial.Serial()
        ser.port=port; ser.baudrate=baud; ser.bytesize=8
        ser.parity='N'; ser.stopbits=1; ser.timeout=0.02
        ser.xonxoff=False; ser.rtscts=False; ser.dsrdtr=False
        ser.open()

        cp(GRN, f"DTR before open: {ser.dtr}")
        ser.dtr = True
        ser.rts = True
        cp(GRN, f"DTR set to True, RTS set to True")
        cp(YLW, "\nListening... change ranges and modes on the meter!\n")

        buf = b""
        lt  = time.time()
        dl  = time.time() + duration

        while time.time() < dl:
            chunk = ser.read(256)
            if chunk:
                buf += chunk
                total += chunk
                lt = time.time()
            elif buf and (time.time()-lt) > 0.08:
                is_new = buf not in seen
                seen[buf] = seen.get(buf,0)+1
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                marker = BLD+GRN if is_new else GRY
                cp(marker, f"[{ts}] {'NEW ' if is_new else '    '}{len(buf)}B: {buf.hex().upper()}  {describe(buf)}")
                if is_new and len(buf) > 7:
                    print(hexdump(buf, "  "))
                buf = b""

            rem = int(dl-time.time())
            sys.stdout.write(f"\r  unique={len(seen)} total={len(total)}B {rem:3d}s  ")
            sys.stdout.flush()

        ser.close()

    except KeyboardInterrupt:
        pass
    except Exception as e:
        cp(RED, f"Error: {e}")

    print()
    cp(CYN, f"\nResult: {len(seen)} unique frames, {len(total)} total bytes")
    for raw, cnt in seen.items():
        cp(GRN, f"  x{cnt:3d}  {raw.hex().upper()}  {describe(raw)}")

# ─────────────────────────────────────────────────────────────────────────────
# Interactive shell with DTR control
# ─────────────────────────────────────────────────────────────────────────────
def shell(port, baud):
    cp(CYN, f"\n[SHELL] {port} @ {baud}")
    print("Commands: dtr [0/1] | rts [0/1] | tx <hex> | ping | init | listen <s> | quit")
    print("─"*60)

    stop = threading.Event()
    try:
        ser = serial.Serial()
        ser.port=port; ser.baudrate=baud; ser.bytesize=8
        ser.parity='N'; ser.stopbits=1; ser.timeout=0.02
        ser.xonxoff=False; ser.rtscts=False; ser.dsrdtr=False
        ser.open()
        ser.dtr=True; ser.rts=True
        cp(GRN, f"Opened. DTR=True RTS=True")
    except Exception as e:
        cp(RED, f"Cannot open {port}: {e}"); return

    def reader():
        buf=b""; lt=time.time()
        while not stop.is_set():
            try:
                ch=ser.read(128)
                if ch: buf+=ch; lt=time.time()
                elif buf and time.time()-lt>0.1:
                    ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    cp(GRN, f"\n[{ts}] RX {len(buf)}B: {buf.hex().upper()}  {describe(buf)}")
                    if len(buf)>7: print(hexdump(buf,"  "))
                    buf=b""
            except: pass
    threading.Thread(target=reader,daemon=True).start()

    while True:
        try:
            line=input(f"\n[DTR={ser.dtr} RTS={ser.rts}] TX> ").strip()
        except (EOFError,KeyboardInterrupt): break
        if not line: continue
        lo=line.lower()
        if lo=="quit": break
        elif lo.startswith("dtr"):
            val = lo.split()[1]=="1" if len(lo.split())>1 else True
            ser.dtr=val; cp(YLW,f"DTR → {val}")
        elif lo.startswith("rts"):
            val = lo.split()[1]=="1" if len(lo.split())>1 else True
            ser.rts=val; cp(YLW,f"RTS → {val}")
        elif lo=="ping":
            ser.reset_input_buffer(); ser.write(b"\x0D\x0A")
            cp(YLW,"Sent CRLF ping")
        elif lo=="init":
            ser.reset_input_buffer(); ser.write(b"\x0D\x01")
            cp(YLW,"Sent ENTER DMM")
        elif lo.startswith("listen"):
            try: secs=int(lo.split()[1])
            except: secs=10
            cp(CYN,f"Silent {secs}s..."); time.sleep(secs)
        else:
            try:
                tx=parse_hex(line)
                ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                cp(YLW,f"[{ts}] TX {len(tx)}B: {tx.hex().upper()}")
                ser.reset_input_buffer(); ser.write(tx)
            except Exception as e:
                cp(RED,f"Parse error: {e}")
    stop.set()
    try: ser.close()
    except: pass

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap=argparse.ArgumentParser(description="ET829 v7 - DTR/RTS Signal Test")
    ap.add_argument("--port",     default="COM8")
    ap.add_argument("--baud",     type=int, default=115200)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--dtr",      action="store_true", help="DTR listen mode")
    ap.add_argument("--bauds",    action="store_true", help="Test all baud rates with DTR")
    ap.add_argument("--shell",    action="store_true")
    ap.add_argument("--list",     action="store_true")
    args=ap.parse_args()

    if args.list:
        for p in serial.tools.list_ports.comports():
            print(f"  {p.device:<10} {p.description}")
        return

    if args.dtr:
        dtr_listen(args.port, args.baud, args.duration)
    elif args.bauds:
        try_bauds_with_dtr(args.port)
    elif args.shell:
        shell(args.port, args.baud)
    else:
        try_all_combinations(args.port, args.baud)

if __name__=="__main__":
    main()