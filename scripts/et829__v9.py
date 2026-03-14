"""
ET829 / MDS8209 — Direct EP3 Reader via Windows USB API  v9
=============================================================
Reads the Interrupt IN endpoint (EP3/0x83) directly using
Windows SetupAPI + WinUSB via ctypes — NO driver change needed.

Also monitors COM port event flags which may reflect EP3 notifications.

Usage:
  python et829_v9.py             # try all methods
  python et829_v9.py --events    # monitor COM port events (EV_*)
  python et829_v9.py --winusb    # direct WinUSB access attempt
  python et829_v9.py --modem     # monitor modem status lines

Requirements: pip install pyserial pywin32
  (pywin32: pip install pywin32)
"""

import sys, os, time, ctypes, ctypes.wintypes, threading, struct, argparse
import serial
from datetime import datetime

if sys.platform != "win32":
    print("This script is Windows-only.")
    sys.exit(1)

os.system("")
RST="\033[0m"; RED="\033[91m"; GRN="\033[92m"
YLW="\033[93m"; CYN="\033[96m"; GRY="\033[90m"; BLD="\033[1m"
def cp(c,*a): print(c+" ".join(str(x) for x in a)+RST, flush=True)

PORT = "COM8"
BAUD = 115200

# ─────────────────────────────────────────────────────────────────────────────
# METHOD 1: Monitor COM port event flags
# usbser.sys translates EP3 Interrupt notifications into COM events
# EV_RXCHAR, EV_CTS, EV_DSR, EV_RLSD, EV_BREAK, EV_ERR, EV_RING etc.
# ─────────────────────────────────────────────────────────────────────────────

# Win32 constants
GENERIC_READ        = 0x80000000
GENERIC_WRITE       = 0x40000000
OPEN_EXISTING       = 3
FILE_FLAG_OVERLAPPED= 0x40000000
INVALID_HANDLE_VALUE= ctypes.c_void_p(-1).value

EV_RXCHAR  = 0x0001
EV_RXFLAG  = 0x0002
EV_TXEMPTY = 0x0004
EV_CTS     = 0x0008
EV_DSR     = 0x0010
EV_RLSD    = 0x0020
EV_BREAK   = 0x0040
EV_ERR     = 0x0080
EV_RING    = 0x0100
EV_ALL     = 0x01FF

kernel32 = ctypes.windll.kernel32

def monitor_com_events(port, baud, duration=30):
    cp(CYN, f"\n[COM EVENTS] Monitoring all serial events on {port} for {duration}s")
    cp(YLW, "This captures EP3 Interrupt notifications translated by usbser.sys")
    cp(YLW, "Change meter ranges and modes while this runs!")
    cp(YLW, "Press Enter to start...")
    input()

    # Open port with overlapped I/O
    port_path = f"\\\\.\\{port}"
    h = kernel32.CreateFileW(
        port_path,
        GENERIC_READ | GENERIC_WRITE,
        0, None, OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED, None
    )
    
    if h == INVALID_HANDLE_VALUE:
        cp(RED, f"Cannot open {port}: error {kernel32.GetLastError()}")
        return

    cp(GRN, f"Opened {port} (handle={h})")

    # Configure baud rate etc via DCB
    class DCB(ctypes.Structure):
        _fields_ = [
            ("DCBlength",       ctypes.c_ulong),
            ("BaudRate",        ctypes.c_ulong),
            ("fBinary",         ctypes.c_uint, 1),
            ("fParity",         ctypes.c_uint, 1),
            ("fOutxCtsFlow",    ctypes.c_uint, 1),
            ("fOutxDsrFlow",    ctypes.c_uint, 1),
            ("fDtrControl",     ctypes.c_uint, 2),
            ("fDsrSensitivity", ctypes.c_uint, 1),
            ("fTXContinueOnXoff",ctypes.c_uint,1),
            ("fOutX",           ctypes.c_uint, 1),
            ("fInX",            ctypes.c_uint, 1),
            ("fErrorChar",      ctypes.c_uint, 1),
            ("fNull",           ctypes.c_uint, 1),
            ("fRtsControl",     ctypes.c_uint, 2),
            ("fAbortOnError",   ctypes.c_uint, 1),
            ("fDummy2",         ctypes.c_uint, 17),
            ("wReserved",       ctypes.c_ushort),
            ("XonLim",          ctypes.c_ushort),
            ("XoffLim",         ctypes.c_ushort),
            ("ByteSize",        ctypes.c_byte),
            ("Parity",          ctypes.c_byte),
            ("StopBits",        ctypes.c_byte),
            ("XonChar",         ctypes.c_char),
            ("XoffChar",        ctypes.c_char),
            ("ErrorChar",       ctypes.c_char),
            ("EofChar",         ctypes.c_char),
            ("EvtChar",         ctypes.c_char),
            ("wReserved1",      ctypes.c_ushort),
        ]
    
    dcb = DCB()
    dcb.DCBlength = ctypes.sizeof(DCB)
    kernel32.GetCommState(h, ctypes.byref(dcb))
    dcb.BaudRate = baud
    dcb.ByteSize = 8
    dcb.Parity   = 0
    dcb.StopBits = 0
    dcb.fDtrControl = 1  # DTR_CONTROL_ENABLE
    dcb.fRtsControl = 1  # RTS_CONTROL_ENABLE
    kernel32.SetCommState(h, ctypes.byref(dcb))

    # Set event mask to ALL events
    kernel32.SetCommMask(h, EV_ALL)

    # Set timeouts
    class COMMTIMEOUTS(ctypes.Structure):
        _fields_ = [
            ("ReadIntervalTimeout",         ctypes.c_ulong),
            ("ReadTotalTimeoutMultiplier",  ctypes.c_ulong),
            ("ReadTotalTimeoutConstant",    ctypes.c_ulong),
            ("WriteTotalTimeoutMultiplier", ctypes.c_ulong),
            ("WriteTotalTimeoutConstant",   ctypes.c_ulong),
        ]
    timeouts = COMMTIMEOUTS(100, 0, 500, 0, 1000)
    kernel32.SetCommTimeouts(h, ctypes.byref(timeouts))

    # Send init commands
    written = ctypes.c_ulong(0)
    ping = b"\x0D\x0A"
    kernel32.WriteFile(h, ping, len(ping), ctypes.byref(written), None)
    cp(GRN, f"Sent CRLF ping ({written.value}B written)")
    time.sleep(0.3)
    init = b"\x0D\x01"
    kernel32.WriteFile(h, init, len(init), ctypes.byref(written), None)
    cp(GRN, f"Sent ENTER DMM ({written.value}B written)")
    time.sleep(0.2)

    cp(CYN, "\nListening for events and data...\n")
    deadline = time.time() + duration
    event_count = 0

    while time.time() < deadline:
        # Check modem status
        modem_status = ctypes.c_ulong(0)
        kernel32.GetCommModemStatus(h, ctypes.byref(modem_status))
        ms = modem_status.value
        cts  = bool(ms & 0x0010)
        dsr  = bool(ms & 0x0020)
        ring = bool(ms & 0x0040)
        dcd  = bool(ms & 0x0080)

        # Try to read data
        buf = ctypes.create_string_buffer(256)
        bytes_read = ctypes.c_ulong(0)
        kernel32.ReadFile(h, buf, 256, ctypes.byref(bytes_read), None)
        
        if bytes_read.value > 0:
            data = bytes(buf.raw[:bytes_read.value])
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            cp(BLD+GRN, f"[{ts}] DATA {bytes_read.value}B: {data.hex().upper()}")
            event_count += 1

        # Check error/status
        errors = ctypes.c_ulong(0)
        class COMSTAT(ctypes.Structure):
            _fields_ = [("fFlags", ctypes.c_ulong),
                        ("cbInQue", ctypes.c_ulong),
                        ("cbOutQue", ctypes.c_ulong)]
        comstat = COMSTAT()
        kernel32.ClearCommError(h, ctypes.byref(errors), ctypes.byref(comstat))
        
        if comstat.cbInQue > 0:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            sys.stdout.write(f"\r[{ts}] InQueue={comstat.cbInQue}B  CTS={cts} DSR={dsr} DCD={dcd} RING={ring}  ")
            sys.stdout.flush()

        rem = int(deadline - time.time())
        sys.stdout.write(f"\r  CTS={int(cts)} DSR={int(dsr)} DCD={int(dcd)} RI={int(ring)}  {rem:3d}s  events={event_count}  ")
        sys.stdout.flush()
        time.sleep(0.1)

    print()
    kernel32.CloseHandle(h)
    cp(CYN, f"Done. {event_count} data events captured.")

# ─────────────────────────────────────────────────────────────────────────────
# METHOD 2: Modem status monitor via pyserial
# Watch DSR/CTS/RI/CD lines — usbser translates EP3 data to these signals
# ─────────────────────────────────────────────────────────────────────────────
def monitor_modem(port, baud, duration=30):
    cp(CYN, f"\n[MODEM STATUS] Monitoring CTS/DSR/RI/CD on {port}")
    cp(YLW, "Change meter modes and ranges while this runs!")
    cp(YLW, "Press Enter to start...")
    input()

    try:
        ser = serial.Serial(port, baud, timeout=0.05,
                           xonxoff=False, rtscts=False, dsrdtr=False)
        ser.dtr = True
        ser.rts = True

        # Send init
        ser.write(b"\x0D\x0A")
        time.sleep(0.2)
        ser.write(b"\x0D\x01")
        time.sleep(0.2)
        ser.reset_input_buffer()

        prev_cts = prev_dsr = prev_ri = prev_cd = None
        prev_data = None
        deadline = time.time() + duration

        while time.time() < deadline:
            cts = ser.cts
            dsr = ser.dsr
            ri  = ser.ri
            cd  = ser.cd

            data = ser.read(256)
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            if data and data != prev_data:
                cp(GRN, f"[{ts}] DATA: {data.hex().upper()}")
                prev_data = data

            changed = (cts != prev_cts or dsr != prev_dsr or
                      ri != prev_ri or cd != prev_cd)
            if changed:
                cp(YLW, f"[{ts}] MODEM CHANGE: CTS={int(cts)} DSR={int(dsr)} "
                         f"RI={int(ri)} CD={int(cd)}")
                prev_cts=cts; prev_dsr=dsr; prev_ri=ri; prev_cd=cd

            rem = int(deadline - time.time())
            sys.stdout.write(
                f"\r  CTS={int(cts)} DSR={int(dsr)} RI={int(ri)} CD={int(cd)}  "
                f"{rem:3d}s  ")
            sys.stdout.flush()

        ser.close()

    except Exception as e:
        cp(RED, f"Error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# METHOD 3: Try WinUSB access to read EP3 directly
# Uses SetupAPI to find the device interface, then WinUSB to read EP3
# ─────────────────────────────────────────────────────────────────────────────
def try_winusb_ep3():
    cp(CYN, "\n[WINUSB EP3] Attempting direct EP3 read via WinUSB")
    
    try:
        import winreg
        # Find the device in registry
        # HKLM\SYSTEM\CurrentControlSet\Enum\USB\VID_2E88&PID_4603
        key_path = r"SYSTEM\CurrentControlSet\Enum\USB\VID_2E88&PID_4603"
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
            cp(GRN, f"Found device in registry: {key_path}")
            # List subkeys (device instances)
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    cp(GRY, f"  Instance: {subkey_name}")
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except FileNotFoundError:
            cp(YLW, "Device not found in registry under VID_2E88&PID_4603")
    except ImportError:
        cp(YLW, "winreg not available")

    # Try to open USB device path directly
    # The composite parent device interface
    VID, PID = 0x2E88, 0x4603
    
    # Try various device path formats
    paths_to_try = [
        f"\\\\.\\USB#VID_{VID:04X}&PID_{PID:04X}",
        f"\\\\.\\USB#VID_{VID:04x}&PID_{PID:04x}",
    ]
    
    for path in paths_to_try:
        cp(GRY, f"Trying: {path}")
        h = kernel32.CreateFileW(
            path, GENERIC_READ | GENERIC_WRITE,
            0, None, OPEN_EXISTING, 0, None
        )
        if h != INVALID_HANDLE_VALUE:
            cp(GRN, f"Opened! handle={h}")
            kernel32.CloseHandle(h)
        else:
            cp(GRY, f"  → error {kernel32.GetLastError()}")

    cp(YLW, "\nDirect WinUSB access requires the WinUSB driver.")
    cp(YLW, "For EP3 access, Zadig is still needed to switch Interface 0.")
    cp(YLW, "\nZadig trick for jumping dropdown:")
    cp(YLW, "  1. Open Zadig")
    cp(YLW, "  2. Options → check 'List All Devices'")
    cp(YLW, "  3. Click the dropdown — it will jump to first item")
    cp(YLW, "  4. QUICKLY press the DOWN arrow key on keyboard")
    cp(YLW, "     to navigate the list without the mouse")
    cp(YLW, "  5. When USB ID shows 2E88 4603, press Tab then click Replace Driver")
    cp(YLW, "  6. Select 'WinUSB (Microsoft)' as the driver")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="ET829 v9 - EP3/Events Monitor")
    ap.add_argument("--port",     default="COM8")
    ap.add_argument("--baud",     type=int, default=115200)
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--events",   action="store_true", help="Monitor COM events via Win32 API")
    ap.add_argument("--modem",    action="store_true", help="Monitor modem status lines")
    ap.add_argument("--winusb",   action="store_true", help="Try WinUSB/registry info")
    args = ap.parse_args()

    if args.modem:
        monitor_modem(args.port, args.baud, args.duration)
    elif args.winusb:
        try_winusb_ep3()
    else:
        # Default: run all methods
        cp(CYN, "Running all EP3/event monitoring methods...")
        cp(YLW, "KEEP CHANGING METER RANGES AND MODES THROUGHOUT!\n")
        monitor_modem(args.port, args.baud, args.duration)
        try_winusb_ep3()

if __name__ == "__main__":
    main()