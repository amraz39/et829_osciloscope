"""
ET829 / MDS8209 — Measurement Data Finder + Logger v2
=======================================================
Known so far:
  TX  0D 0A          → RX  A5 2A 01 00 30 00 00
                              sync cmd len  payload  (mode=DC Voltage, no value yet)

Goal: find the command that returns the actual numeric reading.

Usage:
  python et829_v2.py              # run full measurement-command discovery
  python et829_v2.py --log        # continuous logger once command is found
  python et829_v2.py --csv out.csv
  python et829_v2.py --shell      # interactive shell
  python et829_v2.py --watch      # poll CRLF at 10Hz and dump every unique frame

Requirements: pip install pyserial
"""

import argparse, serial, serial.tools.list_ports
import time, sys, os, csv, threading, struct
from datetime import datetime

# ── colours ──────────────────────────────────────────────────────────────────
if sys.platform == "win32":
    os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; GRY="\033[90m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST)

# ── hex helpers ───────────────────────────────────────────────────────────────
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

# ── port helper ───────────────────────────────────────────────────────────────
def open_port(port, baud, timeout=0.5):
    return serial.Serial(port=port, baudrate=baud,
        bytesize=8, parity='N', stopbits=1,
        timeout=timeout, write_timeout=2,
        xonxoff=False, rtscts=False, dsrdtr=False)

def transact(ser, cmd, wait=0.25, maxb=4096):
    ser.reset_input_buffer()
    ser.write(cmd)
    time.sleep(wait)
    buf=b""
    dl=time.time()+0.4
    while time.time()<dl:
        c=ser.read(256)
        if c: buf+=c; dl=time.time()+0.15
        elif buf: break
    return buf

# ── frame decoder ─────────────────────────────────────────────────────────────
DMM_MODES={
    0x00:"(off/init)",0x30:"DC Voltage",0x31:"AC Voltage",
    0x32:"DC Current",0x33:"AC Current",0x34:"Resistance",
    0x35:"Capacitance",0x36:"Frequency",0x37:"Diode",
    0x38:"Continuity",0x39:"Temperature",0x3A:"NCV",
}
UNITS={0x00:"",0x01:"mV",0x02:"V",0x03:"kV",
       0x10:"uA",0x11:"mA",0x12:"A",
       0x20:"Ohm",0x21:"kOhm",0x22:"MOhm",
       0x30:"nF",0x31:"uF",0x32:"mF",
       0x40:"Hz",0x41:"kHz",0x42:"MHz",
       0x50:"°C",0x51:"°F"}

def decode_frame(raw):
    """Returns dict with decoded fields, or None if invalid."""
    if len(raw)<4 or raw[0]!=0xA5:
        return None
    cmd=raw[1]
    plen=int.from_bytes(raw[2:4],"little")
    if len(raw)<4+plen:
        return None
    payload=raw[4:4+plen]
    chk=raw[4+plen] if len(raw)>4+plen else None
    d={"cmd":cmd,"plen":plen,"payload":payload,"checksum":chk,"raw":raw}

    # 1-byte payload = mode only
    if plen==1:
        d["mode"]=DMM_MODES.get(payload[0],f"0x{payload[0]:02X}")
        d["value"]=None; d["unit"]=""; d["display"]="(mode only)"
        return d

    # Try to decode multi-byte DMM payload
    # Common HDSC layouts:
    # [A] mode(1) val_int16_BE(2) decimal(1) unit(1) flags(1) ...
    # [B] mode(1) val_int32_BE(4) decimal(1) unit(1) flags(1) ...
    # [C] mode(1) bcd_digits(4)   decimal(1) unit(1) ...
    if plen>=5:
        mode_b=payload[0]
        d["mode"]=DMM_MODES.get(mode_b,f"0x{mode_b:02X}")

        # Try int16 BE (layout A)
        raw_i16=struct.unpack_from(">h",payload,1)[0]
        dec_pos=payload[3] if plen>3 else 0
        unit_b =payload[4] if plen>4 else 0
        flags  =payload[5] if plen>5 else 0
        divisor=10**dec_pos if dec_pos<=6 else 1
        val_a  =raw_i16/divisor

        # Try int32 BE (layout B)
        val_b=None
        if plen>=7:
            raw_i32=struct.unpack_from(">i",payload,1)[0]
            dec_b  =payload[5] if plen>5 else 0
            unit_b2=payload[6] if plen>6 else unit_b
            val_b  =raw_i32/(10**dec_b if dec_b<=9 else 1)

        d["value"]=val_a
        d["unit"]=UNITS.get(unit_b,f"0x{unit_b:02X}")
        d["flags"]=f"0x{flags:02X}"
        d["layout_A"]=f"{val_a} {UNITS.get(unit_b,'?')}"
        if val_b is not None:
            d["layout_B"]=f"{val_b} {UNITS.get(unit_b,'?')}"
        d["display"]=(f"{val_a:>12.4f} {UNITS.get(unit_b,'')}"
                      f"  (raw={raw_i16}, dec={dec_pos})")
        return d

    if plen>=3:
        mode_b=payload[0]
        d["mode"]=DMM_MODES.get(mode_b,f"0x{mode_b:02X}")
        raw_i16=struct.unpack_from(">h",payload,1)[0] if plen>=3 else 0
        d["value"]=raw_i16; d["unit"]=""
        d["display"]=f"raw int16={raw_i16}"
        return d

    d["mode"]=DMM_MODES.get(payload[0],f"0x{payload[0]:02X}") if payload else "?"
    d["value"]=None; d["unit"]=""; d["display"]="(short payload)"
    return d

# ── the candidate commands to find measurement data ───────────────────────────
# Strategy: now we know sync=A5, cmd=2A.
# Try every plausible "get measurement" command.
MEASURE_CMDS=[
    # ── Vary the trigger itself ──────────────────────────────────────────────
    ("CRLF (baseline)",        b"\x0D\x0A"),
    ("CRLF x2 rapid",          b"\x0D\x0A\x0D\x0A"),
    ("CRLF x3",                b"\x0D\x0A\x0D\x0A\x0D\x0A"),
    ("0D 0A 0D",               b"\x0D\x0A\x0D"),
    ("0A 0D",                  b"\x0A\x0D"),

    # ── A5-framed requests (mirror the response format back) ─────────────────
    # A5 + cmd + len(2LE) + payload + checksum
    ("A5 2A req (no payload)",  b"\xA5\x2A\x00\x00\x2A"),
    ("A5 2A req payload=01",    b"\xA5\x2A\x01\x00\x01\x2A"),
    ("A5 2A req payload=00",    b"\xA5\x2A\x01\x00\x00\x2A"),
    ("A5 2A req payload=30",    b"\xA5\x2A\x01\x00\x30\x5A"),
    ("A5 01 req",               b"\xA5\x01\x00\x00\x01"),
    ("A5 02 req",               b"\xA5\x02\x00\x00\x02"),
    ("A5 03 req",               b"\xA5\x03\x00\x00\x03"),
    ("A5 10 req",               b"\xA5\x10\x00\x00\x10"),
    ("A5 11 req",               b"\xA5\x11\x00\x00\x11"),
    ("A5 20 req",               b"\xA5\x20\x00\x00\x20"),
    ("A5 21 req",               b"\xA5\x21\x00\x00\x21"),
    ("A5 2B req",               b"\xA5\x2B\x00\x00\x2B"),
    ("A5 2C req",               b"\xA5\x2C\x00\x00\x2C"),
    ("A5 40 req",               b"\xA5\x40\x00\x00\x40"),
    ("A5 50 req",               b"\xA5\x50\x00\x00\x50"),
    ("A5 60 req",               b"\xA5\x60\x00\x00\x60"),
    ("A5 70 req",               b"\xA5\x70\x00\x00\x70"),
    ("A5 80 req",               b"\xA5\x80\x00\x00\x80"),
    ("A5 FF req",               b"\xA5\xFF\x00\x00\xFF"),

    # ── CRLF then immediately A5 request ────────────────────────────────────
    ("CRLF + A5 2A",            b"\x0D\x0A\xA5\x2A\x00\x00\x2A"),
    ("CRLF + A5 01",            b"\x0D\x0A\xA5\x01\x00\x00\x01"),

    # ── Two-byte sequences varying second byte after 0x0D ───────────────────
    ("0D 00",  b"\x0D\x00"), ("0D 01",  b"\x0D\x01"),
    ("0D 02",  b"\x0D\x02"), ("0D 03",  b"\x0D\x03"),
    ("0D 10",  b"\x0D\x10"), ("0D 20",  b"\x0D\x20"),
    ("0D 30",  b"\x0D\x30"), ("0D 40",  b"\x0D\x40"),
    ("0D 50",  b"\x0D\x50"), ("0D A5",  b"\x0D\xA5"),
    ("0D 2A",  b"\x0D\x2A"), ("0D FF",  b"\x0D\xFF"),
]

def find_measurement_cmd(port, baud):
    cp(CYN, f"\n[FIND] Testing {len(MEASURE_CMDS)} commands to get numeric value @ {baud} baud")
    cp(YLW, "Set meter to DC Voltage, probe something (e.g. a battery).")
    cp(YLW, "Press Enter when ready...")
    input()

    hits=[]
    best_len=1   # baseline is 7 bytes (1-byte payload)

    try:
        with open_port(port, baud, timeout=0.5) as ser:
            for desc, cmd in MEASURE_CMDS:
                resp=transact(ser, cmd, wait=0.3)
                label=f"  [{desc:35s}] {hx(cmd):<30}"
                if resp:
                    fr=decode_frame(resp)
                    plen=fr["plen"] if fr else 0
                    marker = GRN if plen>best_len else (YLW if plen==best_len else GRY)
                    cp(marker, label+f"← {len(resp)}B  payload={plen}B  {resp.hex().upper()}")
                    if fr and plen>1:
                        print(f"         display: {fr.get('display','?')}")
                    if plen>best_len:
                        cp(BLD+GRN, f"         *** LONGER PAYLOAD — possible measurement data! ***")
                        hits.append((desc,cmd,resp,plen))
                        best_len=plen
                    elif plen==best_len and plen>1:
                        hits.append((desc,cmd,resp,plen))
                else:
                    cp(GRY, label+"(no response)")
                time.sleep(0.12)

    except serial.SerialException as e:
        cp(RED, f"Port error: {e}")
        return None

    print()
    if hits:
        best=max(hits,key=lambda x:x[3])
        cp(BLD+GRN, f"Best candidate: [{best[0]}]  TX={hx(best[1])}")
        cp(GRN,     f"  Response: {best[2].hex().upper()}")
        fr=decode_frame(best[2])
        if fr:
            cp(GRN, f"  Decoded:  {fr.get('display','?')}")
        return best[1]
    else:
        cp(YLW, "No command returned more data than the baseline CRLF.")
        cp(YLW, "Suggestion: run --watch and change ranges on the meter.")
        cp(YLW, "The protocol may pack all data into CRLF but need a different")
        cp(YLW, "frame after the mode byte — try --watch to catch it.")
        return None

# ── watch mode: poll fast, catch any frame change ─────────────────────────────
def watch_mode(port, baud, duration=60):
    cp(CYN, f"\n[WATCH] Polling CRLF at 10 Hz for {duration}s on {port}")
    cp(YLW, "Change ranges, switch modes, measure things — watch for new frames!")
    cp(YLW, "Press Ctrl+C to stop early.\n")

    seen=set()
    count=0
    try:
        with open_port(port, baud, timeout=0.2) as ser:
            dl=time.time()+duration
            while time.time()<dl:
                resp=transact(ser, b"\x0D\x0A", wait=0.08)
                if resp and resp not in seen:
                    seen.add(resp)
                    ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    fr=decode_frame(resp)
                    cp(BLD+GRN, f"[{ts}] NEW frame #{len(seen)}:")
                    print(hexdump(resp,"  "))
                    if fr:
                        print(f"  → cmd=0x{fr['cmd']:02X}  plen={fr['plen']}"
                              f"  mode={fr.get('mode','?')}  {fr.get('display','')}")
                    count+=1
                rem=int(dl-time.time())
                sys.stdout.write(f"\r  unique frames={len(seen)}  {rem}s  ")
                sys.stdout.flush()
                time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    print()
    cp(CYN, f"\nSaw {len(seen)} unique frames in {duration}s")
    cp(CYN, "Paste the output here for decoding help!")

# ── continuous logger ─────────────────────────────────────────────────────────
def run_logger(port, baud, poll_cmd, rate_hz, csv_path, raw_mode):
    interval=1.0/rate_hz
    cp(CYN, f"\n[LOG] {port} @ {baud}  cmd={hx(poll_cmd)}  {rate_hz}Hz")
    if csv_path:
        cf=open(csv_path,"w",newline="",encoding="utf-8")
        cw=csv.writer(cf)
        cw.writerow(["timestamp","mode","value","unit","flags","payload_hex","raw_hex"])
        cp(GRN, f"CSV → {csv_path}")
    else:
        cf=cw=None

    prev=None; n=0
    try:
        with open_port(port,baud,timeout=0.3) as ser:
            while True:
                t0=time.time()
                resp=transact(ser, poll_cmd, wait=0.1)
                ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                if resp:
                    n+=1
                    fr=decode_frame(resp)
                    changed=(resp!=prev)
                    if fr and fr["value"] is not None:
                        marker=BLD+GRN if changed else GRY
                        cp(marker,
                           f"[{ts}] #{n:04d}  {fr.get('mode','?'):15s}"
                           f"  {fr['display']}")
                    elif fr:
                        cp(GRY if not changed else YLW,
                           f"[{ts}] #{n:04d}  {fr.get('mode','?'):15s}"
                           f"  payload={fr['payload'].hex().upper()}")
                    else:
                        cp(RED, f"[{ts}] Bad frame: {resp.hex().upper()}")

                    if raw_mode and changed:
                        print(hexdump(resp,"  "))

                    if cw and fr:
                        cw.writerow([datetime.now().isoformat(),
                                     fr.get("mode",""),fr.get("value",""),
                                     fr.get("unit",""),fr.get("flags",""),
                                     fr["payload"].hex().upper(),
                                     resp.hex().upper()])
                        cf.flush()
                    prev=resp
                else:
                    cp(GRY, f"[{ts}] no response")

                elapsed=time.time()-t0
                time.sleep(max(0,interval-elapsed))

    except KeyboardInterrupt:
        cp(YLW,"\nStopped.")
    finally:
        if cf: cf.close()

# ── interactive shell ─────────────────────────────────────────────────────────
def shell(port, baud):
    cp(CYN, f"\n[SHELL] {port} @ {baud}")
    print("Hex formats:  AA BB 0D  |  AABB0D  |  0xAA0xBB")
    print("Commands: poll | watch | quit | baud <n>")
    print("─"*60)
    stop=threading.Event()
    try:
        ser=open_port(port,baud,timeout=0.1)
    except serial.SerialException as e:
        cp(RED,f"Cannot open port: {e}"); return

    def reader():
        buf=b""; lt=time.time()
        while not stop.is_set():
            try:
                ch=ser.read(128)
                if ch: buf+=ch; lt=time.time()
                elif buf and time.time()-lt>0.15:
                    ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    fr=decode_frame(buf)
                    cp(GRN,f"\n[{ts}] RX {len(buf)}B  {fr.get('display','') if fr else buf.hex().upper()}")
                    print(hexdump(buf,"  "))
                    buf=b""
            except: pass
    threading.Thread(target=reader,daemon=True).start()

    cb=baud
    while True:
        try:
            line=input(f"\n[{cb}] TX> ").strip()
        except (EOFError,KeyboardInterrupt): break
        if not line: continue
        lo=line.lower()
        if lo=="quit": break
        elif lo=="poll":
            r=transact(ser,b"\x0D\x0A")
            fr=decode_frame(r) if r else None
            cp(GRN if r else YLW,
               f"POLL → {r.hex().upper() if r else 'no response'}")
            if fr: print(f"  {fr.get('display','')}")
            if r: print(hexdump(r,"  "))
        elif lo=="watch":
            cp(CYN,"Watching for 20s...")
            watch_mode(port,cb,20)
        elif lo.startswith("baud "):
            try:
                nb=int(lo.split()[1]); ser.close()
                ser=open_port(port,nb,timeout=0.1); cb=nb
                cp(GRN,f"→ {nb} baud")
            except Exception as e: cp(RED,str(e))
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

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap=argparse.ArgumentParser(description="ET829 v2 - Measurement Finder & Logger")
    ap.add_argument("--port",  default="COM8")
    ap.add_argument("--baud",  type=int, default=115200)
    ap.add_argument("--rate",  type=float, default=5.0)
    ap.add_argument("--csv",   metavar="FILE")
    ap.add_argument("--raw",   action="store_true")
    ap.add_argument("--find",  action="store_true", help="Find measurement command")
    ap.add_argument("--watch", action="store_true", help="Watch for frame changes")
    ap.add_argument("--shell", action="store_true")
    ap.add_argument("--log",   metavar="CMD_HEX", default="0D0A",
                    help="Hex poll command for logger (default: 0D0A)")
    ap.add_argument("--list",  action="store_true")
    ap.add_argument("--duration", type=int, default=60)
    args=ap.parse_args()

    if args.list:
        for p in serial.tools.list_ports.comports():
            print(f"  {p.device:<10} {p.description}")
        return

    if args.find:
        cmd=find_measurement_cmd(args.port, args.baud)
        if cmd:
            cp(GRN, f"\nRun logger with:  python et829_v2.py --log {cmd.hex().upper()}")
    elif args.watch:
        watch_mode(args.port, args.baud, args.duration)
    elif args.shell:
        shell(args.port, args.baud)
    else:
        try:
            poll_cmd=parse_hex(args.log)
        except Exception:
            poll_cmd=b"\x0D\x0A"
        run_logger(args.port, args.baud, poll_cmd, args.rate, args.csv, args.raw)

if __name__=="__main__":
    main()