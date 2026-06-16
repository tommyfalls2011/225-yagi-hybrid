"""polish_gain must ALWAYS run after the wideband descent has settled --
including when the auto-fit step couldn't fully meet the user's SWR target.

User bug: with target SWR 1.20 and an aggressive +/-1.5 MHz half-width, the
matcher auto-fit settled at band-max 1.34 -- ABOVE target.  Old code only
ran polish_gain when best_mx <= target_swr, so the F/B floor (12 dB) was
never enforced.  Result: F/B collapsed to 7.84 dB on the final geometry.

These tests verify polish_gain is invoked even when best_mx > target_swr,
and that the SWR ceiling used for polish is max(target, best_mx) so polish
can never make band-max worse.
"""
from unittest.mock import patch

from hyagi import match_opt


def test_polish_runs_even_when_target_missed():
    """The wideband descent settled at band-max 1.34 with target 1.20.
    polish_gain MUST be called so it can recover gain/F-B under the
    achieved-SWR ceiling, instead of being skipped entirely."""
    polish_calls = []

    def fake_polish(elements, rules, de_pos, height_ft, f_low, f_high,
                    points, target_swr, log_fn):
        polish_calls.append({"target_swr_arg": target_swr})
        return elements

    def fake_descend(*a, **k):
        # Returns (vec, obj, mx) -- mx > target_swr (target=1.20).
        return ({"de_len": 215.7}, 1.5, 1.34)

    rules = {
        "global": {"freq_mhz_low": 26.7, "freq_mhz_high": 27.7,
                   "freq_mhz_center": 27.195},
        "elements": {},
    }
    elements = [
        {"name": "REF",     "position_in": 0.0,   "length_in": 218.5},
        {"name": "XFRMR",   "position_in": 28.4,  "length_in": 199.3},
        {"name": "DE",      "position_in": 46.9,  "length_in": 215.7},
        {"name": "COUPLER", "position_in": 66.9,  "length_in": 199.9},
        {"name": "DIR1",    "position_in": 135.9, "length_in": 195.0},
    ]
    with patch.object(match_opt, "_descend", side_effect=fake_descend), \
         patch.object(match_opt, "_polish_gain", side_effect=fake_polish), \
         patch.object(match_opt.v2_runner, "band_swr_curve",
                      return_value=([(27.195, 50.0, 0.0, 1.0)], 1.34, 1.20)):
        match_opt.optimize(elements, rules, height_ft=22.0,
                           target_swr=1.20,
                           points=5, restarts=0, polish_gain=True,
                           log_fn=lambda *a, **k: None,
                           goal="wideband", tune_spacings=False)

    assert polish_calls, (
        "polish_gain must be called even when best_mx > target_swr; "
        "without it the F/B floor is never enforced"
    )
    # And the ceiling passed to polish must be the achieved band-max so it
    # can't push band-max worse than what the descent achieved.
    first_call = polish_calls[0]
    assert first_call["target_swr_arg"] >= 1.34 - 1e-6, (
        f"polish ceiling must be >= achieved best_mx so it cannot regress; "
        f"got {first_call['target_swr_arg']}"
    )


def test_polish_still_skipped_for_resonant_goal():
    """Resonant mode tunes the centre tightly; we must NOT polish it (would
    move directors and disturb the centre match)."""
    polish_calls = []

    def fake_polish(*a, **k):
        polish_calls.append(True)
        return a[0]

    def fake_descend(*a, **k):
        return ({"de_len": 215.7}, 1.05, 1.05)

    rules = {
        "global": {"freq_mhz_low": 26.7, "freq_mhz_high": 27.7,
                   "freq_mhz_center": 27.195},
        "elements": {},
    }
    elements = [
        {"name": "REF",     "position_in": 0.0,   "length_in": 218.5},
        {"name": "XFRMR",   "position_in": 28.4,  "length_in": 199.3},
        {"name": "DE",      "position_in": 46.9,  "length_in": 215.7},
        {"name": "COUPLER", "position_in": 66.9,  "length_in": 199.9},
        {"name": "DIR1",    "position_in": 135.9, "length_in": 195.0},
    ]
    with patch.object(match_opt, "_descend", side_effect=fake_descend), \
         patch.object(match_opt, "_polish_gain", side_effect=fake_polish), \
         patch.object(match_opt.v2_runner, "band_swr_curve",
                      return_value=([(27.195, 50.0, 0.0, 1.0)], 1.05, 1.02)):
        match_opt.optimize(elements, rules, height_ft=22.0, target_swr=1.20,
                           points=5, restarts=0, polish_gain=True,
                           log_fn=lambda *a, **k: None,
                           goal="resonant", tune_spacings=False)
    assert not polish_calls, (
        "polish_gain must NOT run in resonant mode -- it'd move directors "
        "and disturb the centre R/X match"
    )
