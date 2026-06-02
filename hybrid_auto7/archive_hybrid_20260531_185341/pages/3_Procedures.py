"""
Procedures page:
  - Reorder + on/off existing procedures (hybrid / yagi lanes)
  - Build your own CUSTOM RECIPE (sequence of existing procedures)
  - Or define a CUSTOM STAGE LIST (your own stage names, runner-ready)
"""
import json, re
from pathlib import Path
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data" / "procedures.json"

def load(): return json.loads(P.read_text())
def save(d): P.write_text(json.dumps(d, indent=2))

st.set_page_config(page_title="Procedures", layout="wide")
st.title("Tuning Procedures")
st.caption("Reorder built-ins, or write your own. Order = run order. Hybrid is primary.")

data = load()
procs = data.setdefault("procedures", {})

def ensure_lane(lane):
    if lane not in data or not isinstance(data[lane], list):
        data[lane] = []
    have = {e.get("id") for e in data[lane]}
    if lane == "hybrid":
        for pid in procs:
            if pid not in have:
                data[lane].append({"id": pid, "enabled": True})

ensure_lane("hybrid"); ensure_lane("yagi")

tabs = st.tabs(["Hybrid lane", "Yagi lane", "+ New custom"])

# =========================================================================
# LANE RENDER
# =========================================================================
def render_lane(lane, container):
    entries = data.get(lane, [])
    with container:
        if not entries:
            st.info(f"No procedures in {lane}.")
            return
        for i, e in enumerate(entries):
            pid = e.get("id")
            meta = procs.get(pid, {})
            c1, c2, c3, c4, c5, c6 = st.columns([0.5, 5, 1, 0.7, 0.7, 0.7])
            with c1: st.markdown(f"**{i+1}**")
            with c2:
                kind = meta.get("kind", "builtin")
                badge = {"builtin": "🟦", "recipe": "🟩", "custom_stages": "🟧"}.get(kind, "🟦")
                st.markdown(f"{badge} **{meta.get('name', pid)}** `{pid}`")
                desc = meta.get("description", "")
                if desc:
                    st.caption(desc[:220] + ("..." if len(desc) > 220 else ""))
                if kind == "recipe":
                    st.caption("recipe: " + " -> ".join(meta.get("steps", [])))
                elif kind == "custom_stages":
                    st.caption("stages: " + " -> ".join(meta.get("stages", [])))
                else:
                    stages = meta.get("stages") or []
                    if stages: st.caption("stages: " + " -> ".join(stages))
            with c3:
                e["enabled"] = st.toggle("on", value=bool(e.get("enabled", True)),
                                         key=f"en-{lane}-{i}")
            with c4:
                if st.button("up", key=f"up-{lane}-{i}", disabled=(i == 0),
                             use_container_width=True):
                    entries[i-1], entries[i] = entries[i], entries[i-1]
                    data[lane] = entries; save(data); st.rerun()
            with c5:
                if st.button("dn", key=f"dn-{lane}-{i}",
                             disabled=(i == len(entries)-1),
                             use_container_width=True):
                    entries[i+1], entries[i] = entries[i], entries[i+1]
                    data[lane] = entries; save(data); st.rerun()
            with c6:
                # delete only allowed for user-made (recipe / custom_stages)
                if procs.get(pid, {}).get("kind") in ("recipe", "custom_stages"):
                    if st.button("del", key=f"del-{lane}-{i}",
                                 use_container_width=True):
                        del entries[i]
                        procs.pop(pid, None)
                        data[lane] = entries; save(data); st.rerun()
            st.divider()

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button(f"Save {lane} order", type="primary",
                         use_container_width=True, key=f"sv-{lane}"):
                data[lane] = entries; save(data); st.success("Saved.")
        with b2:
            if st.button(f"Show active {lane} run order",
                         use_container_width=True, key=f"sh-{lane}"):
                active = [e for e in entries if e.get("enabled")]
                lines = [f"{i+1}. {procs.get(e['id'],{}).get('name', e['id'])}  ({e['id']})"
                         for i, e in enumerate(active)]
                st.code("\n".join(lines) or "(none enabled)")
        with b3:
            if st.button(f"Reset {lane} to defaults",
                         use_container_width=True, key=f"rs-{lane}"):
                if lane == "hybrid":
                    builtins = [pid for pid, m in procs.items()
                                if m.get("kind", "builtin") == "builtin"]
                    data[lane] = [{"id": pid, "enabled": True} for pid in builtins]
                else:
                    data[lane] = []
                save(data); st.rerun()

render_lane("hybrid", tabs[0])
render_lane("yagi",   tabs[1])

# =========================================================================
# + NEW CUSTOM TAB
# =========================================================================
def slug(s):
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s.strip().lower()).strip("_")
    return s or "custom_proc"

with tabs[2]:
    st.subheader("Create your own procedure")
    st.caption("Two flavors. Both end up in the Hybrid lane immediately.")

    mode = st.radio("Type", ["Recipe (combine existing procedures)",
                             "Custom stages (your own stage list)"],
                    horizontal=False)

    name = st.text_input("Procedure name", "My roof tune")
    desc = st.text_area("Description", "Custom tune order I want the rig to follow.")

    if mode.startswith("Recipe"):
        st.markdown("**Pick procedures in run order:**")
        kind_icon = {"builtin": "[B]", "custom_stages": "[mini]", "recipe": "[recipe]"}
        # show mini-tunes first so they're easy to grab when chaining
        order_kind = ["custom_stages", "recipe", "builtin"]
        sorted_ids = sorted(procs.keys(),
            key=lambda pid: (order_kind.index(procs[pid].get("kind", "builtin")),
                             procs[pid].get("name", pid).lower()))
        choices = [f"{kind_icon.get(procs[pid].get('kind','builtin'),'[B]')} "
                   f"{pid}  --  {procs[pid].get('name', pid)}"
                   for pid in sorted_ids]
        picked = st.multiselect(
            "Steps (order = order picked)", choices,
            help="Click in the order you want them to run. "
                 "[mini] = your custom-stage mini-tunes, [recipe] = nested recipe, [B] = built-in.")
        steps = [c.split("  --  ")[0].split(" ", 1)[1] for c in picked]
        st.code("Recipe = " + " -> ".join(steps) if steps else "(no steps yet)")

        if st.button("Create recipe", type="primary", disabled=not steps):
            pid = slug(name)
            base = pid; n = 2
            while pid in procs:
                pid = f"{base}_{n}"; n += 1
            procs[pid] = {
                "kind": "recipe",
                "name": name,
                "description": desc,
                "steps": steps,
                "category": "custom",
            }
            data["hybrid"].append({"id": pid, "enabled": True})
            save(data)
            st.success(f"Created recipe '{pid}'. Switch to Hybrid lane to see it.")
            st.rerun()

    else:
        st.markdown("**Type your stage names, one per line, in order.**")
        st.caption("Free-form for now. The runner will execute known stages and "
                   "report any unknown stage so you can map it.")
        text = st.text_area("Stages (one per line)",
            "tune_de_first\ntune_xfrmr_to_50ohm\nadd_reflector\nwalk_directors_strict",
            height=180)
        entry = st.text_input("Optional: entry script (leave blank to use runner)",
                              "", placeholder="./learn_cell_only.py")
        stages = [ln.strip() for ln in text.splitlines() if ln.strip()]
        st.code("Stages = " + " -> ".join(stages) if stages else "(no stages yet)")

        if st.button("Create custom-stages procedure", type="primary", disabled=not stages):
            pid = slug(name)
            base = pid; n = 2
            while pid in procs:
                pid = f"{base}_{n}"; n += 1
            procs[pid] = {
                "kind": "custom_stages",
                "name": name,
                "description": desc,
                "stages": stages,
                "entry": entry or None,
                "category": "custom",
            }
            data["hybrid"].append({"id": pid, "enabled": True})
            save(data)
            st.success(f"Created custom-stages procedure '{pid}'. Hybrid lane has it.")
            st.rerun()

st.divider()
with st.expander("Raw procedures.json (live)"):
    st.code(P.read_text(), language="json")
