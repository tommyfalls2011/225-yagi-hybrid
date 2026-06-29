"""Per-element taper override tests.

`data/taper_v2.json` may carry an `overrides` map keyed by element name (REF,
XFRMR, DE, COUPLER, DIRn).  When present, v2_runner.get_active_taper(name)
must return that override; otherwise the global default.  The learning DB
signature must capture overrides so per-element-taper runs don't share memory
with the default-taper bucket.
"""
import json
import pathlib

import pytest

from hyagi import v2_runner


ELS = [
    {"name": "REF",     "position_in": 0.0,   "length_in": 218.5},
    {"name": "XFRMR",   "position_in": 28.4,  "length_in": 199.3},
    {"name": "DE",      "position_in": 46.9,  "length_in": 215.7},
    {"name": "COUPLER", "position_in": 66.9,  "length_in": 199.9},
    {"name": "DIR1",    "position_in": 135.9, "length_in": 195.0},
    {"name": "DIR2",    "position_in": 214.1, "length_in": 191.1},
    {"name": "DIR3",    "position_in": 292.3, "length_in": 187.2},
]


@pytest.fixture
def taper_json(tmp_path, monkeypatch):
    """Redirect v2_runner._DATA_DIR to a tmp dir with our custom taper config.
    Restores after the test so other tests aren't disturbed."""
    monkeypatch.setattr(v2_runner, "_DATA_DIR", tmp_path)
    return tmp_path / "taper_v2.json"


def test_default_returned_with_no_overrides(taper_json):
    taper_json.write_text(json.dumps({"default": [[0.625, 36.0], [0.5, 999.0]]}))
    t = v2_runner.get_active_taper("DIR1")
    assert t == [(0.625, 36.0), (0.5, 999.0)]


def test_override_returned_for_named_element(taper_json):
    taper_json.write_text(json.dumps({
        "default":   [[0.625, 36.0], [0.5, 999.0]],
        "overrides": {"DIR1": [[0.5, 24.0], [0.375, 999.0]]},
    }))
    assert v2_runner.get_active_taper("DIR1") == [(0.5, 24.0), (0.375, 999.0)]
    assert v2_runner.get_active_taper("DE") == [(0.625, 36.0), (0.5, 999.0)]


def test_override_lookup_is_case_insensitive(taper_json):
    taper_json.write_text(json.dumps({
        "default":   [[0.625, 36.0], [0.5, 999.0]],
        "overrides": {"DIR1": [[0.5, 24.0], [0.375, 999.0]]},
    }))
    # User passes "dir1" -- must still resolve to the override.
    assert v2_runner.get_active_taper("dir1") == [(0.5, 24.0), (0.375, 999.0)]


def test_global_default_when_called_with_no_name(taper_json):
    taper_json.write_text(json.dumps({
        "default":   [[0.625, 36.0], [0.5, 999.0]],
        "overrides": {"DIR1": [[0.5, 24.0], [0.375, 999.0]]},
    }))
    assert v2_runner.get_active_taper() == [(0.625, 36.0), (0.5, 999.0)]


def test_get_taper_config_round_trip(taper_json):
    taper_json.write_text(json.dumps({
        "default":   [[0.625, 36.0], [0.5, 999.0]],
        "overrides": {"DIR1": [[0.5, 24.0], [0.375, 999.0]]},
    }))
    cfg = v2_runner.get_taper_config()
    assert cfg["default"] == [[0.625, 36.0], [0.5, 999.0]]
    assert cfg["overrides"] == {"DIR1": [[0.5, 24.0], [0.375, 999.0]]}


def test_signature_default_unchanged_without_overrides(taper_json):
    taper_json.write_text(json.dumps({"default": [[0.625, 36.0], [0.5, 999.0]]}))
    sig_no_els = v2_runner.taper_signature()
    sig_with_els = v2_runner.taper_signature(elements=ELS)
    assert sig_no_els == sig_with_els == "0.625x36;0.5x999"


def test_signature_includes_overrides_that_apply(taper_json):
    taper_json.write_text(json.dumps({
        "default":   [[0.625, 36.0], [0.5, 999.0]],
        "overrides": {"DIR1": [[0.5, 24.0], [0.375, 999.0]],
                      "DIR2": [[0.5, 24.0], [0.375, 999.0]]},
    }))
    sig = v2_runner.taper_signature(elements=ELS)
    assert sig.startswith("0.625x36;0.5x999|")
    # DIR1 + DIR2 listed (sorted), but NOT XFRMR/REF since they don't override.
    assert "DIR1:" in sig and "DIR2:" in sig
    assert "REF:" not in sig and "XFRMR:" not in sig


def test_signature_partitions_learning_db(taper_json):
    """Different override sets must yield different signatures so learning
    memory doesn't bleed between taper variants."""
    taper_json.write_text(json.dumps({"default": [[0.625, 36.0], [0.5, 999.0]]}))
    s_default = v2_runner.taper_signature(elements=ELS)

    taper_json.write_text(json.dumps({
        "default":   [[0.625, 36.0], [0.5, 999.0]],
        "overrides": {"DIR1": [[0.5, 24.0], [0.375, 999.0]]},
    }))
    s_with_override = v2_runner.taper_signature(elements=ELS)

    assert s_default != s_with_override
