"""v2 Run page — execute a procedure end-to-end."""
import json, pathlib, copy, datetime
import streamlit as st

st.set_page_config(page_title="Run", layout="wide")
st.title("Run")
st.caption("Pick a procedure. Hit Run. Each mini-tune executes against the current geometry; NEC2 evaluates every candidate; v2 scorer ranks them. Best result + log saved to Learning.")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
GEO_PATH   = ROOT / "data/current_geometry_v2.json"
RULES_PATH = ROOT / "data/rules_v2.json"
MINI_PATH  = ROOT / "data/mini_tunes_v2.json"
PROC_PATH  = ROOT / "data/procedures_v2.json"
LEARN_PATH = ROOT / "data/learning_v2.json"

import sys
sys.path.insert(0, str(ROOT))
from hyagi import v2_runner, v2_scorer

@st.cache_data(ttl=2)
def _load(p):
    return json.loads(p.read_text())

geo   = _load(GEO_PATH)
rules = _load(RULES_PATH)
minis = _load(MINI_PATH)
procs = _load(PROC_PATH)

if not procs:
    st.warning("No procedures defined. Create one on the Procedures page.")
    st.stop()

c1, c2 = st.columns([1, 1])
with c1:
    pname = st.selectbox("Procedure", [p["name"] for p in procs], key="run_proc")
    proc  = next(p for p in procs if p["name"] == pname)
    st.markdown(f"**Steps:** {len(proc['steps'])}")
    for j, s in enumerate(proc["steps"], start=1):
        m = next((mm for mm in minis if mm["name"] == s), None)
        if m:
            st.caption(f"  {j}. `{s}`  —  {m.get('type', m.get('param', '?'))} on {m.get('element', m.get('targets', '-'))}")
        else:
            st.caption(f"  {j}. `{s}`  ⚠️ missing")
with c2:
    st.markdown("**Current geometry**")
    for e in geo["elements"]:
        st.caption(f"  {e['name']:<8}  pos={float(e['position_in']):7.2f} in   len={float(e['length_in']):7.2f} in")

st.markdown("---")
if st.button("RUN", type="primary", use_container_width=True):
    log_container = st.container()
    log_lines = []
    progress = log_container.empty()
    def log(msg):
        log_lines.append(msg)
        progress.code("\n".join(log_lines[-50:]), language="text")
    started = datetime.datetime.now()
    minis_by_name = {m["name"]: m for m in minis}
    with st.spinner(f"Running '{pname}'... (this can take a while; one NEC2 run per candidate)"):
        final_geo, best_score, best_m, step_results = v2_runner.run_procedure(
            proc, minis_by_name, geo["elements"], rules, log_fn=log
        )
    elapsed = (datetime.datetime.now() - started).total_seconds()
    log(f"\n=== DONE in {elapsed:.1f}s ===")
    if best_m is None:
        st.error("Run failed. See log above.")
    else:
        log(f"FINAL  score={best_score:+.1f}  gain={best_m['gain_dbi']:.2f}  fb={best_m['fb_db']:.2f}  swr={best_m['max_swr']:.3f}")
        st.success(f"Best score: {best_score:+.1f}  ·  gain {best_m['gain_dbi']:.2f} dBi  ·  F/B {best_m['fb_db']:.2f} dB  ·  max SWR {best_m['max_swr']:.3f}")

        # Persist
        run_record = {
            "saved_at": started.isoformat(),
            "procedure": pname,
            "elapsed_sec": elapsed,
            "initial_geometry": geo["elements"],
            "final_geometry": final_geo,
            "best_score": best_score,
            "best_metrics": best_m,
            "step_results": step_results,
        }
        learn = json.loads(LEARN_PATH.read_text())
        learn.setdefault("runs", []).append(run_record)
        LEARN_PATH.write_text(json.dumps(learn, indent=2))
        st.cache_data.clear()
        st.info(f"Run saved. Learning page now has {len(learn['runs'])} run(s).")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Adopt final geometry as current", use_container_width=True):
                GEO_PATH.write_text(json.dumps({"elements": final_geo}, indent=2))
                st.success("Current geometry updated.")
                st.cache_data.clear()
                st.rerun()
        with col2:
            st.download_button("Download run JSON",
                               data=json.dumps(run_record, indent=2),
                               file_name=f"run_{started.strftime('%Y%m%d_%H%M%S')}.json",
                               use_container_width=True)

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

