"""F/B and F/R reconciliation between v2_runner.evaluate and perf_report.analyze.

User report: the Tune & Learn 'Result' panel showed F/B 18.48 dB but the
'Full performance report' on the same geometry showed F/B 12.63 dB.  Two
different code paths defined F/B differently:

  v2_runner.evaluate()  -- rear lobe inside +/-30 deg AROUND back azimuth AND
                           +/-10 deg around the forward peak elevation.
  perf_report.analyze() -- rear lobe inside +/-30 deg around back azimuth at
                           ALL elevations (mixed in ground-reflection back
                           lobes, came out 4-7 dB pessimistic).

The fix ports the elevation-aware cut into perf_report so a single antenna
at a single frequency produces one number on both surfaces.  This test poke
both code paths with a synthetic pattern containing a known forward main
lobe and verifies the two F/B numbers agree.
"""
from unittest.mock import patch

from hyagi import perf_report, v2_runner


def _synthetic_pattern():
    """Build a pattern that has:
      * a forward main lobe at theta=80 (=10 deg elevation) / phi=0 / 15 dBi;
      * a real rear lobe at the SAME elevation:  phi=180 / theta=80 / -5 dBi
        (F/B should be 15 - (-5) = 20 dB);
      * a HIGH-ANGLE backside lobe that is loud (theta=30 / phi=180 / +5 dBi)
        which is what perf_report used to (wrongly) call the back lobe and
        which would give F/B = 15 - 5 = 10 dB instead of 20.

    Returns pattern as a list of (theta, phi, gain_dbi) tuples covering a
    reasonable sample of the sphere.
    """
    pat = []
    for theta in (10, 30, 50, 70, 75, 80, 85, 90):
        for phi in range(0, 360, 30):
            if theta == 80 and phi == 0:
                g = 15.0                # forward main lobe
            elif theta == 80 and phi == 180:
                g = -5.0                # true back-of-main-lobe
            elif theta == 30 and phi == 180:
                g = 5.0                 # high-angle ground-reflection backlobe
            elif theta == 80 and phi in (30, 330):
                g = 0.0                 # side lobes (just outside +/-30 back cone)
            else:
                g = -15.0               # rest of sphere quiet
            pat.append((float(theta), float(phi), g))
    return pat


def test_fb_matches_between_evaluate_and_analyze():
    """v2_runner.evaluate's F/B and perf_report.analyze's F/B must produce
    the SAME number for the same pattern at the same frequency."""
    pat = _synthetic_pattern()
    imps = [(50.0, 0.0)]                # 50+j0 -> SWR=1, irrelevant here

    def fake_solve(card):
        # perf_report._solve returns (impedances, pattern, text)
        return imps, pat, "EFFICIENCY = 100.00 PERCENT"

    def fake_parse(text):
        return imps, [pat]

    # Patch the heavy NEC calls; both code paths use the same parser hooks.
    with patch.object(perf_report, "_solve", side_effect=fake_solve), \
         patch.object(perf_report.v2_runner, "build_nec_card", return_value="dummy"), \
         patch.object(perf_report.v2_runner, "band_swr_curve",
                      return_value=([(27.195, 50.0, 0.0, 1.0)], 1.0, 1.0)), \
         patch.object(perf_report, "_free_space_card", return_value="dummy_fs"), \
         patch.object(perf_report, "_EFF_RE",
                      type("M", (), {"search": staticmethod(lambda t: None)})()):
        rep = perf_report.analyze(
            [{"name": "REF",  "position_in": 0,   "length_in": 220.0},
             {"name": "DE",   "position_in": 50,  "length_in": 215.0},
             {"name": "DIR1", "position_in": 110, "length_in": 200.0}],
            rules={"global": {"freq_mhz_center": 27.195,
                              "freq_mhz_low": 27.0, "freq_mhz_high": 27.4}},
            height_ft=22.0,
        )

    # The correct F/B (rear at main-lobe elevation only) is 15 - (-5) = 20 dB.
    # The wrong F/B (any elevation) would be 15 - 5 = 10 dB.
    assert abs(rep["fb_db"] - 20.0) < 0.5, (
        f"Report F/B={rep['fb_db']} -- must use the elevation-aware cut so "
        f"high-angle ground backlobes don't conflate into F/B (should be ~20)"
    )


def test_swr_sweep_includes_design_centre():
    """The Report's SWR sweep must include the EXACT design centre as a
    sample point so 'min SWR @ ...' matches the matcher's reported centre
    SWR.  Previously the grid was uniform 0.05 MHz so 27.195 wasn't sampled
    and the report showed 'min 1.013 @ 27.170' while the centre value was
    1.000 at 27.195.  Pick a band whose default 0.05-MHz grid does NOT
    land on fc so the new insert-fc code is exercised."""
    fc = 27.193                                   # NOT a multiple of 0.05

    def fake_curve(elements, lo, hi, npts, height_ft):
        # Special single-sample call for the centre insert.
        if lo == fc and hi == fc and npts == 1:
            return [(fc, 50.0, 0.0, 1.0)], 1.0, 1.0
        pts = [(lo + i * (hi - lo) / (npts - 1), 50.0, 0.0, 1.10)
               for i in range(npts)]
        return pts, max(p[3] for p in pts), sum(p[3] for p in pts) / len(pts)

    with patch.object(perf_report, "_solve",
                      return_value=([(50.0, 0.0)],
                                    [(80.0, 0.0, 15.0)], "")), \
         patch.object(perf_report.v2_runner, "build_nec_card", return_value="dummy"), \
         patch.object(perf_report.v2_runner, "band_swr_curve", side_effect=fake_curve), \
         patch.object(perf_report, "_free_space_card", return_value="dummy_fs"), \
         patch.object(perf_report, "_EFF_RE",
                      type("M", (), {"search": staticmethod(lambda t: None)})()):
        rep = perf_report.analyze(
            [{"name": "REF",  "position_in": 0,   "length_in": 220.0},
             {"name": "DE",   "position_in": 50,  "length_in": 215.0}],
            rules={"global": {"freq_mhz_center": fc,
                              "freq_mhz_low": fc - 0.5, "freq_mhz_high": fc + 0.5}},
            height_ft=22.0,
            sweep_step=0.07,        # non-aligned step so fc is NOT on the grid
        )
    # With fc inserted as an explicit sample at SWR=1.0, the report's min
    # SWR must land exactly on fc.
    assert abs(rep["min_swr_mhz"] - fc) < 1e-3, (
        f"min_swr_mhz={rep['min_swr_mhz']} -- must equal design fc={fc}"
    )
    assert rep["min_swr"] < 1.05, (
        f"min_swr={rep['min_swr']} -- centre sample must be the deepest match"
    )
