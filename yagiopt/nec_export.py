"""
NEC-2 card-deck exporter for the yagiopt optimizer.

Writes a standard .nec text file that can be loaded by:
  - EZNEC (File -> Import)
  - 4nec2
  - xnec2c
  - CocoaNEC
  - raw nec2++ / nec2c CLI

Design goal: this module has NO dependencies on scoring / search / sweep logic,
so future edits to the optimizer will never break export.
"""
import numpy as np
from datetime import datetime

from .constants import (
    FT,
    IN,
    TAPER_CENTER_DIAMETER_IN,
    TAPER_OUTER_DIAMETER_IN,
    OUTER_SECTION_SEGMENTS,
    CENTER_SECTION_SEGMENTS,
    ALUMINUM_SIGMA,
    GROUND_EPSR,
    GROUND_SIGMA,
)
from .geometry import y_positions_from_spacings
# Reuse the exact same taper geometry that nec_engine uses so EZNEC matches 1:1
from .nec_engine import stepped_element_section_lengths_ft, element_part_tags


def _fmt(v):
    """NEC cards use fixed-width columns in classic format, but free-form works too."""
    return f"{float(v): .6f}"


def _emit_tapered_element(lines, element_index, length_ft, y_ft, z_ft):
    """Write 3 GW cards (left taper, center section, right taper) matching nec_engine."""
    tag_left, tag_center, tag_right = element_part_tags(element_index)
    center_len_ft, _ = stepped_element_section_lengths_ft(length_ft)

    half_total_m = 0.5 * length_ft * FT
    half_center_m = 0.5 * center_len_ft * FT
    y_m = y_ft * FT
    z_m = z_ft * FT

    r_outer = 0.5 * TAPER_OUTER_DIAMETER_IN * IN
    r_center = 0.5 * TAPER_CENTER_DIAMETER_IN * IN

    # Left outer taper
    lines.append(
        f"GW {tag_left:>4d} {OUTER_SECTION_SEGMENTS:>3d} "
        f"{_fmt(-half_total_m)} {_fmt(y_m)} {_fmt(z_m)} "
        f"{_fmt(-half_center_m)} {_fmt(y_m)} {_fmt(z_m)} {_fmt(r_outer)}"
    )
    # Center section (holds the feed)
    lines.append(
        f"GW {tag_center:>4d} {CENTER_SECTION_SEGMENTS:>3d} "
        f"{_fmt(-half_center_m)} {_fmt(y_m)} {_fmt(z_m)} "
        f"{_fmt(half_center_m)} {_fmt(y_m)} {_fmt(z_m)} {_fmt(r_center)}"
    )
    # Right outer taper
    lines.append(
        f"GW {tag_right:>4d} {OUTER_SECTION_SEGMENTS:>3d} "
        f"{_fmt(half_center_m)} {_fmt(y_m)} {_fmt(z_m)} "
        f"{_fmt(half_total_m)} {_fmt(y_m)} {_fmt(z_m)} {_fmt(r_outer)}"
    )


def write_nec_deck(path, lengths_ft, spacings_ft, height_ft, freq_mhz,
                   use_real_ground=True, element_names=None,
                   extra_comments=None, write_pattern_request=True):
    """Write a full NEC-2 card deck for the given 7-element Yagi geometry.

    Cards produced (standard order):
        CM ...   comments
        CE       end comments
        GW ...   21 wire cards (7 elements * 3 sections for taper)
        GE       geometry end (with ground flag)
        GN       ground model (real Sommerfeld or free space)
        LD 5     aluminum wire loss (applies to all tags)
        FR       single-frequency request
        EX 0     voltage source at DE center segment
        RP 0     full-sphere radiation pattern (5 deg steps) for 3D plots
        EN       end
    """
    lengths_ft = np.asarray(lengths_ft, dtype=float)
    spacings_ft = np.asarray(spacings_ft, dtype=float)
    y_pos = y_positions_from_spacings(spacings_ft)

    if element_names is None:
        element_names = [f"EL{i}" for i in range(len(lengths_ft))]

    lines = []
    lines.append("CM ==========================================================")
    lines.append("CM  yagiopt exported design")
    lines.append(f"CM  Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"CM  Center frequency: {float(freq_mhz):.3f} MHz")
    lines.append(f"CM  Boom height:      {float(height_ft):.4f} ft")
    lines.append(f"CM  Boom length REF->D5: {float(np.sum(spacings_ft)):.4f} ft")
    lines.append(f"CM  Ground: {'real (eps=%.1f sigma=%.4f)' % (GROUND_EPSR, GROUND_SIGMA) if use_real_ground else 'free space'}")
    lines.append("CM ----------------------------------------------------------")
    lines.append("CM  Element   Length(ft)    Position(ft)")
    for name, L, pos in zip(element_names, lengths_ft, y_pos):
        lines.append(f"CM    {name:>3s}    {L:10.4f}    {pos:10.4f}")
    lines.append("CM ----------------------------------------------------------")
    lines.append(f"CM  Element taper:  center {TAPER_CENTER_DIAMETER_IN:.3f} in OD,"
                 f" outer {TAPER_OUTER_DIAMETER_IN:.3f} in OD")
    lines.append(f"CM  Segments:       outer {OUTER_SECTION_SEGMENTS}, center {CENTER_SECTION_SEGMENTS} (feed on center seg)")

    if extra_comments:
        lines.append("CM ----------------------------------------------------------")
        for c in extra_comments:
            for sub in str(c).splitlines():
                lines.append(f"CM  {sub}")

    lines.append("CE")

    # Wires
    for i in range(len(lengths_ft)):
        _emit_tapered_element(lines, i, lengths_ft[i], y_pos[i], height_ft)

    # Geometry end + ground
    if use_real_ground:
        lines.append("GE 1")  # ground present
        lines.append(f"GN 2 0 0 0 {_fmt(GROUND_EPSR)} {_fmt(GROUND_SIGMA)} 0 0")
    else:
        lines.append("GE 0")
        lines.append("GN -1")

    # Aluminum loss on all wires (tag 0 = all)
    lines.append(f"LD 5 0 0 0 {_fmt(ALUMINUM_SIGMA)} 0 0")

    # Single frequency
    lines.append(f"FR 0 1 0 0 {_fmt(freq_mhz)} 0")

    # Excitation: voltage source on DE center segment
    _, de_center_tag, _ = element_part_tags(1)
    feed_seg = (CENTER_SECTION_SEGMENTS + 1) // 2
    lines.append(f"EX 0 {de_center_tag} {feed_seg} 0 1.0 0.0")

    # Radiation pattern request — 5 deg resolution
    # With ground: theta must stay <= 90 deg (sky only); use 19 theta steps from 0..90
    # Free space:  full sphere is fine; use 37 theta steps from 0..180
    if write_pattern_request:
        # RP 0 NTH NPH XNDA THETS PHIS DTH DPH
        if use_real_ground:
            lines.append("RP 0 19 73 1000 0.0 0.0 5.0 5.0 0.0 0.0")
        else:
            lines.append("RP 0 37 73 1000 0.0 0.0 5.0 5.0 0.0 0.0")

    lines.append("EN")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")

    return path
