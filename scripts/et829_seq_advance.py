"""
ET829 — Seq counter advance test
Seq is stuck at 478. Find what makes it increment.
"""
import usb.core, usb.util, time, sys, os, struct

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; GRY="\033[90m"
BLD="\033[1m"; CYN="\033[96m"; RED="\033[91m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT, EP_BULK = 0x05, 0x84

dev = usb.core.find(idVendor=VID, idProduct=PID)
if not dev: print("Not found!"); exit()
for i in range(3):
    try:
        if dev.is_kernel_driver_active(i): dev.detach_kernel_driver(i)
    except: pass
try: dev.set_configuration()
except: pass
cp(GRN, "Connected. SCOPE mode.\n")

def drain():
    while True:
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def get_seq():
    """Read CH1 and return (seq, samples[:3])."""
    drain()
    dev.write(EP_OUT, bytes([0x00, 0x02]), timeout=500)
    time.sleep(0.15)
    buf = bytearray()
    while True:
        try: buf.extend(dev.read(EP_BULK, 512, timeout=200))
        except: break
    if len(buf) < 10 or buf[0] != 0xA5 or buf[1] != 0x22: return None, None
    plen = struct.unpack_from('<H', buf, 2)[0]
    p = buf[4:4+plen]
    return struct.unpack_from('<H', p, 0)[0], list(p[6:9])

def send(cmd):
    try:
        drain()
        dev.write(EP_OUT, cmd, timeout=500)
        time.sleep(0.05)
        try: r = bytes(dev.read(EP_BULK, 64, timeout=100)); return r
        except: return None
    except: return None

# Baseline
seq0, s0 = get_seq()
cp(YLW, f"Baseline seq={seq0}  samples={s0}\n")

cp(CYN, "=== Test 1: repeat A5 22 01 many times, check if seq advances ===")
for i in range(8):
    send(bytes([0xA5, 0x22, 0x01]))
    time.sleep(0.3)
    seq, s = get_seq()
    changed = seq != seq0
    cp(GRN if changed else GRY, f"  A5 22 01 #{i+1}: seq={seq}  {'CHANGED!' if changed else 'same'}")
    if changed: seq0 = seq

cp(CYN, "\n=== Test 2: just wait — does seq advance on its own? ===")
cp(YLW, "Waiting 5 seconds without sending anything...")
time.sleep(5)
seq, s = get_seq()
cp(GRN if seq != seq0 else GRY, f"  After 5s wait: seq={seq}  {'CHANGED!' if seq != seq0 else 'same'}")
if seq != seq0: seq0 = seq

cp(CYN, "\n=== Test 3: read CH2 then CH1 — does reading CH2 unlock CH1? ===")
drain()
dev.write(EP_OUT, bytes([0x00, 0x03]), timeout=500)
time.sleep(0.2)
buf2 = bytearray()
while True:
    try: buf2.extend(dev.read(EP_BULK, 512, timeout=200))
    except: break
cp(GRY, f"  CH2 response: {len(buf2)}B  {bytes(buf2)[:8].hex().upper()}")
seq, s = get_seq()
cp(GRN if seq != seq0 else GRY, f"  CH1 after CH2: seq={seq}  {'CHANGED!' if seq != seq0 else 'same'}")
if seq != seq0: seq0 = seq

cp(CYN, "\n=== Test 4: read 00 04 (both) then 00 02 ===")
drain()
dev.write(EP_OUT, bytes([0x00, 0x04]), timeout=500)
time.sleep(0.3)
buf4 = bytearray()
while True:
    try: buf4.extend(dev.read(EP_BULK, 512, timeout=200))
    except: break
cp(GRY, f"  00 04 response: {len(buf4)}B")
seq, s = get_seq()
cp(GRN if seq != seq0 else GRY, f"  CH1 after 00 04: seq={seq}  {'CHANGED!' if seq != seq0 else 'same'}")
if seq != seq0: seq0 = seq

cp(CYN, "\n=== Test 5: scan A5 XX 01 variants ===")
for b in [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x2A, 0x2B, 0x2C]:
    cmd = bytes([0xA5, b, 0x01])
    send(cmd)
    time.sleep(0.3)
    seq, s = get_seq()
    cp(GRN if seq != seq0 else GRY,
       f"  A5 {b:02X} 01: seq={seq}  {'*** CHANGED! ***' if seq != seq0 else 'same'}")
    if seq != seq0: seq0 = seq

cp(CYN, "\n=== Test 6: A5 22 XX variants (different value byte) ===")
for b in range(0x10):
    cmd = bytes([0xA5, 0x22, b])
    send(cmd)
    time.sleep(0.25)
    seq, s = get_seq()
    cp(GRN if seq != seq0 else GRY,
       f"  A5 22 {b:02X}: seq={seq}  {'*** CHANGED! ***' if seq != seq0 else 'same'}")
    if seq != seq0: seq0 = seq