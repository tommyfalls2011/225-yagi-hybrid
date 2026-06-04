#!/usr/bin/env python3
"""Run the hybrid_auto7 closed-loop self-learning tuner from the command line.

Examples:
    # Learn on the current saved geometry until SWR <= 1.2 across the band
    python3 auto_learn_run.py --procedure cell_tune_3x

    # Use a specific saved project, custom target and generation budget
    python3 auto_learn_run.py --project cb_7el_hybrid_26ft --target-swr 1.2 \
        --max-generations 10 --height-ft 30

Every generation is saved to data/auto7_history.db (runs + elements +
freq_results) and per-candidate moves to data/learning_runs/auto_learn_moves_*.jsonl.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from hyagi.auto_learn import LearnConfig, run_learning  # noqa: E402

DATA = ROOT / "data"


def _load(name):
    return json.loads((DATA / name).read_text())


def _geometry_for(project):
    """Return element list for a project name, or current geometry."""
    if project in (None, "current", "current_geometry"):
        geo = _load("current_geometry_v2.json")
        return geo["elements"], "current_geometry"
    proj_path = DATA / "projects" / f"{project}.json"
    if not proj_path.exists():
        raise SystemExit(f"Project not found: {proj_path}")
    data = json.loads(proj_path.read_text())
    els = data.get("elements") or data.get("geometry", {}).get("elements")
    if not els:
        raise SystemExit(f"No elements found in project {project}")
    return els, project


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="current", help="Project name or 'current'")
    ap.add_argument("--procedure", default="cell_tune_3x", help="Procedure name to run each generation")
    ap.add_argument("--profile", default="good_1.2", help="SWR profile (tight_1.0 / good_1.2 / ok_1.5)")
    ap.add_argument("--target-swr", type=float, default=1.2, help="Stop when band max SWR <= this")
    ap.add_argument("--max-generations", type=int, default=12)
    ap.add_argument("--patience", type=int, default=3, help="Stop after N gens with no improvement")
    ap.add_argument("--height-ft", type=float, default=30.0)
    ap.add_argument("--band-points", type=int, default=21, help="Fine band sweep points")
    ap.add_argument("--band-low", type=float, default=None, help="Override band low MHz (e.g. 26.665 freeband)")
    ap.add_argument("--band-high", type=float, default=None, help="Override band high MHz (e.g. 27.855 freeband)")
    ap.add_argument("--no-matcher", action="store_true", help="Use the legacy greedy procedure instead of the wideband matcher")
    ap.add_argument("--no-polish", action="store_true", help="Skip the gain/F-B recovery phase after hitting the SWR target")
    args = ap.parse_args()

    rules = _load("rules_v2.json")
    if args.band_low is not None:
        rules["global"]["freq_mhz_low"] = args.band_low
    if args.band_high is not None:
        rules["global"]["freq_mhz_high"] = args.band_high
    minis = _load("mini_tunes_v2.json")
    procs = _load("procedures_v2.json")
    procedure = next((p for p in procs if p["name"] == args.procedure), None)
    if procedure is None:
        raise SystemExit(f"Procedure '{args.procedure}' not found. Available: {[p['name'] for p in procs]}")

    elements, proj_name = _geometry_for(args.project)

    cfg = LearnConfig(
        project_name=proj_name,
        height_ft=args.height_ft,
        swr_profile=args.profile,
        target_max_swr=args.target_swr,
        band_sweep_points=args.band_points,
        max_generations=args.max_generations,
        patience=args.patience,
        use_matcher=not args.no_matcher,
        polish_gain=not args.no_polish,
    )

    print(f"Self-learning '{proj_name}' with procedure '{args.procedure}'")
    print(f"Target: SWR <= {args.target_swr} across {rules['global']['freq_mhz_low']}-{rules['global']['freq_mhz_high']} MHz")
    print(f"Profile: {args.profile} | height {args.height_ft} ft | max {args.max_generations} gens")
    print("=" * 72)

    result = run_learning(elements, rules, minis, procedure, cfg)

    print("=" * 72)
    print(f"FINAL after {result['generations']} generation(s):")
    m = result["final_metrics"]
    print(f"  band max SWR : {m.get('band_max_swr', m.get('max_swr', 0)):.3f}")
    print(f"  gain         : {m.get('gain_dbi', 0):.2f} dBi")
    print(f"  front/back   : {m.get('fb_db', 0):.2f} dB")
    print(f"  score        : {result['final_score']:+.1f}")
    print("  geometry:")
    for e in result["final_geometry"]:
        print(f"    {e['name']:<8} pos={float(e['position_in']):7.2f} in  len={float(e['length_in']):7.2f} in")


if __name__ == "__main__":
    main()
