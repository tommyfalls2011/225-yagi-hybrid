"""v2 Procedures page — chain mini-tunes in user-chosen order."""
import json, pathlib
import streamlit as st

st.set_page_config(page_title="Procedures", layout="wide")
st.title("Procedures")
st.caption("Chain mini-tunes in any order. Different orders = different results. Run page picks one procedure and executes it end-to-end.")

PROC_PATH = pathlib.Path.home() / "scripts/hybrid_auto7/data/procedures_v2.json"
MINI_PATH = pathlib.Path.home() / "scripts/hybrid_auto7/data/mini_tunes_v2.json"

@st.cache_data(ttl=2)
def load_procs():
    return json.loads(PROC_PATH.read_text())

@st.cache_data(ttl=2)
def load_minis():
    return json.loads(MINI_PATH.read_text())

procs = load_procs()
minis = load_minis()
mini_names = [m["name"] for m in minis]

if not mini_names:
    st.warning("No mini-tunes defined yet. Go to the Mini-Tunes page first.")
    st.stop()

st.subheader("Existing procedures")
if not procs:
    st.info("No procedures yet.")
else:
    for i, pr in enumerate(list(procs)):
        with st.expander(f"{pr.get('name','(unnamed)')}  —  {len(pr.get('steps',[]))} step(s)",
                         expanded=False):
            c1, c2 = st.columns([5, 1])
            with c1:
                for j, step in enumerate(pr.get("steps", []), start=1):
                    found = next((m for m in minis if m["name"] == step), None)
                    if found:
                        st.markdown(f"**{j}.** `{step}`  —  {found.get('type', found.get('param', '?'))} on {found.get('element', found.get('targets', '-'))}")
                    else:
                        st.markdown(f"**{j}.** `{step}`  —  ⚠️ mini-tune not found")
                st.caption(pr.get("notes", ""))
            with c2:
                if st.button("Delete", key=f"delp_{i}", use_container_width=True):
                    procs.pop(i)
                    PROC_PATH.write_text(json.dumps(procs, indent=2))
                    st.cache_data.clear()
                    st.rerun()

st.markdown("---")
st.subheader("Create / edit procedure")

with st.form("proc_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        pname = st.text_input("Procedure name (unique)", value="my_new_procedure")
    with c2:
        pnotes = st.text_area("Notes (optional)", height=80)

    st.markdown("**Steps** — pick mini-tunes in the order you want them executed:")
    selected = st.multiselect(
        "Available mini-tunes (selection order = execution order)",
        options=mini_names,
        default=[],
        help="The order you click them is the order they run. Click a mini-tune again to remove it."
    )

    if selected:
        st.markdown("**Preview execution order:**")
        for j, step in enumerate(selected, start=1):
            m = next((mm for mm in minis if mm["name"] == step), None)
            if m:
                st.markdown(f"&nbsp;&nbsp;**{j}.** `{step}`  —  *{m.get('type', m.get('param', '?'))}* on **{m.get('element', m.get('targets', '-'))}**")

    submitted = st.form_submit_button("Save procedure", type="primary")

if submitted:
    if not pname.strip():
        st.error("Name is required.")
    elif any(p["name"] == pname for p in procs):
        st.error(f"A procedure named '{pname}' already exists. Delete it first or pick another name.")
    elif not selected:
        st.error("Pick at least one mini-tune.")
    else:
        new_proc = {"name": pname.strip(), "steps": selected, "notes": pnotes}
        procs.append(new_proc)
        PROC_PATH.write_text(json.dumps(procs, indent=2))
        st.cache_data.clear()
        st.success(f"Saved '{pname}'. {len(procs)} procedure(s) total.")
        st.rerun()

st.markdown("---")
st.caption(f"File: {PROC_PATH}")
