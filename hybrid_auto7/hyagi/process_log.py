"""Pure-Python rationale generator for tuning-move logs.

Used by pages/7_Process_Log.py to translate each row of `learned_moves`
into bench-day English: which element was changed, in which direction, what
the physics prediction was, and (after seeing the result SWR) why the matcher
either accepted or rejected the move.

Kept here -- not in the Streamlit page -- so it's unit-testable without
having to launch the app.  See tests/test_process_log_rationale.py.
"""
from __future__ import annotations

from typing import Optional, Tuple


# Plain-English role of each element in a hybrid array.  Used for the header
# line on each move card so the user sees WHAT the element actually does
# before the optimizer's reason is spelled out.
ELEMENT_ROLES = {
    "REF": "Reflector -- LONGEST element, sits behind the DE; acts as the rear "
           "mirror that bounces RF forward and shapes front-to-back.",
    "XFRMR": "Match transformer -- closely-coupled parasitic resonator in the "
             "driven cell; tunes the centre R/X and gives the upper band edge "
             "its SWR null.",
    "DE":  "Driven element -- the one that's actually FED.  Its resonant length "
           "sets the centre frequency: longer = lower fc, shorter = higher fc.",
    "COUPLER": "Coupler -- the second close-coupled parasitic resonator next to "
               "the DE; works with XFRMR to flatten SWR across a wide band.",
}
DIR_ROLE_TEMPLATE = (
    "Director #{n} -- in front of the DE; helps focus the forward gain. "
    "Each director resonates slightly above the DE; longer pushes its "
    "resonance lower which can move the array's pattern peak."
)


def element_role(name: str) -> str:
    n = (name or "").upper()
    if n.startswith("DIR"):
        try:
            return DIR_ROLE_TEMPLATE.format(n=int(n[3:]))
        except ValueError:
            return DIR_ROLE_TEMPLATE.format(n="?")
    return ELEMENT_ROLES.get(n, f"{name} -- antenna element.")


def parse_dof(dof: str) -> Tuple[str, str]:
    """Return (element_name_upper, kind) where kind is 'len', 'gap', or 'param'.

    Accepts the wire formats used by match_opt: 'DE_len', 'XFRMR_gap',
    'DIR3_len', etc.  Also handles older 'delen' rows (no underscore) just
    in case very old learning DBs are loaded."""
    d = str(dof or "")
    if d.endswith("_len"):
        return d[:-4].upper(), "len"
    if d.endswith("_gap"):
        return d[:-4].upper(), "gap"
    if d.endswith("len"):
        return d[:-3].upper(), "len"
    return d.upper(), "param"


def direction_label(prev: Optional[float], new: float, kind: str) -> str:
    if prev is None:
        return "(initial sample)"
    delta = float(new) - float(prev)
    if abs(delta) < 1e-6:
        return "(unchanged)"
    if kind == "len":
        return f"{delta:+.3f} in ({'longer' if delta > 0 else 'shorter'})"
    if kind == "gap":
        return f"{delta:+.3f} in ({'further from DE' if delta > 0 else 'closer to DE'})"
    return f"{delta:+.3f}"


def physics_prediction(name: str, kind: str, direction: float) -> str:
    """Plain-English prediction of WHY the matcher would TRY this move.

    `direction` is the (new - prev) delta of the parameter; sign matters.
    Returns '' when there's no meaningful direction (initial sample, etc.).
    """
    if direction == 0.0 or direction is None:
        return ""
    nm = (name or "").upper()
    longer = direction > 0
    if kind == "len":
        if nm == "DE":
            return ("Lengthening the DE shifts the antenna's centre resonance DOWN in "
                    "frequency (reactance goes more inductive)."
                    if longer else
                    "Shortening the DE shifts the centre resonance UP in frequency "
                    "(reactance goes more capacitive).")
        if nm == "REF":
            return ("A longer reflector resonates lower; this typically tightens the "
                    "rear pattern (better F/B) at the cost of a small drop in forward "
                    "gain and a shift of the band-low edge."
                    if longer else
                    "A shorter reflector resonates higher; usually trades a bit of F/B "
                    "for a small gain bump and shifts the resonance up.")
        if nm == "XFRMR":
            return ("A longer XFRMR moves its parasitic resonance DOWN toward the DE "
                    "centre -- gives the centre R a bigger push, can widen the band "
                    "but may reintroduce centre reactance."
                    if longer else
                    "A shorter XFRMR resonates HIGHER above the DE -- staggers the "
                    "match further up the band, often pulls the upper edge SWR down.")
        if nm == "COUPLER":
            return ("A longer COUPLER moves its parasitic resonance closer to the DE "
                    "centre -- shifts the matched bandwidth lower."
                    if longer else
                    "A shorter COUPLER pushes its resonance further above the DE -- "
                    "extends the high-side OWA bandwidth.")
        if nm.startswith("DIR"):
            return ("Lengthening this director lowers its resonance -- can sharpen the "
                    "forward beam (a little more gain) but at the cost of bandwidth "
                    "and centre R."
                    if longer else
                    "Shortening this director raises its resonance -- typically widens "
                    "the bandwidth but can flatten the forward gain.")
    if kind == "gap":
        if longer:
            return (f"Moving {nm} further from the DE reduces its mutual coupling -- "
                    "centre R goes up, reactance shifts inductive, beam pattern softens.")
        return (f"Pulling {nm} closer to the DE increases mutual coupling -- "
                "centre R drops, reactance goes capacitive, pattern tightens.")
    return ""


def accept_reason(accepted: bool, prev_swr: Optional[float],
                  new_swr: Optional[float]) -> str:
    if accepted:
        if (prev_swr is not None and new_swr is not None
                and new_swr < prev_swr - 1e-4):
            return f"Kept -- band-max SWR improved {prev_swr:.3f} -> {new_swr:.3f}."
        return "Kept -- objective improved (centre / RL / X / band combined)."
    if (prev_swr is not None and new_swr is not None
            and new_swr > prev_swr + 1e-4):
        return f"Rejected -- band-max SWR got worse {prev_swr:.3f} -> {new_swr:.3f}."
    return ("Rejected -- objective got worse (centre SWR drifted, |X| > 2.5 Ohm, "
            "or F/B dropped below 12 dB; matcher walked back).")
