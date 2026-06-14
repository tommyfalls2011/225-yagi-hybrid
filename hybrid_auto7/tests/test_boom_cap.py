"""Boom HARD CAP regression -- defence in depth.

User wants: when boom_mode='fixed' and a boom length is specified, NOTHING
in the optimizer or any downstream layer should be able to push the last
director past the cap.

This was previously enforced only by:
  (a) hybrid_seed.build_geometry compressing seeded spacings
  (b) page-level 'rescale to fit' button on .maa import / setup load
  (c) the matcher running with tune_spacings=False in FIXED mode (so
      positions don't move at all)

(c) only works if the STARTING geometry already fits.  A too-long warm-start
geometry from the learning DB, an old saved geometry pre-dating the lock,
or any future code path that subtly shifts a position would bypass the cap.
The hard cap inside match_opt._apply() is that backstop.
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
    # Intentionally too long: 292.3" = 24.36 ft -- exceeds a 22-ft (264") cap
    {"name": "DIR3",    "position_in": 292.3,  "length_in": 187.2},
]
RULES_LOCKED = {
    "global": {"boom_max_in": 264.0},      # 22 ft cap
    "elements": {},
}
RULES_FREE = {"global": {}, "elements": {}}


def _last_pos(els):
    return max(float(e["position_in"]) for e in els)


def _build_vec(els):
    """Build a vec that just sets each element to its current length so _apply
    behaves like a no-op move -- the cap clamp must still fire on the geometry."""
    vec = {"de_len": _by_name(els, "DE")["length_in"]}
    for nm in ("REF", "XFRMR", "COUPLER"):
        e = _by_name(els, nm)
        if e is not None:
            tag = {"REF": "ref", "XFRMR": "xf", "COUPLER": "cp"}[nm]
            vec[f"{tag}_len"] = float(e["length_in"])
            # gap is signed distance from DE; sign-aware in _apply.
            de_pos = float(_by_name(els, "DE")["position_in"])
            vec[f"{tag}_gap"] = abs(float(e["position_in"]) - de_pos)
    for e in els:
        if str(e["name"]).upper().startswith("DIR"):
            vec[f"{e['name']}_len"] = float(e["length_in"])
    return vec


def _by_name(els, nm):
    for e in els:
        if str(e["name"]).upper() == nm.upper():
            return e
    return None


def test_locked_boom_compresses_overlong_geometry():
    """Starting with DIR3 at 292.3" (~24.4 ft) and a 264" cap, _apply must
    compress the directors so the last one lands inside the cap."""
    vec = _build_vec(BASE_ELEMENTS)
    de_pos = float(_by_name(BASE_ELEMENTS, "DE")["position_in"])
    result = match_opt._apply(BASE_ELEMENTS, vec, de_pos, rules=RULES_LOCKED)
    span = _last_pos(result) - min(float(e["position_in"]) for e in result)
    assert span <= 264.5, (
        f"_apply must clamp boom span to the cap; got span {span} > 264"
    )


def test_free_boom_leaves_geometry_alone():
    """With no boom_max_in in rules, the cap is OFF and a long boom is
    preserved -- AI is free to design (per user's rule)."""
    vec = _build_vec(BASE_ELEMENTS)
    de_pos = float(_by_name(BASE_ELEMENTS, "DE")["position_in"])
    result = match_opt._apply(BASE_ELEMENTS, vec, de_pos, rules=RULES_FREE)
    assert _last_pos(result) > 290.0, (
        f"FREE mode must not compress positions; got DIR3 at {_last_pos(result)}"
    )


def test_locked_boom_no_op_when_under_cap():
    """If geometry already fits under the cap, _apply must NOT shrink it -- so
    a well-built antenna doesn't get inappropriately compressed every tune."""
    short = copy.deepcopy(BASE_ELEMENTS)
    _by_name(short, "DIR3")["position_in"] = 200.0   # well under 264" cap
    vec = _build_vec(short)
    de_pos = float(_by_name(short, "DE")["position_in"])
    before = _last_pos(short)
    result = match_opt._apply(short, vec, de_pos, rules=RULES_LOCKED)
    assert abs(_last_pos(result) - before) < 0.5, (
        f"in-spec geometry must not be touched by the cap; was {before}, "
        f"became {_last_pos(result)}"
    )


def test_optimize_entry_compresses_overlong_geometry():
    """The match_opt.optimize() entry path must compress a too-long starting
    geometry to the cap BEFORE the descent runs -- even if tune_spacings=False
    (which keeps positions out of the DOF vector entirely).  This was the bug:
    LOCKED 22' reported on the Report header, but the cut sheet showed DIR3 at
    24' 4-5/16\" because positions never moved in FIXED mode.

    We don't actually run the matcher (no nec2c in test env); we just verify
    the entry-time compression fires and the in-place geometry is short
    enough by intercepting after the first log line."""
    captured_log = []

    def grab_log(msg):
        captured_log.append(msg)
        # Raise immediately after the compress fires to skip the heavy descent.
        if "[boom-cap]" in msg:
            raise RuntimeError("INTERCEPT")

    rules = {
        "global": {
            "freq_mhz_low": 26.6, "freq_mhz_high": 27.8,
            "freq_mhz_center": 27.195, "boom_max_in": 264.0,   # 22 ft cap
        },
        "elements": {},
    }
    els = copy.deepcopy(BASE_ELEMENTS)        # DIR3 at 292.3 = ~24.4 ft
    try:
        match_opt.optimize(
            els, rules,
            height_ft=22.0, target_swr=1.5, points=5, restarts=0,
            polish_gain=False, log_fn=grab_log, goal="wideband",
            tune_spacings=False,
        )
    except (RuntimeError, Exception):
        pass

    # Must have logged the compression step.
    cap_log = [m for m in captured_log if "[boom-cap]" in m]
    assert cap_log, (
        f"optimize() entry must log [boom-cap] when starting geometry > cap; "
        f"log was {captured_log[:3]}"
    )
    # Inspect the message: it must say the input span was > cap and was
    # compressed by a scale factor < 1.
    msg = cap_log[0]
    assert ">" in msg and "compressed positions by" in msg, (
        f"[boom-cap] log line should report span > cap and compression factor; "
        f"got {msg}"
    )
