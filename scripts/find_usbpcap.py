"""
Find USBPcap interface for ET829 and capture USB traffic.
Run: python find_usbpcap.py
"""
import subprocess, os, time, sys

USBPCAP = r"C:\Program Files\USBPcap\USBPcapCMD.exe"

print("Scanning for valid USBPcap interfaces...\n")

found = []
for i in range(1, 20):
    device = f"\\\\.\\USBPcap{i}"
    try:
        # Try to open briefly
        p = subprocess.Popen(
            [USBPCAP, "-d", device, "-o", "nul", "-A"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        time.sleep(1.5)
        ret = p.poll()
        if ret is None:
            # Still running = valid interface!
            print(f"  USBPcap{i}  ← VALID (still running)")
            found.append(i)
            p.terminate()
        else:
            out, err = p.communicate()
            msg = (out+err).decode(errors='ignore').strip()
            print(f"  USBPcap{i}  ← invalid ({msg[:60]})")
    except FileNotFoundError:
        print(f"USBPcapCMD not found at: {USBPCAP}")
        sys.exit(1)
    except Exception as e:
        print(f"  USBPcap{i}  ← error: {e}")

if not found:
    print("\nNo valid interfaces found!")
    print("Try running this script as Administrator (right-click CMD → Run as administrator)")
    sys.exit(1)

print(f"\nFound {len(found)} valid interface(s): {[f'USBPcap{i}' for i in found]}")
print("\nNow capturing on all valid interfaces...")
print(">>> Make sure your ET829 is ON and measuring something <<<")
print(">>> Change ranges, switch to scope mode and back       <<<")
print("Press Enter to start 30-second capture, then Ctrl+C to stop early...")
input()

procs = []
files = []
for i in found:
    device = f"\\\\.\\USBPcap{i}"
    outfile = f"et829_cap_USBPcap{i}.pcap"
    files.append(outfile)
    p = subprocess.Popen([USBPCAP, "-d", device, "-o", outfile, "-A",
                          "--inject-descriptors"])
    procs.append((i, p))
    print(f"  Started capture on USBPcap{i} → {outfile}")

print(f"\nCapturing... operate your meter now!")
print("Press Ctrl+C to stop.\n")

try:
    for remaining in range(30, 0, -1):
        sys.stdout.write(f"\r  {remaining:2d}s remaining...")
        sys.stdout.flush()
        time.sleep(1)
except KeyboardInterrupt:
    pass

print("\n\nStopping capture...")
for i, p in procs:
    p.terminate()
    print(f"  USBPcap{i} stopped.")

print(f"\nCapture files saved:")
for f in files:
    if os.path.exists(f) and os.path.getsize(f) > 0:
        print(f"  {f}  ({os.path.getsize(f)} bytes)  ← upload this!")
    else:
        print(f"  {f}  (empty or missing)")

print("\nUpload the .pcap file(s) to Claude for analysis!")