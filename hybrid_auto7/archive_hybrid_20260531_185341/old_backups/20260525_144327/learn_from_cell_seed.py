REJECT_QUIET = True  # suppress per-candidate rejection spam
#!/usr/bin/env python3
"""
learn_from_cell_seed.py

Loads a tuned cell seed (from learn_cell_only.py output) and locks XFRMR/DE/COUPLER
geometry in place. Then tunes REF + N directors around that locked cell to produce
a full hybrid Yagi. Every iteration scores on real-ground gain + F/B + SWR.

Usage:
    python3 -u ./learn_from_cell_seed.py                      # default 4 directors, balanced
    python3 -u ./learn_from_cell_seed.py --n-directors 5      # 8-element total
    python3 -u ./learn_from_cell_seed.py --priority gain
    python3 -u ./learn_from_cell_seed.py --cell-seed data/cell_learning_runs/best_cell_seed.json
"""

import argparse
import json
from datetime import datetime, UTC
from pathlib import Path

from hyagi.config import AntennaConfig, frange
from hyagi.model import Element, validate_elements, generate_nec_text
from hyagi.engine import NecppEngine
from hyagi.physics import summarize, return_loss_db
from hyagi.pattern import evaluate_pattern_for_elements
from hyagi.paths import MODELS_DIR, DATA_DIR, ensure_dirs


CENTER_FREQ_MHZ = 27.195
F_START = 26.965
F_STOP = 27.405
F_STEP = 0.01

BOOM_FT = 36.0             # longer boom to fit directors + cell
BOOM_IN = BOOM_FT * 12.0

DE_BASE_IN = 5616.0 / CENTER_FREQ_MHZ

# Per-element director step-back (shorter than DE)
# PATCHED: original [12,18,24,...] made DIR1 = DE_BASE - 12 = 194.5,
# which is LONGER than a typical hybrid COUPLER (~178). Start the cascade
# at 30" so DIR1 lands comfortably below COUPLER for the strict rule.
DIR_LEN_STEPS_IN = [30.0, 32.0, 34.0, 36.0, 38.0, 42.0, 48.0, 54.0]
# Initial spacings director-to-director (inches), from DIR1 onwards
DIR_SPACING_SEEDS_IN = [36.0, 36.0, 42.0, 48.0, 54.0, 60.0, 66.0, 72.0]

# REF placed behind the cell; initial offset in front of XFRMR going backward
REF_OFFSET_BEHIND_XFRMR_IN = 48.0    # 4 ft behind XFRMR
# REF_FIX_v4: Yagi-standard REF length relative to DE (cell = 1 logical element)
# 4-6 active el (cell + N dirs <= 5): 4-5% over DE  (0.485-0.495 lambda)
# 7-11 active el: 3-4% over DE
# 12+ active el: 3% over DE
# XFRMR/COUPLER do NOT participate in this calc -- they're part of the cell.
REF_CAP_IN = 234.0  # 19.5 ft physical mast/boom cap (still respected)
def _ref_pct_band_for_dir_count(n_directors):
    active = 1 + n_directors  # cell counts as 1 + directors
    if active <= 6:
        return (1.04, 1.05)   # 4-6 el class
    if active <= 11:
        return (1.03, 1.04)   # 7-11 el class
    return (1.025, 1.03)      # 12+ el class

OUT_DIR = DATA_DIR / "full_hybrid_runs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg=""):
    print(msg, flush=True)


def now_tag():
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


# ---------------- cell seed loading ----------------

def load_cell_seed(seed_path: Path) -> dict:
    if not seed_path.exists():
        raise FileNotFoundError(f"Cell seed not found: {seed_path}")
    data = json.loads(seed_path.read_text())
    for key in ("xfrmr_position_in", "de_position_in", "coupler_position_in",
                "xfrmr_length_in", "de_length_in", "coupler_length_in"):
        if key not in data:
            raise ValueError(f"Cell seed missing required field: {key}")
    return data


# ---------------- element builders ----------------

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


def build_full_elements(cell: dict, ref_len_in: float, ref_offset_in: float,
                         n_directors: int):
    xf_pos  = float(cell["xfrmr_position_in"])
    de_pos  = float(cell["de_position_in"])
    cp_pos  = float(cell["coupler_position_in"])
    xf_len  = float(cell["xfrmr_length_in"])
    de_len  = float(cell["de_length_in"])
    cp_len  = float(cell["coupler_length_in"])

    ref_pos = max(0.0, xf_pos - ref_offset_in)

    elements = [
        Element("REF", ref_pos, ref_len_in),
        Element("XFRMR", xf_pos, xf_len),
        Element("DE", de_pos, de_len),
        Element("COUPLER", cp_pos, cp_len),
    ]

    running_pos = cp_pos
    for i in range(n_directors):
        step = DIR_SPACING_SEEDS_IN[i % len(DIR_SPACING_SEEDS_IN)]
        running_pos = min(BOOM_IN, running_pos + step)
        dlen = max(1.0, DE_BASE_IN - DIR_LEN_STEPS_IN[i % len(DIR_LEN_STEPS_IN)])
        elements.append(Element(f"DIR{i+1}", running_pos, dlen))

    return elements


def clone_elements(els):
    return [Element(e.name, e.position_in, e.length_in) for e in els]


def find_element(els, name):
    for e in els:
        if e.name == name:
            return e
    raise RuntimeError(f"Element not found: {name}")


def set_length(els, name, length_in):
    out = clone_elements(els)
    for e in out:
        if e.name == name:
            e.length_in = float(length_in)
            return out
    raise RuntimeError(f"Element not found: {name}")


def set_position(els, name, position_in):
    out = clone_elements(els)
    for e in out:
        if e.name == name:
            e.position_in = float(position_in)
            return out
    raise RuntimeError(f"Element not found: {name}")


# ---------------- scoring ----------------

def center_result(results):
    return min(results, key=lambda r: abs(r.freq_mhz - CENTER_FREQ_MHZ))


def score(summary, center_r, center_x, center_swr, pattern, priority):
    """Weighted composite score. Higher is better."""
    match_pen = abs(center_r - 50.0) * 4.0 + abs(center_x) * 4.0
    swr_pen   = max(0.0, center_swr - 1.0) * 400.0 + max(0.0, summary.max_swr - 1.0) * 250.0
    rl_bonus  = return_loss_db(center_swr) * 12.0
    base = 20000.0 - match_pen - swr_pen + rl_bonus
    if pattern is None:
        return base
    gain   = float(getattr(pattern, "real_gain_dbi", 0.0) or 0.0)
    fb     = float(getattr(pattern, "front_back_db", 0.0) or 0.0)

    if priority == "gain":
        return base + gain * 350.0 + fb * 40.0
    if priority == "swr":
        return base + gain * 80.0 + fb * 20.0
    # balanced
    return base + gain * 180.0 + fb * 60.0


def evaluate(elements, ant, engine, freqs, priority, want_pattern=True):
    validate_elements(elements, ant)
    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)
    center = center_result(results)

    pattern = None
    if want_pattern:
        try:
            pattern = evaluate_pattern_for_elements(elements, CENTER_FREQ_MHZ, ant)
        except Exception as e:
            log(f"[warn] pattern eval failed: {e}")

    return {
        "summary": summary,
        "center_r": center.r_ohm,
        "center_x": center.x_ohm,
        "center_swr": center.swr_50,
        "center_rl_db": return_loss_db(center.swr_50),
        "pattern": pattern,
        "real_gain_dbi": float(pattern.real_gain_dbi) if pattern is not None else None,
        "peak_elev_deg": float(pattern.peak_elev_deg) if pattern is not None else None,
        "front_back_db": float(pattern.front_back_db) if pattern is not None else None,
        "score": score(summary, center.r_ohm, center.x_ohm, center.swr_50, pattern, priority),
    }


# === cell-rules guard (v2, wrapped=True) ===
from hyagi.cell_rules import guard_eval as _hyagi_guard_eval
evaluate = _hyagi_guard_eval(evaluate)

# ---------------- sweep helper ----------------

def sweep(best_els, best_eval, ant, engine, freqs, priority,
          stage_name, values, applier, print_every=3, want_pattern=True):
    log(f"Stage: {stage_name}  ({len(values)} candidates, pattern={'Y' if want_pattern else 'N'})")
    for i, v in enumerate(values, 1):
        cand = applier(best_els, v)
        try:
            cand_eval = evaluate(cand, ant, engine, freqs, priority, want_pattern=want_pattern)
        except Exception as e:
            log(f"  [{i}/{len(values)}] skipped ({v}): {e}")
            continue
        tag = ""
        if cand_eval["score"] > best_eval["score"]:
            best_els = cand; best_eval = cand_eval; tag = "  <-- best"
        if i % print_every == 0 or i == len(values) or tag:
            g = cand_eval.get("real_gain_dbi")
            fb = cand_eval.get("front_back_db")
            g_s  = f"{g:5.2f}" if g is not None else "  -- "
            fb_s = f"{fb:5.2f}" if fb is not None else "  -- "
            log(f"  {i:>3}/{len(values)} v={v:>8.3f} "
                f"score={best_eval['score']:8.1f} "
                f"maxSWR={best_eval['summary'].max_swr:5.3f} "
                f"gain={g_s} FB={fb_s}{tag}")
    return best_els, best_eval


# ---------------- main tuning pipeline ----------------

def tune(cell: dict, n_directors: int, priority: str):
    # PATCH: clamp DIR1 seed so strict-progression rule passes
    _coupler_len = cell.get("c_len") or cell.get("coupler_len") or 0
    DIR1_MAX_FROM_COUPLER = _coupler_len - 1.0
    ant = make_ant()
    engine = NecppEngine()
    freqs = list(frange(F_START, F_STOP, F_STEP))

    # Pick best REF length seed (fast, no pattern)
    log()
    log("Stage 0: seed REF length")
    log("-" * 40)
    best_els = None; best_eval = None
    # REF_FIX_v4: Yagi % over DE (cell = 1 element); ignore XFRMR/COUPLER
    cell_de_len = float(cell.get("de_length_in", DE_BASE_IN))
    pct_lo, pct_hi = _ref_pct_band_for_dir_count(n_directors)
    ref_floor = min(REF_CAP_IN, cell_de_len * pct_lo)
    ref_ceiling = min(REF_CAP_IN, cell_de_len * pct_hi)
    if ref_floor > ref_ceiling:
        ref_floor = ref_ceiling - 0.25
    print(f"  [REF seed bounds] DE={cell_de_len:.3f}  Yagi pct=[{pct_lo:.3f}, {pct_hi:.3f}]  -> REF [{ref_floor:.3f}, {ref_ceiling:.3f}]")
    n_seeds = 6
    _ladder = [ref_floor + (ref_ceiling - ref_floor) * i / max(1, n_seeds - 1) for i in range(n_seeds)]
    for ref_len in _ladder:
        els = build_full_elements(cell, ref_len, REF_OFFSET_BEHIND_XFRMR_IN, n_directors)
        try:
            ev = evaluate(els, ant, engine, freqs, priority, want_pattern=False)
        except Exception as e:
            log(f"  skipped REF len={ref_len:.2f}: {e}")
            continue
        log(f"  REF len={ref_len:7.2f} in  score={ev['score']:8.1f}  maxSWR={ev['summary'].max_swr:5.3f}")
        if best_eval is None or ev["score"] > best_eval["score"]:
            best_els, best_eval = els, ev
    if best_eval is None:
        raise RuntimeError("All REF seeds failed to evaluate")
    # upgrade to pattern eval on the winner
    best_eval = evaluate(best_els, ant, engine, freqs, priority, want_pattern=True)

    # Stage 1: REF position
    ref_x = find_element(best_els, "XFRMR").position_in
    ref_pos_values = [max(0.0, ref_x - o) for o in [72.0, 60.0, 48.0, 36.0, 24.0, 12.0, 6.0]]
    best_els, best_eval = sweep(
        best_els, best_eval, ant, engine, freqs, priority,
        "1. REF position", ref_pos_values,
        lambda els, v: set_position(els, "REF", v),
        print_every=2, want_pattern=True,
    )

    # Stage 2: REF length fine
    cur_ref_len = find_element(best_els, "REF").length_in
    # REF_FIX_v4: clamp Stage 2 to Yagi % over DE
    _de_len = find_element(best_els, "DE").length_in
    _pct_lo, _pct_hi = _ref_pct_band_for_dir_count(n_directors)
    _ref_lo = min(REF_CAP_IN, _de_len * _pct_lo)
    _ref_hi = min(REF_CAP_IN, _de_len * _pct_hi)
    if _ref_lo > _ref_hi:
        _ref_lo = _ref_hi - 0.25
    print(f"  [REF fine] DE={_de_len:.3f}  Yagi pct=[{_pct_lo:.3f}, {_pct_hi:.3f}]  clamp=[{_ref_lo:.3f}, {_ref_hi:.3f}]")
    ref_len_values = sorted({
        min(_ref_hi, max(_ref_lo, cur_ref_len + d))
        for d in [-2.0, -1.0, -0.5, -0.25, 0, 0.25, 0.5, 1.0, 2.0]
    })
    best_els, best_eval = sweep(
        best_els, best_eval, ant, engine, freqs, priority,
        "2. REF length fine", ref_len_values,
        lambda els, v: set_length(els, "REF", v),
        print_every=2, want_pattern=True,
    )

    # Stages 3+: each director position, then length
    for i in range(1, n_directors + 1):
        name = f"DIR{i}"
        cur = find_element(best_els, name)
        prev = find_element(best_els, f"DIR{i-1}" if i > 1 else "COUPLER")
        max_pos = min(BOOM_IN, cur.position_in + 24.0)

        # position sweep (around current +/- range)
        pos_values = sorted({
            min(max_pos, max(prev.position_in + 12.0, cur.position_in + d))
            for d in [-24.0, -18.0, -12.0, -6.0, 0.0, 6.0, 12.0, 18.0, 24.0]
        })
        best_els, best_eval = sweep(
            best_els, best_eval, ant, engine, freqs, priority,
            f"3.{i} {name} position", pos_values,
            lambda els, v, _name=name: set_position(els, _name, v),
            print_every=2, want_pattern=True,
        )

        # length sweep
        cur_len = find_element(best_els, name).length_in
        len_values = [cur_len + d for d in [-18, -12, -6, -3, 0, 3, 6, 12, 18]]
        best_els, best_eval = sweep(
            best_els, best_eval, ant, engine, freqs, priority,
            f"4.{i} {name} length", len_values,
            lambda els, v, _name=name: set_length(els, _name, v),
            print_every=2, want_pattern=True,
        )

    return best_els, best_eval


# ---------------- saving ----------------

def save_outputs(best_els, best_eval, cell_seed_path, n_directors, priority):
    ensure_dirs()
    tag = now_tag()
    ant = make_ant()

    nec_text = generate_nec_text(best_els, ant, F_START, F_STOP, F_STEP)
    nec_path = MODELS_DIR / f"full_hybrid_{n_directors}dir_{tag}.nec"
    nec_path.write_text(nec_text, encoding="utf-8")

    json_path = OUT_DIR / f"full_hybrid_{n_directors}dir_{tag}.json"
    summary_path = OUT_DIR / f"full_hybrid_{n_directors}dir_{tag}.txt"

    s = best_eval["summary"]
    data = {
        "type": "full_hybrid_from_cell_seed",
        "center_freq_mhz": CENTER_FREQ_MHZ,
        "freq_start_mhz": F_START,
        "freq_stop_mhz": F_STOP,
        "n_elements": 4 + n_directors,
        "n_directors": n_directors,
        "priority": priority,
        "cell_seed_path": str(cell_seed_path),
        "elements": [
            {"name": e.name, "position_in": e.position_in, "length_in": e.length_in}
            for e in best_els
        ],
        "score": best_eval["score"],
        "min_swr": s.min_swr,
        "max_swr": s.max_swr,
        "avg_swr": s.avg_swr,
        "center_r": best_eval["center_r"],
        "center_x": best_eval["center_x"],
        "center_swr": best_eval["center_swr"],
        "center_rl_db": best_eval["center_rl_db"],
        "real_gain_dbi": best_eval["real_gain_dbi"],
        "peak_elev_deg": best_eval["peak_elev_deg"],
        "front_back_db": best_eval["front_back_db"],
        "nec_file": str(nec_path),
    }
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # summary
    lines = []
    lines.append("FULL HYBRID TUNE REPORT (from cell seed)")
    lines.append("=" * 42)
    lines.append("")
    lines.append(f"Cell seed source:      {cell_seed_path}")
    lines.append(f"Total elements:        {4 + n_directors}  (REF + XFRMR + DE + COUPLER + {n_directors} directors)")
    lines.append(f"Priority profile:      {priority}")
    lines.append("")
    lines.append(f"Score:                 {best_eval['score']:.1f}")
    lines.append(f"Center R:              {best_eval['center_r']:.3f} ohm")
    lines.append(f"Center X:              {best_eval['center_x']:.3f} ohm")
    lines.append(f"Center SWR:            {best_eval['center_swr']:.3f}")
    lines.append(f"Max SWR (in band):     {s.max_swr:.3f}")
    lines.append(f"Avg SWR (in band):     {s.avg_swr:.3f}")
    lines.append(f"Return loss (center):  {best_eval['center_rl_db']:.2f} dB")
    if best_eval["real_gain_dbi"] is not None:
        lines.append(f"Real gain (ground):    {best_eval['real_gain_dbi']:.2f} dBi")
    if best_eval["peak_elev_deg"] is not None:
        lines.append(f"Peak elev angle:       {best_eval['peak_elev_deg']:.1f} deg")
    if best_eval["front_back_db"] is not None:
        lines.append(f"Front/back:            {best_eval['front_back_db']:.2f} dB")
    lines.append("")
    lines.append("Geometry")
    lines.append("-" * 42)
    for e in best_els:
        lines.append(f"{e.name:<8s} pos={e.position_in:8.3f} in  length={e.length_in:8.3f} in")
    lines.append("")
    lines.append(f"Best NEC:   {nec_path}")
    lines.append(f"Best JSON:  {json_path}")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log()
    log("Saved outputs")
    log("=" * 42)
    log(f"Best NEC:   {nec_path}")
    log(f"Best JSON:  {json_path}")
    log(f"Summary:    {summary_path}")


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(description="Tune full hybrid array using a locked cell seed")
    ap.add_argument("--cell-seed", type=str,
                    default=str(DATA_DIR / "cell_learning_runs" / "best_cell_seed.json"))
    ap.add_argument("--n-directors", type=int, default=4,
                    help="Number of directors (total elements = 4 + N). Default 4 -> 8el.")
    ap.add_argument("--priority", choices=("gain", "balanced", "swr"),
                    default="balanced")
    args = ap.parse_args()

    ensure_dirs()
    seed_path = Path(args.cell_seed).expanduser().resolve()

    log("Full Hybrid Tune (from cell seed)")
    log("=" * 42)
    log(f"Cell seed:      {seed_path}")
    log(f"Directors:      {args.n_directors}  (total elements: {4 + args.n_directors})")
    log(f"Priority:       {args.priority}")

    cell = load_cell_seed(seed_path)
    log(f"Loaded cell:    XFRMR@{cell['xfrmr_position_in']:.2f} (L={cell['xfrmr_length_in']:.2f}) "
        f"DE@{cell['de_position_in']:.2f} (L={cell['de_length_in']:.2f}) "
        f"COUPLER@{cell['coupler_position_in']:.2f} (L={cell['coupler_length_in']:.2f})")

    best_els, best_eval = tune(cell, args.n_directors, args.priority)

    log()
    log("FINAL BEST")
    log("=" * 42)
    log(f"Score: {best_eval['score']:.1f}  maxSWR={best_eval['summary'].max_swr:.3f}  "
        f"gain={best_eval['real_gain_dbi']}  FB={best_eval['front_back_db']}")

    save_outputs(best_els, best_eval, seed_path, args.n_directors, args.priority)


if __name__ == "__main__":
    main()