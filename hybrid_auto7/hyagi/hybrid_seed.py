"""hybrid_auto7 — hybrid geometry seeder for a selectable element count.

A hybrid always has REF + XFRMR + DE + COUPLER, plus 0..N directors.  The user
picks how many directors (0-14), giving 4..18 total elements.  This produces a
sane wavelength-scaled starting geometry; the matcher then re-tunes lengths and
the matching cell.  Director positions are seeded here and held by the matcher
(which tunes director lengths), so the boom stays sensible.
"""
from __future__ import annotations


def build_geometry(n_directors, center_mhz=27.195):
    """Return {'elements': [...]} for a hybrid with n_directors directors."""
    n_directors = max(0, min(14, int(n_directors)))
    wl = 11811.0 / float(center_mhz)          # free-space wavelength, inches

    de_pos = round(0.108 * wl, 1)             # ~46.9" -> keep REF behind DE
    de_len = round(0.484 * wl, 1)             # ~210"
    elements = [
        {"name": "REF",     "position_in": 0.0,            "length_in": round(0.503 * wl, 1)},
        {"name": "XFRMR",   "position_in": round(de_pos - 6.0, 1),  "length_in": round(0.459 * wl, 1)},
        {"name": "DE",      "position_in": de_pos,         "length_in": de_len},
        {"name": "COUPLER", "position_in": round(de_pos + 28.0, 1), "length_in": round(0.398 * wl, 1)},
    ]
    # Directors: first ~0.205 wl ahead of DE, then ~0.18 wl spacing, lengths
    # tapering down a few inches each and levelling off.
    d1 = round(de_pos + 0.205 * wl, 1)
    spacing = round(0.180 * wl, 1)
    for k in range(1, n_directors + 1):
        pos = round(d1 + spacing * (k - 1), 1)
        length = round(max(0.405 * wl, 0.449 * wl - 0.009 * wl * (k - 1)), 1)
        elements.append({"name": f"DIR{k}", "position_in": pos, "length_in": length})
    return {"elements": elements}
