"""v2 Auto-Learn page — closed-loop self-learning wideband matcher in the UI.

Runs the same engine as `auto_learn_run.py` (hyagi.auto_learn.run_learning):
warm-starts from the best matching past run in auto7_history.db, drives the
WORST in-band SWR down with the coordinate-descent matcher, recovers gain/F-B,
and saves every run back to the DB so the next run starts smarter.
"""
import json
import pathlib
import sys
import datetime

import streamlit as st

st.set_page_config(page_title="Auto-Learn", layout="wide")
st.title("Auto-Learn  ·  wideband self-tuner")
st.caption("Closed-loop: warm-start from past best → minimise worst in-band SWR "
           "→ recover gain/F-B → save to history. Built to hold a tight wideband "
           "SWR target across the whole band.")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
GEO_PATH = ROOT / "data/current_geometry_v2.json"
RULES_PATH = ROOT / "data/rules_v2.json"
MINI_PATH = ROOT / "data/mini_tunes_v2.json"
PROC_PATH = ROOT / "data/procedures_v2.json"

sys.path.insert(0, str(ROOT))
from hyagi import v2_runner  # noqa: E402
from hyagi.auto_learn import LearnConfig, run_learning  # noqa: E402


@st.cache_data(ttl=2)
def _load(p):
    return json.loads(pathlib.Path(p).read_text())


geo = _load(str(GEO_PATH))
rules = _load(str(RULES_PATH))
minis = _load(str(MINI_PATH))
procs = _load(str(PROC_PATH))

glb = rules["global"]

c1, c2 = st.columns(2)
with c1:
    band_low = st.number_input("Band low (MHz)", value=float(glb.get("freq_mhz_low", 26.965)),
                               step=0.005, format="%.3f", key="al_low",
                               help="Lower band edge to hold SWR across (e.g. 26.665 freeband)")
    band_high = st.number_input("Band high (MHz)", value=float(glb.get("freq_mhz_high", 27.405)),
                                step=0.005, format="%.3f", key="al_high")
    target_swr = st.number_input("Target max SWR", value=1.20, min_value=1.01, max_value=3.0,
                                 step=0.01, format="%.2f", key="al_target")
with c2:
    height_ft = st.number_input("Height (ft)", value=30.0, step=1.0, key="al_height")
    band_points = st.slider("Band sweep points", 9, 41, 21, key="al_points",
                            help="More points = stricter wideband check (slower)")
    restarts = st.slider("Search restarts (escape local minima)", 0, 4, 1, key="al_restarts")

polish = st.checkbox("Recover gain / F-B after hitting SWR target", value=True, key="al_polish")

st.markdown("**Starting geometry (current)**")
gcols = st.columns(min(4, len(geo["elements"])) or 1)
for i, e in enumerate(geo["elements"]):
    with gcols[i % len(gcols)]:
        st.caption(f"`{e['name']}`  pos={float(e['position_in']):.1f}  len={float(e['length_in']):.1f}")

st.markdown("---")
if st.button("AUTO-LEARN", type="primary", use_container_width=True, key="al_run"):
    if band_high <= band_low:
        st.error("Band high must be greater than band low.")
        st.stop()
    rules_run = json.loads(json.dumps(rules))
    rules_run["global"]["freq_mhz_low"] = float(band_low)
    rules_run["global"]["freq_mhz_high"] = float(band_high)

    log_box = st.empty()
    log_lines = []

    def log(msg):
        log_lines.append(str(msg))
        log_box.code("\n".join(log_lines[-60:]), language="text")

    procedure = procs[0] if procs else {"name": "matcher", "steps": []}
    cfg = LearnConfig(
        project_name="current_geometry",
        height_ft=float(height_ft),
        swr_profile="wideband_1.2",
        target_max_swr=float(target_swr),
        band_sweep_points=int(band_points),
        max_generations=int(restarts) + 1,
        use_matcher=True,
        polish_gain=bool(polish),
    )
    started = datetime.datetime.now()
    with st.spinner("Self-learning… (one NEC2 solve per candidate; this can take a few minutes)"):
        result = run_learning(geo["elements"], rules_run, minis, procedure, cfg, log_fn=log)
    elapsed = (datetime.datetime.now() - started).total_seconds()

    m = result["final_metrics"]
    band_max = m.get("band_max_swr", m.get("max_swr", 0))
    st.session_state["al_result"] = {
        "geometry": result["final_geometry"],
        "band_max": band_max,
        "gain": m.get("gain_dbi", 0),
        "fb": m.get("fb_db", 0),
        "score": result["final_score"],
        "low": float(band_low), "high": float(band_high), "points": int(band_points),
        "height": float(height_ft), "elapsed": elapsed,
    }

res = st.session_state.get("al_result")
if res:
    st.markdown("### Result")
    ok = res["band_max"] <= float(target_swr) + 1e-6
    (st.success if ok else st.warning)(
        f"band-max SWR {res['band_max']:.3f}  ·  gain {res['gain']:.2f} dBi  ·  "
        f"F/B {res['fb']:.2f} dB  ·  score {res['score']:+.1f}  ·  {res['elapsed']:.0f}s"
    )

    curve, _mx, _av = v2_runner.band_swr_curve(
        res["geometry"], res["low"], res["high"], res["points"], res["height"])
    if curve:
        import pandas as pd
        df = pd.DataFrame({"freq_MHz": [c[0] for c in curve], "SWR": [c[3] for c in curve]})
        st.line_chart(df, x="freq_MHz", y="SWR", height=240)

    st.markdown("**Tuned geometry**")
    for e in res["geometry"]:
        st.caption(f"  {e['name']:<8} pos={float(e['position_in']):7.2f} in  len={float(e['length_in']):7.2f} in")

    a1, a2 = st.columns(2)
    with a1:
        if st.button("Adopt tuned geometry as current", use_container_width=True, key="al_adopt"):
            GEO_PATH.write_text(json.dumps({"elements": res["geometry"]}, indent=2))
            st.cache_data.clear()
            st.success("Current geometry updated.")
    with a2:
        st.download_button("Download geometry JSON",
                           data=json.dumps({"elements": res["geometry"]}, indent=2),
                           file_name="auto_learn_geometry.json",
                           use_container_width=True, key="al_dl")
