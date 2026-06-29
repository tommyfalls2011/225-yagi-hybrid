from hyagi.boom_ground import apply_boom_ground  # boom-ground hook
from dataclasses import dataclass

from .config import (
    ELEMENT_ORDER,
    BASE_LENGTHS_IN,
    BASE_POSITIONS_IN,
    inch_to_m,
)


@dataclass
class Element:
    name: str
    position_in: float
    length_in: float


@dataclass
class Wire:
    tag: int
    element_name: str
    part_name: str
    x1_in: float
    y1_in: float
    z1_in: float
    x2_in: float
    y2_in: float
    z2_in: float
    radius_in: float
    segments: int


def _boom_correction_in(ant):
    """
    Simple approximation for boom correction on boom-mounted parasitic cell elements.
    Typical range about 2 to 3 inches depending on boom diameter.
    """
    boom_diam = float(getattr(ant, "boom_diameter_in", 2.0))
    return max(2.0, min(3.0, 1.5 + 0.5 * boom_diam))


def _effective_length_in(element_name, length_in, ant):
    """
    Convert user/build physical length to simulated effective electrical length.
    Current approximation:
      - full_cell_insulated: no correction
      - de_only_insulated: XFRMR and COUPLER electrically shortened by boom effect
    """
    style = getattr(ant, "cell_mounting_style", "full_cell_insulated")
    length_in = float(length_in)

    if style == "de_only_insulated" and element_name in ("XFRMR", "COUPLER"):
        return max(1.0, length_in - _boom_correction_in(ant))

    return length_in


def design_key(design, f_start, f_stop, f_step):
    parts = [
        f"depos{design.de_position_in:.3f}",
        f"xsp{design.xfrmr_spacing_in:.3f}",
        f"csp{design.coupler_spacing_in:.3f}",
        f"xl{design.xfrmr_length_in:.3f}",
        f"cl{design.coupler_length_in:.3f}",
        f"de{design.de_length_in:.3f}",
    ]

    attrs = vars(design)
    dir_nums = set()

    for name in attrs:
        if not name.startswith("dir"):
            continue
        if not (name.endswith("_position_in") or name.endswith("_length_in")):
            continue

        middle = name[3:].split("_", 1)[0]
        if middle.isdigit():
            dir_nums.add(int(middle))

    for n in sorted(dir_nums):
        pos = getattr(design, f"dir{n}_position_in", None)
        length = getattr(design, f"dir{n}_length_in", None)
        parts.append(f"d{n}p{(-1.0 if pos is None else pos):.3f}")
        parts.append(f"d{n}l{(-1.0 if length is None else length):.3f}")

    parts.append(f"f{f_start:.3f}_{f_stop:.3f}_{f_step:.3f}")
    return "_".join(parts)


def build_elements(design):
    positions = dict(BASE_POSITIONS_IN)
    lengths = dict(BASE_LENGTHS_IN)

    positions["DE"] = design.de_position_in
    positions["XFRMR"] = design.de_position_in - design.xfrmr_spacing_in
    positions["COUPLER"] = design.de_position_in + design.coupler_spacing_in

    lengths["XFRMR"] = design.xfrmr_length_in
    lengths["COUPLER"] = design.coupler_length_in
    lengths["DE"] = design.de_length_in

    attrs = vars(design)

    for attr_name, value in attrs.items():
        if value is None:
            continue
        if not attr_name.startswith("dir"):
            continue

        tail = attr_name[3:]
        if "_" not in tail:
            continue

        num, field = tail.split("_", 1)
        if not num.isdigit():
            continue

        element_name = f"DIR{num}"

        if element_name not in positions:
            continue

        if field == "position_in":
            positions[element_name] = float(value)
        elif field == "length_in":
            lengths[element_name] = float(value)

    elements = []

    for name in ELEMENT_ORDER:
        elements.append(
            Element(
                name=name,
                position_in=positions[name],
                length_in=lengths[name],
            )
        )

    return elements



def validate_elements(elements, ant):
    """
    Validate arbitrary hybrid element lists.

    Required:
        REF
        XFRMR
        DE
        COUPLER
        at least one director

    Supports:
        DIR1 ... DIR10 or more
    """

    seen = {}
    names = {e.name for e in elements}

    for required in ["REF", "XFRMR", "DE", "COUPLER"]:
        if required not in names:
            raise ValueError(f"Missing element: {required}")

    if not any(name.startswith("DIR") for name in names):
        raise ValueError("At least one director is required")

    for e in elements:
        if e.position_in < -1e-9:
            raise ValueError(f"{e.name} is behind reflector at {e.position_in}")

        if e.position_in > ant.boom_length_in + 1e-9:
            raise ValueError(f"{e.name} exceeds boom length at {e.position_in}")

        if e.length_in <= 0:
            raise ValueError(f"{e.name} invalid length {e.length_in}")

        key = round(e.position_in, 6)

        if key in seen:
            raise ValueError(f"Position collision: {seen[key]} and {e.name}")

        seen[key] = e.name

    # BOOM_ORDER_v1: enforce physical element ordering along the boom.
    # Canonical order (front of boom away from feed):
    #   REF -> XFRMR -> DE -> COUPLER -> DIR1 -> DIR2 -> ... -> DIRn
    # Anything violating that order is unbuildable.
    MIN_GAP_IN = 1.0
    canonical = ["REF", "XFRMR", "DE", "COUPLER"]
    dirs = sorted(
        [e.name for e in elements if e.name.startswith("DIR")],
        key=lambda n: int(n[3:]) if n[3:].isdigit() else 999,
    )
    order = [n for n in canonical if n in names] + dirs
    pos_by_name = {e.name: e.position_in for e in elements}
    prev_name = None
    prev_pos = None
    for nm in order:
        p = pos_by_name[nm]
        if prev_pos is not None and p < prev_pos + MIN_GAP_IN - 1e-6:
            raise ValueError(
                f"Boom-order violation: {nm} at {p:.3f} in must be at least "
                f"{prev_pos + MIN_GAP_IN:.3f} in (>= {prev_name} + {MIN_GAP_IN:.1f} in gap)"
            )
        prev_name = nm
        prev_pos = p

    return True


def build_wires(elements, ant):
    """
    Convert each physical element into three NEC wires:

        left outer  = 1/2 inch OD
        center      = 5/8 inch OD, 36 inches each side
        right outer = 1/2 inch OD

    Feed is placed on center segment of DE center wire.
    """

    wires = []
    tag = 1

    outer_radius = ant.outer_od_in / 2.0
    center_radius = ant.center_od_in / 2.0
    center_half = ant.center_half_len_in
    z = ant.model_height_in

    feed_tag = None
    feed_seg = None

    for e in elements:
        effective_length_in = _effective_length_in(e.name, e.length_in, ant)
        half = effective_length_in / 2.0
        y = e.position_in

        if half <= center_half:
            segs = ant.center_segments

            wires.append(
                Wire(
                    tag=tag,
                    element_name=e.name,
                    part_name="CENTER",
                    x1_in=-half,
                    y1_in=y,
                    z1_in=z,
                    x2_in=half,
                    y2_in=y,
                    z2_in=z,
                    radius_in=center_radius,
                    segments=segs,
                )
            )

            if e.name == "DE":
                feed_tag = tag
                feed_seg = (segs + 1) // 2

            tag += 1
            continue

        wires.append(
            Wire(
                tag=tag,
                element_name=e.name,
                part_name="LEFT_OUTER",
                x1_in=-half,
                y1_in=y,
                z1_in=z,
                x2_in=-center_half,
                y2_in=y,
                z2_in=z,
                radius_in=outer_radius,
                segments=ant.outer_segments,
            )
        )
        tag += 1

        center_tag = tag
        center_segs = ant.center_segments

        wires.append(
            Wire(
                tag=center_tag,
                element_name=e.name,
                part_name="CENTER",
                x1_in=-center_half,
                y1_in=y,
                z1_in=z,
                x2_in=center_half,
                y2_in=y,
                z2_in=z,
                radius_in=center_radius,
                segments=center_segs,
            )
        )

        if e.name == "DE":
            feed_tag = center_tag
            feed_seg = (center_segs + 1) // 2

        tag += 1

        wires.append(
            Wire(
                tag=tag,
                element_name=e.name,
                part_name="RIGHT_OUTER",
                x1_in=center_half,
                y1_in=y,
                z1_in=z,
                x2_in=half,
                y2_in=y,
                z2_in=z,
                radius_in=outer_radius,
                segments=ant.outer_segments,
            )
        )
        tag += 1

    if feed_tag is None or feed_seg is None:
        raise ValueError("Could not find DE feed tag/segment")

    return wires, feed_tag, feed_seg



def _swap_xy_if_needed(nec_text):
    """If BOOM_AXIS=x (MMANA-GAL convention), swap X<->Y in every GW line.
    Internal model still builds boom-along-Y; this just flips the final NEC output."""
    try:
        from hyagi.cell_rules import get_rules
        if get_rules().get("BOOM_AXIS","x").lower() != "x": return nec_text
    except Exception: return nec_text
    out = []
    for ln in nec_text.splitlines():
        p = ln.split()
        if p[:1] == ["GW"] and len(p) >= 10:
            # GW tag nseg x1 y1 z1 x2 y2 z2 rad
            p[3], p[4] = p[4], p[3]   # x1 <-> y1
            p[6], p[7] = p[7], p[6]   # x2 <-> y2
            out.append(" ".join(p))
        else:
            out.append(ln)
    return "\n".join(out) + "\n"


def generate_nec_text(elements, ant, f_start, f_stop, f_step):
    wires, feed_tag, feed_seg = build_wires(elements, ant)

    nfreq = int(round((f_stop - f_start) / f_step)) + 1
    element_names = "-".join(e.name for e in elements)

    lines = [
        f"CM hybrid_auto7 tapered antenna model with {len(elements)} elements",
        f"CM {element_names}",
        "CM 5/8 inch OD center, 1/2 inch OD outer",
        "CE",
    ]

    for w in wires:
        lines.append(
            "GW {tag:d} {seg:d} {x1:.6f} {y1:.6f} {z1:.6f} "
            "{x2:.6f} {y2:.6f} {z2:.6f} {rad:.6f}".format(
                tag=w.tag,
                seg=w.segments,
                x1=inch_to_m(w.x1_in),
                y1=inch_to_m(w.y1_in),
                z1=inch_to_m(w.z1_in),
                x2=inch_to_m(w.x2_in),
                y2=inch_to_m(w.y2_in),
                z2=inch_to_m(w.z2_in),
                rad=inch_to_m(w.radius_in),
            )
        )

    _gmode = getattr(ant, "ground_mode", "average")
    if _gmode == "free_space":
        lines.append("GE 0")
    else:
        lines.append("GE -1")
    lines.append(f"FR 0 {nfreq:d} 0 0 {f_start:.6f} {f_step:.6f}")
    if _gmode == "perfect":
        lines.append("GN 1 0 0 0 0.0 0.0 0.0 0.0")
    elif _gmode != "free_space":
        _eps = float(getattr(ant, "ground_epsr", 13.0))
        _sig = float(getattr(ant, "ground_sigma_s_per_m", 0.005))
        lines.append(f"GN 2 0 0 0 {_eps:.4f} {_sig:.6f} 0.0 0.0")
    lines.append(f"EX 0 {feed_tag:d} {feed_seg:d} 0 1.0 0.0")
    # Elevation sweep: 19 theta (0..90 deg, 5 deg step) x 73 phi (0..360 deg, 5 deg step)
    # theta=0 is zenith, theta=90 is horizon. Upper hemisphere over ground.
    lines.append("RP 0 19 73 1000 0.0 0.0 5.0 5.0")
    lines.append("EN")


    _nec_out = "\n".join(lines) + "\n"

    try: _nec_out = apply_boom_ground(_nec_out, elements)
    except Exception: pass
    _nec_out = _swap_xy_if_needed(_nec_out)
    return _nec_out
