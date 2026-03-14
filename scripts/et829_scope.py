"""
ET829 / MDS8209 — Live Scope Capture  (FINAL)
==============================================
CORRECT PROTOCOL (discovered via 0D-family scan):

  0D 01  → A5 21  arm scope mode
  0D 02  → A5 22  LIVE CH1 waveform   ← use this
  0D 03  → A5 23  LIVE CH2 waveform
  0D 04  → A5 24  LIVE CH1+CH2
  0D 09  → A5 29  device info

  00 02/03/04  = read CACHED ring buffer (frozen, not live)
  A5 22 XX     = seek ring buffer to historical page XX

Sub-header (6 bytes):
  [0-1]  uint16 LE  seq counter
  [2]    CH2 V/div index
  [3]    CH1 V/div index  (0=10mV .. 9=10V)
  [4]    timebase index
  [5]    channel flags (0x01=CH1, 0x02=CH2, 0x03=both)

Usage:
  python et829_scope.py            single CH1 capture
  python et829_scope.py --loop     continuous live
  python et829_scope.py --ch 2     CH2
  python et829_scope.py --both     CH1 + CH2
  python et829_scope.py --info     device info
  python et829_scope.py --save     save to CSV
"""
import usb.core, usb.util, time, struct, os, argparse
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; CYN="\033[96m"
BLD="\033[1m"; GRY="\033[90m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT, EP_BULK = 0x05, 0x84

CMD_CH1  = bytes([0x0D, 0x02])
CMD_CH2  = bytes([0x0D, 0x03])
CMD_BOTH = bytes([0x0D, 0x04])
CMD_INFO = bytes([0x0D, 0x09])
CMD_ARM  = bytes([0x0D, 0x01])

VDIV_VOLTS = {0:0.01,1:0.02,2:0.05,3:0.1,4:0.2,5:0.5,6:1.0,7:2.0,8:5.0,9:10.0}
VDIV_TABLE = {0:"10mV",1:"20mV",2:"50mV",3:"100mV",4:"200mV",
              5:"500mV",6:"1V",7:"2V",8:"5V",9:"10V"}

def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None: raise RuntimeError("Device not found!")
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

def read_live(dev, cmd, max_bytes=8192, timeout_s=3.0):
    """Arm scope, wait for trigger, read fresh waveform.
    Sends 0D 01 (arm) then polls until a NEW seq counter appears.
    timeout_s: max seconds to wait for trigger event.
    """
    try:
        # Get current seq so we know when a new frame arrives
        drain(dev)
        dev.write(EP_OUT, cmd, timeout=1000)
        time.sleep(0.15)
        first_buf = bytearray()
        while True:
            try: first_buf.extend(dev.read(EP_BULK, 512, timeout=200))
            except: break
        current_seq = None
        if len(first_buf) >= 6 and first_buf[0] == 0xA5:
            import struct as _s
            plen = _s.unpack_from('<H', first_buf, 2)[0]
            if plen > 2:
                current_seq = _s.unpack_from('<H', first_buf, 4)[0]

        # Arm for new capture
        drain(dev)
        dev.write(EP_OUT, CMD_ARM, timeout=500)
        time.sleep(0.1)
        try: dev.read(EP_BULK, 64, timeout=150)
        except: pass

        # Poll until seq changes (new trigger fired) or timeout
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            drain(dev)
            dev.write(EP_OUT, cmd, timeout=500)
            time.sleep(0.12)
            buf = bytearray()
            while True:
                try: buf.extend(dev.read(EP_BULK, 512, timeout=200))
                except: break
            if len(buf) >= 6 and buf[0] == 0xA5:
                import struct as _s
                plen = _s.unpack_from('<H', buf, 2)[0]
                if plen > 2:
                    new_seq = _s.unpack_from('<H', buf, 4)[0]
                    if current_seq is None or new_seq != current_seq:
                        return bytes(buf)  # fresh frame!
            time.sleep(0.05)

        # Timeout — return whatever we have
        return bytes(first_buf) if first_buf else None
    except: return None

def parse_frame(raw, exp_cmd):
    if not raw or len(raw) < 10: return None, None
    if raw[0] != 0xA5 or raw[1] != exp_cmd: return None, None
    plen = struct.unpack_from('<H', raw, 2)[0]
    p = raw[4:4+plen]
    if len(p) < 7: return None, None
    return p[:6], list(p[6:])

def decode_info(raw):
    if not raw or raw[0] != 0xA5 or raw[1] != 0x29: return None
    plen = struct.unpack_from('<H', raw, 2)[0]
    p = raw[4:4+plen]
    parts = p[1:].split(b'\x00')
    return [x.decode('ascii','replace').strip() for x in parts if x]

def ascii_waveform(samples, vdiv=1.0, width=100, height=20, label=""):
    if not samples: return
    step = max(1, len(samples) // width)
    ds = [samples[i*step] for i in range(min(width, len(samples)//max(1,step)))]
    fullscale = vdiv * 4
    grid = [[' ']*len(ds) for _ in range(height)]
    zero_row = height // 2
    for x in range(len(ds)): grid[zero_row][x] = '─'
    for x, val in enumerate(ds):
        row = int((255-val)/255.0*(height-1))
        grid[max(0,min(height-1,row))][x] = '█'
    vdiv_str = next((VDIV_TABLE[k] for k,v in VDIV_VOLTS.items() if abs(v-vdiv)<1e-9), f"{vdiv}V")
    print()
    cp(BLD+CYN, f"  {label}  ({len(samples)} samples, {vdiv_str}/div)")
    for r in range(height):
        v = fullscale*(1-2*r/(height-1))
        prefix = f"{v:+7.3f}V │" if r%(height//4)==0 else "          │"
        col = GRY if r==zero_row else (GRN if r<zero_row else YLW)
        cp(col, prefix+''.join(grid[r]))
    cp(GRY, "          └"+"─"*len(ds))
    vs = [(s-128)/128.0*fullscale for s in samples]
    vpp = max(vs)-min(vs)
    vrms = (sum(v**2 for v in vs)/len(vs))**0.5
    vdc  = sum(vs)/len(vs)
    cp(CYN, f"  Vpp={vpp:.3f}V  Vrms={vrms:.3f}V  DC={vdc:+.3f}V  "
            f"raw min={min(samples)} max={max(samples)}")
    print()

def main():
    ap = argparse.ArgumentParser(description="ET829 Live Scope")
    ap.add_argument("--ch",   type=int, default=1, choices=[1,2])
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--raw",  action="store_true")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    dev = open_device()
    cp(GRN, f"Connected: {dev.manufacturer} — {dev.product}")

    # Arm scope
    drain(dev)
    dev.write(EP_OUT, CMD_ARM, timeout=1000)
    time.sleep(0.2)
    try: dev.read(EP_BULK, 64, timeout=200)
    except: pass

    if args.info:
        raw = read_live(dev, CMD_INFO)
        info = decode_info(raw)
        if info:
            cp(BLD+GRN, "Device Info:")
            for i,f in enumerate(info):
                cp(GRN, f"  {['Board','Firmware','Version','HW rev'][i] if i<4 else f'field{i}'}: {f}")
        else:
            cp(RED, "No info response.")
        return

    count = 0
    saved = False
    try:
        while True:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            if args.both:
                raw = read_live(dev, CMD_BOTH, max_bytes=6000)
                hdr, samp = parse_frame(raw, 0x24)
                if samp:
                    mid = len(samp)//2
                    v1 = VDIV_VOLTS.get(hdr[3], 1.0)
                    v2 = VDIV_VOLTS.get(hdr[2], 1.0)
                    if args.raw:
                        seq = struct.unpack_from('<H', bytes(hdr), 0)[0]
                        cp(GRY, f"seq={seq} hdr={bytes(hdr).hex().upper()}")
                    ascii_waveform(samp[:mid], v1, label=f"[{ts}] CH1")
                    ascii_waveform(samp[mid:], v2, label=f"[{ts}] CH2")
                    count += 1
                else:
                    cp(RED, f"[{ts}] No data")
            else:
                cmd = CMD_CH1 if args.ch==1 else CMD_CH2
                exp = 0x22   if args.ch==1 else 0x23
                raw = read_live(dev, cmd)
                hdr, samp = parse_frame(raw, exp)
                if samp:
                    vdiv_idx = hdr[3] if args.ch==1 else hdr[2]
                    vdiv = VDIV_VOLTS.get(vdiv_idx, 1.0)
                    if args.raw:
                        seq = struct.unpack_from('<H', bytes(hdr), 0)[0]
                        cp(GRY, f"seq={seq} hdr={bytes(hdr).hex().upper()} "
                                f"V/div={VDIV_TABLE.get(vdiv_idx,'?')}")
                    ascii_waveform(samp, vdiv, label=f"[{ts}] CH{args.ch}")
                    if args.save and not saved:
                        fname = f"et829_ch{args.ch}_{datetime.now().strftime('%H%M%S')}.csv"
                        fs = vdiv*4
                        with open(fname,'w') as f:
                            f.write("index,raw,voltage\n")
                            for i,s in enumerate(samp):
                                f.write(f"{i},{s},{(s-128)/128.0*fs:.5f}\n")
                        cp(GRN, f"Saved: {fname}")
                        saved = True
                    count += 1
                else:
                    cp(RED, f"[{ts}] No data — scope mode?")
                    if raw: cp(GRY, f"  raw[0:8]: {raw[:8].hex().upper()}")

            if not args.loop: break
            time.sleep(0.3)

    except KeyboardInterrupt: print()
    cp(CYN, f"\nDone. {count} captures.")

if __name__ == "__main__":
    main()
