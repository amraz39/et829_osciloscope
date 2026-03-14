"""
ET829 - USBPcap capture + serial traffic simultaneously
Run this, then look at the .pcap file to find the ET829 traffic.

Usage: python et829_capture.py
"""
import subprocess, serial, time, os, sys, threading

USBPCAP = r"C:\Program Files\USBPcap\USBPcapCMD.exe"
COM_PORT = "COM8"
BAUD = 115200

def serial_worker(stop_event):
    """Continuously send commands to the meter to generate USB traffic."""
    try:
        ser = serial.Serial(COM_PORT, BAUD, timeout=0.3,
                           xonxoff=False, rtscts=False, dsrdtr=False)
        print(f"[SERIAL] Opened {COM_PORT}")
        count = 0
        while not stop_event.is_set():
            ser.reset_input_buffer()
            # Alternate between known commands to generate varied traffic
            if count % 3 == 0:
                cmd = b"\x0D\x0A"
                label = "PING"
            elif count % 3 == 1:
                cmd = b"\x0D\x01"
                label = "INIT"
            else:
                cmd = b"\x0D\x0A"
                label = "PING"

            ser.write(cmd)
            resp = ser.read(64)
            ts = time.strftime("%H:%M:%S")
            if resp:
                print(f"[SERIAL] [{ts}] TX={cmd.hex().upper()} RX={resp.hex().upper()}")
            else:
                print(f"[SERIAL] [{ts}] TX={cmd.hex().upper()} (no response)")
            count += 1
            time.sleep(0.5)
        ser.close()
    except Exception as e:
        print(f"[SERIAL] Error: {e}")

print("="*60)
print("ET829 USBPcap Capture + Traffic Generator")
print("="*60)
print()
print("This script:")
print("  1. Starts USBPcap capture on USBPcap1 AND USBPcap2")
print("  2. Simultaneously sends commands to ET829 via COM8")
print("  3. The ET829's USB traffic will appear in the .pcap")
print()
print("Make sure ET829 is ON and in DMM mode.")
print("Press Enter to start...")
input()

stop = threading.Event()
serial_thread = threading.Thread(target=serial_worker, args=(stop,), daemon=True)

# Start USBPcap captures
procs = []
files = []
for i in [1, 2]:
    device = f"\\\\.\\USBPcap{i}"
    outfile = f"et829_live_USBPcap{i}.pcap"
    files.append((i, outfile))
    try:
        p = subprocess.Popen(
            [USBPCAP, "-d", device, "-o", outfile,
             "-A", "--inject-descriptors"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        procs.append((i, p))
        print(f"[PCAP] Started USBPcap{i} → {outfile}")
    except Exception as e:
        print(f"[PCAP] USBPcap{i} failed: {e}")

if not procs:
    print("No captures started!")
    sys.exit(1)

time.sleep(1)  # Let USBPcap initialize

# Start serial traffic
serial_thread.start()
print("\n[INFO] Capture running. Now:")
print("  - Change meter ranges")
print("  - Switch between DMM and scope mode")
print("  - Measure different things (voltage, resistance, etc)")
print("\nPress Ctrl+C to stop (runs 60s automatically)\n")

try:
    for remaining in range(60, 0, -1):
        sys.stdout.write(f"\r  {remaining:2d}s remaining... (Ctrl+C to stop early)")
        sys.stdout.flush()
        time.sleep(1)
except KeyboardInterrupt:
    pass

print("\n\nStopping...")
stop.set()
for i, p in procs:
    p.terminate()
    print(f"[PCAP] USBPcap{i} stopped.")

time.sleep(0.5)
print("\nCapture files:")
for i, f in files:
    if os.path.exists(f):
        size = os.path.getsize(f)
        print(f"  USBPcap{i}: {f}  ({size} bytes){' ← upload this!' if size > 500 else ' (too small, likely wrong interface)'}")
    else:
        print(f"  USBPcap{i}: {f} — not created")

print("\nUpload the larger .pcap file to Claude!")