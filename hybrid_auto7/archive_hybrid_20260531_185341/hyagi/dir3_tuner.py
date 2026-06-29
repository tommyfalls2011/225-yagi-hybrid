from itertools import product

from .config import AntennaConfig, Design, frange
from .model import Element, validate_elements, generate_nec_text
from .engine import NecppEngine
from .physics import summarize
from .paths import MODELS_DIR, ensure_dirs
from . import db
from .pattern import evaluate_pattern_for_elements


def get_run_by_id(run_id):
    if hasattr(db, "run_by_id"):
        return db.run_by_id(run_id)

    con = db.connect()
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT *
            FROM runs
            WHERE id = ?
        """, (run_id,))
        return cur.fetchone()
    finally:
        con.close()


def design_from_row(row):
    return Design(
        de_position_in=float(row["de_position_in"]),
        xfrmr_spacing_in=float(row["xfrmr_spacing_in"]),
        coupler_spacing_in=float(row["coupler_spacing_in"]),
        xfrmr_length_in=float(row["xfrmr_length_in"]),
        coupler_length_in=float(row["coupler_length_in"]),
        de_length_in=float(row["de_length_in"]),
    )


def elements_from_run(run_id):
    rows = db.elements_for_run(run_id)

    if not rows:
        raise RuntimeError(f"No elements found for run id={run_id}")

    return [
        Element(
            name=r["name"],
            position_in=float(r["position_in"]),
            length_in=float(r["length_in"]),
        )
        for r in rows
    ]


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


# PEAK_GAIN_FIX_v1: use real_gain_dbi (peak elevation) instead of forward_gain_dbi
# (horizon gain is a null for ground-mounted antennas, was producing -195 dBi)
def score_candidate(summary, pattern):
    score = 0.0

    # Preserve excellent match
    score += summary.points_under_2p0 * 120.0
    score += summary.points_under_1p5 * 80.0
    score -= summary.max_swr * 90.0
    score -= summary.avg_swr * 30.0

    # DIR3 mostly for gain
    score += pattern.real_gain_dbi * 180.0

    # F/B reward, capped
    score += min(pattern.front_back_db, 40.0) * 12.0

    # Main lobe direction
    phi_error = abs(pattern.max_gain_phi_deg - 90.0)
    score -= phi_error * 25.0

    # Strong penalties
    if summary.max_swr > 2.0:
        score -= 5000.0

    if summary.max_swr > 1.5:
        score -= 1000.0

    if pattern.front_back_db < 18.0:
        score -= 1200.0

    if phi_error > 10.0:
        score -= 1500.0

    return score


def _freq_step(freqs):
    if len(freqs) < 2:
        raise ValueError("Need at least two frequency points")
    return round(freqs[1] - freqs[0], 6)


def run_one(base_row, base_elements, dir3_pos, dir3_len, stage, engine, ant, freqs):
    design = design_from_row(base_row)

    elements = override_element(
        base_elements,
        "DIR3",
        position=dir3_pos,
        length=dir3_len,
    )

    validate_elements(elements, ant)

    f_start = freqs[0]
    f_stop = freqs[-1]
    f_step = _freq_step(freqs)
    center_freq = (f_start + f_stop) / 2.0

    key = (
        f"dir3_base{base_row['id']}_"
        f"d3p{dir3_pos:.3f}_"
        f"d3l{dir3_len:.3f}_"
        f"f{f_start:.3f}_{f_stop:.3f}_{f_step:.3f}"
    )

    existing = db.existing_run(key)

    if existing is not None:
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

        pat = evaluate_pattern_for_elements(elements, freq_mhz=center_freq, ant=ant)
        return existing, s, pat, elements

    ensure_dirs()

    nec = generate_nec_text(elements, ant, f_start, f_stop, f_step)
    nec_file = MODELS_DIR / f"{key}.nec"
    nec_file.write_text(nec, encoding="utf-8")

    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)

    run_id = db.insert_run(
        design_key=key,
        stage=stage,
        design=design,
        f_start=f_start,
        f_stop=f_stop,
        f_step=f_step,
        summary=summary,
        elements=elements,
        results=results,
        nec_file=nec_file,
    )

    row = db.run_by_id(run_id) if hasattr(db, "run_by_id") else db.existing_run(key)
    pat = evaluate_pattern_for_elements(elements, freq_mhz=center_freq, ant=ant)

    return row, summary, pat, elements


def tune_dir3(base_run_id, level="quick"):
    base_row = get_run_by_id(base_run_id)

    if base_row is None:
        raise RuntimeError(f"No run found for id={base_run_id}")

    base_elements = elements_from_run(base_run_id)

    ant = AntennaConfig()
    engine = NecppEngine()
    freqs = frange(26.965, 27.405, 0.01)

    if level == "deep":
        pos_values = frange(285, 358, 1.5)
        len_values = frange(155, 195, 1)
    elif level == "normal":
        pos_values = frange(295, 355, 2.5)
        len_values = frange(160, 192, 1.5)
    else:
        pos_values = frange(300, 355, 5)
        len_values = frange(165, 190, 2.5)

    total = len(pos_values) * len(len_values)

    print()
    print("DIR3 tuner")
    print("==========")
    print(f"Level: {level}")
    print(f"Base run id: {base_run_id}")
    print(f"Total candidates: {total}")
    print()

    best_score = None
    best_info = None
    count = 0
    failed = 0

    for pos, length in product(pos_values, len_values):
        count += 1

        try:
            row, summary, pat, elements = run_one(
                base_row=base_row,
                base_elements=base_elements,
                dir3_pos=pos,
                dir3_len=length,
                stage=f"dir3_{level}",
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
                    f"DIR3pos={bpos:.1f} "
                    f"DIR3len={blen:.1f} "
                    f"gain={bp.real_gain_dbi:.3f} "
                    f"FB={bp.front_back_db:.2f} "
                    f"maxSWR={bs.max_swr:.3f}"
                )

        except Exception as exc:
            failed += 1
            print(f"FAILED {count}/{total} DIR3pos={pos} DIR3len={length}: {exc}")

    if best_info is None:
        print("No DIR3 candidate succeeded.")
        return None

    row, summary, pat, score, pos, length = best_info

    print()
    print("Best DIR3 candidate")
    print("===================")
    print(f"Run id:              {row['id']}")
    print(f"Score:               {score:.1f}")
    print(f"DIR3 position:       {pos:.3f} in")
    print(f"DIR3 length:         {length:.3f} in")
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
        "dir3_position_in": pos,
        "dir3_length_in": length,
        "summary": summary,
        "pattern": pat,
        "failed": failed,
    }
