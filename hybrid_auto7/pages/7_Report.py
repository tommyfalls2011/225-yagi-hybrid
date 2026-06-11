"""Report — the bottom page. Full performance report and specs for the current
(best-tuned) antenna, laid out clean for the printer (use your browser's Print,
Ctrl/Cmd+P). Plus the cut lengths and .nec / .maa downloads to take to the bench.
"""
import json
import math
import pathlib
import sqlite3
import sys

import streamlit as st

st.set_page_config(page_title="Report", layout="wide")
st.title("Report  ·  best antenna specs")
st.caption("Everything about the current (adopted) antenna, ready to print "
           "(Ctrl/Cmd+P) or download. Tune on the Tune & Learn page, adopt the "
           "result, then read it here.")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
GEO_PATH = ROOT / "data/current_geometry_v2.json"
RULES_PATH = ROOT / "data/rules_v2.json"
SETUP_PATH = ROOT / "data/setup_v2.json"
DB_PATH = ROOT / "data/auto7_history.db"

sys.path.insert(0, str(ROOT))
from hyagi import v2_runner  # noqa: E402
from hyagi import perf_report  # noqa: E402
from hyagi import exporters  # noqa: E402


@st.cache_data(ttl=2)
def _load(p, fb):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return fb


geo = _load(str(GEO_PATH), {"elements": []})
rules = _load(str(RULES_PATH), {"global": {}})
setup = _load(str(SETUP_PATH), {})
glb = rules.get("global", {})

# Match the model to the saved construction options.
v2_runner.GROUNDED = (str(setup.get("grounding", "insulated")) == "grounded")
v2_runner.BOOM_DIAMETER_IN = float(setup.get("boom_diameter_in", 1.5))
height_ft = float(setup.get("height_ft", 30.0))

els = geo.get("elements", [])
if not els:
    st.info("No geometry yet — set it up on Antenna Setup and tune it first.")
    st.stop()

st.markdown(
    f"**Antenna:** {len(els)} elements · {height_ft:.0f} ft · "
    f"boom Ø {float(setup.get('boom_diameter_in', 1.5)):.2f}\" · "
    f"elements {str(setup.get('grounding', 'insulated')).upper()} · "
    f"taper `{v2_runner.taper_signature()}`"
)

if st.button("📊 Build / refresh full report", type="primary", key="rp_build"):
    with st.spinner("Solving over real ground + free space and sweeping SWR…"):
        st.session_state["rp_report"] = perf_report.analyze(
            els, rules, height_ft=height_ft)

rep = st.session_state.get("rp_report")
if not rep:
    st.info("Hit **Build / refresh full report** to compute the specs.")
    st.stop()
if "error" in rep:
    st.error(f"Report failed: {rep['error']}")
    st.stop()


def _bw(b):
    return f"{b[0]:.3f}–{b[1]:.3f} MHz  ({b[2]:.0f} kHz)" if b else "— (never ≤ this)"


k1, k2, k3, k4 = st.columns(4)
k1.metric("Forward gain", f"{rep['gain_dbi']:.2f} dBi", f"{rep['gain_dbd']:.2f} dBd")
k2.metric("Front / Back", f"{rep['fb_db']:.2f} dB", f"F/R {rep['fr_db']:.2f} dB")
k3.metric("Take-off angle", f"{rep['takeoff_deg']:.1f}°", f"@ {rep['height_ft']:.0f} ft")
k4.metric("Band-max SWR", f"{rep['band_max_swr']:.3f}",
          f"min {rep['min_swr']:.3f} @ {rep['min_swr_mhz']:.3f} MHz")

rows = [
    ("Gain over real ground", f"{rep['gain_dbi']:.2f} dBi  ({rep['gain_dbd']:.2f} dBd)"),
    ("Gain in free space", f"{rep['gain_free_space_dbi']:.2f} dBi"),
    ("Ground-reflection gain", f"+{rep['ground_gain_db']:.2f} dB"),
    ("Power multiplier", f"{rep['power_mult_isotropic']:.1f}× isotropic   ·   {rep['power_mult_dipole']:.2f}× a dipole"),
    ("Front-to-back / front-to-rear", f"{rep['fb_db']:.2f} dB  /  {rep['fr_db']:.2f} dB"),
    ("Azimuth beamwidth (−3 dB)", f"{rep['az_beamwidth_deg']}°" if rep['az_beamwidth_deg'] else "—"),
    ("Elevation beamwidth (−3 dB)", f"{rep['el_beamwidth_deg']}°" if rep['el_beamwidth_deg'] else "—"),
    ("Take-off (peak elevation) angle", f"{rep['takeoff_deg']:.1f}°"),
    ("Radiation efficiency", f"{rep['efficiency_pct']:.1f}%" if rep['efficiency_pct'] is not None else "—"),
    ("Antenna height / boom length", f"{rep['height_ft']:.0f} ft  /  {rep['boom_in']:.1f} in ({rep['boom_in']/12:.1f} ft)"),
    ("Resonant (min-SWR) freq", f"{rep['min_swr']:.3f}:1 @ {rep['min_swr_mhz']:.3f} MHz"),
    ("In-band max SWR", f"{rep['band_max_swr']:.3f}:1  ({rep['band_low_mhz']:.3f}–{rep['band_high_mhz']:.3f} MHz)"),
    ("Bandwidth ≤ 1.2:1", _bw(rep['bw_swr_1p2'])),
    ("Bandwidth ≤ 1.5:1", _bw(rep['bw_swr_1p5'])),
    ("Bandwidth ≤ 2.0:1", _bw(rep['bw_swr_2p0'])),
]
st.markdown("### Performance")
st.markdown("| Metric | Value |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows))

# ---- Cut sheet -------------------------------------------------------------
st.markdown("### Cut sheet (build numbers)")
taper = v2_runner.get_active_taper()
INCH = v2_runner.INCH
cut_rows = []
els_sorted = sorted(els, key=lambda e: float(e["position_in"]))
p0 = float(els_sorted[0]["position_in"])
for e in els_sorted:
    L = float(e["length_in"])
    half_m = (L * INCH) / 2.0
    secs = v2_runner._half_sections(half_m, taper) if taper else []
    sect_txt = " + ".join(f"{(r*2)/INCH:.3f}\"OD×{(ln/INCH):.1f}\"" for r, ln in secs)
    cut_rows.append((e["name"], f"{L:.2f}", f"{float(e['position_in']) - p0:.2f}", sect_txt or "uniform"))
st.markdown("| Element | Overall length (in) | Boom position (in) | Tubing (centre→tip, per half) |\n"
            "|---|---|---|---|\n" +
            "\n".join(f"| {n} | {L} | {pos} | {s} |" for n, L, pos, s in cut_rows))
st.caption("Tubing sections are per HALF element, from the centre out to the tip "
           "(mirror for the other half). Overlap/insertion not included.")

# ---- SWR curve -------------------------------------------------------------
f_low = float(glb.get("freq_mhz_low", 26.665))
f_high = float(glb.get("freq_mhz_high", 27.855))
curve, _mx, _av = v2_runner.band_swr_curve(els, f_low, f_high, 31, height_ft)
if curve:
    import pandas as pd
    st.markdown("### SWR across the band")
    st.line_chart(pd.DataFrame({"freq_MHz": [c[0] for c in curve], "SWR": [c[3] for c in curve]}),
                  x="freq_MHz", y="SWR", height=240)

# ---- Best run logged in the database --------------------------------------
st.markdown("### Best logged run (self-learning history)")
try:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT created_utc, max_swr, min_swr, center_swr, center_r, center_x, "
                      "center_rl_db FROM runs WHERE status='DONE' ORDER BY max_swr ASC, "
                      "avg_swr ASC LIMIT 1").fetchone()
    con.close()
    if row:
        st.caption(f"Best DB run: band-max SWR {row['max_swr']:.3f} · center "
                   f"R={row['center_r']:.1f}Ω X={row['center_x']:+.2f}Ω SWR "
                   f"{row['center_swr']:.3f} · return loss {row['center_rl_db']:.1f} dB "
                   f"· {row['created_utc']}")
    else:
        st.caption("No completed runs in the database yet.")
except Exception as e:
    st.caption(f"(history unavailable: {e})")

# ---- Downloads -------------------------------------------------------------
st.markdown("### Download")
rules_exp = json.loads(json.dumps(rules))
rules_exp["global"]["freq_mhz_low"] = f_low
rules_exp["global"]["freq_mhz_high"] = f_high
d1, d2, d3 = st.columns(3)
with d1:
    st.download_button("Report (JSON)", data=json.dumps(rep, indent=2),
                       file_name="antenna_report.json", use_container_width=True, key="rp_dl_json")
with d2:
    st.download_button(".nec (NEC-2)", data=exporters.to_nec(els, rules_exp, height_ft=height_ft),
                       file_name="antenna.nec", mime="text/plain",
                       use_container_width=True, key="rp_dl_nec")
with d3:
    st.download_button(".maa (MMANA-GAL)",
                       data=exporters.to_maa(els, rules_exp, height_ft=height_ft,
                                             center_mhz=float(glb.get("freq_mhz_center", 27.195))),
                       file_name="antenna.maa", mime="text/plain",
                       use_container_width=True, key="rp_dl_maa")
st.info("To print this page: use your browser's Print (Ctrl/Cmd+P) → Save as PDF "
        "or send to your printer.")
