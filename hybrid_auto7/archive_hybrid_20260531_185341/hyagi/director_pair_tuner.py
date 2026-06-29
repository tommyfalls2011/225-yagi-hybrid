"""
DIR_PAIR_COTUNE_v1
Co-tune adjacent director pairs (DIR1+DIR2, DIR2+DIR3) on a small 4D grid
to escape local optima the greedy single-director search misses.
Reuses the same gain/return-loss aware scoring as project_director_tuner.
"""
from itertools import product
import os as _os

from .project import load_project, apply_element_overrides, save_project
from .dynamic import generate_starting_model
from .dynamic_sim import generated_to_elements
from .model import validate_elements, generate_nec_text
from .config import AntennaConfig, frange
from .engine import NecppEngine
from .physics import summarize, return_loss_db
from .pattern import evaluate_pattern_for_elements
from .paths import MODELS_DIR, ensure_dirs
from .project_director_tuner import override_element, find_element, score_candidate


def _safe_name(name: str) -> str:
    return str(name).replace("/", "_").replace("\\", "_").replace(" ", "_")


def tune_project_director_pair(project_name, dir_a, dir_b, level="deep"):
    cfg = load_project(project_name)

    generated = generate_starting_model(
        element_count=cfg.element_count,
        mode=cfg.mode,
        freq_start_mhz=cfg.freq_start_mhz,
        freq_stop_mhz=cfg.freq_stop_mhz,
        boom_length_ft=cfg.boom_length_ft,
    )
    apply_element_overrides(cfg, generated)
    base_elements = generated_to_elements(generated)

    name_a = str(dir_a).upper().strip()
    name_b = str(dir_b).upper().strip()
    base_a = find_element(base_elements, name_a)
    base_b = find_element(base_elements, name_b)

    ant = AntennaConfig(
        boom_length_in=cfg.boom_length_ft * 12.0,
        boom_diameter_in=cfg.boom_diameter_in,
        center_od_in=cfg.center_od_in,
        outer_od_in=cfg.outer_od_in,
        center_half_len_in=cfg.center_half_len_in,
        model_height_in=cfg.height_ft * 12.0,
        ground_mode=cfg.ground_mode,
        ground_epsr=cfg.ground_epsr,
        ground_sigma_s_per_m=cfg.ground_sigma_s_per_m,
    )
    validate_elements(base_elements, ant)

    if level == "deep":
        pos_half, pos_step = 3.0, 1.5
        len_half, len_step = 1.5, 0.75
    elif level == "normal":
        pos_half, pos_step = 2.0, 1.0
        len_half, len_step = 1.0, 0.5
    else:
        pos_half, pos_step = 2.0, 2.0
        len_half, len_step = 1.0, 1.0

    boom_in = cfg.boom_length_ft * 12.0

    def axis(center, half, step, lo=None, hi=None):
        s = center - half
        e = center + half
        if lo is not None:
            s = max(lo, s)
        if hi is not None:
            e = min(hi, e)
        return frange(s, e, step)

    pos_a_vals = axis(base_a.position_in, pos_half, pos_step, lo=0.0, hi=boom_in - 5.0)
    pos_b_vals = axis(base_b.position_in, pos_half, pos_step, lo=0.0, hi=boom_in - 5.0)
    len_a_vals = axis(base_a.length_in, len_half, len_step, lo=120.0)
    len_b_vals = axis(base_b.length_in, len_half, len_step, lo=120.0)

    f_step = 0.01
    freqs = frange(cfg.freq_start_mhz, cfg.freq_stop_mhz, f_step)
    engine = NecppEngine()
    total = len(pos_a_vals) * len(pos_b_vals) * len(len_a_vals) * len(len_b_vals)

    print()
    print("Project director PAIR co-tuner")
    print("==============================")
    print(f"Project:        {project_name}")
    print(f"Pair:           {name_a} + {name_b}")
    print(f"Level:          {level}")
    print(f"Base {name_a}: pos={base_a.position_in:.3f}  len={base_a.length_in:.3f}")
    print(f"Base {name_b}: pos={base_b.position_in:.3f}  len={base_b.length_in:.3f}")
    print(f"Pos a values:   {[round(v,2) for v in pos_a_vals]}")
    print(f"Pos b values:   {[round(v,2) for v in pos_b_vals]}")
    print(f"Len a values:   {[round(v,2) for v in len_a_vals]}")
    print(f"Len b values:   {[round(v,2) for v in len_b_vals]}")
    print(f"Total tries:    {total}")
    print()

    _center_mhz = 0.5 * (cfg.freq_start_mhz + cfg.freq_stop_mhz)
    _gain_aware = _os.environ.get("DIRECTOR_GAIN_AWARE", "1") != "0"

    def _eval(elements):
        results = engine.evaluate(elements, ant, freqs)
        summary = summarize(results)
        _center = min(results, key=lambda r: abs(r.freq_mhz - _center_mhz))
        _rl = return_loss_db(_center.swr_50)
        _gain = None
        if _gain_aware:
            try:
                _pat = evaluate_pattern_for_elements(elements, _center_mhz, ant)
                _gain = float(getattr(_pat, "real_gain_dbi", None) or 0.0)
            except Exception:
                _gain = None
        return summary, _rl, _gain

    # Baseline (no change) -- only commit if strictly better
    try:
        summary0, rl0, gain0 = _eval(base_elements)
        baseline_score = score_candidate(summary0, gain_dbi=gain0, rl_db=rl0)
    except Exception as exc:
        print(f"Baseline simulation failed: {exc}")
        return None

    print(f"Baseline score (current overrides): {baseline_score:.1f}  "
          f"maxSWR={summary0.max_swr:.3f}  gain={('%.2f' % gain0) if gain0 is not None else 'NA'}")
    print()

    PLATEAU_LIMIT = int(_os.environ.get("DIRECTOR_PAIR_PLATEAU_LIMIT", "100"))
    best_score = baseline_score
    best_info = None
    plateau_streak = 0
    failed = 0
    count = 0

    ensure_dirs()

    for pa, pb, la, lb in product(pos_a_vals, pos_b_vals, len_a_vals, len_b_vals):
        count += 1
        try:
            elements = override_element(base_elements, name_a, position=pa, length=la)
            elements = override_element(elements, name_b, position=pb, length=lb)
            validate_elements(elements, ant)

            summary, rl, gain = _eval(elements)
            score = score_candidate(summary, gain_dbi=gain, rl_db=rl)

            if score > best_score:
                best_score = score
                best_info = (pa, pb, la, lb, summary, score, elements, gain)
                plateau_streak = 0
                print(
                    f"{count}/{total} NEW BEST score={score:.1f} "
                    f"{name_a}(pos={pa:.1f},len={la:.1f}) "
                    f"{name_b}(pos={pb:.1f},len={lb:.1f}) "
                    f"maxSWR={summary.max_swr:.3f} "
                    f"gain={('%.2f' % gain) if gain is not None else 'NA'}"
                )
            else:
                plateau_streak += 1
                if plateau_streak >= PLATEAU_LIMIT and best_info is not None:
                    print(f"[plateau] no improvement in {PLATEAU_LIMIT} candidates "
                          f"after {count}/{total} -- ending pair sweep.")
                    break
        except Exception as exc:
            failed += 1
            if failed <= 5:
                print(f"FAILED {count}/{total}: {exc}")

    if best_info is None:
        print()
        print(f"No improvement over baseline for {name_a}+{name_b} pair "
              f"(baseline {baseline_score:.1f} held).")
        return None

    pa, pb, la, lb, summary, score, elements, gain = best_info

    cfg = load_project(project_name)
    cfg.element_overrides[name_a] = {"position_in": float(pa), "length_in": float(la)}
    cfg.element_overrides[name_b] = {"position_in": float(pb), "length_in": float(lb)}
    save_project(cfg)

    nec = generate_nec_text(
        elements=elements, ant=ant,
        f_start=cfg.freq_start_mhz, f_stop=cfg.freq_stop_mhz, f_step=f_step,
    )
    nec_file = MODELS_DIR / f"project_{_safe_name(project_name)}_{name_a}_{name_b}_pair_best.nec"
    nec_file.write_text(nec, encoding="utf-8")

    print()
    print("Best project director PAIR candidate")
    print("====================================")
    print(f"Project:        {project_name}")
    print(f"Pair:           {name_a} + {name_b}")
    print(f"Score:          {score:.1f}  (baseline {baseline_score:.1f}, delta +{score - baseline_score:.1f})")
    print(f"{name_a}: pos={pa:.3f}  len={la:.3f}")
    print(f"{name_b}: pos={pb:.3f}  len={lb:.3f}")
    print(f"Max SWR:        {summary.max_swr:.3f}")
    print(f"Avg SWR:        {summary.avg_swr:.3f}")
    print(f"Real gain:      {('%.2f dBi' % gain) if gain is not None else 'NA'}")
    print(f"Failed:         {failed}")
    print(f"NEC file:       {nec_file}")
    print("Saved best pair override into project.")

    return {
        "project": project_name,
        "pair": (name_a, name_b),
        "score": score,
        "baseline_score": baseline_score,
        "delta": score - baseline_score,
        "position_a": pa, "length_a": la,
        "position_b": pb, "length_b": lb,
        "nec_file": str(nec_file),
        "failed": failed,
    }
