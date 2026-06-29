"""Imperial formatting helpers.

The whole project stores lengths and positions in DECIMAL INCHES internally
(matches the .nec / .maa exporters and the rules JSON).  Users build the
antenna on a workbench with a tape measure, so the UI always renders those
inches as feet + inches + a fraction snapped to 1/16".

Public API:
    fmt_in(inches, denom=16, with_decimal=False) -> str   e.g. "18' 11-1/2\""
    fmt_in_long(inches)                       -> str      same, spelt 'ft'/'in'
    fmt_inches_only(inches, denom=16)         -> str      "215-1/2\""

All formatters round to the nearest 1/denom inch (default 1/16") and reduce
the fraction so 8/16 -> 1/2, 12/16 -> 3/4, etc.
"""
from __future__ import annotations

import math


def _snap(inches: float, denom: int):
    """Round `inches` to the nearest 1/denom inch.

    Returns (sign, total_sixteenths) where total_sixteenths is the magnitude
    expressed in units of 1/denom inch.  Doing the rounding ONCE here lets the
    feet/inches/fraction split avoid any "11 16/16" off-by-one bugs.
    """
    sign = "-" if inches < 0 else ""
    n = int(round(abs(inches) * denom))
    return sign, n


def _split(n_units: int, denom: int):
    """Split N units of 1/denom inch into (feet, whole_inches, num, den)."""
    inches_units = denom * 12
    feet = n_units // inches_units
    rem = n_units - feet * inches_units
    whole_in = rem // denom
    frac_units = rem - whole_in * denom
    if frac_units == 0:
        return feet, whole_in, 0, 1
    g = math.gcd(frac_units, denom)
    return feet, whole_in, frac_units // g, denom // g


def _frac_str(num: int, den: int) -> str:
    return "" if num == 0 else f"-{num}/{den}"


def fmt_in(inches: float, denom: int = 16, with_decimal: bool = False) -> str:
    """Compact builder format: `18' 11-1/2"`.

    If with_decimal=True, appends the raw decimal inches in parentheses so the
    user can sanity-check the rounding when they want to (good for tuning
    pages where the optimizer outputs e.g. 215.7" but the bench tape only
    resolves to 1/16th).
    """
    sign, units = _snap(float(inches), denom)
    feet, win, n, d = _split(units, denom)
    frac = _frac_str(n, d)
    if feet > 0:
        out = f"{sign}{feet}' {win}{frac}\""
    else:
        out = f"{sign}{win}{frac}\""
    if with_decimal:
        out += f"  ({float(inches):.2f} in)"
    return out


def fmt_in_long(inches: float, denom: int = 16) -> str:
    """Long form: `18 ft 11-1/2 in` (used where the ' " glyphs cramp up)."""
    sign, units = _snap(float(inches), denom)
    feet, win, n, d = _split(units, denom)
    frac = _frac_str(n, d)
    if feet > 0:
        return f"{sign}{feet} ft {win}{frac} in"
    return f"{sign}{win}{frac} in"


def fmt_inches_only(inches: float, denom: int = 16) -> str:
    """No feet roll-up: `215-1/2"`. Used for element overall lengths in the
    cut-sheet table where it's more useful to read total inches at a glance."""
    sign, units = _snap(float(inches), denom)
    _feet, win, n, d = _split(units + 0, denom)
    # No feet split here -- reflow back to inches.
    total_in = (units // denom)
    rem_units = units - total_in * denom
    if rem_units == 0:
        return f"{sign}{total_in}\""
    g = math.gcd(rem_units, denom)
    return f"{sign}{total_in}-{rem_units // g}/{denom // g}\""
