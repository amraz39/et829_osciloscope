"""Quick test to reproduce TX=00 05 measurement response."""
import usb.core, usb.util, time, struct

VID, PID = 0x2E88, 0x4603
EP_OUT = 0x05
EP_BULK = 0x84

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    print("Device not found!"); exit()

for intf in range(3):
    try:
        if dev.is_kernel_driver_active(intf): dev.detach_kernel_driver(intf)
    except: pass
try: dev.set_configuration()
except: pass

print("Connected:", dev.manufacturer, "—", dev.product)

def read_all(timeout=500):
    results = []
    while True:
        try:
            d = bytes(dev.read(EP_BULK, 64, timeout=timeout))
            results.append(d)
            timeout = 100  # shorter for subsequent reads
        except: break
    return results

def send(cmd, label=""):
    print(f"\nTX {label}: {cmd.hex().upper()}")
    dev.write(EP_OUT, cmd, timeout=1000)
    time.sleep(0.3)
    results = read_all()
    for r in results:
        print(f"  RX: {r.hex().upper()}  ({len(r)}B)")
    if not results:
        print("  RX: (none)")
    return results

# Try different sequences
print("\n=== Sequence 1: direct 00 05 ===")
send(bytes([0x00, 0x05]), "00 05")

print("\n=== Sequence 2: CRLF then 00 05 ===")
send(bytes([0x0D, 0x0A]), "0D 0A")
send(bytes([0x00, 0x05]), "00 05")

print("\n=== Sequence 3: 0D 01 then 00 05 ===")
send(bytes([0x0D, 0x01]), "0D 01")
send(bytes([0x00, 0x05]), "00 05")

print("\n=== Sequence 4: rapid 00 05 x5 ===")
for i in range(5):
    r = send(bytes([0x00, 0x05]), f"00 05 #{i+1}")
    if any(len(x) > 7 for x in r):
        print("  *** LONG RESPONSE FOUND ***")
        break
    time.sleep(0.1)

print("\n=== Sequence 5: 0D 0A, 0D 01, then 00 05 immediately ===")
dev.write(EP_OUT, bytes([0x0D, 0x0A]), timeout=1000)
time.sleep(0.1)
dev.write(EP_OUT, bytes([0x0D, 0x01]), timeout=1000)
time.sleep(0.1)
dev.write(EP_OUT, bytes([0x00, 0x05]), timeout=1000)
time.sleep(0.5)
r = read_all(300)
for x in r: print(f"  RX: {x.hex().upper()}  ({len(x)}B)")

print("\n=== Sequence 6: try 00 05 many times with long wait ===")
for i in range(10):
    try:
        dev.read(EP_BULK, 64, timeout=50)
    except: pass
    dev.write(EP_OUT, bytes([0x00, 0x05]), timeout=1000)
    time.sleep(0.5)
    try:
        r = bytes(dev.read(EP_BULK, 64, timeout=500))
        print(f"  Attempt {i+1}: RX={r.hex().upper()}  ({len(r)}B)")
        if len(r) > 7:
            print("  *** MEASUREMENT DATA ***")
    except:
        print(f"  Attempt {i+1}: (none)")