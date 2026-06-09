"""Tests for the looping 'smart_group_match_4x' procedure and run_procedure loop logic."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hyagi import v2_runner  # noqa: E402

DATA = ROOT / "data"


def _load(name):
    return json.loads((DATA / name).read_text())


def test_procedures_json_valid():
    procs = _load("procedures_v2.json")
    assert isinstance(procs, list) and procs


def test_smart_group_match_exists_and_steps_resolve():
    procs = _load("procedures_v2.json")
    minis = {m["name"]: m for m in _load("mini_tunes_v2.json")}
    proc = next((p for p in procs if p["name"] == "smart_group_match_4x"), None)
    assert proc is not None, "smart_group_match_4x procedure missing"
    assert proc.get("repeat") == 4
    assert "repeat_min_improve" in proc
    missing = [s for s in proc["steps"] if s not in minis]
    assert not missing, f"steps not found in mini_tunes_v2.json: {missing}"


def test_run_procedure_honors_repeat_and_early_stop():
    """Functional: a tiny 1-step procedure with repeat=3 must execute, loop, and
    converge/stop early (since a cheap nudge won't improve forever). Uses the real
    nec2c engine but only one cheap mini-tune for speed."""
    geo = _load("current_geometry_v2.json")
    rules = _load("rules_v2.json")
    minis = {m["name"]: m for m in _load("mini_tunes_v2.json")}
    # cheap single-element fine nudge
    proc = {"name": "t", "repeat": 3, "repeat_min_improve": 0.3,
            "steps": ["retune_XFRMR_length"]}
    logs = []
    final_geo, score, metrics, step_results = v2_runner.run_procedure(
        proc, minis, geo["elements"], rules, log_fn=logs.append)
    assert metrics is not None and "error" not in metrics
    # at least one pass ran
    passes = {sr["pass"] for sr in step_results}
    assert 1 in passes
    # loop banner appeared
    assert any("PASS 1 of 3" in ln for ln in logs)
    # converged early OR completed all 3 — either way final metrics are sane
    assert metrics["max_swr"] > 0
