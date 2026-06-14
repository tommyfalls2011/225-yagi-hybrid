"""Pure-Python rationale generator tests (no Streamlit needed)."""
from hyagi import process_log as plg


# ---- parse_dof -----------------------------------------------------------

def test_parse_len_dof():
    assert plg.parse_dof("DE_len") == ("DE", "len")
    assert plg.parse_dof("XFRMR_len") == ("XFRMR", "len")
    assert plg.parse_dof("DIR3_len") == ("DIR3", "len")


def test_parse_gap_dof():
    assert plg.parse_dof("XFRMR_gap") == ("XFRMR", "gap")
    assert plg.parse_dof("COUPLER_gap") == ("COUPLER", "gap")


def test_parse_unknown_dof():
    name, kind = plg.parse_dof("something_weird")
    assert name == "SOMETHING_WEIRD"
    assert kind == "param"


# ---- element_role -------------------------------------------------------

def test_element_role_known():
    assert "Driven element" in plg.element_role("DE")
    assert "Reflector" in plg.element_role("REF")
    assert "transformer" in plg.element_role("XFRMR")
    assert "Coupler" in plg.element_role("COUPLER")


def test_element_role_director_numbers():
    assert "Director #1" in plg.element_role("DIR1")
    assert "Director #4" in plg.element_role("DIR4")


def test_element_role_unknown():
    assert "antenna element" in plg.element_role("FOO")


# ---- direction_label ---------------------------------------------------

def test_direction_label_initial():
    assert "(initial sample)" in plg.direction_label(None, 215.0, "len")


def test_direction_label_longer_shorter():
    assert "longer" in plg.direction_label(215.0, 216.0, "len")
    assert "shorter" in plg.direction_label(215.0, 214.0, "len")


def test_direction_label_unchanged():
    assert "unchanged" in plg.direction_label(215.0, 215.0, "len")


def test_direction_label_gap():
    assert "further from DE" in plg.direction_label(30.0, 31.0, "gap")
    assert "closer to DE" in plg.direction_label(30.0, 29.0, "gap")


# ---- physics_prediction ------------------------------------------------

def test_physics_de_longer_lowers_freq():
    text = plg.physics_prediction("DE", "len", direction=+0.5).lower()
    assert "down" in text
    assert "inductive" in text


def test_physics_de_shorter_raises_freq():
    text = plg.physics_prediction("DE", "len", direction=-0.5).lower()
    assert "up" in text
    assert "capacitive" in text


def test_physics_dir_change_describes_beam():
    text = plg.physics_prediction("DIR2", "len", direction=+0.5).lower()
    assert "beam" in text or "bandwidth" in text


def test_physics_gap_directions():
    closer = plg.physics_prediction("XFRMR", "gap", direction=-0.5).lower()
    further = plg.physics_prediction("XFRMR", "gap", direction=+0.5).lower()
    assert "closer" in closer or "stronger" in closer or "drops" in closer
    assert "further" in further or "reduces" in further


def test_physics_unknown_direction_returns_empty():
    assert plg.physics_prediction("DE", "len", direction=0.0) == ""
    assert plg.physics_prediction("DE", "len", direction=None) == ""


# ---- accept_reason -----------------------------------------------------

def test_accept_reason_kept_improved_swr():
    msg = plg.accept_reason(True, prev_swr=1.50, new_swr=1.30)
    assert "Kept" in msg and "1.500" in msg and "1.300" in msg


def test_accept_reason_kept_objective_only():
    """Accepted but band-max wasn't the metric that improved (centre / RL /
    X / F/B did) -- generic 'objective improved' phrasing."""
    msg = plg.accept_reason(True, prev_swr=1.30, new_swr=1.31)
    assert "Kept" in msg and "objective" in msg


def test_accept_reason_rejected_worse_swr():
    msg = plg.accept_reason(False, prev_swr=1.30, new_swr=1.55)
    assert "Rejected" in msg and "1.300" in msg and "1.550" in msg


def test_accept_reason_rejected_constraint():
    """Rejected even though SWR didn't get explicitly worse -- the priority
    ladder (|X|<=2.5, RL, F/B floor) rejected it."""
    msg = plg.accept_reason(False, prev_swr=1.30, new_swr=1.30)
    assert "Rejected" in msg
    assert "X" in msg or "F/B" in msg or "centre" in msg
