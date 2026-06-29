"""Resolves a Procedures lane into a flat plan and executes it.
- builtin   -> runs the entry script via subprocess
- recipe    -> expands into its steps (recursively)
- custom_stages -> walks the stages list, calls any registered handler,
                   logs TODO for unmapped stages
"""
import json, subprocess, shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCS_PATH = ROOT / "data" / "procedures.json"

STAGE_REGISTRY = {}
def register_stage(name):
    def deco(fn): STAGE_REGISTRY[name] = fn; return fn
    return deco

def load_procs(): return json.loads(PROCS_PATH.read_text())

def resolve_plan(lane="hybrid"):
    data = load_procs()
    procs = data.get("procedures", {})
    entries = data.get(lane, [])
    plan = []
    def add(pid, path=()):
        if pid in path:
            plan.append((pid, {"kind":"error","name":pid,
                "error": f"cycle: {' -> '.join(path+(pid,))}"})); return
        meta = procs.get(pid)
        if not meta:
            plan.append((pid, {"kind":"error","name":pid,
                "error": f"unknown id: {pid}"})); return
        if meta.get("kind") == "recipe":
            for sub in meta.get("steps", []): add(sub, path+(pid,))
        else:
            plan.append((pid, meta))
    for e in entries:
        if e.get("enabled", True): add(e["id"])
    return plan

def run_plan(lane="hybrid"):
    plan = resolve_plan(lane)
    if not plan:
        yield "[runner] no enabled procedures in lane."
        return
    yield f"[runner] resolved plan ({len(plan)} steps):"
    for i, (pid, m) in enumerate(plan, 1):
        yield f"  {i:>2}. {m.get('name', pid)}  ({pid})  [{m.get('kind','?')}]"
    yield ""
    for i, (pid, m) in enumerate(plan, 1):
        kind = m.get("kind", "builtin")
        yield "=" * 72
        yield f"[step {i}/{len(plan)}] {m.get('name', pid)}  ({pid})  kind={kind}"
        yield "=" * 72
        try:
            if kind == "error":
                yield f"[ERROR] {m.get('error')}"
            elif kind == "custom_stages":
                yield from _run_custom_stages(m)
            else:  # builtin
                yield from _run_builtin(m)
        except Exception as e:
            yield f"[runner] EXCEPTION in {pid}: {e}"
        yield ""
    yield "[runner] DONE"

def _run_builtin(meta):
    entry = meta.get("entry")
    if not entry:
        yield "[builtin] no 'entry' set -- nothing to execute"; return
    # refuse if any {placeholder} is still unfilled
    import re as _re
    unfilled = _re.findall(r"\{(\w+)\}", entry)
    if unfilled:
        yield f"[builtin] entry has unfilled placeholders: {unfilled}"
        yield "         -> fill them on the Run page under 'Entry placeholders'"
        return
    parts = [entry]
    for k, v in (meta.get("default_args") or {}).items():
        parts.append(f"{k} {shlex.quote(str(v))}")
    cmd = " ".join(parts)
    yield f"[exec] cd {ROOT} && {cmd}"
    py = str(Path.home() / "ai-env/bin/python3")
    # if entry starts with ./ and ends with .py, force ai-env python
    if entry.startswith("./") and entry.endswith(".py"):
        cmd = cmd.replace(entry, f"{py} {entry[2:]}", 1)
    proc = subprocess.Popen(["bash","-c", f"cd {ROOT} && {cmd}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
    for line in iter(proc.stdout.readline, ''):
        yield line.rstrip()
    proc.wait()
    yield f"[exec] exit code {proc.returncode}"
    # auto-attach full report on latest .nec file
    try:
        from hyagi.full_report import report_for_nec
        import glob, os
        # prefer real sweep files (learn_cell_best, full_hybrid_*) over single-freq pattern files
        all_ncs = sorted(glob.glob(str(ROOT/"models/*.nec")), key=os.path.getmtime, reverse=True)
        ncs = [n for n in all_ncs if "pattern_" not in os.path.basename(n)] or all_ncs
        if ncs:
            yield ""
            yield report_for_nec(ncs[0], title=f"FULL REPORT: {os.path.basename(ncs[0])}")
    except Exception as e:
        yield f"[report] skipped: {e}"

def _run_custom_stages(meta):
    stages = meta.get("stages") or []
    yield f"[custom_stages] {len(stages)} stages"
    for j, name in enumerate(stages, 1):
        yield f"  ({j}/{len(stages)}) {name}"
        h = STAGE_REGISTRY.get(name)
        if h is None:
            yield f"    [TODO] no handler registered for '{name}' "
            yield f"           -> add it in hyagi/stage_handlers.py via @register_stage('{name}')"
        else:
            try:
                out = h() or []
                for ln in out: yield f"    {ln}"
            except Exception as e:
                yield f"    [STAGE ERROR] {e}"


def resolve_single(proc_id):
    """Resolve a single procedure (expand recipes)."""
    data = load_procs()
    procs = data.get("procedures", {})
    plan = []
    def add(pid, path=()):
        if pid in path:
            plan.append((pid, {"kind":"error","name":pid,
                "error": f"cycle: {' -> '.join(path+(pid,))}"})); return
        meta = procs.get(pid)
        if not meta:
            plan.append((pid, {"kind":"error","name":pid,
                "error": f"unknown id: {pid}"})); return
        if meta.get("kind") == "recipe":
            for sub in meta.get("steps", []): add(sub, path+(pid,))
        else:
            plan.append((pid, meta))
    add(proc_id)
    return plan
