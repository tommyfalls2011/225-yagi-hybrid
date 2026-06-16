"""Merge the 3 dips into ONE wide curve: find the spacing sweet spot.

Question: with XFRMR length and COUPLER length fixed at the "good
frequency" positions, what spacing makes the anti-resonance peaks
BETWEEN the dips collapse so the band-max stays low?

Hypothesis: coupling strength (spacing) trades resonance depth for
inter-resonance ripple.  Closer = stronger coupling = flatter band.
Need to find the spacing where band-max is minimised.

Two sweeps:
  A) XFRMR position varied 15..40", COUPLER position fixed at +20".
  B) COUPLER position varied 14..40", XFRMR position fixed at 28.4".

Both with XFRMR length 222" (low dip ~26.1) and COUPLER length 198"
(high dip ~28.5).
"""
import json
import pathlib
import sys

ROOT = pathlib.Path("/app/src_repo/hybrid_auto7")
sys.path.insert(0, str(ROOT))
from hyagi import v2_runner


WL = 11811.0 / 27.195
DE_POS = round(0.108 * WL, 1)             # ~46.9"
DIR_SP = round(0.18 * WL, 1)              # ~78"
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


def sweep_curve(els, lo=25.5, hi=29.0):
    try:
        c, _mx, _av = v2_runner.band_swr_curve(els, lo, hi, 72, 26.0)
        return [(x[0], x[3]) for x in c]
    except Exception:
        return []


def dips(curve, max_swr=8.0):
    return [(round(curve[i][0], 3), round(curve[i][1], 3))
            for i in range(1, len(curve) - 1)
            if curve[i][1] < curve[i - 1][1] and curve[i][1] < curve[i + 1][1]
            and curve[i][1] < max_swr]


def vary(name, field, values, label):
    print(f"\n=== {label} ===")
    print(f"{'value':>7}  {'band-min':>9}  {'band-max':>9}  "
          f"{'n_dips':>6}  dips")
    best_max = 999
    best_v = None
    for v in values:
        els = json.loads(json.dumps(BASE))
        for e in els:
            if e["name"] == name:
                e[field] = float(v)
        curve = sweep_curve(els)
        if not curve:
            continue
        # Compute IN-BAND max (25.7 - 28.7) -- that's what the user cares about,
        # not the full sweep including the wings outside the band.
        in_band = [s for f, s in curve if 25.7 <= f <= 28.7]
        if not in_band:
            continue
        bmax = max(in_band)
        bmin = min(in_band)
        d = dips(curve)
        if bmax < best_max:
            best_max, best_v = bmax, v
        print(f"{v:>7.2f}  {bmin:>9.3f}  {bmax:>9.3f}  {len(d):>6}  "
              f"{d}")
    print(f"  BEST in-band max: {best_max:.3f} at {field}={best_v}")
    return best_v, best_max


def main():
    print(f"Baseline: XFRMR_pos={BASE[1]['position_in']}, "
          f"COUPLER_pos={BASE[3]['position_in']}, "
          f"XFRMR_len={BASE[1]['length_in']}, COUPLER_len={BASE[3]['length_in']}")

    # Sweep A: XFRMR position
    xf_positions = [DE_POS - d for d in (32, 28, 24, 22, 20, 18.5, 16, 13, 10)]
    vary("XFRMR", "position_in", xf_positions, "Sweep A: XFRMR position (XFRMR len fixed at 222\")")

    # Sweep B: COUPLER position
    cp_positions = [DE_POS + d for d in (10, 13, 16, 18, 20, 22, 25, 28, 32, 40)]
    vary("COUPLER", "position_in", cp_positions, "Sweep B: COUPLER position (COUPLER len fixed at 198\")")

    # Sweep C: both moved together (symmetric "tighten the cell" test)
    print("\n=== Sweep C: cell symmetric tighten (XFRMR&COUPLER each X\" from DE) ===")
    print(f"{'gap':>7}  {'band-min':>9}  {'band-max':>9}  dips")
    for gap in (10, 13, 16, 18, 20, 22, 25, 28, 32):
        els = json.loads(json.dumps(BASE))
        for e in els:
            if e["name"] == "XFRMR":
                e["position_in"] = DE_POS - gap
            elif e["name"] == "COUPLER":
                e["position_in"] = DE_POS + gap
        curve = sweep_curve(els)
        if not curve:
            continue
        in_band = [s for f, s in curve if 25.7 <= f <= 28.7]
        bmax = max(in_band) if in_band else 99
        bmin = min(in_band) if in_band else 99
        d = dips(curve)
        print(f"{gap:>7.1f}  {bmin:>9.3f}  {bmax:>9.3f}  {d}")


if __name__ == "__main__":
    main()
