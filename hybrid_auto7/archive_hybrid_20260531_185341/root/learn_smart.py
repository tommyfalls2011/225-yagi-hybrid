#!/usr/bin/env python3
"""
LEARN_SMART_v1
Two-phase learning with persistent knowledge base:

Phase 1: CELL EXPLORATION
  - Random DE position sweep [36..72"] on 18ft boom
  - XFRMR locked at DE-13", COUPLER locked at DE+13"
  - Score on SWR + cell impedance only
  - Top-K successful cells -> "good_cells" in KB
  - Failed/SWR-explode cells -> "bad_starts" in KB (skip in future runs)

Phase 2: BUILD-OUT
  - For each good cell from Phase 1 (or KB if --use-kb):
    - Add REF, sweep length
    - Add DIR1 + tune
    - Add DIR2 + tune
    - Add DIR3 + tune
    - Score full antenna with real_gain_dbi + F/B + SWR
  - If beats KB best -> save as new "best_full_designs"
  - If worse than KB best -> log as "dead_paths"

Knowledge base: data/smart_kb.json (persistent across runs)

Usage:
  python3 ./learn_smart.py                    # full new run
  python3 ./learn_smart.py --use-kb           # use stored good_cells as seeds, skip Phase 1
  python3 ./learn_smart.py --trials 30        # number of random DE positions
  python3 ./learn_smart.py --top-k 5          # how many good cells to build out
"""
import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from hyagi.config import AntennaConfig, frange
from hyagi.model import Element, validate_elements
from hyagi.engine import NecppEngine
from hyagi.physics import summarize, return_loss_db
from hyagi.pattern import evaluate_pattern_for_elements
from hyagi.paths import DATA_DIR, ensure_dirs

# PATCH: module-level default so phase2_buildout doesn't NameError
_DB_CON = None


# ----------- constants -----------
CENTER_FREQ_MHZ = 27.195
F_START, F_STOP, F_STEP = 26.965, 27.405, 0.01

# MODES_BOOM_v1: BOOM/MODE/DIR count are now CLI args (defaults below)
BOOM_FT = 18.0
BOOM_IN = BOOM_FT * 12.0
args = None  # set in main()
DE_BASE_IN = 5616.0 / CENTER_FREQ_MHZ  # ~206.5"

# Phase 1 search space
DE_MIN, DE_MAX = 36.0, 72.0
CELL_XSP = 13.0       # Phase 1 seed: XFRMR 13" behind DE (fast cell find)
CELL_CSP = 13.0       # Phase 1 seed: COUPLER 13" ahead of DE
# SPACING_TUNE_v1: Phase 2 spacing search bounds
XSP_MIN, XSP_MAX, XSP_STEP = 4.0, 36.0, 2.0   # XFRMR spacing range behind DE
CSP_MIN, CSP_MAX, CSP_STEP = 4.0, 36.0, 2.0   # COUPLER spacing range ahead of DE

KB_FILE = DATA_DIR / "smart_kb.json"
LOG_DIR = DATA_DIR / "smart_runs"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg=""):
    print(msg, flush=True)


def make_ant(boom_in=None):
    return AntennaConfig(
        boom_length_in=boom_in if boom_in is not None else BOOM_IN,
        boom_diameter_in=1.5,
        center_od_in=0.625, outer_od_in=0.500,
        center_half_len_in=36.0,
        model_height_in=54.0 * 12.0,
        ground_mode="average",
        ground_epsr=13.0, ground_sigma_s_per_m=0.005,
        cell_mounting_style="full_cell_insulated",
    )


def load_kb():
    if not KB_FILE.exists():
        return {
            "good_cells": [],
            "bad_starts": [],
            "best_full_designs": [],
            "dead_paths": [],
            "schema_version": 1,
        }
    return json.loads(KB_FILE.read_text())


def save_kb(kb):
    KB_FILE.parent.mkdir(parents=True, exist_ok=True)
    KB_FILE.write_text(json.dumps(kb, indent=2))


def in_bad_zone(de_pos, kb, tol=2.0):
    for b in kb.get("bad_starts", []):
        if abs(float(b["de_pos"]) - de_pos) < tol:
            return True
    return False


def score_cell(summary, center_r, center_x):
    s = 0.0
    s += summary.points_under_2p0 * 120.0
    s += summary.points_under_1p5 * 140.0
    s -= summary.max_swr * 180.0
    s -= summary.avg_swr * 80.0
    s -= abs(center_r - 50.0) * 8.0
    s -= abs(center_x) * 10.0
    return s


def score_full(summary, center_r, center_x, pattern):
    s = score_cell(summary, center_r, center_x)
    if pattern is not None:
        s += pattern.real_gain_dbi * 200.0
        s += min(pattern.front_back_db, 35.0) * 25.0  # cap-don't-clamp F/B
    return s


def eval_elements(elements, ant, engine, freqs, want_pattern=False):
    validate_elements(elements, ant)
    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)
    center = min(results, key=lambda r: abs(r.freq_mhz - CENTER_FREQ_MHZ))
    pat = None
    if want_pattern:
        try:
            pat = evaluate_pattern_for_elements(elements, freq_mhz=CENTER_FREQ_MHZ, ant=ant)
        except Exception as exc:
            log(f"  WARN pattern unavailable: {exc}")
    return {
        "summary": summary, "center_r": center.r_ohm, "center_x": center.x_ohm,
        "center_swr": center.swr_50, "pattern": pat,
    }


# SEED_LENGTHS_v2: real-world physics seeds (XFRMR < DE, COUPLER << DE)
# Derived from your tuned 7-element project (run 3651, score 13417, gain 13.09 dBi)
SEED_DE_LEN      = 210.0   # DE length (full-size half-wave-ish at 27.2 MHz)
SEED_REF_DELTA   = +8.0    # REF =   DE + 8"   (218")  --> reflector longer
SEED_XFRMR_DELTA = -10.5   # XFRMR = DE - 10.5" (199.5")  --> hybrid transformer
SEED_COUPLER_DELTA = -37.0 # COUPLER = DE - 37" (173")  --> short coupler
SEED_DIR1_DELTA  = -15.5
SEED_DIR2_DELTA  = -22.0
SEED_DIR3_DELTA  = -48.5

def build_cell_elements(de_pos, ref_len=None):
    """7-element model honoring real-world physics: XFRMR < DE, COUPLER << DE."""
    de_len = SEED_DE_LEN
    return [
        Element("REF", 0.0, ref_len if ref_len is not None else (de_len + SEED_REF_DELTA)),
        Element("XFRMR", de_pos - CELL_XSP, de_len + SEED_XFRMR_DELTA),
        Element("DE", de_pos, de_len),
        Element("COUPLER", de_pos + CELL_CSP, de_len + SEED_COUPLER_DELTA),
        Element("DIR1", min(BOOM_IN - 1.0, de_pos + 48.0), de_len + SEED_DIR1_DELTA),
        Element("DIR2", min(BOOM_IN - 1.0, de_pos + 96.0), de_len + SEED_DIR2_DELTA),
        Element("DIR3", min(BOOM_IN - 1.0, de_pos + 144.0), de_len + SEED_DIR3_DELTA),
    ]





# MODES_BOOM_v1: hybrid builder with variable n_directors
def build_hybrid_elements(de_pos, boom_in, n_directors, ref_len=None,
                          xsp=None, csp=None,
                          xfrmr_len=None, coupler_len=None):
    de_len = SEED_DE_LEN
    xsp = xsp if xsp is not None else CELL_XSP
    csp = csp if csp is not None else CELL_CSP
    elements = [
        Element("REF", 0.0, ref_len if ref_len is not None else (de_len + SEED_REF_DELTA)),
        Element("XFRMR", de_pos - xsp, xfrmr_len if xfrmr_len is not None else (de_len + SEED_XFRMR_DELTA)),
        Element("DE", de_pos, de_len),
        Element("COUPLER", de_pos + csp, coupler_len if coupler_len is not None else (de_len + SEED_COUPLER_DELTA)),
    ]
    # Directors after COUPLER, evenly spaced to boom end
    coupler_pos = de_pos + csp
    span = (boom_in - 1.0) - coupler_pos
    if n_directors > 0 and span > 0:
        step = span / float(n_directors)
        deltas = [-15.5, -20.0, -24.0, -28.0, -32.0, -36.0, -40.0, -44.0]
        for i in range(n_directors):
            pos = coupler_pos + step * (i + 1)
            elements.append(Element(f"DIR{i+1}", min(boom_in - 1.0, pos),
                                    de_len + deltas[min(i, len(deltas)-1)]))
    return elements


# ===========================================================
# PHASE 1: random cell exploration
# ===========================================================
def phase1_explore(kb, n_trials, ant, engine, freqs, top_k):
    log()
    log("=" * 64)
    log("PHASE 1: random DE-position cell exploration")
    log("=" * 64)
    log(f"Search range: DE = [{DE_MIN}, {DE_MAX}] in")
    log(f"Locked: XFRMR @ DE-{CELL_XSP}\"  COUPLER @ DE+{CELL_CSP}\"")
    log(f"Trials: {n_trials}  |  top-K to keep: {top_k}")
    log(f"Known bad starts in KB: {len(kb.get('bad_starts', []))}  (will skip)")
    log()

    results = []
    skipped = 0
    for i in range(n_trials):
        de = round(random.uniform(DE_MIN, DE_MAX), 1)
        if in_bad_zone(de, kb):
            skipped += 1
            continue
        elements = build_hybrid_elements(de, BOOM_IN, args.n_directors)
        try:
            ev = eval_elements(elements, ant, engine, freqs, want_pattern=False)
            sc = score_cell(ev["summary"], ev["center_r"], ev["center_x"])
            verdict = "OK" if ev["summary"].max_swr < 3.0 else "swr_explode"
            log(
                f"  trial {i+1:3d}/{n_trials}  de={de:5.1f}  score={sc:+9.1f}  "
                f"maxSWR={ev['summary'].max_swr:5.2f}  R={ev['center_r']:5.1f}  X={ev['center_x']:+6.1f}  [{verdict}]"
            )
            results.append({
                "de_pos": de,
                "xfrmr_pos": de - CELL_XSP,
                "coupler_pos": de + CELL_CSP,
                "score": sc,
                "max_swr": ev["summary"].max_swr,
                "center_r": ev["center_r"],
                "center_x": ev["center_x"],
                "verdict": verdict,
                "tried_at": now_iso(),
            })
            if verdict == "swr_explode":
                kb["bad_starts"].append({
                    "de_pos": de, "reason": "swr_explode",
                    "max_swr": ev["summary"].max_swr, "logged_at": now_iso(),
                })
        except Exception as exc:
            log(f"  trial {i+1:3d}/{n_trials}  de={de:5.1f}  FAILED: {exc}")
            kb["bad_starts"].append({
                "de_pos": de, "reason": "exception", "msg": str(exc), "logged_at": now_iso(),
            })

    log()
    log(f"Phase 1 done. Tested {len(results)} (skipped {skipped} known-bad).")
    # Top-K successful by score
    ok = [r for r in results if r["verdict"] == "OK"]
    ok.sort(key=lambda r: r["score"], reverse=True)
    top = ok[:top_k]
    log(f"Top {len(top)} cells:")
    for i, c in enumerate(top, 1):
        log(f"  #{i}  de={c['de_pos']:5.1f}  score={c['score']:+9.1f}  maxSWR={c['max_swr']:.3f}")

    # Merge top into KB good_cells (dedupe by de_pos rounded to nearest 0.5)
    existing = {round(c["de_pos"] * 2) / 2 for c in kb["good_cells"]}
    added = 0
    for c in top:
        key = round(c["de_pos"] * 2) / 2
        if key not in existing:
            kb["good_cells"].append(c)
            existing.add(key)
            added += 1
    log(f"Added {added} new good cells to KB ({len(kb['good_cells'])} total).")
    return top




# SPACING_TUNE_v1: tune XFRMR or COUPLER spacing (4"-36" from DE)
def tune_spacing(elements, which, ant, engine, freqs, want_pattern=False):
    import time as _t
    _t0 = _t.time()
    """which = 'XFRMR' (sweep behind DE) or 'COUPLER' (sweep ahead of DE)."""
    de_pos = next(e.position_in for e in elements if e.name == "DE")
    if which == "XFRMR":
        spacings = list(frange(XSP_MIN, XSP_MAX, XSP_STEP))
        pos_fn = lambda sp: de_pos - sp
    else:
        spacings = list(frange(CSP_MIN, CSP_MAX, CSP_STEP))
        pos_fn = lambda sp: de_pos + sp

    best = elements
    best_ev = eval_elements(best, ant, engine, freqs, want_pattern=want_pattern)
    best_sc = score_full(best_ev["summary"], best_ev["center_r"], best_ev["center_x"], best_ev["pattern"])
    best_sp = next(abs(e.position_in - de_pos) for e in best if e.name == which)

    for sp in spacings:
        cand = [Element(e.name, e.position_in, e.length_in) for e in best]
        for e in cand:
            if e.name == which:
                e.position_in = pos_fn(sp)
                break
        try:
            ev = eval_elements(cand, ant, engine, freqs, want_pattern=want_pattern)
            sc = score_full(ev["summary"], ev["center_r"], ev["center_x"], ev["pattern"])
            if sc > best_sc:
                best, best_ev, best_sc, best_sp = cand, ev, sc, sp
        except Exception:
            pass
    log(f"    {which} spacing tune: best_sp={best_sp:.1f}\"  score={best_sc:+.1f}")
    return best, best_ev, best_sc

# ===========================================================
# PHASE 2: build full antenna from each good cell
# ===========================================================
def tune_param_1d(elements, name, attr, values, ant, engine, freqs, log_label, want_pattern):
    import time as _t
    _t0 = _t.time()
    _n = sum(1 for _ in values) if hasattr(values, '__iter__') else 0
    log(f"    {log_label}: starting ({_n} candidates)...")  # PHASE2_VERBOSE_v1
    best = elements
    best_ev = eval_elements(best, ant, engine, freqs, want_pattern=want_pattern)
    best_sc = score_full(best_ev["summary"], best_ev["center_r"], best_ev["center_x"], best_ev["pattern"])
    for v in values:
        cand = [Element(e.name, e.position_in, e.length_in) for e in best]
        for e in cand:
            if e.name == name:
                setattr(e, attr, float(v))
                break
        try:
            ev = eval_elements(cand, ant, engine, freqs, want_pattern=want_pattern)
            sc = score_full(ev["summary"], ev["center_r"], ev["center_x"], ev["pattern"])
            if sc > best_sc:
                best, best_ev, best_sc = cand, ev, sc
        except Exception:
            pass
    log(f"    {log_label}: best={best_sc:+.1f}  ({_t.time()-_t0:.1f}s)")
    return best, best_ev, best_sc


def phase2_buildout(top_cells, kb, ant, engine, freqs):
    log()
    log("=" * 64)
    log("PHASE 2: build full 7-element antenna from each good cell")
    log("=" * 64)

    kb_best_score = max((d["score"] for d in kb.get("best_full_designs", [])), default=float("-inf"))
    log(f"Current KB best score: {kb_best_score:+.1f}")
    log()

    for ci, cell in enumerate(top_cells, 1):
        log(f"Cell {ci}/{len(top_cells)}  de_pos={cell['de_pos']:.1f}  (cell_score={cell['score']:+.1f})")

        # Start from cell + default REF/DIR positions
        if args.lock_best_cell and kb.get("best_full_designs"):
            # Lock XFRMR/DE/COUPLER from KB best, only REF + DIRs will tune
            _kb_best = max(kb["best_full_designs"], key=lambda d: d["score"])
            _kbe = {e["name"]: e for e in _kb_best["elements"]}
            elements = build_hybrid_elements(
                _kbe["DE"]["position_in"], BOOM_IN, args.n_directors,
                xsp=_kbe["DE"]["position_in"] - _kbe["XFRMR"]["position_in"],
                csp=_kbe["COUPLER"]["position_in"] - _kbe["DE"]["position_in"],
                xfrmr_len=_kbe["XFRMR"]["length_in"],
                coupler_len=_kbe["COUPLER"]["length_in"],
            )
            log(f"  [lock-best-cell] using KB best cell from score {_kb_best['score']:+.1f}")
        else:
            elements = build_hybrid_elements(cell["de_pos"], BOOM_IN, args.n_directors)

        # REF length sweep
        elements, ev, sc = tune_param_1d(
            elements, "REF", "length_in",
            frange(DE_BASE_IN - 24, DE_BASE_IN + 48, 2.0),
            ant, engine, freqs, "REF length", want_pattern=False
        )

        if not args.lock_best_cell:
            elements, ev, sc = tune_spacing(elements, "XFRMR", ant, engine, freqs, want_pattern=False)
            elements, ev, sc = tune_spacing(elements, "COUPLER", ant, engine, freqs, want_pattern=False)

        # MODES_BOOM_v1: generic DIR loop (1..n_directors)
        # First DIR anchored relative to last static element (COUPLER for hybrid, DE for yagi)
        anchor_pos = next(e.position_in for e in elements if e.name == "COUPLER")
        first_dir_start = anchor_pos + 24
        first_dir_stop  = anchor_pos + 72
        for di in range(1, args.n_directors + 1):
            dname = f"DIR{di}"
            if di == 1:
                pos_lo, pos_hi = first_dir_start, first_dir_stop
            else:
                prev_pos = next(e.position_in for e in elements if e.name == f"DIR{di-1}")
                pos_lo = prev_pos + 18
                pos_hi = min(BOOM_IN - 1, prev_pos + 72)
            if pos_hi - pos_lo < 6:  # not enough room
                continue
            elements, ev, sc = tune_param_1d(
                elements, dname, "position_in",
                frange(pos_lo, pos_hi, 3.0),
                ant, engine, freqs, f"{dname} pos", want_pattern=True
            )
            len_lo = DE_BASE_IN - 24 - (di * 4)
            len_hi = DE_BASE_IN - 4 - (di * 2)
            elements, ev, sc = tune_param_1d(
                elements, dname, "length_in",
                frange(len_lo, len_hi, 1.5),
                ant, engine, freqs, f"{dname} len", want_pattern=True
            )

        if not args.lock_best_cell:
            log("    -- final spacing repass with directors locked --")
            elements, ev, sc = tune_spacing(elements, "XFRMR", ant, engine, freqs, want_pattern=True)
            elements, ev, sc = tune_spacing(elements, "COUPLER", ant, engine, freqs, want_pattern=True)

        pat = ev["pattern"]
        gain = pat.real_gain_dbi if pat else None
        fb = pat.front_back_db if pat else None
        log(
            f"  final: score={sc:+.1f}  maxSWR={ev['summary'].max_swr:.3f}  "
            f"gain={gain if gain is None else f'{gain:.2f} dBi'}  "
            f"F/B={fb if fb is None else f'{fb:.2f} dB'}"
        )

        # YAGI_KB_v1: spacing fields conditional on element presence
        _de = next(e.position_in for e in elements if e.name == "DE")
        _xs = (_de - next((e.position_in for e in elements if e.name == "XFRMR"), _de))               if any(e.name == "XFRMR" for e in elements) else None
        _cs = (next((e.position_in for e in elements if e.name == "COUPLER"), _de) - _de)               if any(e.name == "COUPLER" for e in elements) else None
        design_entry = {
            "from_cell_de_pos": cell["de_pos"],
            "boom_ft": args.boom_ft,
            "n_directors": args.n_directors,
            "label": args.label,
            "final_de_pos": _de,
            "final_xfrmr_spacing": round(_xs, 2) if _xs is not None else None,
            "final_coupler_spacing": round(_cs, 2) if _cs is not None else None,
            "score": sc,
            "max_swr": ev["summary"].max_swr,
            "avg_swr": ev["summary"].avg_swr,
            "gain_dbi": float(gain) if gain is not None else None,
            "fb_db": float(fb) if fb is not None else None,
            "elements": [{"name": e.name, "position_in": e.position_in, "length_in": e.length_in} for e in elements],
            "saved_at": now_iso(),
        }

        # DB_LEARN_v1: always save final design (winner or not, for full analysis)
        if _DB_CON is not None:
            try:
                _DB_CON.execute("""
                    INSERT INTO smart_designs
                    (run_id, saved_at, mode, label, boom_ft, n_directors,
                     score, gain_dbi, fb_db, max_swr, avg_swr, elements_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (_RUN_ID,
                     datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "hybrid",
                     getattr(args, "label", None),
                     getattr(args, "boom_ft", None),
                     getattr(args, "n_directors", None),
                     sc, float(gain) if gain is not None else None,
                     float(fb) if fb is not None else None,
                     ev["summary"].max_swr, ev["summary"].avg_swr,
                     json.dumps([{"name": e.name, "position_in": e.position_in, "length_in": e.length_in} for e in elements])))
            except Exception as exc:
                log(f"[db-learn] design insert failed: {exc}")

        if sc > kb_best_score:
            kb["best_full_designs"].append(design_entry)
            kb_best_score = sc
            log(f"  >>> NEW KB BEST  (+{sc - kb_best_score:.1f} delta over previous)")
        else:
            kb["dead_paths"].append({
                "from_cell_de_pos": cell["de_pos"],
                "score": sc,
                "delta_to_best": sc - kb_best_score,
                "reason": "worse_than_kb_best",
                "logged_at": now_iso(),
            })
        log()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=20, help="random DE positions to try in phase 1")
    p.add_argument("--top-k", type=int, default=3, help="how many top cells to build out in phase 2")
    p.add_argument("--use-kb", action="store_true", help="skip phase 1, use stored good_cells")
    p.add_argument("--seed", type=int, default=None, help="random seed (for reproducibility)")
    # MODES_BOOM_v1 new flags (hybrid-only)
    p.add_argument("--boom-ft", type=float, default=18.0, help="boom length in feet")
    p.add_argument("--n-directors", type=int, default=3, help="number of directors")
    p.add_argument("--lock-best-cell", action="store_true",
                   help="hybrid only: lock XFRMR/DE/COUPLER from KB best_full_designs and only tune REF + DIRs")
    p.add_argument("--label", type=str, default=None,
                   help="tag for this run in KB (e.g. '30ft_6el_yagi')")
    global args, BOOM_FT, BOOM_IN  # MODES_BOOM_v2_args_global
    args = p.parse_args(argv)
    BOOM_FT = args.boom_ft
    BOOM_IN = BOOM_FT * 12.0

    if args.seed is not None:
        random.seed(args.seed)

    ensure_dirs()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ant = make_ant()
    engine = NecppEngine()
    freqs = frange(F_START, F_STOP, F_STEP)
    kb = load_kb()

    log()
    log("LEARN_SMART_v1 (MODES_BOOM_v1)")
    log("=" * 32)
    log(f"n directors:    {args.n_directors}")
    if args.label: log(f"Label:          {args.label}")
    log(f"Boom: {BOOM_FT} ft / {BOOM_IN} in")
    log(f"KB: {KB_FILE}")
    log(f"  good_cells:        {len(kb.get('good_cells', []))}")
    log(f"  bad_starts:        {len(kb.get('bad_starts', []))}")
    log(f"  best_full_designs: {len(kb.get('best_full_designs', []))}")
    log(f"  dead_paths:        {len(kb.get('dead_paths', []))}")

    t0 = time.time()

    if args.use_kb:
        log()
        log("--use-kb: skipping Phase 1, using stored good_cells")
        good = sorted(kb["good_cells"], key=lambda c: c["score"], reverse=True)[:args.top_k]
        if not good:
            log("ERROR: no good_cells in KB. Run without --use-kb first.")
            return 1
        top_cells = good
    else:
        top_cells = phase1_explore(kb, args.trials, ant, engine, freqs, args.top_k)
        save_kb(kb)  # persist phase 1 results

    if top_cells:
        phase2_buildout(top_cells, kb, ant, engine, freqs)
        save_kb(kb)

    # Final summary
    elapsed = time.time() - t0
    log()
    log("=" * 64)
    log("RUN COMPLETE")
    log("=" * 64)
    log(f"Elapsed: {elapsed:.1f}s")
    log(f"KB now has:")
    log(f"  good_cells:        {len(kb['good_cells'])}")
    log(f"  bad_starts:        {len(kb['bad_starts'])}")
    log(f"  best_full_designs: {len(kb['best_full_designs'])}")
    log(f"  dead_paths:        {len(kb['dead_paths'])}")

    if kb["best_full_designs"]:
        best = max(kb["best_full_designs"], key=lambda d: d["score"])
        log()
        log("KB BEST DESIGN")
        log("--------------")
        log(f"  score:    {best['score']:+.1f}")
        log(f"  gain:     {best['gain_dbi']} dBi")
        log(f"  F/B:      {best['fb_db']} dB")
        log(f"  max SWR:  {best['max_swr']:.3f}")
        log(f"  from_cell de_pos: {best['from_cell_de_pos']}")
        for e in best["elements"]:
            log(f"    {e['name']:<8} pos={e['position_in']:7.2f}  len={e['length_in']:7.2f}")

    # Save run log snapshot
    snap = LOG_DIR / f"smart_run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    snap.write_text(json.dumps({
        "elapsed_sec": elapsed,
        "args": vars(args),
        "kb_state_after": kb,
    }, indent=2))
    log(f"\nSnapshot: {snap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# RICH_REPORT_HOOK_V1
try:
    import sys as _sys, pathlib as _pl
    _sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from hyagi.rich_report import print_rich_report
    print_rich_report()
except Exception as _e:
    print(f"[rich_report] err: {_e}")
