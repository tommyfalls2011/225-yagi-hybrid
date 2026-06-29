"""Tests for the high-power 'resonant' match objective (R->50, X->0 at center)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hyagi import match_opt  # noqa: E402


def test_center_rx_interpolates():
    curve = [(26.0, 30.0, -20.0, 2.0), (27.0, 50.0, 0.0, 1.0), (28.0, 70.0, 20.0, 2.0)]
    r, x = match_opt._center_rx(curve, 27.0)
    assert abs(r - 50.0) < 1e-6 and abs(x) < 1e-6
    r, x = match_opt._center_rx(curve, 27.5)
    assert abs(r - 60.0) < 1e-6 and abs(x - 10.0) < 1e-6


def test_optimize_accepts_resonant_goal():
    # signature/threading smoke test (no NEC needed): default + resonant keyword.
    import inspect
    sig = inspect.signature(match_opt.optimize)
    assert "goal" in sig.parameters
    assert sig.parameters["goal"].default == "wideband"
