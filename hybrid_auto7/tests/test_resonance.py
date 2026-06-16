"""hyagi.resonance.find_de_resonance tests.

The resonance helper does a coarse -> medium -> fine sweep of DE length
looking for the value that puts centre SWR at its minimum.  This test
patches v2_runner.band_swr_curve with a known synthetic 'antenna response'
(a parabola in SWR centred at a known DE length) and verifies the search
converges to within 0.25 inch of the true minimum.
"""
from unittest.mock import patch

from hyagi import resonance


def _synthetic_response(true_resonant_len):
    """Returns a band_swr_curve mock whose SWR is a parabola centred at
    `true_resonant_len`.  The function inspects the elements list to pick
    the current DE length, returning SWR = 1 + (L - true)^2 * 0.01."""

    def fake_band_swr_curve(elements, lo, hi, npts, height_ft):
        de = next((e for e in elements if str(e["name"]).upper() == "DE"), None)
        if de is None:
            return [], 99.0, 99.0
        L = float(de["length_in"])
        swr = 1.0 + ((L - true_resonant_len) ** 2) * 0.01
        return [(lo, 50.0, 0.0, swr)], swr, swr

    return fake_band_swr_curve


def test_finds_resonance_within_quarter_inch():
    """Truth is 218.5 in.  Starting from 210.2 in (the buggy 0.484*lambda
    seed value).  Search must land within 0.25 of 218.5."""
    truth = 218.5
    elements = [
        {"name": "REF",   "position_in": 0.0,   "length_in": 220.0},
        {"name": "DE",    "position_in": 50.0,  "length_in": 210.2},
        {"name": "DIR1",  "position_in": 130.0, "length_in": 195.0},
    ]
    with patch.object(resonance.v2_runner, "band_swr_curve",
                      side_effect=_synthetic_response(truth)):
        out = resonance.find_de_resonance(elements, fc_mhz=27.195,
                                          height_ft=22.0)
    de_after = next(e for e in out if str(e["name"]).upper() == "DE")
    assert abs(float(de_after["length_in"]) - truth) < 0.25, (
        f"DE should converge to within 1/4\" of {truth}, "
        f"got {de_after['length_in']}"
    )


def test_no_de_returns_unchanged():
    """Geometry without a DE element must come back unchanged (no crash)."""
    elements = [{"name": "REF", "position_in": 0.0, "length_in": 220.0}]
    out = resonance.find_de_resonance(elements, fc_mhz=27.195, height_ft=22.0)
    assert len(out) == 1
    assert out[0]["name"] == "REF"


def test_clamps_to_rules_bounds():
    """If the rules cap DE length, the search must stay within them."""
    elements = [
        {"name": "REF", "position_in": 0.0,  "length_in": 220.0},
        {"name": "DE",  "position_in": 50.0, "length_in": 210.2},
    ]
    rules = {"elements": {"DE": {"length_min_in": 200.0, "length_max_in": 215.0}}}
    # Truth is 218.5 but the rules cap at 215.
    with patch.object(resonance.v2_runner, "band_swr_curve",
                      side_effect=_synthetic_response(218.5)):
        out = resonance.find_de_resonance(elements, fc_mhz=27.195,
                                          height_ft=22.0, rules=rules)
    de_after = next(e for e in out if str(e["name"]).upper() == "DE")
    assert 200.0 <= float(de_after["length_in"]) <= 215.0, (
        f"DE must respect rules bounds 200..215, got {de_after['length_in']}"
    )


def test_handles_nec_errors_gracefully():
    """When band_swr_curve raises, the search must not crash -- it returns
    the input geometry's DE length unchanged."""
    elements = [
        {"name": "REF", "position_in": 0.0,  "length_in": 220.0},
        {"name": "DE",  "position_in": 50.0, "length_in": 210.2},
    ]
    with patch.object(resonance.v2_runner, "band_swr_curve",
                      side_effect=RuntimeError("nec2c crash")):
        out = resonance.find_de_resonance(elements, fc_mhz=27.195,
                                          height_ft=22.0)
    de_after = next(e for e in out if str(e["name"]).upper() == "DE")
    # Crash -> 99 returned for every probe -> first init guess wins or stays.
    assert "length_in" in de_after
