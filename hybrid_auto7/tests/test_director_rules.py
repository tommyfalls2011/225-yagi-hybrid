"""Tests for director progression + REF + pattern rules."""
import os, sys, importlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# Force-reload module per test so HYAGI_DIRECTOR_MODE env var takes effect
def _reload():
    import hyagi.cell_rules as cr
    importlib.reload(cr)
    return cr


class FakeEl:
    def __init__(self, name, pos, length):
        self.name, self.position_in, self.length_in = name, pos, length


def _full(de_len, x_len, c_len, ref_len, director_lengths):
    """Build a full element list matching extract_cell_geom expectations."""
    els = [
        FakeEl("REF",     0,   ref_len),
        FakeEl("XFRMR",   30,  x_len),
        FakeEl("DE",      60,  de_len),
        FakeEl("COUPLER", 75,  c_len),
    ]
    for i, L in enumerate(director_lengths, start=1):
        els.append(FakeEl(f"DIR{i}", 90 + 20*i, L))
    return els


# ----- REF rule -----
def test_ref_shorter_than_de_rejected():
    cr = _reload()
    els = _full(de_len=210, x_len=200, c_len=180, ref_len=205, director_lengths=[])
    with pytest.raises(cr.CellRulesViolation):
        cr.validate_final(els)


def test_ref_equal_to_de_ok():
    cr = _reload()
    els = _full(de_len=210, x_len=200, c_len=180, ref_len=210, director_lengths=[])
    cr.validate_final(els)  # no raise


# ----- Strict mode -----
def test_strict_dir1_longer_than_coupler_rejected():
    os.environ["HYAGI_DIRECTOR_MODE"] = "strict_progressive"
    cr = _reload()
    els = _full(de_len=210, x_len=200, c_len=171.5, ref_len=226.5,
                director_lengths=[197.0])  # the real-world example!
    with pytest.raises(cr.CellRulesViolation):
        cr.validate_final(els)


def test_strict_dir_progression_rejected():
    os.environ["HYAGI_DIRECTOR_MODE"] = "strict_progressive"
    cr = _reload()
    # DIR2 longer than DIR1 -> reject
    els = _full(210, 200, 171, 226, director_lengths=[168, 172, 160])
    with pytest.raises(cr.CellRulesViolation):
        cr.validate_final(els)


def test_strict_clean_progression_ok():
    os.environ["HYAGI_DIRECTOR_MODE"] = "strict_progressive"
    cr = _reload()
    els = _full(210, 200, 171, 226, director_lengths=[168, 164, 160])
    cr.validate_final(els)


# ----- Experimental mode -----
def test_experimental_lets_long_directors_through_geometrically():
    os.environ["HYAGI_DIRECTOR_MODE"] = "experimental_progressive"
    cr = _reload()
    els = _full(210, 200, 171, 226, director_lengths=[197, 200, 195])
    cr.validate_final(els)  # geometry alone does NOT raise


def test_experimental_blocks_backwards_pattern_via_guard():
    os.environ["HYAGI_DIRECTOR_MODE"] = "experimental_progressive"
    cr = _reload()
    els = _full(210, 200, 171, 226, director_lengths=[197])

    @cr.guard_eval
    def eval_fn(elements):
        return {"fb_db": -3.5, "real_gain_dbi": 8.0, "rear_gain_dbi": 11.5}

    with pytest.raises(cr.CellRulesViolation):
        eval_fn(els)


def test_experimental_lets_forward_pattern_through_guard():
    os.environ["HYAGI_DIRECTOR_MODE"] = "experimental_progressive"
    cr = _reload()
    els = _full(210, 200, 171, 226, director_lengths=[197])

    @cr.guard_eval
    def eval_fn(elements):
        return {"fb_db": 18.0, "real_gain_dbi": 12.0, "rear_gain_dbi": 5.0}

    assert eval_fn(els)["fb_db"] == 18.0

