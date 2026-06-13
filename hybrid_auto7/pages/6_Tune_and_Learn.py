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

st.set_page_config(page_title="Tune & Learn", layout="wide")
st.title("Tune & Learn  ·  run · log · self-learn")
st.caption("Bottom of the workflow: tune with the auto-matcher OR your own "
           "procedure, log every move (good and bad), self-learn from past runs, "
           "and report the result. Set the antenna up on the Antenna Setup page "
           "first; pick mini-tunes / procedures on their pages.")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
GEO_PATH = ROOT / "data/current_geometry_v2.json"
RULES_PATH = ROOT / "data/rules_v2.json"
MINI_PATH = ROOT / "data/mini_tunes_v2.json"
PROC_PATH = ROOT / "data/procedures_v2.json"
SETUP_PATH = ROOT / "data/setup_v2.json"

sys.path.insert(0, str(ROOT))
from hyagi import v2_runner  # noqa: E402
from hyagi import perf_report  # noqa: E402
from hyagi import hybrid_seed  # noqa: E402
from hyagi import exporters  # noqa: E402
from hyagi.units import fmt_in  # noqa: E402
from hyagi.auto_learn import LearnConfig, run_learning  # noqa: E402


@st.cache_data(ttl=2)
def _load(p):
    return json.loads(pathlib.Path(p).read_text())


def _load_setup():
    try:
        return json.loads(SETUP_PATH.read_text())
    except Exception:
        return {"n_directors": 3, "boom_mode": "fixed", "boom_length_in": None,
                "height_ft": 30.0, "boom_diameter_in": 1.5, "grounding": "insulated"}


geo = _load(str(GEO_PATH))
rules = _load(str(RULES_PATH))
minis = _load(str(MINI_PATH))
procs = _load(str(PROC_PATH))
setup = _load_setup()

glb = rules["global"]
# Apply construction options from Antenna Setup to live exports / previews too.
v2_runner.GROUNDED = (str(setup.get("grounding", "insulated")) == "grounded")
v2_runner.BOOM_DIAMETER_IN = float(setup.get("boom_diameter_in", 1.5))

# Initialise band-edge state ONCE so the OWA-preset button (or any future
# preset) can write to it before the number_input widgets render.  Writing to
# a widget's key AFTER it has been instantiated raises StreamlitAPIException,
# so all preset buttons must mutate state up here and let the widgets read it
# back on the rerun.
if "al_low" not in st.session_state:
    st.session_state["al_low"] = float(glb.get("freq_mhz_low", 26.965))
if "al_high" not in st.session_state:
    st.session_state["al_high"] = float(glb.get("freq_mhz_high", 27.405))

st.success(
    f"From Antenna Setup → {len(geo['elements'])} elements · height "
    f"{float(setup.get('height_ft', 30.0)):.0f} ft · boom "
    f"{'FREE (tuner moves spacings)' if setup.get('boom_mode') == 'free' else 'FIXED'} · "
    f"boom Ø {float(setup.get('boom_diameter_in', 1.5)):.2f}\" · "
    f"elements {str(setup.get('grounding', 'insulated')).upper()}. "
    f"Change these on the Antenna Setup page."
)

c1, c2 = st.columns(2)
with c1:
    # OWA wideband preset — must mutate band state BEFORE the band_low/high
    # widgets render below, otherwise Streamlit rejects the write.
    if st.button("📡 OWA wideband preset (25.000 – 28.000 MHz)",
                 key="al_owa_preset", use_container_width=True,
                 help="Sets the band to the 3 MHz OWA range. The matcher will "
                      "stagger-tune the XFRMR / DE / COUPLER as coupled "
                      "resonators to flatten SWR across the band. Most antennas "
                      "use a narrower CB band; pick OWA only for true wideband "
                      "builds."):
        st.session_state["al_low"] = 25.000
        st.session_state["al_high"] = 28.000
        st.rerun()
    band_low = st.number_input("Band low (MHz)", step=0.005, format="%.3f",
                               key="al_low",
                               help="Lower band edge to hold SWR across (e.g. 26.665 freeband, "
                                    "25.000 for full OWA wideband)")
    band_high = st.number_input("Band high (MHz)", step=0.005, format="%.3f",
                                key="al_high")
    target_swr = st.number_input("Target max SWR", value=1.20, min_value=1.01, max_value=3.0,
                                 step=0.01, format="%.2f", key="al_target")
    tune_goal = st.selectbox(
        "Tune goal",
        ["hybrid", "wideband", "resonant"],
        format_func=lambda g: (
            "Hybrid — strong beam + flat wideband match (recommended)" if g == "hybrid"
            else "Wideband SWR only (flattest across band — can cost gain/F-B)" if g == "wideband"
            else "Resonant match — high power (R≈50, X≈0 at center)"),
        key="al_goal",
        help="Hybrid alternates a BEAM phase (reflector + directors → max gain & "
             "front-to-back) with a MATCH phase (driven XFRMR/DE/COUPLER cell → "
             "flat wideband SWR), so the directors are NOT shortened to chase SWR. "
             "Wideband optimizes SWR alone (can flatten the beam). Resonant drives "
             "X→0 / R→50 at center for safe high-power (50 kW+) operation.")
with c2:
    height_ft = st.number_input("Height (ft)", value=float(setup.get("height_ft", 30.0)),
                                step=1.0, key="al_height")
    band_points = st.slider("Band sweep points", 9, 41, 21, key="al_points",
                            help="More points = stricter wideband check (slower)")
    restarts = st.slider("Search restarts (escape local minima)", 0, 4, 1, key="al_restarts")

st.markdown("#### Tuning method")
tune_method = st.radio(
    "How should it tune?",
    ["matcher", "procedure"],
    format_func=lambda m: ("Auto-matcher (coordinate descent — fast, hands-off)"
                           if m == "matcher"
                           else "Run MY procedure (your selected mini-tunes, step by step)"),
    key="al_method", horizontal=False,
    help="Auto-matcher tunes element lengths (and spacings if boom is FREE) to the "
         "goal above. 'Run my procedure' executes the mini-tune sequence you built "
         "on the Procedures page, logging and learning each move.")
sel_proc_name = None
if tune_method == "procedure":
    if procs:
        sel_proc_name = st.selectbox("Procedure to run", [p["name"] for p in procs],
                                     key="al_proc_pick")
        _p = next(p for p in procs if p["name"] == sel_proc_name)
        st.caption("Steps: " + " → ".join(_p.get("steps", [])) if _p.get("steps") else "no steps")
    else:
        st.warning("No procedures defined yet — build one on the Procedures page, "
                   "or use the Auto-matcher.")

polish = st.checkbox("Recover gain / F-B after hitting SWR target", value=True, key="al_polish")

# ---- Element taper (tubing schedule) — set BEFORE tuning -------------------
STD_TAPER = [[0.625, 36.0], [0.5, 999.0]]   # standard commercial taper (default)
TAPER_PATH = ROOT / "data/taper_v2.json"
st.markdown("### ⚙️ Tubing taper  ·  set this BEFORE you tune")
st.caption("This is the aluminum tube schedule the optimizer and the .nec/.maa "
           "export use for EVERY element. **Default = standard commercial taper "
           "(0.625\" → 0.5\").** Change it here for a custom build, hit Save, then "
           "run AUTO-LEARN. One tube per line: `OD_inches, section_length_inches`, "
           "from the element CENTRE out to the TIP; use a big length (e.g. 999) for "
           "the piece that runs to the tip.")
try:
    _cur_taper_cfg = json.loads(TAPER_PATH.read_text())
except Exception:
    _cur_taper_cfg = {"default": STD_TAPER}
_cur_taper = _cur_taper_cfg.get("default", STD_TAPER)
_cur_overrides = _cur_taper_cfg.get("overrides", {}) or {}
_taper_text = "\n".join(f"{od}, {L}" for od, L in _cur_taper)
new_taper_text = st.text_area("Taper sections (OD_in, length_in — centre → tip)",
                              value=_taper_text, key="al_taper", height=120)
_tc1, _tc2 = st.columns(2)
with _tc1:
    if st.button("💾 Save taper", key="al_save_taper", use_container_width=True):
        sched = []
        for ln in new_taper_text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            parts = [p for p in ln.replace("\t", ",").split(",") if p.strip()]
            if len(parts) >= 2:
                sched.append([float(parts[0]), float(parts[1])])
        if sched:
            _save = {"default": sched}
            if _cur_overrides:                # preserve per-element overrides
                _save["overrides"] = _cur_overrides
            TAPER_PATH.write_text(json.dumps(_save, indent=2))
            st.cache_data.clear()
            st.success(f"Saved taper: {sched}  (re-run AUTO-LEARN to tune for it)")
            st.rerun()
        else:
            st.error("Could not parse any 'OD, length' lines.")
with _tc2:
    if st.button("↩️ Reset to standard commercial (0.625\"/0.5\")",
                 key="al_reset_taper", use_container_width=True):
        TAPER_PATH.write_text(json.dumps({"default": STD_TAPER}, indent=2))
        st.cache_data.clear()
        st.success("Taper reset to standard commercial 0.625\"/0.5\" "
                   "(per-element overrides cleared).")
        st.rerun()

# ---- Per-element taper overrides ------------------------------------------
# Lets the user run thinner tubing on the directors (typical: 0.5"/0.375") than
# on the driven cell, exactly like commercial Yagis.  Each override fully
# REPLACES the default schedule for that one element; leave a field blank to
# inherit the default above.  Overrides are saved into taper_v2.json and the
# whole engine (.nec, .maa, optimizer, cut sheet, report) picks them up
# automatically.
with st.expander("🧵 Per-element taper overrides "
                 "(directors thinner than the driven cell, etc.)",
                 expanded=bool(_cur_overrides)):
    st.caption("Each override fully replaces the default schedule for that "
               "one element.  Leave a field BLANK to keep the default.  Format "
               "(one tube per line): `OD_inches, section_length_inches` from "
               "the element CENTRE out to the TIP.")
    _names = [str(e["name"]).upper() for e in geo["elements"]]
    _ov_inputs = {}
    _ocols = st.columns(min(3, len(_names)) or 1)
    for i, name in enumerate(_names):
        with _ocols[i % len(_ocols)]:
            cur = _cur_overrides.get(name)
            txt = "\n".join(f"{od}, {L}" for od, L in cur) if cur else ""
            _ov_inputs[name] = st.text_area(
                f"`{name}`",
                value=txt, key=f"al_ov_{name}", height=88,
                placeholder="(blank = use default above)",
            )
    if st.button("💾 Save per-element overrides", key="al_save_overrides",
                 use_container_width=True):
        new_overrides = {}
        for name, txt in _ov_inputs.items():
            sched = []
            for ln in (txt or "").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                parts = [p for p in ln.replace("\t", ",").split(",") if p.strip()]
                if len(parts) >= 2:
                    try:
                        sched.append([float(parts[0]), float(parts[1])])
                    except ValueError:
                        pass
            if sched:
                new_overrides[name] = sched
        save = {"default": _cur_taper}
        if new_overrides:
            save["overrides"] = new_overrides
        TAPER_PATH.write_text(json.dumps(save, indent=2))
        st.cache_data.clear()
        if new_overrides:
            st.success(f"Saved {len(new_overrides)} per-element override(s): "
                       f"{', '.join(new_overrides)}.  Re-run AUTO-LEARN to "
                       f"tune for the new taper combination.")
        else:
            st.success("All per-element overrides cleared.")
        st.rerun()

st.markdown("**Starting geometry (current)**  ·  build/reseed on the Antenna Setup page")
n_dirs_now = sum(1 for e in geo["elements"] if str(e["name"]).upper().startswith("DIR"))


gcols = st.columns(min(4, len(geo["elements"])) or 1)
for i, e in enumerate(geo["elements"]):
    with gcols[i % len(gcols)]:
        st.caption(f"`{e['name']}`  pos {fmt_in(e['position_in'])}  "
                   f"len {fmt_in(e['length_in'])}")

with st.expander("📤 Export CURRENT geometry to .nec / .maa", expanded=False):
    st.caption("Export the current geometry as-is (no tuning needed) to open in "
               "nec2c / 4nec2 / xnec2c (.nec) or MMANA-GAL (.maa).")
    rules_cur = json.loads(json.dumps(rules))
    rules_cur["global"]["freq_mhz_low"] = float(band_low)
    rules_cur["global"]["freq_mhz_high"] = float(band_high)
    try:
        nec_cur = exporters.to_nec(geo["elements"], rules_cur,
                                   height_ft=float(height_ft), points=int(band_points))
        maa_cur = exporters.to_maa(geo["elements"], rules_cur,
                                   height_ft=float(height_ft),
                                   center_mhz=float(glb.get("freq_mhz_center", 27.195)))
        ec1, ec2 = st.columns(2)
        with ec1:
            st.download_button("Download .nec", data=nec_cur,
                               file_name="hybrid_auto7_current.nec", mime="text/plain",
                               use_container_width=True, key="al_dl_nec_cur")
        with ec2:
            st.download_button("Download .maa", data=maa_cur,
                               file_name="hybrid_auto7_current.maa", mime="text/plain",
                               use_container_width=True, key="al_dl_maa_cur")
    except Exception as _ex:
        st.warning(f"Export unavailable: {_ex}")

st.info(f"**Active tubing taper:** `{v2_runner.taper_signature()}`  — every tune "
        f"and export uses THIS schedule. Edit it in the ⚙️ Element taper box above "
        f"if it doesn't match your real elements, then re-run.")

st.markdown("---")
if st.button("RUN TUNE + LEARN", type="primary", use_container_width=True, key="al_run"):
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

    use_matcher = (tune_method == "matcher")
    if use_matcher:
        procedure = procs[0] if procs else {"name": "matcher", "steps": []}
    else:
        procedure = next((p for p in procs if p["name"] == sel_proc_name),
                         {"name": "procedure", "steps": []})
    cfg = LearnConfig(
        project_name="current_geometry",
        height_ft=float(height_ft),
        swr_profile="wideband_1.2",
        target_max_swr=float(target_swr),
        band_sweep_points=int(band_points),
        max_generations=int(restarts) + 1,
        use_matcher=use_matcher,
        polish_gain=bool(polish),
        tune_goal=str(tune_goal),
        tune_spacings=(str(setup.get("boom_mode", "fixed")) == "free"),
        grounded=(str(setup.get("grounding", "insulated")) == "grounded"),
        boom_diameter_in=float(setup.get("boom_diameter_in", 1.5)),
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
        "taper": v2_runner.taper_signature(),
    }

res = st.session_state.get("al_result")
if res:
    st.markdown("### Result")
    # Guard against a STALE result: if the current geometry no longer matches the
    # one this result was tuned for (e.g. you reseeded a different element count,
    # adopted/pulled a new geometry, or never adopted this run), the result's
    # numbers and .nec/.maa export belong to a DIFFERENT antenna. Make that loud.
    res_names = [e["name"] for e in res["geometry"]]
    cur_names = [e["name"] for e in geo["elements"]]
    if res_names != cur_names:
        st.error(
            f"⚠️ This result is from a PREVIOUS run ({len(res_names)} elements: "
            f"{', '.join(res_names)}) and does NOT match your current geometry "
            f"({len(cur_names)} elements: {', '.join(cur_names)}). Its metrics and "
            f"the .nec/.maa export below are for that other antenna — adopt it, or "
            f"clear it and re-run AUTO-LEARN on your current geometry."
        )
        if st.button("Clear stale result", key="al_clear_stale"):
            del st.session_state["al_result"]
            st.rerun()
    ok = res["band_max"] <= float(target_swr) + 1e-6
    (st.success if ok else st.warning)(
        f"band-max SWR {res['band_max']:.3f}  ·  gain {res['gain']:.2f} dBi  ·  "
        f"F/B {res['fb']:.2f} dB  ·  score {res['score']:+.1f}  ·  {res['elapsed']:.0f}s"
    )
    if res_names == cur_names and res["geometry"] != geo["elements"]:
        st.warning("📌 This tune is **NOT saved yet.** Click **Adopt tuned geometry "
                   "as current** below to make it your antenna — the **Report** page "
                   "(and the next warm-start) reads the ADOPTED geometry, not this panel.")
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Center R", f"{res.get('center_r', 0):.1f} Ω")
    cc2.metric("Center X (reactance)", f"{res.get('center_x', 0):+.2f} Ω",
               help="For high power this must be ≈0")
    cc3.metric("Center SWR", f"{res.get('center_swr', 0):.3f}")
    cc4.metric("Return loss", f"{res.get('center_rl', 0):.1f} dB")
    st.caption(f"Tuned on tubing taper: `{res.get('taper', '?')}`")

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
            ("Antenna height / boom length", f"{rep['height_ft']:.0f} ft  /  {fmt_in(rep['boom_in'])}"),
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

    st.markdown("**Tuned geometry**  ·  imperial (ft / in / 16ths)")
    tuned = sorted(res["geometry"], key=lambda e: float(e["position_in"]))
    rows_geom = []
    for i, e in enumerate(tuned):
        # Spacing to the next element along the boom -- this is what you'd
        # mark on the boom with a tape measure on construction day.
        spacing = (float(tuned[i + 1]["position_in"]) - float(e["position_in"])
                   if i + 1 < len(tuned) else None)
        rows_geom.append({
            "Element": e["name"],
            "Boom position": fmt_in(e["position_in"]),
            "Overall length (tip-to-tip)": fmt_in(e["length_in"]),
            "Half-length (centre→tip)": fmt_in(float(e["length_in"]) / 2.0),
            "Spacing to next": fmt_in(spacing) if spacing is not None else "—",
        })
    st.dataframe(rows_geom, hide_index=True, use_container_width=True)
    boom_in = float(tuned[-1]["position_in"]) - float(tuned[0]["position_in"])
    st.caption(f"Boom length (REF → last director): **{fmt_in(boom_in)}**")

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

    # ---- Open in external simulators (.nec / .maa) -------------------------
    st.markdown("**Open in external programs**")
    st.caption("Exports the TUNED antenna so you can verify / view it elsewhere. "
               "`.nec` is the same tapered-aluminium model the optimizer used "
               "(nec2c / 4nec2 / xnec2c). `.maa` is MMANA-GAL — element span on Y, "
               "boom on X, height on Z; the DE is voltage-fed at its centre. "
               "Each element keeps its stepped tubing so the resonance matches.")
    rules_exp = json.loads(json.dumps(rules))
    rules_exp["global"]["freq_mhz_low"] = res["low"]
    rules_exp["global"]["freq_mhz_high"] = res["high"]
    try:
        nec_txt = exporters.to_nec(res["geometry"], rules_exp,
                                   height_ft=res["height"], points=res["points"])
        maa_txt = exporters.to_maa(res["geometry"], rules_exp,
                                   height_ft=res["height"],
                                   center_mhz=float(glb.get("freq_mhz_center", 27.195)))
        e1, e2 = st.columns(2)
        with e1:
            st.download_button("Download .nec (NEC-2 deck)", data=nec_txt,
                               file_name="hybrid_auto7_tuned.nec", mime="text/plain",
                               use_container_width=True, key="al_dl_nec")
        with e2:
            st.download_button("Download .maa (MMANA-GAL)", data=maa_txt,
                               file_name="hybrid_auto7_tuned.maa", mime="text/plain",
                               use_container_width=True, key="al_dl_maa")
        with st.expander("Preview .maa", expanded=False):
            st.code(maa_txt, language="text")
    except Exception as _ex:
        st.warning(f"Export unavailable: {_ex}")

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
