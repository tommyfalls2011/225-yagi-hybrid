"""Migrate hybrid runs out of legacy `yagi_history.db` into `auto7_history.db`.

The legacy `opt_7el_yagi2.py` optimizer wrote ALL its runs (pure Yagi AND
hybrid) to a database called `yagi_history.db`.  hybrid_auto7 now uses
`auto7_history.db`.  Hundreds of hybrid runs are stranded in the legacy DB
where the new self-learning loop cannot see them.

This script:

  * Opens the legacy DB READ-ONLY (Yagi Designer keeps using it unchanged).
  * Picks only runs whose element table includes XFRMR + COUPLER -- i.e.
    real hybrids, not pure Yagis.
  * Re-keys each row with a hybrid-style design_key so it doesn't collide
    with hybrid_auto7 native runs.
  * Inserts runs, elements and freq_results into auto7_history.db using
    the canonical schema (`hyagi.db.init_db`).
  * Is fully IDEMPOTENT: a UNIQUE design_key prevents duplicates, so it's
    safe to re-run after every Yagi-Designer session to keep the hybrid
    learning DB in sync.

Usage:
    python -m scripts.migrate_yagi_history \\
        --source ~/scripts/yagi_history.db \\
        [--dest /path/to/auto7_history.db]   # default: hybrid_auto7/data/...
        [--dry-run]                          # report only, no writes
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from typing import Iterable


# ----- canonical hybrid schema, mirrored from hyagi.db.init_db ---------------
CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    design_key TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,

    de_position_in REAL NOT NULL,
    xfrmr_spacing_in REAL NOT NULL,
    coupler_spacing_in REAL NOT NULL,
    xfrmr_length_in REAL NOT NULL,
    coupler_length_in REAL NOT NULL,
    de_length_in REAL NOT NULL,

    f_start_mhz REAL NOT NULL,
    f_stop_mhz REAL NOT NULL,
    f_step_mhz REAL NOT NULL,

    min_swr REAL,
    max_swr REAL,
    avg_swr REAL,
    points_under_1p5 INTEGER,
    points_under_2p0 INTEGER,
    avg_r REAL,
    avg_abs_x REAL,

    center_r REAL, center_x REAL, center_swr REAL, center_rl_db REAL,
    bw_1p5_mhz REAL, bw_2p0_mhz REAL,
    low_edge_1p5_mhz REAL, high_edge_1p5_mhz REAL,
    low_edge_2p0_mhz REAL, high_edge_2p0_mhz REAL,

    nec_file TEXT
)
"""

CREATE_ELEMENTS = """
CREATE TABLE IF NOT EXISTS elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position_in REAL NOT NULL,
    length_in REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
)
"""

CREATE_FREQS = """
CREATE TABLE IF NOT EXISTS freq_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    freq_mhz REAL NOT NULL,
    r_ohm REAL NOT NULL,
    x_ohm REAL NOT NULL,
    swr_50 REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
)
"""

RUN_COLS = [
    "created_utc", "design_key", "stage", "status",
    "de_position_in", "xfrmr_spacing_in", "coupler_spacing_in",
    "xfrmr_length_in", "coupler_length_in", "de_length_in",
    "f_start_mhz", "f_stop_mhz", "f_step_mhz",
    "min_swr", "max_swr", "avg_swr",
    "points_under_1p5", "points_under_2p0",
    "avg_r", "avg_abs_x",
    "center_r", "center_x", "center_swr", "center_rl_db",
    "bw_1p5_mhz", "bw_2p0_mhz",
    "low_edge_1p5_mhz", "high_edge_1p5_mhz",
    "low_edge_2p0_mhz", "high_edge_2p0_mhz",
    "nec_file",
]


def _open_readonly(path: pathlib.Path) -> sqlite3.Connection:
    """SQLite URI 'mode=ro' guarantees the source file is never written to.
    Falls back to a regular open if the URI path can't be resolved (very old
    sqlite builds), in which case we manually set query_only afterwards."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        con = sqlite3.connect(str(path))
        con.execute("PRAGMA query_only = ON")
    con.row_factory = sqlite3.Row
    return con


def _table_cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _ensure_dest_schema(dest: sqlite3.Connection) -> None:
    cur = dest.cursor()
    cur.execute(CREATE_RUNS)
    cur.execute(CREATE_ELEMENTS)
    cur.execute(CREATE_FREQS)
    # Make sure the optional / newer columns exist on the runs table.
    have = set(_table_cols(dest, "runs"))
    for col in [
        "center_r REAL", "center_x REAL", "center_swr REAL", "center_rl_db REAL",
        "bw_1p5_mhz REAL", "bw_2p0_mhz REAL",
        "low_edge_1p5_mhz REAL", "high_edge_1p5_mhz REAL",
        "low_edge_2p0_mhz REAL", "high_edge_2p0_mhz REAL",
    ]:
        name = col.split()[0]
        if name not in have:
            cur.execute(f"ALTER TABLE runs ADD COLUMN {col}")
    dest.commit()


def _hybrid_run_ids(src: sqlite3.Connection) -> set[int]:
    """Return run_ids whose elements include both XFRMR and COUPLER (case-
    insensitive).  Pure-Yagi runs (REF / DE / DIRn only) get filtered out."""
    rows = src.execute(
        "SELECT DISTINCT run_id, UPPER(name) AS nm FROM elements"
    ).fetchall()
    by_run: dict[int, set[str]] = {}
    for r in rows:
        by_run.setdefault(r["run_id"], set()).add(r["nm"])
    return {rid for rid, names in by_run.items()
            if "XFRMR" in names and "COUPLER" in names}


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _coerce_run_values(run: dict, available: Iterable[str]) -> tuple[list, str]:
    """Build the INSERT parameter list in RUN_COLS order, filling missing
    columns with None, and re-tag the design_key + stage so it's obvious
    these came from the legacy DB.  Returns (values, new_design_key)."""
    available = set(available)
    out = []
    for col in RUN_COLS:
        if col == "design_key":
            base = run.get("design_key") or f"legacy-{run.get('id', '?')}"
            out.append(f"legacy_yagi:{base}")
            continue
        if col == "stage":
            out.append(f"legacy_import_from_yagi_history:{run.get('stage', '')}")
            continue
        if col == "status":
            out.append(run.get("status") or "DONE")
            continue
        if col in available:
            out.append(run.get(col))
        else:
            out.append(None)
    new_design_key = out[RUN_COLS.index("design_key")]
    return out, new_design_key


def migrate(source: pathlib.Path, dest: pathlib.Path, dry_run: bool = False):
    if not source.exists():
        raise FileNotFoundError(f"source DB not found: {source}")
    src = _open_readonly(source)
    src_tables = {r["name"] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "runs" not in src_tables or "elements" not in src_tables:
        raise SystemExit(f"source {source} doesn't look like a yagi history DB "
                         f"(missing 'runs' or 'elements' table)")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        dst = sqlite3.connect(":memory:")
    else:
        dst = sqlite3.connect(str(dest))
    dst.row_factory = sqlite3.Row
    _ensure_dest_schema(dst)

    src_run_cols = _table_cols(src, "runs")
    hybrid_ids = _hybrid_run_ids(src)
    print(f"source: {source}")
    print(f"dest  : {dest}{'   (DRY RUN)' if dry_run else ''}")
    print(f"  total source runs               : "
          f"{src.execute('SELECT COUNT(*) FROM runs').fetchone()[0]}")
    print(f"  identified as HYBRID (XFRMR+COUPLER): {len(hybrid_ids)}")

    inserted = 0
    skipped_dup = 0
    skipped_no_xfrmr = 0
    skipped_missing = 0
    has_freqs = "freq_results" in src_tables

    for run_row in src.execute("SELECT * FROM runs").fetchall():
        rid = run_row["id"]
        if rid not in hybrid_ids:
            skipped_no_xfrmr += 1
            continue
        run = _row_to_dict(run_row)
        values, new_key = _coerce_run_values(run, src_run_cols)
        # Mandatory NOT NULL columns we may be missing in old rows.
        required_idx = [RUN_COLS.index(c) for c in (
            "de_position_in", "xfrmr_spacing_in", "coupler_spacing_in",
            "xfrmr_length_in", "coupler_length_in", "de_length_in",
            "f_start_mhz", "f_stop_mhz", "f_step_mhz",
            "created_utc")]
        if any(values[i] is None for i in required_idx):
            skipped_missing += 1
            continue
        ph = ", ".join("?" * len(RUN_COLS))
        cols_sql = ", ".join(RUN_COLS)
        try:
            cur = dst.execute(
                f"INSERT INTO runs ({cols_sql}) VALUES ({ph})", values)
        except sqlite3.IntegrityError:
            skipped_dup += 1
            continue
        new_run_id = cur.lastrowid

        # Elements
        for er in src.execute(
            "SELECT name, position_in, length_in FROM elements WHERE run_id=?",
            (rid,),
        ):
            dst.execute(
                "INSERT INTO elements (run_id, name, position_in, length_in)"
                " VALUES (?, ?, ?, ?)",
                (new_run_id, er["name"], er["position_in"], er["length_in"]),
            )

        # Per-frequency sweep, if the legacy DB has it
        if has_freqs:
            for fr in src.execute(
                "SELECT freq_mhz, r_ohm, x_ohm, swr_50 "
                "FROM freq_results WHERE run_id=?", (rid,),
            ):
                dst.execute(
                    "INSERT INTO freq_results "
                    "(run_id, freq_mhz, r_ohm, x_ohm, swr_50) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (new_run_id, fr["freq_mhz"], fr["r_ohm"],
                     fr["x_ohm"], fr["swr_50"]),
                )
        inserted += 1

    if not dry_run:
        dst.commit()
    dst.close()
    src.close()

    print()
    print(f"  inserted into auto7_history.db  : {inserted}")
    print(f"  skipped (already present)       : {skipped_dup}")
    print(f"  skipped (not hybrid)            : {skipped_no_xfrmr}")
    print(f"  skipped (incomplete row)        : {skipped_missing}")
    print()
    if dry_run:
        print("DRY RUN -- nothing was written.  Re-run without --dry-run to apply.")
    else:
        print("Done.  Source file was NOT modified (opened read-only).")
    return {
        "inserted": inserted,
        "skipped_dup": skipped_dup,
        "skipped_no_xfrmr": skipped_no_xfrmr,
        "skipped_missing": skipped_missing,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    here = pathlib.Path(__file__).resolve()
    repo_root = here.parent.parent          # hybrid_auto7/
    default_source = pathlib.Path.home() / "scripts/yagi_history.db"
    default_dest = repo_root / "data" / "auto7_history.db"
    p.add_argument("--source", default=str(default_source),
                   help=f"path to legacy yagi_history.db (default: {default_source})")
    p.add_argument("--dest", default=str(default_dest),
                   help=f"path to hybrid_auto7 db (default: {default_dest})")
    p.add_argument("--dry-run", action="store_true",
                   help="report only, do not write")
    args = p.parse_args(argv)
    return migrate(pathlib.Path(args.source).expanduser(),
                   pathlib.Path(args.dest).expanduser(),
                   dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(0 if main() else 0)
