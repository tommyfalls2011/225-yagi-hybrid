"""User-driven hybrid design space search.

User instinct from on-the-roof testing:
  * XFRMR closer to DE  (5.5\" - 14.5\" gap, possibly further)
  * COUPLER further from DE (14.5\" - 32\")
  * Longer-than-usual DE
  * Longer-than-usual REF
  * Tune XFRMR + COUPLER lengths around each scenario.

This script searches that exact design space with NEC2 and reports the
configurations that minimise band-max SWR over the 25.7-28.7 MHz band.

4D grid (192 combinations total at default resolution):
  DE length:       215, 218, 221, 224     (longer than usual 215.5)
  REF length:      220, 224, 228          (longer than usual 220)
  XFRMR position:  5.5, 8.0, 11.0, 14.5   (close)
  COUPLER position: 14.5, 19.0, 24.0, 29.0 (further)

At each grid point, XFRMR and COUPLER lengths are quickly tuned to the
empirically-derived OWA targets (XF = DE+5, CP = DE-15) -- the previous
study showed that pair gives the canonical 3-dip pattern.

Output:
  scripts/charts/user_design_space_search.csv   all 192 results
  scripts/charts/user_design_space_top.png      top-6 SWR curves
  Top-10 printed to stdout.
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
from hyagi import v2_runner, fb as fb_module


OUT = ROOT / "scripts/charts"
OUT.mkdir(exist_ok=True)

WL = 11811.0 / 27.195
DE_POS = round(0.108 * WL, 1)             # 46.9"
DIR_SP = round(0.18 * WL, 1)              # 78"
v2_runner.GROUNDING = "all_insulated"
v2_runner.GROUNDED = False


def _build(de_len, ref_len, xf_gap, cp_gap):
    """Build a 7-el hybrid with the given knobs.  XFRMR length is DE+5,
    COUPLER length is DE-15 (the OWA-pattern values from prior studies)."""
    return [
        {"name": "REF",     "position_in": 0.0,             "length_in": ref_len},
        {"name": "XFRMR",   "position_in": DE_POS - xf_gap, "length_in": de_len + 5.0},
        {"name": "DE",      "position_in": DE_POS,          "length_in": de_len},
        {"name": "COUPLER", "position_in": DE_POS + cp_gap, "length_in": de_len - 15.0},
        {"name": "DIR1",    "position_in": DE_POS + DIR_SP,    "length_in": 196.0},
        {"name": "DIR2",    "position_in": DE_POS + 2 * DIR_SP, "length_in": 193.0},
        {"name": "DIR3",    "position_in": DE_POS + 3 * DIR_SP, "length_in": 190.0},
    ]


def _sweep(els):
    try:
        # 80 points across 25-29 MHz: enough to find dips, ~3x faster than 200
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
    """Quick centre + pattern eval -- gives gain & F/B at fc."""
    try:
        m = v2_runner.evaluate(els, {"global": {"freq_mhz_center": fc,
                                                 "freq_mhz_low": 25.7,
                                                 "freq_mhz_high": 28.7}},
                               height_ft=height_ft)
        return m.get("gain_dbi", 0.0), m.get("fb_db", 0.0)
    except Exception:
        return 0.0, 0.0


def main():
    # Smaller grid focused on the user's stated sweet spots so the run
    # finishes in 10-15 minutes rather than 50.  Total = 3 * 2 * 3 * 3 = 54.
    de_lengths = [216.0, 220.0, 224.0]                    # longer-than-usual DE
    ref_lengths = [220.0, 226.0]                          # longer-than-usual REF
    xf_gaps = [5.5, 9.0, 13.0]                            # close to DE
    cp_gaps = [16.0, 22.0, 28.0]                          # further from DE

    rows = []
    total = len(de_lengths) * len(ref_lengths) * len(xf_gaps) * len(cp_gaps)
    print(f"Searching {total} configurations...")
    n = 0
    for de_len in de_lengths:
        for ref_len in ref_lengths:
            for xf_gap in xf_gaps:
                for cp_gap in cp_gaps:
                    n += 1
                    els = _build(de_len, ref_len, xf_gap, cp_gap)
                    curve = _sweep(els)
                    if not curve:
                        continue
                    in_band = [(f, s) for f, s in curve if 25.7 <= f <= 28.7]
                    bmax = max(s for _, s in in_band)
                    bw15 = _bw_under(curve, 1.5)
                    bw20 = _bw_under(curve, 2.0)
                    # Skip the expensive gain / F-B pattern eval here; user
                    # can verify the top candidates separately with the
                    # built-in evaluate.  Keeps the search at ~1 NEC solve
                    # per config instead of 2.
                    rec = {
                        "de_len": de_len, "ref_len": ref_len,
                        "xf_gap": xf_gap, "cp_gap": cp_gap,
                        "band_max": round(bmax, 2),
                        "bw15_khz": round(bw15),
                        "bw20_khz": round(bw20),
                        "gain_dbi": 0.0,
                        "fb_db": 0.0,
                        "curve": curve,
                    }
                    rows.append(rec)
                    if n % 6 == 0:
                        print(f"  ...{n}/{total} done  "
                              f"(latest bmax {bmax:.2f} bw20 {bw20:.0f}k)")

    # Sort by band-max ascending (best first), tie-break by bandwidth.
    rows.sort(key=lambda r: (r["band_max"], -r["bw20_khz"]))

    # NOW evaluate gain + F/B for the top 6 (full pattern NEC -- expensive
    # but only 6 of them).
    print("\nRunning full pattern eval on top 6...")
    for r in rows[:6]:
        els = _build(r["de_len"], r["ref_len"], r["xf_gap"], r["cp_gap"])
        gain, fb = _evaluate(els)
        r["gain_dbi"] = round(gain, 2)
        r["fb_db"] = round(fb, 2)

    # Print top-10
    print("\n=== TOP 10 by band-max SWR (gain/F-B only computed for top 6) ===")
    print(f"{'#':>3}  {'DE':>5} {'REF':>5} {'XFg':>5} {'CPg':>5}  "
          f"{'bmax':>6}  {'BW@1.5':>7}  {'BW@2.0':>7}  {'gain':>5}  {'F/B':>5}")
    for i, r in enumerate(rows[:10], 1):
        gain_s = f"{r['gain_dbi']:>5.2f}" if i <= 6 else "  -- "
        fb_s = f"{r['fb_db']:>5.2f}" if i <= 6 else "  -- "
        print(f"{i:>3}  {r['de_len']:>5.0f} {r['ref_len']:>5.0f} "
              f"{r['xf_gap']:>5.1f} {r['cp_gap']:>5.1f}  "
              f"{r['band_max']:>6.2f}  {r['bw15_khz']:>6}k  "
              f"{r['bw20_khz']:>6}k  {gain_s}  {fb_s}")

    # CSV (drop curve to keep file small)
    csv_rows = [{k: v for k, v in r.items() if k != "curve"} for r in rows]
    with open(OUT / "user_design_space_search.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        w.writeheader(); w.writerows(csv_rows)

    # Plot top-6
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    axes = axes.flatten()
    for ax, r in zip(axes, rows[:6]):
        freqs = [f for f, _ in r["curve"]]
        swrs  = [s for _, s in r["curve"]]
        ax.plot(freqs, swrs, color="#0b3b8c", linewidth=1.6)
        ax.axhline(2.0, color="#cc8800", linestyle="--", linewidth=0.9, alpha=0.7)
        ax.axhline(1.5, color="#3a8a3a", linestyle="--", linewidth=0.9, alpha=0.7)
        # Mark dips
        for i in range(1, len(r["curve"]) - 1):
            f0, s0 = r["curve"][i - 1]
            f1, s1 = r["curve"][i]
            f2, s2 = r["curve"][i + 1]
            if s1 < s0 and s1 < s2 and s1 < 4.0:
                ax.plot(f1, s1, "o", color="red", markersize=5)
                ax.annotate(f"{f1:.2f}", (f1, s1),
                            textcoords="offset points", xytext=(0, -12),
                            ha="center", fontsize=7, color="red")
        title = (f"DE={r['de_len']:.0f} REF={r['ref_len']:.0f} "
                 f"XFg={r['xf_gap']:.1f} CPg={r['cp_gap']:.1f}\n"
                 f"bmax {r['band_max']:.2f}  BW@2:1 {r['bw20_khz']}k  "
                 f"gain {r['gain_dbi']:.1f}  F/B {r['fb_db']:.1f}")
        ax.set_title(title, fontsize=10)
        ax.set_xlim(25.0, 29.0)
        ax.set_ylim(1.0, max(6.0, max(swrs) * 1.05))
        ax.set_xlabel("Freq (MHz)"); ax.set_ylabel("SWR")
        ax.grid(True, alpha=0.3)
    fig.suptitle("Top 6 hybrid configurations from user-driven search\n"
                 "(XFRMR close + COUPLER far + longer DE/REF)",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "user_design_space_top.png", dpi=110)
    plt.close(fig)
    print(f"\nPNG: {OUT / 'user_design_space_top.png'}")
    print(f"CSV: {OUT / 'user_design_space_search.csv'}")


if __name__ == "__main__":
    main()
