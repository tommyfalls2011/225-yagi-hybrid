"""Length vs Spacing: which parameter actually moves the SWR-dip frequencies?

Two sweeps:
  1. Hold XFRMR position fixed, sweep its LENGTH only.  Watch dip freqs.
  2. Hold XFRMR length fixed, sweep its POSITION (boom distance from DE).
     Watch dip freqs.
Same setup for COUPLER.  4 sweeps total.

If LENGTH moves dip freqs and SPACING doesn't, theory wins:
  length = resonance frequency of the parasitic itself
  spacing = mutual coupling = depth of the dip, not its frequency

This study answers the user's question directly with NEC2 data instead of
me handwaving with antenna textbook quotes.
"""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path("/app/src_repo/hybrid_auto7")
sys.path.insert(0, str(ROOT))
from hyagi import v2_runner


OUT = pathlib.Path("/tmp/length_vs_spacing")
OUT.mkdir(exist_ok=True)

# Same 7-el baseline as the first physics study, but with the NEW
# asymmetric stagger (XFRMR > DE).
WL = 11811.0 / 27.195
DE_POS = round(0.108 * WL, 1)             # ~46.9"
# Use realistic 0.18 wavelength director spacing (typical commercial Yagi)
# rather than the 0.25 wavelength the original prompt requested.  0.25
# stretches the boom to 32+ ft and NEC2's default segment count gives
# noisy results.  0.18 = ~78" between directors, total boom ~28 ft.
DIR_SP = round(0.18 * WL, 1)              # ~78"
BASE = [
    {"name": "REF",     "position_in": 0.0,            "length_in": 220.0},
    {"name": "XFRMR",   "position_in": DE_POS - 18.5,  "length_in": 222.5},   # DE+7
    {"name": "DE",      "position_in": DE_POS,         "length_in": 215.5},
    {"name": "COUPLER", "position_in": DE_POS + 20.0,  "length_in": 200.5},   # DE-15
    {"name": "DIR1",    "position_in": DE_POS + DIR_SP,    "length_in": 196.0},
    {"name": "DIR2",    "position_in": DE_POS + 2 * DIR_SP, "length_in": 193.0},
    {"name": "DIR3",    "position_in": DE_POS + 3 * DIR_SP, "length_in": 190.0},
]
v2_runner.GROUNDING = "all_insulated"
v2_runner.GROUNDED = False


def sweep_curve(els):
    try:
        curve, _mx, _av = v2_runner.band_swr_curve(els, 25.5, 29.0, 72, 26.0)
        return [(c[0], c[3]) for c in curve]
    except Exception as ex:
        print(f"  [nec2c error: {ex}]")
        return []


def find_dips(curve, max_swr=8.0):
    dips = []
    if not curve:
        return dips
    for i in range(1, len(curve) - 1):
        f0, s0 = curve[i - 1]
        f1, s1 = curve[i]
        f2, s2 = curve[i + 1]
        if s1 < s0 and s1 < s2 and s1 < max_swr:
            dips.append((round(f1, 3), round(s1, 3)))
    return dips


def vary(name, field, values):
    rows = []
    print(f"\n=== {name}.{field} sweep ===")
    for v in values:
        els = json.loads(json.dumps(BASE))
        for e in els:
            if e["name"] == name:
                e[field] = float(v)
        curve = sweep_curve(els)
        if not curve:
            rows.append({f"{name}_{field}": v, "n_dips": 0,
                         "band_min": 99.0, "band_max": 99.0,
                         "dip_freqs": "(nec error)", "dip_swrs": ""})
            continue
        dips = find_dips(curve)
        band_max = max(s for _, s in curve)
        band_min = min(s for _, s in curve)
        row = {f"{name}_{field}": v, "n_dips": len(dips),
               "band_min": round(band_min, 3),
               "band_max": round(band_max, 3),
               "dip_freqs": ";".join(f"{f:.3f}" for f, _ in dips),
               "dip_swrs":  ";".join(f"{s:.3f}" for _, s in dips)}
        rows.append(row)
        print(f"  {v:>7.2f}  band[{band_min:.2f}-{band_max:.2f}]  "
              f"dips: {dips}")
    path = OUT / f"{name}_{field}.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    print("Baseline (XFRMR=222.5=DE+7, COUPLER=200.5=DE-15):")
    base_curve = sweep_curve(BASE)
    base_dips = find_dips(base_curve)
    print(f"  dips: {base_dips}")

    # XFRMR length sweep (position held at 28.4")
    xf_len = vary("XFRMR", "length_in",
                  [215.0, 218.0, 220.0, 222.5, 225.0, 227.0, 230.0])

    # XFRMR position sweep (length held at 222.5")
    xf_pos = vary("XFRMR", "position_in",
                  [DE_POS - 26.0, DE_POS - 22.0, DE_POS - 18.5,
                   DE_POS - 14.0, DE_POS - 10.0, DE_POS - 6.0])

    # COUPLER length sweep (position held at +20")
    cp_len = vary("COUPLER", "length_in",
                  [196.0, 198.0, 200.5, 203.0, 206.0, 209.0, 212.0])

    # COUPLER position sweep (length held at 200.5")
    cp_pos = vary("COUPLER", "position_in",
                  [DE_POS + 14.0, DE_POS + 17.0, DE_POS + 20.0,
                   DE_POS + 25.0, DE_POS + 32.0, DE_POS + 42.0])

    # Summary
    summary = ["# Length vs Spacing study\n\n",
               "Question: which parameter (LENGTH or POSITION/SPACING) "
               "determines the SWR-dip frequencies?\n\n",
               f"Baseline geometry: DE={BASE[2]['length_in']}\", "
               f"XFRMR={BASE[1]['length_in']}\" "
               f"(DE+{BASE[1]['length_in']-BASE[2]['length_in']:+.1f}), "
               f"COUPLER={BASE[3]['length_in']}\" "
               f"(DE{BASE[3]['length_in']-BASE[2]['length_in']:+.1f})\n\n"]

    def fmt(rows, key, what):
        out = [f"\n### {what} ({key})\n",
               f"| {key} | n_dips | band[min..max] | dip frequencies |\n",
               "|---|---|---|---|\n"]
        for r in rows:
            out.append(f"| {r[key]:.2f} | {r['n_dips']} | "
                       f"{r['band_min']:.2f}..{r['band_max']:.2f} | "
                       f"{r['dip_freqs']} |\n")
        return "".join(out)

    summary.append(fmt(xf_len, "XFRMR_length_in", "XFRMR length sweep "
                       "(position fixed at 28.4\")"))
    summary.append(fmt(xf_pos, "XFRMR_position_in", "XFRMR position sweep "
                       "(length fixed at 222.5\")"))
    summary.append(fmt(cp_len, "COUPLER_length_in", "COUPLER length sweep "
                       "(position fixed at +20\")"))
    summary.append(fmt(cp_pos, "COUPLER_position_in", "COUPLER position sweep "
                       "(length fixed at 200.5\")"))

    path = OUT / "summary.md"
    path.write_text("".join(summary))
    print(f"\n--- Summary written to {path}")


if __name__ == "__main__":
    main()
