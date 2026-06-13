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



# ---------------------------------------------------------------------------
# MMANA-GAL .maa IMPORT
# ---------------------------------------------------------------------------
# Reads a MMANA-GAL .maa text file and reconstructs the antenna's element
# table (the same {name, position_in, length_in} list the rest of the app
# consumes).  Used so the user can micro-optimise a tune in MMANA-GAL and
# pull the geometry back into hybrid_auto7 without retyping numbers.
#
# Rules of the .maa format we rely on (matches MMANA-GAL writer + to_maa()):
#   * Wires section starts with `*** wires ***`, next line = N, then N rows.
#   * Each wire row is `X1,Y1,Z1,X2,Y2,Z2,R,SEG` in METERS, comma separated.
#   * In a Yagi/hybrid laid out by this app, span = Y, boom = X, height = Z.
#   * Each PHYSICAL element groups all wires that share the same X (within
#     a small tolerance).  Its overall length = 2 * max|Y| across its wires.
#   * The element fed from the `*** source ***` `w<n>c` line is the DE.
#
# Wires for an element built with a stepped taper come out as one centre
# wire plus mirrored +Y / -Y outer sections; grouping by X handles all
# variants (uniform, stepped, with or without G/W_E grounded boom drops).
# ---------------------------------------------------------------------------
def _maa_section_index(lines, header):
    """Return the line index after a `*** header ***` row, or -1 if absent."""
    for i, ln in enumerate(lines):
        if ln.strip().lower() == header.lower():
            return i + 1
    return -1


def _maa_parse_wires(lines):
    """Return list of (x1,y1,z1,x2,y2,z2,r) in METERS from the wires section."""
    start = _maa_section_index(lines, "*** wires ***")
    if start < 0:
        raise ValueError("`*** wires ***` section not found")
    try:
        n = int(lines[start].strip().split()[0])
    except (ValueError, IndexError) as ex:
        raise ValueError(f"could not read wire count: {ex}")
    wires = []
    for raw in lines[start + 1: start + 1 + n]:
        parts = [p.strip() for p in raw.split(",") if p.strip() != ""]
        if len(parts) < 7:
            continue                           # skip blank / malformed rows
        try:
            w = tuple(float(parts[k]) for k in range(7))
        except ValueError:
            continue
        wires.append(w)
    if len(wires) < 1:
        raise ValueError("no wires parsed from .maa")
    return wires


def _maa_feed_wire_index(lines):
    """1-based wire index addressed by the `w<n>c` source line, or None."""
    start = _maa_section_index(lines, "*** source ***")
    if start < 1:
        return None
    if start + 1 >= len(lines):
        return None
    src = lines[start + 1].strip()             # e.g. "w12c, 0.0, 1.0"
    if not src.lower().startswith("w"):
        return None
    try:
        return int(src[1: src.index("c")])
    except ValueError:
        return None


def _group_wires_by_element(wires, tol=0.01):
    """Cluster wires by their X (boom position).  Returns list of
    (x_mean_m, half_len_m, [wire_indices]) sorted by X ascending.

    A real element's wires all share the same X (= boom position); span comes
    from Y.  A small tolerance handles rounding (1 cm in METRES is plenty)."""
    groups = []
    for idx, (x1, _y1, _z1, x2, _y2, _z2, _r) in enumerate(wires, start=1):
        xm = 0.5 * (x1 + x2)
        placed = False
        for g in groups:
            if abs(g["x"] - xm) <= tol:
                g["wires"].append(idx)
                g["xs"].append(xm)
                placed = True
                break
        if not placed:
            groups.append({"x": xm, "xs": [xm], "wires": [idx]})
    # Drop any "group" that is actually a horizontal boom segment (its wires
    # change X significantly along their length, i.e. x1 != x2).  Element
    # wires are vertical-on-Y and have x1==x2 within tol.
    cleaned = []
    for g in groups:
        keep = []
        ymax = 0.0
        for idx in g["wires"]:
            x1, y1, _z1, x2, y2, _z2, _r = wires[idx - 1]
            if abs(x1 - x2) > tol:
                continue                       # boom drop / G/W_E wire
            keep.append(idx)
            ymax = max(ymax, abs(y1), abs(y2))
        if keep and ymax > 0:
            g["wires"] = keep
            g["x"] = sum(g["xs"]) / len(g["xs"])
            g["half_m"] = ymax
            cleaned.append(g)
    cleaned.sort(key=lambda g: g["x"])
    return cleaned


def _name_for_index(idx, n_groups, de_idx):
    """Assign canonical hybrid names by position relative to the DE group.
    Order along the boom: REF, XFRMR, DE, COUPLER, DIR1, DIR2, ...  If a
    group is missing (DE-only, no XFRMR etc.) we still emit a sensible name."""
    if idx == de_idx:
        return "DE"
    if idx == de_idx - 2 and de_idx >= 2:
        return "REF"
    if idx == de_idx - 1:
        return "XFRMR"
    if idx == de_idx + 1:
        return "COUPLER"
    if idx > de_idx + 1:
        return f"DIR{idx - de_idx - 1}"
    # Below REF (e.g. an extra reflector): label as REFn for safety.
    return "REF" if idx == 0 else f"REF{de_idx - idx}"


def from_maa(text):
    """Parse an MMANA-GAL .maa text file and return a dict the app expects:

        {"elements": [{"name": str, "position_in": float, "length_in": float}, ...],
         "center_mhz": float | None,
         "title": str}

    Element names are reassigned in boom order around the FED (DE) wire, so
    the result drops straight into data/current_geometry_v2.json.
    """
    if not text or not text.strip():
        raise ValueError("empty .maa text")
    lines = text.splitlines()
    title = lines[0].strip() if lines else ""
    # Centre frequency is the line after the title comment, e.g.
    #   title
    #   *
    #   27.195
    center_mhz = None
    for ln in lines[1:6]:
        s = ln.strip().replace(",", ".")
        try:
            v = float(s)
            if 0.1 < v < 5000.0:
                center_mhz = v
                break
        except ValueError:
            continue

    wires = _maa_parse_wires(lines)
    feed_idx = _maa_feed_wire_index(lines)
    groups = _group_wires_by_element(wires)
    if not groups:
        raise ValueError("no antenna elements found (only boom wires?)")

    # Locate the DE group via the fed wire.
    de_g_idx = 0
    if feed_idx is not None:
        for gi, g in enumerate(groups):
            if feed_idx in g["wires"]:
                de_g_idx = gi
                break

    # ZERO the boom origin on the FIRST element so positions match how the
    # app stores them (REF at 0, others at +x in inches).
    INCH_M = INCH                              # 0.0254 m / in
    x0 = groups[0]["x"]
    elements = []
    for gi, g in enumerate(groups):
        name = _name_for_index(gi, len(groups), de_g_idx)
        pos_in = round((g["x"] - x0) / INCH_M, 4)
        length_in = round((2.0 * g["half_m"]) / INCH_M, 4)
        elements.append({"name": name,
                         "position_in": pos_in,
                         "length_in": length_in})
    return {"elements": elements,
            "center_mhz": center_mhz,
            "title": title}
