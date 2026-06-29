"""SQLite-backed run history for the Yagi optimizer."""
import json, sqlite3, datetime
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "yagi_opt_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    center_freq_mhz REAL NOT NULL,
    seed            INTEGER,
    prio_gain       INTEGER, prio_swr INTEGER,
    prio_rl         INTEGER, prio_bw  INTEGER, prio_fb INTEGER,
    final_swr       REAL, final_rl_db     REAL,
    final_local_rl  REAL, final_bw_mhz    REAL,
    final_gain_db   REAL, final_fb_db     REAL,
    final_score     REAL,
    geometry_json   TEXT, stages_json     TEXT,
    winner_stage    TEXT, winner_note     TEXT,
    tag             TEXT, nec_file_path   TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts   ON runs(timestamp);
CREATE INDEX IF NOT EXISTS idx_runs_freq ON runs(center_freq_mhz);
CREATE INDEX IF NOT EXISTS idx_runs_fb   ON runs(final_fb_db);
CREATE INDEX IF NOT EXISTS idx_runs_score ON runs(final_score);
"""

def _connect(db_path=None):
    p = Path(db_path) if db_path else _DEFAULT_DB
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn

def save_run(center_freq, seed, priorities, final_metrics,
             geometry, stages, winner_stage, winner_note,
             tag=None, nec_file_path=None, db_path=None):
    conn = _connect(db_path)
    try:
        cur = conn.execute("""
            INSERT INTO runs (
                timestamp, center_freq_mhz, seed,
                prio_gain, prio_swr, prio_rl, prio_bw, prio_fb,
                final_swr, final_rl_db, final_local_rl, final_bw_mhz,
                final_gain_db, final_fb_db, final_score,
                geometry_json, stages_json,
                winner_stage, winner_note, tag, nec_file_path
            ) VALUES (?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?, ?,?,?,?)
        """, (
            datetime.datetime.now().isoformat(timespec="seconds"),
            float(center_freq), int(seed) if seed is not None else None,
            priorities.get("gain"), priorities.get("swr"),
            priorities.get("return_loss"), priorities.get("bandwidth"),
            priorities.get("front_to_back"),
            final_metrics.get("swr"), final_metrics.get("rl_db"),
            final_metrics.get("local_rl"), final_metrics.get("bw_mhz"),
            final_metrics.get("gain_db"), final_metrics.get("fb_db"),
            final_metrics.get("score"),
            json.dumps(geometry, default=float),
            json.dumps(stages, default=float),
            winner_stage, winner_note, tag, nec_file_path,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def list_recent(n=10, db_path=None):
    with _connect(db_path) as c:
        return [dict(r) for r in c.execute(
            "SELECT id,timestamp,center_freq_mhz,final_swr,final_rl_db,"
            "final_bw_mhz,final_gain_db,final_fb_db,final_score,tag,winner_stage "
            "FROM runs ORDER BY id DESC LIMIT ?", (int(n),)
        ).fetchall()]

def get_run(run_id, db_path=None):
    with _connect(db_path) as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (int(run_id),)).fetchone()
        return dict(r) if r else None

def best_by(metric, n=5, db_path=None, where_freq=None):
    valid = {"fb_db":"final_fb_db", "rl_db":"final_rl_db",
             "bw_mhz":"final_bw_mhz", "gain_db":"final_gain_db",
             "score":"final_score", "swr_low":"final_swr"}
    if metric not in valid:
        raise ValueError("bad metric")
    col = valid[metric]
    order = "ASC" if metric == "swr_low" else "DESC"
    q = "SELECT id,timestamp,center_freq_mhz,final_swr,final_rl_db,final_bw_mhz,final_gain_db,final_fb_db,final_score,tag,winner_stage FROM runs"
    params = []
    if where_freq is not None:
        q += " WHERE ABS(center_freq_mhz - ?) < 0.05"
        params.append(float(where_freq))
    q += " ORDER BY " + col + " " + order + " LIMIT ?"
    params.append(int(n))
    with _connect(db_path) as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]

def stats(db_path=None):
    with _connect(db_path) as c:
        row = c.execute("""
            SELECT COUNT(*) AS n,
                   AVG(final_swr) AS avg_swr, AVG(final_rl_db) AS avg_rl,
                   AVG(final_bw_mhz) AS avg_bw, AVG(final_gain_db) AS avg_gain,
                   AVG(final_fb_db) AS avg_fb, AVG(final_score) AS avg_score,
                   MAX(final_fb_db) AS max_fb, MIN(final_swr) AS min_swr,
                   MAX(final_bw_mhz) AS max_bw
            FROM runs
        """).fetchone()
        return dict(row) if row else {}

def prune(keep=100, db_path=None):
    with _connect(db_path) as c:
        c.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)", (int(keep),))
        c.commit()
        return c.total_changes
