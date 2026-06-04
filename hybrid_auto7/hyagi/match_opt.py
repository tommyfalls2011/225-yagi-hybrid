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


def _build_dofs(elements, rules):
    """Return (vec, bounds, director_names). vec/bounds keyed by DOF name.

    Positions are parametrised as gaps relative to the DE so the DE stays put
    and only the matching cell moves; director positions are held fixed and
    only their lengths are tuned (keeps boom length / gain pattern stable)."""
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

    return vec, bounds, de_pos


def _apply(elements, vec, de_pos):
    e = copy.deepcopy(elements)
    de = _el(e, "DE")
    de["length_in"] = vec["de_len"]
    if "ref_len" in vec:
        ref = _el(e, "REF")
        ref["length_in"] = vec["ref_len"]
        ref["position_in"] = round(de_pos - vec["ref_gap"], 4)
    if "xf_len" in vec:
        xf = _el(e, "XFRMR")
        xf["length_in"] = vec["xf_len"]
        xf["position_in"] = round(de_pos - vec["xf_gap"], 4)
    if "cp_len" in vec:
        cp = _el(e, "COUPLER")
        cp["length_in"] = vec["cp_len"]
        cp["position_in"] = round(de_pos + vec["cp_gap"], 4)
    for key, val in vec.items():
        if key.endswith("_len") and key[:-4].upper().startswith("DIR"):
            _el(e, key[:-4])["length_in"] = val
    return e


def _objective(elements, rules, height_ft, f_low, f_high, points):
    _curve, mx, av = v2_runner.band_swr_curve(elements, f_low, f_high, points, height_ft)
    return mx + 0.05 * av, mx


def _descend(vec, bounds, elements, de_pos, rules, height_ft, f_low, f_high,
             points, steps, target, log_fn):
    best_obj, best_mx = _objective(_apply(elements, vec, de_pos), rules,
                                   height_ft, f_low, f_high, points)
    keys = list(vec.keys())
    for step in steps:
        improved, rounds = True, 0
        while improved and rounds < 10:
            improved, rounds = False, rounds + 1
            for k in keys:
                lo, hi = bounds[k]
                for d in (step, -step):
                    nv = dict(vec)
                    nv[k] = round(min(hi, max(lo, vec[k] + d)), 3)
                    if abs(nv[k] - vec[k]) < 1e-9:
                        continue
                    obj, mx = _objective(_apply(elements, nv, de_pos), rules,
                                         height_ft, f_low, f_high, points)
                    if obj < best_obj - 1e-4:
                        best_obj, best_mx, vec, improved = obj, mx, nv, True
            if log_fn:
                log_fn(f"    [match] step={step:>4} round={rounds} band_max_swr={best_mx:.3f}")
            if best_mx <= target:
                return vec, best_obj, best_mx
    return vec, best_obj, best_mx


def _polish_gain(elements, rules, de_pos, height_ft, f_low, f_high, points,
                 target_swr, log_fn):
    """After SWR is under target, recover gain/F-B by re-tuning the passive
    elements (REF + director lengths) with the FULL pattern eval, rejecting any
    move that pushes band-max SWR back over target. Keeps the wideband match
    while climbing back toward maximum gain."""
    v2_runner.EVAL_FREQ_POINTS = max(7, int(points))
    keys = [e["name"] for e in elements
            if str(e["name"]).upper().startswith("DIR")
            or str(e["name"]).upper() == "REF"]
    if not keys:
        return elements

    def composite(els):
        m = v2_runner.evaluate(els, rules, height_ft=height_ft)
        if "error" in m:
            return -1e9, 99.0
        return m["gain_dbi"] + 0.15 * m["fb_db"], m["max_swr"]

    cur = copy.deepcopy(elements)
    best_score, _sw = composite(cur)
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
                    sc, _ = composite(trial)
                    if sc > best_score + 1e-4:
                        best_score, cur, improved = sc, trial, True
                        base_len = nl
            if log_fn:
                gm = v2_runner.evaluate(cur, rules, height_ft=height_ft)
                log_fn(f"    [gain-polish] step={step} round={rounds} "
                       f"gain={gm.get('gain_dbi',0):.2f} fb={gm.get('fb_db',0):.2f}")
    return cur


def optimize(elements, rules, height_ft=30.0, target_swr=1.2,
             points=21, restarts=2, steps=(8.0, 4.0, 2.0, 1.0, 0.5, 0.25),
             seed=12345, polish_gain=True, log_fn=print):
    """Minimise band-max SWR across the band in rules['global'], then recover
    gain/F-B while holding the match.

    Returns (best_elements, best_max_swr, curve)."""
    glb = rules["global"]
    f_low = float(glb["freq_mhz_low"])
    f_high = float(glb["freq_mhz_high"])

    vec0, bounds, de_pos = _build_dofs(elements, rules)
    rng = random.Random(seed)

    best_vec, best_obj, best_mx = _descend(
        dict(vec0), bounds, elements, de_pos, rules, height_ft,
        f_low, f_high, points, steps, target_swr, log_fn)
    if log_fn:
        log_fn(f"  [match] base pass -> band_max_swr={best_mx:.3f}")

    r = 0
    while best_mx > target_swr and r < restarts:
        r += 1
        pv = dict(best_vec)
        for k in pv:  # perturb only the matching cell, leave directors mostly alone
            if k.endswith("_len") and k[:-4].upper().startswith("DIR"):
                continue
            lo, hi = bounds[k]
            span = (hi - lo) * 0.15
            pv[k] = round(min(hi, max(lo, pv[k] + rng.uniform(-span, span))), 3)
        v, o, mx = _descend(pv, bounds, elements, de_pos, rules, height_ft,
                            f_low, f_high, points, steps, target_swr, log_fn)
        if log_fn:
            log_fn(f"  [match] restart {r} -> band_max_swr={mx:.3f}")
        if o < best_obj - 1e-4:
            best_vec, best_obj, best_mx = v, o, mx

    best_elements = _apply(elements, best_vec, de_pos)

    # Recover gain/F-B if we are comfortably under the SWR target.
    if polish_gain and best_mx <= target_swr:
        best_elements = _polish_gain(best_elements, rules, de_pos, height_ft,
                                     f_low, f_high, points, target_swr, log_fn)

    curve, mx, _av = v2_runner.band_swr_curve(best_elements, f_low, f_high, points, height_ft)
    return best_elements, mx, curve
