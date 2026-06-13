"""hybrid_auto7 — hybrid geometry seeder for a selectable element count.

A hybrid always has REF + XFRMR + DE + COUPLER, plus 0..N directors.  The user
picks how many directors, giving a total of (4 + N) elements.  This produces a
sane wavelength-scaled starting geometry; the matcher then re-tunes lengths and
the matching cell.  Director positions are seeded here and held by the matcher
(which tunes director lengths), so the boom stays sensible.

When `max_boom_in` is given, director spacings are COMPRESSED so the last
director sits within that limit -- so a locked 22 ft boom is honoured at
build time, never overrun.
"""
from __future__ import annotations


def build_geometry(n_directors, center_mhz=27.195, max_boom_in=None):
    """Return {'elements': [...]} for a hybrid with n_directors directors.

    n_directors clamps to 0..18 (the UI slider goes up to 14 directors / 18 total
    elements -- this gives headroom).  If `max_boom_in` is set the director
    spacings are uniformly scaled so the last director's position is <= that
    value, with a 1" tip margin for the optimizer to wiggle within."""
    n_directors = max(0, min(18, int(n_directors)))
    wl = 11811.0 / float(center_mhz)          # free-space wavelength, inches

    de_pos = round(0.108 * wl, 1)             # ~46.9" -> keep REF behind DE
    de_len = round(0.484 * wl, 1)             # ~210"
    elements = [
        {"name": "REF",     "position_in": 0.0,
         "length_in": round(0.503 * wl, 1)},
        {"name": "XFRMR",   "position_in": round(de_pos - 6.0, 1),
         "length_in": round(0.459 * wl, 1)},
        {"name": "DE",      "position_in": de_pos,
         "length_in": de_len},
        {"name": "COUPLER", "position_in": round(de_pos + 28.0, 1),
         "length_in": round(0.398 * wl, 1)},
    ]
    if n_directors == 0:
        return {"elements": elements}

    # Default freeband-style spacing.  d1 is the gap from DE to DIR1 (longer
    # than between directors), then directors march out at uniform spacing.
    d1_gap = 0.205 * wl
    spacing = 0.180 * wl

    # If the user locked the boom, compress the spacings so the last director
    # lands at (boom - 1") (leaves ~1" of tip margin past the last director).
    if max_boom_in is not None:
        boom = float(max_boom_in) - 1.0
        # Required reach from DE to last director.
        needed = d1_gap + spacing * (n_directors - 1)
        avail = boom - de_pos
        if needed > avail and needed > 0:
            scale = avail / needed
            d1_gap *= scale
            spacing *= scale
        # Whatever the COUPLER position is, the directors start after it.
        # _post-clamp_ keeps DIR1 strictly past the COUPLER.
        coupler_pos = elements[3]["position_in"]
        if de_pos + d1_gap <= coupler_pos:
            d1_gap = max(d1_gap, (coupler_pos - de_pos) + 6.0)

    d1 = round(de_pos + d1_gap, 1)
    for k in range(1, n_directors + 1):
        pos = round(d1 + spacing * (k - 1), 1)
        length = round(max(0.405 * wl, 0.449 * wl - 0.009 * wl * (k - 1)), 1)
        elements.append({"name": f"DIR{k}", "position_in": pos, "length_in": length})

    return {"elements": elements}
