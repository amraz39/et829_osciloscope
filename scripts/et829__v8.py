"""
ET829 / MDS8209 — Frame Correlation Test  v8
=============================================
HYPOTHESIS: The 7-byte frame A5 2A 01 00 30 00 00 may already contain
the measurement value — we just haven't been reading it correctly.

Frame:  A5  2A  01  00  30  00  00
Byte:    0   1   2   3   4   5   6

If probes are floating → display shows 0.000 → bytes 5,6 = 00 00
If probes touch 1.5V  → display shows 1.500 → bytes 5,6 might change
If probes touch 9.0V  → bytes 5,6 might show 09 00 or 03 84 (900)

We need to correlate what's on the display to what changes in the frame.

Usage:
  python et829_v8.py          # continuous poll + show ALL bytes prominently
  python et829_v8.py --slow   # poll every 2s with long read window
  python et829_v8.py --touch  # guided test: touch probes, watch bytes change

Requirements: pip install pyserial
"""

import argparse, serial, time, sys, os, struct
from datetime import datetime

if sys.platform == "win32":
    os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; GRY="\033[90m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

def open_port(port, baud, timeout=0.1):
    ser = serial.Serial()
    ser.port=port; ser.baudrate=baud; ser.bytesize=8
    ser.parity='N'; ser.stopbits=1; ser.timeout=timeout
    ser.write_timeout=1; ser.xonxoff=False; ser.rtscts=False; ser.dsrdtr=False
    ser.open()
    ser.dtr=True; ser.rts=True
    return ser

def read_all(ser, wait=0.5):
    """Send CRLF and collect ALL bytes for `wait` seconds."""
    ser.reset_input_buffer()
    ser.write(b"\x0D\x0A")
    buf = b""
    dl = time.time() + wait
    while time.time() < dl:
        c = ser.read(256)
        if c: buf += c
    return buf

def try_interpret(raw):
    """Try every plausible interpretation of the 7 bytes."""
    if len(raw) < 7: return
    b = raw
    
    print(f"\n  Frame: {' '.join(f'{x:02X}' for x in b)}")
    print(f"  Dec:   {' '.join(f'{x:3d}' for x in b)}")
    
    # Try treating bytes 4,5,6 as measurement
    if len(b) >= 7:
        # Little-endian int16 at pos 4
        v_le16_4 = struct.unpack_from('<h', b, 4)[0]
        v_be16_4 = struct.unpack_from('>h', b, 4)[0]
        # Little-endian int16 at pos 5
        v_le16_5 = struct.unpack_from('<h', b, 5)[0] if len(b)>=7 else 0
        v_be16_5 = struct.unpack_from('>h', b, 5)[0] if len(b)>=7 else 0
        
        print(f"\n  Interpretations of bytes 4-6 ({b[4]:02X} {b[5]:02X} {b[6]:02X}):")
        print(f"    int16 LE @4: {v_le16_4:6d}  → /10={v_le16_4/10:8.1f}  /100={v_le16_4/100:8.2f}  /1000={v_le16_4/1000:8.3f}")
        print(f"    int16 BE @4: {v_be16_4:6d}  → /10={v_be16_4/10:8.1f}  /100={v_be16_4/100:8.2f}  /1000={v_be16_4/1000:8.3f}")
        print(f"    int16 LE @5: {v_le16_5:6d}  → /10={v_le16_5/10:8.1f}  /100={v_le16_5/100:8.2f}  /1000={v_le16_5/1000:8.3f}")
        print(f"    int16 BE @5: {v_be16_5:6d}  → /10={v_be16_5/10:8.1f}  /100={v_be16_5/100:8.2f}  /1000={v_be16_5/1000:8.3f}")
        print(f"    byte[4]={b[4]} byte[5]={b[5]} byte[6]={b[6]}")
        
        # BCD interpretation
        def bcd(byte): return (byte>>4)*10 + (byte&0xF)
        print(f"    BCD: {bcd(b[4])}.{bcd(b[5])}{bcd(b[6])}")

# ─────────────────────────────────────────────────────────────────────────────
# GUIDED TOUCH TEST
# Shows all bytes with large display, guides user to touch probes
# ─────────────────────────────────────────────────────────────────────────────
def guided_touch_test(port, baud):
    cp(CYN, "\n[GUIDED TOUCH TEST]")
    cp(YLW, "This test guides you step by step.")
    cp(YLW, "Meter in DC Voltage mode, probes NOT touching anything yet.")
    cp(YLW, "Press Enter to take baseline reading (probes in air)...")
    input()

    try:
        ser = open_port(port, baud, timeout=0.2)
        
        def poll():
            ser.reset_input_buffer()
            ser.write(b"\x0D\x0A")
            time.sleep(0.4)
            buf=b""
            dl=time.time()+0.5
            while time.time()<dl:
                c=ser.read(256); 
                if c: buf+=c; dl=time.time()+0.15
                elif buf: break
            return buf

        # Baseline - probes in air
        cp(CYN, "\n=== READING 1: Probes in air (floating) ===")
        r1 = poll()
        print(f"Raw: {r1.hex().upper()}")
        try_interpret(r1)
        
        cp(YLW, "\nNow touch BOTH probes to a 1.5V AA battery (red=+, black=-).")
        cp(YLW, "Press Enter when probes are touching the battery...")
        input()
        
        # Read 3 times quickly
        cp(CYN, "\n=== READING 2: Probes on 1.5V battery ===")
        for i in range(3):
            r = poll()
            print(f"Read {i+1}: {r.hex().upper()}")
            time.sleep(0.2)
        try_interpret(r)
        
        cp(YLW, "\nNow try a 9V battery if you have one, or short the probes together.")
        cp(YLW, "Press Enter when ready (or Enter to skip)...")
        input()
        
        cp(CYN, "\n=== READING 3: New measurement ===")
        for i in range(3):
            r = poll()
            print(f"Read {i+1}: {r.hex().upper()}")
            time.sleep(0.2)
        try_interpret(r)
        
        cp(YLW, "\nNow switch the meter to RESISTANCE mode.")
        cp(YLW, "Press Enter when in resistance mode...")
        input()
        
        cp(CYN, "\n=== READING 4: Resistance mode (probes in air) ===")
        r4 = poll()
        print(f"Raw: {r4.hex().upper()}")
        try_interpret(r4)
        
        ser.close()
        
        cp(CYN, "\n=== COMPARISON ===")
        print(f"DC Voltage (floating): {r1.hex().upper()}")
        print(f"Resistance (floating): {r4.hex().upper()}")
        print()
        
        if r1 == r4:
            cp(YLW, "Frames IDENTICAL across modes — measurement not in CRLF response.")
            cp(YLW, "The value must come from a different command or endpoint.")
        else:
            changed = [i for i in range(min(len(r1),len(r4))) if r1[i]!=r4[i]]
            cp(GRN, f"Bytes that changed: positions {changed}")
            cp(GRN, "THESE bytes contain the measurement data!")

    except Exception as e:
        cp(RED, f"Error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# SLOW POLL — long read window to catch any delayed second frame
# ─────────────────────────────────────────────────────────────────────────────
def slow_poll(port, baud):
    cp(CYN, f"\n[SLOW POLL] Reading with 3s window after each CRLF")
    cp(YLW, "Meter ON, measuring a battery. Press Enter...")
    input()

    try:
        ser = open_port(port, baud, timeout=0.05)
        
        for trial in range(5):
            cp(YLW, f"\nTrial {trial+1}/5 — sending CRLF, listening 3s...")
            ser.reset_input_buffer()
            ser.write(b"\x0D\x0A")
            
            buf=b""; chunks=[]
            dl=time.time()+3.0
            last_t=time.time()
            
            while time.time()<dl:
                c=ser.read(64)
                if c:
                    buf+=c
                    ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    cp(GRN, f"  [{ts}] +{len(c)}B chunk: {c.hex().upper()}")
                    chunks.append((ts,c))
                    last_t=time.time()
                rem=int(dl-time.time())
                sys.stdout.write(f"\r  listening... {rem}s  total={len(buf)}B  ")
                sys.stdout.flush()
            
            print()
            if buf:
                cp(GRN, f"  Total: {buf.hex().upper()}  ({len(buf)}B in {len(chunks)} chunks)")
                try_interpret(buf[:7])
            else:
                cp(GRY, "  No data received")
            
            time.sleep(1)
        
        ser.close()
        
    except Exception as e:
        cp(RED, f"Error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# CONTINUOUS POLL — show all bytes prominently
# ─────────────────────────────────────────────────────────────────────────────
def continuous_poll(port, baud):
    cp(CYN, f"\n[CONTINUOUS POLL] Polling every 0.5s, showing all bytes")
    cp(YLW, "Touch probes to different things and watch bytes 4,5,6 closely!")
    cp(CYN, "\nFormat: [B0] [B1] [B2] [B3] [B4] [B5] [B6]")
    cp(CYN,  "         sync  cmd  ??   ??  <--- watch these 3 bytes --->\n")
    cp(YLW, "Press Ctrl+C to stop.\n")
    
    prev = None
    try:
        ser = open_port(port, baud, timeout=0.2)
        while True:
            ser.reset_input_buffer()
            ser.write(b"\x0D\x0A")
            time.sleep(0.35)
            buf=b""
            dl=time.time()+0.4
            while time.time()<dl:
                c=ser.read(256)
                if c: buf+=c; dl=time.time()+0.15
                elif buf: break
            
            ts=datetime.now().strftime("%H:%M:%S.%f")[:-3]
            if buf:
                changed = (buf != prev)
                b = buf
                
                # Big clear display
                b_strs = [f"{x:02X}" for x in b]
                
                if changed:
                    cp(BLD+GRN,
                       f"[{ts}]  "
                       f"{b_strs[0] if len(b)>0 else '--'}  "
                       f"{b_strs[1] if len(b)>1 else '--'}  "
                       f"{b_strs[2] if len(b)>2 else '--'}  "
                       f"{b_strs[3] if len(b)>3 else '--'}  "
                       f"| {b_strs[4] if len(b)>4 else '--'}  "
                       f"{b_strs[5] if len(b)>5 else '--'}  "
                       f"{b_strs[6] if len(b)>6 else '--'} |"
                       f"  ← CHANGED!")
                    try_interpret(b)
                else:
                    # Single line for repeated frames
                    sys.stdout.write(
                        f"\r[{ts}]  "
                        f"{' '.join(b_strs[:4])}  "
                        f"| {' '.join(b_strs[4:7])} |  "
                        f"({len(b)}B)"
                    )
                    sys.stdout.flush()
                prev = buf
            else:
                sys.stdout.write(f"\r[{ts}]  (no response)")
                sys.stdout.flush()
            
            time.sleep(0.15)
            
    except KeyboardInterrupt:
        print()
    except Exception as e:
        cp(RED, f"Error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap=argparse.ArgumentParser(description="ET829 v8 - Frame Correlation Test")
    ap.add_argument("--port",  default="COM8")
    ap.add_argument("--baud",  type=int, default=115200)
    ap.add_argument("--slow",  action="store_true", help="3s read window per poll")
    ap.add_argument("--touch", action="store_true", help="Guided touch test")
    args=ap.parse_args()

    if args.slow:
        slow_poll(args.port, args.baud)
    elif args.touch:
        guided_touch_test(args.port, args.baud)
    else:
        continuous_poll(args.port, args.baud)

if __name__=="__main__":
    main()