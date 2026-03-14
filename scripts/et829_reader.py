"""
ET829 / MDS8209 — Live DMM Reader  (fully decoded)
===================================================
Protocol reverse engineered frame format (TX=00 05):

  RX (15 bytes):
    [0]     A5          sync
    [1]     25          command ID
    [2-3]   09 00       payload length = 9
    [4]     B0          0x2D (frame subtype)
    [5-8]   B1-B4       signed int32 LE  (measurement count)
    [9]     B5          RANGE code (for resistance: multiplier = 10^(B5-1))
    [10]    B6          MODE code (see table)
    [11]    B7          decimal places on display
    [12]    00          padding
    [13]    OL          0x01 = overload/open circuit, 0x00 = normal
    [14]    CHK         checksum = (0x100 - sum(bytes[0..13])) % 0x100

  Mode codes (B6):
     5 = DC Voltage      value = raw_int32 / 1000  [V]
     6 = AC Voltage      value = raw_int32 / 1000  [V]
     7 = Resistance      value = raw_int32 * 10^(B5-1)  [Ohm]
     9 = Continuity      value = raw_int32 * 10^(B5-1)  [Ohm]
    10 = Diode           value = raw_int32 / 1000  [V]
    11 = Capacitance     value = raw_int32 / 1000  [F]
    18 = Frequency       value = raw_int32 / 1000  [Hz]
    19 = Duty Cycle      value = raw_uint32 / 100  [%]

  Resistance ranges (B5):
     0 = 600 Ohm    (x0.1   per count)
     1 = 6 kOhm     (x1     per count)
     2 = 60 kOhm    (x10    per count)
     3 = 600 kOhm   (x100   per count)  ← verified
     4 = 6 MOhm     (x1000  per count)
     5 = 60 MOhm    (x10000 per count)  ← verified

  Overload: byte[13] == 0x01

Usage:
  python et829_reader.py           # live display
  python et829_reader.py --csv     # CSV logging
  python et829_reader.py --once    # single reading
  python et829_reader.py --raw     # show raw hex each line

Requires: pip install pyusb  +  libusb-1.0.dll in same folder
"""

import usb.core, usb.util
import time, sys, os, struct, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"
CYN="\033[96m"; BLD="\033[1m"; GRY="\033[90m"; MAG="\033[95m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84
CMD      = bytes([0x00, 0x05])

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError(
            "Device not found!\n"
            "  -> Check USB cable\n"
            "  -> Run Zadig: replace driver on 'CDC Config (Interface 0)' with WinUSB")
    for intf in range(3):
        try:
            if dev.is_kernel_driver_active(intf): dev.detach_kernel_driver(intf)
        except: pass
    try: dev.set_configuration()
    except: pass
    return dev

def query(dev):
    try:
        try: dev.read(EP_BULK, 64, timeout=30)
        except: pass
        dev.write(EP_OUT, CMD, timeout=500)
        time.sleep(0.12)
        raw = bytes(dev.read(EP_BULK, 64, timeout=500))
    except usb.core.USBTimeoutError:
        return None
    except:
        return None

    if len(raw) < 15 or raw[0] != 0xA5 or raw[1] != 0x25:
        return None

    plen      = struct.unpack_from('<H', raw, 2)[0]   # = 9
    payload   = raw[4:4+plen]                          # bytes [4..12]
    ol_flag   = raw[13]                                # 0x01 = overload
    checksum  = raw[14]
    expected  = (0x100 - sum(raw[:14]) % 0x100) % 0x100

    mode_code  = payload[6] if len(payload) > 6 else 0
    dec_places = payload[7] if len(payload) > 7 else 3
    b5         = payload[5] if len(payload) > 5 else 0
    raw_int32  = struct.unpack_from('<i', payload, 1)[0]
    raw_uint32 = struct.unpack_from('<I', payload, 1)[0]

    overload = (ol_flag == 0x01)

    # Calculate value based on mode
    if mode_code in (7, 9):  # Resistance / Continuity
        # formula: raw_int32 * 10^(b5-1)
        value = raw_int32 * (10 ** (b5 - 1))
        unit  = "Ohm"
    elif mode_code == 19:    # Duty Cycle: unsigned, /100
        value = raw_uint32 / 100.0
        unit  = "%"
    elif mode_code == 5:     value = raw_int32 / 1000.0; unit = "V"   # DC-V
    elif mode_code == 6:     value = raw_int32 / 1000.0; unit = "V"   # AC-V
    elif mode_code == 10:    value = raw_int32 / 1000.0; unit = "V"   # Diode
    elif mode_code == 11:    value = raw_int32 * (10 ** (b5 - 1)) * 1e-9; unit = "F"   # Cap: base unit nF
    elif mode_code == 18:    value = raw_int32 / 1000.0; unit = "Hz"  # Freq
    else:                    value = raw_int32 / 1000.0; unit = "?"

    mode_names = {
        5:"DC-V", 6:"AC-V", 7:"Resistance", 9:"Continuity",
        10:"Diode", 11:"Capacitance", 18:"Frequency", 19:"Duty"
    }

    return {
        'value':      value,
        'raw_int32':  raw_int32,
        'raw_uint32': raw_uint32,
        'mode_code':  mode_code,
        'mode_name':  mode_names.get(mode_code, f"mode{mode_code}"),
        'unit':       unit,
        'dec_places': dec_places,
        'b5':         b5,
        'overload':   overload,
        'ol_flag':    ol_flag,
        'chk_ok':     checksum == expected,
        'raw_hex':    raw.hex().upper(),
    }

def format_reading(r):
    if r['overload']:
        return f"OL   [{r['unit']}]"

    val  = r['value']
    dec  = r['dec_places']
    mode = r['mode_code']

    # Resistance/Continuity: auto-scale
    if mode in (7, 9):
        av = abs(val)
        if av >= 1_000_000:
            return f"{val/1_000_000:.{dec}f} MOhm"
        elif av >= 1_000:
            return f"{val/1_000:.{dec}f} kOhm"
        return f"{val:.{dec}f} Ohm"

    # Capacitance: auto-scale
    if mode == 11:
        av = abs(val)
        if   av >= 1:     return f"{val:.{dec}f} F"
        elif av >= 1e-3:  return f"{val*1e3:.{dec}f} mF"
        elif av >= 1e-6:  return f"{val*1e6:.{dec}f} uF"
        elif av >= 1e-9:  return f"{val*1e9:.{dec}f} nF"
        else:             return f"{val*1e12:.{dec}f} pF"

    # Voltage: mV if < 1V
    if mode in (5, 6, 10) and 0 < abs(val) < 1.0:
        return f"{val*1000:.{max(1,dec)}f} mV"

    return f"{val:.{dec}f} {r['unit']}"

def main():
    ap = argparse.ArgumentParser(description="ET829 / MDS8209 Live DMM Reader")
    ap.add_argument("--csv",      action="store_true", help="CSV output for logging")
    ap.add_argument("--once",     action="store_true", help="Single reading then exit")
    ap.add_argument("--interval", type=float, default=0.5, help="Poll interval (s)")
    ap.add_argument("--raw",      action="store_true", help="Show raw hex each line")
    args = ap.parse_args()

    dev = open_device()

    if args.csv:
        print("timestamp,mode,value_raw,value_formatted,unit,overload,b5,b6,b7,ol_flag,chk_ok,raw_hex")
    else:
        cp(BLD+GRN, "=" * 52)
        cp(BLD+GRN, "  ET829 / MDS8209  Live DMM Reader")
        cp(BLD+GRN, "=" * 52)
        cp(GRY, f"  polling every {args.interval}s  |  Ctrl+C to stop\n")

    count = 0
    prev_mode = None
    no_resp = 0

    try:
        while True:
            r = query(dev)
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            if r:
                no_resp = 0
                reading = format_reading(r)
                new_mode = (r['mode_code'] != prev_mode)
                prev_mode = r['mode_code']

                if args.csv:
                    print(f"{ts},{r['mode_name']},{r['value']:.6f},{reading},"
                          f"{r['unit']},{r['overload']},{r['b5']},"
                          f"{r['mode_code']},{r['dec_places']},"
                          f"{r['ol_flag']},{r['chk_ok']},{r['raw_hex']}")
                elif args.once:
                    cp(BLD+GRN, f"\n  {reading}")
                    cp(GRY, f"  mode={r['mode_name']} (b6={r['mode_code']})"
                             f"  b5={r['b5']}  dec={r['dec_places']}"
                             f"  OL_flag={r['ol_flag']}"
                             f"  chk={'OK' if r['chk_ok'] else 'BAD'}")
                    if args.raw: cp(GRY, f"  {r['raw_hex']}")
                    break
                else:
                    col = MAG if new_mode else (YLW if r['overload'] else GRN)
                    tag = f"  [{r['mode_name']}]" if new_mode else ""
                    chk = f"  {RED}!CHK{RST}" if not r['chk_ok'] else ""
                    cp(col, f"[{ts}]  {reading:<22}{tag}{chk}")
                    if args.raw:
                        cp(GRY, f"           b5={r['b5']} b6={r['mode_code']} b7={r['dec_places']} ol={r['ol_flag']}  {r['raw_hex']}")
                count += 1
            else:
                no_resp += 1
                if not args.csv:
                    sys.stdout.write(f"\r[{ts}]  (no response x{no_resp} — scope mode?)    ")
                    sys.stdout.flush()

            if args.once:
                break
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print()
        if not args.csv:
            cp(CYN, f"\nDone. {count} readings captured.")

if __name__ == "__main__":
    main()