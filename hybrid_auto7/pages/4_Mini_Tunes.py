"""v2 Mini-Tunes page — create/edit reusable tune primitives."""
import json, pathlib
import streamlit as st

st.set_page_config(page_title="Mini-Tunes", layout="wide")
st.title("Mini-Tunes")
st.caption("Small reusable tune primitives. Each one mutates ONE element parameter. Chain them on the Procedures page.")

PATH       = pathlib.Path.home() / "scripts/hybrid_auto7/data/mini_tunes_v2.json"
RULES_PATH = pathlib.Path.home() / "scripts/hybrid_auto7/data/rules_v2.json"

@st.cache_data(ttl=2)
def load():
    return json.loads(PATH.read_text())

@st.cache_data(ttl=2)
def load_rules():
    return json.loads(RULES_PATH.read_text())

mts   = load()
rules = load_rules()
elements_list = list(rules["elements"].keys())

TYPES = {
    "sweep_length":   "Sweep an element length across [start..stop] step",
    "sweep_position": "Sweep an element position across [start..stop] step",
    "nudge_length":   "Fine-tune length: current ± delta, in steps",
    "nudge_position": "Fine-tune position: current ± delta, in steps",
}

st.subheader("Existing mini-tunes")
if not mts:
    st.info("No mini-tunes yet. Use the form below to create one.")
else:
    for i, mt in enumerate(list(mts)):
        with st.expander(f"{mt.get('name','(unnamed)')}  —  {mt.get('type','?')} on {mt.get('element','?')}",
                         expanded=False):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.code(json.dumps(mt, indent=2), language="json")
                st.caption(mt.get("notes", ""))
            with c2:
                if st.button("Delete", key=f"del_{i}", use_container_width=True):
                    mts.pop(i)
                    PATH.write_text(json.dumps(mts, indent=2))
                    st.cache_data.clear()
                    st.rerun()

st.markdown("---")
st.subheader("Create / edit mini-tune")

with st.form("mini_tune_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        name = st.text_input("Name (unique)", value="my_new_mini_tune",
                              help="Used by Procedures to reference this mini-tune.")
        mtype = st.selectbox("Type", list(TYPES.keys()),
                              help="\n".join([f"{k}: {v}" for k, v in TYPES.items()]))
        element = st.selectbox("Element", elements_list)
    with c2:
        notes = st.text_area("Notes (optional)", height=80)
        score_mode = st.selectbox("Score mode", ["composite", "resonance"],
            help="composite = gain+F/B+SWR (default). resonance = |X|~0, R~50, SWR low (use for bare-DE length tuning).",
            key="score_mode_v1")  # SCORE_MODE_FORM_V1

    st.markdown("**Parameters** (units = inches)")

    if mtype in ("sweep_length", "sweep_position"):
        p1, p2, p3 = st.columns(3)
        with p1:
            start_in = st.number_input("start", value=180.0, step=0.5)
        with p2:
            stop_in  = st.number_input("stop",  value=220.0, step=0.5)
        with p3:
            step_in  = st.number_input("step",  value=0.5,   step=0.05, format="%.2f")
        delta_in = None
    else:
        p1, p2 = st.columns(2)
        with p1:
            delta_in = st.number_input("delta (± from current)", value=4.0, step=0.5)
        with p2:
            step_in  = st.number_input("step", value=0.25, step=0.05, format="%.2f")
        start_in = stop_in = None

    submitted = st.form_submit_button("Save mini-tune", type="primary")

if submitted:
    if not name.strip():
        st.error("Name is required.")
    elif any(m["name"] == name for m in mts):
        st.error(f"A mini-tune named '{name}' already exists. Delete it first or pick another name.")
    else:
        new_mt = {"name": name.strip(), "type": mtype, "element": element, "notes": notes, "score_mode": score_mode}
        if mtype in ("sweep_length", "sweep_position"):
            new_mt.update(start_in=float(start_in), stop_in=float(stop_in), step_in=float(step_in))
        else:
            new_mt.update(delta_in=float(delta_in), step_in=float(step_in))
        mts.append(new_mt)
        PATH.write_text(json.dumps(mts, indent=2))
        st.cache_data.clear()
        st.success(f"Saved '{name}'. {len(mts)} mini-tune(s) total.")
        st.rerun()

st.markdown("---")
st.caption(f"File: {PATH}")
