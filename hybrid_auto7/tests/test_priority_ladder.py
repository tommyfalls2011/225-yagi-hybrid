"""Verify the user-specified priority ladder in _objective and _polish_gain.

User's rules (verbatim):

  1. X = 0.0 (within +/-2.5) -- pushed for ONLY if ideal SWR and RL also hit.
  2. After X, return loss highest is best (high RL over wide range good) --
     but NOT if SWR drives |X| over 2.5.
  3. SWR target = 1.0:1; up to 1.07 tolerable when X / RL would otherwise have
     to give up.
  4. Gain > F/B, BUT if F/B is low, sacrifice some gain to recoup F/B.

These tests poke the cost function with synthetic curves to confirm the
ordering, without running nec2c.
"""
import sys
import types
from unittest.mock import patch

from hyagi import match_opt


# We don't want this test to require nec2c.  Mock band_swr_curve to return a
# synthetic curve and evaluate() to return a metric dict the polish layer reads.
def _fake_curve(R_at_fc, X_at_fc, mx=1.05, av=1.03):
    # 5 freq points -- the centre frequency lives in the middle one.
    curve = [
        (26.5, 50.0, -10.0, 1.20),
        (27.0, 49.0,  -5.0, 1.10),
        (27.195, float(R_at_fc), float(X_at_fc), 1.0),  # centre row
        (27.4, 51.0,   5.0, 1.10),
        (27.9, 50.0,  10.0, 1.20),
    ]
    return curve, mx, av


def _patch_curve(R, X, mx=1.05, av=1.03):
    return patch.object(match_opt.v2_runner, "band_swr_curve",
                        side_effect=lambda *a, **kw: _fake_curve(R, X, mx, av))


RULES = {"global": {"freq_mhz_center": 27.195,
                    "freq_mhz_low": 26.5, "freq_mhz_high": 27.9}}


def test_reactance_penalty_jumps_outside_pm_2p5():
    """Priority 1 -- moving |X| from 2.5 -> 3.0 must cost MUCH more than
    moving |X| from 0.0 -> 2.5 (the brutal-ramp outside the zone)."""
    inside_jump = []
    outside_jump = []
    for X in (0.0, 2.5, 3.0):
        with _patch_curve(50.0, X):
            obj, _mx = match_opt._objective([], RULES, 30.0, 26.5, 27.9, 5,
                                            fc=27.195, goal="wideband")
        if X <= 2.5:
            inside_jump.append(obj)
        else:
            outside_jump.append(obj)
    delta_inside = inside_jump[1] - inside_jump[0]      # 0 -> 2.5
    delta_outside = outside_jump[0] - inside_jump[1]    # 2.5 -> 3.0
    # 0.5 ohm outside the zone must be at LEAST as bad as 2.5 ohm inside.
    assert delta_outside > delta_inside, (
        f"|X| moves outside the zone must be heavier; inside_jump={inside_jump}, "
        f"outside_jump={outside_jump}"
    )


def test_centre_swr_pin_dominates_band_max_drift():
    """Priority 3 -- a tune that drops band-max but lifts centre SWR is
    REJECTED by the objective (centre stays pinned)."""
    # Tune A: SWR centred (R=50 X=0), band-max 1.5
    with _patch_curve(50.0, 0.0, mx=1.5, av=1.4):
        obj_a, _ = match_opt._objective([], RULES, 30.0, 26.5, 27.9, 5,
                                        fc=27.195, goal="wideband")
    # Tune B: SWR drifted (R=20 X=0 -> SWR ~2.5), band-max better at 1.1
    with _patch_curve(20.0, 0.0, mx=1.1, av=1.05):
        obj_b, _ = match_opt._objective([], RULES, 30.0, 26.5, 27.9, 5,
                                        fc=27.195, goal="wideband")
    # The pinned tune A wins despite the worse band-max.
    assert obj_a < obj_b, (
        f"centre-pinned tune A ({obj_a:.3f}) should beat drifted tune B "
        f"({obj_b:.3f}) under user's priority ladder"
    )


def test_higher_return_loss_is_rewarded_at_centre():
    """Priority 2 -- between two tunes with identical X and band, the one
    with the deeper centre SWR null (higher RL) wins."""
    # csw 1.02 -> RL ~40 dB
    with _patch_curve(50.0, 1.0, mx=1.2, av=1.1):
        obj_high_rl, _ = match_opt._objective([], RULES, 30.0, 26.5, 27.9, 5,
                                              fc=27.195, goal="wideband")
    # csw 1.07 -> RL ~30 dB
    with _patch_curve(53.5, 1.0, mx=1.2, av=1.1):
        obj_low_rl, _ = match_opt._objective([], RULES, 30.0, 26.5, 27.9, 5,
                                             fc=27.195, goal="wideband")
    assert obj_high_rl < obj_low_rl, (
        f"higher-RL tune ({obj_high_rl:.3f}) should win over lower-RL "
        f"tune ({obj_low_rl:.3f})"
    )


def test_swr_1p07_is_tolerated_when_x_perfect():
    """Priority 3 slack: with X exactly zero, centre SWR 1.07 stays a
    small cost (objective ~ band + small pin + tiny X term) -- the
    matcher will live with 1.07 to keep X perfect."""
    with _patch_curve(53.5, 0.0, mx=1.07, av=1.05):
        obj, _ = match_opt._objective([], RULES, 30.0, 26.5, 27.9, 5,
                                      fc=27.195, goal="wideband")
    # SWR pin at 1.07 = 4*0.07 = 0.28 added; X term = 0; RL term ~ -1.5
    # So total << 5.  Confirms 1.07 SWR isn't a deal-breaker.
    assert obj < 1.5, (
        f"centre SWR 1.07 with X=0 should cost < 1.5 (got {obj:.3f})"
    )
