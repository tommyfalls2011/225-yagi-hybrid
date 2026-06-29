"""Regression tests for the v2_runner physics math fixes:
- F/B is read from the centre-frequency pattern at the main-lobe elevation
  (not mixed across all frequencies / not over all elevations).
- centre R/X/SWR are read at the true operating centre frequency.
- parse_nec_output returns per-frequency pattern blocks.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hyagi import v2_runner as R  # noqa: E402

DATA = ROOT / "data"


def _geo_rules():
    return (json.load(open(DATA / "current_geometry_v2.json")),
            json.load(open(DATA / "rules_v2.json")))


def test_interp_rx_basic():
    freqs = [26.0, 27.0, 28.0]
    imps = [(20.0, -10.0), (50.0, 0.0), (80.0, 10.0)]
    r, x = R._interp_rx(freqs, imps, 27.0)
    assert abs(r - 50.0) < 1e-6 and abs(x - 0.0) < 1e-6
    r, x = R._interp_rx(freqs, imps, 27.5)
    assert abs(r - 65.0) < 1e-6 and abs(x - 5.0) < 1e-6  # midway 27->28


def test_parse_returns_blocks_per_frequency():
    geo, rules = _geo_rules()
    g = rules["global"]
    import subprocess, tempfile, os
    nec = R.build_nec_card(geo["elements"],
                           [g["freq_mhz_low"], g["freq_mhz_center"], g["freq_mhz_high"]],
                           height_ft=30.0, pattern=True)
    with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as f:
        f.write(nec); p = f.name
    o = p.replace(".nec", ".out")
    subprocess.run(["nec2c", "-i", p, "-o", o], capture_output=True, text=True, timeout=60)
    text = pathlib.Path(o).read_text()
    os.unlink(p); os.unlink(o)
    imps, blocks = R.parse_nec_output(text)
    assert len(imps) == 3, f"expected 3 impedance points, got {len(imps)}"
    assert len(blocks) == 3, f"expected 3 pattern blocks, got {len(blocks)}"
    assert all(len(b) > 0 for b in blocks), "every frequency must have a pattern block"


def test_fb_and_center_are_physical():
    geo, rules = _geo_rules()
    m5 = R.evaluate(geo["elements"], rules, n_points=5)
    m9 = R.evaluate(geo["elements"], rules, n_points=9)
    assert "error" not in m5 and "error" not in m9
    # gain/F-B come from the centre block now, so they must NOT drift with the
    # number of band sample points.
    assert abs(m5["gain_dbi"] - m9["gain_dbi"]) < 0.5
    assert abs(m5["fb_db"] - m9["fb_db"]) < 0.5
    # F/B for this forward-beaming array must be a sane positive figure, well
    # above the old all-elevation-mixed ~9.8 dB artefact.
    assert m5["fb_db"] > 14.0, f"F/B unexpectedly low: {m5['fb_db']}"
    # centre R/X read at true 27.195 -> ~60 ohm / +11 ohm for the seed geometry.
    assert 45.0 < m5["center_r"] < 75.0
    assert m5["center_swr"] < 1.5


def test_match_score_mode_prefers_lower_swr():
    """The 'match' score must reward the lowest worst-case band SWR, not trade
    it away to zero out reactance.  Regression for the run that picked
    SWR 1.430/X=0 over SWR 1.307/X=-2 and ended worse than it started."""
    higher_swr_zero_x = {"max_swr": 1.430, "center_x": 0.0}
    lower_swr_some_x = {"max_swr": 1.307, "center_x": -2.0}
    s_hi = R._score_for_mode("match", higher_swr_zero_x)
    s_lo = R._score_for_mode("match", lower_swr_some_x)
    assert s_lo > s_hi, "match mode must prefer the lower band SWR"
    # and X still breaks ties at equal SWR
    a = R._score_for_mode("match", {"max_swr": 1.20, "center_x": 0.0})
    b = R._score_for_mode("match", {"max_swr": 1.20, "center_x": 8.0})
    assert a > b
