"""Tests for horizon-gain + sky-bouncer rules."""
import sys, importlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def _reload():
    import hyagi.cell_rules as cr
    importlib.reload(cr)
    return cr

def test_sky_bouncer_rejected():
    cr = _reload()
    msg = cr.violates_pattern({"peak_elev_deg": 30.0, "front_back_db": 20.0,
                               "horizon_gain_dbi": 8.0, "horizon_rear_gain_dbi": -12.0})
    assert msg and "sky-bouncer" in msg

def test_horizon_design_accepted():
    cr = _reload()
    res = {"peak_elev_deg": 5.0, "front_back_db": 18.0,
           "horizon_gain_dbi": 12.5, "horizon_rear_gain_dbi": -5.0}
    assert cr.violates_pattern(res) is None

def test_horizon_reversed_rejected():
    cr = _reload()
    msg = cr.violates_pattern({"peak_elev_deg": 6.0, "front_back_db": 12.0,
                               "horizon_gain_dbi": 4.0, "horizon_rear_gain_dbi": 8.0})
    assert msg and "horizon" in msg.lower()

def test_negative_fb_rejected():
    cr = _reload()
    msg = cr.violates_pattern({"peak_elev_deg": 6.0, "front_back_db": -2.0})
    assert msg and "reversed" in msg.lower()

def test_no_fields_means_no_judgement():
    cr = _reload()
    assert cr.violates_pattern({}) is None

def test_sentinel_rear_gain_does_not_trigger_reversal():
    cr = _reload()
    res = {"peak_elev_deg": 5.0, "horizon_gain_dbi": 12.0,
           "horizon_rear_gain_dbi": -999.0, "forward_gain_dbi": 12.0,
           "rear_gain_dbi": -999.0}
    assert cr.violates_pattern(res) is None
