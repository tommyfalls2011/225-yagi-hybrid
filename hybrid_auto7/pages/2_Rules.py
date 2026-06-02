"""v2 Rules page — per-element length bounds + per-pair spacing bounds."""
import json, pathlib
import streamlit as st

st.set_page_config(page_title="Rules v2", layout="wide")
st.title("Rules")
st.caption("Hard physical bounds. The runner refuses any geometry outside these. Sim still explores the full range — sweet spots are operator notes, not enforced.")

RULES_PATH = pathlib.Path.home() / "scripts/hybrid_auto7/data/rules_v2.json"

@st.cache_data(ttl=2)
def load():
    return json.loads(RULES_PATH.read_text())

def save(d):
    RULES_PATH.write_text(json.dumps(d, indent=2))
    st.cache_data.clear()

rules = load()
elements = rules["elements"]
spacings = rules["spacings"]
glb      = rules["global"]

tab1, tab2, tab3 = st.tabs(["Element lengths", "Pair spacings", "Global / frequency"])

with tab1:
    st.subheader("Per-element length bounds (inches)")
    for name in list(elements.keys()):
        with st.expander(f"{name}", expanded=False):
            cols = st.columns([1, 1, 3])
            with cols[0]:
                lmin = st.number_input(f"{name} min (in)", value=float(elements[name].get("length_min_in", 0.0)),
                                       step=0.5, key=f"{name}_lmin")
            with cols[1]:
                lmax = st.number_input(f"{name} max (in)", value=float(elements[name].get("length_max_in", 0.0)),
                                       step=0.5, key=f"{name}_lmax")
            with cols[2]:
                notes = st.text_input(f"{name} notes", value=elements[name].get("notes", ""),
                                      key=f"{name}_notes")
            elements[name]["length_min_in"] = float(lmin)
            elements[name]["length_max_in"] = float(lmax)
            elements[name]["notes"] = notes

with tab2:
    st.subheader("Per-pair spacing bounds (inches)")
    st.caption("Distance along boom from one element to the next. Sweet is a hint from your real-world measurements — not enforced.")
    for pair in list(spacings.keys()):
        with st.expander(f"{pair.replace('_', '  ↔  ')}", expanded=False):
            cols = st.columns([1, 1, 3])
            with cols[0]:
                smin = st.number_input(f"{pair} min (in)", value=float(spacings[pair].get("min_in", 0.0)),
                                       step=0.5, key=f"{pair}_smin")
            with cols[1]:
                smax = st.number_input(f"{pair} max (in)", value=float(spacings[pair].get("max_in", 0.0)),
                                       step=0.5, key=f"{pair}_smax")
            with cols[2]:
                sweet = st.text_input(f"{pair} operator sweet-spot note",
                                      value=spacings[pair].get("sweet_in", ""),
                                      key=f"{pair}_sweet")
            spacings[pair]["min_in"]   = float(smin)
            spacings[pair]["max_in"]   = float(smax)
            spacings[pair]["sweet_in"] = sweet

with tab3:
    st.subheader("Global / frequency settings")
    c1, c2, c3 = st.columns(3)
    with c1:
        glb["freq_mhz_low"]    = float(st.number_input("Low MHz",    value=float(glb["freq_mhz_low"]),    step=0.005, format="%.3f"))
    with c2:
        glb["freq_mhz_center"] = float(st.number_input("Center MHz", value=float(glb["freq_mhz_center"]), step=0.005, format="%.3f"))
    with c3:
        glb["freq_mhz_high"]   = float(st.number_input("High MHz",   value=float(glb["freq_mhz_high"]),   step=0.005, format="%.3f"))
    c4, c5 = st.columns(2)
    with c4:
        glb["min_boom_ft"] = float(st.number_input("Min boom (ft)", value=float(glb["min_boom_ft"]), step=0.5))
    with c5:
        glb["max_boom_ft"] = float(st.number_input("Max boom (ft)", value=float(glb["max_boom_ft"]), step=0.5))

st.markdown("---")
col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    if st.button("Save rules", type="primary", use_container_width=True):
        save(rules)
        st.success("Saved.")
with col2:
    if st.button("Reload from disk", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with col3:
    st.caption(f"File: {RULES_PATH}")

with st.expander("Raw JSON", expanded=False):
    st.code(json.dumps(rules, indent=2), language="json")
