"""REF length tuner. Constrained: DE_len + 4 <= REF_len <= DE_len + 24."""
from .project import load_project, save_project, apply_element_overrides, set_element_override
from .dynamic import generate_starting_model
from .project_cell_tuner import (
    _make_ant, _run_candidate, score_candidate,
    override_element, find_element,
)
from .model import validate_elements
from .config import frange
from .engine import NecppEngine


def _to_elements(generated):
    from .model import Element
    return [Element(g.name, g.position_in, g.length_in) for g in generated]


def tune_project_ref(project_name, level="quick"):
    """Sweep REF length in [DE_length+4, DE_length+24] inches, save best as override."""
    cfg = load_project(project_name)
    if cfg.mode != "hybrid":
        return {"skipped": True, "reason": "non-hybrid mode"}

    generated = generate_starting_model(
        element_count=cfg.element_count, mode=cfg.mode,
        freq_start_mhz=cfg.freq_start_mhz, freq_stop_mhz=cfg.freq_stop_mhz,
        boom_length_ft=cfg.boom_length_ft,
    )
    apply_element_overrides(cfg, generated)
    elements = _to_elements(generated)

    de = find_element(elements, "DE")
    ref = find_element(elements, "REF")
    de_len = float(de.length_in)
    lo = de_len + 4.0
    hi = de_len + 24.0
    step = 0.5 if level == "deep" else (1.0 if level == "normal" else 2.0)

    ant = _make_ant(cfg)
    engine = NecppEngine()
    freqs = frange(cfg.freq_start_mhz, cfg.freq_stop_mhz, 0.01)
    validate_elements(elements, ant)

    print()
    print("REF length tuner")
    print("================")
    print(f"DE length:         {de_len:.3f} in")
    print(f"REF range:         [{lo:.3f}, {hi:.3f}] in   step {step}")
    print(f"Current REF:       {ref.length_in:.3f} in")

    # Sweep
    candidates = []
    v = lo
    while v <= hi + 1e-6:
        candidates.append(round(v, 3))
        v += step

    best_score = None
    best_len = float(ref.length_in)
    best_summary = None

    for cand_len in candidates:
        cand = override_element(elements, "REF", length=cand_len)
        try:
            res = _run_candidate(cand, ant, freqs, engine)
            summary = res[0] if isinstance(res, tuple) else res
        except Exception as e:
            print(f"  REF {cand_len:7.3f} in  FAILED ({e})")
            continue
        sc = score_candidate(summary)
        marker = ""
        if best_score is None or sc > best_score:
            best_score = sc
            best_len = cand_len
            best_summary = summary
            marker = "  <-- best"
        print(f"  REF {cand_len:7.3f} in  score={sc:9.2f}  maxSWR={summary.max_swr:.3f}  avgSWR={summary.avg_swr:.3f}{marker}")

    if best_summary is None:
        print("REF tuner: no valid candidates, leaving REF unchanged.")
        return {"changed": False}

    # REF_POS_SWEEP_v1: after locking length, sweep REF position forward 0..15 in
    print()
    print("REF position tuner (0..15 in forward of boom front)")
    print("===================================================")
    print(f"Locked REF length: {best_len:.3f} in")
    pos_step = 0.5 if level == "deep" else (1.0 if level == "normal" else 2.0)
    pos_values = []
    pv = 0.0
    while pv <= 15.0 + 1e-6:
        pos_values.append(round(pv, 3))
        pv += pos_step

    best_pos = 0.0
    pos_best_score = best_score
    elements_locked_len = override_element(elements, "REF", length=best_len)
    for cand_pos in pos_values:
        cand = override_element(elements_locked_len, "REF", position=cand_pos)
        try:
            res = _run_candidate(cand, ant, freqs, engine)
            summary = res[0] if isinstance(res, tuple) else res
        except Exception as e:
            print(f"  REFpos {cand_pos:6.2f} in  FAILED ({e})")
            continue
        sc = score_candidate(summary)
        marker = ""
        if sc > pos_best_score:
            pos_best_score = sc
            best_pos = cand_pos
            best_summary = summary
            marker = "  <-- best"
        print(f"  REFpos {cand_pos:6.2f} in  score={sc:9.2f}  maxSWR={summary.max_swr:.3f}  avgSWR={summary.avg_swr:.3f}{marker}")

    # REF_BACK_SWEEP_v1: if forward stuck at 0, try equivalent "backward" by
    # keeping REF at 0 and shifting all other elements forward.
    back_shift = 0.0
    if best_pos < 0.25:
        print()
        print("REF backward equivalence sweep (forward unimproved)")
        print("====================================================")
        print("Keeping REF at 0, shifting cell+directors forward.")
        from .model import Element as _El
        bv = pos_step
        back_values = []
        while bv <= 8.0 + 1e-6:
            back_values.append(round(bv, 3))
            bv += pos_step
        for back_in in back_values:
            shifted = []
            for e in elements_locked_len:
                if e.name.upper() == "REF":
                    shifted.append(_El(e.name, 0.0, best_len))
                else:
                    shifted.append(_El(e.name, e.position_in + back_in, e.length_in))
            # REF_BACK_BOOM_v1: extend boom temporarily to accommodate shifted elements
            import copy as _copy
            _ant_ext = _copy.copy(ant)
            _ant_ext.boom_length_in = ant.boom_length_in + back_in + 1.0
            try:
                res = _run_candidate(shifted, _ant_ext, freqs, engine)
                summary = res[0] if isinstance(res, tuple) else res
            except Exception as exc:
                print(f"  REFback {back_in:5.2f} in  SKIPPED ({type(exc).__name__})")
                continue
            sc = score_candidate(summary)
            marker = ""
            if sc > pos_best_score:
                pos_best_score = sc
                back_shift = back_in
                best_pos = -back_in
                best_summary = summary
                marker = "  <-- best"
            print(f"  REFback {back_in:5.2f} in  score={sc:9.2f}  maxSWR={summary.max_swr:.3f}  avgSWR={summary.avg_swr:.3f}{marker}")

    # Persist: if backward won, REF stays at 0 and other elements shift forward
    if back_shift > 0:
        # REF_BACK_BOOM_v1: extend the project boom to accommodate shifted directors
        _cfg_save = load_project(project_name)
        _old_boom_ft = float(_cfg_save.boom_length_ft)
        _new_boom_ft = (ant.boom_length_in + back_shift + 1.0) / 12.0
        if _new_boom_ft > _old_boom_ft:
            _cfg_save.boom_length_ft = _new_boom_ft
            save_project(_cfg_save)
            print(f"  [boom extended] {_old_boom_ft:.3f} ft -> {_new_boom_ft:.3f} ft "
                  f"(+{(_new_boom_ft - _old_boom_ft)*12:.2f} in)")
        for e in elements_locked_len:
            if e.name.upper() != "REF":
                set_element_override(project_name, e.name, position_in=float(e.position_in + back_shift))
        set_element_override(project_name, "REF", length_in=float(best_len), position_in=0.0)
        print()
        print(f"[REF tuner] length={best_len:.3f} in  effective REF position={best_pos:.3f} in")
        print(f"  (REF kept at 0; cell+directors shifted forward by {back_shift:.3f} in; score {pos_best_score:.2f})")
    else:
        set_element_override(project_name, "REF", length_in=float(best_len), position_in=float(best_pos))
        print()
        print(f"[REF tuner] length={best_len:.3f} in  position={best_pos:.3f} in  (score {pos_best_score:.2f}) saved as override.")
    return {"changed": True, "best_length_in": best_len, "best_position_in": best_pos, "score": pos_best_score, "back_shift_in": back_shift}
