"""
ET829 — Find the scope re-arm command
The device returns the same frozen buffer on every 00 02 read.
Frame counter byte[0-1] = DE 00 never changes = same buffer replayed.
We need to find which command triggers a new capture.
"""
import usb.core, usb.util, time, sys, os, struct
from datetime import datetime

os.system("")
RST="\033[0m"; GRN="\033[92m"; YLW="\033[93m"; GRY="\033[90m"; BLD="\033[1m"; CYN="\033[96m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

VID, PID = 0x2E88, 0x4603
EP_OUT, EP_BULK = 0x05, 0x84

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None: print("Not found!"); exit()
for i in range(3):
    try:
        if dev.is_kernel_driver_active(i): dev.detach_kernel_driver(i)
    except: pass
try: dev.set_configuration()
except: pass
cp(GRN, "Connected. Device must be in SCOPE mode.\n")

def drain():
    while True:
        try: dev.read(EP_BULK, 512, timeout=40)
        except: break

def read_waveform():
    """Read CH1 waveform, return (seq_counter, first_10_samples) or None."""
    drain()
    dev.write(EP_OUT, bytes([0x00, 0x02]), timeout=500)
    time.sleep(0.15)
    buf = bytearray()
    while True:
        try:
            chunk = bytes(dev.read(EP_BULK, 512, timeout=200))
            buf.extend(chunk)
        except: break
    if len(buf) < 10 or buf[0] != 0xA5 or buf[1] != 0x22:
        return None
    plen = struct.unpack_from('<H', buf, 2)[0]
    payload = buf[4:4+plen]
    seq = struct.unpack_from('<H', payload, 0)[0]
    samples = list(payload[6:16])
    return seq, samples

def try_rearm(cmd, label, wait=0.3):
    """Send potential re-arm command, then read waveform. Return new seq counter."""
    drain()
    try:
        dev.write(EP_OUT, cmd, timeout=500)
        time.sleep(0.05)
        try:
            r = bytes(dev.read(EP_BULK, 64, timeout=100))
            resp = r.hex().upper()
        except:
            resp = "(no resp)"
    except: resp = "(error)"
    time.sleep(wait)
    result = read_waveform()
    return resp, result

# First: get baseline
cp(YLW, "Getting baseline (current frozen frame)...")
baseline = read_waveform()
if baseline is None:
    cp(GRN, "No waveform — waiting 2s for device to be ready...")
    time.sleep(2)
    baseline = read_waveform()
if baseline is None:
    print("Still no data. Replugging USB might help."); exit()

base_seq, base_samples = baseline
cp(GRN, f"Baseline: seq={base_seq} (0x{base_seq:04X})  samples={base_samples[:5]}")
cp(YLW, f"Looking for any command that changes seq from {base_seq}...\n")

# Test candidates
candidates = [
    # write-only commands (no response expected = trigger commands)
    (bytes([0x00, 0x06]), "00 06"),
    (bytes([0x00, 0x07]), "00 07"),
    (bytes([0x00, 0x08]), "00 08"),
    (bytes([0x00, 0x0A]), "00 0A"),
    (bytes([0x00, 0x0B]), "00 0B"),
    (bytes([0x00, 0x0C]), "00 0C"),
    (bytes([0x00, 0x0D]), "00 0D"),
    (bytes([0x00, 0x0E]), "00 0E"),
    (bytes([0x00, 0x0F]), "00 0F"),
    (bytes([0x00, 0x10]), "00 10"),
    # A5-style scope commands
    (bytes([0x0D, 0x00]), "0D 00 (scope)"),
    (bytes([0x0D, 0x0A]), "0D 0A (ping)"),
    # re-read with just 00 02 again (sanity)
    (bytes([0x00, 0x02]), "00 02 (self)"),
    # Try 00 01 as re-arm
    (bytes([0x00, 0x01]), "00 01 (arm)"),
    # 3-byte A5 variants
    (bytes([0xA5, 0x22, 0x01]), "A5 22 01"),
    (bytes([0xA5, 0x00, 0x01]), "A5 00 01"),
]

found = []
for cmd, label in candidates:
    resp, result = try_rearm(cmd, label, wait=0.4)
    if result:
        new_seq, new_samples = result
        changed = (new_seq != base_seq)
        marker = f"  {BLD}{GRN}*** SEQ CHANGED! {base_seq} → {new_seq} ***{RST}" if changed else ""
        cp(GRN if changed else GRY,
           f"  {label:<20} resp={resp:<20} seq={new_seq:5d}  samples={new_samples[:3]}{marker}")
        if changed:
            found.append((label, cmd, new_seq))
            base_seq = new_seq  # update baseline
    else:
        cp(GRY, f"  {label:<20} resp={resp:<20} → no waveform")
    time.sleep(0.1)

print()
if found:
    cp(BLD+GRN, f"RE-ARM COMMANDS FOUND: {len(found)}")
    for label, cmd, seq in found:
        cp(GRN, f"  {label}: {cmd.hex().upper()} → new seq={seq}")
else:
    cp(YLW, "No re-arm command found in this set.")
    cp(YLW, "The device may need a longer delay between captures,")
    cp(YLW, "or the re-arm might be implicit (send 00 02 only AFTER the device signals ready).")
    cp(YLW, "\nTry: just wait longer — does the seq counter change on its own after 2-5 seconds?")
    print()
    cp(CYN, "Testing: wait 3 seconds and re-read...")
    time.sleep(3)
    result = read_waveform()
    if result:
        new_seq, _ = result
        cp(GRN if new_seq != base_seq else YLW,
           f"After 3s wait: seq={new_seq}  {'CHANGED!' if new_seq != base_seq else 'still same'}")