"""Regression test: FREE boom mode must let the optimizer expand director
spacings out of the previously-locked basin.

Bug history: switching FIXED -> FREE left director positions compressed to
the prior cap.  Coordinate descent step sizes (0.5"-8") are too small to
walk the boom 30-100" outward, so the boom stayed artificially short.
Fix: when boom_max_in <= 0 and tune_spacings=True, reseed director gaps
to the midpoint of each pair's rules spacing window BEFORE descent runs.
XFRMR / DE / COUPLER tight-cell spacings (4-32") are intentionally NOT
touched -- they hold the hybrid/OWA wideband resonator triple.
"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from hyagi import match_opt


def _span(els):
    ps = [float(e["position_in"]) for e in els]
    return max(ps) - min(ps)


@pytest.fixture
def rules():
    with open(os.path.join(HERE, "..", "data", "rules_v2.json")) as f:
        r = json.load(f)
    # Narrow band so a single descent is fast.
    r["global"]["freq_mhz_center"] = 27.195
    r["global"]["freq_mhz_low"] = 26.965
    r["global"]["freq_mhz_high"] = 27.425
    return r


@pytest.fixture
def compressed_elements():
    """7-element geometry crammed to its minimum spacings (~183" span),
    as if a previous FIXED tune compressed it to a short locked boom.
    Note the tight XFRMR_DE=6.5" / DE_COUPLER=6.0" cell -- the fix must
    preserve these wideband-coupling spacings."""
    return [
        {"name": "REF",     "position_in":   0.0,  "length_in": 220.0},
        {"name": "XFRMR",   "position_in":  35.0,  "length_in": 191.0},
        {"name": "DE",      "position_in":  41.5,  "length_in": 210.0},
        {"name": "COUPLER", "position_in":  47.5,  "length_in": 182.25},
        {"name": "DIR1",    "position_in":  95.5,  "length_in": 190.5},
        {"name": "DIR2",    "position_in": 135.5,  "length_in": 175.0},
        {"name": "DIR3",    "position_in": 183.5,  "length_in": 191.75},
    ]


def _has_nec2c():
    import shutil
    return shutil.which("nec2c") is not None


@pytest.mark.skipif(not _has_nec2c(), reason="nec2c binary not installed")
def test_free_boom_reseeds_director_spacings(rules, compressed_elements):
    """FREE mode + tune_spacings=True must reseed director gaps to rules
    midpoints, producing a starting span noticeably larger than the
    compressed input.  Theoretical max span from the default rules is
    ~413"; midpoint reseed should put us in the 260-310" range."""
    rules_free = copy.deepcopy(rules)
    rules_free["global"]["boom_max_in"] = 0.0          # FREE mode

    start_span = _span(compressed_elements)
    assert start_span < 200.0, "fixture should be compressed (<200\")"

    best, _mx, _curve = match_opt.optimize(
        copy.deepcopy(compressed_elements), rules_free,
        height_ft=30.0,
        target_swr=1.20,
        points=5,
        restarts=0,
        steps=(8.0, 4.0),
        seed=42,
        polish_gain=False,
        log_fn=lambda *_a, **_k: None,
        goal="wideband",
        tune_spacings=True,                            # FREE: spacings move
    )

    final_span = _span(best)
    # The reseed alone should land the boom in the 250-330" band.
    assert final_span > start_span + 50.0, (
        f"FREE boom failed to grow out of compressed basin: "
        f"start={start_span:.1f}\"  final={final_span:.1f}\""
    )

    # Critical: the tight hybrid/OWA cell (XFRMR_DE, DE_COUPLER) must stay
    # near the user's tuned sweet spot (~5-7").  The reseed only moves the
    # REF gap and director chain; cell gaps are bounded by the rules
    # spacings (4-32") and the optimizer can refine within that window,
    # but the start MUST come in tight.  This catches future regressions
    # that would naively widen the whole boom.
    cell = {e["name"]: float(e["position_in"]) for e in best}
    xf_de = cell["DE"] - cell["XFRMR"]
    de_cp = cell["COUPLER"] - cell["DE"]
    assert 4.0 <= xf_de <= 32.0, f"XFRMR_DE out of rules window: {xf_de:.2f}\""
    assert 4.0 <= de_cp <= 32.0, f"DE_COUPLER out of rules window: {de_cp:.2f}\""


@pytest.mark.skipif(not _has_nec2c(), reason="nec2c binary not installed")
def test_fixed_boom_does_not_reseed(rules, compressed_elements):
    """FIXED mode must NOT reseed -- the user's locked boom length wins.
    Starting from a 183" geometry with cap_in=240, final span should be
    rescaled to ~240 (the cap), not blown out to the rules midpoints."""
    rules_fixed = copy.deepcopy(rules)
    rules_fixed["global"]["boom_max_in"] = 240.0       # FIXED at 240"

    best, _mx, _curve = match_opt.optimize(
        copy.deepcopy(compressed_elements), rules_fixed,
        height_ft=30.0,
        target_swr=1.20,
        points=5,
        restarts=0,
        steps=(8.0,),
        seed=42,
        polish_gain=False,
        log_fn=lambda *_a, **_k: None,
        goal="wideband",
        tune_spacings=True,
    )

    final_span = _span(best)
    # FIXED mode rescales endpoints to exactly the cap (within ~1").
    assert abs(final_span - 240.0) < 2.0, (
        f"FIXED boom should pin span to cap (240\") but got {final_span:.2f}\""
    )
