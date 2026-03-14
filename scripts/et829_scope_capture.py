"""
ET829 / MDS8209 — Scope Capture  (v2)
=======================================
Commands (scope mode):
  00 01 or 00 02 → CH1 waveform  (A5 22, ~1360 samples)
  00 03          → CH2 waveform  (A5 23, ~1357 samples)
  00 04          → CH1+CH2       (A5 24, ~4250 bytes)
  00 09          → Device info   (A5 29)

Sub-header (6 bytes after A5 cmd plen):
  [0-1]  uint16 LE sequence counter
  [2]    CH2 V/div index
  [3]    CH1 V/div index  (0=10mV .. 9=10V, see VDIV_TABLE)
  [4]    timebase index
  [5]    channel flags (0x01=CH1 active, 0x03=both)

Sample encoding: uint8, 0x80(128)=0V, 0=negative peak, 255=positive peak

Usage:
  python et829_scope_capture.py          # single CH1 capture
  python et829_scope_capture.py --ch 2   # CH2
  python et829_scope_capture.py --loop   # continuous
  python et829_scope_capture.py --both   # CH1 + CH2
  python et829_scope_capture.py --info   # device info
  python et829_scope_capture.py --save   # save to CSV
  python et829_scope_capture.py --raw    # show sub-header bytes

Requires: pip install pyusb  +  libusb-1.0.dll in same folder
"""
import usb.core, usb.util
import time, sys, os, struct, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; CYN="\033[96m"
BLD="\033[1m"; GRY="\033[90m"; MAG="\033[95m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84

CMD_CH1  = bytes([0x00, 0x02])
CMD_CH2  = bytes([0x00, 0x03])
CMD_BOTH = bytes([0x00, 0x04])
CMD_INFO = bytes([0x00, 0x09])

# V/div index → volts per division (from Hantek.h SCOPE_VAL_SCALE_*)
VDIV_VOLTS = {0:0.01,1:0.02,2:0.05,3:0.1,4:0.2,5:0.5,6:1.0,7:2.0,8:5.0,9:10.0}
VDIV_TABLE = {0:"10mV",1:"20mV",2:"50mV",3:"100mV",4:"200mV",
              5:"500mV",6:"1V",7:"2V",8:"5V",9:"10V"}

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Device not found! Check USB + Zadig WinUSB driver.")
    for i in range(3):
        try:
            if dev.is_kernel_driver_active(i): dev.detach_kernel_driver(i)
        except: pass
    try: dev.set_configuration()
    except: pass
    return dev

def drain(dev):
    while True:
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def read_full(dev, cmd, max_bytes=8192):
    """Trigger new capture (00 00), arm (00 01), then read waveform."""
    try:
        drain(dev)
        # Step 1: trigger new acquisition
        dev.write(EP_OUT, bytes([0x00, 0x00]), timeout=500)
        time.sleep(0.05)
        try: dev.read(EP_BULK, 64, timeout=80)
        except: pass
        # Step 2: arm USB transfer
        dev.write(EP_OUT, bytes([0x00, 0x01]), timeout=500)
        time.sleep(0.1)
        try: dev.read(EP_BULK, 64, timeout=80)
        except: pass
        # Step 3: read waveform
        dev.write(EP_OUT, cmd, timeout=1000)
        time.sleep(0.15)
        buf = bytearray()
        while True:
            try:
                chunk = bytes(dev.read(EP_BULK, 512, timeout=250))
                buf.extend(chunk)
                if len(buf) >= max_bytes: break
            except: break
        return bytes(buf) if buf else None
    except:
        return None

def parse_frame(raw, expected_cmd):
    """Parse A5 frame → (sub_header_6B, samples_list) or (None, None)."""
    if not raw or len(raw) < 10:
        return None, None
    if raw[0] != 0xA5 or raw[1] != expected_cmd:
        return None, None
    plen = struct.unpack_from('<H', raw, 2)[0]
    payload = raw[4:4+plen]
    if len(payload) < 7:
        return None, None
    return payload[:6], list(payload[6:])

def decode_info(raw):
    if not raw or raw[0] != 0xA5 or raw[1] != 0x29: return None
    plen = struct.unpack_from('<H', raw, 2)[0]
    payload = raw[4:4+plen]
    parts = payload[1:].split(b'\x00')
    return [p.decode('ascii','replace').strip() for p in parts if p]

def ascii_waveform(samples, vdiv=1.0, width=100, height=20, label=""):
    """Render ASCII oscilloscope trace. vdiv = volts per division."""
    if not samples: return
    step = max(1, len(samples) // width)
    ds = [samples[i*step] for i in range(min(width, len(samples)//max(1,step)))]
    
    # Scale: 8 divisions total (4 above + 4 below 0V), 128=0V center
    ndivs = 8
    volts_fullscale = vdiv * ndivs / 2  # one-sided
    
    grid = [[' ']*len(ds) for _ in range(height)]
    zero_row = height // 2
    for x in range(len(ds)):
        grid[zero_row][x] = '─'
    for x, val in enumerate(ds):
        row = int((255 - val) / 255.0 * (height - 1))
        row = max(0, min(height-1, row))
        grid[row][x] = '█'

    mn, mx = min(samples), max(samples)
    print()
    cp(BLD+CYN, f"  {label}  ({len(samples)} samples, {VDIV_TABLE.get(list(VDIV_VOLTS.keys())[list(VDIV_VOLTS.values()).index(vdiv)] if vdiv in VDIV_VOLTS.values() else -1, f'{vdiv}V')}/div)")
    
    for r in range(height):
        v = volts_fullscale * (1 - 2*r/(height-1))
        show_label = (r % (height//4) == 0)
        prefix = f"{v:+6.3f}V │" if show_label else "         │"
        row_str = ''.join(grid[r])
        col = GRY if r == zero_row else (GRN if r < zero_row else YLW)
        cp(col, prefix + row_str)
    cp(GRY, "         └" + "─"*len(ds))
    
    # Stats
    def raw_to_v(s): return (s - 128) / 128.0 * volts_fullscale
    vsamps = [raw_to_v(s) for s in samples]
    vpp  = max(vsamps) - min(vsamps)
    vrms = (sum(v**2 for v in vsamps)/len(vsamps))**0.5
    vdc  = sum(vsamps)/len(vsamps)
    cp(CYN, f"  Vpp={vpp:.3f}V  Vrms={vrms:.3f}V  DC={vdc:+.3f}V  "
            f"raw min={mn} max={mx}")
    print()

def main():
    ap = argparse.ArgumentParser(description="ET829 Scope Capture")
    ap.add_argument("--ch",    type=int, default=1, choices=[1,2])
    ap.add_argument("--both",  action="store_true")
    ap.add_argument("--info",  action="store_true")
    ap.add_argument("--save",  action="store_true")
    ap.add_argument("--raw",   action="store_true")
    ap.add_argument("--loop",  action="store_true")
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")
    cp(GRN, "  (make sure device is in SCOPE mode)\n")

    if args.info:
        raw = read_full(dev, CMD_INFO)
        info = decode_info(raw)
        if info:
            cp(BLD+GRN, "Device Info:")
            for i, part in enumerate(info):
                label = ["Board ID","Firmware date","FW version","HW rev"][i] if i < 4 else f"field{i}"
                cp(GRN, f"  {label}: {part}")
        else:
            cp(RED, "No response — is device in scope mode?")
        return

    count = 0
    saved = False
    try:
        while True:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            if args.both:
                raw = read_full(dev, CMD_BOTH, max_bytes=6000)
                hdr, samples = parse_frame(raw, 0x24)
                if samples:
                    mid = len(samples) // 2
                    vdiv1 = VDIV_VOLTS.get(hdr[3], 1.0)
                    vdiv2 = VDIV_VOLTS.get(hdr[2], 1.0)
                    if args.raw:
                        cp(GRY, f"Sub-header: {' '.join(f'{b:02X}' for b in hdr)}  "
                                f"CH1={VDIV_TABLE.get(hdr[3],'?')}/div  "
                                f"CH2={VDIV_TABLE.get(hdr[2],'?')}/div")
                    ascii_waveform(samples[:mid], vdiv1, label=f"[{ts}] CH1")
                    ascii_waveform(samples[mid:], vdiv2, label=f"[{ts}] CH2")
                else:
                    cp(RED, f"[{ts}] No data")
            else:
                cmd    = CMD_CH1 if args.ch == 1 else CMD_CH2
                exp    = 0x22   if args.ch == 1 else 0x23
                raw    = read_full(dev, cmd, max_bytes=4096)
                hdr, samples = parse_frame(raw, exp)

                if samples:
                    # CH1 V/div is in sub-header byte[3], CH2 in byte[2]
                    vdiv_idx = hdr[3] if args.ch == 1 else hdr[2]
                    vdiv = VDIV_VOLTS.get(vdiv_idx, 1.0)
                    if args.raw:
                        cp(GRY, f"Sub-header: {' '.join(f'{b:02X}' for b in hdr)}  "
                                f"CH{args.ch} V/div idx={vdiv_idx} → {VDIV_TABLE.get(vdiv_idx,'?')}")
                    ascii_waveform(samples, vdiv, label=f"[{ts}] CH{args.ch}")

                    if args.save and not saved:
                        fname = f"et829_ch{args.ch}_{datetime.now().strftime('%H%M%S')}.csv"
                        with open(fname,'w') as f:
                            f.write("index,raw,voltage\n")
                            for i,s in enumerate(samples):
                                v = (s-128)/128.0 * vdiv * 4
                                f.write(f"{i},{s},{v:.5f}\n")
                        cp(GRN, f"Saved: {fname}")
                        saved = True
                else:
                    cp(RED, f"[{ts}] No data — scope mode? Try replugging USB.")
                    if raw: cp(GRY, f"  raw[0:8]: {raw[:8].hex().upper()}")

            count += 1
            if not args.loop: break
            time.sleep(0.3)

    except KeyboardInterrupt:
        print()
    cp(CYN, f"\nDone. {count} captures.")

if __name__ == "__main__":
    main()