"""Round-trip + import tests for hyagi.exporters.from_maa().

Validates:
  * a hybrid geometry round-trips through to_maa / from_maa with the same
    element count, names (REF/XFRMR/DE/COUPLER/DIRn) and inch-accurate
    positions / lengths;
  * the centre frequency and DE identification (fed wire) are recovered;
  * stray boom / G/W_E wires (vertical drops, horizontal boom) do not get
    misread as elements.
"""
import json
import pathlib

import pytest

from hyagi import exporters

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEO = json.loads((ROOT / "data/current_geometry_v2.json").read_text())
RULES = json.loads((ROOT / "data/rules_v2.json").read_text())
ELEMENTS = GEO["elements"]


def test_from_maa_roundtrip_element_count_and_names():
    text = exporters.to_maa(ELEMENTS, RULES, height_ft=30.0)
    parsed = exporters.from_maa(text)
    assert len(parsed["elements"]) == len(ELEMENTS)
    assert [e["name"] for e in parsed["elements"]] == \
           [e["name"] for e in ELEMENTS]


def test_from_maa_roundtrip_positions_lengths():
    text = exporters.to_maa(ELEMENTS, RULES, height_ft=30.0)
    parsed = exporters.from_maa(text)
    for src, got in zip(ELEMENTS, parsed["elements"]):
        # Round-trip via metres allows ~0.01" drift due to float<->string.
        assert abs(got["position_in"] - float(src["position_in"])) < 0.02
        assert abs(got["length_in"] - float(src["length_in"])) < 0.02


def test_from_maa_center_freq():
    text = exporters.to_maa(ELEMENTS, RULES, height_ft=30.0)
    parsed = exporters.from_maa(text)
    assert parsed["center_mhz"] is not None
    assert abs(parsed["center_mhz"] - 27.195) < 1e-3


def test_from_maa_empty_raises():
    with pytest.raises(ValueError):
        exporters.from_maa("")


def test_from_maa_ignores_extra_boom_wires():
    """A horizontal boom wire (x1 != x2) and a vertical drop wire must NOT
    be misread as an antenna element."""
    text = exporters.to_maa(ELEMENTS, RULES, height_ft=30.0)
    lines = text.splitlines()
    wi = lines.index("*** wires ***")
    n_idx = wi + 1
    n = int(lines[n_idx].strip())
    # Insert two junk wires: one horizontal boom segment (x1 != x2) and one
    # very short vertical "drop" at a brand-new X (no element wires there).
    extra = [
        "0.5, 0.0, 9.0, 1.5, 0.0, 9.0, 0.02, -1",       # boom segment
        "9.999, 0.0, 9.0, 9.999, 0.0, 8.95, 0.02, -1",  # short drop
    ]
    new_lines = lines[:n_idx] + [str(n + len(extra))] + \
                lines[n_idx + 1: n_idx + 1 + n] + extra + lines[n_idx + 1 + n:]
    parsed = exporters.from_maa("\n".join(new_lines))
    assert len(parsed["elements"]) == len(ELEMENTS)
