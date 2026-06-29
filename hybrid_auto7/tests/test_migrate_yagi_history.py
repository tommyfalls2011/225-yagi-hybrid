"""End-to-end test for scripts.migrate_yagi_history.

Builds a synthetic legacy `yagi_history.db` containing two pure-Yagi runs
(REF + DE + DIRn) and two hybrid runs (REF + XFRMR + DE + COUPLER + DIRn),
runs the migration, and verifies:

  * only the hybrid rows land in `auto7_history.db`;
  * their elements and freq_results travel with them;
  * the source file is unchanged (the script opens it read-only);
  * a second migrate() call is a no-op (idempotent via UNIQUE design_key).
"""
import sqlite3
import pathlib

import pytest

from scripts.migrate_yagi_history import migrate


LEGACY_SCHEMA = """
CREATE TABLE runs (
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
    nec_file TEXT
);
CREATE TABLE elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position_in REAL NOT NULL,
    length_in REAL NOT NULL
);
CREATE TABLE freq_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    freq_mhz REAL NOT NULL,
    r_ohm REAL NOT NULL,
    x_ohm REAL NOT NULL,
    swr_50 REAL NOT NULL
);
"""


def _make_legacy(path: pathlib.Path):
    con = sqlite3.connect(str(path))
    con.executescript(LEGACY_SCHEMA)
    # Two pure Yagis (no XFRMR/COUPLER) -- must be SKIPPED.
    for k in (1, 2):
        con.execute(
            "INSERT INTO runs (created_utc, design_key, stage, status, "
            "de_position_in, xfrmr_spacing_in, coupler_spacing_in, "
            "xfrmr_length_in, coupler_length_in, de_length_in, "
            "f_start_mhz, f_stop_mhz, f_step_mhz) "
            "VALUES (?, ?, ?, 'DONE', ?, 0, 0, 0, 0, ?, ?, ?, ?)",
            (f"2025-01-{k:02d}", f"yagi-{k}", "tune",
             40.0 + k, 215.0, 26.9, 27.4, 0.05),
        )
        rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for nm, pos, L in [("REF", 0, 220), ("DE", 40 + k, 215),
                           ("DIR1", 100, 200)]:
            con.execute("INSERT INTO elements (run_id, name, position_in, length_in) "
                        "VALUES (?, ?, ?, ?)", (rid, nm, pos, L))
        con.execute("INSERT INTO freq_results (run_id, freq_mhz, r_ohm, x_ohm, swr_50) "
                    "VALUES (?, ?, ?, ?, ?)", (rid, 27.1, 48.0, -2.0, 1.05))
    # Two hybrids -- must be MIGRATED.
    for k in (1, 2):
        con.execute(
            "INSERT INTO runs (created_utc, design_key, stage, status, "
            "de_position_in, xfrmr_spacing_in, coupler_spacing_in, "
            "xfrmr_length_in, coupler_length_in, de_length_in, "
            "f_start_mhz, f_stop_mhz, f_step_mhz, max_swr, min_swr) "
            "VALUES (?, ?, ?, 'DONE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"2025-02-{k:02d}", f"hyb-{k}", "tune",
             46.9, 18.5, 20.0, 199.3, 199.9, 215.7,
             26.9, 27.4, 0.05, 1.25, 1.05),
        )
        rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        for nm, pos, L in [
            ("REF", 0, 218.5), ("XFRMR", 28.4, 199.3), ("DE", 46.9, 215.7),
            ("COUPLER", 66.9, 199.9), ("DIR1", 135.9, 195.0),
        ]:
            con.execute("INSERT INTO elements (run_id, name, position_in, length_in) "
                        "VALUES (?, ?, ?, ?)", (rid, nm, pos, L))
        for f, r, x, s in [(26.9, 55, -8, 1.20), (27.1, 50, 0, 1.0),
                           (27.4, 45, 8, 1.22)]:
            con.execute("INSERT INTO freq_results (run_id, freq_mhz, r_ohm, x_ohm, swr_50) "
                        "VALUES (?, ?, ?, ?, ?)", (rid, f, r, x, s))
    con.commit()
    con.close()


def test_migrate_only_hybrid_runs(tmp_path):
    src = tmp_path / "yagi_history.db"
    dst = tmp_path / "auto7_history.db"
    _make_legacy(src)
    src_mtime_before = src.stat().st_mtime

    stats = migrate(src, dst)
    assert stats == {
        "inserted": 2,
        "skipped_dup": 0,
        "skipped_no_xfrmr": 2,
        "skipped_missing": 0,
    }

    # Source unchanged (read-only open).
    assert src.stat().st_mtime == src_mtime_before

    # Dest contains exactly the 2 hybrid runs.
    con = sqlite3.connect(str(dst))
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM runs").fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r["design_key"].startswith("legacy_yagi:")
        assert r["stage"].startswith("legacy_import_from_yagi_history:")
        # Elements present
        els = con.execute(
            "SELECT name FROM elements WHERE run_id=?", (r["id"],)
        ).fetchall()
        names = {e["name"].upper() for e in els}
        assert {"REF", "XFRMR", "DE", "COUPLER"} <= names
        # Freq sweep travelled too
        n = con.execute("SELECT COUNT(*) FROM freq_results WHERE run_id=?",
                        (r["id"],)).fetchone()[0]
        assert n == 3
    con.close()


def test_migrate_is_idempotent(tmp_path):
    src = tmp_path / "yagi_history.db"
    dst = tmp_path / "auto7_history.db"
    _make_legacy(src)
    first = migrate(src, dst)
    second = migrate(src, dst)
    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["skipped_dup"] == 2
    con = sqlite3.connect(str(dst))
    assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM elements").fetchone()[0] == 10
    con.close()


def test_dry_run_writes_nothing(tmp_path):
    src = tmp_path / "yagi_history.db"
    dst = tmp_path / "auto7_history.db"
    _make_legacy(src)
    stats = migrate(src, dst, dry_run=True)
    assert stats["inserted"] == 2          # reports what WOULD be inserted
    assert not dst.exists()                # nothing actually written


def test_handles_missing_freq_results_table(tmp_path):
    """If the legacy DB has no freq_results table (very old vintage), the
    migration must still copy runs + elements without crashing."""
    src = tmp_path / "yagi_history.db"
    dst = tmp_path / "auto7_history.db"
    con = sqlite3.connect(str(src))
    con.executescript(LEGACY_SCHEMA)
    con.execute("DROP TABLE freq_results")
    con.execute(
        "INSERT INTO runs (created_utc, design_key, stage, status, "
        "de_position_in, xfrmr_spacing_in, coupler_spacing_in, "
        "xfrmr_length_in, coupler_length_in, de_length_in, "
        "f_start_mhz, f_stop_mhz, f_step_mhz) "
        "VALUES ('2025-02-01', 'hyb-1', 'tune', 'DONE', "
        "46.9, 18.5, 20.0, 199.3, 199.9, 215.7, 26.9, 27.4, 0.05)"
    )
    rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    for nm, pos, L in [("REF", 0, 218.5), ("XFRMR", 28.4, 199.3),
                       ("DE", 46.9, 215.7), ("COUPLER", 66.9, 199.9)]:
        con.execute("INSERT INTO elements (run_id, name, position_in, length_in) "
                    "VALUES (?, ?, ?, ?)", (rid, nm, pos, L))
    con.commit(); con.close()

    stats = migrate(src, dst)
    assert stats["inserted"] == 1
