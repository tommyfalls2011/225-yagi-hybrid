"""Exploratory NEC2 study: how XFRMR/COUPLER lengths and positions affect
the SWR-dip pattern of a 7-element hybrid Yagi at 27.195 MHz.

Goal: STOP asking the user and START learning from the physics.  Build a
baseline 1/4-wavelength-positioned 7-element hybrid, sweep one variable at
a time, log where the SWR dips land in the 25-29 MHz range, and report.

Outputs:
  /tmp/hybrid_study/<variable>.csv   one row per probe with dip locations
  /tmp/hybrid_study/summary.md        human-readable findings

Sweeps run:
  1. XFRMR length: DE-15 to DE+10  (does XFRMR>DE ever help? answers #1/#2)
  2. COUPLER length: DE-25 to DE+5
  3. REF length: 215 to 230" (does REF skirt provide the low dip?)
  4. DIR1 length: 180 to 210" (does DIR1 contribute another dip?)
"""
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path("/app/src_repo/hybrid_auto7")
sys.path.insert(0, str(ROOT))
from hyagi import v2_runner


OUT = pathlib.Path("/tmp/hybrid_study")
OUT.mkdir(parents=True, exist_ok=True)

# Baseline 7-el hybrid at 27.195 MHz, ~1/4 wavelength director spacing.
# Wavelength = 11811/27.195 = 434.4".  1/4 wave = 108.6".  But for typical
# 7-el spacing we use ~0.18-0.20 lambda (78-87"); 1/4 wave between every
# director is unconventional but the user asked for it.  Sticking with
# their request: REF at 0, DE at 0.108*lambda, directors at 0.25*lambda
# spacings.
WL = 11811.0 / 27.195
DE_POS = round(0.108 * WL, 1)         # ~46.9"
DIR_SP = round(0.25 * WL, 1)          # ~108.6"
BASE = [
    {"name": "REF",     "position_in": 0.0,             "length_in": 220.0},
    {"name": "XFRMR",   "position_in": DE_POS - 18.5,   "length_in": 199.0},
    {"name": "DE",      "position_in": DE_POS,          "length_in": 215.5},
    {"name": "COUPLER", "position_in": DE_POS + 20.0,   "length_in": 198.0},
    {"name": "DIR1",    "position_in": DE_POS + DIR_SP, "length_in": 196.0},
    {"name": "DIR2",    "position_in": DE_POS + 2 * DIR_SP, "length_in": 193.0},
    {"name": "DIR3",    "position_in": DE_POS + 3 * DIR_SP, "length_in": 190.0},
]

# Force a uniform thin-wire taper so the sweeps stay comparable and fast.
v2_runner.GROUNDING = "all_insulated"
v2_runner.GROUNDED = False
v2_runner.BOOM_DIAMETER_IN = 1.5


def sweep_curve(els, f_low=25.5, f_high=29.0, points=72):
    """Return [(freq, swr), ...] over the band."""
    curve, _mx, _av = v2_runner.band_swr_curve(els, f_low, f_high, points, 26.0)
    return [(c[0], c[3]) for c in curve]


def find_dips(curve, max_swr=4.0):
    """Return list of (freq, swr) where SWR is a LOCAL MINIMUM under max_swr.
    These are the resonance dips that make the hybrid wideband."""
    dips = []
    for i in range(1, len(curve) - 1):
        f0, s0 = curve[i - 1]
        f1, s1 = curve[i]
        f2, s2 = curve[i + 1]
        if s1 < s0 and s1 < s2 and s1 < max_swr:
            dips.append((f1, s1))
    return dips


def run_sweep(name, baseline_field, values, label):
    """Sweep `values` over the element named `name`, field `baseline_field`.
    Logs every result + dip set."""
    rows = []
    print(f"\n=== Sweeping {name}.{baseline_field} = {values[0]}..{values[-1]} ===")
    for v in values:
        els = json.loads(json.dumps(BASE))
        for e in els:
            if e["name"] == name:
                e[baseline_field] = float(v)
                break
        curve = sweep_curve(els)
        dips = find_dips(curve)
        band_max = max(s for _, s in curve)
        band_min = min(s for _, s in curve)
        row = {
            f"{name}_{baseline_field}": v,
            "n_dips": len(dips),
            "band_max_swr": round(band_max, 3),
            "band_min_swr": round(band_min, 3),
            "dip_freqs": ";".join(f"{f:.3f}" for f, _ in dips),
            "dip_swrs":  ";".join(f"{s:.3f}" for _, s in dips),
        }
        rows.append(row)
        if dips:
            dip_str = ", ".join(f"{f:.2f}/{s:.2f}" for f, s in dips)
            print(f"  {v:8.2f}  band[{band_min:.2f}-{band_max:.2f}]  "
                  f"dips: {dip_str}")
        else:
            print(f"  {v:8.2f}  band[{band_min:.2f}-{band_max:.2f}]  no dips")
    # CSV out
    path = OUT / f"{label}.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    print("Baseline geometry:")
    for e in BASE:
        print(f"  {e['name']:8} pos={e['position_in']:7.2f}  len={e['length_in']:7.2f}")
    print()
    base_curve = sweep_curve(BASE)
    base_dips = find_dips(base_curve)
    print("Baseline curve summary:")
    print(f"  band-max SWR: {max(s for _,s in base_curve):.3f}")
    print(f"  band-min SWR: {min(s for _,s in base_curve):.3f}")
    print(f"  dips: {[(round(f,2), round(s,2)) for f,s in base_dips]}")

    de_len = next(e["length_in"] for e in BASE if e["name"] == "DE")

    summary = []
    summary.append("# 7-el Hybrid Yagi physics study (NEC2 sweep)\n")
    summary.append(f"## Baseline geometry @ 27.195 MHz, 26 ft, insulated\n")
    summary.append(f"- WL = {WL:.2f}\"\n- DE position = {DE_POS:.1f}\"\n")
    summary.append(f"- DE length = {de_len}\"\n- Director spacing = "
                   f"{DIR_SP:.1f}\" (1/4 wave)\n\n")
    summary.append(f"### Baseline curve\n")
    summary.append(f"- band-max SWR (25.5-29): "
                   f"{max(s for _,s in base_curve):.2f}\n")
    summary.append(f"- band-min SWR: {min(s for _,s in base_curve):.2f}\n")
    summary.append(f"- dips: {[(round(f,2), round(s,2)) for f,s in base_dips]}"
                   "\n\n")

    # 1. XFRMR length sweep (DE-15 to DE+10) -- does XFRMR > DE help?
    xf_values = [de_len + d for d in
                 (-15, -12, -10, -8, -6, -4, -2, 0, +2, +5, +8, +10)]
    xf_rows = run_sweep("XFRMR", "length_in", xf_values, "xfrmr_length_sweep")

    # 2. COUPLER length sweep
    cp_values = [de_len + d for d in
                 (-25, -22, -19, -16, -13, -10, -7, -4, 0, +3, +5)]
    cp_rows = run_sweep("COUPLER", "length_in", cp_values, "coupler_length_sweep")

    # 3. REF length sweep
    ref_values = [215, 218, 220, 222, 224, 226, 228, 230]
    ref_rows = run_sweep("REF", "length_in", ref_values, "ref_length_sweep")

    # 4. DIR1 length sweep
    d1_values = [180, 185, 190, 193, 196, 199, 202, 205, 210]
    d1_rows = run_sweep("DIR1", "length_in", d1_values, "dir1_length_sweep")

    # ---- Summary findings -------------------------------------------------
    def fmt_rows(rows, label_col):
        out = [f"\n### {label_col} sweep\n",
               f"| {label_col} | n_dips | band-max | dip_freqs |\n",
               "|---|---|---|---|\n"]
        for r in rows:
            out.append(f"| {r[label_col]:.2f} | {r['n_dips']} | "
                       f"{r['band_max_swr']:.2f} | {r['dip_freqs'] or '—'} |\n")
        return "".join(out)

    summary.append(fmt_rows(xf_rows, "XFRMR_length_in"))
    summary.append(fmt_rows(cp_rows, "COUPLER_length_in"))
    summary.append(fmt_rows(ref_rows, "REF_length_in"))
    summary.append(fmt_rows(d1_rows, "DIR1_length_in"))

    # Findings - what configurations gave the most dips?
    summary.append("\n## Key findings\n\n")
    summary.append("### Multi-dip configurations\n")
    summary.append("Geometries that produced **>= 2 distinct SWR dips under "
                   "4:1** in the 25.5-29 MHz band:\n\n")
    multi = []
    for rows, lbl in [(xf_rows, "XFRMR"), (cp_rows, "COUPLER"),
                      (ref_rows, "REF"), (d1_rows, "DIR1")]:
        for r in rows:
            if r["n_dips"] >= 2:
                multi.append((lbl, r))
    if multi:
        for lbl, r in multi:
            field_key = next(k for k in r if k.endswith("_in"))
            summary.append(f"- {lbl} = {r[field_key]}\" -> "
                           f"dips at {r['dip_freqs']} (SWRs {r['dip_swrs']})\n")
    else:
        summary.append("(none observed in this single-variable sweep -- the "
                       "multi-dip pattern needs SIMULTANEOUS tuning of "
                       "XFRMR+COUPLER+DE.)\n")

    summary.append("\n### XFRMR vs DE constraint check\n")
    summary.append(f"Baseline DE = {de_len}\".\n")
    summary.append("Looking for XFRMR length values where XFRMR > DE that "
                   "still produce a usable (< 3:1) band-min SWR:\n\n")
    for r in xf_rows:
        if r["XFRMR_length_in"] > de_len and r["band_min_swr"] < 3.0:
            summary.append(f"- XFRMR = {r['XFRMR_length_in']:.1f}\" "
                           f"(DE+{r['XFRMR_length_in']-de_len:.1f}\"): "
                           f"band-min {r['band_min_swr']:.2f}, "
                           f"dips at {r['dip_freqs'] or '(none)'}\n")
    summary.append("\n")

    path = OUT / "summary.md"
    path.write_text("".join(summary))
    print(f"\n--- Summary written to {path} ---")
    print()
    print("".join(summary[-30:]))  # tail of summary

if __name__ == "__main__":
    main()
