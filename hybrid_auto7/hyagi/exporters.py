"""hybrid_auto7 — geometry exporters (.nec and .maa / MMANA-GAL).

Turns a tuned element table (current_geometry_v2.json / a run result) into files
the user can open in external simulators:

  * to_nec()  -> a ready NEC-2 input deck (the same tapered-aluminium model the
                 engine tunes against, plus a band sweep + full radiation
                 pattern), openable in nec2c / 4nec2 / xnec2c.
  * to_maa()  -> an MMANA-GAL .maa text file.  Element span lies along Y, boom
                 along X, height on Z.  Each element is emitted as the SAME
                 stepped-diameter tubing sections the engine models, so MMANA's
                 predicted resonance agrees with the app (a single uniform wire
                 never matches a real telescoping build).  The DE is voltage-fed
                 at the centre of its centre section (``w<n>c``).

Both reuse the active taper schedule from data/taper_v2.json via v2_runner, so
the exported cut lengths match exactly what the optimizer produced.
"""
from __future__ import annotations

from . import v2_runner

INCH = v2_runner.INCH
FT = v2_runner.FT


def _center_mhz(rules, center_mhz):
    glb = rules.get("global", {})
    if center_mhz is not None:
        return float(center_mhz)
    flow = float(glb.get("freq_mhz_low", 26.665))
    fhigh = float(glb.get("freq_mhz_high", 27.855))
    return float(glb.get("freq_mhz_center", 0.5 * (flow + fhigh)))


# ---------------------------------------------------------------------------
# NEC-2 deck
# ---------------------------------------------------------------------------
def to_nec(elements, rules, height_ft=30.0, taper="auto", points=21):
    """Return a NEC-2 input deck (string) for the geometry: tapered-Al elements
    over real ground, a band sweep (FR) across rules['global'] and a full
    hemisphere radiation pattern (RP).  Drop-in for nec2c / 4nec2 / xnec2c."""
    glb = rules.get("global", {})
    f_low = float(glb["freq_mhz_low"])
    f_high = float(glb["freq_mhz_high"])
    points = max(2, int(points))
    freqs = [f_low + i * (f_high - f_low) / (points - 1) for i in range(points)]
    return v2_runner.build_nec_card(elements, freqs, height_ft=height_ft,
                                    pattern=True, taper=taper)


# ---------------------------------------------------------------------------
# MMANA-GAL .maa
# ---------------------------------------------------------------------------
def _fmt(v):
    """MMANA-friendly number: trim trailing zeros, keep at least 1 decimal."""
    s = f"{v:.5f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    return s


def _maa_wires(elements, height_ft, taper):
    """Build MMANA wire rows + the 1-based index of the DE centre wire.

    Element span is along Y (-half..+half), boom along X (= position), height on
    Z.  Each element is the same stepped-diameter tubing the engine models: one
    centre wire crossing Y=0 plus a +Y and -Y wire per outer taper section."""
    if taper == "auto":
        taper = v2_runner.get_active_taper()
    H = height_ft * FT
    rows = []                 # (x1,y1,z1,x2,y2,z2,r)
    de_center_idx = None      # 1-based wire index fed on the DE
    for el in sorted(elements, key=lambda e: float(e["position_in"])):
        x = float(el["position_in"]) * INCH
        half = (float(el["length_in"]) * INCH) / 2.0
        is_de = str(el["name"]).upper() == "DE"
        if taper:
            secs = v2_runner._half_sections(half, taper)
        else:
            secs = [(0.25 * INCH, half)]   # uniform fallback
        r0, l0 = secs[0]
        rows.append((x, -l0, H, x, l0, H, r0))      # centre wire crosses Y=0
        if is_de:
            de_center_idx = len(rows)                # 1-based
        inner = l0
        for (r, seglen) in secs[1:]:                 # mirrored outer sections
            rows.append((x, inner, H, x, inner + seglen, H, r))
            rows.append((x, -inner, H, x, -(inner + seglen), H, r))
            inner += seglen
    if de_center_idx is None:
        raise ValueError("geometry has no DE element to feed")
    return rows, de_center_idx


def to_maa(elements, rules, height_ft=30.0, taper="auto", center_mhz=None,
           title=None):
    """Return an MMANA-GAL .maa file (string) for the geometry."""
    fc = _center_mhz(rules, center_mhz)
    if title is None:
        title = f"hybrid_auto7 {fc:.3f} MHz tuned ({len(elements)} elements)"
    rows, de_idx = _maa_wires(elements, height_ft, taper)

    lines = [title, "*", _fmt(fc)]
    lines.append("*** wires ***")
    lines.append(str(len(rows)))
    for (x1, y1, z1, x2, y2, z2, r) in rows:
        lines.append(", ".join(_fmt(v) for v in (x1, y1, z1, x2, y2, z2, r))
                     + ", -1")
    lines.append("*** source ***")
    lines.append("1, 0")
    lines.append(f"w{de_idx}c, 0.0, 1.0")
    lines.append("*** load ***")
    lines.append("0, 0")
    lines.append("*** segmentation ***")
    lines.append("40, 40, 2.0, 2")
    lines.append("*** G/W_E ***")
    lines.append("0, 0.0")
    return "\n".join(lines) + "\n"
