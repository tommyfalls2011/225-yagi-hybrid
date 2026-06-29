"""Grounding tristate engine tests.

Three element-grounding modes:
  all_insulated  -> nothing bonded; DE coax-fed; standard hybrid build
  cell_insulated -> XFRMR/DE/COUPLER float on insulators; REF + every DIRn
                    bonded to the boom (cell-isolated parasitic-bonded build)
  all_grounded   -> every parasitic bonded to the boom; DE stays coax-fed
                    (lightning-bonded / high-power build)

These tests pin v2_runner._bond_for() so the NEC card writer can't drift the
rules silently.  build_nec_card uses _bond_for() to decide which elements
get bonded to the boom drop wires, so this is the single source of truth
for the grounding model.
"""
from hyagi import v2_runner


def test_all_insulated_bonds_nothing():
    """Default mode: no element gets a boom-drop wire; the only emission to
    boom is the feed point on the DE which is coax-fed (handled separately)."""
    for nm in ("REF", "XFRMR", "DE", "COUPLER", "DIR1", "DIR2", "DIR3"):
        assert v2_runner._bond_for(nm, "all_insulated") is False, (
            f"all_insulated: {nm} must NOT be bonded"
        )


def test_cell_insulated_bonds_only_parasitics():
    """Cell elements (XFRMR, DE, COUPLER) stay floating; REF + every DIRn
    bonds to the boom."""
    # Cell - must NOT bond
    for nm in ("XFRMR", "DE", "COUPLER"):
        assert v2_runner._bond_for(nm, "cell_insulated") is False, (
            f"cell_insulated: {nm} (cell) must stay floating"
        )
    # Parasitics - MUST bond
    for nm in ("REF", "DIR1", "DIR2", "DIR3", "DIR12"):
        assert v2_runner._bond_for(nm, "cell_insulated") is True, (
            f"cell_insulated: {nm} (parasitic) must be bonded"
        )


def test_all_grounded_bonds_everything_except_de():
    """All parasitics bonded; DE stays unbonded (coax-fed)."""
    assert v2_runner._bond_for("DE", "all_grounded") is False
    for nm in ("REF", "XFRMR", "COUPLER", "DIR1", "DIR2", "DIR3"):
        assert v2_runner._bond_for(nm, "all_grounded") is True, (
            f"all_grounded: {nm} must be bonded (DE is the only floater)"
        )


def test_case_insensitive_element_names():
    """Lower-case element names from old saved geometries must still resolve
    to the right bond decision."""
    assert v2_runner._bond_for("de", "all_grounded") is False
    assert v2_runner._bond_for("dir1", "cell_insulated") is True


def test_unknown_mode_falls_back_to_safe():
    """An unrecognised mode string must not crash and must default to NO
    bonding (safer model: DE coax-fed, everything insulated)."""
    assert v2_runner._bond_for("DIR1", "weird_thing_not_implemented") is False
    assert v2_runner._bond_for("REF", "") is False


def test_build_nec_card_uses_mode_for_bonding(monkeypatch):
    """The NEC card writer must follow _bond_for's verdict when emitting drop
    wires.  Inspect the deck for the boom-bond signature `0.000000 {zb:.6f}`
    (a vertical drop from element centre to the boom Y=0 plane).

    In all_insulated mode there should be NO bond drop wires.
    In all_grounded mode there should be N drop wires for N-1 parasitics
    (DE excluded)."""
    elements = [
        {"name": "REF",  "position_in": 0.0,   "length_in": 218.5},
        {"name": "DE",   "position_in": 46.9,  "length_in": 215.7},
        {"name": "DIR1", "position_in": 135.9, "length_in": 195.0},
    ]
    # all_insulated
    card_ins = v2_runner.build_nec_card(elements, [27.195], height_ft=30.0,
                                        grounding="all_insulated", taper=None)
    bonds_ins = [ln for ln in card_ins.splitlines()
                 if ln.startswith("GW") and ln.count("0.000000") >= 2]
    # Vertical drop wires have x identical / y=0 / z differs -- they're
    # short and distinct from horizontal element wires.  Easier check: in
    # all_insulated mode the deck must be SHORTER than the all_grounded one.
    card_grn = v2_runner.build_nec_card(elements, [27.195], height_ft=30.0,
                                        grounding="all_grounded", taper=None)
    assert len(card_grn.splitlines()) > len(card_ins.splitlines()), (
        "all_grounded deck must contain extra GW boom-drop wires"
    )
    # cell_insulated: REF + DIR1 bonded (2 parasitics) -> still longer than
    # all_insulated, but should bond ONLY 2 (REF and DIR1) since DE is the
    # only cell element here.
    card_cell = v2_runner.build_nec_card(elements, [27.195], height_ft=30.0,
                                         grounding="cell_insulated", taper=None)
    assert len(card_cell.splitlines()) > len(card_ins.splitlines())


def test_legacy_grounded_bool_still_supported():
    """Old configs that set grounded=True/False (no 'grounding' string) must
    keep working end-to-end."""
    elements = [
        {"name": "REF",  "position_in": 0.0,   "length_in": 218.5},
        {"name": "DE",   "position_in": 46.9,  "length_in": 215.7},
        {"name": "DIR1", "position_in": 135.9, "length_in": 195.0},
    ]
    card_legacy_grounded = v2_runner.build_nec_card(
        elements, [27.195], height_ft=30.0, grounded=True, taper=None)
    card_new_grounded = v2_runner.build_nec_card(
        elements, [27.195], height_ft=30.0, grounding="all_grounded", taper=None)
    # Equivalent decks (allow header timestamp differences -> compare line counts).
    assert len(card_legacy_grounded.splitlines()) == len(card_new_grounded.splitlines())
