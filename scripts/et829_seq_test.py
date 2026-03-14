"""Replicate exact brute force sequence that got the 00 05 response."""
import usb.core, usb.util, time

VID, PID = 0x2E88, 0x4603
EP_OUT = 0x05
EP_BULK = 0x84

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None: print("Device not found!"); exit()
for intf in range(3):
    try:
        if dev.is_kernel_driver_active(intf): dev.detach_kernel_driver(intf)
    except: pass
try: dev.set_configuration()
except: pass
print("Connected:", dev.manufacturer)

def drain():
    while True:
        try: dev.read(EP_BULK, 64, timeout=50)
        except: break

def txrx(b1, b2, wait=0.15):
    drain()
    cmd = bytes([b1, b2])
    dev.write(EP_OUT, cmd, timeout=500)
    time.sleep(wait)
    try:
        r = bytes(dev.read(EP_BULK, 64, timeout=300))
        print(f"  {b1:02X} {b2:02X} → {r.hex().upper()}  ({len(r)}B){'  ***HIT***' if len(r)>7 else ''}")
        return r
    except:
        print(f"  {b1:02X} {b2:02X} → (none)")
        return None

print("\n=== Test 1: send 00 00 through 00 05 (exact brute force sequence) ===")
for b in range(6):
    r = txrx(0x00, b)
    if r and len(r) > 7:
        print(f"  *** TRIGGERED BY 00 {b:02X} ***")

print("\n=== Test 2: just 00 04 then 00 05 ===")
txrx(0x00, 0x04)
txrx(0x00, 0x05)

print("\n=== Test 3: 00 03 then 00 05 ===")
txrx(0x00, 0x03)
txrx(0x00, 0x05)

print("\n=== Test 4: 00 01 then 00 05 ===")
txrx(0x00, 0x01)
txrx(0x00, 0x05)

print("\n=== Test 5: repeat 00 05 ten times fast (no drain between) ===")
for i in range(10):
    cmd = bytes([0x00, 0x05])
    dev.write(EP_OUT, cmd, timeout=500)
    time.sleep(0.08)
    try:
        r = bytes(dev.read(EP_BULK, 64, timeout=150))
        print(f"  #{i+1}: {r.hex().upper()}  ({len(r)}B){'  ***HIT***' if len(r)>7 else ''}")
        if len(r) > 7: break
    except:
        print(f"  #{i+1}: (none)")

print("\n=== Test 6: what mode is the meter in? send 0D 0A ===")
drain()
dev.write(EP_OUT, bytes([0x0D, 0x0A]), timeout=500)
time.sleep(0.4)
try:
    r = bytes(dev.read(EP_BULK, 64, timeout=400))
    print(f"  Mode response: {r.hex().upper()}")
    if len(r) >= 5:
        mode = r[4]
        modes = {0x30:"DC-V",0x31:"AC-V",0x32:"DC-A",0x33:"AC-A",
                 0x34:"Ohm",0x35:"Cap",0x36:"Hz",0x37:"Diode",
                 0x38:"Buzz",0x39:"Temp",0x3A:"NCV"}
        print(f"  Mode byte: 0x{mode:02X} = {modes.get(mode, 'unknown')}")
except:
    print("  (no response)")

print("\n=== Test 7: switch to Hz mode (0D 0A gives mode, try changing ranges) ===")
print("Make sure meter is in Hz mode with AWG connected, then press Enter...")
input()
drain()
for i in range(5):
    dev.write(EP_OUT, bytes([0x00, 0x00]), timeout=500)
    time.sleep(0.1)
    dev.write(EP_OUT, bytes([0x00, 0x05]), timeout=500)
    time.sleep(0.3)
    try:
        r = bytes(dev.read(EP_BULK, 64, timeout=400))
        print(f"  00 00 + 00 05 #{i+1}: {r.hex().upper()}  ({len(r)}B)")
        if len(r) > 7: break
    except:
        print(f"  #{i+1}: (none)")