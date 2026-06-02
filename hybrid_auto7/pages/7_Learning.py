"""v2 Learning page — view all past runs."""
import json, pathlib
import streamlit as st

st.set_page_config(page_title="Learning", layout="wide")
st.title("Learning")
st.caption("Every Run is appended here. Compare procedures. Restore any geometry as your current starting point.")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
LEARN_PATH = ROOT / "data/learning_v2.json"
GEO_PATH   = ROOT / "data/current_geometry_v2.json"

@st.cache_data(ttl=2)
def load():
    return json.loads(LEARN_PATH.read_text())

data = load()
runs = data.get("runs", [])

if not runs:
    st.info("No runs yet. Go to the Run page and execute a procedure.")
    st.stop()

# Sort by score descending
sorted_runs = sorted(enumerate(runs), key=lambda x: x[1].get("best_score", -1e18), reverse=True)

st.subheader(f"All runs  ({len(runs)} total)")

# Summary table
st.markdown("| # | Saved | Procedure | Score | Gain | F/B | Max SWR |")
st.markdown("|---|---|---|---|---|---|---|")
for i, r in sorted_runs:
    m = r.get("best_metrics", {}) or {}
    st.markdown(
        f"| {i} | {r.get('saved_at','')[:19]} | {r.get('procedure','?')} | "
        f"{r.get('best_score', 0):+.1f} | {m.get('gain_dbi', 0):.2f} | "
        f"{m.get('fb_db', 0):.2f} | {m.get('max_swr', 0):.3f} |"
    )

st.markdown("---")

# Detail viewer
idx = st.selectbox(
    "Inspect run #",
    options=[i for i, _ in sorted_runs],
    format_func=lambda i: f"#{i}  ·  {runs[i].get('procedure','?')}  ·  score={runs[i].get('best_score',0):+.1f}"
)
r = runs[idx]
m = r.get("best_metrics", {}) or {}

c1, c2, c3 = st.columns(3)
c1.metric("Score", f"{r.get('best_score', 0):+.1f}")
c2.metric("Gain (dBi)", f"{m.get('gain_dbi', 0):.2f}")
c3.metric("F/B (dB)", f"{m.get('fb_db', 0):.2f}")
c4, c5, c6 = st.columns(3)
c4.metric("Max SWR", f"{m.get('max_swr', 0):.3f}")
c5.metric("Avg SWR", f"{m.get('avg_swr', 0):.3f}")
c6.metric("Center R", f"{m.get('center_r', 0):.1f} Ω")

st.markdown("### Final geometry")
for e in r.get("final_geometry", []):
    st.caption(f"  {e['name']:<8}  pos={float(e['position_in']):7.2f} in   len={float(e['length_in']):7.2f} in")

col_a, col_b = st.columns(2)
with col_a:
    if st.button("Adopt this geometry as current", use_container_width=True):
        GEO_PATH.write_text(json.dumps({"elements": r["final_geometry"]}, indent=2))
        st.success("Current geometry replaced with this run's final geometry.")
with col_b:
    if st.button("Delete this run", use_container_width=True):
        del runs[idx]
        data["runs"] = runs
        LEARN_PATH.write_text(json.dumps(data, indent=2))
        st.cache_data.clear()
        st.rerun()

with st.expander("Step-by-step results", expanded=False):
    for sr in r.get("step_results", []):
        sc = sr.get("best_score")
        sc_s = f"{sc:+.1f}" if sc is not None else "n/a"
        st.markdown(f"**{sr['step']}**  ·  best score {sc_s}  ·  {len(sr.get('candidates',[]))} candidate(s)")
        st.json(sr.get("candidates", []), expanded=False)

with st.expander("Raw run JSON", expanded=False):
    st.code(json.dumps(r, indent=2), language="json")

# ===== v2_run_helpers BEGIN =====
# Appended by patch 3.  Provides:
#   * SWR target profile selector (sidebar)
#   * Score-mode selector
#   * Full-log + NEC2-card export of the most recent run
import json as _json
from pathlib import Path as _Path
import streamlit as st  # safe re-import
try:
    from hyagi.v2_scorer import SWR_PROFILES as _SWR_PROFILES
except Exception:
    _SWR_PROFILES = {
        "tight_1.0": {"label": "Tight ~1.0:1"},
        "good_1.2":  {"label": "Good  ~1.2:1"},
        "ok_1.5":    {"label": "OK    ~1.5:1"},
    }

_DATA   = _Path(__file__).resolve().parent.parent / "data"
_OPTS_P = _DATA / "run_options_v2.json"
_LRN_P  = _DATA / "learning_v2.json"

def _load_opts():
    try:
        if _OPTS_P.exists():
            return _json.loads(_OPTS_P.read_text())
    except Exception:
        pass
    return {"swr_profile": "tight_1.0", "score_mode": "composite"}

def _save_opts(d):
    _DATA.mkdir(parents=True, exist_ok=True)
    _OPTS_P.write_text(_json.dumps(d, indent=2))

# ---------- Sidebar: SWR profile + score mode --------------------------------
with st.sidebar:
    st.markdown("### SWR target profile")
    _opts = _load_opts()
    _keys = list(_SWR_PROFILES.keys())
    _cur  = _opts.get("swr_profile", "tight_1.0")
    if _cur not in _keys:
        _cur = _keys[0]
    _labels = [_SWR_PROFILES[k].get("label", k) for k in _keys]
    _idx    = _keys.index(_cur)
    _pick   = st.selectbox("Target SWR band",
                           options=_keys,
                           index=_idx,
                           format_func=lambda k: _SWR_PROFILES[k].get("label", k),
                           key="v2_swr_profile_select")
    _mode   = st.radio("Score mode",
                       ["composite", "resonance"],
                       index=0 if _opts.get("score_mode", "composite") == "composite" else 1,
                       key="v2_score_mode_select",
                       horizontal=True)
    if _pick != _opts.get("swr_profile") or _mode != _opts.get("score_mode"):
        _save_opts({"swr_profile": _pick, "score_mode": _mode})
        st.success(f"Saved profile -> {_pick} / {_mode}")

# ---------- Run log / NEC2-card export -------------------------------------
def _latest_run_entry():
    try:
        data = _json.loads(_LRN_P.read_text())
    except Exception:
        return None
    if isinstance(data, list) and data:
        return data[-1]
    if isinstance(data, dict):
        runs = data.get("runs") or data.get("history") or []
        if isinstance(runs, list) and runs:
            return runs[-1]
        return data
    return None

def _fmt_full_log(entry: dict) -> str:
    """Produce the verbose iteration-by-iteration log."""
    lines = []
    lines.append("=" * 72)
    lines.append("hybrid_auto7  --  v2 RUN LOG  (latest)")
    lines.append("=" * 72)
    for k in ("timestamp", "procedure", "swr_profile", "score_mode",
              "freq_mhz", "best_score"):
        if k in entry:
            lines.append(f"{k:>14}: {entry[k]}")
    lines.append("-" * 72)

    iters = (entry.get("iterations")
             or entry.get("history")
             or entry.get("steps")
             or [])
    if iters:
        hdr = (f"{'#':>4} {'mini_tune':<24} {'param':<8} "
               f"{'value':>8} {'SWR':>6} {'gain':>6} {'F/B':>6} "
               f"{'|X|':>6} {'score':>8} {'d_score':>8}")
        lines.append(hdr)
        lines.append("-" * len(hdr))
        prev = None
        for i, it in enumerate(iters):
            sc = it.get("score")
            ds = "" if prev is None or sc is None else f"{sc - prev:+.3f}"
            if sc is not None:
                prev = sc
            lines.append(
                f"{i+1:>4} "
                f"{str(it.get('mini_tune',''))[:24]:<24} "
                f"{str(it.get('param',''))[:8]:<8} "
                f"{float(it.get('value', 0)):>8.3f} "
                f"{float(it.get('swr', 0)):>6.3f} "
                f"{float(it.get('gain_dbi', 0)):>6.2f} "
                f"{float(it.get('fb_db', 0)):>6.2f} "
                f"{abs(float(it.get('x_ohm', 0))):>6.2f} "
                f"{(sc if sc is not None else 0):>8.3f} "
                f"{ds:>8}"
            )
    else:
        lines.append("(no per-iteration history captured)")

    lines.append("-" * 72)
    final = entry.get("final") or entry.get("best") or {}
    if final:
        lines.append("FINAL METRICS:")
        for k, v in final.items():
            lines.append(f"  {k}: {v}")
    lines.append("=" * 72)
    return "\n".join(lines)

def _fmt_nec2_cards(entry: dict) -> str:
    """Emit NEC2-card-style geometry of the final element table."""
    geom = (entry.get("geometry")
            or entry.get("final_geometry")
            or entry.get("best_geometry")
            or {})
    elements = geom.get("elements") if isinstance(geom, dict) else None
    if not elements:
        try:
            cg = _json.loads((_DATA / "current_geometry_v2.json").read_text())
            elements = cg.get("elements") or cg.get("wires") or []
            geom = cg
        except Exception:
            elements = []
    lines = []
    lines.append("CM hybrid_auto7 v2 -- exported geometry")
    lines.append(f"CM profile={entry.get('swr_profile','?')}  "
                 f"freq={entry.get('freq_mhz','?')} MHz")
    lines.append("CE")
    for i, el in enumerate(elements, start=1):
        try:
            length = float(el.get("length", el.get("L", 0)))
            x      = float(el.get("x",      el.get("pos", 0)))
            radius = float(el.get("radius", el.get("r",   0.0625)))
        except Exception:
            continue
        # GW tag segs x1 y1 z1 x2 y2 z2 radius
        y1 = -length / 2.0
        y2 =  length / 2.0
        lines.append(
            f"GW {i:>3} 21 {x:>9.4f} {y1:>9.4f} 0.0000 "
            f"{x:>9.4f} {y2:>9.4f} 0.0000 {radius:.5f}"
        )
    lines.append("GE 0")
    lines.append("FR 0 1 0 0 {} 0".format(entry.get("freq_mhz", 28.4)))
    lines.append("EX 0 1 11 0 1.0 0.0")
    lines.append("RP 0 91 1 1000 0 0 1 0")
    lines.append("EN")
    return "\n".join(lines)

st.markdown("---")
st.markdown("### Copy / Export latest run")

_entry = _latest_run_entry()
if _entry is None:
    st.info("No runs found in data/learning_v2.json yet.")
else:
    _log  = _fmt_full_log(_entry)
    _nec  = _fmt_nec2_cards(_entry)
    _full = _log + "\n\n" + _nec

    _t1, _t2, _t3 = st.tabs(["Full run log", "NEC2 cards", "Combined"])
    with _t1:
        st.code(_log, language="text")
    with _t2:
        st.code(_nec, language="text")
    with _t3:
        st.code(_full, language="text")

    st.download_button(
        "Download run.txt",
        data=_full,
        file_name="hybrid_auto7_run.txt",
        mime="text/plain",
        key="v2_dl_run_txt",
    )
# ===== v2_run_helpers END =====

