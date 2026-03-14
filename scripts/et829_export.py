"""
ET829 / MDS8209 — SAVE BANK READER & VISUALIZER
=================================================
Reads ALL saved waveform pages from the device and generates:
  - One PNG per saved waveform  (et829_page_00.png, etc.)
  - One combined overview PNG   (et829_overview.png)
  - Optional CSV per page       (--csv flag)

Press SAVE on the device to store waveforms, then run this script.

Usage:
  python et829_export.py                   # export all, save to current folder
  python et829_export.py --out captures/   # custom output folder
  python et829_export.py --csv             # also export raw CSV
  python et829_export.py --no-overview     # skip combined image
"""

import usb.core, usb.util, time, sys, struct, argparse, os, csv, datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import AutoMinorLocator

VID, PID  = 0x2E88, 0x4603
EP_OUT    = 0x05
EP_BULK   = 0x84
MAX_SCAN  = 64   # max seek values to probe

VDIV_TABLE = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
VDIV_LABEL = ['10mV','20mV','50mV','100mV','200mV','500mV','1V','2V','5V','10V']

# Extended vdiv byte map for indices ≥ 10.
# CONFIRMED from device: index 0x0E (14) = 1V/div
# (1X probe shows ±2.7V; raw abs_peak=83; 83/128×vdiv×4=2.7 → vdiv≈1.04V ✓)
# The device V/div table appears to repeat/extend beyond index 9.
# Add entries here as confirmed from device screen observations.
VDIV_EXTENDED = {
    0x0A: (0.01, '10mV'),   # tentative — mirrors index 0
    0x0B: (0.02, '20mV'),
    0x0C: (0.05, '50mV'),
    0x0D: (0.1,  '100mV'),
    0x0E: (1.0,  '1V'),     # CONFIRMED: all saves at 1V/div match display
    0x0F: (2.0,  '2V'),
    0x10: (5.0,  '5V'),
    0x11: (10.0, '10V'),
}

TIMEBASE_TABLE = [
    '5ns','10ns','20ns','50ns','100ns','200ns','500ns',
    '1µs','2µs','5µs','10µs','20µs','50µs',
    '1ms','2ms','5ms','10ms','20ms','50ms',
    '100ms','200ms','500ms','1s','2s','5s','10s'
]

def get_vdiv(byte_val, raw_samples, vdiv_override=None):
    """
    The V/div setting is NOT stored in save frames — the header bytes are
    session counters, not vdiv indices. Voltage scale cannot be recovered
    from the frame alone.

    If vdiv_override is provided (from --vdiv CLI arg), use that.
    Otherwise report raw ADC units normalized to ±1.0 (displayed as ±128 raw).
    The waveform SHAPE is always correct. Only Y-axis scale requires knowing vdiv.

    To get true voltages: multiply Y values by (your_vdiv / 1.0).
    e.g. if device was at 2V/div, multiply all values by 2.
    """
    if vdiv_override is not None:
        return vdiv_override, f'{vdiv_override}V/div'
    # Return 1V/div as neutral base — shape correct, scale needs user verification
    return 1.0, '1V/div(⚠ multiply by actual V/div setting)'

def trim_garbage_tail(raw_samples, run_len=15):
    """Remove uninitialized buffer tail: first run of run_len zeros → truncate."""
    n = len(raw_samples)
    for i in range(n - run_len):
        if all(raw_samples[j] == 0 for j in range(i, i + run_len)):
            return raw_samples[:i]
    return raw_samples

# ── USB ───────────────────────────────────────────────────────────────────────

def find_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("Device not found — connect ET829/MDS8209 via USB")
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
    dev.write(EP_OUT, tx, timeout=1000)
    time.sleep(0.15)
    buf = bytearray()
    while True:
        try:
            buf.extend(dev.read(EP_BULK, 512, timeout=300))
            if len(buf) >= max_bytes: break
        except: break
    return bytes(buf) if buf else None

# ── Protocol ──────────────────────────────────────────────────────────────────

def seek_and_read(dev, page):
    """Seek to page N and read it. Returns raw bytes or None."""
    xfer(dev, bytes([0xA5, 0x22, page]), max_bytes=64)
    time.sleep(0.06)
    return xfer(dev, bytes([0x00, 0x02]))

def parse_frame(data, vdiv_override=None):
    """Parse a waveform frame. Returns dict or None.

    IMPORTANT: V/div is NOT stored in save frames. Header bytes d[6]/d[7] are
    session counters, not vdiv indices. Use --vdiv to set the correct scale,
    or note the V/div setting on the device screen when pressing SAVE.
    Without --vdiv, all captures are shown at 1V/div (shape correct, scale ×N).
    """
    if not data or len(data) < 10 or data[0] != 0xA5:
        return None
    try:
        cmd      = data[1]
        plen     = struct.unpack_from('<H', data, 2)[0]
        if plen < 6: return None
        seq      = struct.unpack_from('<H', data, 4)[0]
        ch_flags = data[9]
        tb_idx   = data[8]
        raw      = list(data[10:])
        if not raw: return None

        raw = trim_garbage_tail(raw)
        if not raw: return None

        vdiv, vdiv_lbl = get_vdiv(None, raw, vdiv_override)
        tb_lbl = TIMEBASE_TABLE[tb_idx] if tb_idx < len(TIMEBASE_TABLE) else f'idx{tb_idx}'

        def to_v(s): return (s - 128) / 128.0 * (vdiv * 4)
        ch1 = [to_v(s) for s in raw]

        return dict(
            seq=seq, cmd=cmd, ch_flags=ch_flags,
            ch1_vdiv=vdiv, ch2_vdiv=vdiv,
            ch1_label=vdiv_lbl, ch2_label=vdiv_lbl,
            tb_idx=tb_idx, tb_label=tb_lbl,
            raw=raw, ch1=ch1, n=len(raw)
        )
    except:
        return None

def read_all_pages(dev, vdiv_override=None):
    """Scan all seek values, return list of (seek, frame) for valid pages."""
    print("Scanning saved pages...", flush=True)
    pages = []
    for p in range(MAX_SCAN):
        data  = seek_and_read(dev, p)
        frame = parse_frame(data, vdiv_override)
        if frame:
            pages.append((p, frame))
            vdiv_note = f"{frame['ch1_vdiv']}V/div" + (
                " (set via --vdiv)" if vdiv_override else " (⚠ use --vdiv for true scale)")
            print(f"  Page {p:2d}: seq={frame['seq']:5d}  n={frame['n']:4d}  {vdiv_note}")
        elif p > 0 and len(pages) > 0 and p > pages[-1][0] + 3:
            break  # stop after 3 consecutive misses past first found page
    return pages

# ── Stats ─────────────────────────────────────────────────────────────────────

def wstats(v):
    if not v: return 0, 0, 0, 0, 0
    vmin, vmax = min(v), max(v)
    vpp  = vmax - vmin
    dc   = sum(v) / len(v)
    rms  = (sum(x*x for x in v) / len(v)) ** 0.5
    return vpp, dc, rms, vmin, vmax

# ── Plotting ──────────────────────────────────────────────────────────────────

COLORS = {
    'bg':       '#1a1a2e',
    'panel':    '#16213e',
    'grid':     '#2a2a4a',
    'ch1':      '#00d4aa',
    'ch2':      '#ff6b35',
    'text':     '#e0e0e0',
    'subtext':  '#888888',
    'border':   '#0f3460',
    'accent':   '#e94560',
}

def style_ax(ax, frame, title=""):
    """Apply oscilloscope-style dark theme to an axis."""
    vdiv  = frame['ch1_vdiv']
    top   =  vdiv * 4
    bot   = -(vdiv * 4)
    n     = frame['n']

    ax.set_facecolor(COLORS['panel'])
    ax.set_xlim(0, n - 1)
    ax.set_ylim(bot * 1.05, top * 1.05)

    # Grid — 8 vertical divisions, 10 horizontal
    for i in range(9):
        y = bot + (top - bot) * i / 8
        ax.axhline(y, color=COLORS['grid'], linewidth=0.5, zorder=0)
    for i in range(11):
        x = (n - 1) * i / 10
        ax.axvline(x, color=COLORS['grid'], linewidth=0.5, zorder=0)

    # Zero line
    ax.axhline(0, color=COLORS['subtext'], linewidth=0.8, linestyle='--', zorder=1)

    ax.tick_params(colors=COLORS['subtext'], labelsize=7)
    ax.spines[:].set_color(COLORS['border'])

    # Y ticks at each division
    yticks = [bot + (top - bot) * i / 8 for i in range(9)]
    ax.set_yticks(yticks)
    ax.set_yticklabels([f"{y:+.3f}" for y in yticks], fontsize=6)

    ax.set_xticks([])
    if title:
        ax.set_title(title, color=COLORS['text'], fontsize=9, pad=4)

def plot_single(page_idx, seek, frame, outpath):
    """Generate a detailed single-page PNG."""
    fig = plt.figure(figsize=(12, 6), facecolor=COLORS['bg'])
    fig.subplots_adjust(left=0.07, right=0.97, top=0.88, bottom=0.12)

    ax = fig.add_subplot(111)
    style_ax(ax, frame)

    n  = frame['n']
    xs = list(range(n))
    ax.plot(xs, frame['ch1'], color=COLORS['ch1'], linewidth=1.0,
            label='CH1', zorder=3)

    vpp, dc, rms, vmin, vmax = wstats(frame['ch1'])
    vdiv = frame['ch1_vdiv']

    # Header
    fig.text(0.07, 0.94,
             f"ET829 / MDS8209  —  Saved Waveform  (Slot {page_idx + 1})",
             color=COLORS['text'], fontsize=12, fontweight='bold')
    fig.text(0.07, 0.90,
             f"seq={frame['seq']}   CH1: {frame['ch1_label']}/div   "
             f"Timebase: {frame['tb_label']}   Samples: {n}",
             color=COLORS['subtext'], fontsize=9)

    # Stats box
    stats_txt = (f"Vpp = {vpp:.4f} V\n"
                 f"Vmax = {vmax:.4f} V\n"
                 f"Vmin = {vmin:.4f} V\n"
                 f"DC   = {dc:.4f} V\n"
                 f"Vrms = {rms:.4f} V")
    ax.text(0.99, 0.97, stats_txt,
            transform=ax.transAxes, fontsize=8,
            color=COLORS['ch1'], verticalalignment='top',
            horizontalalignment='right',
            fontfamily='monospace',
            bbox=dict(facecolor=COLORS['bg'], alpha=0.8,
                      edgecolor=COLORS['border'], boxstyle='round,pad=0.4'))

    # V/div label
    ax.set_ylabel(f"Voltage  ({frame['ch1_label']}/div)", color=COLORS['subtext'], fontsize=8)
    ax.set_xlabel(f"Sample index  ({n} samples total)", color=COLORS['subtext'], fontsize=8)

    ax.legend(loc='upper left', fontsize=8, facecolor=COLORS['bg'],
              edgecolor=COLORS['border'], labelcolor=COLORS['ch1'])

    plt.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=COLORS['bg'])
    plt.close(fig)

def plot_overview(pages, outpath):
    """Generate combined overview PNG — all pages in a grid."""
    n_pages = len(pages)
    cols    = min(3, n_pages)
    rows    = (n_pages + cols - 1) // cols

    fig = plt.figure(figsize=(cols * 5.5, rows * 3.2 + 1.0),
                     facecolor=COLORS['bg'])
    fig.suptitle("ET829 / MDS8209  —  All Saved Waveforms",
                 color=COLORS['text'], fontsize=14, fontweight='bold', y=0.98)

    for idx, (seek, frame) in enumerate(pages):
        ax = fig.add_subplot(rows, cols, idx + 1)
        style_ax(ax, frame,
                 title=f"Slot {idx+1}  (seq={frame['seq']}  {frame['ch1_label']}/div  {frame['tb_label']})")

        n  = frame['n']
        xs = list(range(n))
        ax.plot(xs, frame['ch1'], color=COLORS['ch1'], linewidth=0.8, zorder=3)

        vpp, dc, rms, vmin, vmax = wstats(frame['ch1'])
        ax.text(0.99, 0.97,
                f"Vpp={vpp:.3f}V  DC={dc:.3f}V",
                transform=ax.transAxes, fontsize=7,
                color=COLORS['ch1'], va='top', ha='right',
                fontfamily='monospace',
                bbox=dict(facecolor=COLORS['bg'], alpha=0.7,
                          edgecolor='none', boxstyle='round,pad=0.2'))

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=COLORS['bg'])
    plt.close(fig)
    print(f"  Saved overview: {outpath}")

# ── CSV ───────────────────────────────────────────────────────────────────────

def save_csv(page_idx, seek, frame, outpath):
    with open(outpath, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['sample_idx', 'raw', 'ch1_volts'])
        for i, (r, v) in enumerate(zip(frame['raw'], frame['ch1'])):
            w.writerow([i, r, f"{v:.6f}"])
    print(f"  CSV: {outpath}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Read all saved waveforms from ET829/MDS8209 and export as PNG',
        epilog='NOTE: V/div is not stored in save frames. Use --vdiv to set correct scale.')
    ap.add_argument('--out',         default='.', metavar='DIR',
                    help='Output directory (default: current folder)')
    ap.add_argument('--csv',         action='store_true',
                    help='Also save raw CSV files')
    ap.add_argument('--no-overview', action='store_true',
                    help='Skip the combined overview image')
    ap.add_argument('--no-single',   action='store_true',
                    help='Skip individual page images (overview only)')
    ap.add_argument('--vdiv',        type=float, default=None, metavar='V',
                    help='V/div setting from device screen (e.g. 1.0, 2.0, 0.5). '
                         'Applied to ALL pages. Without this, shape is correct but '
                         'Y-axis values need multiplying by actual V/div.')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    dev = find_dev()
    print(f"Connected: {dev.manufacturer} — {dev.product}")
    if args.vdiv:
        print(f"V/div override: {args.vdiv}V/div")
    else:
        print("⚠  No --vdiv set. Y-axis shows values at 1V/div base.")
        print("   Multiply Y values by your actual V/div setting for true voltages.")
        print("   e.g.  python et829_export.py --vdiv 2.0\n")

    # Enter scope mode
    dev.write(EP_OUT, bytes([0x0D, 0x00]), timeout=1000)
    time.sleep(0.3)
    drain(dev)

    pages = read_all_pages(dev, vdiv_override=args.vdiv)
    if not pages:
        print("No saved pages found. Press SAVE on the device first.")
        return

    print(f"\nFound {len(pages)} saved waveform(s). Exporting...\n")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    for idx, (seek, frame) in enumerate(pages):
        base = os.path.join(args.out, f"et829_{ts}_slot{idx+1:02d}_seq{frame['seq']}")

        if not args.no_single:
            png = base + ".png"
            plot_single(idx, seek, frame, png)
            print(f"  PNG:  {png}")

        if args.csv:
            save_csv(idx, seek, frame, base + ".csv")

    if not args.no_overview:
        overview = os.path.join(args.out, f"et829_{ts}_overview.png")
        plot_overview(pages, overview)

    print(f"\nDone. {len(pages)} waveform(s) exported to: {os.path.abspath(args.out)}")

if __name__ == '__main__':
    main()
