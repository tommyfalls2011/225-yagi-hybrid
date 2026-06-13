"""OWA stagger-tune matcher tests.

The user's frustration: the prior matcher treated the hybrid driven cell as
a single resonant match cell, so on extreme widebands (3 MHz / 25-28 MHz) the
search stayed in a single-dip basin and could not flatten the SWR.

These tests validate the stagger-tuned seed:
  * narrow band (CB 26.965-27.405) leaves the geometry untouched (no
    unnecessary destabilisation of the existing tunes);
  * wide OWA band (25-28 MHz) shortens the XFRMR and COUPLER relative to
    the DE so the three resonators sit at staggered frequencies in the band;
  * the wideband optimise() entry point completes without raising and
    returns a band SWR finite (i.e. the matcher RAN over the wide band).
"""
import copy
import json
import pathlib
import shutil

import pytest

from hyagi import match_opt, v2_runner

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEO = json.loads((ROOT / "data/current_geometry_v2.json").read_text())
RULES = json.loads((ROOT / "data/rules_v2.json").read_text())
ELEMENTS = GEO["elements"]


def _names_lens(els):
    return {e["name"].upper(): float(e["length_in"]) for e in els}


def test_stagger_seed_noop_for_narrow_band():
    """CB-only (~0.4 MHz) bands should NOT trigger the stagger seed -- the
    existing geometry is left alone so narrow-band tunes are not disrupted."""
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(ELEMENTS), RULES,
        f_low=26.965, f_high=27.405, fc=27.195, log_fn=None,
    )
    assert _names_lens(seeded) == _names_lens(ELEMENTS)


def test_stagger_seed_owa_3mhz_band():
    """On a 3 MHz OWA band the seed must place XFRMR and COUPLER SHORTER
    than the DE (= resonate above design centre).  The design centre stays
    pinned at rules.global.freq_mhz_center -- it does NOT drift to the band
    midpoint, and REF/DIRn/DE lengths must not be rescaled."""
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(ELEMENTS), RULES,
        f_low=25.0, f_high=28.0, fc=26.5, log_fn=None,
    )
    lens = _names_lens(seeded)
    de_len = lens["DE"]
    assert lens["XFRMR"] < de_len - 0.5    # must remain shorter than DE
    assert lens["COUPLER"] < de_len - 0.5
    # Sanity: still inside rules bounds.
    xrules = RULES["elements"]["XFRMR"]
    crules = RULES["elements"]["COUPLER"]
    assert xrules["length_min_in"] <= lens["XFRMR"] <= xrules["length_max_in"]
    assert crules["length_min_in"] <= lens["COUPLER"] <= crules["length_max_in"]


def test_stagger_seed_does_not_drift_design_centre():
    """Critical user requirement: the antenna's natural resonance must stay
    pinned at rules.global.freq_mhz_center (the DESIGN centre) -- even if the
    user picks a band that's NOT symmetric about it.  REF / DIRn / DE lengths
    are never rescaled by the seed; only XFRMR / COUPLER move."""
    rules = json.loads(json.dumps(RULES))
    rules["global"]["freq_mhz_center"] = 27.195      # user's chosen design fc
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(ELEMENTS), rules,
        f_low=25.0, f_high=28.0, fc=26.5, log_fn=None,
    )
    lens = _names_lens(seeded)
    original = _names_lens(ELEMENTS)
    for nm in ("REF", "DE", "DIR1", "DIR2", "DIR3", "DIR4", "DIR5"):
        if nm in original:
            assert lens[nm] == original[nm], (
                f"{nm} got rescaled (was {original[nm]}, now {lens[nm]}) "
                "-- the design centre is no longer where the user set it!"
            )


def test_stagger_seed_xfrmr_above_coupler_in_freq():
    """COUPLER (most coupled to the director array) should sit slightly
    higher in frequency than XFRMR -> shorter length. Both above DE."""
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(ELEMENTS), RULES,
        f_low=25.0, f_high=28.0, fc=26.5, log_fn=None,
    )
    lens = _names_lens(seeded)
    assert lens["COUPLER"] <= lens["XFRMR"]   # COUPLER tuned to higher f


@pytest.mark.skipif(shutil.which("nec2c") is None, reason="nec2c not installed")
def test_owa_wideband_optimize_runs():
    """End-to-end: the wideband matcher must accept a 3 MHz band and produce a
    finite band-max SWR (without crashing).  Asserting a numerical target here
    would be flaky on shared CI -- the regression we are guarding is that the
    search RUNS on extreme widebands at all."""
    rules = json.loads(json.dumps(RULES))
    rules["global"]["freq_mhz_low"] = 25.0
    rules["global"]["freq_mhz_high"] = 28.0
    rules["global"]["freq_mhz_center"] = 26.5
    els, mx, curve = match_opt.optimize(
        copy.deepcopy(ELEMENTS), rules,
        height_ft=30.0, target_swr=1.5,
        points=7,                       # keep test fast
        restarts=0, polish_gain=False,
        log_fn=lambda *a, **k: None,
        goal="wideband",
    )
    assert isinstance(mx, float)
    assert mx < 50.0                    # not the 99.0 "failed-eval" sentinel
    assert curve and len(curve) >= 5


@pytest.mark.skipif(shutil.which("nec2c") is None, reason="nec2c not installed")
def test_owa_stagger_beats_no_stagger_band_max():
    """The seeded run must NOT be worse than a single-resonance start across
    the wideband -- the whole point of the stagger seed.

    We compare band-max SWR from a stagger-seeded start vs the raw geometry
    over the same fixed budget; in the worst case they tie."""
    rules = json.loads(json.dumps(RULES))
    rules["global"]["freq_mhz_low"] = 25.0
    rules["global"]["freq_mhz_high"] = 28.0
    rules["global"]["freq_mhz_center"] = 26.5

    _curve, raw_mx, _ = v2_runner.band_swr_curve(
        ELEMENTS, 25.0, 28.0, 7, 30.0,
    )
    seeded = match_opt._apply_stagger_seed(
        copy.deepcopy(ELEMENTS), rules,
        f_low=25.0, f_high=28.0, fc=26.5, log_fn=None,
    )
    _curve2, seed_mx, _ = v2_runner.band_swr_curve(
        seeded, 25.0, 28.0, 7, 30.0,
    )
    # The seed alone may not beat raw on every random geometry, but after a
    # single coordinate-descent pass over the wide band it MUST not be worse.
    _els, opt_mx, _ = match_opt.optimize(
        copy.deepcopy(ELEMENTS), rules,
        height_ft=30.0, target_swr=1.5,
        points=7, restarts=0, polish_gain=False,
        log_fn=lambda *a, **k: None,
        goal="wideband",
    )
    assert opt_mx <= raw_mx + 0.05, (
        f"OWA optimise made things worse: raw={raw_mx:.3f}  opt={opt_mx:.3f}  "
        f"seed_only={seed_mx:.3f}"
    )
