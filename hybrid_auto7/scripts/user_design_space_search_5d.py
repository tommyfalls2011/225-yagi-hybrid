"""User-driven hybrid design space search (5D: positions + lengths).

User instinct from on-the-roof testing:
  * XFRMR closer to DE  (5.5\" - 14.5\" gap)
  * COUPLER further from DE (14.5\" - 32\")
  * Longer-than-usual DE
  * Tune XFRMR + COUPLER lengths around each scenario.

First pass (4D, fixed XF=DE+5 / CP=DE-15) FAILED -- every config had
band-max SWR > 80 because the LENGTHS need to be coordinated with
the POSITIONS, not held fixed.  Tightly-coupled XFRMR (close) needs a
DIFFERENT length than loosely-coupled XFRMR (far).

This 5D search:
  DE length:                 216, 220
  XFRMR position:            6, 10, 14
  COUPLER position:          18, 24, 30
  XFRMR length offset (DE+): 0, +3, +6
  COUPLER length offset (DE+): -8, -14, -20

= 162 configurations.  Each uses a 80-point SWR sweep; gain/F-B
computed for top 6 only after the search.
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
DE_POS = round(0.108 * WL, 1)
DIR_SP = round(0.18 * WL, 1)
v2_runner.GROUNDING = "all_insulated"
v2_runner.GROUNDED = False


def _build(de_len, ref_len, xf_gap, cp_gap, xf_len, cp_len):
    return [
        {"name": "REF",     "position_in": 0.0,             "length_in": ref_len},
        {"name": "XFRMR",   "position_in": DE_POS - xf_gap, "length_in": xf_len},
        {"name": "DE",      "position_in": DE_POS,          "length_in": de_len},
        {"name": "COUPLER", "position_in": DE_POS + cp_gap, "length_in": cp_len},
        {"name": "DIR1",    "position_in": DE_POS + DIR_SP,    "length_in": 196.0},
        {"name": "DIR2",    "position_in": DE_POS + 2 * DIR_SP, "length_in": 193.0},
        {"name": "DIR3",    "position_in": DE_POS + 3 * DIR_SP, "length_in": 190.0},
    ]


def _sweep(els):
    try:
        c, _, _ = v2_runner.band_swr_curve(els, 25.0, 29.0, 80, 26.0)
        return [(x[0], x[3]) for x in c]
    except Exception:
        return []


def _bw_under(curve, threshold, lo=25.7, hi=28.7):
    inside = [(f, s) for f, s in curve if lo <= f <= hi]
    spans, start = [], None
    for f, s in inside:
        if s <= threshold and start is None:
            start = f
        elif s > threshold and start is not None:
            spans.append((start, f)); start = None
    if start is not None:
        spans.append((start, inside[-1][0]))
    return max(((sp[1] - sp[0]) * 1000 for sp in spans), default=0.0)


def _evaluate(els, fc=27.195, height_ft=26.0):
    try:
        m = v2_runner.evaluate(els, {"global": {"freq_mhz_center": fc,
                                                 "freq_mhz_low": 25.7,
                                                 "freq_mhz_high": 28.7}},
                               height_ft=height_ft)
        return m.get("gain_dbi", 0.0), m.get("fb_db", 0.0)
    except Exception:
        return 0.0, 0.0


def main():
    de_lengths = [216.0, 220.0]
    ref_lengths = [220.0]
    xf_gaps = [6.0, 10.0, 14.0]
    cp_gaps = [18.0, 24.0, 30.0]
    xf_offsets = [0.0, 3.0, 6.0]
    cp_offsets = [-8.0, -14.0, -20.0]

    rows = []
    total = (len(de_lengths) * len(ref_lengths) * len(xf_gaps)
             * len(cp_gaps) * len(xf_offsets) * len(cp_offsets))
    print(f"Searching {total} configurations (5D grid, ~12-15 min)...")
    n = 0
    best_so_far = 999.0
    for de_len in de_lengths:
        for ref_len in ref_lengths:
            for xf_gap in xf_gaps:
                for cp_gap in cp_gaps:
                    for xf_off in xf_offsets:
                        for cp_off in cp_offsets:
                            n += 1
                            xf_len = de_len + xf_off
                            cp_len = de_len + cp_off
                            els = _build(de_len, ref_len, xf_gap, cp_gap,
                                         xf_len, cp_len)
                            curve = _sweep(els)
                            if not curve:
                                continue
                            in_band = [(f, s) for f, s in curve
                                       if 25.7 <= f <= 28.7]
                            bmax = max(s for _, s in in_band)
                            if bmax < best_so_far:
                                best_so_far = bmax
                            rec = {
                                "de_len": de_len, "ref_len": ref_len,
                                "xf_gap": xf_gap, "cp_gap": cp_gap,
                                "xf_len": xf_len, "cp_len": cp_len,
                                "xf_off": xf_off, "cp_off": cp_off,
                                "band_max": round(bmax, 2),
                                "bw15_khz": round(_bw_under(curve, 1.5)),
                                "bw20_khz": round(_bw_under(curve, 2.0)),
                                "gain_dbi": 0.0, "fb_db": 0.0,
                                "curve": curve,
                            }
                            rows.append(rec)
                            if n % 18 == 0:
                                print(f"  ...{n}/{total}  best so far "
                                      f"bmax={best_so_far:.2f}")

    rows.sort(key=lambda r: (r["band_max"], -r["bw20_khz"]))

    print("\nRunning pattern eval on top 6 (gain + F/B)...")
    for r in rows[:6]:
        els = _build(r["de_len"], r["ref_len"], r["xf_gap"], r["cp_gap"],
                     r["xf_len"], r["cp_len"])
        gain, fb = _evaluate(els)
        r["gain_dbi"] = round(gain, 2)
        r["fb_db"] = round(fb, 2)

    print("\n=== TOP 10 ===")
    print(f"{'#':>3}  {'DE':>5} {'XFg':>4} {'CPg':>4} {'XFlen':>5} {'CPlen':>5}  "
          f"{'bmax':>6}  {'BW@1.5':>7}  {'BW@2.0':>7}  {'gain':>5}  {'F/B':>5}")
    for i, r in enumerate(rows[:10], 1):
        gs = f"{r['gain_dbi']:>5.2f}" if i <= 6 else "  -- "
        fbs = f"{r['fb_db']:>5.2f}" if i <= 6 else "  -- "
        print(f"{i:>3}  {r['de_len']:>5.0f} {r['xf_gap']:>4.0f} "
              f"{r['cp_gap']:>4.0f} {r['xf_len']:>5.1f} {r['cp_len']:>5.1f}  "
              f"{r['band_max']:>6.2f}  {r['bw15_khz']:>6}k  "
              f"{r['bw20_khz']:>6}k  {gs}  {fbs}")

    csv_rows = [{k: v for k, v in r.items() if k != "curve"} for r in rows]
    with open(OUT / "user_design_space_search.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        w.writeheader(); w.writerows(csv_rows)

    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    axes = axes.flatten()
    for ax, r in zip(axes, rows[:6]):
        freqs = [f for f, _ in r["curve"]]; swrs = [s for _, s in r["curve"]]
        ax.plot(freqs, swrs, "#0b3b8c", linewidth=1.6)
        ax.axhline(2.0, color="#cc8800", linestyle="--", linewidth=0.9, alpha=0.7)
        ax.axhline(1.5, color="#3a8a3a", linestyle="--", linewidth=0.9, alpha=0.7)
        for i in range(1, len(r["curve"]) - 1):
            f0, s0 = r["curve"][i - 1]
            f1, s1 = r["curve"][i]
            f2, s2 = r["curve"][i + 1]
            if s1 < s0 and s1 < s2 and s1 < 4.0:
                ax.plot(f1, s1, "o", color="red", markersize=5)
                ax.annotate(f"{f1:.2f}", (f1, s1),
                            textcoords="offset points", xytext=(0, -12),
                            ha="center", fontsize=7, color="red")
        title = (f"DE={r['de_len']:.0f}  XFg={r['xf_gap']:.0f} "
                 f"CPg={r['cp_gap']:.0f}\n"
                 f"XFlen={r['xf_len']:.1f}(DE{r['xf_off']:+.0f})  "
                 f"CPlen={r['cp_len']:.1f}(DE{r['cp_off']:+.0f})\n"
                 f"bmax {r['band_max']:.2f}  BW@2:1 {r['bw20_khz']}k  "
                 f"gain {r['gain_dbi']:.1f}  F/B {r['fb_db']:.1f}")
        ax.set_title(title, fontsize=9)
        ax.set_xlim(25.0, 29.0)
        ax.set_ylim(1.0, max(6.0, max(swrs) * 1.05))
        ax.set_xlabel("Freq (MHz)"); ax.set_ylabel("SWR")
        ax.grid(True, alpha=0.3)
    fig.suptitle("Top 6 hybrid configs from 5D search "
                 "(XFRMR/COUPLER positions + lengths varied)",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "user_design_space_top.png", dpi=110)
    plt.close(fig)
    print(f"\nPNG: {OUT / 'user_design_space_top.png'}")
    print(f"CSV: {OUT / 'user_design_space_search.csv'}")


if __name__ == "__main__":
    main()
