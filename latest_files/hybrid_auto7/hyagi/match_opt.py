"""hybrid_auto7 — wideband impedance matcher.

The old generation step tuned one element at a time with greedy sweeps and got
trapped in local minima (it plateaued ~1.33:1 and could not reach the user's
wideband <=1.2:1 target across the freeband).

This module replaces that with a proper *coordinate-descent* optimiser whose
objective is the WORST (max) SWR across the whole band — the thing the user
actually cares about — using the fast SWR-only evaluator (no radiation pattern,
so ~3x faster).  It tunes the matching cell jointly (DE length, plus the
REF / XFRMR / COUPLER lengths and their spacings relative to the DE) and the
director lengths, at decreasing step sizes, looping each resolution until no
further improvement.  Optional perturbation restarts escape shallow minima.

Validated: drives band-max SWR from 3.44 -> ~1.20 across 26.665-27.855 MHz
where the old greedy procedure stalled at 1.33.
"""
from __future__ import annotations

import copy
import random

from . import v2_runner


class TuneStopped(Exception):
    """Raised by an on_move callback to abort the tune early.

    The page-side 'Stop' button sets a threading.Event; the live-status
    callback raises this exception when it sees the event is set.  Caught
    at the top of optimize() so the tune returns with the best geometry
    found at the time of the stop, instead of crashing or silently
    continuing past the user's wishes.
    """


def _el(elements, name):
    for e in elements:
        if str(e.get("name", "")).upper() == name:
            return e
    return None


def _spacing_bounds(rules, pair, default=(0.0, 9999.0)):
    sp = rules.get("spacings", {}).get(pair, {})
    lo = float(sp.get("min_in", default[0]))
    hi = float(sp.get("max_in", default[1]))
    return lo, hi


def _len_bounds(rules, name, default=(1.0, 9999.0)):
    r = rules.get("elements", {}).get(name, {})
    lo = float(r.get("length_min_in", default[0]))
    hi = float(r.get("length_max_in", default[1]))
    return lo, hi


# ---------------------------------------------------------------------------
# OWA (Optimized Wideband Antenna) stagger-tuned seed for the driven cell.
#
# Background -- WHY this exists.  A driven-cell hybrid (REF / XFRMR / DE /
# COUPLER) is a coupled-resonator network.  The XFRMR and COUPLER are NOT just
# match transformers; they are closely-coupled passive resonators sitting next
# to the DE.  If they happen to be tuned NEAR the same resonance as the DE the
# antenna behaves like one high-Q resonator -- single deep SWR dip, narrow band.
# If they are STAGGER-TUNED -- one a bit BELOW the operating centre, one a bit
# ABOVE -- the three resonances overlap into a broad, low-Q SWR trough, which is
# exactly how a real OWA covers 2-5 MHz of bandwidth on 11/10 m.
#
# A coordinate-descent matcher that only minimises the WORST SWR can stay stuck
# in the single-dip basin if it starts there -- every small move LOOKS bad
# because moving one element off-resonance lifts the centre SWR before the band
# edges come down.  So for any band wider than ~1 MHz we explicitly seed the
# three cell lengths into a stagger-tuned configuration first; the descent then
# converges into the correct basin.
#
# Physical model: in this code XFRMR and COUPLER are forced to be SHORTER than
# the DE (cap = de_len - 1 in _apply, see also v2_runner.validate); a shorter
# fat-tube element resonates HIGHER in frequency.  So to stagger:
#   - DE   -> resonant at the band centre (longest of the three)
#   - XFRMR -> resonant slightly HIGHER than centre (a bit shorter than DE)
#   - COUPLER -> resonant slightly higher still (shortest)
# With both helpers above centre, the band-low edge SWR is pulled down by the
# DE's natural skirt, and the band-high edge SWR is pulled down by the helpers'
# resonances.  Without this we cannot cover wide bands like 25-28 MHz.
# ---------------------------------------------------------------------------
def _stagger_lengths(de_len, f_low, f_high, fc):
    """Return target (de_len, xf_len, cp_len) stagger-tuned for the band.

    Empirical NEC2 study (scripts/hybrid_physics_study.py) confirmed the
    canonical OWA 3-dip wideband pattern requires asymmetric placement of
    the two helpers around DE:

      XFRMR resonant BELOW fc -> XFRMR LONGER than DE -> low-side dip
      DE     resonant AT fc
      COUPLER resonant ABOVE fc -> COUPLER SHORTER than DE -> high-side dip

    The previous code put both helpers above fc (both shorter than DE),
    which gave asymmetric coverage with no low-side helper.  The 3-dip
    sweet spot we observed: XFRMR ~ DE + 5\", COUPLER ~ DE - 10\".  Spread
    scales with the requested bandwidth; wider band -> wider stagger.
    Capped at +/-5% which is the practical OWA Q limit.
    """
    bw = max(0.0, float(f_high) - float(f_low))
    # Empirical stagger: from scripts/hybrid_physics_findings.md, the
    # working 3-dip configurations had:
    #   XFRMR ~ DE + 2..5"  (longer than DE -> resonant ~2% BELOW fc)
    #   COUPLER ~ DE - 10..17"  (much shorter -> resonant ~8% ABOVE fc)
    # The asymmetry is critical: a small XFRMR-above-DE offset adds the
    # low-side dip; a LARGE COUPLER-below-DE offset adds the high-side dip.
    # Mirror that here.  Spread scales with band width but never beyond the
    # NEC-verified safe ranges.
    spread = min(0.025, 0.4 * bw / max(fc, 1.0))
    # XFRMR: small offset below fc (2-3%) -> length DE * (1 + small)
    xf_freq_offset = min(0.022, spread)            # capped 2.2% below fc
    # COUPLER: large offset above fc (6-9%) -> length DE * (1 - large)
    cp_freq_offset = min(0.085, 3.5 * spread)      # capped 8.5% above fc
    f_xf = fc * (1.0 - xf_freq_offset)             # BELOW fc (XFRMR longer)
    f_cp = fc * (1.0 + cp_freq_offset)             # ABOVE fc (COUPLER shorter)
    # Length scales inversely with target resonance frequency (same diameter,
    # same height -> physical length ratio ~= fc / f_target).
    xf_target = de_len * (fc / f_xf)              # > de_len
    cp_target = de_len * (fc / f_cp)              # < de_len
    return de_len, xf_target, cp_target


def _apply_stagger_seed(elements, rules, f_low, f_high, fc, log_fn=None):
    """Place the driven cell in the canonical 3-dip OWA configuration.

    Stagger rule (data-driven, NOT the old 'both helpers above DE' rule):
      XFRMR  -> longer than DE  -> resonance BELOW design fc -> low-side dip
      DE     -> at design fc                                  -> centre
      COUPLER -> shorter than DE -> resonance ABOVE design fc  -> high-side dip

    f_low / f_high size the SPREAD; the design centre stays at user's fc.
    REF and directors are left alone (they own the beam shape).  No-op for
    narrow bands (<=1 MHz) and for arrays without an XFRMR/COUPLER.

    Honours rules.global.respect_seeded_cell:  when True, the user has
    explicitly seeded the cell via the Antenna Setup panel and we MUST NOT
    override their XFRMR / COUPLER lengths -- only the descent should touch
    them after that.  Default is False (the seed runs as before)."""
    if rules.get("global", {}).get("respect_seeded_cell"):
        if log_fn:
            log_fn("  [stagger-seed] respect_seeded_cell=True -- skipping the "
                   "wideband stagger seed (user's XFRMR/COUPLER lengths kept "
                   "as-is from the Antenna Setup seed panel).")
        return elements
    bw = float(f_high) - float(f_low)
    if bw <= 1.0:
        return elements
    design_fc = float(rules.get("global", {}).get("freq_mhz_center",
                                                  0.5 * (f_low + f_high))
                      or 0.5 * (f_low + f_high))

    de = _el(elements, "DE")
    xf = _el(elements, "XFRMR")
    cp = _el(elements, "COUPLER")
    if de is None or (xf is None and cp is None):
        return elements

    de_lo, de_hi = _len_bounds(rules, "DE", (185.0, 235.0))
    de_len_seed = min(de_hi, max(de_lo, float(de["length_in"])))
    _, xf_t, cp_t = _stagger_lengths(de_len_seed, f_low, f_high, design_fc)
    de["length_in"] = round(de_len_seed, 3)
    if xf is not None:
        # NEW: no de_len-1 cap.  Default bounds widened to 170..235 so a
        # DE+10 stagger seed (XFRMR > DE) isn't blocked by stale rule
        # bounds carried over from the 'XFRMR must be < DE' assumption.
        lo, hi = _len_bounds(rules, "XFRMR", (170.0, 235.0))
        xf["length_in"] = round(min(hi, max(lo, xf_t)), 3)
    if cp is not None:
        # COUPLER stays shorter than DE in the OWA pattern, but allow the
        # full bound range in case the user has a non-standard rule.
        lo, hi = _len_bounds(rules, "COUPLER", (150.0, 220.0))
        cp["length_in"] = round(min(hi, max(lo, cp_t)), 3)
    if log_fn:
        log_fn(f"  [stagger-seed] bw={bw:.2f} MHz  design_fc={design_fc:.3f}  "
               f"DE={de['length_in']:.2f}  "
               f"XFRMR={xf['length_in'] if xf else 0:.2f}  "
               f"COUPLER={cp['length_in'] if cp else 0:.2f}  "
               "(XFRMR longer than DE -> low-side dip; "
               "COUPLER shorter than DE -> high-side dip)")
    return elements


def _build_dofs(elements, rules, tune_spacings=False):
    """Return (vec, bounds, director_names). vec/bounds keyed by DOF name.

    Positions are parametrised as gaps relative to the DE so the DE stays put
    and only the matching cell moves; director positions are held fixed and
    only their lengths are tuned (keeps boom length / gain pattern stable).

    tune_spacings=True ("boom free") additionally makes each director's gap to
    the previous element a DOF, so the optimizer can move spacings / boom
    length too, bounded by the rules spacings."""
    de = _el(elements, "DE")
    if de is None:
        raise ValueError("geometry has no DE element")
    de_pos = float(de["position_in"])

    vec, bounds = {}, {}
    vec["de_len"] = float(de["length_in"])
    bounds["de_len"] = _len_bounds(rules, "DE")

    ref = _el(elements, "REF")
    if ref is not None:
        vec["ref_len"] = float(ref["length_in"])
        vec["ref_gap"] = de_pos - float(ref["position_in"])
        bounds["ref_len"] = _len_bounds(rules, "REF")
        bounds["ref_gap"] = _spacing_bounds(rules, "REF_DE", (30.0, 90.0))

    xf = _el(elements, "XFRMR")
    if xf is not None:
        vec["xf_len"] = float(xf["length_in"])
        vec["xf_gap"] = de_pos - float(xf["position_in"])
        bounds["xf_len"] = _len_bounds(rules, "XFRMR")
        bounds["xf_gap"] = _spacing_bounds(rules, "XFRMR_DE", (3.0, 34.0))

    cp = _el(elements, "COUPLER")
    if cp is not None:
        vec["cp_len"] = float(cp["length_in"])
        vec["cp_gap"] = float(cp["position_in"]) - de_pos
        bounds["cp_len"] = _len_bounds(rules, "COUPLER")
        bounds["cp_gap"] = _spacing_bounds(rules, "DE_COUPLER", (3.0, 34.0))

    director_names = [e["name"] for e in elements
                      if str(e["name"]).upper().startswith("DIR")]
    for d in director_names:
        vec[f"{d}_len"] = float(_el(elements, d)["length_in"])
        bounds[f"{d}_len"] = _len_bounds(rules, d, (140.0, 215.0))

    if tune_spacings:
        # Boom-free: each director's gap to the previous element becomes a DOF.
        ordered = sorted(director_names,
                         key=lambda n: float(_el(elements, n)["position_in"]))
        prev = _el(elements, "COUPLER") or de
        prev_pos = float(prev["position_in"])
        prev_name = str(prev["name"]).upper()
        for d in ordered:
            dpos = float(_el(elements, d)["position_in"])
            vec[f"sp_{d}"] = round(dpos - prev_pos, 4)
            bounds[f"sp_{d}"] = _spacing_bounds(rules, f"{prev_name}_{d}", (40.0, 120.0))
            prev_pos, prev_name = dpos, d.upper()

    return vec, bounds, de_pos


def _apply_spacings(e, vec):
    """Boom-free: reposition directors cumulatively from their learned gaps."""
    sp = {k[3:]: v for k, v in vec.items() if k.startswith("sp_")}
    if not sp:
        return
    dirs = sorted([x for x in e if str(x["name"]).upper().startswith("DIR")],
                  key=lambda x: float(x["position_in"]))
    anchor = _el(e, "COUPLER") or _el(e, "DE")
    prev_pos = float(anchor["position_in"])
    for d in dirs:
        g = sp.get(d["name"])
        if g is not None:
            prev_pos = round(prev_pos + g, 4)
            d["position_in"] = prev_pos
        else:
            prev_pos = float(d["position_in"])


def _apply(elements, vec, de_pos, rules=None):
    e = copy.deepcopy(elements)
    de = _el(e, "DE")
    de["length_in"] = vec["de_len"]
    # XFRMR / COUPLER must stay shorter than the DE (else the pattern reverses).
    cap = vec["de_len"] - 1.0
    if "ref_len" in vec:
        ref = _el(e, "REF")
        ref["length_in"] = vec["ref_len"]
        ref["position_in"] = round(de_pos - vec["ref_gap"], 4)
    if "xf_len" in vec:
        xf = _el(e, "XFRMR")
        xf["length_in"] = min(vec["xf_len"], cap)
        xf["position_in"] = round(de_pos - vec["xf_gap"], 4)
    if "cp_len" in vec:
        cp = _el(e, "COUPLER")
        cp["length_in"] = min(vec["cp_len"], cap)
        cp["position_in"] = round(de_pos + vec["cp_gap"], 4)
    for key, val in vec.items():
        if key.endswith("_len") and key[:-4].upper().startswith("DIR"):
            _el(e, key[:-4])["length_in"] = val
    _apply_spacings(e, vec)
    # ---- Endpoint pin (locked-boom mode) -----------------------------------
    # When the user has FIXED the boom to an exact length:
    #   * REF stays at position 0
    #   * Last director stays at exactly boom_max_in
    #   * Middle elements (XFRMR, DE, COUPLER, DIR1..DIR(N-1)) are free
    # to slide between them.  This block enforces those endpoint pins and a
    # minimum 6" spacing between adjacent elements so a runaway DOF can't
    # collapse elements on top of each other.
    cap_in = float((rules or {}).get("global", {}).get("boom_max_in") or 0.0)
    if cap_in > 0.0:
        # Hard-pin REF at 0.
        ref = _el(e, "REF")
        if ref is not None:
            ref["position_in"] = 0.0
        # Hard-pin the last director at the cap.
        dirs = sorted([x for x in e if str(x["name"]).upper().startswith("DIR")],
                      key=lambda x: float(x["position_in"]))
        if dirs:
            dirs[-1]["position_in"] = round(cap_in, 4)
        # Clamp every other element to [REF+min_sp, last_dir-min_sp] and keep
        # them strictly ordered by their current position, with at least
        # MIN_SP between neighbours.  Sliding is fine but order/coupling
        # sanity must hold.
        MIN_SP = 6.0
        movable = sorted(
            [x for x in e if str(x["name"]).upper() not in ("REF",)
             and not (dirs and x is dirs[-1])],
            key=lambda x: float(x["position_in"]),
        )
        prev = 0.0
        for k, m in enumerate(movable):
            remaining_slots = len(movable) - k        # how many still ahead
            hi = cap_in - remaining_slots * MIN_SP
            new_pos = max(prev + MIN_SP, min(hi, float(m["position_in"])))
            m["position_in"] = round(new_pos, 4)
            prev = new_pos
    # ---- Hard boom-length cap (defense in depth) ---------------------------
    # If the user locked the boom on Antenna Setup, rules.global.boom_max_in
    # carries the cap.  Coordinate descent should already keep positions
    # bounded by the endpoint pin above; this is a final safety net that
    # compresses any remaining overrun proportionally.
    if cap_in > 0.0:
        els = sorted(e, key=lambda el: float(el["position_in"]))
        p0 = float(els[0]["position_in"])
        span = float(els[-1]["position_in"]) - p0
        if span > cap_in + 0.5:
            scale = cap_in / span
            for el in els:
                el["position_in"] = round(p0 + (float(el["position_in"]) - p0) * scale, 4)
    return e


def _center_rx(curve, fc):
    """Interpolate (R, X) at the operating centre fc from a band SWR curve of
    (freq, R, X, swr) tuples (sorted ascending by freq)."""
    if not curve:
        return 50.0, 0.0
    if fc <= curve[0][0]:
        return curve[0][1], curve[0][2]
    if fc >= curve[-1][0]:
        return curve[-1][1], curve[-1][2]
    for i in range(1, len(curve)):
        if fc <= curve[i][0]:
            f0, r0, x0, _ = curve[i - 1]
            f1, r1, x1, _ = curve[i]
            t = 0.0 if f1 == f0 else (fc - f0) / (f1 - f0)
            return r0 + t * (r1 - r0), x0 + t * (x1 - x0)
    return curve[-1][1], curve[-1][2]


def _objective(elements, rules, height_ft, f_low, f_high, points, fc=None, goal="wideband"):
    curve, mx, av = v2_runner.band_swr_curve(elements, f_low, f_high, points, height_ft)
    if goal == "resonant":
        Rc, Xc = _center_rx(curve, fc if fc is not None else 0.5 * (f_low + f_high))
        csw = v2_runner.swr(Rc, Xc)
        # HIGH-POWER goal: a true resonant 50-ohm match AT the operating centre
        # -> R->50 and X->0 (low reactance for 50 kW+, high return loss, low SWR
        # at centre).  center SWR already encodes both R-from-50 and X; the extra
        # |Xc| term makes zero reactance a hard priority.  A light band term keeps
        # the wideband edges from blowing up.
        return csw + 0.04 * abs(Xc) + 0.30 * max(0.0, mx - 1.0), mx
    # ---- WIDEBAND / HYBRID priority ladder (user specification) -------------
    #
    #   1. Centre reactance X must sit in +/-2.5 ohm (resonant).  Inside this
    #      window the cost is small and lets the search wander to find the
    #      best SWR / RL combo; OUTSIDE the window the cost ramps hard so the
    #      matcher refuses to leave the resonant zone for a better band-edge.
    #
    #   2. Return loss (RL) at centre -- the higher the better -- gives a
    #      light reward.  Caps at 40 dB so an unrealistically deep null can't
    #      dominate the gain/F-B step that comes next.
    #
    #   3. SWR at centre target is 1.00:1; up to 1.07:1 is tolerated when X
    #      and RL would otherwise have to give up.  Encoded as the 4x centre
    #      pin we already had -- centre SWR 1.07 only costs +0.28 here, while
    #      centre SWR 2 costs +4.0, so the matcher will spend SWR slack first
    #      when it has to.
    #
    #   4. Band edges (mx + 0.05*av) are the LAST term -- a wide flat band is
    #      desirable but never at the cost of (1)-(3).  This is also what the
    #      auto-fit loop narrows when it can't be met.
    if fc is None:
        return mx + 0.05 * av, mx
    Rc, Xc = _center_rx(curve, fc)
    csw_centre = v2_runner.swr(Rc, Xc)
    abs_x = abs(Xc)
    # Reactance ladder: cheap up to +/-2.5, brutal beyond.
    if abs_x <= 2.5:
        x_term = 0.40 * abs_x                          # max 1.0 at +/-2.5
    else:
        x_term = 1.0 + 5.0 * (abs_x - 2.5)             # >>5x slope after that
    # Centre-SWR pin (priority 3).  The user's stated slack is "SWR needs to
    # be 1.0:1 but 1.07 is OK if X or RL require it".  So below 1.07 there's
    # NO penalty -- the matcher is then free to focus on band-max instead of
    # squeezing the last 0.05 out of an already-good centre.  Above 1.07 the
    # penalty climbs 4x per unit of overshoot, dominating quickly.
    swr_pin = 4.0 * max(0.0, csw_centre - 1.07)
    # Return-loss bonus (priority 2) -- small reward for high RL at centre.
    if csw_centre <= 1.0:
        rl_bonus = -2.0                                 # cap the bonus
    else:
        import math
        rl_db = -20.0 * math.log10((csw_centre - 1.0) / (csw_centre + 1.0))
        rl_bonus = -0.05 * min(40.0, rl_db)             # up to -2.0
    # Band edges (priority 4) -- last, lightest term.
    band_term = mx + 0.05 * av

    # NOTE: a 'multi-dip bonus' term was tried here (each SWR dip < 2:1 added
    # -0.30 to cost) but caused real-world regressions: a geometry with one
    # very deep dip at SWR 1.12 lost to a 3-dip 1.5/1.5/1.5 geometry because
    # the bonus (-0.9) outweighed the band-max delta.  The OWA multi-dip
    # response emerges naturally from the asymmetric stagger seed + the
    # descent's standard band-max minimisation; explicitly rewarding dip
    # count over-rewards mediocre matches.  Removed.

    return band_term + swr_pin + x_term + rl_bonus, mx


def _descend(vec, bounds, elements, de_pos, rules, height_ft, f_low, f_high,
             points, steps, target, log_fn, move_log=None, on_move=None,
             fc=None, goal="wideband", free_boom=False):
    best_obj, best_mx = _objective(_apply(elements, vec, de_pos, rules), rules,
                                   height_ft, f_low, f_high, points, fc, goal)
    keys = list(vec.keys())
    for step in steps:
        improved, rounds = True, 0
        while improved and rounds < 10:
            improved, rounds = False, rounds + 1
            for k in keys:
                lo, hi = bounds[k]
                # FREE-boom widens director-spacing DOFs 4x so coordinate
                # descent can actually walk the boom across its full rules
                # window (e.g. sp_DIR2 has 40-96" of room; with step=8 the
                # native probe is +-8 -- too small.  With 4x it's +-32, enough
                # to actually explore).  XFRMR/COUPLER cell gaps and lengths
                # use the normal step so the tight cell stays tight.
                dof_step = step
                if free_boom and (k.startswith("sp_") or k == "ref_gap"):
                    dof_step = step * 4.0
                for d in (dof_step, -dof_step):
                    nv = dict(vec)
                    nv[k] = round(min(hi, max(lo, vec[k] + d)), 3)
                    if abs(nv[k] - vec[k]) < 1e-9:
                        continue
                    new_els = _apply(elements, nv, de_pos, rules)
                    obj, mx = _objective(new_els, rules, height_ft,
                                         f_low, f_high, points, fc, goal)
                    accept = obj < best_obj - 1e-4
                    # Quick centre R/X read so the live status panel on the
                    # Tune & Learn page can show R, X, centre SWR, RL in
                    # real time without doing a second NEC solve.  Pull from
                    # the curve _objective already computed.
                    try:
                        _curve, _mx2, _av = v2_runner.band_swr_curve(
                            new_els, fc, fc, 1, height_ft)
                        if _curve:
                            cr = float(_curve[0][1])
                            cx = float(_curve[0][2])
                            csw = float(_curve[0][3])
                        else:
                            cr, cx, csw = 0.0, 0.0, 99.0
                    except Exception:
                        cr, cx, csw = 0.0, 0.0, 99.0
                    move = {"dof": k, "value": nv[k],
                            "band_max_swr": round(mx, 4),
                            "center_r": round(cr, 3),
                            "center_x": round(cx, 3),
                            "center_swr": round(csw, 4),
                            "accepted": 1 if accept else 0}
                    if move_log is not None:
                        move_log.append(move)
                    if on_move is not None:
                        on_move(move)
                    if accept:
                        best_obj, best_mx, vec, improved = obj, mx, nv, True
            if log_fn:
                log_fn(f"    [match:{goal}] step={step:>4} round={rounds} band_max_swr={best_mx:.3f}")
            # Early-out only in wideband mode (target is a band-SWR ceiling).
            if goal == "wideband" and best_mx <= target:
                return vec, best_obj, best_mx
    return vec, best_obj, best_mx


def _polish_gain(elements, rules, de_pos, height_ft, f_low, f_high, points,
                 target_swr, log_fn):
    """After SWR is under target, recover gain/F-B by re-tuning the passive
    elements (REF + director lengths) with the FULL pattern eval.

    User priority (rule 4): 'gain over F/B, but only if F/B is HIGH; if F/B is
    LOW lose some gain to recoup F/B.'  Encoded as an adaptive F/B weight that
    grows hard the lower F/B drops below 15 dB.  A trial move that would push
    F/B below 12 dB is rejected outright.  Above ~18 dB, F/B is fine and we
    weight gain more.  Move is also rejected if it would lift centre |X|
    above 2.5 ohm or band-max SWR back over target."""
    v2_runner.EVAL_FREQ_POINTS = max(7, int(points))
    keys = [e["name"] for e in elements
            if str(e["name"]).upper().startswith("DIR")
            or str(e["name"]).upper() == "REF"]
    if not keys:
        return elements

    def composite(els):
        m = v2_runner.evaluate(els, rules, height_ft=height_ft)
        if "error" in m:
            return -1e9, 99.0, 0.0, 0.0, 99.0
        fb = float(m.get("fb_db", 0.0))
        # Adaptive F/B weight: tiny when F/B already high, dominates when low.
        if fb >= 18.0:
            fb_w = 0.10
        elif fb >= 15.0:
            fb_w = 0.25
        elif fb >= 12.0:
            fb_w = 0.60
        else:
            fb_w = 1.20                  # below 12 dB, F/B dominates the score
        return (m["gain_dbi"] + fb_w * fb,
                m["max_swr"],
                fb,
                abs(float(m.get("center_x", 0.0))),
                float(m.get("center_swr", 99.0)))

    cur = copy.deepcopy(elements)
    best_score, _sw, _fb_now, _ax_now, _csw_now = composite(cur)
    for step in (2.0, 1.0):
        improved, rounds = True, 0
        while improved and rounds < 6:
            improved, rounds = False, rounds + 1
            for name in keys:
                lo, hi = _len_bounds(rules, name, (140.0, 240.0))
                el = _el(cur, name)
                base_len = float(el["length_in"])
                for d in (step, -step):
                    nl = round(min(hi, max(lo, base_len + d)), 3)
                    if abs(nl - base_len) < 1e-9:
                        continue
                    trial = copy.deepcopy(cur)
                    _el(trial, name)["length_in"] = nl
                    _curve, mx, _av = v2_runner.band_swr_curve(
                        trial, f_low, f_high, points, height_ft)
                    if mx > target_swr:
                        continue
                    sc, _, fb_t, ax_t, csw_t = composite(trial)
                    # Reject moves that violate priorities 1 (X) / 3 (SWR) /
                    # 4 (F/B floor) -- even if pure gain would go up.
                    if ax_t > 2.5:
                        continue
                    if csw_t > 1.07:
                        continue
                    if fb_t < 12.0:
                        continue
                    if sc > best_score + 1e-4:
                        best_score, cur, improved = sc, trial, True
                        base_len = nl
            if log_fn:
                gm = v2_runner.evaluate(cur, rules, height_ft=height_ft)
                log_fn(f"    [gain-polish] step={step} round={rounds} "
                       f"gain={gm.get('gain_dbi',0):.2f} "
                       f"fb={gm.get('fb_db',0):.2f} "
                       f"|X|={abs(float(gm.get('center_x',0))):.2f} "
                       f"csw={float(gm.get('center_swr',0)):.3f}")
    return cur


def _beam_metrics(els, rules, height_ft):
    m = v2_runner.evaluate(els, rules, height_ft=height_ft)
    return None if "error" in m else m


def _beam_score(m, fb_weight):
    # Reward gain + F/B, but keep the driving-point impedance in a matchable
    # range so the driven cell can still finish the wideband match (an
    # unconstrained beam wanders to a high-reactance point the cell can't fix).
    R = float(m.get("center_r", 50.0))
    X = float(m.get("center_x", 0.0))
    pen = 0.0
    if R < 22.0:
        pen += (22.0 - R) * 0.30
    if R > 110.0:
        pen += (R - 110.0) * 0.10
    pen += max(0.0, abs(X) - 55.0) * 0.10
    return m["gain_dbi"] + fb_weight * m["fb_db"] - pen


def _pos_bounds(cur, name, rules):
    """Allowed boom-position range for a beam element, from the rules spacings,
    keeping element order (reflector behind DE; directors after their inboard
    neighbour and before the next one)."""
    nm = str(name).upper()
    de_pos = float(_el(cur, "DE")["position_in"])
    if nm == "REF":
        lo_g, hi_g = _spacing_bounds(rules, "REF_DE", (30.0, 90.0))
        return de_pos - hi_g, de_pos - lo_g
    dirs = sorted([e for e in cur if str(e["name"]).upper().startswith("DIR")],
                  key=lambda e: float(e["position_in"]))
    names = [str(d["name"]).upper() for d in dirs]
    idx = names.index(nm)
    prev = (_el(cur, "COUPLER") or _el(cur, "DE")) if idx == 0 else dirs[idx - 1]
    prev_pos = float(prev["position_in"])
    lo_g, hi_g = _spacing_bounds(rules, f"{str(prev['name']).upper()}_{nm}", (40.0, 120.0))
    lo, hi = prev_pos + lo_g, prev_pos + hi_g
    if idx < len(dirs) - 1:
        hi = min(hi, float(dirs[idx + 1]["position_in"]) - 12.0)
    return lo, max(lo, hi)


def _optimize_beam(elements, rules, height_ft, tune_spacings, log_fn,
                   fb_weight=0.3, f_low=None, f_high=None, points=None,
                   target_swr=None, free_boom=False):
    """Tune the BEAM (REF + directors) for max gain + F/B, leaving the cell
    to the match phase.  This is the hybrid's whole point: REF+directors
    shape the beam, the driven cell handles the wideband match, so
    directors are NOT shortened to chase SWR.

    CO-OPTIMIZATION GUARD: every trial move ALSO checks band-max SWR.
    A beam move that would push band-max above the SWR ceiling is rejected
    even if it improves gain / F-B.  Ceiling = max(target_swr * 1.5, 1.5)
    -- a 50% slack above the user's target (or a 1.5:1 floor for very
    tight targets like 1.02) gives beam room to explore without letting
    it shred the match.  Previous 15% slack was too restrictive and made
    the beam phase reject most moves on tight targets.

    fb_weight=0.3 (was 0.5): the beam objective is gain + fb_weight * F/B.
    Lowering the F/B weight makes gain dominate more trade-offs.  Users
    were seeing slight gain drops (14.15 -> 14.05) with the 0.5 weight
    because F/B improvements at the cost of small gain hits scored higher.
    0.3 means it takes ~3 dB of F/B improvement to justify a 1 dB gain
    drop instead of ~2 dB -- closer to bench operator instinct.
    """
    prev_pts = getattr(v2_runner, "EVAL_FREQ_POINTS", 3)
    v2_runner.EVAL_FREQ_POINTS = 1
    try:
        cur = copy.deepcopy(elements)
        m = _beam_metrics(cur, rules, height_ft)
        if m is None:
            return elements
        best = _beam_score(m, fb_weight)
        dirs = [e["name"] for e in cur if str(e["name"]).upper().startswith("DIR")]
        len_names = (["REF"] if _el(cur, "REF") else []) + dirs

        # SWR ceiling -- generous so the beam phase actually has room to
        # explore.  Was target * 1.15 which choked the search on tight
        # targets (1.05 -> ceiling 1.21, too narrow).  Now target * 1.5
        # with a 1.5 absolute floor.
        swr_ceiling = (max(float(target_swr) * 1.5, 1.5)
                       if target_swr is not None
                       and f_low is not None and f_high is not None
                       and points is not None
                       else None)

        def _swr_ok(trial):
            if swr_ceiling is None:
                return True
            try:
                _c, mx, _av = v2_runner.band_swr_curve(
                    trial, f_low, f_high, points, height_ft)
                return mx <= swr_ceiling
            except Exception:
                return False

        rej_swr = 0
        for step in (2.0, 1.0, 0.5):
            improved, rounds = True, 0
            while improved and rounds < 5:
                improved, rounds = False, rounds + 1
                for name in len_names:
                    el = _el(cur, name)
                    lo, hi = _len_bounds(rules, name, (140.0, 240.0))
                    base = float(el["length_in"])
                    for d in (step, -step):
                        nl = round(min(hi, max(lo, base + d)), 3)
                        if abs(nl - base) < 1e-9:
                            continue
                        trial = copy.deepcopy(cur)
                        _el(trial, name)["length_in"] = nl
                        mm = _beam_metrics(trial, rules, height_ft)
                        if not mm:
                            continue
                        if _beam_score(mm, fb_weight) <= best + 1e-4:
                            continue
                        if not _swr_ok(trial):
                            rej_swr += 1
                            continue
                        best, cur, improved = _beam_score(mm, fb_weight), trial, True
                pos_names = (["REF"] if _el(cur, "REF") else [])
                if tune_spacings:
                    pos_names += dirs
                # FREE-boom widens position probes 4x so the beam phase can
                # actually walk director positions across their rules window
                # (sp_DIR ranges 40-120").  Without this, the +-2/+-1/+-0.5
                # probes were 100% rejected because they couldn't escape
                # the local SWR basin -- see user's tune log showing
                # 14 + 38 + 52 rejections per beam step.
                pos_step = step * 4.0 if free_boom else step
                for name in pos_names:
                    base = float(_el(cur, name)["position_in"])
                    lo, hi = _pos_bounds(cur, name, rules)
                    for d in (pos_step, -pos_step):
                        npos = round(min(hi, max(lo, base + d)), 3)
                        if abs(npos - base) < 1e-9:
                            continue
                        trial = copy.deepcopy(cur)
                        _el(trial, name)["position_in"] = npos
                        mm = _beam_metrics(trial, rules, height_ft)
                        if not mm:
                            continue
                        if _beam_score(mm, fb_weight) <= best + 1e-4:
                            continue
                        if not _swr_ok(trial):
                            rej_swr += 1
                            continue
                        best, cur, improved = _beam_score(mm, fb_weight), trial, True
            if log_fn:
                mm = _beam_metrics(cur, rules, height_ft) or {}
                ceiling_msg = (f" ceiling {swr_ceiling:.2f}"
                               if swr_ceiling else "")
                rej_msg = (f"  rej-swr {rej_swr}" if rej_swr else "")
                log_fn(f"    [beam] step={step} gain={mm.get('gain_dbi', 0):.2f} "
                       f"fb={mm.get('fb_db', 0):.2f}{rej_msg}{ceiling_msg}")
        return cur
    finally:
        v2_runner.EVAL_FREQ_POINTS = prev_pts


def _match_cell(cur, rules, height_ft, f_low, f_high, points, target, fc, steps,
                log_fn, move_log, on_move):
    """Tune ONLY the driven matching cell (DE / XFRMR / COUPLER lengths + gaps)
    for minimum band-max SWR, holding the reflector + directors fixed."""
    vec, bounds, de_pos = _build_dofs(cur, rules, tune_spacings=False)
    for k in list(vec):                       # keep only the driven matching cell
        base = k[:-4] if k.endswith(("_len", "_gap")) else k
        if (k.startswith("sp_") or base.upper().startswith("DIR")
                or base in ("ref",)):
            vec.pop(k, None)
            bounds.pop(k, None)
    best_vec, _o, mx = _descend(vec, bounds, cur, de_pos, rules, height_ft,
                                f_low, f_high, points, steps, target, log_fn,
                                move_log, on_move, fc=fc, goal="wideband")
    return _apply(cur, best_vec, de_pos, rules), mx


def _hybrid_overall(els, rules, height_ft, f_low, f_high, points, target):
    """Combined hybrid quality: strong beam (gain + F/B) minus a penalty for any
    SWR above target. Used to guarantee the hybrid run never returns something
    worse than the cell-only-match baseline."""
    m = v2_runner.evaluate(els, rules, height_ft=height_ft)
    if "error" in m:
        return -1e9, 99.0
    _curve, mx, _av = v2_runner.band_swr_curve(els, f_low, f_high, points, height_ft)
    swr_pen = max(0.0, mx - target) * 12.0
    return m["gain_dbi"] + 0.4 * m["fb_db"] - swr_pen, mx


def _optimize_hybrid(elements, rules, height_ft, target_swr, points, f_low,
                     f_high, fc, tune_spacings, steps, log_fn, move_log, on_move,
                     iters=3, free_boom=False):
    """Hybrid optimiser, made robust:

    1. baseline = match the DRIVEN CELL only (directors frozen) -> keeps the
       beam the user built and flattens SWR with the cell, the way a hybrid is
       meant to work.
    2. then try to IMPROVE the beam (reflector + directors -> more gain/F-B,
       matchability-guarded) and re-match the cell, keeping the change only if
       the combined quality goes up. Never returns worse than the baseline.
    """
    cur = copy.deepcopy(elements)
    cur, _mx = _match_cell(cur, rules, height_ft, f_low, f_high, points,
                           target_swr, fc, steps, log_fn, move_log, on_move)
    best = copy.deepcopy(cur)
    best_score, best_mx = _hybrid_overall(best, rules, height_ft, f_low, f_high,
                                          points, target_swr)
    if log_fn:
        gm = v2_runner.evaluate(best, rules, height_ft=height_ft)
        log_fn(f"  [hybrid] baseline (cell match): band_max_swr={best_mx:.3f}  "
               f"gain={gm.get('gain_dbi', 0):.2f}  fb={gm.get('fb_db', 0):.2f}")

    # "Already good enough" early-exit.  If the baseline is already
    # comfortably under the user's target SWR (with 20% slack), iterations
    # are unlikely to improve it and just waste 30-50 minutes.  This is
    # exactly the user's case: baseline 1.12 vs target 1.03 with a near-
    # perfectly-tuned warm-start geometry -- the matcher should report
    # 'baseline is near-optimal, skipping iterations' instead of grinding.
    if best_mx <= float(target_swr) * 1.2:
        if log_fn:
            log_fn(f"  [hybrid] baseline band-max {best_mx:.3f} is within "
                   f"20% of target {target_swr:.2f} -- the warm-start "
                   f"geometry is already near-optimal.  Skipping the "
                   f"{iters}-iteration beam/match loop.  If you want more "
                   f"aggressive optimisation, set a lower target SWR or "
                   f"a wider band.")
        curve, mx, _av = v2_runner.band_swr_curve(best, f_low, f_high,
                                                  points, height_ft)
        return best, mx, curve

    # FREE-boom: between iterations we need to PERTURB the director
    # positions so each iteration genuinely explores a new boom basin.
    # Without this, beam->cell->beam->cell just polishes the same local
    # minimum every iteration.  Perturbation magnitude is half the rules
    # spacing range per director gap (e.g. sp_DIR2 40-96" gives +-14"
    # jumps), enough to land in a different basin while staying within
    # physically reasonable bounds.  XFRMR / COUPLER positions are NOT
    # touched (tight cell stays tight).
    import random as _random
    _rng = _random.Random(0xB00F)

    def _perturb_directors(els, magnitude=0.50):
        out = copy.deepcopy(els)
        de = _el(out, "DE")
        if de is None:
            return out
        dirs_sorted = sorted(
            [e for e in out if str(e["name"]).upper().startswith("DIR")],
            key=lambda e: float(e["position_in"]),
        )
        anchor = _el(out, "COUPLER") or de
        prev_pos = float(anchor["position_in"])
        prev_name = str(anchor["name"]).upper()
        for d in dirs_sorted:
            lo_g, hi_g = _spacing_bounds(
                rules, f"{prev_name}_{d['name'].upper()}", (48.0, 96.0))
            cur_gap = float(d["position_in"]) - prev_pos
            jump = (hi_g - lo_g) * magnitude
            new_gap = cur_gap + _rng.uniform(-jump, jump)
            new_gap = max(lo_g, min(hi_g, new_gap))
            prev_pos = round(prev_pos + new_gap, 4)
            d["position_in"] = prev_pos
            prev_name = str(d["name"]).upper()
        return out

    for it in range(iters):
        # Between iters in FREE mode: jump to a different boom basin so
        # the next beam+cell pass explores genuinely new territory.
        if free_boom and it > 0:
            cur = _perturb_directors(cur, magnitude=0.50)
            if log_fn:
                p_span = (max(float(e["position_in"]) for e in cur)
                          - min(float(e["position_in"]) for e in cur))
                log_fn(f"  [hybrid] iter {it + 1} -- FREE perturbation: "
                       f"jumped director gaps by ~50% of their rules range "
                       f"-> new starting boom {p_span:.2f}\".  Beam + cell "
                       f"match will now refine from this fresh basin.")
        trial = _optimize_beam(cur, rules, height_ft, tune_spacings, log_fn,
                               f_low=f_low, f_high=f_high, points=points,
                               target_swr=target_swr, free_boom=free_boom)
        trial, mx = _match_cell(trial, rules, height_ft, f_low, f_high, points,
                                target_swr, fc, steps, log_fn, move_log, on_move)
        score, mx = _hybrid_overall(trial, rules, height_ft, f_low, f_high,
                                    points, target_swr)
        gm = v2_runner.evaluate(trial, rules, height_ft=height_ft)
        if log_fn:
            try:
                t_span = (max(float(e["position_in"]) for e in trial)
                          - min(float(e["position_in"]) for e in trial))
                log_fn(f"  [hybrid] iter {it + 1}: band_max_swr={mx:.3f}  "
                       f"gain={gm.get('gain_dbi', 0):.2f}  "
                       f"fb={gm.get('fb_db', 0):.2f}  "
                       f"boom={t_span:.2f}\"  score={score:+.2f} "
                       f"(best {best_score:+.2f})")
            except Exception:
                log_fn(f"  [hybrid] iter {it + 1}: band_max_swr={mx:.3f}  "
                       f"gain={gm.get('gain_dbi', 0):.2f}  fb={gm.get('fb_db', 0):.2f}  "
                       f"score={score:+.2f} (best {best_score:+.2f})")
        if score > best_score + 1e-3:
            best, best_score, best_mx = copy.deepcopy(trial), score, mx
            cur = trial
        elif free_boom:
            # FREE mode: keep iterating even if this jump didn't beat
            # the current best -- the next perturbation might find a
            # better basin.  Break only on the explicit final iter.
            continue
        else:
            break                              # no further improvement -> stop
    curve, mx, _av = v2_runner.band_swr_curve(best, f_low, f_high, points, height_ft)
    return best, mx, curve


def optimize(elements, rules, height_ft=30.0, target_swr=1.2,
             points=21, restarts=2, steps=(8.0, 4.0, 2.0, 1.0, 0.5, 0.25),
             seed=12345, polish_gain=True, log_fn=print,
             learned_start=None, move_log=None, on_move=None, goal="wideband",
             tune_spacings=False):
    """Minimise band-max SWR across the band in rules['global'], then recover
    gain/F-B while holding the match.

    goal="wideband"  -> drive the WORST in-band SWR down (default).
    goal="resonant"  -> drive a true 50-ohm resonant match (R->50, X->0) AT the
                        operating centre (high-power: low reactance, high return
                        loss, low centre SWR), keeping the band edges in check.
    goal="hybrid"    -> alternate a BEAM phase (reflector + directors -> max
                        gain/F-B) with a MATCH phase (driven cell -> min wideband
                        SWR); keeps a strong pattern AND a flat wideband match.

    tune_spacings=True -> "boom free": also move director spacings/positions
                          (boom length floats), not just element lengths.

    learned_start: {dof: value} proven-good values from past runs to seed from.
    move_log: list that receives every candidate {dof,value,band_max_swr,accepted}.
    on_move: callback(move) fired for every candidate so runs can persist moves
             live (so nothing is lost if the run is stopped or errors).
    Returns (best_elements, best_max_swr, curve)."""
    glb = rules["global"]
    f_low = float(glb["freq_mhz_low"])
    f_high = float(glb["freq_mhz_high"])
    fc = float(glb.get("freq_mhz_center", 0.5 * (f_low + f_high)))

    # ---- RESONANCE PRE-PASS ------------------------------------------------
    # 'Cut DE for resonance first' -- what any experienced bench operator
    # does before worrying about gain / F-B / bandwidth.  If the input
    # geometry's centre SWR is above 3 at fc (i.e. the antenna is way off
    # the user's chosen centre frequency), sweep DE length until centre
    # SWR drops below 3 BEFORE the wideband descent runs.  Costs ~25 NEC
    # solves (~10-15 seconds at 26 ft) and saves the matcher 100+ probed
    # candidates that would otherwise be evaluated at horrible band-edge
    # SWR values.
    try:
        _curve_pre, _mx_pre, _av_pre = v2_runner.band_swr_curve(
            elements, fc, fc, 1, height_ft)
        centre_swr_pre = float(_curve_pre[0][3]) if _curve_pre else 99.0
    except Exception:
        centre_swr_pre = 99.0
    if centre_swr_pre > 3.0:
        try:
            from . import resonance
            if log_fn:
                log_fn(f"  [resonance-pre-pass] centre SWR {centre_swr_pre:.2f} "
                       f"at {fc:.3f} MHz -- tuning DE length for resonance "
                       f"first (cut DE before chasing bandwidth)")
            elements = resonance.find_de_resonance(
                elements, fc_mhz=fc, height_ft=height_ft,
                rules=rules, log_fn=log_fn,
            )
        except Exception as ex:
            if log_fn:
                log_fn(f"  [resonance-pre-pass] skipped: {ex}")
    elif log_fn:
        log_fn(f"  [resonance-pre-pass] centre SWR {centre_swr_pre:.2f} "
               f"at {fc:.3f} MHz -- already at resonance, skipping DE "
               f"pre-tune (no action needed).")

    # ---- HARD CAP: enforce the user's locked boom length up-front -----------
    # New spec (user, latest): FIXED + cap means the boom is EXACTLY the cap
    # length -- REF locked at position 0, last director locked at exactly the
    # cap, middle elements free to slide.  So whether the starting geometry is
    # longer OR shorter than the cap, we RESCALE positions uniformly so the
    # endpoints sit on those two values.  No-op when FREE (cap == 0).
    cap_in = float(glb.get("boom_max_in") or 0.0)
    lock_endpoints = cap_in > 0.0
    if lock_endpoints and elements:
        elements = copy.deepcopy(elements)
        els_sorted = sorted(elements, key=lambda e: float(e["position_in"]))
        p0 = float(els_sorted[0]["position_in"])
        span = float(els_sorted[-1]["position_in"]) - p0
        if span > 0:
            for el in elements:
                # Map [p0, p0+span] -> [0, cap_in] uniformly.
                el["position_in"] = round(
                    (float(el["position_in"]) - p0) * cap_in / span, 4)
            if log_fn and abs(span - cap_in) > 0.5:
                log_fn(f"  [boom-lock] starting geometry span {span:.2f}\" "
                       f"-> rescaled to exactly {cap_in:.2f}\" "
                       f"(REF at 0, last DIR at {cap_in:.2f}\").")

    # ---- FREE MODE: reseed director spacings to rules midpoints -------------
    # When the user has FREE'd the boom AND asked the tuner to move spacings
    # (tune_spacings=True), the director positions in the input geometry
    # are LEFTOVER from the previous FIXED-mode tune (e.g. user had FIXED
    # cap = 294", tune ran, geometry was adopted to disk, user then
    # switched mode to FREE -- the on-disk geometry still has the 294"
    # boom from the FIXED run).  FREE means "the optimizer should pick the
    # boom length", so we discard those leftover director positions and
    # reseed to the rules MIDPOINTS as a neutral starting layout.  The
    # optimizer then descends from this fresh start to find the actual
    # best boom length.
    #
    # IMPORTANT -- HYBRID/OWA CELL STAYS TIGHT.  This reseed touches ONLY
    # the REF gap and the director-to-director gaps.  The XFRMR_DE and
    # DE_COUPLER spacings (4-32" tight-cell rules) are intentionally NOT
    # rewritten -- the hybrid wideband performance comes from the tightly
    # coupled XFRMR / DE / COUPLER resonator triple, which the user has
    # already tuned to its operating sweet-spot (~5-7").  Director spacings
    # are the only DOFs being repositioned here.
    #
    # After repositioning, the whole array is shifted so the leftmost
    # element sits at position 0 (matches the conventional REF=0 layout
    # the rest of the app expects).
    if not lock_endpoints and tune_spacings and elements:
        elements = copy.deepcopy(elements)
        de = _el(elements, "DE")
        if de is not None:
            de_pos = float(de["position_in"])
            # Re-seed REF gap to midpoint of REF_DE spacing rules.
            ref = _el(elements, "REF")
            if ref is not None:
                lo_g, hi_g = _spacing_bounds(rules, "REF_DE", (40.0, 80.0))
                ref["position_in"] = round(de_pos - 0.5 * (lo_g + hi_g), 4)
            # Re-seed director gaps cumulatively from COUPLER (or DE) using
            # the midpoint of each pair's spacing rule.  XFRMR / COUPLER
            # positions are NOT touched here -- the tight cell stays tight.
            dirs = sorted(
                [e for e in elements if str(e["name"]).upper().startswith("DIR")],
                key=lambda e: float(e["position_in"]),
            )
            anchor = _el(elements, "COUPLER") or de
            prev_pos = float(anchor["position_in"])
            prev_name = str(anchor["name"]).upper()
            for d in dirs:
                lo_g, hi_g = _spacing_bounds(
                    rules, f"{prev_name}_{d['name'].upper()}", (48.0, 96.0))
                gap = 0.5 * (lo_g + hi_g)
                prev_pos = round(prev_pos + gap, 4)
                d["position_in"] = prev_pos
                prev_name = str(d["name"]).upper()
            # Shift so the leftmost element lands at position 0 (convention:
            # REF anchors the boom).  Preserves every gap exactly.
            min_pos = min(float(e["position_in"]) for e in elements)
            if abs(min_pos) > 1e-6:
                for e in elements:
                    e["position_in"] = round(float(e["position_in"]) - min_pos, 4)
            if log_fn:
                new_span = max(float(e["position_in"]) for e in elements) - \
                           min(float(e["position_in"]) for e in elements)
                log_fn(f"  [boom-free] FRESH START -- discarded leftover "
                       f"director positions from disk (these were from your "
                       f"previous FIXED tune).  Reseeded director gaps to "
                       f"rules midpoints (XFRMR/COUPLER cell untouched) -> "
                       f"starting span {new_span:.2f}\".  Optimizer free to "
                       f"grow or shrink from here -- result will NOT revert "
                       f"to the old disk geometry.")

    if goal == "hybrid":
        # Stagger-tune the driven cell when the band is wide -- positions the
        # three coupled resonators in the right basin for multi-MHz coverage
        # (see _stagger_lengths docstring).  No-op for narrow bands / no XFRMR.
        elements = _apply_stagger_seed(copy.deepcopy(elements), rules,
                                       f_low, f_high, fc, log_fn=log_fn)
        # WIDE-BAND HYBRID: > 1.5 MHz is beyond what locking the directors can
        # do.  Physically the array's resonant centre must move with the band,
        # which the cell-only matcher cannot achieve.  Fall through to the
        # wideband matcher (all element lengths free) and then polish the gain
        # under an SWR ceiling -- the user gets a real OWA flat-SWR result
        # instead of the locked SWR ~3 the cell-only hybrid produces.
        if (f_high - f_low) > 1.5:
            if log_fn:
                log_fn("  [hybrid] band > 1.5 MHz -> using wide-hybrid path "
                       "(full-array wideband match + gain polish under SWR ceiling)")
            goal = "wideband"
            polish_gain = True
        else:
            return _optimize_hybrid(elements, rules, height_ft, target_swr, points,
                                    f_low, f_high, fc, tune_spacings, steps, log_fn,
                                    move_log, on_move, iters=max(2, int(restarts) + 1),
                                    free_boom=not lock_endpoints)

    if goal == "wideband":
        elements = _apply_stagger_seed(copy.deepcopy(elements), rules,
                                       f_low, f_high, fc, log_fn=log_fn)

    vec0, bounds, de_pos = _build_dofs(elements, rules, tune_spacings=tune_spacings)
    if learned_start:
        n = 0
        for k, v in learned_start.items():
            if k in vec0:
                lo, hi = bounds[k]
                vec0[k] = round(min(hi, max(lo, float(v))), 3)
                n += 1
        if log_fn and n:
            log_fn(f"  [learn] warm-started {n} parameter(s) from past best moves")
    rng = random.Random(seed)

    best_vec, best_obj, best_mx = _descend(
        dict(vec0), bounds, elements, de_pos, rules, height_ft,
        f_low, f_high, points, steps, target_swr, log_fn, move_log, on_move,
        fc=fc, goal=goal, free_boom=not lock_endpoints)
    if log_fn:
        log_fn(f"  [match:{goal}] base pass -> band_max_swr={best_mx:.3f}")

    # ---- BASELINE SAFETY ---------------------------------------------------
    # Snapshot the input geometry so we can REVERT to it if the descent +
    # auto-fit + restarts end up at a worse band-max.  User hit this exact
    # case with a 6-hour ±2 MHz wideband tune: baseline 1.116, after 6 hours
    # the matcher returned 1.493.  The optimizer must NEVER return a result
    # worse than what it started with.  Evaluated over the ORIGINAL band
    # the user requested, not whatever auto-fit narrowed to.
    baseline_geom = copy.deepcopy(elements)
    baseline_f_low, baseline_f_high = f_low, f_high
    try:
        _bc, baseline_mx, _bav = v2_runner.band_swr_curve(
            baseline_geom, baseline_f_low, baseline_f_high, points, height_ft)
    except Exception:
        baseline_mx = float("inf")

    # Restart to escape shallow minima. Wideband restarts only while still above
    # target; resonant always uses the full restart budget (centre match is hard).
    # For OWA-wide bands widen the perturbation on the driven cell AND directors
    # so restarts can JUMP between stagger-tune basins.  Directors are NOT
    # exempted here -- user reports of physical antennas covering 2.7 MHz at
    # SWR <= 1.3 mean such basins exist; the matcher must actually search for
    # them.  Previous 'directors mostly alone' policy kept us locked near the
    # warm-start cell which was the local minimum the user kept hitting.
    bw_mhz = float(f_high) - float(f_low)
    # Wider bands -> more aggressive perturbation:
    cell_span = 0.60 if bw_mhz > 1.5 else (0.40 if bw_mhz > 1.0 else 0.20)
    dir_span  = 0.30 if bw_mhz > 1.5 else (0.20 if bw_mhz > 1.0 else 0.08)
    # FREE-boom: the user is explicitly asking the optimizer to explore
    # different boom lengths.  Crank up the director-perturbation magnitude
    # so each restart actually JUMPS between boom-length basins instead
    # of nudging by a few inches.  0.50 = +-50% of the rules range for
    # each director gap (e.g. sp_DIR2 ranges 40-96", so a perturbation
    # of +-28" -- enough to escape a local minimum).  Cell perturbation
    # is also widened so the matcher can pair the new director geometry
    # with a re-tuned XFRMR / COUPLER position.
    if not lock_endpoints:
        cell_span = max(cell_span, 0.50)
        dir_span = max(dir_span, 0.50)
    r = 0
    while r < restarts and (goal == "resonant" or best_mx > target_swr):
        r += 1
        pv = dict(best_vec)
        for k in pv:
            lo, hi = bounds[k]
            base = k[:-4] if k.endswith(("_len", "_gap")) else k
            is_cell = base.lower() in ("de", "xf", "cp", "ref")
            is_dir = base.upper().startswith("DIR")
            if is_cell:
                frac = cell_span
            elif is_dir:
                frac = dir_span                  # directors NOW perturb too
            else:
                frac = 0.15
            span = (hi - lo) * frac
            pv[k] = round(min(hi, max(lo, pv[k] + rng.uniform(-span, span))), 3)
        v, o, mx = _descend(pv, bounds, elements, de_pos, rules, height_ft,
                            f_low, f_high, points, steps, target_swr, log_fn,
                            move_log, on_move, fc=fc, goal=goal,
                            free_boom=not lock_endpoints)
        if log_fn:
            # Report the actual boom span of the restart's converged result
            # so the user can SEE the optimizer trying different boom
            # lengths instead of just numbers in a log.
            v_elems = _apply(elements, v, de_pos, rules)
            try:
                v_span = (max(float(e["position_in"]) for e in v_elems)
                          - min(float(e["position_in"]) for e in v_elems))
                log_fn(f"  [match:{goal}] restart {r} -> "
                       f"band_max_swr={mx:.3f}  boom={v_span:.2f}\"")
            except Exception:
                log_fn(f"  [match:{goal}] restart {r} -> band_max_swr={mx:.3f}")
        if o < best_obj - 1e-4:
            best_vec, best_obj, best_mx = v, o, mx

    best_elements = _apply(elements, best_vec, de_pos, rules)

    # Recover gain/F-B (wideband goal) -- ALWAYS run polish_gain after the
    # descent has settled; the polish step itself rejects moves that violate
    # the SWR ceiling so it can't make band-max worse, and crucially it
    # enforces the F/B floor (12 dB by default).  Old code only polished when
    # best_mx <= target_swr, so a tune that auto-fit to band-max 1.336 with
    # target 1.20 skipped polish entirely -> F/B 7.84 dB.  Now polish always
    # runs once the descent has converged.  In resonant mode we still skip
    # polish to avoid disturbing the centre match.
    if polish_gain and goal == "wideband":
        # Use the achieved band-max as the SWR ceiling for polish so it can't
        # make things worse, even when target_swr wasn't hit.
        ceiling = max(float(target_swr), float(best_mx))
        best_elements = _polish_gain(best_elements, rules, de_pos, height_ft,
                                     f_low, f_high, points, ceiling, log_fn)

    # ---- AUTO-FIT BANDWIDTH ------------------------------------------------
    # User instruction: 'it needs to work outwards from my center frequency
    # regardless ... if 6 MHz is too large and the freq drifts then only go
    # as far as it can work and stay within my specs. make it smart not stupid'.
    #
    # If the requested half-width is physically too aggressive (band-max SWR
    # missed the target by more than ~5%), narrow the band 20% at a time
    # toward the centre and re-run descent from the current best.  Centre is
    # held by the centre-pin penalty in _objective(), so the antenna's natural
    # resonance stays at fc; only the BAND we ask the matcher to flatten
    # shrinks until it CAN meet the user's target SWR.  Reports the band that
    # actually came true.
    if goal == "wideband":
        attempt = 0
        achieved_hw = 0.5 * (f_high - f_low)
        target_swr_with_slack = float(target_swr) * 1.05
        prev_mx = best_mx
        no_progress = 0
        # Cap at 3 retries (was 6) -- each retry is a full descent + polish,
        # ~15-20 min for 401-point sweeps.  Six retries gave a 6-hour run for
        # the user with zero net improvement.  Three is plenty.
        while (best_mx > target_swr_with_slack
               and achieved_hw > 0.10
               and attempt < 3):
            attempt += 1
            achieved_hw *= 0.80
            f_low = fc - achieved_hw
            f_high = fc + achieved_hw
            rules["global"]["freq_mhz_low"] = f_low
            rules["global"]["freq_mhz_high"] = f_high
            if log_fn:
                log_fn(f"  [auto-fit] band-max {best_mx:.3f} > target {target_swr:.2f} "
                       f"-- narrowing to +/-{achieved_hw:.2f} MHz "
                       f"({f_low:.3f}-{f_high:.3f} MHz) and re-tuning")
            best_vec, best_obj, best_mx = _descend(
                dict(best_vec), bounds, best_elements, de_pos, rules, height_ft,
                f_low, f_high, points, steps, target_swr, log_fn, move_log, on_move,
                fc=fc, goal=goal, free_boom=not lock_endpoints)
            best_elements = _apply(best_elements, best_vec, de_pos, rules)
            if polish_gain:
                ceiling = max(float(target_swr), float(best_mx))
                best_elements = _polish_gain(
                    best_elements, rules, de_pos, height_ft,
                    f_low, f_high, points, ceiling, log_fn)
            if log_fn:
                log_fn(f"  [auto-fit] retry {attempt} -> band_max_swr={best_mx:.3f}")
            # Early-exit if a retry didn't materially improve band-max.
            # Two consecutive retries with <2% improvement -> stop.
            if best_mx >= prev_mx * 0.98:
                no_progress += 1
                if no_progress >= 2:
                    if log_fn:
                        log_fn(f"  [auto-fit] no progress for 2 retries in a row "
                               f"-- stopping the narrowing.  Best achievable is "
                               f"band-max {best_mx:.3f} at +/-{achieved_hw:.2f} MHz.")
                    break
            else:
                no_progress = 0
            prev_mx = best_mx
        if attempt > 0 and log_fn:
            verdict = ("MET target" if best_mx <= target_swr_with_slack
                       else "best achievable; user target unmet")
            log_fn(f"  [auto-fit] settled at half-width +/-{achieved_hw:.2f} MHz "
                   f"(band {f_low:.3f}-{f_high:.3f}) -- {verdict}")

    # ---- BASELINE REVERT GUARD ---------------------------------------------
    # Final guard against TOTAL regressions only.  Compares final band-max on
    # the user's original band against the baseline.  Reverts ONLY if final
    # is catastrophically worse (>= 50% worse).  Previous 0.02 threshold was
    # too tight -- it triggered on legitimate wideband searches where the
    # final geometry trades narrow-band SWR for wider coverage and the user
    # had reported real-world data showing such configurations exist.
    try:
        _fc, final_mx_user_band, _fav = v2_runner.band_swr_curve(
            best_elements, baseline_f_low, baseline_f_high, points, height_ft)
    except Exception:
        final_mx_user_band = float("inf")
    if (final_mx_user_band > baseline_mx * 1.5
            and baseline_mx < float("inf")
            and baseline_mx < 5.0):
        if log_fn:
            log_fn(f"  [revert] final band-max {final_mx_user_band:.3f} "
                   f"is >= 50% worse than baseline {baseline_mx:.3f} on the "
                   f"user's original band ({baseline_f_low:.3f}-"
                   f"{baseline_f_high:.3f} MHz) -- catastrophic regression, "
                   f"reverting to the input geometry.")
        best_elements = baseline_geom
        rules["global"]["freq_mhz_low"] = baseline_f_low
        rules["global"]["freq_mhz_high"] = baseline_f_high
        f_low, f_high = baseline_f_low, baseline_f_high

    curve, mx, _av = v2_runner.band_swr_curve(best_elements, f_low, f_high, points, height_ft)
    return best_elements, mx, curve
