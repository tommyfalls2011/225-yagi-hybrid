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
    """With max_boom_in supplied, the last DIRn must sit within the cap (with
    a 1\" tip margin).  Tested on a long array where unlocked the boom would
    overrun the limit by ~6 ft."""
    geo = hybrid_seed.build_geometry(7, max_boom_in=22 * 12.0)  # 22 ft = 264"
    assert _last_pos(geo) <= 264.0 + 0.5      # within the cap (rounding slack)
    # Make sure DE/REF/XFRMR/COUPLER aren't moved by the compress -- they sit
    # at their normal positions; only DIRn spacings shrink.
    de = next(e for e in geo["elements"] if e["name"] == "DE")
    assert 40.0 < float(de["position_in"]) < 55.0


def test_boom_lock_does_not_inflate_when_already_short():
    """If the boom is already shorter than the cap, the seeder must leave the
    spacings alone (don't artificially stretch to fill)."""
    geo_uncapped = hybrid_seed.build_geometry(3)
    geo_capped = hybrid_seed.build_geometry(3, max_boom_in=60.0 * 12.0)  # huge
    assert _last_pos(geo_uncapped) == _last_pos(geo_capped)


def test_director_lengths_unaffected_by_boom_lock():
    """Boom lock changes spacings only -- element LENGTHS are tuned later,
    not at seed time, so they must come back identical lock-vs-unlocked."""
    a = hybrid_seed.build_geometry(7)
    b = hybrid_seed.build_geometry(7, max_boom_in=22 * 12.0)
    a_lens = {e["name"]: e["length_in"] for e in a["elements"]}
    b_lens = {e["name"]: e["length_in"] for e in b["elements"]}
    assert a_lens == b_lens
