import json
from pathlib import Path
import streamlit as st
from hyagi import cell_rules
st.set_page_config(page_title="Physics Rules", layout="wide")
st.title("Physics Rules  --  Hybrid OWA")
st.caption("These rules override the NEC2 simulator. Procedures MUST obey them.")
rules = cell_rules.get_rules()
defaults = cell_rules.DEFAULTS
ca, cb = st.columns(2)
with ca:
    st.subheader("Element-length rules")
    rules["MIN_LEN_GAP_FROM_DE"] = st.number_input(
        "MIN_LEN_GAP_FROM_DE (in)  -- XFRMR/COUPLER must be this much SHORTER than DE",
        0.0, 24.0, float(rules["MIN_LEN_GAP_FROM_DE"]), 0.25)
    rules["XFRMR_LT_DE"]   = st.toggle("Enforce XFRMR < DE", value=bool(rules["XFRMR_LT_DE"]))
    rules["COUPLER_LT_DE"] = st.toggle("Enforce COUPLER < DE", value=bool(rules["COUPLER_LT_DE"]))
    st.divider()
    st.subheader("Director progression")
    rules["DIRECTOR_MODE"] = st.selectbox("DIRECTOR_MODE",
        ["strict_progressive", "experimental_progressive"],
        0 if rules["DIRECTOR_MODE"]=="strict_progressive" else 1)
    rules["STRICT_PROGRESSION"] = st.toggle("STRICT_PROGRESSION (master)", value=bool(rules["STRICT_PROGRESSION"]))
with cb:
    st.subheader("Spacing rules")
    rules["MIN_SPACING_IN"] = st.number_input("MIN_SPACING_IN (in)", 1.0, 24.0, float(rules["MIN_SPACING_IN"]), 0.25)
    rules["MAX_SPACING_IN"] = st.number_input("MAX_SPACING_IN (in)", 12.0, 120.0, float(rules["MAX_SPACING_IN"]), 0.5)
    st.divider()
    st.subheader("Pattern realism (sky-bouncer)")
    rules["REJECT_SKY_BOUNCER"] = st.toggle("Reject sky-bouncers", value=bool(rules["REJECT_SKY_BOUNCER"]))
    rules["MAX_PEAK_ELEV_DEG"] = st.number_input("MAX_PEAK_ELEV_DEG (deg)", 5.0, 60.0, float(rules["MAX_PEAK_ELEV_DEG"]), 1.0)
st.divider()
st.subheader("Element-length bounds")
c1, c2, c3 = st.columns(3)
with c1:
    rules["REFL_MIN_LEN_IN"] = st.number_input("Reflector min (in)", value=float(rules["REFL_MIN_LEN_IN"]), step=0.25)
    rules["REFL_MAX_LEN_IN"] = st.number_input("Reflector max (in)", value=float(rules["REFL_MAX_LEN_IN"]), step=0.25)
with c2:
    rules["DE_MIN_LEN_IN"] = st.number_input("DE min (in)", value=float(rules["DE_MIN_LEN_IN"]), step=0.25)
    rules["DE_MAX_LEN_IN"] = st.number_input("DE max (in)", value=float(rules["DE_MAX_LEN_IN"]), step=0.25)
with c3:
    rules["DIR_MIN_LEN_IN"] = st.number_input("Director min (in)", value=float(rules["DIR_MIN_LEN_IN"]), step=0.25)
    rules["DIR_MAX_LEN_IN"] = st.number_input("Director max (in)", value=float(rules["DIR_MAX_LEN_IN"]), step=0.25)
st.divider()

st.divider()
st.subheader("Boom grounding  --  sim/roof correlation")
st.caption("Real-world XFRMR/COUPLER bolted to a conductive boom. Toggle ON to model this in NEC.")
rules["BOOM_GROUNDED"] = st.toggle(
    "Bond XFRMR/COUPLER to a conductive boom", value=bool(rules.get("BOOM_GROUNDED", False)))
rules["BOOM_RADIUS_IN"] = st.number_input(
    "Boom radius (in)", 0.1, 3.0, float(rules.get("BOOM_RADIUS_IN", 0.5)), 0.05)
_names = rules.get("BOOM_GROUND_NAMES", ["XFRMR","COUPLER"])
_text = st.text_input("Elements to ground (comma-separated)", ",".join(_names))
rules["BOOM_GROUND_NAMES"] = [t.strip().upper() for t in _text.split(",") if t.strip()]

b1, b2, b3 = st.columns(3)
with b1:
    if st.button("Save rules", type="primary", use_container_width=True):
        cell_rules.save_rules(rules); st.success(f"Saved to {cell_rules.RULES_PATH}"); st.balloons()
with b2:
    if st.button("Reload from disk", use_container_width=True): st.rerun()
with b3:
    if st.button("Reset to defaults", use_container_width=True):
        cell_rules.save_rules(dict(defaults)); st.warning("Reset. Click Reload.")
st.divider()
with st.expander("Active rules (what the sim sees right now)"):
    st.code(cell_rules.describe_active_rules(), language="text")
with st.expander("Raw JSON on disk"):
    try: st.code(Path(cell_rules.RULES_PATH).read_text(), language="json")
    except Exception as e: st.error(str(e))


# === XFRMR_COUPLER_SLIDERS_V1 ===
import streamlit as st
st.markdown("---")
st.subheader("XFRMR / COUPLER per-element spacing bounds")
st.caption("Real-world physical limits. Sim still explores the full range; sweet spots are hints only.")

_rules = _load_rules() if "_load_rules" in dir() else __import__("json").loads(
    (__import__("pathlib").Path.home()/"scripts/hybrid_auto7/data/physics_rules.json").read_text()
)
c1, c2 = st.columns(2)
with c1:
    st.markdown("**XFRMR**  _(sweet spot ≈ 5.5–6.5\")_")
    xmin = st.slider("XFRMR min (in)", 1.0, 32.0, float(_rules.get("XFRMR_MIN_SPACING_IN", 4.0)), 0.5, key="xfrmr_min_v1")
    xmax = st.slider("XFRMR max (in)", 1.0, 48.0, float(_rules.get("XFRMR_MAX_SPACING_IN", 32.0)), 0.5, key="xfrmr_max_v1")
with c2:
    st.markdown("**COUPLER**  _(sweet spot ≈ 12–23\")_")
    cmin = st.slider("COUPLER min (in)", 1.0, 32.0, float(_rules.get("COUPLER_MIN_SPACING_IN", 4.0)), 0.5, key="coup_min_v1")
    cmax = st.slider("COUPLER max (in)", 1.0, 48.0, float(_rules.get("COUPLER_MAX_SPACING_IN", 32.0)), 0.5, key="coup_max_v1")

if st.button("Save XFRMR/COUPLER bounds", key="save_xc_v1"):
    import json as _json, pathlib as _pl
    _p = _pl.Path.home()/"scripts/hybrid_auto7/data/physics_rules.json"
    _d = _json.loads(_p.read_text())
    _d["XFRMR_MIN_SPACING_IN"]   = float(xmin)
    _d["XFRMR_MAX_SPACING_IN"]   = float(xmax)
    _d["COUPLER_MIN_SPACING_IN"] = float(cmin)
    _d["COUPLER_MAX_SPACING_IN"] = float(cmax)
    _p.write_text(_json.dumps(_d, indent=2))
    st.success(f"Saved: XFRMR [{xmin}..{xmax}], COUPLER [{cmin}..{cmax}]")
