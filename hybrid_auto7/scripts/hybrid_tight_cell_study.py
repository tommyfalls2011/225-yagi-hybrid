"""HYBRID cell-correct study: lengths sweep at TIGHT (real-world) cell gaps.

User correction: 'its a hybrid widebanded antenna it needs close xfrmr and
coupler otherwise its a yagi.. why do you keep wasting my time and money'.

Re-runs the merge study within the actual hybrid spacing range (8-20"
from DE on each side, real-world commercial hybrid builds), and at each
spacing searches the full LENGTH plane (XFRMR len x COUPLER len) for the
best 3-dip merge.  This is the design space a HYBRID actually lives in.

Output:
  scripts/charts/hybrid_tight_cell.png    one panel per cell gap, best SWR curve
  scripts/charts/hybrid_tight_cell.csv    raw results
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


OUT = ROOT / "scripts/charts"
OUT.mkdir(exist_ok=True)

WL = 11811.0 / 27.195
DE_POS = round(0.108 * WL, 1)             # 46.9"
DIR_SP = round(0.18 * WL, 1)              # 78"
DE_LEN = 215.5
BASE = [
    {"name": "REF",     "position_in": 0.0,                "length_in": 220.0},
    {"name": "XFRMR",   "position_in": DE_POS - 16.0,      "length_in": 218.0},
    {"name": "DE",      "position_in": DE_POS,             "length_in": DE_LEN},
    {"name": "COUPLER", "position_in": DE_POS + 16.0,      "length_in": 205.0},
    {"name": "DIR1",    "position_in": DE_POS + DIR_SP,    "length_in": 196.0},
    {"name": "DIR2",    "position_in": DE_POS + 2 * DIR_SP, "length_in": 193.0},
    {"name": "DIR3",    "position_in": DE_POS + 3 * DIR_SP, "length_in": 190.0},
]
v2_runner.GROUNDING = "all_insulated"
v2_runner.GROUNDED = False


def sweep(els, lo=25.0, hi=29.0, points=180):
    try:
        c, _, _ = v2_runner.band_swr_curve(els, lo, hi, points, 26.0)
        return [(x[0], x[3]) for x in c]
    except Exception:
        return []


def find_dips(curve, max_swr=8.0):
    return [(curve[i][0], curve[i][1])
            for i in range(1, len(curve) - 1)
            if curve[i][1] < curve[i - 1][1] and curve[i][1] < curve[i + 1][1]
            and curve[i][1] < max_swr]


def bw_under(curve, threshold, lo=25.5, hi=28.7):
    inside = [(f, s) for f, s in curve if lo <= f <= hi]
    spans = []
    start = None
    for f, s in inside:
        if s <= threshold and start is None:
            start = f
        elif s > threshold and start is not None:
            spans.append((start, f)); start = None
    if start is not None:
        spans.append((start, inside[-1][0]))
    return max(((sp[1] - sp[0]) * 1000 for sp in spans), default=0.0)


def search(gap):
    """Find best (XF_len, CP_len) pair at this cell gap (each side of DE).
    Searches the full L_xf x L_cp plane and returns the BEST band-max."""
    best = None
    # XFRMR length: from DE to DE+10 (the OWA-style longer-than-DE region)
    # COUPLER length: from DE-5 down to DE-22 (typical hybrid short)
    xf_grid = [DE_LEN + d for d in (0, 2, 4, 6, 8, 10)]
    cp_grid = [DE_LEN - d for d in (3, 6, 9, 12, 15, 18, 21)]
    for xf in xf_grid:
        for cp in cp_grid:
            els = json.loads(json.dumps(BASE))
            for e in els:
                if e["name"] == "XFRMR":
                    e["position_in"] = DE_POS - gap
                    e["length_in"] = xf
                elif e["name"] == "COUPLER":
                    e["position_in"] = DE_POS + gap
                    e["length_in"] = cp
            curve = sweep(els)
            if not curve:
                continue
            in_band = [(f, s) for f, s in curve if 25.7 <= f <= 28.7]
            bmax = max(s for _, s in in_band)
            dips = find_dips(curve)
            rec = {"gap": gap, "xf_len": xf, "cp_len": cp,
                   "band_max": round(bmax, 2),
                   "n_dips": len(dips),
                   "dip_freqs": ";".join(f"{f:.2f}" for f, _ in dips),
                   "bw15_khz": round(bw_under(curve, 1.5)),
                   "bw20_khz": round(bw_under(curve, 2.0)),
                   "curve": curve}
            if best is None or bmax < best["band_max"]:
                best = rec
    return best


def main():
    gaps = [8, 10, 12, 14, 16, 18, 20]
    bests = {}
    csv_rows = []
    print(f"{'gap':>4}  {'XF':>5} {'CP':>5}  {'bmax':>6}  "
          f"{'BW@1.5':>7}  {'BW@2.0':>7}  dips")
    print("-" * 80)
    for gap in gaps:
        best = search(gap)
        if best is None:
            print(f"{gap}\"  (no NEC results)")
            continue
        bests[gap] = best
        csv_rows.append({k: best[k] for k in best if k != "curve"})
        print(f"{gap:>4}\"  {best['xf_len']:>5.1f} {best['cp_len']:>5.1f}  "
              f"{best['band_max']:>6.2f}  {best['bw15_khz']:>6}k  "
              f"{best['bw20_khz']:>6}k  {best['dip_freqs']}")

    with open(OUT / "hybrid_tight_cell.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        w.writeheader()
        w.writerows(csv_rows)

    cols = 3
    rows = (len(gaps) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(15, 3.6 * rows))
    axes = [a for r in axes for a in (r if hasattr(r, '__iter__') else [r])]
    for ax, gap in zip(axes, gaps):
        b = bests.get(gap)
        if not b:
            ax.axis("off"); continue
        freqs = [f for f, _ in b["curve"]]
        swrs  = [s for _, s in b["curve"]]
        ax.plot(freqs, swrs, "#0b3b8c", linewidth=1.6)
        ax.axhline(2.0, color="#cc8800", linestyle="--", linewidth=0.9, alpha=0.7)
        ax.axhline(1.5, color="#3a8a3a", linestyle="--", linewidth=0.9, alpha=0.7)
        for f, s in find_dips(b["curve"]):
            ax.plot(f, s, "o", color="red", markersize=6)
            ax.annotate(f"{f:.2f}", (f, s),
                        textcoords="offset points", xytext=(0, -14),
                        ha="center", fontsize=8, color="red")
        title = (f"CELL GAP {gap}\" each side\n"
                 f"XF={b['xf_len']:.0f}(DE{b['xf_len']-DE_LEN:+.0f})  "
                 f"CP={b['cp_len']:.0f}(DE{b['cp_len']-DE_LEN:+.0f})\n"
                 f"bmax {b['band_max']:.2f}  BW@2:1 {b['bw20_khz']} kHz")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Freq (MHz)"); ax.set_ylabel("SWR")
        ax.set_xlim(25.0, 29.0)
        ax.set_ylim(1.0, max(6.0, max(swrs) * 1.05))
        ax.grid(True, alpha=0.3)
    for ax in axes[len(gaps):]:
        ax.axis("off")
    fig.suptitle("HYBRID tight-cell study: best SWR curve at each cell gap\n"
                 "(every length pair searched; tight cell = real hybrid topology)",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "hybrid_tight_cell.png", dpi=110)
    plt.close(fig)
    print(f"\nPNG: {OUT / 'hybrid_tight_cell.png'}")
    print(f"CSV: {OUT / 'hybrid_tight_cell.csv'}")


if __name__ == "__main__":
    main()
