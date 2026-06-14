"""Single-source-of-truth F/B tests.

User requirement (after the F/B value kept drifting between the matcher's
Result panel and the Report): 'fix it right now -- it should look at a
specific point on the back, not the whole rear region'.

This module verifies:
  1. hyagi.fb.front_to_back implements the TEXTBOOK definition (peak gain
     minus gain at exactly (peak_theta, peak_phi+180) -- one point only).
  2. Bilinear interpolation lands the right gain when the rear point isn't
     on the 5-degree grid.
  3. v2_runner.evaluate and perf_report.analyze both call hyagi.fb so they
     CANNOT diverge -- patching the function makes both code paths read
     the patched value.  This is the regression test that locks down the
     'one source of truth' invariant.
"""
from unittest.mock import patch

from hyagi import fb, perf_report, v2_runner


# ---------- the function itself --------------------------------------------

def test_textbook_single_point_fb():
    """The rear sample at exactly (peak_theta, peak_phi+180 deg) is the ONLY
    point that feeds F/B.  Even very loud sidelobes or high-angle backlobes
    must not change the number."""
    pat = []
    for theta in (0, 5, 10, 70, 75, 80, 85, 90):
        for phi in range(0, 365, 5):       # 0..360 inclusive
            if theta == 80 and phi == 0:
                g = 15.0                   # forward peak
            elif theta == 80 and phi == 180:
                g = -5.0                   # textbook back point
            elif theta == 80 and phi in (175, 185):
                g = +6.0                   # loud rear SIDE lobes (textbook ignores)
            elif theta == 30 and phi == 180:
                g = +9.0                   # loud high-angle ground backlobe (ignore)
            else:
                g = -20.0
            pat.append((float(theta), float(phi), g))
    val = fb.front_to_back(pat)
    # 15 dBi peak minus -5 dBi at the exact back point = 20 dB.
    assert abs(val - 20.0) < 0.01, (
        f"textbook F/B must be peak - back_point gain (20), got {val:.2f}"
    )


def test_bilinear_interp_off_grid():
    """If the back point's phi isn't sampled exactly, bilinear interpolation
    on the existing grid samples is applied.  Peak at theta=80, phi=2.5 (off
    grid in phi); back point becomes theta=80, phi=182.5 -- between phi=180
    (-10 dBi) and phi=185 (-20 dBi), so the interp lands -15 dBi back; F/B =
    12 - (-15) = 27 dB."""
    pat = [
        # Forward peak (off-grid phi).
        (80.0,   2.5, 12.0),
        # Grid samples around the back point.
        (80.0, 180.0, -10.0),
        (80.0, 185.0, -20.0),
        # A couple of unrelated samples so the function has data to
        # work with on the rest of the sphere.
        (80.0,   0.0, 11.0),
        (80.0,   5.0, 12.0),
    ]
    val = fb.front_to_back(pat)
    assert abs(val - 27.0) < 0.5, (
        f"bilinear-interp F/B should be 27 dB (12 - mean(-10, -20)), got {val:.2f}"
    )


def test_front_to_rear_uses_whole_rear_hemisphere():
    """F/R is a DIFFERENT number -- it's the worst-case rear lobe anywhere
    in the rear hemisphere, not the back-point.  Must be >= F/B."""
    pat = [
        (80.0,   0.0, 15.0),   # forward peak
        (80.0, 180.0, -5.0),   # textbook back point  -> F/B = 20
        (60.0, 170.0, +2.0),   # off-axis high rear sidelobe   -> F/R = 13
    ]
    assert abs(fb.front_to_back(pat) - 20.0) < 0.01
    fr = fb.front_to_rear(pat)
    assert abs(fr - 13.0) < 0.5, f"F/R should be ~13 dB, got {fr:.2f}"
    # F/R >= F/B always.
    assert fr <= fb.front_to_back(pat)


# ---------- the two callers MUST go through hyagi.fb -----------------------

def test_v2_runner_evaluate_calls_hyagi_fb():
    """If the matcher had its own inline F/B, this monkeypatch would have no
    effect on what evaluate() reports.  Asserts the wiring is real."""
    pattern = [(80.0, 0.0, 15.0), (80.0, 180.0, -5.0)]
    impedances = [(50.0, 0.0)]

    def fake_parse(text):
        return impedances, [pattern]

    captured = {}

    def fake_fb(pat, peak=None):
        captured["called"] = True
        captured["peak"] = peak or max(pat, key=lambda t: t[2])
        return 42.42                # arbitrary sentinel value

    with patch.object(v2_runner, "parse_nec_output", side_effect=fake_parse), \
         patch.object(v2_runner, "build_nec_card", return_value="dummy"), \
         patch("subprocess.run"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=""), \
         patch.object(v2_runner.fb, "front_to_back", side_effect=fake_fb):
        m = v2_runner.evaluate(
            [{"name": "REF", "position_in": 0, "length_in": 220.0},
             {"name": "DE",  "position_in": 50, "length_in": 215.0}],
            rules={"global": {"freq_mhz_center": 27.195,
                              "freq_mhz_low": 27.0, "freq_mhz_high": 27.4}},
            height_ft=22.0,
        )

    assert captured.get("called"), "v2_runner.evaluate must call hyagi.fb.front_to_back"
    assert m.get("fb_db") == 42.42, (
        f"v2_runner.evaluate must return the value hyagi.fb returned, "
        f"got {m.get('fb_db')}"
    )


def test_perf_report_analyze_calls_hyagi_fb():
    """Same regression as above but for the Report code path -- if these two
    tests pass, the matcher's Result panel and the Report MUST show the same
    F/B for the same antenna because both go through the same function."""
    pattern = [(80.0, 0.0, 15.0), (80.0, 180.0, -5.0)]
    captured = {"fb": None, "fr": None}

    def fake_fb(pat, peak=None):
        captured["fb"] = (id(pat), peak)
        return 33.33

    def fake_fr(pat, peak=None):
        captured["fr"] = (id(pat), peak)
        return 44.44

    with patch.object(perf_report, "_solve",
                      return_value=([(50.0, 0.0)], pattern, "")), \
         patch.object(perf_report.v2_runner, "build_nec_card", return_value="dummy"), \
         patch.object(perf_report.v2_runner, "band_swr_curve",
                      return_value=([(27.195, 50.0, 0.0, 1.0)], 1.0, 1.0)), \
         patch.object(perf_report, "_free_space_card", return_value="dummy_fs"), \
         patch.object(perf_report, "_EFF_RE",
                      type("M", (), {"search": staticmethod(lambda t: None)})()), \
         patch.object(perf_report.fb, "front_to_back", side_effect=fake_fb), \
         patch.object(perf_report.fb, "front_to_rear", side_effect=fake_fr):
        rep = perf_report.analyze(
            [{"name": "REF",  "position_in": 0,   "length_in": 220.0},
             {"name": "DE",   "position_in": 50,  "length_in": 215.0}],
            rules={"global": {"freq_mhz_center": 27.195,
                              "freq_mhz_low": 27.0, "freq_mhz_high": 27.4}},
            height_ft=22.0,
        )

    assert captured["fb"] is not None, "perf_report.analyze must call hyagi.fb.front_to_back"
    assert rep["fb_db"] == 33.33, (
        f"perf_report.analyze must return what hyagi.fb returned, "
        f"got {rep['fb_db']}"
    )
    assert rep["fr_db"] == 44.44


def test_both_callers_return_identical_fb_for_same_pattern():
    """End-to-end invariant: feed the SAME pattern through both callers
    (with everything else stubbed) and assert F/B is byte-identical."""
    pattern = [
        (80.0,   0.0, 15.0),    # forward peak
        (80.0, 180.0, -5.0),    # back point -> F/B = 20 textbook
        (30.0, 180.0,  9.0),    # high-angle ground backlobe (ignored)
    ]
    impedances = [(50.0, 0.0)]

    # v2_runner path.
    with patch.object(v2_runner, "parse_nec_output",
                      side_effect=lambda text: (impedances, [pattern])), \
         patch.object(v2_runner, "build_nec_card", return_value="dummy"), \
         patch("subprocess.run"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=""):
        m = v2_runner.evaluate(
            [{"name": "REF", "position_in": 0, "length_in": 220.0},
             {"name": "DE",  "position_in": 50, "length_in": 215.0}],
            rules={"global": {"freq_mhz_center": 27.195,
                              "freq_mhz_low": 27.0, "freq_mhz_high": 27.4}},
            height_ft=22.0,
        )
    fb_v2 = m["fb_db"]

    # perf_report path.
    with patch.object(perf_report, "_solve",
                      return_value=(impedances, pattern, "")), \
         patch.object(perf_report.v2_runner, "build_nec_card", return_value="dummy"), \
         patch.object(perf_report.v2_runner, "band_swr_curve",
                      return_value=([(27.195, 50.0, 0.0, 1.0)], 1.0, 1.0)), \
         patch.object(perf_report, "_free_space_card", return_value="dummy_fs"), \
         patch.object(perf_report, "_EFF_RE",
                      type("M", (), {"search": staticmethod(lambda t: None)})()):
        rep = perf_report.analyze(
            [{"name": "REF",  "position_in": 0,   "length_in": 220.0},
             {"name": "DE",   "position_in": 50,  "length_in": 215.0}],
            rules={"global": {"freq_mhz_center": 27.195,
                              "freq_mhz_low": 27.0, "freq_mhz_high": 27.4}},
            height_ft=22.0,
        )
    fb_rep = rep["fb_db"]

    assert abs(fb_v2 - fb_rep) < 1e-9, (
        f"F/B drifted between paths: v2_runner={fb_v2}, perf_report={fb_rep}"
    )
    # Both must report the textbook value (20 dB), not the high-angle 6 dB.
    assert abs(fb_v2 - 20.0) < 0.5, (
        f"Both paths agree but on the wrong value: {fb_v2:.2f} (expected ~20)"
    )
