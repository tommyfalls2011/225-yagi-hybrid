"""Visual proof: SWR curves vs cell spacing — watch the 3 dips merge.

For each cell-helper spacing from DE, runs a full NEC2 SWR sweep and:
  * plots the SWR curve over 25.0-29.0 MHz
  * marks the dips (local minima)
  * draws the SWR=1.5 and SWR=2.0 reference lines
  * annotates bandwidth at <=2:1
Output: PNGs in scripts/charts/, plus a summary table.

Sweeps a WIDER spacing range than the previous study (8" to 40", every 2")
so the merge progression is easy to see.  Includes ASCII summary in case
the user wants to read it in terminal.
"""
import csv
import json
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path("/app/src_repo/hybrid_auto7")
sys.path.insert(0, str(ROOT))
from hyagi import v2_runner


OUT_DIR = ROOT / "scripts/charts"
OUT_DIR.mkdir(exist_ok=True)

WL = 11811.0 / 27.195                  # ~434.4"
DE_POS = round(0.108 * WL, 1)          # ~46.9"
DIR_SP = round(0.18 * WL, 1)           # ~78"

BASE = [
    {"name": "REF",     "position_in": 0.0,            "length_in": 220.0},
    {"name": "XFRMR",   "position_in": DE_POS - 18.5,  "length_in": 222.0},
    {"name": "DE",      "position_in": DE_POS,         "length_in": 215.5},
    {"name": "COUPLER", "position_in": DE_POS + 20.0,  "length_in": 198.0},
    {"name": "DIR1",    "position_in": DE_POS + DIR_SP,    "length_in": 196.0},
    {"name": "DIR2",    "position_in": DE_POS + 2 * DIR_SP, "length_in": 193.0},
    {"name": "DIR3",    "position_in": DE_POS + 3 * DIR_SP, "length_in": 190.0},
]
v2_runner.GROUNDING = "all_insulated"
v2_runner.GROUNDED = False


def sweep(els, lo=25.0, hi=29.0, points=200):
    try:
        curve, _mx, _av = v2_runner.band_swr_curve(els, lo, hi, points, 26.0)
        return [(c[0], c[3]) for c in curve]
    except Exception:
        return []


def find_dips(curve, max_swr=10.0):
    dips = []
    for i in range(1, len(curve) - 1):
        if curve[i][1] < curve[i - 1][1] and curve[i][1] < curve[i + 1][1] \
                and curve[i][1] < max_swr:
            dips.append((curve[i][0], curve[i][1]))
    return dips


def bandwidth_under(curve, threshold, lo_freq=25.5, hi_freq=28.5):
    """Find the WIDEST contiguous window where SWR stays under `threshold`."""
    inside = [(f, s) for f, s in curve if lo_freq <= f <= hi_freq]
    spans = []
    start = None
    for f, s in inside:
        if s <= threshold and start is None:
            start = f
        elif s > threshold and start is not None:
            spans.append((start, f))
            start = None
    if start is not None:
        spans.append((start, inside[-1][0]))
    if not spans:
        return None
    best = max(spans, key=lambda sp: sp[1] - sp[0])
    return best[0], best[1], (best[1] - best[0]) * 1000.0  # kHz wide


def make_grid_chart(panels, out_path, title):
    """panels = [(label, curve, dips), ...]
    Lays them out in a grid and writes a PNG."""
    n = len(panels)
    cols = 3
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(15, 3.4 * rows),
                             sharey=True)
    if rows == 1:
        axes = [axes]
    axes = [a for row in axes for a in (row if hasattr(row, '__iter__') else [row])]

    for ax, (label, curve, dips) in zip(axes, panels):
        if not curve:
            ax.text(0.5, 0.5, "NEC error", ha="center", va="center")
            ax.set_title(label)
            continue
        freqs = [f for f, _ in curve]
        swrs = [s for _, s in curve]
        ax.plot(freqs, swrs, color="#0b3b8c", linewidth=1.6)
        # SWR reference lines
        ax.axhline(2.0, color="#cc8800", linestyle="--", linewidth=0.9,
                   label="SWR 2.0", alpha=0.75)
        ax.axhline(1.5, color="#3a8a3a", linestyle="--", linewidth=0.9,
                   label="SWR 1.5", alpha=0.75)
        # Mark each dip with a red dot
        for fdip, sdip in dips:
            ax.plot(fdip, sdip, "o", color="red", markersize=6)
            ax.annotate(f"{fdip:.2f}", (fdip, sdip),
                        textcoords="offset points", xytext=(0, -15),
                        ha="center", fontsize=8, color="red")
        # Band-max annotation
        bmax = max(swrs)
        ax.set_title(f"{label}\nband-max {bmax:.2f}  |  {len(dips)} dips",
                     fontsize=11)
        ax.set_xlabel("Freq (MHz)")
        ax.set_ylabel("SWR")
        ax.set_xlim(25.0, 29.0)
        ax.set_ylim(1.0, 10.0)
        ax.grid(True, alpha=0.3)

    # Hide unused axes
    for ax in axes[n:]:
        ax.axis("off")

    fig.suptitle(title, fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def make_summary_chart(rows, out_path):
    """Plot of band-max-SWR vs cell gap, plus bandwidth-at-2-to-1 vs gap."""
    gaps = [r["gap"] for r in rows]
    bmax = [r["band_max"] for r in rows]
    bw15 = [r["bw15_khz"] for r in rows]
    bw20 = [r["bw20_khz"] for r in rows]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    ax1.plot(gaps, bmax, "o-", color="#b03030", linewidth=2)
    ax1.set_yscale("log")
    ax1.set_xlabel("Cell helper gap from DE (inches, each side)")
    ax1.set_ylabel("Band-max SWR (log scale)")
    ax1.set_title("Worst SWR in 25.7-28.7 MHz vs cell spacing")
    ax1.axhline(2.0, color="#cc8800", linestyle="--", linewidth=1, alpha=0.7,
                label="SWR 2:1")
    ax1.axhline(1.5, color="#3a8a3a", linestyle="--", linewidth=1, alpha=0.7,
                label="SWR 1.5:1")
    ax1.grid(True, which="both", alpha=0.3)
    ax1.legend()

    ax2.plot(gaps, bw20, "o-", color="#cc8800", label="BW under 2:1", linewidth=2)
    ax2.plot(gaps, bw15, "o-", color="#3a8a3a", label="BW under 1.5:1", linewidth=2)
    ax2.set_xlabel("Cell helper gap from DE (inches, each side)")
    ax2.set_ylabel("Usable bandwidth (kHz)")
    ax2.set_title("Usable bandwidth vs cell spacing")
    ax2.grid(True, alpha=0.3)
    ax2.legend()

    fig.suptitle("Cell spacing trade-off: band-max SWR vs usable bandwidth",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def main():
    # Sweep cell gap each side of DE from 8" to 40" in 2" steps.
    gaps = list(range(8, 42, 2))
    rows = []
    panels = []
    print(f"{'gap':>4}  {'bmax':>7}  {'BW@1.5':>8}  {'BW@2.0':>8}  dips")
    print("-" * 70)
    for gap in gaps:
        els = json.loads(json.dumps(BASE))
        for e in els:
            if e["name"] == "XFRMR":
                e["position_in"] = DE_POS - gap
            elif e["name"] == "COUPLER":
                e["position_in"] = DE_POS + gap
        curve = sweep(els)
        if not curve:
            print(f"{gap:>4}  NEC ERROR")
            continue
        dips = find_dips(curve)
        in_band = [(f, s) for f, s in curve if 25.7 <= f <= 28.7]
        bmax = max(s for _, s in in_band)
        bw15 = bandwidth_under(curve, 1.5)
        bw20 = bandwidth_under(curve, 2.0)
        bw15_kHz = bw15[2] if bw15 else 0
        bw20_kHz = bw20[2] if bw20 else 0
        rows.append({
            "gap": gap, "band_max": round(bmax, 2),
            "bw15_khz": round(bw15_kHz),
            "bw20_khz": round(bw20_kHz),
            "n_dips": len(dips),
            "dip_freqs": ";".join(f"{f:.2f}" for f, _ in dips),
        })
        panels.append((f"gap = {gap}\"", curve, dips))
        print(f"{gap:>4}\"  {bmax:>7.2f}  {bw15_kHz:>7.0f}k  "
              f"{bw20_kHz:>7.0f}k  {[(round(f,2), round(s,2)) for f,s in dips]}")

    # CSV
    with open(OUT_DIR / "cell_gap_sweep.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Grid chart: SWR curves at every gap (so user can SEE the dips merge)
    make_grid_chart(panels, OUT_DIR / "cell_gap_swr_curves.png",
                    "SWR curves vs cell helper gap from DE  (3-dip merge progression)")
    # Summary chart: band-max & bandwidth vs gap
    make_summary_chart(rows, OUT_DIR / "cell_gap_summary.png")
    print()
    print(f"\nPNGs written to {OUT_DIR}")
    print("  cell_gap_swr_curves.png — one panel per gap, SWR curve + dips")
    print("  cell_gap_summary.png    — band-max + bandwidth vs gap")
    print(f"  cell_gap_sweep.csv      — raw numbers")


if __name__ == "__main__":
    main()
