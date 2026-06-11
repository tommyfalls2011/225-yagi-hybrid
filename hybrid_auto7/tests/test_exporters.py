"""Tests for hyagi.exporters — .nec and .maa (MMANA-GAL) generation.

Validates:
  * the .maa structure matches MMANA-GAL's text layout (headers, counts, the
    DE voltage source pointing at the DE centre wire, real numeric rows);
  * wire endpoints are physically sane (element span = its length, boom along X,
    height on Z);
  * the .nec deck is a valid NEC-2 input that nec2c actually solves (impedance
    parsed back out), i.e. the export round-trips through the real engine.
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from hyagi import exporters, v2_runner

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEO = json.loads((ROOT / "data/current_geometry_v2.json").read_text())
RULES = json.loads((ROOT / "data/rules_v2.json").read_text())
ELEMENTS = GEO["elements"]


def test_maa_structure():
    maa = exporters.to_maa(ELEMENTS, RULES, height_ft=30.0)
    lines = maa.splitlines()
    # title, comment, freq, then sections
    assert lines[1] == "*"
    assert abs(float(lines[2]) - 27.195) < 1e-6
    assert "*** wires ***" in lines
    wi = lines.index("*** wires ***")
    n_wires = int(lines[wi + 1])
    assert n_wires > len(ELEMENTS)        # tapered -> more wires than elements
    # every wire row: 8 comma-separated fields ending in SEG -1
    for row in lines[wi + 2: wi + 2 + n_wires]:
        parts = [p.strip() for p in row.split(",")]
        assert len(parts) == 8
        assert parts[-1] == "-1"
        [float(p) for p in parts[:-1]]    # all numeric
    # source section feeds a DE centre wire 'w<n>c'
    si = lines.index("*** source ***")
    assert lines[si + 1] == "1, 0"
    src = lines[si + 2]
    assert src.startswith("w") and "c," in src
    feed_idx = int(src[1: src.index("c")])
    assert 1 <= feed_idx <= n_wires
    # trailing sections present (matches the user's real MMANA file)
    for hdr in ("*** load ***", "*** segmentation ***", "*** G/W_E ***"):
        assert hdr in lines


def test_maa_de_feed_is_the_de():
    """The fed wire must be the centre wire of the DE element."""
    maa = exporters.to_maa(ELEMENTS, RULES, height_ft=30.0)
    lines = maa.splitlines()
    wi = lines.index("*** wires ***")
    n_wires = int(lines[wi + 1])
    wires = lines[wi + 2: wi + 2 + n_wires]
    src = lines[lines.index("*** source ***") + 2]
    feed_idx = int(src[1: src.index("c")])
    de = next(e for e in ELEMENTS if e["name"].upper() == "DE")
    de_x = float(de["position_in"]) * exporters.INCH
    fed = [float(p) for p in wires[feed_idx - 1].split(",")[:-1]]
    # fed wire is at the DE boom position and crosses Y=0
    assert abs(fed[0] - de_x) < 1e-4
    assert fed[1] < 0 < fed[4]


def test_maa_element_span_matches_length():
    """Max |Y| of an element's wires must equal half its length."""
    de = next(e for e in ELEMENTS if e["name"].upper() == "DE")
    maa = exporters.to_maa([de], RULES, height_ft=30.0)
    lines = maa.splitlines()
    wi = lines.index("*** wires ***")
    n = int(lines[wi + 1])
    ys = []
    for row in lines[wi + 2: wi + 2 + n]:
        p = [float(v) for v in row.split(",")[:-1]]
        ys += [abs(p[1]), abs(p[4])]
    half_in = max(ys) / exporters.INCH
    assert abs(half_in - float(de["length_in"]) / 2.0) < 0.01


@pytest.mark.skipif(shutil.which("nec2c") is None, reason="nec2c not installed")
def test_nec_deck_solves():
    """The exported .nec deck must be valid: nec2c solves it and we can read an
    impedance back, proving the export round-trips through the real engine."""
    deck = exporters.to_nec(ELEMENTS, RULES, height_ft=30.0, points=5)
    assert deck.startswith("CM") and deck.rstrip().endswith("EN")
    assert "GN 2" in deck and "EX 0" in deck and "RP 0" in deck
    with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as f:
        f.write(deck)
        nec_path = f.name
    out_path = nec_path.replace(".nec", ".out")
    try:
        subprocess.run(["nec2c", "-i", nec_path, "-o", out_path],
                       capture_output=True, text=True, timeout=60)
        text = pathlib.Path(out_path).read_text()
    finally:
        for p in (nec_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass
    imps, _pat = v2_runner.parse_nec_output(text)
    assert imps, "nec2c produced no impedance — exported deck is invalid"
    R, X = imps[0]
    assert 1.0 < R < 500.0
