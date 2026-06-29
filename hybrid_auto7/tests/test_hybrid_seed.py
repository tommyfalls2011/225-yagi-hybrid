"""Tests for hybrid_seed.build_geometry boom-lock + extended-N behaviour."""
import pytest

from hyagi import hybrid_seed


def _span(geo):
    ps = [float(e["position_in"]) for e in geo["elements"]]
    return max(ps) - min(ps)


def _last_pos(geo):
    return max(float(e["position_in"]) for e in geo["elements"])


@pytest.mark.parametrize("n", [0, 1, 3, 7, 12, 14, 18])
def test_supports_directors_up_to_18(n):
    geo = hybrid_seed.build_geometry(n)
    names = [e["name"] for e in geo["elements"]]
    assert names[:4] == ["REF", "XFRMR", "DE", "COUPLER"]
    assert sum(1 for x in names if x.startswith("DIR")) == n
    # Always 4 + N total -- catches off-by-one in the loop.
    assert len(geo["elements"]) == 4 + n


def test_clamps_n_above_18():
    geo = hybrid_seed.build_geometry(99)
    n = sum(1 for e in geo["elements"] if str(e["name"]).startswith("DIR"))
    assert n == 18


def test_boom_lock_compresses_last_director_to_fit():
    """With max_boom_in supplied, the last DIRn must sit EXACTLY at the cap
    (REF stays at 0).  New spec: boom is an exact length, not just a cap."""
    geo = hybrid_seed.build_geometry(7, max_boom_in=22 * 12.0)  # 22 ft = 264"
    assert abs(_last_pos(geo) - 264.0) < 0.5, (
        f"last DIR must land EXACTLY at the cap, got {_last_pos(geo)}"
    )
    # REF must stay at exactly 0.
    ref = next(e for e in geo["elements"] if e["name"] == "REF")
    assert abs(float(ref["position_in"])) < 0.01


def test_boom_lock_grows_short_geometry_to_exact_length():
    """If the seeder's natural directors land SHORTER than the cap, the
    geometry is STRETCHED so the last director sits exactly at the cap.
    Boom is an exact length, not a cap."""
    # 1 director at 27.195 MHz with a generous 30 ft cap -> needs stretching.
    geo = hybrid_seed.build_geometry(1, max_boom_in=30 * 12.0)
    assert abs(_last_pos(geo) - 360.0) < 0.5, (
        f"last DIR must be at exactly 360 in (30 ft) cap, got {_last_pos(geo)}"
    )


def test_director_lengths_unaffected_by_boom_lock():
    """Boom lock changes spacings only -- element LENGTHS are tuned later,
    not at seed time, so they must come back identical lock-vs-unlocked."""
    a = hybrid_seed.build_geometry(7)
    b = hybrid_seed.build_geometry(7, max_boom_in=22 * 12.0)
    a_lens = {e["name"]: e["length_in"] for e in a["elements"]}
    b_lens = {e["name"]: e["length_in"] for e in b["elements"]}
    assert a_lens == b_lens
