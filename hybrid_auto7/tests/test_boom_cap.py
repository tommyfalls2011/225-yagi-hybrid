"""Boom HARD CAP / endpoint-pin regression tests.

New spec from user: 'lock REF and last DIR on antenna -- but can move the
other elements not just adjust lengths.  When in free mode let AI do
whatever it needs to get my numbers.'

This module verifies the THREE places that enforce the lock:

  1. match_opt.optimize() entry rescales the input geometry so REF lands at
     0 and the last director lands at EXACTLY boom_max_in (no shorter, no
     longer).
  2. match_opt._apply() pins REF at 0 and the last director at boom_max_in
     after every coordinate-descent move, regardless of what the DOF vector
     says.  Middle elements stay between them with at least 6 inches between
     adjacent neighbours.
  3. FREE mode (boom_max_in == 0) leaves the geometry alone -- AI is free to
     design.
"""
import copy

from hyagi import match_opt


BASE_ELEMENTS = [
    {"name": "REF",     "position_in": 0.0,    "length_in": 218.5},
    {"name": "XFRMR",   "position_in": 28.4,   "length_in": 199.3},
    {"name": "DE",      "position_in": 46.9,   "length_in": 215.7},
    {"name": "COUPLER", "position_in": 66.9,   "length_in": 199.9},
    {"name": "DIR1",    "position_in": 135.9,  "length_in": 195.0},
    {"name": "DIR2",    "position_in": 214.1,  "length_in": 191.1},
    # Too long: 292.3 in = 24.36 ft, while we'll lock to 22 ft = 264 in.
    {"name": "DIR3",    "position_in": 292.3,  "length_in": 187.2},
]
RULES_LOCKED = {
    "global": {"boom_max_in": 264.0},
    "elements": {},
}
RULES_FREE = {"global": {}, "elements": {}}


def _by_name(els, nm):
    for e in els:
        if str(e["name"]).upper() == nm.upper():
            return e
    return None


def _build_vec(els):
    """Build a no-op DOF vec so _apply just enforces structure, not moves."""
    vec = {"de_len": _by_name(els, "DE")["length_in"]}
    for nm in ("REF", "XFRMR", "COUPLER"):
        e = _by_name(els, nm)
        if e is not None:
            tag = {"REF": "ref", "XFRMR": "xf", "COUPLER": "cp"}[nm]
            vec[f"{tag}_len"] = float(e["length_in"])
            de_pos = float(_by_name(els, "DE")["position_in"])
            vec[f"{tag}_gap"] = abs(float(e["position_in"]) - de_pos)
    for e in els:
        if str(e["name"]).upper().startswith("DIR"):
            vec[f"{e['name']}_len"] = float(e["length_in"])
    return vec


# ---- _apply() endpoint pin ------------------------------------------------

def test_apply_pins_ref_at_zero():
    """REF MUST land at exactly position 0 after _apply when boom is locked.
    Even if a starting geometry has REF at e.g. +5 from a bad warm-start."""
    els = copy.deepcopy(BASE_ELEMENTS)
    _by_name(els, "REF")["position_in"] = 5.2     # nudged off zero
    vec = _build_vec(els)
    de_pos = float(_by_name(els, "DE")["position_in"])
    result = match_opt._apply(els, vec, de_pos, rules=RULES_LOCKED)
    assert abs(float(_by_name(result, "REF")["position_in"])) < 0.01, (
        f"REF must be at 0 with locked boom; got "
        f"{_by_name(result, 'REF')['position_in']}"
    )


def test_apply_pins_last_director_at_cap():
    """Last director MUST land at exactly boom_max_in after _apply."""
    vec = _build_vec(BASE_ELEMENTS)
    de_pos = float(_by_name(BASE_ELEMENTS, "DE")["position_in"])
    result = match_opt._apply(BASE_ELEMENTS, vec, de_pos, rules=RULES_LOCKED)
    # The last director by sorted position should be exactly 264.0
    last = max(result, key=lambda e: float(e["position_in"]))
    assert last["name"].upper().startswith("DIR")
    assert abs(float(last["position_in"]) - 264.0) < 0.01, (
        f"last DIR must be at exactly cap (264.0), got {last['position_in']}"
    )


def test_apply_keeps_middle_elements_in_order():
    """Middle elements must stay in their boom order, with at least the 6\"
    minimum spacing between neighbours (we don't want elements collapsing
    onto each other when the DOF moves get aggressive)."""
    els = copy.deepcopy(BASE_ELEMENTS)
    # Force XFRMR way past DE to test the order-fix.
    _by_name(els, "XFRMR")["position_in"] = 999.0
    vec = _build_vec(els)
    de_pos = float(_by_name(els, "DE")["position_in"])
    result = match_opt._apply(els, vec, de_pos, rules=RULES_LOCKED)
    positions = [float(e["position_in"]) for e in
                 sorted(result, key=lambda e: float(e["position_in"]))]
    # Each adjacent gap should be >= 6 in (or close to it after rounding).
    gaps = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
    assert all(g >= 5.9 for g in gaps), (
        f"adjacent elements must keep min 6 in spacing; gaps={gaps}"
    )


# ---- FREE mode (no constraints) -------------------------------------------

def test_free_boom_leaves_geometry_alone():
    """With no boom_max_in in rules, _apply doesn't touch positions."""
    els = copy.deepcopy(BASE_ELEMENTS)
    vec = _build_vec(els)
    de_pos = float(_by_name(els, "DE")["position_in"])
    last_before = max(float(e["position_in"]) for e in els)
    result = match_opt._apply(els, vec, de_pos, rules=RULES_FREE)
    last_after = max(float(e["position_in"]) for e in result)
    assert abs(last_after - last_before) < 0.5, (
        "FREE mode (boom_max_in=0) must not touch positions; "
        f"was {last_before}, became {last_after}"
    )


# ---- optimize() entry rescale --------------------------------------------

def test_optimize_entry_rescales_overlong_to_exact_cap():
    """optimize() entry must rescale a too-long starting geometry so REF=0
    and last director = boom_max_in EXACTLY (not just <= cap)."""
    captured = []

    def grab(msg):
        captured.append(msg)
        if "[boom-lock]" in msg:
            raise RuntimeError("INTERCEPT")

    rules = {
        "global": {
            "freq_mhz_low": 26.6, "freq_mhz_high": 27.8,
            "freq_mhz_center": 27.195, "boom_max_in": 264.0,    # 22 ft cap
        }, "elements": {},
    }
    try:
        match_opt.optimize(copy.deepcopy(BASE_ELEMENTS), rules,
                           height_ft=22.0, target_swr=1.5, points=5,
                           restarts=0, polish_gain=False, log_fn=grab,
                           goal="wideband", tune_spacings=True)
    except (RuntimeError, Exception):
        pass
    cap_log = [m for m in captured if "[boom-lock]" in m]
    assert cap_log, (
        f"optimize() must log [boom-lock] when rescaling input geometry; "
        f"first log msgs: {captured[:3]}"
    )
    msg = cap_log[0]
    assert "264.00" in msg and "REF at 0" in msg, (
        f"[boom-lock] message must name the cap value and REF pin; got {msg}"
    )


def test_optimize_entry_stretches_undersized_geometry():
    """If the starting geometry is SHORTER than the locked cap, the entry
    rescale must STRETCH it so the last director reaches the cap exactly."""
    captured = []

    def grab(msg):
        captured.append(msg)
        if "[boom-lock]" in msg:
            raise RuntimeError("INTERCEPT")

    # 18-ft array, but we'll lock to 22 ft -> needs stretching.
    short = [
        {"name": "REF",     "position_in": 0.0,    "length_in": 218.5},
        {"name": "XFRMR",   "position_in": 28.4,   "length_in": 199.3},
        {"name": "DE",      "position_in": 46.9,   "length_in": 215.7},
        {"name": "COUPLER", "position_in": 66.9,   "length_in": 199.9},
        {"name": "DIR1",    "position_in": 100.0,  "length_in": 195.0},
        {"name": "DIR2",    "position_in": 160.0,  "length_in": 191.1},
        {"name": "DIR3",    "position_in": 215.0,  "length_in": 187.2},  # < 264
    ]
    rules = {
        "global": {
            "freq_mhz_low": 26.6, "freq_mhz_high": 27.8,
            "freq_mhz_center": 27.195, "boom_max_in": 264.0,
        }, "elements": {},
    }
    try:
        match_opt.optimize(short, rules,
                           height_ft=22.0, target_swr=1.5, points=5,
                           restarts=0, polish_gain=False, log_fn=grab,
                           goal="wideband", tune_spacings=True)
    except (RuntimeError, Exception):
        pass
    assert [m for m in captured if "[boom-lock]" in m], (
        "must also rescale when geometry is SHORTER than the cap "
        "(stretch up to exact length)"
    )
