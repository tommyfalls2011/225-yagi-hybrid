from itertools import product

from .project import load_project, apply_element_overrides, save_project
from .dynamic import generate_starting_model
from .dynamic_sim import generated_to_elements
from .model import Element, validate_elements, generate_nec_text
from .config import AntennaConfig, frange
from .engine import NecppEngine
from .physics import summarize, return_loss_db
from .pattern import evaluate_pattern_for_elements
from .paths import MODELS_DIR, ensure_dirs


def _safe_name(name: str) -> str:
    return str(name).replace("/", "_").replace("\\", "_").replace(" ", "_")


def override_element(elements, name, position=None, length=None):
    out = []
    found = False
    target = str(name).upper().strip()

    for e in elements:
        if str(e.name).upper() == target:
            found = True
            out.append(
                Element(
                    name=e.name,
                    position_in=e.position_in if position is None else float(position),
                    length_in=e.length_in if length is None else float(length),
                )
            )
        else:
            out.append(e)

    if not found:
        raise RuntimeError(f"Element {name} not found")

    return out


def find_element(elements, name):
    target = str(name).upper().strip()
    for e in elements:
        if str(e.name).upper() == target:
            return e
    raise RuntimeError(f"Element {name} not found")


def score_candidate(summary, gain_dbi=None, rl_db=None):
    # GAIN_RL_FIX_v1: gain-aware + return-loss-aware scoring
    score = 0.0
    score += summary.points_under_2p0 * 150.0
    score += summary.points_under_1p5 * 100.0
    score -= summary.max_swr * 100.0
    score -= summary.avg_swr * 40.0
    score -= summary.avg_abs_x * 8.0

    if summary.max_swr > 2.0:
        score -= 5000.0
    elif summary.max_swr > 1.5:
        score -= 1000.0

    # GAIN_RL_FIX_v1: reward forward gain (per dBi) and return loss (per dB, cap 40)
    if gain_dbi is not None and gain_dbi == gain_dbi:
        score += float(gain_dbi) * 200.0
    if rl_db is not None and rl_db == rl_db:
        score += min(float(rl_db), 40.0) * 30.0

    return score


def tune_project_director(project_name, director, level="quick"):
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

    director_name = str(director).upper().strip()
    base_dir = find_element(base_elements, director_name)

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
        pos_half = 30.0
        len_half = 14.0
        pos_step = 1.0
        len_step = 0.5
    elif level == "normal":
        pos_half = 20.0
        len_half = 10.0
        pos_step = 2.0
        len_step = 1.0
    else:
        pos_half = 15.0
        len_half = 8.0
        pos_step = 3.0
        len_step = 2.0

    boom_in = cfg.boom_length_ft * 12.0

    pos_start = max(0.0, base_dir.position_in - pos_half)
    pos_stop = min(boom_in - 5.0, base_dir.position_in + pos_half)

    len_start = max(120.0, base_dir.length_in - len_half)
    len_stop = base_dir.length_in + len_half

    pos_values = frange(pos_start, pos_stop, pos_step)
    len_values = frange(len_start, len_stop, len_step)

    f_step = 0.01
    freqs = frange(cfg.freq_start_mhz, cfg.freq_stop_mhz, f_step)

    engine = NecppEngine()

    total = len(pos_values) * len(len_values)

    print()
    print("Project director tuner (match-only mode)")
    print("=========================================")
    print(f"Project:        {project_name}")
    print(f"Director:       {director_name}")
    print(f"Level:          {level}")
    print(f"Ground mode:    {cfg.ground_mode}")
    print(f"Base position:  {base_dir.position_in:.3f} in")
    print(f"Base length:    {base_dir.length_in:.3f} in")
    print(f"Total tries:    {total}")
    print()

    best_score = None
    best_info = None
    failed = 0
    count = 0

    # Early-stop on plateau: abort grid scan after N consecutive no-improvement
    import os as _os
    PLATEAU_LIMIT = int(_os.environ.get("DIRECTOR_PLATEAU_LIMIT", "100"))
    plateau_streak = 0
    early_stopped = False

    # GAIN_RL_FIX_v1: pattern eval cost is real; toggle via env (default ON)
    _gain_aware = _os.environ.get("DIRECTOR_GAIN_AWARE", "1") != "0"
    _center_mhz = 0.5 * (cfg.freq_start_mhz + cfg.freq_stop_mhz)

    ensure_dirs()

    for pos, length in product(pos_values, len_values):
        count += 1

        try:
            elements = override_element(
                base_elements,
                director_name,
                position=pos,
                length=length,
            )

            validate_elements(elements, ant)

            results = engine.evaluate(elements, ant, freqs)
            summary = summarize(results)

            # GAIN_RL_FIX_v1: center metrics + (optional) pattern
            _center = min(results, key=lambda r: abs(r.freq_mhz - _center_mhz))
            _rl_db = return_loss_db(_center.swr_50)
            _gain_dbi = None
            if _gain_aware:
                try:
                    _pat = evaluate_pattern_for_elements(elements, _center_mhz, ant)
                    _gain_dbi = float(getattr(_pat, "real_gain_dbi", None) or 0.0)
                except Exception:
                    _gain_dbi = None

            score = score_candidate(summary, gain_dbi=_gain_dbi, rl_db=_rl_db)

            if best_score is None or score > best_score:
                best_score = score
                best_info = (pos, length, summary, score, elements)
                plateau_streak = 0
            else:
                plateau_streak += 1
                if plateau_streak >= PLATEAU_LIMIT and best_info is not None:
                    print(
                        f"[plateau] no improvement in {PLATEAU_LIMIT} candidates "
                        f"after {count}/{total} -- advancing to next director."
                    )
                    early_stopped = True
                    break

            if best_info is not None and (count == 1 or count == total or count % 10 == 0):
                bpos, blen, bs, bscore, _ = best_info
                print(
                    f"{count}/{total} "
                    f"best_score={bscore:.1f} "
                    f"{director_name}pos={bpos:.1f} "
                    f"{director_name}len={blen:.1f} "
                    f"maxSWR={bs.max_swr:.3f} "
                    f"avgSWR={bs.avg_swr:.3f} "
                    f"|X|={bs.avg_abs_x:.3f}"
                )

        except Exception as exc:
            failed += 1
            if failed <= 10:
                print(f"FAILED {count}/{total}: pos={pos} len={length}: {exc}")

    if best_info is None:
        print("No successful candidates.")
        return None

    pos, length, summary, score, elements = best_info

    cfg = load_project(project_name)
    cfg.element_overrides[director_name] = {
        "position_in": float(pos),
        "length_in": float(length),
    }
    save_project(cfg)

    nec = generate_nec_text(
        elements=elements,
        ant=ant,
        f_start=cfg.freq_start_mhz,
        f_stop=cfg.freq_stop_mhz,
        f_step=f_step,
    )

    nec_file = MODELS_DIR / f"project_{_safe_name(project_name)}_{director_name}_best.nec"
    nec_file.write_text(nec, encoding="utf-8")

    print()
    print("Best project director candidate")
    print("===============================")
    print(f"Project:             {project_name}")
    print(f"Director:            {director_name}")
    print(f"Ground mode:         {cfg.ground_mode}")
    print(f"Score:               {score:.1f}")
    print(f"{director_name} position:     {pos:.3f} in")
    print(f"{director_name} length:       {length:.3f} in")
    print(f"Max SWR:             {summary.max_swr:.3f}")
    print(f"Avg SWR:             {summary.avg_swr:.3f}")
    print(f"Points <= 1.5:       {summary.points_under_1p5}")
    print(f"Points <= 2.0:       {summary.points_under_2p0}")
    print(f"Avg R:               {summary.avg_r:.3f} ohm")
    print(f"Avg |X|:             {summary.avg_abs_x:.3f} ohm")
    print(f"Failed candidates:   {failed}")
    print(f"NEC file:            {nec_file}")
    print("Saved best director override into project.")

    return {
        "project": project_name,
        "director": director_name,
        "position_in": pos,
        "length_in": length,
        "summary": summary,
        "score": score,
        "nec_file": str(nec_file),
        "failed": failed,
    }
