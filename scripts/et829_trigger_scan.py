"""
ET829 — Trigger investigation tool

Two goals:
1. Find software trigger command (send trigger from PC)
2. Document AUTO trigger mode (continuous capture)

Known facts:
- Pressing AUTO button on device fires a new capture
- Normal trigger mode only captures on signal crossing
- seq changes on a page = new capture occurred
- hdr[5] = channel flags, hdr[1] = sub-page index?

Strategy for software trigger:
- Scan all 3-byte A5 XX YY commands where XX=0x22..0x2F, YY=0x00..0x0F
- After each, check if any page seq changed = trigger fired
"""
import usb.core, usb.util, time, struct, os
from datetime import datetime

os.system("")
GRN="\033[92m"; YLW="\033[93m"; GRY="\033[90m"; CYN="\033[96m"
BLD="\033[1m"; RED="\033[91m"; RST="\033[0m"
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
cp(GRN, "Connected.\n")

def drain():
    while True:
        try: dev.read(EP_BULK, 512, timeout=30)
        except: break

def get_all_seqs():
    """Read seq from all 7 pages. Returns dict {page: seq}."""
    seqs = {}
    for pg in range(7):
        drain()
        dev.write(EP_OUT, bytes([0xA5, 0x22, pg]), timeout=500)
        time.sleep(0.04)
        try: dev.read(EP_BULK, 64, timeout=80)
        except: pass
        drain()
        dev.write(EP_OUT, bytes([0x00, 0x02]), timeout=500)
        time.sleep(0.12)
        buf = bytearray()
        while True:
            try: buf.extend(dev.read(EP_BULK, 512, timeout=150))
            except: break
        if len(buf) >= 10 and buf[0] == 0xA5 and buf[1] == 0x22:
            plen = struct.unpack_from('<H', buf, 2)[0]
            p = buf[4:4+plen]
            seqs[pg] = struct.unpack_from('<H', p, 0)[0]
    return seqs

def any_seq_changed(before, after):
    for pg in before:
        if pg in after and after[pg] != before[pg]:
            return pg, before[pg], after[pg]
    return None

def send_cmd(cmd, wait=0.5):
    """Send command, drain response, wait, return response bytes."""
    drain()
    dev.write(EP_OUT, cmd, timeout=500)
    time.sleep(0.05)
    resp = bytearray()
    try: resp.extend(dev.read(EP_BULK, 64, timeout=100))
    except: pass
    time.sleep(wait)
    return bytes(resp)

# ── Step 1: Baseline ────────────────────────────────────────────────────────
cp(YLW, "Step 1: Baseline seq snapshot")
base = get_all_seqs()
cp(GRY, "  " + "  ".join(f"p{pg}:{seq}" for pg,seq in sorted(base.items())))

# ── Step 2: Test AUTO trigger candidates ───────────────────────────────────
cp(YLW, "\nStep 2: Scanning for software trigger command")
cp(YLW, "Testing A5 XX YY where XX in 0x20-0x2F, YY in 0x00-0x0F\n")

found_triggers = []

for xx in range(0x20, 0x30):
    for yy in range(0x10):
        cmd = bytes([0xA5, xx, yy])
        
        # Skip known seek commands
        if xx in [0x22, 0x23, 0x24] and yy < 8:
            cp(GRY, f"  A5 {xx:02X} {yy:02X}  (skip - known seek)")
            continue
        
        snap_before = get_all_seqs()
        resp = send_cmd(cmd, wait=0.4)
        snap_after = get_all_seqs()
        
        changed = any_seq_changed(snap_before, snap_after)
        resp_str = resp.hex().upper() if resp else "(none)"
        
        if changed:
            pg, old_seq, new_seq = changed
            cp(GRN+BLD, f"  A5 {xx:02X} {yy:02X}  resp={resp_str:<16}  *** TRIGGER! page {pg}: {old_seq}→{new_seq} ***")
            found_triggers.append((xx, yy, pg, old_seq, new_seq))
        else:
            cp(GRY, f"  A5 {xx:02X} {yy:02X}  resp={resp_str:<16}  no trigger")

# ── Step 3: Test 0D-prefix trigger candidates ───────────────────────────────
cp(YLW, "\nStep 3: Testing 0D XX candidates (mode/trigger commands)")
for xx in range(0x00, 0x20):
    cmd = bytes([0x0D, xx])
    snap_before = get_all_seqs()
    resp = send_cmd(cmd, wait=0.4)
    snap_after = get_all_seqs()
    changed = any_seq_changed(snap_before, snap_after)
    resp_str = resp.hex().upper() if resp else "(none)"
    if changed:
        pg, old_seq, new_seq = changed
        cp(GRN+BLD, f"  0D {xx:02X}  resp={resp_str:<16}  *** TRIGGER! page {pg}: {old_seq}→{new_seq} ***")
        found_triggers.append((0x0D, xx, pg, old_seq, new_seq))
    elif resp:
        cp(YLW, f"  0D {xx:02X}  resp={resp_str}")

# ── Step 4: Test single-byte and 00-prefix ───────────────────────────────
cp(YLW, "\nStep 4: Testing 00 XX candidates")
for xx in [0x00, 0x05, 0x06, 0x07, 0x08, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
           0x10, 0x11, 0x12, 0x20, 0x21, 0x22, 0x30, 0x40, 0x50, 0xFF]:
    cmd = bytes([0x00, xx])
    snap_before = get_all_seqs()
    resp = send_cmd(cmd, wait=0.4)
    snap_after = get_all_seqs()
    changed = any_seq_changed(snap_before, snap_after)
    resp_str = resp.hex().upper() if resp else "(none)"
    if changed:
        pg, old_seq, new_seq = changed
        cp(GRN+BLD, f"  00 {xx:02X}  resp={resp_str:<16}  *** TRIGGER! ***")
        found_triggers.append((0x00, xx, pg, old_seq, new_seq))
    elif resp:
        cp(YLW, f"  00 {xx:02X}  resp={resp_str}")

# ── Summary ──────────────────────────────────────────────────────────────────
print()
cp(BLD+CYN, "═══ SUMMARY ═══")
if found_triggers:
    cp(GRN+BLD, f"Software trigger commands found: {len(found_triggers)}")
    for xx, yy, pg, old_seq, new_seq in found_triggers:
        cp(GRN, f"  {xx:02X} {yy:02X}  → triggered page {pg}: seq {old_seq}→{new_seq}")
else:
    cp(YLW, "No software trigger found in scanned range.")
    cp(YLW, "Recommendation: set device to AUTO trigger mode manually,")
    cp(YLW, "then use ring-buffer poll (et829_scope_live2.py) to capture data.")
    cp(CYN, "\nAUTO mode doc: press AUTO button on device to enter continuous")
    cp(CYN, "trigger mode. Script detects seq changes on any page.")

cp(BLD+CYN, "\n═══ AUTO TRIGGER MODE NOTES ═══")
cp(CYN, "- Press AUTO button on device = forces immediate capture")
cp(CYN, "- In AUTO mode: device captures continuously on each trigger event")  
cp(CYN, "- Ring buffer: pages 0→1→2→3→4→5→6→0→1... (7 slots)")
cp(CYN, "- Each new capture writes to next page slot, advancing seq by 256")
cp(CYN, "- et829_scope_live2.py detects all seq changes across all pages")
