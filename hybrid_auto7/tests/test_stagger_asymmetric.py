"""Stagger seed: verify the EMPIRICAL OWA asymmetric layout.

After scripts/hybrid_physics_study.py was run, the data showed the working
3-dip OWA configuration requires:
  XFRMR  ~ DE + 2..5"  (longer than DE -> resonant BELOW fc -> low-side dip)
  COUPLER ~ DE - 10..17" (shorter than DE -> resonant ABOVE fc -> high-side dip)

These tests pin _stagger_lengths and _apply_stagger_seed so the asymmetry is
locked in -- a regression that returns both helpers to 'shorter than DE'
(the old wrong rule) or both 'longer than DE' (the early-overshoot bug) will
fail here.
"""
import copy
import json

from hyagi import match_opt


BASE = [
    {"name": "REF",     "position_in": 0.0,    "length_in": 220.0},
    {"name": "XFRMR",   "position_in": 28.4,   "length_in": 199.0},
    {"name": "DE",      "position_in": 46.9,   "length_in": 215.5},
    {"name": "COUPLER", "position_in": 66.9,   "length_in": 198.0},
    {"name": "DIR1",    "position_in": 155.5,  "length_in": 196.0},
    {"name": "DIR2",    "position_in": 264.1,  "length_in": 193.0},
    {"name": "DIR3",    "position_in": 372.7,  "length_in": 190.0},
]
RULES = {
    "global": {"freq_mhz_low": 25.7, "freq_mhz_high": 28.7,
               "freq_mhz_center": 27.195},
    "elements": {},
}


def _lens(els):
    return {e["name"].upper(): float(e["length_in"]) for e in els}


def test_xfrmr_is_longer_than_de_for_wide_bands():
    """User's data: XFRMR > DE is REQUIRED for the OWA low-side dip.
    Previous code capped XFRMR <= DE - 1 which removed that dip entirely."""
    seeded = match_opt._apply_stagger_seed(
        json.loads(json.dumps(BASE)), RULES,
        f_low=25.7, f_high=28.7, fc=27.195,
    )
    lens = _lens(seeded)
    assert lens["XFRMR"] > lens["DE"], (
        f"XFRMR must be LONGER than DE for the low-side dip; "
        f"got XFRMR={lens['XFRMR']}, DE={lens['DE']}"
    )
    assert (lens["XFRMR"] - lens["DE"]) <= 8.0, (
        f"XFRMR shouldn't overshoot -- empirical sweet spot is DE+2..5; "
        f"got DE+{lens['XFRMR']-lens['DE']:.1f}"
    )


def test_coupler_is_shorter_than_de_for_wide_bands():
    """COUPLER stays much shorter than DE; resonant ABOVE fc -> high-side dip."""
    seeded = match_opt._apply_stagger_seed(
        json.loads(json.dumps(BASE)), RULES,
        f_low=25.7, f_high=28.7, fc=27.195,
    )
    lens = _lens(seeded)
    assert lens["COUPLER"] < lens["DE"], (
        f"COUPLER must be SHORTER than DE; got COUPLER={lens['COUPLER']}, "
        f"DE={lens['DE']}"
    )
    # Sweet spot from data: DE - 10..17.
    drop = lens["DE"] - lens["COUPLER"]
    assert 5.0 <= drop <= 25.0, (
        f"COUPLER offset should be in DE-5..DE-25 range; got DE-{drop:.1f}"
    )


def test_seed_produces_xfrmr_below_de_in_frequency():
    """Sanity check the direction of the stagger: XFRMR must resonate BELOW
    fc (longer length), COUPLER above (shorter length)."""
    seeded = match_opt._apply_stagger_seed(
        json.loads(json.dumps(BASE)), RULES,
        f_low=25.7, f_high=28.7, fc=27.195,
    )
    lens = _lens(seeded)
    # length inversely proportional to resonance freq -- longer = lower freq
    de_freq_ratio = lens["DE"] / lens["DE"]           # 1.0
    xf_freq_ratio = lens["DE"] / lens["XFRMR"]        # < 1.0 (XFRMR resonates BELOW fc)
    cp_freq_ratio = lens["DE"] / lens["COUPLER"]      # > 1.0 (COUPLER above)
    assert xf_freq_ratio < de_freq_ratio
    assert cp_freq_ratio > de_freq_ratio


def test_narrow_band_no_stagger():
    """For narrow bands (<= 1 MHz) the stagger seed is a no-op so a CB-only
    tune doesn't get its helpers re-positioned."""
    narrow_rules = {
        "global": {"freq_mhz_low": 27.0, "freq_mhz_high": 27.4,
                   "freq_mhz_center": 27.195},
        "elements": {},
    }
    before = _lens(BASE)
    seeded = match_opt._apply_stagger_seed(
        json.loads(json.dumps(BASE)), narrow_rules,
        f_low=27.0, f_high=27.4, fc=27.195,
    )
    assert _lens(seeded) == before, (
        "Narrow bands (<=1 MHz) shouldn't trigger stagger; geometry must "
        "come back unchanged"
    )


def test_objective_naturally_prefers_multi_dip_when_avg_lower():
    """The matcher previously had an explicit multi-dip bonus term in
    _objective() that caused real-world regressions (a 1.12-SWR single-dip
    geometry losing to a mediocre 3-dip one because the bonus outweighed
    the band-max delta).  The term was removed.  The natural mx+0.05*av
    objective STILL prefers genuinely better multi-dip curves whenever the
    average SWR drops -- which is the cleaner physical signal anyway."""
    from unittest.mock import patch

    def fake_band_swr_curve_1dip(*a, **k):
        # One dip at 27.195
        pts = [(25.7, 50.0, -2.0, 2.5),
               (26.5, 50.0, -1.0, 1.5),
               (27.195, 50.0, 0.0, 1.0),    # the single dip
               (27.9, 50.0, +1.0, 1.5),
               (28.7, 50.0, +2.0, 2.5)]
        return pts, 2.5, 1.8

    def fake_band_swr_curve_3dip(*a, **k):
        # Three dips at 26, 27.2, 28.4 -- classic OWA with lower avg SWR
        pts = [(25.7, 50.0, -2.0, 2.5),
               (26.0, 50.0, -1.0, 1.2),    # dip 1
               (26.5, 50.0,  0.0, 1.6),
               (27.195, 50.0, 0.0, 1.0),   # dip 2
               (27.9, 50.0,  0.0, 1.6),
               (28.4, 50.0,  0.0, 1.2),    # dip 3
               (28.7, 50.0, +1.0, 2.5)]
        return pts, 2.5, 1.7

    rules = {"global": {"freq_mhz_center": 27.195}, "elements": {}}
    with patch.object(match_opt.v2_runner, "band_swr_curve",
                      side_effect=fake_band_swr_curve_1dip):
        obj_1dip, _ = match_opt._objective(
            [], rules, 26.0, 25.7, 28.7, 7,
            fc=27.195, goal="wideband",
        )
    with patch.object(match_opt.v2_runner, "band_swr_curve",
                      side_effect=fake_band_swr_curve_3dip):
        obj_3dip, _ = match_opt._objective(
            [], rules, 26.0, 25.7, 28.7, 7,
            fc=27.195, goal="wideband",
        )
    assert obj_3dip < obj_1dip, (
        f"3-dip curve (obj={obj_3dip:.3f}) must be preferred to "
        f"1-dip curve (obj={obj_1dip:.3f}) via the natural mx+0.05*av term"
    )
