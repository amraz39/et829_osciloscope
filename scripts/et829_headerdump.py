"""ET829 — RAW HEADER DUMP for all saved pages."""
import usb.core, usb.util, time, sys, struct

VID, PID = 0x2E88, 0x4603
EP_OUT   = 0x05
EP_BULK  = 0x84

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None: sys.exit("Not found")
    try: dev.set_configuration()
    except: pass
    try: usb.util.claim_interface(dev, 0)
    except: pass
    return dev

def drain(dev):
    while True:
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def xfer(dev, tx, max_bytes=8192):
    drain(dev)
    dev.write(EP_OUT, tx, timeout=500)
    time.sleep(0.15)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=300))
        except: break
    return bytes(buf) if buf else None

VDIV = ['10mV','20mV','50mV','100mV','200mV','500mV','1V','2V','5V','10V']

dev = find_dev()
print(f"Connected: {dev.manufacturer} — {dev.product}\n")
dev.write(EP_OUT, bytes([0x0D, 0x00]), timeout=1000)
time.sleep(0.3); drain(dev)

print(f"{'Page':>4}  {'seq':>5}  "
      f"{'d[4]':>5} {'d[5]':>5} {'d[6]':>5} {'d[7]':>5} {'d[8]':>5} {'d[9]':>5}  "
      f"{'seq_le':>6}  {'ch2_vdiv':>10}  {'ch1_vdiv':>10}  {'tb':>5}  {'flags':>5}  "
      f"{'n_samples':>9}  raw_min-max")
print("-"*110)

for p in range(32):
    xfer(dev, bytes([0xA5, 0x22, p]), max_bytes=64)
    time.sleep(0.05)
    data = xfer(dev, bytes([0x00, 0x02]))
    if not data or len(data) < 10 or data[0] != 0xA5:
        if p > 0:
            break
        continue
    
    plen = struct.unpack_from('<H', data, 2)[0]
    if plen < 6: continue
    
    d4, d5, d6, d7, d8, d9 = data[4], data[5], data[6], data[7], data[8], data[9]
    seq = struct.unpack_from('<H', data, 4)[0]
    samples = list(data[10:])
    
    # trim zeros
    cutoff = len(samples)
    for i in range(len(samples)-15):
        if all(samples[j]==0 for j in range(i, i+15)):
            cutoff=i; break
    valid = samples[:cutoff]
    
    ch2_lbl = VDIV[d6] if d6 < len(VDIV) else f'idx{d6}'
    ch1_lbl = VDIV[d7] if d7 < len(VDIV) else f'idx{d7}'
    raw_range = f"{min(valid)}-{max(valid)}" if valid else "empty"
    
    print(f"{p:>4}  {seq:>5}  "
          f"0x{d4:02X}  0x{d5:02X}  0x{d6:02X}  0x{d7:02X}  0x{d8:02X}  0x{d9:02X}  "
          f"{seq:>6}  {ch2_lbl:>10}  {ch1_lbl:>10}  {d8:>5}  0x{d9:02X}  "
          f"{cutoff:>9}  {raw_range}")

print("\nNote: d[6]=CH2 vdiv, d[7]=CH1 vdiv, d[8]=timebase, d[9]=ch_flags")
print("      flags: 0x01=CH1 only, 0x02=CH2 only, 0x03=both")
