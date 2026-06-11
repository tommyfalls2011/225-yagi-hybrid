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
from hyagi import perf_report  # noqa: E402
from hyagi import hybrid_seed  # noqa: E402
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
    tune_goal = st.selectbox(
        "Tune goal",
        ["wideband", "resonant"],
        format_func=lambda g: ("Wideband SWR (flattest across band)" if g == "wideband"
                               else "Resonant match — high power (R≈50, X≈0 at center)"),
        key="al_goal",
        help="Resonant drives reactance X→0 and R→50 at the center frequency for "
             "max return loss / safe high-power (50 kW+) operation; band edges may rise. "
             "Wideband holds the lowest worst-case SWR across the whole band.")
with c2:
    height_ft = st.number_input("Height (ft)", value=30.0, step=1.0, key="al_height")
    band_points = st.slider("Band sweep points", 9, 41, 21, key="al_points",
                            help="More points = stricter wideband check (slower)")
    restarts = st.slider("Search restarts (escape local minima)", 0, 4, 1, key="al_restarts")

polish = st.checkbox("Recover gain / F-B after hitting SWR target", value=True, key="al_polish")

# ---- Element taper (tubing schedule) --------------------------------------
TAPER_PATH = ROOT / "data/taper_v2.json"
with st.expander("⚙️ Element taper / tubing schedule (aluminum)", expanded=False):
    st.caption("One tube per line: `OD_inches, section_length_inches`, from element "
               "CENTRE out to the TIP. Use a big length (e.g. 999) for the piece that "
               "runs to the tip. Example for this antenna: `0.625, 36` then `0.5, 999`.")
    try:
        _cur_taper = json.loads(TAPER_PATH.read_text()).get("default", [[0.625, 36.0], [0.5, 999.0]])
    except Exception:
        _cur_taper = [[0.625, 36.0], [0.5, 999.0]]
    _taper_text = "\n".join(f"{od}, {L}" for od, L in _cur_taper)
    new_taper_text = st.text_area("Taper sections", value=_taper_text, key="al_taper", height=120)
    if st.button("Save taper", key="al_save_taper"):
        sched = []
        for ln in new_taper_text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            parts = [p for p in ln.replace("\t", ",").split(",") if p.strip()]
            if len(parts) >= 2:
                sched.append([float(parts[0]), float(parts[1])])
        if sched:
            TAPER_PATH.write_text(json.dumps({"default": sched}, indent=2))
            st.cache_data.clear()
            st.success(f"Saved taper: {sched}  (re-run AUTO-LEARN to re-tune for it)")
        else:
            st.error("Could not parse any 'OD, length' lines.")

st.markdown("**Starting geometry (current)**")
n_dirs_now = sum(1 for e in geo["elements"] if str(e["name"]).upper().startswith("DIR"))
with st.expander(f"🔧 Build geometry — element count (now: {len(geo['elements'])} total, {n_dirs_now} directors)", expanded=False):
    st.caption("A hybrid is always REF + XFRMR + DE + COUPLER, plus the directors "
               "you choose. 0–14 directors = 4–18 total elements. Building reseeds a "
               "fresh wavelength-scaled geometry; then run AUTO-LEARN to tune it.")
    n_dir = st.slider("Number of directors", 0, 14, n_dirs_now, key="al_ndir")
    st.caption(f"→ {n_dir + 4} total elements (REF, XFRMR, DE, COUPLER + {n_dir} directors)")
    if st.button("Build / reseed geometry", key="al_build"):
        new_geo = hybrid_seed.build_geometry(n_dir, center_mhz=float(glb.get("freq_mhz_center", 27.195)))
        GEO_PATH.write_text(json.dumps(new_geo, indent=2))
        st.cache_data.clear()
        st.success(f"Built {len(new_geo['elements'])}-element hybrid. Scroll down and hit AUTO-LEARN to tune it.")
        st.rerun()


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
        tune_goal=str(tune_goal),
    )
    started = datetime.datetime.now()
    with st.spinner("Self-learning… (one NEC2 solve per candidate; this can take a few minutes)"):
        result = run_learning(geo["elements"], rules_run, minis, procedure, cfg, log_fn=log)
    elapsed = (datetime.datetime.now() - started).total_seconds()

    m = result["final_metrics"]
    band_max = m.get("band_max_swr", m.get("max_swr", 0))
    csw = float(m.get("center_swr", 0.0))
    import math as _math
    crl = 99.0 if csw <= 1.0 else -20.0 * _math.log10((csw - 1.0) / (csw + 1.0))
    with st.spinner("Building full performance report…"):
        report = perf_report.analyze(result["final_geometry"], rules_run, height_ft=float(height_ft))
    st.session_state["al_result"] = {
        "geometry": result["final_geometry"],
        "band_max": band_max,
        "gain": m.get("gain_dbi", 0),
        "fb": m.get("fb_db", 0),
        "center_r": float(m.get("center_r", 0.0)),
        "center_x": float(m.get("center_x", 0.0)),
        "center_swr": csw,
        "center_rl": crl,
        "goal": str(tune_goal),
        "score": result["final_score"],
        "low": float(band_low), "high": float(band_high), "points": int(band_points),
        "height": float(height_ft), "elapsed": elapsed,
        "report": report,
    }

res = st.session_state.get("al_result")
if res:
    st.markdown("### Result")
    ok = res["band_max"] <= float(target_swr) + 1e-6
    (st.success if ok else st.warning)(
        f"band-max SWR {res['band_max']:.3f}  ·  gain {res['gain']:.2f} dBi  ·  "
        f"F/B {res['fb']:.2f} dB  ·  score {res['score']:+.1f}  ·  {res['elapsed']:.0f}s"
    )
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Center R", f"{res.get('center_r', 0):.1f} Ω")
    cc2.metric("Center X (reactance)", f"{res.get('center_x', 0):+.2f} Ω",
               help="For high power this must be ≈0")
    cc3.metric("Center SWR", f"{res.get('center_swr', 0):.3f}")
    cc4.metric("Return loss", f"{res.get('center_rl', 0):.1f} dB")

    curve, _mx, _av = v2_runner.band_swr_curve(
        res["geometry"], res["low"], res["high"], res["points"], res["height"])
    if curve:
        import pandas as pd
        df = pd.DataFrame({"freq_MHz": [c[0] for c in curve], "SWR": [c[3] for c in curve]})
        st.line_chart(df, x="freq_MHz", y="SWR", height=240)

    rep = res.get("report") or {}
    if rep and "error" not in rep:
        def _bw(b):
            return f"{b[0]:.3f}–{b[1]:.3f} MHz  ({b[2]:.0f} kHz)" if b else "— (never ≤ this)"
        st.markdown("### Full performance report")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Forward gain", f"{rep['gain_dbi']:.2f} dBi", f"{rep['gain_dbd']:.2f} dBd")
        k2.metric("Front / Back", f"{rep['fb_db']:.2f} dB", f"F/R {rep['fr_db']:.2f} dB")
        k3.metric("Take-off angle", f"{rep['takeoff_deg']:.1f}°", f"@ {rep['height_ft']:.0f} ft")
        k4.metric("Band-max SWR", f"{rep['band_max_swr']:.3f}", f"min {rep['min_swr']:.3f} @ {rep['min_swr_mhz']:.3f} MHz")

        rows = [
            ("Gain over real ground", f"{rep['gain_dbi']:.2f} dBi  ({rep['gain_dbd']:.2f} dBd)"),
            ("Gain in free space", f"{rep['gain_free_space_dbi']:.2f} dBi"),
            ("Ground-reflection gain", f"+{rep['ground_gain_db']:.2f} dB"),
            ("Power multiplier", f"{rep['power_mult_isotropic']:.1f}× isotropic   ·   {rep['power_mult_dipole']:.2f}× a dipole"),
            ("Front-to-back / front-to-rear", f"{rep['fb_db']:.2f} dB  /  {rep['fr_db']:.2f} dB"),
            ("Azimuth beamwidth (−3 dB)", f"{rep['az_beamwidth_deg']}°" if rep['az_beamwidth_deg'] else "—"),
            ("Elevation beamwidth (−3 dB)", f"{rep['el_beamwidth_deg']}°" if rep['el_beamwidth_deg'] else "—"),
            ("Take-off (peak elevation) angle", f"{rep['takeoff_deg']:.1f}°"),
            ("Radiation efficiency", f"{rep['efficiency_pct']:.1f}%  (lossless-wire model)" if rep['efficiency_pct'] is not None else "—"),
            ("Antenna height / boom length", f"{rep['height_ft']:.0f} ft  /  {rep['boom_in']:.1f} in ({rep['boom_in']/12:.1f} ft)"),
            ("Resonant (min-SWR) freq", f"{rep['min_swr']:.3f}:1 @ {rep['min_swr_mhz']:.3f} MHz"),
            ("In-band max SWR", f"{rep['band_max_swr']:.3f}:1  ({rep['band_low_mhz']:.3f}–{rep['band_high_mhz']:.3f} MHz)"),
            ("Bandwidth ≤ 1.2:1", _bw(rep['bw_swr_1p2'])),
            ("Bandwidth ≤ 1.5:1", _bw(rep['bw_swr_1p5'])),
            ("Bandwidth ≤ 2.0:1", _bw(rep['bw_swr_2p0'])),
        ]
        md = "| Metric | Value |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)
        st.markdown(md)
        st.download_button("Download report (JSON)",
                           data=json.dumps(rep, indent=2),
                           file_name="auto_learn_report.json",
                           key="al_dl_report")

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

# ---- Learning memory (what it has learned for THIS design) -----------------
st.markdown("---")
st.markdown("### 🧠 Learning memory")
st.caption("Every candidate move (good AND bad) is saved per design signature "
           "(taper + band + height + element count). New runs warm-start each "
           "parameter from its best-known value and steer away from bad ones.")
try:
    import sqlite3
    from hyagi import v2_runner as _vr
    DB = ROOT / "data/auto7_history.db"
    con = sqlite3.connect(str(DB))
    con.execute("CREATE TABLE IF NOT EXISTS learned_moves (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "created_utc TEXT, signature TEXT, dof TEXT, value REAL, band_max_swr REAL, accepted INTEGER)")
    grand = con.execute("SELECT COUNT(*) FROM learned_moves").fetchone()[0]
    n_designs = con.execute("SELECT COUNT(DISTINCT signature) FROM learned_moves").fetchone()[0]
    n_el = len(geo["elements"])
    sig_like = f"{_vr.taper_signature()}|%h{float(height_ft):.0f}|n{n_el}"
    total = con.execute("SELECT COUNT(*) FROM learned_moves WHERE signature LIKE ?", (sig_like,)).fetchone()[0]
    acc = con.execute("SELECT COUNT(*) FROM learned_moves WHERE signature LIKE ? AND accepted=1", (sig_like,)).fetchone()[0]
    st.caption(f"Active design: `{_vr.taper_signature()} | h{float(height_ft):.0f} | {n_el} elements`")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total memory (all designs)", grand, f"{n_designs} design(s)")
    c2.metric("This design — moves", total)
    c3.metric("This design — good moves", acc)
    rows = con.execute("""
        SELECT dof, value, band_max_swr FROM learned_moves
        WHERE signature LIKE ? AND accepted=1
          AND band_max_swr=(SELECT MIN(band_max_swr) FROM learned_moves x
                            WHERE x.signature=learned_moves.signature
                              AND x.dof=learned_moves.dof AND x.accepted=1)
        GROUP BY dof ORDER BY dof
    """, (sig_like,)).fetchall()
    con.close()
    if rows:
        st.markdown("**Best value learned for each parameter (this design):**")
        md = "| Parameter | Best value | gave band-max SWR |\n|---|---|---|\n" + \
             "\n".join(f"| {d} | {v:.2f} | {s:.3f} |" for d, v, s in rows)
        st.markdown(md)
    elif grand:
        st.info(f"Memory holds {grand} moves from other designs. This exact "
                f"taper/height/element-count has none yet — run AUTO-LEARN to build it.")
    else:
        st.info("No learned moves yet — run AUTO-LEARN to start the memory.")
except Exception as e:
    st.caption(f"(learning memory unavailable: {e})")
