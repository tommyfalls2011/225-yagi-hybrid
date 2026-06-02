REJECT_QUIET = True  # suppress per-candidate rejection spam
#!/usr/bin/env python3
"""
learn_cell_cascade.py -- Cascading Procedure cell tune.

Procedure (user research):
  Phase 1: DE only at fixed position; sweep length, find |X|=0 at center freq.
  Phase 2: Add XFRMR behind DE; 2D sweep (spacing in x length in), target R=50.
  Phase 3: Add COUPLER in front of DE; 2D sweep (spacing in x length in),
           target |X|=0 with R clamped at 50.

Output schema matches learn_cell_only.py exactly so 'Run Full Hybrid Tune
from Cell Seed' picks up the result.  best_cell_seed.json is overwritten
ONLY if the new score beats the existing one.
"""

import json
from datetime import datetime, UTC

from hyagi.config import AntennaConfig, frange
from hyagi.model import Element, generate_nec_text
from hyagi.engine import NecppEngine
from hyagi.physics import summarize, return_loss_db
from hyagi.pattern import evaluate_pattern_for_cell
from hyagi.paths import MODELS_DIR, DATA_DIR, ensure_dirs


CENTER_FREQ_MHZ = 27.205
F_START = 26.965
F_STOP = 27.405
F_STEP = 0.01

BOOM_FT = 18.0
BOOM_IN = BOOM_FT * 12.0

DE_START_POS_IN = 60.0
DE_BASE_IN = 5616.0 / CENTER_FREQ_MHZ  # ~206.5

OUT_DIR = DATA_DIR / "cell_learning_runs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg=""):
    print(msg, flush=True)


def now_tag():
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


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


def center_of(results):
    return min(results, key=lambda r: abs(r.freq_mhz - CENTER_FREQ_MHZ))


def eval_center(elements, ant, engine):
    """Single-freq evaluation at CENTER_FREQ_MHZ. Returns dict with R/X/swr."""
    results = engine.evaluate(elements, ant, [CENTER_FREQ_MHZ])
    c = results[0]
    return {"R": c.r_ohm, "X": c.x_ohm, "swr": c.swr_50}


# === cell-rules guard (v2, wrapped=True) ===
from hyagi.cell_rules import guard_eval as _hyagi_guard_eval
eval_center = _hyagi_guard_eval(eval_center)

def eval_band(elements, ant, engine):
    """Full-band evaluation. Returns dict with summary + center metrics."""
    freqs = frange(F_START, F_STOP, F_STEP)
    results = engine.evaluate(elements, ant, freqs)
    c = center_of(results)
    s = summarize(results)
    return {
        "summary": s,
        "center_r": c.r_ohm,
        "center_x": c.x_ohm,
        "center_swr": c.swr_50,
        "center_rl_db": return_loss_db(c.swr_50),
    }


def band_score(ev):
    s = ev["summary"]
    sc = 0.0
    sc += s.points_under_2p0 * 180.0
    sc += s.points_under_1p5 * 220.0
    sc -= s.max_swr * 220.0
    sc -= s.avg_swr * 100.0
    sc -= abs(ev["center_r"] - 50.0) * 14.0
    sc -= abs(ev["center_x"]) * 16.0
    sc -= s.avg_abs_x * 8.0
    sc -= ev["center_swr"] * 60.0
    return sc


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------
def phase1(ant, engine, log_rows):
    log()
    log("Phase 1: DE-only resonance hunt")
    log("===============================")
    log("Mount DE only. Sweep length until |X| at center freq is minimized.")
    log(f"  Starting DE_BASE = {DE_BASE_IN:.3f} in (5616/{CENTER_FREQ_MHZ})")

    best_L = None
    best_absx = 1e9
    best_R = None

    # Coarse sweep
    log("  -- coarse sweep (step 0.5 in) --")
    for L in frange(DE_BASE_IN - 18.0, DE_BASE_IN + 18.0, 0.5):
        elements = [Element("DE", DE_START_POS_IN, L)]
        try:
            ev = eval_center(elements, ant, engine)
        except Exception as exc:
            log(f"  NEC err  L={L:.2f}: {exc}")
            continue
        log_rows.append({
            "phase": 1, "stage": "p1_coarse",
            "de_length_in": L,
            "R": ev["R"], "X": ev["X"], "swr": ev["swr"],
        })
        if abs(ev["X"]) < best_absx:
            best_absx = abs(ev["X"])
            best_L = L
            best_R = ev["R"]

    if best_L is None:
        raise RuntimeError("Phase 1 coarse sweep produced no valid result")
    log(f"  coarse best: DE={best_L:.3f} in   R={best_R:.2f}   |X|={best_absx:.3f}")

    # Fine sweep
    log("  -- fine sweep (step 0.05 in) --")
    for L in frange(best_L - 1.0, best_L + 1.0, 0.05):
        elements = [Element("DE", DE_START_POS_IN, L)]
        try:
            ev = eval_center(elements, ant, engine)
        except Exception:
            continue
        log_rows.append({
            "phase": 1, "stage": "p1_fine",
            "de_length_in": L,
            "R": ev["R"], "X": ev["X"], "swr": ev["swr"],
        })
        if abs(ev["X"]) < best_absx:
            best_absx = abs(ev["X"])
            best_L = L
            best_R = ev["R"]

    log(f"  Phase 1 LOCKED: DE length = {best_L:.4f} in   R={best_R:.2f}   |X|={best_absx:.3f}")
    return best_L, best_R


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------
def phase2(ant, engine, de_len, log_rows):
    """Add XFRMR behind DE. Real-world spacing range 4-32 in. XFRMR length < DE."""
    log()
    log("Phase 2: Add XFRMR behind DE")
    log("============================")
    log("Real-world spacing: 4-32 in. XFRMR length < DE. No clamps -- search picks the natural minimum.")

    best_score = 1e9
    best = None

    # Coarse: full real-world spacing range, full length range up to DE-1
    log("  -- coarse 2D grid: spacing 4..32 step 1, length DE-30..DE-1 step 1 --")
    for xsp in frange(4.0, 32.0, 1.0):
        for xL in frange(de_len - 30.0, de_len - 1.0, 1.0):
            elements = [
                Element("XFRMR", DE_START_POS_IN - xsp, xL),
                Element("DE", DE_START_POS_IN, de_len),
            ]
            try:
                ev = eval_center(elements, ant, engine)
            except Exception:
                continue
            r, x = ev["R"], ev["X"]
            sc = abs(r - 50.0) + 0.4 * abs(x)
            log_rows.append({
                "phase": 2, "stage": "p2_coarse",
                "xfrmr_spacing_in": xsp, "xfrmr_length_in": xL,
                "R": r, "X": x, "swr": ev["swr"], "score": sc,
            })
            if sc < best_score:
                best_score = sc
                best = (xsp, xL, r, x, ev["swr"])

    if best is None:
        raise RuntimeError("Phase 2 coarse grid produced no valid result")
    xsp_c, xL_c, r_c, x_c, swr_c = best
    edge = ""
    if xsp_c >= 31.5:  edge = "  (at 32-in physical cap -- consider widening)"
    elif xsp_c <= 4.5: edge = "  (at 4-in physical floor)"
    log(f"  coarse best: xsp={xsp_c:.2f}  xL={xL_c:.2f}  R={r_c:.2f}  X={x_c:+.2f}  swr={swr_c:.2f}{edge}")

    # Fine: +/-2 around coarse winner at step 0.25; still bounded by 4..32 and XFRMR<DE
    log("  -- fine 2D grid (step 0.25; bounded by 4..32 and XFRMR<DE) --")
    _xsp_lo = max(4.0, xsp_c - 2.0)
    _xsp_hi = min(32.0, xsp_c + 2.0)
    _xL_hi  = min(xL_c + 2.0, de_len - 0.25)
    for xsp in frange(_xsp_lo, _xsp_hi, 0.25):
        for xL in frange(xL_c - 2.0, _xL_hi, 0.25):
            elements = [
                Element("XFRMR", DE_START_POS_IN - xsp, xL),
                Element("DE", DE_START_POS_IN, de_len),
            ]
            try:
                ev = eval_center(elements, ant, engine)
            except Exception:
                continue
            r, x = ev["R"], ev["X"]
            sc = abs(r - 50.0) + 0.4 * abs(x)
            log_rows.append({
                "phase": 2, "stage": "p2_fine",
                "xfrmr_spacing_in": xsp, "xfrmr_length_in": xL,
                "R": r, "X": x, "swr": ev["swr"], "score": sc,
            })
            if sc < best_score:
                best_score = sc
                best = (xsp, xL, r, x, ev["swr"])

    xsp, xL, r, x, _ = best
    log(f"  Phase 2 LOCKED: xfrmr_spacing={xsp:.3f} in  xfrmr_length={xL:.3f} in")
    log(f"                  R={r:.2f}  X={x:+.2f}")
    return xsp, xL
# ---------------------------------------------------------------------------
# Phase 3
# ---------------------------------------------------------------------------
def phase3(ant, engine, de_len, xsp, xL, log_rows):
    """Add COUPLER in front of DE. Real-world spacing range 4-32 in. COUPLER length < DE."""
    log()
    log("Phase 3: Add COUPLER in front of DE")
    log("===================================")
    log("Real-world spacing: 4-32 in. COUPLER length < DE. No clamps -- search picks the natural minimum.")

    best_score = 1e9
    best = None

    log("  -- coarse 2D grid: spacing 4..32 step 1, length DE-30..DE-2 step 1 --")
    for csp in frange(4.0, 32.0, 1.0):
        for cL in frange(de_len - 30.0, de_len - 2.0, 1.0):
            elements = [
                Element("XFRMR", DE_START_POS_IN - xsp, xL),
                Element("DE", DE_START_POS_IN, de_len),
                Element("COUPLER", DE_START_POS_IN + csp, cL),
            ]
            try:
                ev = eval_center(elements, ant, engine)
            except Exception:
                continue
            r, x = ev["R"], ev["X"]
            sc = abs(x) + 0.5 * abs(r - 50.0)
            log_rows.append({
                "phase": 3, "stage": "p3_coarse",
                "coupler_spacing_in": csp, "coupler_length_in": cL,
                "R": r, "X": x, "swr": ev["swr"], "score": sc,
            })
            if sc < best_score:
                best_score = sc
                best = (csp, cL, r, x, ev["swr"])

    if best is None:
        raise RuntimeError("Phase 3 coarse grid produced no valid result")
    csp_c, cL_c, r_c, x_c, swr_c = best
    edge = ""
    if csp_c >= 31.5:  edge = "  (at 32-in physical cap -- consider widening)"
    elif csp_c <= 4.5: edge = "  (at 4-in physical floor)"
    log(f"  coarse best: csp={csp_c:.2f}  cL={cL_c:.2f}  R={r_c:.2f}  X={x_c:+.2f}  swr={swr_c:.2f}{edge}")

    # Fine: +/-2 around coarse winner; spacing step 0.25, length step 1/16; bounded by 4..32 and COUPLER<DE
    log("  -- fine 2D grid (csp step 0.25, cL step 1/16; bounded by 4..32 and COUPLER<DE) --")
    _csp_lo = max(4.0, csp_c - 2.0)
    _csp_hi = min(32.0, csp_c + 2.0)
    _cL_hi  = min(cL_c + 2.0, de_len - 0.25)
    for csp in frange(_csp_lo, _csp_hi, 0.25):
        for cL in frange(cL_c - 2.0, _cL_hi, 0.0625):
            elements = [
                Element("XFRMR", DE_START_POS_IN - xsp, xL),
                Element("DE", DE_START_POS_IN, de_len),
                Element("COUPLER", DE_START_POS_IN + csp, cL),
            ]
            try:
                ev = eval_center(elements, ant, engine)
            except Exception:
                continue
            r, x = ev["R"], ev["X"]
            sc = abs(x) + 0.5 * abs(r - 50.0)
            log_rows.append({
                "phase": 3, "stage": "p3_fine",
                "coupler_spacing_in": csp, "coupler_length_in": cL,
                "R": r, "X": x, "swr": ev["swr"], "score": sc,
            })
            if sc < best_score:
                best_score = sc
                best = (csp, cL, r, x, ev["swr"])

    csp, cL, r, x, _ = best
    log(f"  Phase 3 LOCKED: coupler_spacing={csp:.3f} in  coupler_length={cL:.3f} in")
    log(f"                  R={r:.2f}  X={x:+.2f}")
    return csp, cL
# ---------------------------------------------------------------------------
# Final + save
# ---------------------------------------------------------------------------
def build_elements(de_len, xsp, xL, csp, cL):
    return [
        Element("XFRMR", DE_START_POS_IN - xsp, xL),
        Element("DE", DE_START_POS_IN, de_len),
        Element("COUPLER", DE_START_POS_IN + csp, cL),
    ]


def current_geometry(elements):
    def find(n):
        for e in elements:
            if e.name.upper() == n:
                return e
        raise RuntimeError(f"missing {n}")
    x = find("XFRMR")
    de = find("DE")
    c = find("COUPLER")
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


def save_outputs(elements, full_eval, log_rows, force_promote=False):
    ensure_dirs()
    tag = now_tag()

    ant = make_ant()
    nec_text = generate_nec_text(elements, ant, F_START, F_STOP, F_STEP)

    nec_path = MODELS_DIR / f"learn_cell_cascade_{tag}.nec"
    json_path = OUT_DIR / f"learn_cell_cascade_best_{tag}.json"
    log_path = OUT_DIR / f"learn_cell_cascade_moves_{tag}.jsonl"
    summary_path = OUT_DIR / f"learn_cell_cascade_summary_{tag}.txt"
    seed_path = OUT_DIR / "best_cell_seed.json"

    nec_path.write_text(nec_text, encoding="utf-8")

    g = current_geometry(elements)
    s = full_eval["summary"]
    score = band_score(full_eval)

    real_gain_dbi = None
    peak_elev_deg = None
    try:
        pat = evaluate_pattern_for_cell(elements, CENTER_FREQ_MHZ, ant)
        real_gain_dbi = getattr(pat, "real_gain_dbi", None)
        peak_elev_deg = getattr(pat, "peak_elev_deg", None)
        if real_gain_dbi is not None and peak_elev_deg is not None:
            log(f"Ground pattern: real_gain={real_gain_dbi:.2f} dBi  peak_elev={peak_elev_deg:.1f} deg")
    except Exception as exc:
        log(f"[warn] pattern extraction failed: {exc}")

    best_data = {
        "type": "cell_placement_tune",
        "tune_method": "cascading_procedure",
        "center_freq_mhz": CENTER_FREQ_MHZ,
        "freq_start_mhz": F_START,
        "freq_stop_mhz": F_STOP,
        **g,
        "elements": [
            {"name": e.name, "position_in": e.position_in, "length_in": e.length_in}
            for e in elements
        ],
        "score": score,
        "min_swr": s.min_swr,
        "max_swr": s.max_swr,
        "avg_swr": s.avg_swr,
        "center_r": full_eval["center_r"],
        "center_x": full_eval["center_x"],
        "center_swr": full_eval["center_swr"],
        "center_rl_db": full_eval["center_rl_db"],
        "avg_r": s.avg_r,
        "avg_abs_x": s.avg_abs_x,
        "points_under_1p5": s.points_under_1p5,
        "points_under_2p0": s.points_under_2p0,
        "real_gain_dbi": real_gain_dbi,
        "peak_elev_deg": peak_elev_deg,
        "nec_file": str(nec_path),
    }

    json_path.write_text(json.dumps(best_data, indent=2), encoding="utf-8")

    # Promote-only-if-better policy
    promoted = False
    try:
        existing_score = -1e18
        if seed_path.exists():
            existing = json.loads(seed_path.read_text())
            existing_score = float(existing.get("score", -1e18))
        if score > existing_score or force_promote:
            seed_path.write_text(json.dumps(best_data, indent=2), encoding="utf-8")
            promoted = True
            if force_promote and score <= existing_score:
                log(f"FORCE-PROMOTED: new score {score:.1f} <= existing {existing_score:.1f} but --force-promote set")
                log("(NEC2 scoring may be biased; trusting real-world XFRMR<DE COUPLER<DE rule)")
            else:
                log(f"PROMOTED: new score {score:.1f} beats existing {existing_score:.1f}")
        else:
            log(f"NOT promoted: new score {score:.1f} < existing {existing_score:.1f}")
            log("(best_cell_seed.json untouched; per-run JSON saved alongside)")
            log("(use --force-promote to override and use this physically-correct geometry anyway)")
    except Exception as exc:
        log(f"[warn] seed promotion check failed, keeping existing seed: {exc}")

    with log_path.open("w", encoding="utf-8") as f:
        for row in log_rows:
            f.write(json.dumps(row) + "\n")

    lines = [
        "CASCADING PROCEDURE CELL TUNE REPORT",
        "====================================",
        "",
        "Procedure: Phase 1 (DE only)  ->  Phase 2 (add XFRMR)  ->  Phase 3 (add COUPLER)",
        "",
        f"Best score:           {score:.1f}",
        f"Min SWR:              {s.min_swr:.3f}",
        f"Max SWR:              {s.max_swr:.3f}",
        f"Avg SWR:              {s.avg_swr:.3f}",
        f"Center R:             {full_eval['center_r']:.3f} ohm",
        f"Center X:             {full_eval['center_x']:.3f} ohm",
        f"Center SWR:           {full_eval['center_swr']:.3f}",
        f"Center RL:            {full_eval['center_rl_db']:.3f} dB",
        f"Avg R:                {s.avg_r:.3f} ohm",
        f"Avg |X|:              {s.avg_abs_x:.3f} ohm",
    ]
    if real_gain_dbi is not None:
        lines.append(f"Real gain (ground):   {real_gain_dbi:.2f} dBi")
    if peak_elev_deg is not None:
        lines.append(f"Peak elev angle:      {peak_elev_deg:.1f} deg")
    lines += [
        "",
        "Best cell layout",
        "----------------",
    ]
    for e in elements:
        lines.append(f"{e.name:<8s} pos={e.position_in:8.3f} in  length={e.length_in:8.3f} in")
    lines += [
        "",
        f"XFRMR-DE spacing:     {g['xfrmr_spacing_in']:.3f} in",
        f"DE-COUPLER spacing:   {g['coupler_spacing_in']:.3f} in",
        "",
        f"Best NEC:             {nec_path}",
        f"Best JSON:            {json_path}",
        f"Seed promoted:        {promoted}",
        f"Move log:             {log_path}",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log()
    log("Saved outputs")
    log("=============")
    log(f"Best NEC:   {nec_path}")
    log(f"Best JSON:  {json_path}")
    log(f"Cell seed:  {seed_path}  (promoted={promoted})")
    log(f"Move log:   {log_path}")
    log(f"Summary:    {summary_path}")


def _parse_args():
    # REALWORLD_FIX_v1: --force-promote bypasses score-based gate so physically
    # correct geometry (XFRMR<DE, COUPLER<DE) can replace seed even if NEC2 scores
    # it lower than the legacy (XFRMR>DE) cell which is physically wrong.
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--force-promote", action="store_true",
                   help="Promote new cell to best_cell_seed.json regardless of score")
    return p.parse_args()


def main():
    args = _parse_args()
    ensure_dirs()

    log("Cascading Procedure Cell Tune")
    log("=============================")
    log("Save format matches learn_cell_only.py -- 'Run Full Hybrid Tune")
    log("from Cell Seed' picks up the result automatically when it scores higher.")
    log(f"Center freq: {CENTER_FREQ_MHZ} MHz   Height: 54 ft   Boom: {BOOM_FT} ft")

    ant = make_ant()
    engine = NecppEngine()
    log_rows = []

    de_len, _de_r = phase1(ant, engine, log_rows)
    xsp, xL = phase2(ant, engine, de_len, log_rows)
    csp, cL = phase3(ant, engine, de_len, xsp, xL, log_rows)

    log()
    log("Final full-band evaluation on locked cell")
    log("==========================================")
    elements = build_elements(de_len, xsp, xL, csp, cL)
    full_eval = eval_band(elements, ant, engine)
    score = band_score(full_eval)
    s = full_eval["summary"]
    log(f"  score        = {score:.1f}")
    log(f"  center R/X   = {full_eval['center_r']:.2f}  {full_eval['center_x']:+.2f}")
    log(f"  center SWR   = {full_eval['center_swr']:.3f}")
    log(f"  max/avg SWR  = {s.max_swr:.3f} / {s.avg_swr:.3f}")
    log(f"  under 1.5/2.0 = {s.points_under_1p5} / {s.points_under_2p0}")

    save_outputs(elements, full_eval, log_rows, force_promote=args.force_promote)


if __name__ == "__main__":
    main()
