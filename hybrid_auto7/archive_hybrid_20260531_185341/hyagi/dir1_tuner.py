from dataclasses import replace
from itertools import product

from .config import AntennaConfig, Design, frange
from .model import build_elements, validate_elements, design_key, generate_nec_text
from .engine import NecppEngine
from .physics import summarize
from .paths import MODELS_DIR, ensure_dirs
from . import db
from .pattern import evaluate_pattern_for_design


def design_from_row(row):
    return Design(
        de_position_in=float(row["de_position_in"]),
        xfrmr_spacing_in=float(row["xfrmr_spacing_in"]),
        coupler_spacing_in=float(row["coupler_spacing_in"]),
        xfrmr_length_in=float(row["xfrmr_length_in"]),
        coupler_length_in=float(row["coupler_length_in"]),
        de_length_in=float(row["de_length_in"]),
    )


# PEAK_GAIN_FIX_v1: use real_gain_dbi (peak elevation) instead of forward_gain_dbi
# (horizon gain is a null for ground-mounted antennas, was producing -195 dBi)
def score_candidate(summary, pattern):
    """
    Higher is better.

    Hard priorities:
        - Keep SWR usable
        - Keep main lobe forward
        - Keep F/B acceptable
        - Increase gain
    """
    score = 0.0

    # Strong reward for SWR coverage
    score += summary.points_under_2p0 * 100.0
    score += summary.points_under_1p5 * 50.0

    # Penalize high SWR
    score -= summary.max_swr * 20.0
    score -= summary.avg_swr * 10.0

    # Gain reward
    score += pattern.real_gain_dbi * 100.0

    # F/B reward, capped so it does not dominate forever
    score += min(pattern.front_back_db, 35.0) * 15.0

    # Prefer max lobe near forward phi=90
    phi_error = abs(pattern.max_gain_phi_deg - 90.0)
    score -= phi_error * 10.0

    # Reject / strong penalties
    if pattern.front_back_db < 15.0:
        score -= 1000.0

    if summary.max_swr > 2.0:
        score -= 2000.0

    return score


def _freq_step(freqs):
    if len(freqs) < 2:
        raise ValueError("Need at least two frequency points")
    return round(freqs[1] - freqs[0], 6)


def run_one(design, stage, engine, ant, freqs):
    ensure_dirs()

    f_step = _freq_step(freqs)
    center_freq = (freqs[0] + freqs[-1]) / 2.0

    key = design_key(design, freqs[0], freqs[-1], f_step)
    existing = db.existing_run(key)

    if existing is not None:
        pat = evaluate_pattern_for_design(design, freq_mhz=center_freq)

        class SummaryLike:
            pass

        s = SummaryLike()
        s.min_swr = existing["min_swr"]
        s.max_swr = existing["max_swr"]
        s.avg_swr = existing["avg_swr"]
        s.points_under_1p5 = existing["points_under_1p5"]
        s.points_under_2p0 = existing["points_under_2p0"]
        s.avg_r = existing["avg_r"]
        s.avg_abs_x = existing["avg_abs_x"]

        return existing, s, pat

    elements = build_elements(design)
    validate_elements(elements, ant)

    nec = generate_nec_text(elements, ant, freqs[0], freqs[-1], f_step)
    nec_file = MODELS_DIR / f"{key}.nec"
    nec_file.write_text(nec, encoding="utf-8")

    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)

    run_id = db.insert_run(
        design_key=key,
        stage=stage,
        design=design,
        f_start=freqs[0],
        f_stop=freqs[-1],
        f_step=f_step,
        summary=summary,
        elements=elements,
        results=results,
        nec_file=nec_file,
    )

    row = db.run_by_id(run_id) if hasattr(db, "run_by_id") else db.existing_run(key)
    pat = evaluate_pattern_for_design(design, freq_mhz=center_freq)

    return row, summary, pat


def tune_dir1(level="quick"):
    best = db.best_run()

    if best is None:
        raise RuntimeError("No best run found. Run autotune first.")

    base_design = design_from_row(best)

    ant = AntennaConfig()
    engine = NecppEngine()
    freqs = frange(26.965, 27.405, 0.01)

    if level == "deep":
        pos_values = frange(105, 170, 2)
        len_values = frange(170, 205, 1)
    elif level == "normal":
        pos_values = frange(110, 165, 2.5)
        len_values = frange(175, 202, 1.5)
    else:
        pos_values = frange(115, 160, 5)
        len_values = frange(178, 200, 2)

    total = len(pos_values) * len(len_values)

    print()
    print("DIR1 tuner")
    print("==========")
    print(f"Level: {level}")
    print(f"Base run id: {best['id']}")
    print(f"Total candidates: {total}")
    print()

    best_score = None
    best_info = None
    count = 0
    failed = 0

    for pos, length in product(pos_values, len_values):
        count += 1

        d = replace(
            base_design,
            dir1_position_in=pos,
            dir1_length_in=length,
        )

        try:
            row, summary, pat = run_one(
                design=d,
                stage=f"dir1_{level}",
                engine=engine,
                ant=ant,
                freqs=freqs,
            )

            score = score_candidate(summary, pat)

            if best_score is None or score > best_score:
                best_score = score
                best_info = (row, summary, pat, score, pos, length)

            if best_info is not None and (count == 1 or count == total or count % 10 == 0):
                brow, bs, bp, bscore, bpos, blen = best_info
                print(
                    f"{count}/{total} "
                    f"best_score={bscore:.1f} "
                    f"DIR1pos={bpos:.1f} "
                    f"DIR1len={blen:.1f} "
                    f"gain={bp.real_gain_dbi:.3f} "
                    f"FB={bp.front_back_db:.2f} "
                    f"maxSWR={bs.max_swr:.3f}"
                )

        except Exception as exc:
            failed += 1
            print(f"FAILED {count}/{total} DIR1pos={pos} DIR1len={length}: {exc}")

    if best_info is None:
        print("No DIR1 candidate succeeded.")
        return None

    row, summary, pat, score, pos, length = best_info

    print()
    print("Best DIR1 candidate")
    print("===================")
    print(f"Run id:              {row['id']}")
    print(f"Score:               {score:.1f}")
    print(f"DIR1 position:       {pos:.3f} in")
    print(f"DIR1 length:         {length:.3f} in")
    print(f"Max SWR:             {summary.max_swr:.3f}")
    print(f"Avg SWR:             {summary.avg_swr:.3f}")
    print(f"Points <= 1.5:       {summary.points_under_1p5}")
    print(f"Points <= 2.0:       {summary.points_under_2p0}")
    print(f"Forward gain:        {pat.real_gain_dbi:.3f} dBi")
    print(f"Front/back:          {pat.front_back_db:.3f} dB")
    print(
        f"Beamwidth:           "
        f"{'not found' if pat.beamwidth_deg is None else f'{pat.beamwidth_deg:.1f} deg'}"
    )
    print(f"Max gain phi:        {pat.max_gain_phi_deg:.1f} deg")
    print(f"Failed candidates:   {failed}")

    return {
        "run_id": row["id"],
        "score": score,
        "dir1_position_in": pos,
        "dir1_length_in": length,
        "summary": summary,
        "pattern": pat,
        "failed": failed,
    }
