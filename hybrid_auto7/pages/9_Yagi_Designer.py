#!/usr/bin/env python3
"""
Yagi Designer page (Streamlit multipage).

Runs ~/scripts/opt_7el_yagi2.py with full CLI control. Streams live progress.
History pulled from ~/scripts/yagi_history.db.
"""
import json
import sys
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import streamlit as st


HOME = Path.home()
YAGI_SCRIPT = HOME / "scripts" / "opt_7el_yagi2.py"
YAGI_DB = HOME / "scripts" / "yagi_history.db"
YAGI_NEC_OUT_DIR = HOME / "scripts" / "yagi_runs"
YAGI_NEC_OUT_DIR.mkdir(exist_ok=True)


st.set_page_config(page_title="Yagi Designer", page_icon=":satellite:", layout="wide")
st.title(":satellite: Yagi Designer")
st.caption("Pure Yagi-Uda optimizer (no hybrid cell). Powered by opt_7el_yagi2.py + yagiopt package.")

if not YAGI_SCRIPT.exists():
    st.error(f"opt_7el_yagi2.py not found at {YAGI_SCRIPT}")
    st.stop()


# ---------- helpers ----------
@st.cache_data(ttl=60)
def list_strategies():
    """Call --list-strategies once, cache for 60s."""
    try:
        out = subprocess.check_output(
            [sys.executable, str(YAGI_SCRIPT), "--list-strategies"],
            text=True, timeout=15,
        )
        # STRAT_PARSE_v2: only accept 2-space-indented strategy name lines
        names = []
        for raw in out.splitlines():
            if not raw.strip(): continue
            if "strateg" in raw.lower() and "available" in raw.lower(): continue
            stripped = raw.lstrip()
            if (len(raw) - len(stripped)) != 2: continue
            if stripped.startswith("#"): continue
            tok = stripped.split()[0].strip(":-,")
            if tok and tok.replace("-", "").replace("_", "").isalnum():
                names.append(tok)
        return sorted(set(names)) or ["champion", "deep-match", "broadband"]
    except Exception:
        return ["champion", "deep-match", "broadband"]


def load_history(limit=100):  # OPT_LEARN_v1: raised cap
    if not YAGI_DB.exists():
        return []
    try:
        with sqlite3.connect(str(YAGI_DB)) as cx:
            cx.row_factory = sqlite3.Row
            try:
                cur = cx.execute(
                    "SELECT id, timestamp, center_freq_mhz, "
                    "final_swr, final_rl_db, final_bw_mhz, final_gain_db, final_fb_db, "
                    "final_score, tag, winner_stage, nec_file_path "
                    "FROM runs ORDER BY id DESC LIMIT ?", (limit,))
                return [dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                # Schema mismatch -- show whatever's available
                cur = cx.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        st.warning(f"History DB read failed: {e}")
        return []


def stream_subprocess(cmd, title="Running"):
    """Run cmd and stream stdout into Streamlit live."""
    placeholder = st.empty()
    code_box = placeholder.code("", language="text")
    captured = []

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    try:
        for line in proc.stdout:
            captured.append(line.rstrip("\n"))
            # Only render last ~120 lines to keep Streamlit responsive
            tail = captured[-120:]
            placeholder.code("\n".join(tail), language="text")
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
    return proc.returncode, "\n".join(captured)


# ---------- UI ----------
with st.sidebar:
    st.header("Quick reference")
    st.markdown("- **Higher priority value** = stronger weight")
    st.markdown("- **--quick / --fast** = fewer freq points (faster, less accurate)")
    st.markdown("- **--free-space** = no ground reflection")
    st.caption(f"Script: `{YAGI_SCRIPT}`")
    st.caption(f"History DB: `{YAGI_DB}` {'(found)' if YAGI_DB.exists() else '(missing)'}")


tabs = st.tabs(["New tune", "History", "Latest output files"])

# ============== TAB 1: NEW TUNE ==============
with tabs[0]:
    col_geo, col_prio, col_run = st.columns([1, 1, 1])

    with col_geo:
        st.subheader("Geometry & frequency")
        n_elements = st.slider("Elements", 2, 18, 7, key="yagi_n_elem")
        center_freq = st.number_input("Center freq (MHz)", value=27.195, step=0.005,
                                       format="%.3f", key="yagi_center")
        polarization = st.selectbox("Polarization", ["horizontal"], key="yagi_pol")
        seed = st.number_input("Random seed", min_value=1, value=1, step=1, key="yagi_seed")
        # YAGI_BOOM_v1
        with st.expander("Boom constraints", expanded=False):
            lock_boom = st.checkbox("Lock boom length (REF at 0, last DIR at end)",
                                    value=False, key="yagi_lock_boom")
            boom_length_ft = st.number_input("Boom length (ft)",
                min_value=3.0, max_value=120.0, value=22.0, step=0.5,
                format="%.2f", key="yagi_boom_len")
            boom_diameter_in = st.number_input("Boom diameter (in)",
                min_value=0.25, max_value=4.0, value=1.5, step=0.125,
                format="%.3f", key="yagi_boom_diam")
            spacing_style = st.selectbox("Spacing style",
                ["auto", "tight", "long"], index=0, key="yagi_spacing_style",
                help="tight=short boom, broad bw, lower gain; long=max gain, narrow bw; auto=optimizer choice")
        

    with col_prio:
        st.subheader("Priorities (0-100)")
        prio_gain = st.slider("Gain",      0, 100, 55, key="yagi_p_gain")
        prio_swr  = st.slider("SWR",       0, 100, 70, key="yagi_p_swr")
        prio_rl   = st.slider("Return loss",0, 100, 70, key="yagi_p_rl")
        prio_bw   = st.slider("Bandwidth", 0, 100, 75, key="yagi_p_bw")
        prio_fb   = st.slider("Front/back",0, 100, 50, key="yagi_p_fb")

    with col_run:
        st.subheader("Engine & strategy")
        strategies = list_strategies()
        strategy = st.selectbox("Strategy", ["(none)"] + strategies, index=0, key="yagi_strategy")
        target_rl = st.number_input("Target RL (dB)", value=20.0, step=1.0, key="yagi_target_rl")
        col_a, col_b = st.columns(2)
        with col_a:
            free_space = st.checkbox("Free space", value=False, key="yagi_fs")
            quick = st.checkbox("Quick (fewer freqs)", value=False, key="yagi_quick")
            fast = st.checkbox("Fast test mode", value=False, key="yagi_fast")
        with col_b:
            preflight = st.checkbox("Preflight only", value=False, key="yagi_pre")
            no_history = st.checkbox("Skip history DB", value=False, key="yagi_no_hist")
        position_passes = st.number_input("Position passes", min_value=1, value=2,
                                           step=1, key="yagi_pos_passes")
        length_passes = st.number_input("Length passes", min_value=1, value=2,
                                         step=1, key="yagi_len_passes")
        tag = st.text_input("Tag (optional)", value="", key="yagi_tag",
                            placeholder="e.g. cb_7el_first_try")

    st.markdown("---")
    auto_export = st.checkbox("Auto-export final .nec", value=True, key="yagi_export")
    show_cmd = st.checkbox("Show command before running", value=True, key="yagi_show_cmd")

    # Build command
    cmd = [sys.executable, "-u", str(YAGI_SCRIPT),
           "--elements", str(int(n_elements)),
           "--center-freq", f"{center_freq:.3f}",
           "--polarization", polarization,
           "--seed", str(int(seed)),
           "--gain-priority", str(int(prio_gain)),
           "--swr-priority", str(int(prio_swr)),
           "--rl-priority", str(int(prio_rl)),
           "--bw-priority", str(int(prio_bw)),
           "--fb-priority", str(int(prio_fb)),
           "--target-rl", f"{target_rl:.1f}",
           "--position-passes", str(int(position_passes)),
           
           "--boom-length-ft", f"{boom_length_ft:.3f}",
           "--boom-diameter-in", f"{boom_diameter_in:.3f}",
           "--spacing-style", spacing_style]
    if strategy != "(none)":
        cmd += ["--strategy", strategy]
    if free_space:  cmd.append("--free-space")
    if quick:       cmd.append("--quick")
    if fast:        cmd.append("--fast")
    if preflight:   cmd.append("--preflight-only")
    if no_history:  cmd.append("--no-history")
    if tag.strip(): cmd += ["--tag", tag.strip()]
    if lock_boom:    cmd.append("--lock-boom")

    nec_export_path = None
    if auto_export and not preflight:
        ts = time.strftime("%Y%m%d_%H%M%S")
        tag_seg = tag.strip().replace(" ", "_") or "yagi"
        nec_export_path = YAGI_NEC_OUT_DIR / f"{tag_seg}_{n_elements}el_{ts}.nec"
        cmd += ["--export-nec", str(nec_export_path)]

    if show_cmd:
        st.code(" ".join(cmd), language="bash")

    if st.button("Run Yagi tune", type="primary", key="yagi_run_btn"):
        with st.spinner(f"Tuning {n_elements}-element Yagi..."):
            code, output = stream_subprocess(cmd, title="Yagi tune")
        if code == 0:
            st.success(f"Run complete (exit 0). Lines: {len(output.splitlines())}")
            if nec_export_path and nec_export_path.exists():
                st.success(f"Exported NEC: {nec_export_path}")
                st.download_button("Download .nec",
                                   nec_export_path.read_text(encoding="utf-8"),
                                   file_name=nec_export_path.name,
                                   mime="text/plain")
        else:
            st.error(f"Run failed (exit {code}). Last 20 lines:")
            st.code("\n".join(output.splitlines()[-20:]), language="text")


# ============== TAB 2: HISTORY ==============
with tabs[1]:
    st.subheader("Run history (yagi_history.db)")
    st.caption(f"DB path: `{YAGI_DB}`")
    if not YAGI_DB.exists():
        st.error(f"Database file is missing at `{YAGI_DB}`. No runs to display.")
        rows = []
    else:
        rows = load_history(limit=100)
        st.caption(f"Found **{len(rows)}** rows in `runs` table.")
    if not rows:
        st.info("No runs found. Run a tune from the 'New tune' tab to populate history.")
    else:
        st.write(f"Showing latest **{len(rows)}** runs")
        st.dataframe(rows, use_container_width=True, height=520)
        # Quick summary stats
        scores = [r.get("final_score") for r in rows if r.get("final_score") is not None]
        gains  = [r.get("final_gain_db") for r in rows if r.get("final_gain_db") is not None]
        if scores:
            c1, c2, c3 = st.columns(3)
            c1.metric("Best score", f"{max(scores):.1f}")
            c2.metric("Avg score (last 30)", f"{sum(scores)/len(scores):.1f}")
            if gains:
                c3.metric("Best gain (dBi)", f"{max(gains):.2f}")


# ============== TAB 3: LATEST OUTPUT FILES ==============
with tabs[2]:
    st.subheader("Recent .nec / .json output files")
    scan_dirs = [YAGI_NEC_OUT_DIR, HOME / "scripts", HOME / "scripts" / "hybrid_auto7" / "models"]
    candidates = []
    for d in scan_dirs:
        st.caption(f"Scanning: `{d}` " + ("(found)" if d.exists() else "(missing)"))
        if d.exists():
            for ext in ("*.nec", "*.json"):
                candidates += list(d.glob(ext))
    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:25]
    st.caption(f"Total files found across all scan dirs: **{len(candidates)}** (showing latest 25)")
    if not candidates:
        st.info("No .nec or .json files found. Run a tune to generate exports.")
    else:
        for p in candidates:
            with st.expander(f"{p.name}    ({p.stat().st_size//1024} KB,  {time.ctime(p.stat().st_mtime)})"):
                st.caption(str(p))
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                    st.code(txt[:4000] + ("\n...[truncated]" if len(txt) > 4000 else ""),
                            language="text")
                    st.download_button(f"Download {p.name}", txt,
                                       file_name=p.name, key=f"dl_{p.name}")
                except Exception as e:
                    st.warning(f"Read failed: {e}")
