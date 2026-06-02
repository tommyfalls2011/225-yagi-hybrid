"""Run page: pick one procedure, run it.
Built-ins fire their entry script. Recipes expand. Custom-stages walk stages."""
import streamlit as st
from hyagi import runner
try: from hyagi import stage_handlers  # noqa
except Exception: pass

st.set_page_config(page_title="Run", layout="wide")
st.title("Run a procedure")
st.caption("Pick one. Built-ins are standalone tunes. Recipes expand into their steps.")

data = runner.load_procs()
procs = data.get("procedures", {})

def lbl(pid, m):
    k = m.get("kind", "builtin")
    icon = {"builtin":"[B]", "recipe":"[recipe]", "custom_stages":"[mini]"}.get(k, "[?]")
    return f"{icon} {m.get('name', pid)}  ({pid})"

order_kind = ["builtin", "recipe", "custom_stages"]
ids = sorted(procs.keys(),
    key=lambda pid: (order_kind.index(procs[pid].get("kind", "builtin")),
                     procs[pid].get("name", pid).lower()))
choice = st.selectbox("Procedure", options=ids,
                      format_func=lambda pid: lbl(pid, procs[pid]))
meta = procs.get(choice, {})
kind = meta.get("kind", "builtin")

if meta.get("description"):
    st.info(meta["description"])

if kind == "recipe":
    st.markdown("**Steps:**")
    for i, sid in enumerate(meta.get("steps", []), 1):
        sm = procs.get(sid, {"name": sid})
        st.markdown(f"&nbsp;&nbsp;{i}. {lbl(sid, sm)}")
elif kind == "custom_stages":
    st.markdown("**Stages:**")
    for i, sn in enumerate(meta.get("stages", []), 1):
        st.markdown(f"&nbsp;&nbsp;{i}. `{sn}`")
elif kind == "builtin" and meta.get("stages"):
    st.markdown("**Stages:** " + " -> ".join(meta["stages"]))

# editable params (built-in only)
overrides = {}
if kind == "builtin" and meta.get("editable_params"):
    with st.expander("Parameters", expanded=True):
        for k, p in meta["editable_params"].items():
            default = p.get("default")
            label = p.get("label", k)
            if p.get("type") == "bool":
                overrides[k] = st.checkbox(label, value=bool(default), key=f"p-{k}")
            elif "choices" in p:
                idx = p["choices"].index(default) if default in p["choices"] else 0
                overrides[k] = st.selectbox(label, p["choices"], index=idx, key=f"p-{k}")
            elif isinstance(default, (int, float)):
                overrides[k] = st.number_input(label, value=default,
                    min_value=p.get("min"), max_value=p.get("max"),
                    step=p.get("step", 1.0), key=f"p-{k}")
            else:
                overrides[k] = st.text_input(label,
                    value="" if default is None else str(default), key=f"p-{k}")


# entry-template placeholders (e.g. ./run.py design {project_name})
entry_vals = {}
if kind == "builtin" and meta.get("entry"):
    import re as _re
    ph = _re.findall(r"\{(\w+)\}", meta["entry"])
    if ph:
        with st.expander("Entry placeholders", expanded=True):
            for name in ph:
                entry_vals[name] = st.text_input(name, key=f"ph-{name}")

c1, c2 = st.columns(2)
with c1: run_btn = st.button("Run", type="primary", use_container_width=True)
with c2: show_btn = st.button("Show resolved plan", use_container_width=True)

if show_btn:
    plan = runner.resolve_single(choice)
    st.code("\n".join(f"{i+1}. {lbl(pid, m)}" for i, (pid, m) in enumerate(plan)) or "(empty)")

if run_btn:
    st.divider()
    st.subheader("Live output")
    log = st.empty()
    lines = []
    plan = runner.resolve_single(choice)
    for i, (pid, m) in enumerate(plan, 1):
        lines.append("="*72)
        lines.append(f"[{i}/{len(plan)}] {lbl(pid, m)}")
        lines.append("="*72)
        log.code("\n".join(lines[-500:]), language="text")

        run_meta = dict(m)
        # fill entry placeholders
        if pid == choice and entry_vals and run_meta.get("entry"):
            e = run_meta["entry"]
            for k, v in entry_vals.items():
                e = e.replace("{" + k + "}", str(v))
            run_meta["entry"] = e
        # apply overrides only on the chosen top-level builtin
        if pid == choice and overrides and m.get("kind", "builtin") == "builtin":
            args = dict(run_meta.get("default_args", {}))
            for k, v in overrides.items():
                pdef = meta["editable_params"][k]
                flag = pdef.get("cli")
                if not flag: continue
                if isinstance(v, bool):
                    if v: args[flag] = ""
                else:
                    args[flag] = v
            run_meta["default_args"] = args

        try:
            if m.get("kind") == "custom_stages":
                gen = runner._run_custom_stages(run_meta)
            elif m.get("kind") == "error":
                gen = iter([f"[ERROR] {m.get('error')}"])
            else:
                gen = runner._run_builtin(run_meta)
            for ln in gen:
                lines.append(ln)
                log.code("\n".join(lines[-500:]), language="text")
        except Exception as e:
            lines.append(f"[EXCEPTION] {e}")
            log.code("\n".join(lines[-500:]), language="text")
    st.success("Done.")
