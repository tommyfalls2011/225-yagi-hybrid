"""Warm-start design-signature filter + SWR sanity guard.

User bug report: a CB-tuned geometry (band 26.7-27.7 MHz) was being warm-
started when the user later tuned over 24-30 MHz OWA, producing a baseline
band-max SWR of 927.935 -- physically impossible if the warm-start were a
sane match for the band.

Two filters now guard against that:

  1. design signature: warm_start_geometry() prefers DB runs whose
     design_key contains the CURRENT design's
     (taper|band|height|n_elements) signature.  Falls back to project-
     name-only if no signature hit exists (so an empty / freshly migrated
     DB still warm-starts).

  2. SWR sanity: even when the signature matches, the candidate is probed
     with band_swr_curve() over the user's CURRENT band; if the band-max
     comes back > 5:1 (catastrophic mistune) the candidate is rejected and
     the next one is tried.  No warm-start is worse than the current
     fallback geometry.
"""
import copy
import sqlite3
import types
from unittest.mock import patch

from hyagi import auto_learn, v2_runner


HYBRID = [
    {"name": "REF",     "position_in": 0.0,    "length_in": 218.5},
    {"name": "XFRMR",   "position_in": 28.4,   "length_in": 199.3},
    {"name": "DE",      "position_in": 46.9,   "length_in": 215.7},
    {"name": "COUPLER", "position_in": 66.9,   "length_in": 199.9},
    {"name": "DIR1",    "position_in": 135.9,  "length_in": 195.0},
    {"name": "DIR2",    "position_in": 214.1,  "length_in": 191.1},
    {"name": "DIR3",    "position_in": 292.3,  "length_in": 187.2},
]
GOOD_GEOM = [{"name": n, "position_in": p, "length_in": L}
             for n, p, L in [("REF", 0, 220), ("XFRMR", 28, 200), ("DE", 47, 217),
                             ("COUPLER", 67, 200), ("DIR1", 136, 195),
                             ("DIR2", 214, 191), ("DIR3", 292, 187)]]
BAD_GEOM = [{"name": n, "position_in": p, "length_in": L}
            for n, p, L in [("REF", 0, 100), ("XFRMR", 28, 90), ("DE", 47, 95),
                            ("COUPLER", 67, 88), ("DIR1", 136, 85),
                            ("DIR2", 214, 82), ("DIR3", 292, 80)]]


SCHEMA = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL,
    design_key TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    de_position_in REAL NOT NULL, xfrmr_spacing_in REAL NOT NULL,
    coupler_spacing_in REAL NOT NULL, xfrmr_length_in REAL NOT NULL,
    coupler_length_in REAL NOT NULL, de_length_in REAL NOT NULL,
    f_start_mhz REAL NOT NULL, f_stop_mhz REAL NOT NULL, f_step_mhz REAL NOT NULL,
    min_swr REAL, max_swr REAL, avg_swr REAL,
    points_under_1p5 INTEGER, points_under_2p0 INTEGER,
    avg_r REAL, avg_abs_x REAL
);
CREATE TABLE elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position_in REAL NOT NULL,
    length_in REAL NOT NULL
);
"""


def _insert(con, run_id, design_key, max_swr, geo):
    con.execute(
        "INSERT INTO runs (id, created_utc, design_key, stage, status, "
        "de_position_in, xfrmr_spacing_in, coupler_spacing_in, "
        "xfrmr_length_in, coupler_length_in, de_length_in, "
        "f_start_mhz, f_stop_mhz, f_step_mhz, max_swr, avg_swr) "
        "VALUES (?, '2025-01-01', ?, 'baseline', 'DONE', "
        "46.9, 18.5, 20.0, 199.3, 199.9, 215.7, 26.7, 27.7, 0.05, ?, ?)",
        (run_id, design_key, max_swr, max_swr * 0.95),
    )
    for e in geo:
        con.execute("INSERT INTO elements (run_id, name, position_in, length_in) "
                    "VALUES (?, ?, ?, ?)",
                    (run_id, e["name"], e["position_in"], e["length_in"]))


def _build_cfg():
    cfg = types.SimpleNamespace(
        project_name="test_antenna",
        height_ft=22.0,
        band_sweep_points=21,
    )
    return cfg


def test_warm_start_prefers_signature_match(tmp_path):
    """Two candidate runs in the DB:
        #1: design_key has 'CB' signature (no current-band match)
        #2: design_key has 'OWA' signature (matches current run)
    Even if #1 has a LOWER stored max_swr, #2 must win when sig='OWA'."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    auto_learn.cfg_band = (26.7, 27.7)         # current run's band
    cfg = _build_cfg()
    # Run #1: CB-band sig, very low stored SWR.
    _insert(con, 1, "test_antenna|CB_SIG|g1|2025-01-01", max_swr=1.1, geo=GOOD_GEOM)
    # Run #2: OWA-band sig, higher stored SWR but signature matches.
    _insert(con, 2, "test_antenna|OWA_SIG|g2|2025-01-02", max_swr=1.4, geo=GOOD_GEOM)
    con.commit()

    # band_swr_curve is mocked to a healthy SWR for both candidates so the
    # sanity check doesn't reject either.
    with patch.object(v2_runner, "band_swr_curve",
                      return_value=([(27.2, 50.0, 0.0, 1.2)], 1.2, 1.1)):
        els, run_id = auto_learn.warm_start_geometry(
            con, cfg, HYBRID, sig="OWA_SIG", height_ft=22.0
        )
    assert run_id == 2, (
        f"signature 'OWA_SIG' must win over 'CB_SIG' even at higher SWR; "
        f"got run #{run_id}"
    )


def test_sanity_guard_rejects_catastrophic_baseline(tmp_path):
    """The exact bug the user hit: warm-start candidate gives baseline
    SWR 927 when probed in the current band.  Must be rejected so we
    fall through to the user's current geometry."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    auto_learn.cfg_band = (24.2, 30.2)         # current run's wide OWA band
    cfg = _build_cfg()
    # Only one candidate, signature-matches, stored max_swr looks fine, but
    # the SANITY PROBE over the current band returns SWR > 5.
    _insert(con, 1, "test_antenna|OWA_SIG|g1|2025-01-01",
            max_swr=1.1, geo=BAD_GEOM)
    con.commit()

    with patch.object(v2_runner, "band_swr_curve",
                      return_value=([(27.2, 5.0, 0.0, 927.0)], 927.0, 500.0)):
        els, run_id = auto_learn.warm_start_geometry(
            con, cfg, HYBRID, sig="OWA_SIG", height_ft=22.0,
            log_fn=lambda *a, **k: None,
        )
    # Catastrophic warm-start rejected -> falls back to HYBRID (input).
    assert run_id is None, (
        f"must REJECT a warm-start whose baseline > 5; got run #{run_id}"
    )
    # Element identity (names) verifies the original HYBRID came back through.
    assert [e["name"] for e in els] == [e["name"] for e in HYBRID]


def test_falls_back_to_project_name_when_no_signature_hit(tmp_path):
    """If the DB has no row with the current signature, the warm-start may
    still use a project-name match (e.g. very early DB before signatures
    were stored).  Sanity guard still applies."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    auto_learn.cfg_band = (26.7, 27.7)
    cfg = _build_cfg()
    # Legacy DB: design_key has no signature segment.
    _insert(con, 1, "test_antenna|legacy|g0|2024-12-01",
            max_swr=1.2, geo=GOOD_GEOM)
    con.commit()

    with patch.object(v2_runner, "band_swr_curve",
                      return_value=([(27.2, 50.0, 0.0, 1.2)], 1.2, 1.1)):
        els, run_id = auto_learn.warm_start_geometry(
            con, cfg, HYBRID, sig="NEW_SIG", height_ft=22.0,
        )
    assert run_id == 1, "fallback to project-name match must still work"


def test_no_match_returns_input_geometry(tmp_path):
    """Empty DB -> warm-start returns the fallback elements unchanged with
    run_id=None.  No crash."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    auto_learn.cfg_band = (26.7, 27.7)
    cfg = _build_cfg()
    els, run_id = auto_learn.warm_start_geometry(con, cfg, HYBRID, sig="SIG")
    assert run_id is None
    assert els is HYBRID                       # exact identity, no copy
