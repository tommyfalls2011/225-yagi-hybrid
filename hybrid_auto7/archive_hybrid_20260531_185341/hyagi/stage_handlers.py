"""Real handlers for the DIR1-shorten mini-tune. Flexible to seed shape."""
import json
from pathlib import Path
from hyagi.runner import register_stage

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "data" / "cell_learning_runs" / "best_cell_seed.json"
KB_LOG    = ROOT / "data" / "dir1_shorten_log.jsonl"
_state = {}

def _find(d, *keys, default=None):
    """Search dict (and one level of nested dicts) for any of the keys."""
    if not isinstance(d, dict): return default
    for k in keys:
        if k in d and d[k] is not None: return d[k]
    for v in d.values():
        if isinstance(v, dict):
            r = _find(v, *keys)
            if r is not None: return r
    return default

def _extract_cell(seed):
    """Pull DE/XFRMR/COUPLER fields out of any seed shape."""
    # try elements list first
    els = _find(seed, "elements")
    if isinstance(els, list):
        by = {}
        for e in els:
            name = (e.get("name") or e.get("tag") or "").upper()
            by[name] = e
        return {
            "de_pos": _find(by.get("DE",{}), "position","pos","position_in","x"),
            "de_len": _find(by.get("DE",{}), "length","length_in","len","L"),
            "x_pos":  _find(by.get("XFRMR",{}), "position","pos","position_in","x"),
            "x_len":  _find(by.get("XFRMR",{}), "length","length_in","len","L"),
            "c_pos":  _find(by.get("COUPLER",{}), "position","pos","position_in","x"),
            "c_len":  _find(by.get("COUPLER",{}), "length","length_in","len","L"),
            "ref_len": _find(by.get("REF",{}) or by.get("REFL",{}) or by.get("REFLECTOR",{}),
                             "length","length_in","len","L"),
        }
    # flat keys (handles the actual *_position_in / *_length_in shape too)
    return {
        "de_pos": _find(seed, "de_position_in","de_pos","DE_pos","de_position"),
        "de_len": _find(seed, "de_length_in","de_len","DE_len","de_length"),
        "x_pos":  _find(seed, "xfrmr_position_in","x_pos","xfrmr_pos","X_pos"),
        "x_len":  _find(seed, "xfrmr_length_in","x_len","xfrmr_len","X_len"),
        "c_pos":  _find(seed, "coupler_position_in","c_pos","coupler_pos","C_pos"),
        "c_len":  _find(seed, "coupler_length_in","c_len","coupler_len","C_len"),
        "ref_len":_find(seed, "ref_length_in","ref_len","reflector_len","REF_len"),
    }

def _reset():
    _state.clear()
    _state.update({"cell": None, "directors": [], "ref_len": None,
        "history": [], "best": None, "stop_reason": None,
        "iter": 0, "MAX_ITERS": 30, "STEP_IN": 0.5,
        "SWR_RISE_TOL": 0.1, "FB_DROP_TOL": 1.0})

@register_stage("lock_de_xfrmr_coupler")
def _lock():
    _reset()
    if not SEED_PATH.exists():
        raise RuntimeError(f"No cell seed at {SEED_PATH}.")
    seed = json.loads(SEED_PATH.read_text())
    cell = _extract_cell(seed)
    _state["cell"] = cell
    missing = [k for k in ("de_len","x_len","c_len") if cell.get(k) is None]
    if missing:
        return [f"ERROR: missing keys in seed: {missing}",
                f"seed top-level keys: {list(seed.keys())}"]
    _state["ref_len"] = cell.get("ref_len") or cell["de_len"] * 1.045
    return [f"loaded cell from {SEED_PATH.name}",
        f"  DE  @ {cell['de_pos']}  L={cell['de_len']:.3f}",
        f"  XF  @ {cell['x_pos']}   L={cell['x_len']:.3f}",
        f"  CPL @ {cell['c_pos']}   L={cell['c_len']:.3f}",
        f"  REF L={_state['ref_len']:.3f} (seeded)",
        "cell LOCKED -- only DIR1 will move"]

@register_stage("read_current_dir1_length")
def _read():
    c = _state["cell"]["c_len"]
    if not _state["directors"]:
        _state["directors"] = [c - 0.5]
    return [f"COUPLER={c:.3f}  DIR1 start={_state['directors'][0]:.3f}"]

@register_stage("step_dir1_down_0p5in")
def _step():
    _state["iter"] += 1
    if _state["iter"] >= _state["MAX_ITERS"]:
        _state["stop_reason"] = "MAX_ITERS"; return [f"STOP: MAX_ITERS"]
    _state["directors"][0] -= _state["STEP_IN"]
    return [f"step {_state['iter']}: DIR1={_state['directors'][0]:.3f}"]

@register_stage("guard_dir1_lt_coupler")
def _g1():
    d1 = _state["directors"][0]; c = _state["cell"]["c_len"]
    if d1 >= c:
        _state["stop_reason"] = f"DIR1 {d1:.3f} >= COUPLER {c:.3f}"
        return [f"GUARD: {_state['stop_reason']}"]
    return [f"ok: DIR1 {d1:.3f} < COUPLER {c:.3f}"]

@register_stage("guard_strict_progression")
def _g2():
    d = _state["directors"]
    for i in range(1, len(d)):
        if d[i] >= d[i-1]:
            _state["stop_reason"] = f"progression broken DIR{i+1}"
            return [f"GUARD: {_state['stop_reason']}"]
    return [f"ok: {' > '.join(f'{x:.2f}' for x in d) or '(only DIR1)'}"]

@register_stage("eval_nec_score")
def _eval():
    if _state.get("stop_reason"): return ["skipped"]
    score, swr, fb, gain = _evaluate()
    _state["history"].append({"iter": _state["iter"],
        "dir1": _state["directors"][0],
        "score": score, "swr": swr, "fb": fb, "gain": gain})
    return [f"score={score:.1f} maxSWR={swr:.3f} F/B={fb:.2f} gain={gain:.2f}"]

@register_stage("compare_to_previous")
def _cmp():
    h = _state["history"]
    if not h: return ["no history"]
    last = h[-1]
    b = _state["best"]
    if b is None or last["score"] > b["score"]:
        _state["best"] = dict(last)
        return [f"NEW BEST score={last['score']:.1f} DIR1={last['dir1']:.3f}"]
    return [f"no improvement (best={b['score']:.1f} @ DIR1={b['dir1']:.3f})"]

@register_stage("loop_until_swr_rises_or_fb_drops")
def _loop():
    out = ["entering loop"]
    while True:
        if _state.get("stop_reason"):
            out.append(f"loop stop: {_state['stop_reason']}"); break
        out.append(f"--- iter {_state['iter']+1} ---")
        out += _step()
        if _state.get("stop_reason"): break
        out += _g1()
        if _state.get("stop_reason"): break
        out += _g2()
        if _state.get("stop_reason"): break
        out += _eval()
        out += _cmp()
        h = _state["history"]
        if len(h) >= 2:
            if h[-1]["swr"] - h[-2]["swr"] > _state["SWR_RISE_TOL"]:
                _state["stop_reason"] = "SWR rose"; out.append("STOP: SWR rose"); break
            if h[-2]["fb"] - h[-1]["fb"] > _state["FB_DROP_TOL"]:
                _state["stop_reason"] = "F/B dropped"; out.append("STOP: F/B dropped"); break
    return out

@register_stage("record_best_to_kb")
def _rec():
    b = _state.get("best")
    if not b: return ["no best to record"]
    if b.get("score", -9e9) < -500:
        return [f"best is sentinel ({b['score']}) -- not recording (eval failed)"]
    KB_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(KB_LOG, "a") as f:
        f.write(json.dumps({"dir1_shorten": _state["best"]}) + "\n")
    return [f"recorded to {KB_LOG.name}: {_state['best']}"]

def _evaluate():
    """Real NEC eval mirroring learn_cell_only.py pattern."""
    try:
        from hyagi.config import AntennaConfig, frange
        from hyagi.model import Element
        from hyagi.engine import NecppEngine
        from hyagi.physics import summarize
        from hyagi.pattern import evaluate_pattern_for_elements
        from hyagi.cell_rules import CellRulesViolation

        cell = _state["cell"]; d1 = _state["directors"][0]
        # build a minimal hybrid: REF + XFRMR + DE + COUPLER + DIR1
        elements = [
            Element("REF",     0.0,             _state["ref_len"]),
            Element("XFRMR",   cell["x_pos"],   cell["x_len"]),
            Element("DE",      cell["de_pos"],  cell["de_len"]),
            Element("COUPLER", cell["c_pos"],   cell["c_len"]),
            Element("DIR1",    cell["c_pos"] + 12.0, d1),
        ]
        ant = AntennaConfig()
        engine = NecppEngine()
        freqs = frange(26.965, 27.405, 0.01)

        results = engine.evaluate(elements, ant, freqs)
        summary = summarize(results)
        max_swr = float(summary.max_swr)
        # score: prioritise low SWR, then gain
        score = 10000.0 - (max_swr - 1.0) * 1000.0

        # pattern -> gain + F/B
        try:
            patt = evaluate_pattern_for_elements(elements, freq_mhz=27.185, ant=ant)
            gain = float(getattr(patt, "real_gain_dbi",
                          getattr(patt, "forward_gain_dbi", 0.0)) or 0.0)
            fb = float(getattr(patt, "front_back_db",
                        getattr(patt, "fb_db", 0.0)) or 0.0)
            # bonus for gain & F/B
            score += gain * 100.0 + fb * 20.0
        except Exception as pe:
            gain, fb = 0.0, 0.0
            print(f"[eval] pattern warn: {pe}")

        return (score, max_swr, fb, gain)
    except CellRulesViolation as cv:
        return (-9999.0, 9.99, 0.0, 0.0)
    except Exception as e:
        print(f"[eval] ERROR: {e}")
        return (-999.0, 9.99, 0.0, 0.0)
