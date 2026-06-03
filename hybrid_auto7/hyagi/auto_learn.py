"""hybrid_auto7 — closed-loop self-learning tuner.

This is the missing piece that turns the hybrid optimizer into a real
SELF-LEARNING system. Each "generation":

  1. Runs a tuning procedure (existing v2_runner mini-tunes) against the
     current geometry.
  2. Auto-adopts the result if it improved (closing the loop — no manual
     "Adopt geometry" click needed).
  3. Does a fine frequency sweep across the band and records EVERYTHING to the
     SQL database (auto7_history.db): the run summary, every element, and the
     full SWR/impedance curve.
  4. LEARNS from the per-candidate move logs — which element/length/position
     values actually lowered SWR / raised the score — and narrows the search
     around those proven values for the next generation, so each run starts
     smarter and converges.

Stops when SWR <= target (default 1.2) across the whole band, or when no
generation improves for `patience` rounds, or after `max_generations`.

Every antenna is different (height, boom, element diameters, band), so warm
starts are matched to a design signature, never blindly reused.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import v2_runner, v2_scorer
from .db import connect, now_utc
from .paths import DATA_DIR, ensure_dirs

INCH = 0.0254
FT = 0.3048


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class LearnConfig:
    project_name: str = "current_geometry"
    height_ft: float = 30.0
    swr_profile: str = "wideband_1.2"   # hard wideband target ~1.2:1
    target_max_swr: float = 1.2       # stop when band max SWR <= this
    band_sweep_points: int = 21       # fine sweep for stop-check + DB curve
    max_generations: int = 12
    patience: int = 3                 # stop after N gens with no improvement
    narrow_window_in: float = 3.0     # learning: +/- window around best value
    narrow_step_in: float = 0.25      # learning: finer step when narrowing
    db_path: str | None = None        # None -> default auto7_history.db


# ---------------------------------------------------------------------------
# Band sweep (fine) for stop-check + full SWR curve in the DB
# ---------------------------------------------------------------------------
def sweep_band(elements, f_low, f_high, points, height_ft):
    """Run one nec2c sweep across the band. Returns list of
    (freq, r, x, swr) and the max SWR across the band."""
    points = max(2, int(points))
    freqs = [f_low + i * (f_high - f_low) / (points - 1) for i in range(points)]
    nec = v2_runner.build_nec_card(elements, freqs, height_ft=height_ft)

    import os
    import pathlib
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as fh:
        fh.write(nec)
        nec_path = fh.name
    out_path = nec_path.replace(".nec", ".out")
    try:
        subprocess.run(["nec2c", "-i", nec_path, "-o", out_path],
                       capture_output=True, text=True, timeout=120)
        if not pathlib.Path(out_path).exists():
            return [], 99.0
        text = pathlib.Path(out_path).read_text()
    finally:
        for p in (nec_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass

    impedances, _pattern = v2_runner.parse_nec_output(text)
    curve = []
    for i, (r, x) in enumerate(impedances):
        f = freqs[i] if i < len(freqs) else freqs[-1]
        curve.append((round(f, 4), float(r), float(x), float(v2_runner.swr(r, x))))
    max_swr = max((c[3] for c in curve), default=99.0)
    return curve, max_swr


# ---------------------------------------------------------------------------
# Database persistence (writes into the user's existing auto7_history.db)
# ---------------------------------------------------------------------------
def _el(elements, name):
    for e in elements:
        if str(e.get("name", "")).upper() == name:
            return e
    return None


def _pos(elements, name):
    e = _el(elements, name)
    return float(e["position_in"]) if e else 0.0


def _len(elements, name):
    e = _el(elements, name)
    return float(e["length_in"]) if e else 0.0


def save_generation(con, cfg, gen, stage, elements, metrics, curve, f_step):
    """Persist one generation to auto7_history.db (runs + elements + freq_results)."""
    cur = con.cursor()
    design_key = f"{cfg.project_name}|g{gen}|{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    de_pos = _pos(elements, "DE")
    xfrmr_pos = _pos(elements, "XFRMR")
    coupler_pos = _pos(elements, "COUPLER")

    swrs = [c[3] for c in curve] if curve else [metrics.get("max_swr", 99.0)]
    rs = [c[1] for c in curve] if curve else [metrics.get("center_r", 0.0)]
    xs = [abs(c[2]) for c in curve] if curve else [abs(metrics.get("center_x", 0.0))]
    min_swr = min(swrs)
    max_swr = max(swrs)
    avg_swr = sum(swrs) / len(swrs)
    under_1p5 = sum(1 for s in swrs if s <= 1.5)
    under_2p0 = sum(1 for s in swrs if s <= 2.0)

    f_low = cfg_band[0]
    f_high = cfg_band[1]

    cur.execute("""
        INSERT INTO runs (
            created_utc, design_key, stage, status,
            de_position_in, xfrmr_spacing_in, coupler_spacing_in,
            xfrmr_length_in, coupler_length_in, de_length_in,
            f_start_mhz, f_stop_mhz, f_step_mhz,
            min_swr, max_swr, avg_swr, points_under_1p5, points_under_2p0,
            avg_r, avg_abs_x,
            center_r, center_x, center_swr, center_rl_db, nec_file
        ) VALUES (?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?, ?,?, ?,?,?,?,?)
    """, (
        now_utc(), design_key, stage, "DONE",
        de_pos, abs(xfrmr_pos - de_pos), abs(coupler_pos - de_pos),
        _len(elements, "XFRMR"), _len(elements, "COUPLER"), _len(elements, "DE"),
        f_low, f_high, f_step,
        round(min_swr, 6), round(max_swr, 6), round(avg_swr, 6),
        under_1p5, under_2p0,
        round(sum(rs) / len(rs), 6), round(sum(xs) / len(xs), 6),
        round(float(metrics.get("center_r", 0.0)), 6),
        round(float(metrics.get("center_x", 0.0)), 6),
        round(float(metrics.get("center_swr", max_swr)), 6),
        round(_rl_from_swr(metrics.get("center_swr", max_swr)), 6),
        f"auto_learn:{stage}",
    ))
    run_id = cur.lastrowid

    cur.executemany(
        "INSERT INTO elements (run_id, name, position_in, length_in) VALUES (?,?,?,?)",
        [(run_id, e["name"], float(e["position_in"]), float(e["length_in"])) for e in elements],
    )
    if curve:
        cur.executemany(
            "INSERT INTO freq_results (run_id, freq_mhz, r_ohm, x_ohm, swr_50) VALUES (?,?,?,?,?)",
            [(run_id, f, r, x, s) for (f, r, x, s) in curve],
        )
    con.commit()
    return run_id


def _rl_from_swr(swr):
    swr = float(swr)
    if swr <= 1.0:
        return 99.0
    g = (swr - 1.0) / (swr + 1.0)
    g = max(g, 1e-12)
    return -20.0 * math.log10(g)


# global filled per-run so save_generation can read the band
cfg_band = (26.965, 27.405)


# ---------------------------------------------------------------------------
# Learning: aggregate per-candidate move logs -> best value per mini-tune
# ---------------------------------------------------------------------------
class MoveMemory:
    """Remembers which sweep value gave the best score for each mini-tune, so
    later generations narrow the search around proven values."""

    def __init__(self):
        self.best_value = {}      # mini_name -> value with best score seen
        self._best_score = {}     # mini_name -> that best score
        self.moves_logged = 0

    def ingest_step_results(self, step_results):
        for sr in step_results or []:
            name = sr.get("step")
            for cand in sr.get("candidates", []) or []:
                v = cand.get("v")
                s = cand.get("score")
                if v is None or s is None:
                    continue
                self.moves_logged += 1
                if name not in self._best_score or s > self._best_score[name]:
                    self._best_score[name] = s
                    self.best_value[name] = float(v)

    def narrow(self, minis, window_in, step_in):
        """Return a copy of mini-tunes with sweep ranges narrowed around the
        best learned value (only for sweep_* types we have learned)."""
        out = []
        for m in minis:
            mm = copy.deepcopy(m)
            name = mm.get("name")
            if name in self.best_value and mm.get("type", "").startswith("sweep_"):
                center = self.best_value[name]
                mm["start_in"] = round(center - window_in, 3)
                mm["stop_in"] = round(center + window_in, 3)
                mm["step_in"] = step_in
                mm["_narrowed"] = True
            out.append(mm)
        return out

    def write_jsonl(self, step_results, gen, stage):
        """Append per-candidate moves to a learn_insights-compatible JSONL."""
        ensure_dirs()
        out_dir = DATA_DIR / "learning_runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"auto_learn_moves_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for sr in step_results or []:
                step = sr.get("step")
                cands = sr.get("candidates", []) or []
                prev = None
                for cand in cands:
                    s = cand.get("score")
                    if s is None:
                        continue
                    rec = {
                        "generation": gen,
                        "stage": stage,
                        "move": f"{step}={cand.get('v')}",
                        "before_score": prev if prev is not None else s,
                        "after_score": s,
                        "score_delta": (s - prev) if prev is not None else 0.0,
                        "after_max_swr": cand.get("max_swr"),
                        "after_gain": cand.get("gain"),
                        "after_fb": cand.get("fb"),
                    }
                    fh.write(json.dumps(rec) + "\n")
                    prev = s
        return path


# ---------------------------------------------------------------------------
# Warm start from the best matching past run in the DB
# ---------------------------------------------------------------------------
def warm_start_geometry(con, cfg, fallback_elements):
    """If the DB holds a good prior run for this project signature, start from
    its geometry instead of the generic one."""
    cur = con.cursor()
    cur.execute("""
        SELECT id FROM runs
        WHERE status='DONE' AND design_key LIKE ?
        ORDER BY max_swr ASC, avg_swr ASC LIMIT 1
    """, (f"{cfg.project_name}|%",))
    row = cur.fetchone()
    if not row:
        return fallback_elements, None
    run_id = row["id"]
    cur.execute("SELECT name, position_in, length_in FROM elements WHERE run_id=? ORDER BY position_in", (run_id,))
    els = [{"name": r["name"], "position_in": r["position_in"], "length_in": r["length_in"]} for r in cur.fetchall()]
    if not els:
        return fallback_elements, None
    return els, run_id


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_learning(elements, rules, minis, procedure, cfg: LearnConfig, log_fn=print):
    global cfg_band

    # Lock the scorer to the requested wideband SWR target.
    _set_swr_profile(cfg.swr_profile)

    # Make the optimizer score across the full band (not just 5 spot freqs) so
    # it can be driven to a wideband low-SWR target.
    v2_runner.EVAL_FREQ_POINTS = max(7, int(cfg.band_sweep_points))

    glb = rules["global"]
    f_low = float(glb["freq_mhz_low"])
    f_high = float(glb["freq_mhz_high"])
    cfg_band = (f_low, f_high)
    f_step = round((f_high - f_low) / max(1, cfg.band_sweep_points - 1), 5)

    con = connect() if cfg.db_path is None else _connect_path(cfg.db_path)

    try:
        # Warm start
        elements, warm_id = warm_start_geometry(con, cfg, elements)
        if warm_id is not None:
            log_fn(f"[warm-start] resuming from DB run #{warm_id} for '{cfg.project_name}'")
        else:
            log_fn(f"[warm-start] no prior history for '{cfg.project_name}', starting fresh")

        memory = MoveMemory()
        current = copy.deepcopy(elements)

        # Baseline
        base = v2_runner.evaluate(current, rules, height_ft=cfg.height_ft)
        if "error" in base:
            raise RuntimeError(f"baseline eval failed: {base['error']}")
        curve, band_max = sweep_band(current, f_low, f_high, cfg.band_sweep_points, cfg.height_ft)
        base_score = v2_scorer.score(**base)
        best_score = base_score
        best_geo = copy.deepcopy(current)
        best_metrics = dict(base, band_max_swr=band_max)
        save_generation(con, cfg, 0, "baseline", current, base, curve, f_step)
        log_fn(f"[gen 0] baseline  score={base_score:+.1f}  band_max_swr={band_max:.3f}  "
               f"gain={base.get('gain_dbi',0):.2f}  fb={base.get('fb_db',0):.2f}")

        if band_max <= cfg.target_max_swr:
            log_fn(f"[done] baseline already meets SWR<= {cfg.target_max_swr} across band.")
            return _result(best_geo, best_metrics, best_score, 0)

        stale = 0
        for gen in range(1, cfg.max_generations + 1):
            # Learning: narrow search around proven values after gen 1.
            active_minis = memory.narrow(minis, cfg.narrow_window_in, cfg.narrow_step_in) if gen > 1 else minis
            active_by_name = {m["name"]: m for m in active_minis}
            n_narrowed = sum(1 for m in active_minis if m.get("_narrowed"))
            log_fn(f"\n[gen {gen}] running '{procedure['name']}'  "
                   f"({n_narrowed} learned/narrowed mini-tunes applied)")

            new_geo, score, metrics, step_results = v2_runner.run_procedure(
                procedure, active_by_name, current, rules, log_fn=None
            )
            if metrics is None:
                log_fn(f"[gen {gen}] procedure produced no result, stopping.")
                break

            # Learn from this generation's candidate logs.
            memory.ingest_step_results(step_results)
            memory.write_jsonl(step_results, gen, procedure["name"])

            curve, band_max = sweep_band(new_geo, f_low, f_high, cfg.band_sweep_points, cfg.height_ft)
            metrics = dict(metrics, band_max_swr=band_max)
            save_generation(con, cfg, gen, procedure["name"], new_geo, metrics, curve, f_step)

            # Adoption rule tuned for a WIDEBAND low-SWR target:
            #   - while we are still above the SWR target, prefer whatever
            #     lowers the band-max SWR (even at a small gain cost);
            #   - once at/under target, keep optimizing the composite score
            #     (gain + F/B) without letting SWR creep back over target.
            best_band = best_metrics.get("band_max_swr", 99.0)
            if best_band > cfg.target_max_swr:
                improved = band_max < best_band - 1e-3
            else:
                improved = (score > best_score + 1e-6) and (band_max <= cfg.target_max_swr + 1e-6)
            log_fn(f"[gen {gen}] score={score:+.1f}  band_max_swr={band_max:.3f}  "
                   f"gain={metrics.get('gain_dbi',0):.2f}  fb={metrics.get('fb_db',0):.2f}  "
                   f"{'IMPROVED' if improved else 'no improvement'}  (learned moves: {memory.moves_logged})")

            if improved:
                best_score = score
                best_geo = copy.deepcopy(new_geo)
                best_metrics = metrics
                current = copy.deepcopy(new_geo)   # auto-adopt
                stale = 0
            else:
                stale += 1

            if band_max <= cfg.target_max_swr:
                log_fn(f"\n[done] reached SWR <= {cfg.target_max_swr} across the band at gen {gen}.")
                return _result(best_geo, best_metrics, best_score, gen)

            if stale >= cfg.patience:
                log_fn(f"\n[done] plateau: no improvement for {cfg.patience} generations (best band_max_swr "
                       f"{best_metrics.get('band_max_swr',0):.3f}).")
                return _result(best_geo, best_metrics, best_score, gen)

        log_fn(f"\n[done] reached max generations ({cfg.max_generations}). "
               f"Best band_max_swr {best_metrics.get('band_max_swr',0):.3f}.")
        return _result(best_geo, best_metrics, best_score, cfg.max_generations)
    finally:
        con.close()


def _result(geo, metrics, score, generations):
    return {
        "final_geometry": geo,
        "final_metrics": metrics,
        "final_score": score,
        "generations": generations,
    }


def _set_swr_profile(profile_key):
    opts_path = DATA_DIR / "run_options_v2.json"
    try:
        opts = json.loads(opts_path.read_text()) if opts_path.exists() else {}
    except Exception:
        opts = {}
    opts["swr_profile"] = profile_key
    opts.setdefault("score_mode", "composite")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    opts_path.write_text(json.dumps(opts, indent=2))


def _connect_path(db_path):
    import sqlite3
    from .db import init_db
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    init_db(con)
    return con
