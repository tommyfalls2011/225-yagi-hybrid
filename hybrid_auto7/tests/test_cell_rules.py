"""Regression tests for hyagi.cell_rules."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from hyagi.cell_rules import (
    violates_cell_rules,
    validate_final,
    CellRulesViolation,
    guard_eval,
    extract_cell_geom,
    MIN_LEN_GAP_FROM_DE,
    MIN_SPACING_IN, MAX_SPACING_IN,
)


class FakeEl:
    def __init__(self, name, pos, length):
        self.name, self.position_in, self.length_in = name, pos, length


def test_clean_geometry_passes():
    assert violates_cell_rules(de_len=210.0, x_len=200.0, c_len=195.0,
                               x_spacing=15.0, c_spacing=12.0) is None


def test_xfrmr_longer_than_de_is_rejected():
    err = violates_cell_rules(de_len=210.0, x_len=212.0, c_len=195.0)
    assert err is not None and "XFRMR length" in err


def test_xfrmr_too_close_to_de_in_length():
    # 4" floor: 210 - 4 = 206 -> 207 must fail
    err = violates_cell_rules(de_len=210.0, x_len=207.0, c_len=195.0)
    assert err is not None


def test_xfrmr_exactly_at_floor_passes():
    err = violates_cell_rules(de_len=210.0, x_len=206.0, c_len=195.0)
    assert err is None


def test_coupler_longer_than_xfrmr_rejected():
    err = violates_cell_rules(de_len=210.0, x_len=200.0, c_len=204.0)
    # coupler also fails the 4" floor (210-4=206 < 204? no, 204<206 so floor ok),
    # but XFRMR=200 < COUPLER=204 -> must fail XFRMR>=COUPLER
    assert err is not None and ("XFRMR length" in err and "COUPLER length" in err)


def test_spacing_out_of_range():
    err = violates_cell_rules(de_len=210, x_len=200, c_len=195, x_spacing=2.0)
    assert err is not None and "XFRMR spacing" in err
    err = violates_cell_rules(de_len=210, x_len=200, c_len=195, c_spacing=40.0)
    assert err is not None and "COUPLER spacing" in err


def test_extract_geom_from_elements():
    els = [FakeEl("XFRMR", 30, 200), FakeEl("DE", 60, 210),
           FakeEl("COUPLER", 75, 195)]
    g = extract_cell_geom(els)
    assert g["de_len"] == 210 and g["x_len"] == 200 and g["c_len"] == 195
    assert g["x_spacing"] == 30 and g["c_spacing"] == 15
    assert g["ref_len"] is None and g["directors"] == []


def test_validate_final_raises_on_bad():
    bad = [FakeEl("XFRMR", 30, 220), FakeEl("DE", 60, 210),
           FakeEl("COUPLER", 75, 195)]
    with pytest.raises(CellRulesViolation):
        validate_final(bad)


def test_guard_eval_lets_good_through():
    @guard_eval
    def eval_fn(elements, *a, **k):
        return {"ok": True}
    good = [FakeEl("XFRMR", 30, 200), FakeEl("DE", 60, 210),
            FakeEl("COUPLER", 75, 195)]
    assert eval_fn(good)["ok"]


def test_guard_eval_blocks_bad():
    @guard_eval
    def eval_fn(elements, *a, **k):
        return {"ok": True}
    bad = [FakeEl("XFRMR", 30, 220), FakeEl("DE", 60, 210),
           FakeEl("COUPLER", 75, 195)]
    with pytest.raises(CellRulesViolation):
        eval_fn(bad)
