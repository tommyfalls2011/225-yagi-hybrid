"""When the user has seeded the cell via the Antenna Setup panel and ticked
'Respect my seeded cell', the wideband stagger seed must NOT override their
XFRMR / COUPLER / DE / REF lengths.

User bug report (verbatim):
  'Starting geometry: XFRMR len 17' 1-5/16\" (205) ... DE 18' 4-3/16\" (220)
   [stagger-seed] DE=220.20 XFRMR=225.15 COUPLER=202.95
   The matcher RESET XFRMR to 225.15 -- overrode my bench numbers in ~10ms.'

This test guards the new respect_seeded_cell flag: when rules.global has
it set to True, _apply_stagger_seed() returns the input unchanged and
logs a one-line skip notice.  When it's False or absent, the existing
asymmetric OWA stagger still applies.
"""
import copy
import json

from hyagi import match_opt


HYBRID = [
    {"name": "REF",     "position_in": 0.0,    "length_in": 232.75},   # LONG
    {"name": "XFRMR",   "position_in": 36.5,   "length_in": 205.31},   # SHORTER than DE (user choice)
    {"name": "DE",      "position_in": 42.0,   "length_in": 220.19},
    {"name": "COUPLER", "position_in": 65.5,   "length_in": 198.62},
    {"name": "DIR1",    "position_in": 121.9,  "length_in": 195.0},
    {"name": "DIR2",    "position_in": 192.1,  "length_in": 191.1},
    {"name": "DIR3",    "position_in": 262.3,  "length_in": 187.2},
]
RULES_BASE = {
    "global": {"freq_mhz_low": 25.7, "freq_mhz_high": 28.7,
               "freq_mhz_center": 27.195},
    "elements": {},
}


def _lens(els):
    return {e["name"].upper(): float(e["length_in"]) for e in els}


def test_respect_seeded_cell_disables_stagger():
    """With rules.global.respect_seeded_cell=True, _apply_stagger_seed must
    return the input geometry UNCHANGED -- the user's bench-tested lengths
    are kept as-is."""
    rules = json.loads(json.dumps(RULES_BASE))
    rules["global"]["respect_seeded_cell"] = True
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(HYBRID), rules,
        f_low=25.7, f_high=28.7, fc=27.195, log_fn=None,
    )
    assert _lens(seeded) == _lens(HYBRID), (
        f"respect_seeded_cell=True must NOT change any element length; "
        f"got {_lens(seeded)} vs original {_lens(HYBRID)}"
    )


def test_respect_seeded_cell_logs_skip_notice():
    """The user must see a log line confirming the seed was skipped."""
    log_lines = []
    rules = json.loads(json.dumps(RULES_BASE))
    rules["global"]["respect_seeded_cell"] = True
    match_opt._apply_stagger_seed(
        copy.deepcopy(HYBRID), rules,
        f_low=25.7, f_high=28.7, fc=27.195,
        log_fn=lambda msg: log_lines.append(msg),
    )
    assert any("[stagger-seed]" in m and "respect_seeded_cell" in m
               for m in log_lines), (
        f"must log a skip notice mentioning respect_seeded_cell; "
        f"got {log_lines}"
    )


def test_stagger_still_fires_when_flag_is_false():
    """The OWA stagger still applies for users who haven't seeded a cell --
    rules.global.respect_seeded_cell False or absent."""
    rules = json.loads(json.dumps(RULES_BASE))
    rules["global"]["respect_seeded_cell"] = False
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(HYBRID), rules,
        f_low=25.7, f_high=28.7, fc=27.195, log_fn=None,
    )
    # Stagger fires -> XFRMR length CHANGES (was 205.31, will be ~225).
    assert abs(_lens(seeded)["XFRMR"] - 205.31) > 1.0, (
        f"With respect_seeded_cell=False the stagger must run and change "
        f"XFRMR; got {_lens(seeded)['XFRMR']} (unchanged)"
    )


def test_missing_flag_defaults_to_stagger_on():
    """Older saved rules without the new flag must still get the stagger
    seed (preserves prior behaviour for users who haven't updated their
    saved config)."""
    rules = json.loads(json.dumps(RULES_BASE))
    # No respect_seeded_cell key at all -- the default behaviour.
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(HYBRID), rules,
        f_low=25.7, f_high=28.7, fc=27.195, log_fn=None,
    )
    # Stagger fires -> XFRMR length changes.
    assert abs(_lens(seeded)["XFRMR"] - 205.31) > 1.0
