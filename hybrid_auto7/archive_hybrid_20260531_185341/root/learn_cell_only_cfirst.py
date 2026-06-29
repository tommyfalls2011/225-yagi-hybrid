REJECT_QUIET = True  # suppress per-candidate rejection spam
#!/usr/bin/env python3

import json
from datetime import datetime, UTC

from hyagi.config import AntennaConfig, frange
from hyagi.model import Element, generate_nec_text
from hyagi.engine import NecppEngine
from hyagi.physics import summarize, return_loss_db
from hyagi.paths import MODELS_DIR, DATA_DIR, ensure_dirs


CENTER_FREQ_MHZ = 27.195
F_START = 26.965
F_STOP = 27.405
F_STEP = 0.01

BOOM_FT = 18.0
BOOM_IN = BOOM_FT * 12.0

DE_START_POS_IN = 60.0
DE_BASE_IN = 5616.0 / CENTER_FREQ_MHZ

OUT_DIR = DATA_DIR / "cell_learning_runs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg=""):
    print(msg, flush=True)


def now_tag():
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def clone_elements(elements):
    return [Element(e.name, e.position_in, e.length_in) for e in elements]


def find_element(elements, name):
    target = str(name).upper().strip()
    for e in elements:
        if str(e.name).upper() == target:
            return e
    raise RuntimeError(f"Element {name} not found")


def set_element(elements, name, position=None, length=None):
    out = clone_elements(elements)
    target = str(name).upper().strip()
    for e in out:
        if str(e.name).upper() == target:
            if position is not None:
                e.position_in = float(position)
            if length is not None:
                e.length_in = float(length)
            return out
    raise RuntimeError(f"Element {name} not found")


def set_cell_relative_to_de(elements, de_pos=None, xsp=None, csp=None):
    out = clone_elements(elements)

    de = find_element(out, "DE")
    x = find_element(out, "XFRMR")
    c = find_element(out, "COUPLER")

    if de_pos is None:
        de_pos = de.position_in
    if xsp is None:
        xsp = de.position_in - x.position_in
    if csp is None:
        csp = c.position_in - de.position_in

    de.position_in = float(de_pos)
    x.position_in = float(de_pos - xsp)
    c.position_in = float(de_pos + csp)

    return out


def make_cell():
    de_pos = DE_START_POS_IN
    xsp = 15.0
    csp = 10.0
    return [
        Element("XFRMR", de_pos - xsp, DE_BASE_IN - 6.0),  # CELLRULES_v2: was DE+2 (illegal); XFRMR must be < DE
        Element("DE", de_pos, DE_BASE_IN),
        Element("COUPLER", de_pos + csp, DE_BASE_IN - 10.0),
    ]


def make_ant():
    return AntennaConfig(
        boom_length_in=BOOM_IN,
        boom_diameter_in=1.5,
        center_od_in=0.625,
        outer_od_in=0.500,
        center_half_len_in=36.0,
        model_height_in=54.0 * 12.0,
        ground_mode="average",
        ground_epsr=13.0,
        ground_sigma_s_per_m=0.005,
        cell_mounting_style="full_cell_insulated",
    )


def validate_cell_elements(elements, ant):
    names = {e.name for e in elements}

    for required in ["XFRMR", "DE", "COUPLER"]:
        if required not in names:
            raise ValueError(f"Missing cell element: {required}")

    seen = {}
    for e in elements:
        if e.position_in < -1e-9:
            raise ValueError(f"{e.name} is negative position: {e.position_in}")

        if e.position_in > ant.boom_length_in + 1e-9:
            raise ValueError(f"{e.name} exceeds boom length: {e.position_in} > {ant.boom_length_in}")

        if e.length_in <= 0:
            raise ValueError(f"{e.name} invalid length {e.length_in}")

        key = round(e.position_in, 6)
        if key in seen:
            raise ValueError(f"Position collision: {seen[key]} and {e.name}")

        seen[key] = e.name

    return True


def center_result(results):
    return min(results, key=lambda r: abs(r.freq_mhz - CENTER_FREQ_MHZ))


def score(summary, center_r, center_x, center_swr):
    s = 0.0
    s += summary.points_under_2p0 * 180.0
    s += summary.points_under_1p5 * 220.0
    s -= summary.max_swr * 220.0
    s -= summary.avg_swr * 100.0
    s -= abs(center_r - 50.0) * 14.0
    s -= abs(center_x) * 16.0
    s -= summary.avg_abs_x * 8.0
    s -= center_swr * 60.0
    return s


def evaluate(elements, ant, engine, freqs):
    validate_cell_elements(elements, ant)

    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)
    center = center_result(results)

    return {
        "summary": summary,
        "center_r": center.r_ohm,
        "center_x": center.x_ohm,
        "center_swr": center.swr_50,
        "center_rl_db": return_loss_db(center.swr_50),
        "score": score(summary, center.r_ohm, center.x_ohm, center.swr_50),
    }


# === cell-rules guard (v2, wrapped=True) ===
from hyagi.cell_rules import guard_eval as _hyagi_guard_eval
evaluate = _hyagi_guard_eval(evaluate)

def current_geometry(elements):
    x = find_element(elements, "XFRMR")
    de = find_element(elements, "DE")
    c = find_element(elements, "COUPLER")
    return {
        "xfrmr_position_in": x.position_in,
        "de_position_in": de.position_in,
        "coupler_position_in": c.position_in,
        "xfrmr_spacing_in": de.position_in - x.position_in,
        "coupler_spacing_in": c.position_in - de.position_in,
        "xfrmr_length_in": x.length_in,
        "de_length_in": de.length_in,
        "coupler_length_in": c.length_in,
    }


def log_move(log_rows, stage, move_name, value, before_eval, after_eval, before_elements, after_elements):
    log_rows.append({
        "stage": stage,
        "move": move_name,
        "value": value,
        "before_score": before_eval["score"],
        "after_score": after_eval["score"],
        "score_delta": after_eval["score"] - before_eval["score"],
        "before_max_swr": before_eval["summary"].max_swr,
        "after_max_swr": after_eval["summary"].max_swr,
        "before_avg_swr": before_eval["summary"].avg_swr,
        "after_avg_swr": after_eval["summary"].avg_swr,
        "before_center_r": before_eval["center_r"],
        "after_center_r": after_eval["center_r"],
        "before_center_x": before_eval["center_x"],
        "after_center_x": after_eval["center_x"],
        "before_center_swr": before_eval["center_swr"],
        "after_center_swr": after_eval["center_swr"],
        "before_geometry": current_geometry(before_elements),
        "after_geometry": current_geometry(after_elements),
    })


def sweep_single(elements, ant, engine, freqs, stage, move_name, values, applier, log_rows, print_every=5):
    best_elements = clone_elements(elements)
    best_eval = evaluate(best_elements, ant, engine, freqs)

    total = len(values)
    for idx, v in enumerate(values, start=1):
        try:
            cand_elements = applier(best_elements, v)
            cand_eval = evaluate(cand_elements, ant, engine, freqs)

            log_move(
                log_rows, stage, move_name, v,
                best_eval, cand_eval,
                best_elements, cand_elements
            )

            if cand_eval["score"] > best_eval["score"]:
                best_elements = cand_elements
                best_eval = cand_eval

            if idx == 1 or idx == total or idx % print_every == 0:
                g = current_geometry(best_elements)
                log(
                    f"  {stage} {idx}/{total} {move_name}={v} "
                    f"best_score={best_eval['score']:.1f} "
                    f"maxSWR={best_eval['summary'].max_swr:.3f} "
                    f"avgSWR={best_eval['summary'].avg_swr:.3f} "
                    f"R={best_eval['center_r']:.2f} "
                    f"X={best_eval['center_x']:.2f} "
                    f"DE={g['de_position_in']:.2f} "
                    f"Xsp={g['xfrmr_spacing_in']:.2f} "
                    f"Csp={g['coupler_spacing_in']:.2f}"
                )

        except Exception as exc:
            (None if REJECT_QUIET else log(f"  FAILED {stage} {move_name}={v}: {exc}"))

    return best_elements, best_eval


def run_cell_tune():
    ant = make_ant()
    engine = NecppEngine()
    freqs = frange(F_START, F_STOP, F_STEP)

    elements = make_cell()
    log_rows = []

    best_eval = evaluate(elements, ant, engine, freqs)

    log()
    log("Cell placement tune (coupler-first test)")
    log("===================")
    log("Only XFRMR / DE / COUPLER are active")
    log(f"Starting DE fixed at {DE_START_POS_IN:.1f} in")
    log(
        f"Baseline score={best_eval['score']:.1f} "
        f"maxSWR={best_eval['summary'].max_swr:.3f} "
        f"avgSWR={best_eval['summary'].avg_swr:.3f} "
        f"R={best_eval['center_r']:.2f} "
        f"X={best_eval['center_x']:.2f}"
    )

    log()
    log("Stage 1: COUPLER spacing, DE fixed at 60")
    vals = frange(6.0, 24.0, 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage1_csp", "csp", vals,
        lambda els, v: set_cell_relative_to_de(
            els,
            de_pos=DE_START_POS_IN,
            xsp=current_geometry(els)["xfrmr_spacing_in"],
            csp=v,
        ),
        log_rows,
    )

    log()
    log("Stage 2: XFRMR spacing, DE fixed at 60")
    vals = frange(8.0, 28.0, 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage2_xsp", "xsp", vals,
        lambda els, v: set_cell_relative_to_de(
            els,
            de_pos=DE_START_POS_IN,
            xsp=v,
            csp=current_geometry(els)["coupler_spacing_in"],
        ),
        log_rows,
    )

    log()
    log("Stage 3: repeat COUPLER spacing fine")
    csp0 = current_geometry(elements)["coupler_spacing_in"]
    vals = frange(max(6.0, csp0 - 3.0), min(24.0, csp0 + 3.0), 0.5)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage3_csp_fine", "csp", vals,
        lambda els, v: set_cell_relative_to_de(
            els,
            de_pos=DE_START_POS_IN,
            xsp=current_geometry(els)["xfrmr_spacing_in"],
            csp=v,
        ),
        log_rows,
    )

    log()
    log("Stage 4: repeat XFRMR spacing fine")
    xsp0 = current_geometry(elements)["xfrmr_spacing_in"]
    vals = frange(max(8.0, xsp0 - 3.0), min(28.0, xsp0 + 3.0), 0.5)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage4_xsp_fine", "xsp", vals,
        lambda els, v: set_cell_relative_to_de(
            els,
            de_pos=DE_START_POS_IN,
            xsp=v,
            csp=current_geometry(els)["coupler_spacing_in"],
        ),
        log_rows,
    )

    log()
    log("Stage 5: COUPLER length")
    vals = frange(DE_BASE_IN - 25.0, DE_BASE_IN + 15.0, 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage5_clen", "c_len", vals,
        lambda els, v: set_element(els, "COUPLER", length=v),
        log_rows,
    )

    log()
    log("Stage 6: XFRMR length")
    vals = frange(DE_BASE_IN - 20.0, DE_BASE_IN + 20.0, 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage6_xlen", "x_len", vals,
        lambda els, v: set_element(els, "XFRMR", length=v),
        log_rows,
    )

    log()
    log("Stage 7: DE length")
    vals = frange(DE_BASE_IN - 15.0, DE_BASE_IN + 15.0, 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage7_dlen", "de_len", vals,
        lambda els, v: set_element(els, "DE", length=v),
        log_rows,
    )

    log()
    log("Stage 8: move DE only, leave XFRMR and COUPLER fixed")
    de0 = current_geometry(elements)["de_position_in"]
    vals = frange(max(40.0, de0 - 6.0), min(80.0, de0 + 6.0), 0.5)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage8_de_only", "de_pos", vals,
        lambda els, v: set_element(els, "DE", position=v),
        log_rows,
    )

    log()
    log("Stage 9: fine nudge XFRMR position")
    x0 = find_element(elements, "XFRMR").position_in
    vals = frange(max(0.0, x0 - 2.0), min(BOOM_IN, x0 + 2.0), 0.25)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage9_xpos_nudge", "x_pos", vals,
        lambda els, v: set_element(els, "XFRMR", position=v),
        log_rows,
    )

    log()
    log("Stage 10: fine nudge COUPLER position")
    c0 = find_element(elements, "COUPLER").position_in
    vals = frange(max(0.0, c0 - 2.0), min(BOOM_IN, c0 + 2.0), 0.25)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage10_cpos_nudge", "c_pos", vals,
        lambda els, v: set_element(els, "COUPLER", position=v),
        log_rows,
    )

    log()
    log("Stage 11: fine nudge XFRMR length")
    xl0 = find_element(elements, "XFRMR").length_in
    vals = frange(xl0 - 3.0, xl0 + 3.0, 0.25)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage11_xlen_nudge", "x_len", vals,
        lambda els, v: set_element(els, "XFRMR", length=v),
        log_rows,
    )

    log()
    log("Stage 12: fine nudge COUPLER length")
    cl0 = find_element(elements, "COUPLER").length_in
    vals = frange(cl0 - 3.0, cl0 + 3.0, 0.25)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage12_clen_nudge", "c_len", vals,
        lambda els, v: set_element(els, "COUPLER", length=v),
        log_rows,
    )

    log()
    log("Stage 13: fine nudge DE length")
    dl0 = find_element(elements, "DE").length_in
    vals = frange(dl0 - 3.0, dl0 + 3.0, 0.25)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage13_dlen_nudge", "de_len", vals,
        lambda els, v: set_element(els, "DE", length=v),
        log_rows,
    )

    log()
    log("Stage 14: fine nudge DE position")
    dp0 = find_element(elements, "DE").position_in
    vals = frange(max(40.0, dp0 - 2.0), min(80.0, dp0 + 2.0), 0.25)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs,
        "stage14_dpos_nudge", "de_pos", vals,
        lambda els, v: set_element(els, "DE", position=v),
        log_rows,
    )

    return elements, best_eval, log_rows


def save_outputs(best_elements, best_eval, all_logs):
    ensure_dirs()
    tag = now_tag()

    ant = make_ant()
    nec = generate_nec_text(best_elements, ant, F_START, F_STOP, F_STEP)

    nec_path = MODELS_DIR / f"learn_cell_cfirst_best_{tag}.nec"
    json_path = OUT_DIR / f"learn_cell_cfirst_best_{tag}.json"
    log_path = OUT_DIR / f"learn_cell_cfirst_moves_{tag}.jsonl"
    summary_path = OUT_DIR / f"learn_cell_cfirst_summary_{tag}.txt"
    seed_path = OUT_DIR / "best_cell_seed_cfirst.json"

    nec_path.write_text(nec, encoding="utf-8")

    g = current_geometry(best_elements)
    s = best_eval["summary"]

    best_data = {
        "type": "cell_placement_tune_coupler_first",
        "center_freq_mhz": CENTER_FREQ_MHZ,
        "freq_start_mhz": F_START,
        "freq_stop_mhz": F_STOP,
        **g,
        "elements": [
            {"name": e.name, "position_in": e.position_in, "length_in": e.length_in}
            for e in best_elements
        ],
        "score": best_eval["score"],
        "min_swr": s.min_swr,
        "max_swr": s.max_swr,
        "avg_swr": s.avg_swr,
        "center_r": best_eval["center_r"],
        "center_x": best_eval["center_x"],
        "center_swr": best_eval["center_swr"],
        "center_rl_db": best_eval["center_rl_db"],
        "avg_r": s.avg_r,
        "avg_abs_x": s.avg_abs_x,
        "points_under_1p5": s.points_under_1p5,
        "points_under_2p0": s.points_under_2p0,
        "nec_file": str(nec_path),
    }

    json_path.write_text(json.dumps(best_data, indent=2), encoding="utf-8")
    seed_path.write_text(json.dumps(best_data, indent=2), encoding="utf-8")

    with log_path.open("w", encoding="utf-8") as f:
        for row in all_logs:
            f.write(json.dumps(row) + "\n")

    best_move = max(all_logs, key=lambda r: r["score_delta"]) if all_logs else None
    worst_move = min(all_logs, key=lambda r: r["score_delta"]) if all_logs else None

    lines = []
    lines.append("CELL PLACEMENT TUNE REPORT (COUPLER-FIRST TEST)")
    lines.append("==========================")
    lines.append("")
    lines.append(f"Best score:           {best_eval['score']:.1f}")
    lines.append(f"Min SWR:              {s.min_swr:.3f}")
    lines.append(f"Max SWR:              {s.max_swr:.3f}")
    lines.append(f"Avg SWR:              {s.avg_swr:.3f}")
    lines.append(f"Center R:             {best_eval['center_r']:.3f} ohm")
    lines.append(f"Center X:             {best_eval['center_x']:.3f} ohm")
    lines.append(f"Center SWR:           {best_eval['center_swr']:.3f}")
    lines.append(f"Center RL:            {best_eval['center_rl_db']:.3f} dB")
    lines.append(f"Avg R:                {s.avg_r:.3f} ohm")
    lines.append(f"Avg |X|:              {s.avg_abs_x:.3f} ohm")
    lines.append("")
    lines.append("Best cell layout")
    lines.append("----------------")
    for e in best_elements:
        lines.append(
            f"{e.name:<8s} pos={e.position_in:8.3f} in  length={e.length_in:8.3f} in"
        )
    lines.append("")
    lines.append(f"XFRMR-DE spacing:     {g['xfrmr_spacing_in']:.3f} in")
    lines.append(f"DE-COUPLER spacing:   {g['coupler_spacing_in']:.3f} in")
    lines.append("")
    lines.append("Best move")
    lines.append("---------")
    lines.append(json.dumps(best_move, indent=2) if best_move else "None")
    lines.append("")
    lines.append("Worst move")
    lines.append("----------")
    lines.append(json.dumps(worst_move, indent=2) if worst_move else "None")
    lines.append("")
    lines.append(f"Best NEC:             {nec_path}")
    lines.append(f"Best JSON:            {json_path}")
    lines.append(f"Reusable seed:        {seed_path}")
    lines.append(f"Move log:             {log_path}")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log()
    log("Saved outputs")
    log("=============")
    log(f"Best NEC:   {nec_path}")
    log(f"Best JSON:  {json_path}")
    log(f"Cell seed:  {seed_path}")
    log(f"Move log:   {log_path}")
    log(f"Summary:    {summary_path}")


def main():
    ensure_dirs()

    log("Cell placement tune")
    log("===================")
    log("No REF, no DIR1 sweep, no run-up-the-boom seed search")
    log(f"DE starts at {DE_START_POS_IN:.1f} in")
    log("Tuning order: csp -> xsp -> csp fine -> xsp fine -> clen -> xlen -> de_len -> de_only_move -> nudges")

    best_elements, best_eval, all_logs = run_cell_tune()

    log()
    log("FINAL BEST")
    log("==========")
    log(f"Best score: {best_eval['score']:.1f}")
    log(f"Max SWR:    {best_eval['summary'].max_swr:.3f}")
    log(f"Avg SWR:    {best_eval['summary'].avg_swr:.3f}")
    log(f"Center R:   {best_eval['center_r']:.3f} ohm")
    log(f"Center X:   {best_eval['center_x']:.3f} ohm")
    log(f"Avg |X|:    {best_eval['summary'].avg_abs_x:.3f}")

    save_outputs(best_elements, best_eval, all_logs)


if __name__ == "__main__":
    main()
