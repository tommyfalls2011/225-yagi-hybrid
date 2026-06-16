"""Quick resonance finder — tunes DE length until centre X ~ 0 at fc.

Used in TWO places to make sure every tune starts from a geometry that's
actually resonant at the user's chosen frequency:

  1. hybrid_seed.build_geometry(tune_to_fc=True, ...) calls this at build
     time so the seed geometry is calibrated against NEC2 reality (fat
     tubing + ground + grounding state) instead of the thin-wire
     '0.484 * lambda' formula that puts a 27 MHz antenna at 28 MHz.

  2. match_opt.optimize() calls this at the start of every tune when the
     starting geometry's centre SWR is above ~3, so the wideband descent
     begins from a sane baseline.  This is what a bench operator does
     first: VNA the DE alone, trim until X=0, then move on.

Returns a fresh `elements` list with the DE length adjusted.  Cost is
~15-25 NEC2 solves total (a coarse sweep + two refining passes).
"""
from __future__ import annotations

import copy
from typing import Optional

from . import v2_runner


def _swr_at(elements, fc_mhz: float, height_ft: float) -> float:
    """Single-frequency NEC2 probe -> centre SWR at fc.  Returns 99 on error
    so a NEC crash never wins the search."""
    try:
        curve, _mx, _av = v2_runner.band_swr_curve(
            elements, fc_mhz, fc_mhz, 1, height_ft)
        if curve:
            return float(curve[0][3])
    except Exception:
        pass
    return 99.0


def find_de_resonance(elements, fc_mhz: float, height_ft: float = 22.0,
                      rules: Optional[dict] = None,
                      log_fn=None) -> list:
    """Coarse + fine search for the DE length that puts the antenna's
    natural resonance at fc.  Sweeps the DE in three passes:

      1. wide  +/-15" in 3" steps   (rough basin find)
      2. medium +/-3"  in 1" steps   (narrow on the basin floor)
      3. fine   +/-1"  in 0.25" steps (resolve to ~1/4 inch)

    Each probe is one NEC2 solve, so the whole search is ~25 solves and
    completes in a few seconds.  The DE is the only element moved; REF
    and parasitics stay where the seed put them (they'll be re-tuned by
    the main matcher).  Useful resonance: centre SWR <= ~3 means the
    matcher's first wideband sweep won't blow up at the band edges."""
    els = copy.deepcopy(elements)
    de = next((e for e in els if str(e["name"]).upper() == "DE"), None)
    if de is None:
        return els

    # Apply rules-driven length bounds so the search stays physical.
    lo = 150.0
    hi = 260.0
    if rules:
        de_rules = rules.get("elements", {}).get("DE", {}) or {}
        lo = float(de_rules.get("length_min_in", lo))
        hi = float(de_rules.get("length_max_in", hi))

    def probe(L: float) -> float:
        L_clamped = min(hi, max(lo, float(L)))
        de["length_in"] = round(L_clamped, 3)
        return _swr_at(els, fc_mhz, height_ft)

    def clip(L: float) -> float:
        return min(hi, max(lo, float(L)))

    init = float(de["length_in"])
    best_L, best_S = clip(init), probe(init)

    # Coarse pass: +/-15 inches in 3-inch increments.
    for L in [init + 3.0 * k for k in range(-5, 6)]:
        Lc = clip(L)
        s = probe(Lc)
        if s < best_S:
            best_L, best_S = Lc, s

    # Medium pass: +/-3 inches in 1-inch increments around the basin floor.
    for L in [best_L + 1.0 * k for k in range(-3, 4)]:
        Lc = clip(L)
        s = probe(Lc)
        if s < best_S:
            best_L, best_S = Lc, s

    # Fine pass: +/-1 inch in 1/4-inch increments.
    for L in [best_L + 0.25 * k for k in range(-4, 5)]:
        Lc = clip(L)
        s = probe(Lc)
        if s < best_S:
            best_L, best_S = Lc, s

    de["length_in"] = round(best_L, 3)
    if log_fn:
        log_fn(f"  [resonance] DE {init:.2f}\" -> {best_L:.2f}\" "
               f"(centre SWR {best_S:.3f} at {fc_mhz:.3f} MHz)")
    return els
