REJECT_QUIET = True  # suppress per-candidate rejection spam
#!/usr/bin/env python3

import json  # PEAK_GAIN_FIX_v1: pattern.real_gain_dbi swapped in for forward_gain_dbi
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone

from hyagi.config import AntennaConfig, frange
from hyagi.model import Element, validate_elements, generate_nec_text
from hyagi.engine import NecppEngine
from hyagi.physics import summarize, return_loss_db
from hyagi.pattern import evaluate_pattern_for_elements
from hyagi.paths import MODELS_DIR, DATA_DIR, ensure_dirs


# POWER_MULT_v1: convert antenna gain into honest power multipliers
def gain_power_multipliers(gain_dbi):
    if gain_dbi is None or gain_dbi != gain_dbi:
        return None, None, None
    try:
        g = float(gain_dbi)
    except (TypeError, ValueError):
        return None, None, None
    dbd = g - 2.15
    return dbd, 10.0 ** (dbd / 10.0), 10.0 ** (g / 10.0)


CENTER_FREQ_MHZ = 27.195
F_START = 26.965
F_STOP = 27.405
F_STEP = 0.01

BOOM_FT = 18.0
BOOM_IN = BOOM_FT * 12.0

DE_BASE_IN = 5616.0 / CENTER_FREQ_MHZ  # approx full dipole length in inches

# Your requested style
DE_SEEDS = [36.0, 42.0, 48.0, 54.0, 60.0, 66.0, 72.0]  # 3ft to 6ft from REF
XSP_SEEDS = [15.0, 17.0, 19.0, 21.0, 23.0, 25.0]
CSP_SEEDS = [10.0, 12.0, 14.0, 16.0, 18.0]
DIR1_OFFSET_FROM_COUPLER = 48.0  # 4 ft initial placement

GENERATIONS = 2

# REF = 4', 3', 2' longer than DE and 1', 2', 3' shorter than DE
# PATCHED: REF must be >= DE (hybrid OWA rule). Old list had three shorter
# variants which the cell_rules engine now rejects. Replaced with realistic
# 2%-10% longer-than-DE seeds.
REF_VARIANTS = [
    DE_BASE_IN * 1.10,
    DE_BASE_IN * 1.07,
    DE_BASE_IN * 1.05,
    DE_BASE_IN * 1.04,
    DE_BASE_IN * 1.03,
    DE_BASE_IN * 1.02,
]

OUT_DIR = DATA_DIR / "learning_runs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# SMART_SEEDS_v1: load historical winning seeds from latest insights snapshot
def _load_smart_seeds():
    """Returns dict like {'xsp': 24, 'csp': 17, 'de_pos': 48, 'ref_len_list': [...]}
    or {} if no insights found / opted out."""
    import os, json
    if os.environ.get("LEARN_SMART_SEEDS", "0") != "1":
        return {}
    insights_dir = DATA_DIR / "learning_runs"
    snapshots = sorted(insights_dir.glob("insights_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if not snapshots:
        log("[smart-seeds] no insights snapshot found; using hardcoded seeds.")
        return {}
    try:
        ins = json.loads(snapshots[0].read_text())
    except Exception as exc:
        log(f"[smart-seeds] failed reading {snapshots[0].name}: {exc}")
        return {}

    out = {}
    params = ins.get("parameters", {})
    for pname in ("xsp", "csp", "de_pos"):
        if pname not in params:
            continue
        top = params[pname].get("top_values", [])
        # pick the single best bin with mean_delta > 0 AND win_rate > 0.5
        winners = [b for b in top if b.get("mean_delta", 0) > 0 and b.get("win_rate", 0) > 0.5]
        if winners:
            out[pname] = int(winners[0]["value"])

    # ref_len: take top 4 historical winners as new REF_VARIANTS
    if "ref_len" in params:
        top = params["ref_len"].get("top_values", [])
        winning_lens = [float(b["value"]) for b in top
                        if b.get("mean_delta", 0) > -5.0][:4]
        if winning_lens:
            out["ref_len_list"] = winning_lens

    out["_snapshot"] = snapshots[0].name
    out["_total_moves"] = ins.get("summary", {}).get("total_moves", 0)
    return out


def _apply_smart_seeds_or_defaults(seeds):
    """Print banner + return (de_pos, xsp, csp, ref_variants)."""
    if not seeds:
        log("[smart-seeds] disabled (set LEARN_SMART_SEEDS=1 to enable)")
        return 48.0, 19.0, 14.0, REF_VARIANTS

    log("=" * 64)
    log(f"[smart-seeds] using {seeds['_snapshot']} ({seeds['_total_moves']:,} moves analyzed)")
    de  = float(seeds.get("de_pos", 48.0))
    xsp = float(seeds.get("xsp", 19.0))
    csp = float(seeds.get("csp", 14.0))
    refs = list(seeds.get("ref_len_list", REF_VARIANTS)) or REF_VARIANTS
    log(f"[smart-seeds] de_pos={de}  xsp={xsp}  csp={csp}")
    log(f"[smart-seeds] ref_variants={refs}")
    log("=" * 64)
    return de, xsp, csp, refs



def log(msg=""):
    print(msg, flush=True)


def now_tag():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def clone_elements(elements):
    return [Element(e.name, e.position_in, e.length_in) for e in elements]


def find_element(elements, name):
    for e in elements:
        if e.name == name:
            return e
    raise RuntimeError(f"Element {name} not found")


def set_element(elements, name, position=None, length=None):
    out = clone_elements(elements)
    for e in out:
        if e.name == name:
            if position is not None:
                e.position_in = float(position)
            if length is not None:
                e.length_in = float(length)
            return out
    raise RuntimeError(f"Element {name} not found")


def set_cell(elements, de_pos, xsp, csp):
    out = clone_elements(elements)
    for e in out:
        if e.name == "DE":
            e.position_in = float(de_pos)
        elif e.name == "XFRMR":
            e.position_in = float(de_pos - xsp)
        elif e.name == "COUPLER":
            e.position_in = float(de_pos + csp)
    return out


def make_base_elements(ref_len, de_pos=48.0, xsp=19.0, csp=14.0):
    coupler_pos = de_pos + csp
    dir1_pos = min(BOOM_IN, coupler_pos + DIR1_OFFSET_FROM_COUPLER)

    return [
        Element("REF", 0.0, ref_len),
        Element("XFRMR", de_pos - xsp, DE_BASE_IN - 8.0),
        Element("DE", de_pos, DE_BASE_IN),
        Element("COUPLER", coupler_pos, DE_BASE_IN - 10.0),
        Element("DIR1", dir1_pos, DE_BASE_IN - 15.0),
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


def center_result(results):
    target = CENTER_FREQ_MHZ
    return min(results, key=lambda r: abs(r.freq_mhz - target))


def score(summary, center_r, center_x, pattern):
    s = 0.0

    s += summary.points_under_2p0 * 120.0
    s += summary.points_under_1p5 * 140.0
    s -= summary.max_swr * 180.0
    s -= summary.avg_swr * 80.0
    s -= abs(center_r - 50.0) * 10.0
    s -= abs(center_x) * 12.0
    s -= summary.avg_abs_x * 8.0

    if pattern is not None:
        s += pattern.real_gain_dbi * 120.0
        s += min(pattern.front_back_db, 50.0) * 16.0

    return s


def evaluate(elements, ant, engine, freqs):
    validate_elements(elements, ant)

    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)
    center = center_result(results)

    try:
        pattern = evaluate_pattern_for_elements(elements, freq_mhz=CENTER_FREQ_MHZ, ant=ant)
    except Exception as exc:
        log(f"WARNING pattern unavailable: {exc}")
        pattern = None

    sc = score(summary, center.r_ohm, center.x_ohm, pattern)

    return {
        "summary": summary,
        "center_r": center.r_ohm,
        "center_x": center.x_ohm,
        "center_swr": center.swr_50,
        "pattern": pattern,
        "score": sc,
    }


# === cell-rules guard (v2, wrapped=True) ===
from hyagi.cell_rules import guard_eval as _hyagi_guard_eval
evaluate = _hyagi_guard_eval(evaluate)

def log_move(log_rows, generation, stage, move_name, before_eval, after_eval):
    row = {
        "generation": generation,
        "stage": stage,
        "move": move_name,
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
        "before_gain": None if before_eval["pattern"] is None else before_eval["pattern"].real_gain_dbi,
        "after_gain": None if after_eval["pattern"] is None else after_eval["pattern"].real_gain_dbi,
        "before_fb": None if before_eval["pattern"] is None else before_eval["pattern"].front_back_db,
        "after_fb": None if after_eval["pattern"] is None else after_eval["pattern"].front_back_db,
    }
    log_rows.append(row)


def sweep_single(elements, ant, engine, freqs, generation, stage, move_name, values, applier, log_rows, print_every=5):
    best_elements = clone_elements(elements)
    best_eval = evaluate(best_elements, ant, engine, freqs)

    total = len(values)
    for idx, v in enumerate(values, start=1):
        try:
            cand_elements = applier(best_elements, v)
            cand_eval = evaluate(cand_elements, ant, engine, freqs)
            log_move(log_rows, generation, stage, f"{move_name}={v}", best_eval, cand_eval)

            if cand_eval["score"] > best_eval["score"]:
                best_elements = cand_elements
                best_eval = cand_eval

            if idx == 1 or idx == total or idx % print_every == 0:
                log(
                    f"  {stage} {idx}/{total} {move_name}={v} "
                    f"best_score={best_eval['score']:.1f} "
                    f"maxSWR={best_eval['summary'].max_swr:.3f} "
                    f"avgSWR={best_eval['summary'].avg_swr:.3f} "
                    f"R={best_eval['center_r']:.2f} X={best_eval['center_x']:.2f}"
                )

        except Exception as exc:
            (None if REJECT_QUIET else log(f"  FAILED {stage} {move_name}={v}: {exc}"))

    return best_elements, best_eval


def run_generation(start_elements, generation):
    ant = make_ant()
    engine = NecppEngine()
    freqs = frange(F_START, F_STOP, F_STEP)

    log_rows = []

    elements = clone_elements(start_elements)
    best_eval = evaluate(elements, ant, engine, freqs)

    log()
    log(f"Generation {generation} baseline")
    log(f"  score={best_eval['score']:.1f} maxSWR={best_eval['summary'].max_swr:.3f} avgSWR={best_eval['summary'].avg_swr:.3f} R={best_eval['center_r']:.2f} X={best_eval['center_x']:.2f}")

    # 1) XFRMR spacing
    de = find_element(elements, "DE")
    x = find_element(elements, "XFRMR")
    c = find_element(elements, "COUPLER")
    xsp = de.position_in - x.position_in
    csp = c.position_in - de.position_in
    vals = frange(max(12.0, xsp - 6.0), min(25.0, xsp + 6.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage1_xsp", "xsp",
        vals,
        lambda els, v: set_cell(els, find_element(els, "DE").position_in, v, find_element(els, "COUPLER").position_in - find_element(els, "DE").position_in),
        log_rows
    )

    # 2) COUPLER spacing
    de = find_element(elements, "DE")
    x = find_element(elements, "XFRMR")
    c = find_element(elements, "COUPLER")
    xsp = de.position_in - x.position_in
    csp = c.position_in - de.position_in
    vals = frange(max(8.0, csp - 6.0), min(20.0, csp + 6.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage2_csp", "csp",
        vals,
        lambda els, v: set_cell(els, find_element(els, "DE").position_in, find_element(els, "DE").position_in - find_element(els, "XFRMR").position_in, v),
        log_rows
    )

    # 3) DE position only
    de = find_element(elements, "DE")
    vals = frange(max(36.0, de.position_in - 9.0), min(72.0, de.position_in + 9.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage3_de_pos", "de_pos",
        vals,
        lambda els, v: set_element(els, "DE", position=v),
        log_rows
    )

    # 4) XFRMR length
    x = find_element(elements, "XFRMR")
    vals = frange(max(170.0, x.length_in - 8.0), min(230.0, x.length_in + 8.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage4_x_len", "x_len",
        vals,
        lambda els, v: set_element(els, "XFRMR", length=v),
        log_rows
    )

    # 5) COUPLER length
    c = find_element(elements, "COUPLER")
    vals = frange(max(120.0, c.length_in - 12.0), min(220.0, c.length_in + 12.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage5_c_len", "c_len",
        vals,
        lambda els, v: set_element(els, "COUPLER", length=v),
        log_rows
    )

    # 6) DE length
    de = find_element(elements, "DE")
    vals = frange(max(190.0, de.length_in - 8.0), min(225.0, de.length_in + 8.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage6_de_len", "de_len",
        vals,
        lambda els, v: set_element(els, "DE", length=v),
        log_rows
    )

    # 7) REF length
    ref = find_element(elements, "REF")
    vals = frange(max(160.0, ref.length_in - 36.0), min(270.0, ref.length_in + 36.0), 2.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage7_ref_len", "ref_len",
        vals,
        lambda els, v: set_element(els, "REF", length=v),
        log_rows
    )

    # 8) whole cell move later
    de = find_element(elements, "DE")
    x = find_element(elements, "XFRMR")
    c = find_element(elements, "COUPLER")
    xsp = de.position_in - x.position_in
    csp = c.position_in - de.position_in
    vals = frange(max(30.0, de.position_in - 6.0), min(72.0, de.position_in + 6.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage8_cell_move", "cell_move",
        vals,
        lambda els, v: set_cell(els, v, xsp, csp),
        log_rows
    )

    # 9) DIR1 position
    d1 = find_element(elements, "DIR1")
    coupler = find_element(elements, "COUPLER")
    vals = frange(max(coupler.position_in + 30.0, d1.position_in - 18.0), min(BOOM_IN, d1.position_in + 18.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage9_dir1_pos", "dir1_pos",
        vals,
        lambda els, v: set_element(els, "DIR1", position=v),
        log_rows
    )

    # 10) DIR1 length
    d1 = find_element(elements, "DIR1")
    vals = frange(max(160.0, d1.length_in - 8.0), min(210.0, d1.length_in + 8.0), 1.0)
    elements, best_eval = sweep_single(
        elements, ant, engine, freqs, generation, "stage10_dir1_len", "dir1_len",
        vals,
        lambda els, v: set_element(els, "DIR1", length=v),
        log_rows
    )

    return elements, best_eval, log_rows


def save_outputs(best_elements, best_eval, all_logs):
    ensure_dirs()
    tag = now_tag()

    ant = make_ant()
    nec = generate_nec_text(best_elements, ant, F_START, F_STOP, F_STEP)
    nec_path = MODELS_DIR / f"learn3el_best_{tag}.nec"
    nec_path.write_text(nec, encoding="utf-8")

    json_path = OUT_DIR / f"learn3el_best_{tag}.json"
    log_path = OUT_DIR / f"learn3el_moves_{tag}.jsonl"
    summary_path = OUT_DIR / f"learn3el_summary_{tag}.txt"

    best_data = {
        "elements": [
            {"name": e.name, "position_in": e.position_in, "length_in": e.length_in}
            for e in best_elements
        ],
        "score": best_eval["score"],
        "min_swr": best_eval["summary"].min_swr,
        "max_swr": best_eval["summary"].max_swr,
        "avg_swr": best_eval["summary"].avg_swr,
        "center_r": best_eval["center_r"],
        "center_x": best_eval["center_x"],
        "center_swr": best_eval["center_swr"],
        "avg_r": best_eval["summary"].avg_r,
        "avg_abs_x": best_eval["summary"].avg_abs_x,
        "worst_return_loss_db": return_loss_db(best_eval["summary"].max_swr),
        "nec_file": str(nec_path),
        "real_gain_dbi": None if best_eval["pattern"] is None else best_eval["pattern"].real_gain_dbi,
        "front_back_db": None if best_eval["pattern"] is None else best_eval["pattern"].front_back_db,
        "gain_dbd": (None if best_eval["pattern"] is None
                     else gain_power_multipliers(best_eval["pattern"].real_gain_dbi)[0]),
        "power_mult_vs_dipole": (None if best_eval["pattern"] is None
                                 else gain_power_multipliers(best_eval["pattern"].real_gain_dbi)[1]),
        "power_mult_vs_isotropic": (None if best_eval["pattern"] is None
                                    else gain_power_multipliers(best_eval["pattern"].real_gain_dbi)[2]),
    }
    json_path.write_text(json.dumps(best_data, indent=2), encoding="utf-8")

    with log_path.open("w", encoding="utf-8") as f:
        for row in all_logs:
            f.write(json.dumps(row) + "\n")

    if all_logs:
        best_move = max(all_logs, key=lambda r: r["score_delta"])
        worst_move = min(all_logs, key=lambda r: r["score_delta"])
    else:
        best_move = None
        worst_move = None

    lines = []
    lines.append("3-ELEMENT HYBRID LEARNING REPORT")
    lines.append("================================")
    lines.append("")
    lines.append(f"Best score:           {best_eval['score']:.1f}")
    lines.append(f"Min SWR:              {best_eval['summary'].min_swr:.3f}")
    lines.append(f"Max SWR:              {best_eval['summary'].max_swr:.3f}")
    lines.append(f"Avg SWR:              {best_eval['summary'].avg_swr:.3f}")
    lines.append(f"Center R:             {best_eval['center_r']:.3f} ohm")
    lines.append(f"Center X:             {best_eval['center_x']:.3f} ohm")
    lines.append(f"Center SWR:           {best_eval['center_swr']:.3f}")
    lines.append(f"Avg R:                {best_eval['summary'].avg_r:.3f} ohm")
    lines.append(f"Avg |X|:              {best_eval['summary'].avg_abs_x:.3f} ohm")
    lines.append(f"Worst RL:             {return_loss_db(best_eval['summary'].max_swr):.2f} dB")
    if best_eval["pattern"] is not None:
        _g = best_eval['pattern'].real_gain_dbi
        _dbd, _mult_d, _mult_i = gain_power_multipliers(_g)
        lines.append(f"Forward gain:         {_g:.3f} dBi  ({_dbd:.3f} dBd)")
        lines.append(f"Front/back:           {best_eval['pattern'].front_back_db:.3f} dB")
        lines.append(f"Power x (vs dipole):  {_mult_d:.2f}x")
        lines.append(f"Power x (vs iso):     {_mult_i:.2f}x")
    lines.append("")
    lines.append("Best layout")
    lines.append("-----------")
    prev = None
    for e in best_elements:
        spacing = 0.0 if prev is None else e.position_in - prev
        lines.append(
            f"{e.name:<8s} pos={e.position_in:8.3f} in  spacing={spacing:8.3f} in  length={e.length_in:8.3f} in"
        )
        prev = e.position_in

    lines.append("")
    lines.append("Best move")
    lines.append("---------")
    lines.append(json.dumps(best_move, indent=2) if best_move else "None")

    lines.append("")
    lines.append("Worst move")
    lines.append("----------")
    lines.append(json.dumps(worst_move, indent=2) if worst_move else "None")

    lines.append("")
    lines.append(f"NEC file:             {nec_path}")
    lines.append(f"JSON best:            {json_path}")
    lines.append(f"Move log:             {log_path}")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log()
    log("Saved outputs")
    log("=============")
    log(f"Best NEC:   {nec_path}")
    log(f"Best JSON:  {json_path}")
    log(f"Move log:   {log_path}")
    log(f"Summary:    {summary_path}")


def main():
    ensure_dirs()

    all_logs = []
    overall_best_eval = None
    overall_best_elements = None

    # SMART_SEEDS_v1: optionally override hardcoded starting points
    _seeds = _load_smart_seeds()
    _de_seed, _xsp_seed, _csp_seed, _ref_variants = _apply_smart_seeds_or_defaults(_seeds)

    log("Generation 1")
    log("============")
    for ref_len in _ref_variants:
        log()
        log(f"Testing reflector seed length: {ref_len:.3f} in")
        # start each family with one of your cell seeds (smart or default)
        base = make_base_elements(ref_len, de_pos=_de_seed, xsp=_xsp_seed, csp=_csp_seed)
        best_elements, best_eval, logs = run_generation(base, generation=1)
        all_logs.extend(logs)

        if overall_best_eval is None or best_eval["score"] > overall_best_eval["score"]:
            overall_best_eval = best_eval
            overall_best_elements = best_elements

    for gen in range(2, GENERATIONS + 1):
        log()
        log(f"Generation {gen}")
        log("============")
        best_elements, best_eval, logs = run_generation(overall_best_elements, generation=gen)
        all_logs.extend(logs)

        if best_eval["score"] > overall_best_eval["score"]:
            overall_best_eval = best_eval
            overall_best_elements = best_elements

    log()
    log("FINAL BEST")
    log("==========")
    log(f"Best score: {overall_best_eval['score']:.1f}")
    log(f"Max SWR:    {overall_best_eval['summary'].max_swr:.3f}")
    log(f"Avg SWR:    {overall_best_eval['summary'].avg_swr:.3f}")
    log(f"Center R:   {overall_best_eval['center_r']:.3f} ohm")
    log(f"Center X:   {overall_best_eval['center_x']:.3f} ohm")
    log(f"Avg |X|:    {overall_best_eval['summary'].avg_abs_x:.3f}")
    if overall_best_eval["pattern"] is not None:
        _g = overall_best_eval["pattern"].real_gain_dbi
        _dbd, _mult_d, _mult_i = gain_power_multipliers(_g)
        log(f"Gain:       {_g:.3f} dBi  ({_dbd:.3f} dBd)")
        log(f"F/B:        {overall_best_eval['pattern'].front_back_db:.3f} dB")
        log(f"Power x (vs dipole):    {_mult_d:.2f}x")
        log(f"Power x (vs isotropic): {_mult_i:.2f}x")

    save_outputs(overall_best_elements, overall_best_eval, all_logs)


if __name__ == "__main__":
    main()
