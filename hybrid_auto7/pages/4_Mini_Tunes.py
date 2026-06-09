"""v2 Mini-Tunes page — create/edit reusable tune primitives."""
import json
import pathlib
import streamlit as st

st.set_page_config(page_title="Mini-Tunes", layout="wide")
st.title("Mini-Tunes")
st.caption("Reusable tune primitives. Single-element ones move ONE element; GROUP ones "
           "lock everything else and move a group of elements together (evenly). Chain them on the Procedures page.")

PATH       = pathlib.Path.home() / "scripts/hybrid_auto7/data/mini_tunes_v2.json"
RULES_PATH = pathlib.Path.home() / "scripts/hybrid_auto7/data/rules_v2.json"
GEO_PATH   = pathlib.Path.home() / "scripts/hybrid_auto7/data/current_geometry_v2.json"

@st.cache_data(ttl=2)
def load():
    return json.loads(PATH.read_text())

@st.cache_data(ttl=2)
def load_rules():
    return json.loads(RULES_PATH.read_text())

@st.cache_data(ttl=2)
def load_geo_names():
    try:
        return [e["name"] for e in json.loads(GEO_PATH.read_text())["elements"]]
    except Exception:
        return []

mts   = load()
rules = load_rules()
# Element roster is NOT hardcoded to the rules file: it follows the geometry you
# build (Auto-Learn -> Build geometry, 0-14 directors) and always offers the full
# hybrid roster up to 18 elements (REF + XFRMR + DE + COUPLER + DIR1..DIR14).
_geo_names = load_geo_names()
_full_roster = ["REF", "XFRMR", "DE", "COUPLER"] + [f"DIR{i}" for i in range(1, 15)]
elements_list = list(dict.fromkeys(_geo_names + _full_roster))
st.caption(f"Element roster: {len(elements_list)} elements available "
           f"(current geometry has {len(_geo_names) or '—'}). "
           "Set the element count on **Auto-Learn → Build geometry**. "
           "A mini-tune that targets an element not in the loaded geometry is skipped automatically.")

TYPES = {
    "sweep_length":   "Sweep ONE element length across [start..stop] step",
    "sweep_position": "Sweep ONE element position across [start..stop] step",
    "nudge_length":   "Fine-tune ONE length: current ± delta, in steps",
    "nudge_position": "Fine-tune ONE position: current ± delta, in steps",
    "group_position": "GROUP move: shift several chosen elements' POSITIONS together ± delta (others locked)",
    "group_length":   "GROUP move: shift several chosen elements' LENGTHS together ± delta (others locked)",
    "group_window_position": "AUTO-TILE: slide a window of N directors across the whole array (DIR1+2, DIR3+4, ...), POSITION. Auto-fits any director count.",
    "group_window_length":   "AUTO-TILE: slide a window of N directors across the whole array, LENGTH.",
}
GROUP_TYPES = ("group_position", "group_length")
WINDOW_TYPES = ("group_window_position", "group_window_length")
SCORE_MODES = ["match", "resonance", "composite"]

def _label(mt):
    t = mt.get("type")
    if t in WINDOW_TYPES:
        w = mt.get("window")
        wm = f"-{mt['window_max']}" if mt.get("window_max") else ""
        return f"{mt.get('name','(unnamed)')}  —  {t} (window {w}{wm} directors, auto)"
    if t in GROUP_TYPES:
        return f"{mt.get('name','(unnamed)')}  —  {t} on [{', '.join(mt.get('elements', []))}]"
    return f"{mt.get('name','(unnamed)')}  —  {t or '?'} on {mt.get('element','?')}"

st.subheader("Existing mini-tunes")
if not mts:
    st.info("No mini-tunes yet. Use the form below to create one.")
else:
    for i, mt in enumerate(list(mts)):
        with st.expander(_label(mt), expanded=False):
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

# Type is OUTSIDE the form so the fields below react to it immediately.
mtype = st.selectbox("Type", list(TYPES.keys()), key="mt_type_v2",
                     help="\n".join([f"{k}: {v}" for k, v in TYPES.items()]))
st.caption(TYPES[mtype])
is_group = mtype in GROUP_TYPES
is_window = mtype in WINDOW_TYPES

with st.form("mini_tune_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        name = st.text_input("Name (unique)", value="my_new_mini_tune",
                              help="Used by Procedures to reference this mini-tune.")
        if is_window:
            st.caption("Auto-tile needs no element list — it discovers the directors in the "
                       "loaded geometry and tiles them itself.")
            element = None
            group_elements = None
        elif is_group:
            group_elements = st.multiselect(
                "Elements in the group (moved together; everything else is LOCKED)",
                elements_list,
                help="e.g. pick DIR2 + DIR3 to slide them as a pair; or REF/XFRMR/DE/COUPLER "
                     "to move the whole cell; or add the directors too.")
            element = None
        else:
            element = st.selectbox("Element", elements_list)
            group_elements = None
    with c2:
        notes = st.text_area("Notes (optional)", height=80)
        score_mode = st.selectbox("Score mode", SCORE_MODES,
            help="match = drive X to 0 + lowest SWR / highest return loss (best for group/window moves). "
                 "resonance = |X|~0, R~50, SWR low. composite = gain+F/B+SWR.",
            key="score_mode_v2")

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
        window = window_max = None
    elif is_window:
        w1, w2, w3, w4 = st.columns(4)
        with w1:
            window = st.number_input("window (dirs at once)", min_value=1, max_value=14, value=2, step=1)
        with w2:
            window_max = st.number_input("grow to (optional)", min_value=1, max_value=14, value=int(window), step=1,
                                         help="Set higher than 'window' to grow the window each pass: 2,3,4...")
        with w3:
            delta_in = st.number_input("delta (± offset)", value=8.0, step=0.5)
        with w4:
            step_in  = st.number_input("step", value=0.5, step=0.05, format="%.2f")
        start_in = stop_in = None
    else:
        p1, p2 = st.columns(2)
        with p1:
            delta_in = st.number_input("delta (± offset moved together)" if is_group
                                       else "delta (± from current)",
                                       value=6.0 if is_group else 4.0, step=0.5)
        with p2:
            step_in  = st.number_input("step", value=0.5 if is_group else 0.25,
                                       step=0.05, format="%.2f")
        start_in = stop_in = None
        window = window_max = None

    submitted = st.form_submit_button("Save mini-tune", type="primary")

if submitted:
    if not name.strip():
        st.error("Name is required.")
    elif is_group and not group_elements:
        st.error("Pick at least one element for the group.")
    elif any(m["name"] == name for m in mts):
        st.error(f"A mini-tune named '{name}' already exists. Delete it first or pick another name.")
    else:
        new_mt = {"name": name.strip(), "type": mtype, "notes": notes, "score_mode": score_mode}
        if is_window:
            new_mt.update(window=int(window), delta_in=float(delta_in), step_in=float(step_in))
            if int(window_max) > int(window):
                new_mt["window_max"] = int(window_max)
        elif is_group:
            new_mt["elements"] = list(group_elements)
            new_mt.update(delta_in=float(delta_in), step_in=float(step_in))
        else:
            new_mt["element"] = element
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
