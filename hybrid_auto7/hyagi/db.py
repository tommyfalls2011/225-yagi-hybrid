import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .paths import DB_PATH, ensure_dirs


def now_utc():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect():
    ensure_dirs()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")

    init_db(con)
    return con


@contextmanager
def get_connection():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _add_column_if_missing(cur, table, column_def):
    col_name = column_def.split()[0]
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if col_name not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def init_db(con):
    cur = con.cursor()

    cur.execute("""
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

            nec_file TEXT
        )
    """)

    # Add learning-oriented columns if missing
    for coldef in [
        "center_r REAL",
        "center_x REAL",
        "center_swr REAL",
        "center_rl_db REAL",
        "bw_1p5_mhz REAL",
        "bw_2p0_mhz REAL",
        "low_edge_1p5_mhz REAL",
        "high_edge_1p5_mhz REAL",
        "low_edge_2p0_mhz REAL",
        "high_edge_2p0_mhz REAL",
    ]:
        _add_column_if_missing(cur, "runs", coldef)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS elements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            position_in REAL NOT NULL,
            length_in REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS freq_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            freq_mhz REAL NOT NULL,
            r_ohm REAL NOT NULL,
            x_ohm REAL NOT NULL,
            swr_50 REAL NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS move_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_utc TEXT NOT NULL,
            project_name TEXT,
            stage TEXT,
            move_name TEXT,
            score_delta REAL,

            before_max_swr REAL,
            after_max_swr REAL,
            before_avg_swr REAL,
            after_avg_swr REAL,
            before_center_r REAL,
            after_center_r REAL,
            before_center_x REAL,
            after_center_x REAL,

            before_center_rl_db REAL,
            after_center_rl_db REAL,
            before_bw_1p5_mhz REAL,
            after_bw_1p5_mhz REAL,
            before_bw_2p0_mhz REAL,
            after_bw_2p0_mhz REAL
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_stage ON runs(stage)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_elements_run_id ON elements(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_freq_results_run_id ON freq_results(run_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_freq_results_run_freq ON freq_results(run_id, freq_mhz)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_move_history_project ON move_history(project_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_move_history_stage ON move_history(stage)")


def existing_run(design_key):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM runs WHERE design_key = ?", (design_key,))
        return cur.fetchone()


def run_by_id(run_id):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        return cur.fetchone()


def insert_run(
    design_key,
    stage,
    design,
    f_start,
    f_stop,
    f_step,
    summary,
    elements,
    results,
    nec_file,
):
    with get_connection() as con:
        cur = con.cursor()

        cur.execute("""
            INSERT INTO runs (
                created_utc,
                design_key,
                stage,
                status,

                de_position_in,
                xfrmr_spacing_in,
                coupler_spacing_in,
                xfrmr_length_in,
                coupler_length_in,
                de_length_in,

                f_start_mhz,
                f_stop_mhz,
                f_step_mhz,

                min_swr,
                max_swr,
                avg_swr,
                points_under_1p5,
                points_under_2p0,
                avg_r,
                avg_abs_x,

                center_r,
                center_x,
                center_swr,
                center_rl_db,
                bw_1p5_mhz,
                bw_2p0_mhz,
                low_edge_1p5_mhz,
                high_edge_1p5_mhz,
                low_edge_2p0_mhz,
                high_edge_2p0_mhz,

                nec_file
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_utc(),
            design_key,
            stage,
            "DONE",

            design.de_position_in,
            design.xfrmr_spacing_in,
            design.coupler_spacing_in,
            design.xfrmr_length_in,
            design.coupler_length_in,
            design.de_length_in,

            f_start,
            f_stop,
            f_step,

            summary.min_swr,
            summary.max_swr,
            summary.avg_swr,
            summary.points_under_1p5,
            summary.points_under_2p0,
            summary.avg_r,
            summary.avg_abs_x,

            summary.center_r,
            summary.center_x,
            summary.center_swr,
            summary.center_rl_db,
            summary.bw_1p5_mhz,
            summary.bw_2p0_mhz,
            summary.low_edge_1p5_mhz,
            summary.high_edge_1p5_mhz,
            summary.low_edge_2p0_mhz,
            summary.high_edge_2p0_mhz,

            str(nec_file),
        ))

        run_id = cur.lastrowid

        cur.executemany("""
            INSERT INTO elements (run_id, name, position_in, length_in)
            VALUES (?, ?, ?, ?)
        """, [
            (run_id, e.name, e.position_in, e.length_in)
            for e in elements
        ])

        cur.executemany("""
            INSERT INTO freq_results (run_id, freq_mhz, r_ohm, x_ohm, swr_50)
            VALUES (?, ?, ?, ?, ?)
        """, [
            (run_id, r.freq_mhz, r.r_ohm, r.x_ohm, r.swr_50)
            for r in results
        ])

        return run_id


def insert_move_history(
    project_name,
    stage,
    move_name,
    before_summary,
    after_summary,
    score_delta,
):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO move_history (
                created_utc,
                project_name,
                stage,
                move_name,
                score_delta,

                before_max_swr,
                after_max_swr,
                before_avg_swr,
                after_avg_swr,
                before_center_r,
                after_center_r,
                before_center_x,
                after_center_x,

                before_center_rl_db,
                after_center_rl_db,
                before_bw_1p5_mhz,
                after_bw_1p5_mhz,
                before_bw_2p0_mhz,
                after_bw_2p0_mhz
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_utc(),
            project_name,
            stage,
            move_name,
            score_delta,

            before_summary.center_swr if hasattr(before_summary, "max_swr") else None,
            after_summary.max_swr if hasattr(after_summary, "max_swr") else None,
            before_summary.avg_swr if hasattr(before_summary, "avg_swr") else None,
            after_summary.avg_swr if hasattr(after_summary, "avg_swr") else None,
            before_summary.center_r if hasattr(before_summary, "center_r") else None,
            after_summary.center_r if hasattr(after_summary, "center_r") else None,
            before_summary.center_x if hasattr(before_summary, "center_x") else None,
            after_summary.center_x if hasattr(after_summary, "center_x") else None,

            before_summary.center_rl_db if hasattr(before_summary, "center_rl_db") else None,
            after_summary.center_rl_db if hasattr(after_summary, "center_rl_db") else None,
            before_summary.bw_1p5_mhz if hasattr(before_summary, "bw_1p5_mhz") else None,
            after_summary.bw_1p5_mhz if hasattr(after_summary, "bw_1p5_mhz") else None,
            before_summary.bw_2p0_mhz if hasattr(before_summary, "bw_2p0_mhz") else None,
            after_summary.bw_2p0_mhz if hasattr(after_summary, "bw_2p0_mhz") else None,
        ))


def best_run():
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT *
            FROM runs
            WHERE status='DONE'
            ORDER BY
                points_under_2p0 DESC,
                points_under_1p5 DESC,
                max_swr ASC,
                avg_swr ASC,
                avg_abs_x ASC
            LIMIT 1
        """)
        return cur.fetchone()


def best_rows(limit=20):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT *
            FROM runs
            WHERE status='DONE'
            ORDER BY
                points_under_2p0 DESC,
                points_under_1p5 DESC,
                max_swr ASC,
                avg_swr ASC,
                avg_abs_x ASC
            LIMIT ?
        """, (limit,))
        return cur.fetchall()


def elements_for_run(run_id):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT *
            FROM elements
            WHERE run_id = ?
            ORDER BY position_in ASC
        """, (run_id,))
        return cur.fetchall()


def freqs_for_run(run_id):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT *
            FROM freq_results
            WHERE run_id = ?
            ORDER BY freq_mhz ASC
        """, (run_id,))
        return cur.fetchall()
