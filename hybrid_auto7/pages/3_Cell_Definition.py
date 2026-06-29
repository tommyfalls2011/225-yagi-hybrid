"""v2 Cell Definition page — define initial cell geometry + tune order."""
import json, pathlib
import streamlit as st

st.set_page_config(page_title="Cell Definition", layout="wide")
st.title("Cell Definition")
st.caption("The driven cell (XFRMR + DE + COUPLER) is the heart of the antenna. Define its starting geometry and the order in which its parameters get tuned.")

CELL_PATH  = pathlib.Path.home() / "scripts/hybrid_auto7/data/cell_def_v2.json"
RULES_PATH = pathlib.Path.home() / "scripts/hybrid_auto7/data/rules_v2.json"

@st.cache_data(ttl=2)
def load_cell():
    return json.loads(CELL_PATH.read_text())

@st.cache_data(ttl=2)
def load_rules():
    return json.loads(RULES_PATH.read_text())

cell  = load_cell()
rules = load_rules()

name = st.text_input("Cell name", value=cell.get("name", "default_3el_cell"))
cell["name"] = name

st.subheader("Initial geometry")
st.caption("Starting values. The runner mutates from here per the tune order.")

elements = cell["elements"]
for i, el in enumerate(elements):
    elname = el["name"]
    bounds = rules["elements"].get(elname, {})
    lmin = bounds.get("length_min_in", 0.0)
    lmax = bounds.get("length_max_in", 999.0)
    with st.expander(f"{elname}", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            el["init_position_in"] = float(st.number_input(
                f"{elname} init position (in)",
                value=float(el.get("init_position_in", 0.0)),
                step=0.25, key=f"cell_{i}_pos"))
        with c2:
            el["init_length_in"] = float(st.number_input(
                f"{elname} init length (in)  [rules: {lmin}-{lmax}]",
                value=float(el.get("init_length_in", 200.0)),
                step=0.25, key=f"cell_{i}_len"))
        with c3:
            in_bounds = lmin <= float(el["init_length_in"]) <= lmax
            st.metric("In bounds?", "Yes" if in_bounds else "OUT OF RANGE")

st.subheader("Tune order")
st.caption("Which knob gets tuned first, second, third... You can drag this list later; for now edit as text (one stage per line).")

default_order = cell.get("tune_order", [])
order_txt = st.text_area("Tune order (one stage per line)",
                          value="\n".join(default_order),
                          height=180,
                          help="Stage names. They will reference Mini-Tunes by name once you create them next turn.")
cell["tune_order"] = [s.strip() for s in order_txt.splitlines() if s.strip()]

cell["notes"] = st.text_area("Notes", value=cell.get("notes", ""), height=80)

st.markdown("---")
col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    if st.button("Save cell definition", type="primary", use_container_width=True):
        CELL_PATH.write_text(json.dumps(cell, indent=2))
        st.cache_data.clear()
        st.success("Saved.")
with col2:
    if st.button("Reload from disk", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with col3:
    st.caption(f"File: {CELL_PATH}")

with st.expander("Raw JSON", expanded=False):
    st.code(json.dumps(cell, indent=2), language="json")
