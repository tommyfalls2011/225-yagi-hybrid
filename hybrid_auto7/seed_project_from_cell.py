#!/usr/bin/env python3
"""
seed_project_from_cell.py

Takes best_cell_seed.json and writes XFRMR/DE/COUPLER position+length into
an existing project's element_overrides, then flips tuning_procedure to
cell_then_directors_repeat so `run.py design <project>` uses your tuned cell
as the locked starting point.

Usage:
    python3 seed_project_from_cell.py --project cb_7el_hybrid_36ft
    python3 seed_project_from_cell.py --project cb_7el_hybrid_36ft --cell-seed <path.json>
    python3 seed_project_from_cell.py --project cb_7el_hybrid_36ft --procedure legacy_hybrid
"""
import argparse
import json
from pathlib import Path

from hyagi.project import (
    load_project, save_project, set_element_override,
    print_project,
)
from hyagi.paths import DATA_DIR


DEFAULT_SEED = DATA_DIR / "cell_learning_runs" / "best_cell_seed.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True,
                    help="Project name (e.g. cb_7el_hybrid_36ft)")
    ap.add_argument("--cell-seed", default=str(DEFAULT_SEED),
                    help=f"Path to cell seed JSON (default: {DEFAULT_SEED})")
    ap.add_argument("--procedure", default="cell_then_directors_repeat",
                    choices=("legacy_hybrid", "wide_cell_owa", "cell_then_directors_repeat"))
    ap.add_argument("--no-procedure-change", action="store_true",
                    help="Do not touch tuning_procedure on the project")
    args = ap.parse_args()

    seed_path = Path(args.cell_seed).expanduser().resolve()
    if not seed_path.exists():
        raise SystemExit(f"Cell seed not found: {seed_path}")

    data = json.loads(seed_path.read_text())
    for k in ("xfrmr_position_in", "de_position_in", "coupler_position_in",
              "xfrmr_length_in", "de_length_in", "coupler_length_in"):
        if k not in data:
            raise SystemExit(f"Cell seed missing key: {k}")

    print(f"Seed source: {seed_path}")
    print(f"Project:     {args.project}")
    print()

    # Verify project loads cleanly first
    cfg = load_project(args.project)
    print(f"Loaded project '{cfg.name}' "
          f"(elements={cfg.element_count}, mode={cfg.mode}, "
          f"procedure={cfg.tuning_procedure})")

    # Write overrides using the official helper (persists to disk each call)
    set_element_override(args.project, "XFRMR",
                         position_in=float(data["xfrmr_position_in"]),
                         length_in=float(data["xfrmr_length_in"]))
    set_element_override(args.project, "DE",
                         position_in=float(data["de_position_in"]),
                         length_in=float(data["de_length_in"]))
    set_element_override(args.project, "COUPLER",
                         position_in=float(data["coupler_position_in"]),
                         length_in=float(data["coupler_length_in"]))
    print("[ok] wrote XFRMR/DE/COUPLER overrides")

    # Optionally switch procedure
    if not args.no_procedure_change:
        cfg = load_project(args.project)
        if cfg.tuning_procedure != args.procedure:
            cfg.tuning_procedure = args.procedure
            save_project(cfg)
            print(f"[ok] tuning_procedure -> {args.procedure}")
        else:
            print(f"[skip] tuning_procedure already {args.procedure}")

    # Show final state
    print()
    print("=" * 60)
    cfg = load_project(args.project)
    print_project(cfg)
    print()
    print("Next step:")
    print(f"  python3 run.py design {args.project} --level normal")


if __name__ == "__main__":
    main()
