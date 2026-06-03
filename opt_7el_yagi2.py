#!/usr/bin/env python3
"""
opt_7el_yagi2.py

Modular 7-element flat/horizontal Yagi staged optimizer centered around 27.195 MHz.

Workflow:
    1) Baseline SWR / match tune
    2) Baseline return-loss tune
    3) Coordinate placement search
    4) Small length-trim search
    5) Final best-layout selection from logged candidates
"""

import argparse

# BOOM_LOCK_v1: module-level config set from CLI args in main()
BOOM_TARGET_FT = 0.0    # 0 = no target
BOOM_LOCK = False        # True = strict equality to BOOM_TARGET_FT
BOOM_DIAMETER_IN = 1.5   # metadata only for now
SPACING_STYLE = "auto"   # auto | tight | long
import sys
import numpy as np

from yagiopt.seeding import make_seed, make_element_names
from yagiopt.stage_plan import make_plan
from yagiopt.constants import (
    TAPER_CENTER_DIAMETER_IN,
    TAPER_OUTER_DIAMETER_IN,
    DEFAULT_USE_REAL_GROUND,
)
from yagiopt.geometry import (
    pack_design,
    unpack_design,
    active_from_full,
    full_from_active,
)
from yagiopt.nec_engine import solve_nec_impedance
from yagiopt.pattern import estimate_pattern_metrics, probe_gain_support
from yagiopt.sweep import (
    make_centered_freqs,
    make_final_sweep_freqs,
    evaluate_candidate_metrics,
    sweep_design,
)
from yagiopt.search import (
    sanitize_bound,
    build_de_init_population,
    run_differential_evolution,
    coordinate_position_search,
    coordinate_length_search,
    coordinate_region_position_search,
    coordinate_region_length_search,
    choose_best_logged_layout,
)
from yagiopt.nec_export import write_nec_deck
from yagiopt.reporting import (
    print_stage_result,
    print_search_record,
    print_top_layouts,
    print_design,
    print_center_result,
    print_sweep_summary,
)


REFLECTOR_MIN_OVER_DE_FT = 2.0 / 12.0
REFLECTOR_MAX_OVER_DE_FT = 18.0 / 12.0

ELEMENT_NAMES = ["REF", "DE", "D1", "D2", "D3", "D4", "D5"]

SEED_LENGTHS_FT = np.array([
    18.90,
    17.80,
    17.05,
    16.65,
    16.30,
    16.00,
    15.75,
], dtype=float)

SEED_SPACINGS_FT = np.array([
    4.50,
    3.80,
    4.70,
    5.10,
    5.40,
    5.70,
], dtype=float)

SEED_HEIGHT_FT = 50.0



DEFAULT_TUNE_PREFS = {
    "gain": 55,
    "swr": 70,
    "return_loss": 70,
    "bandwidth": 75,
    "front_to_back": 50,
}
USER_TUNE_PREFS = DEFAULT_TUNE_PREFS.copy()

USER_N_ELEMENTS = 7
USER_POLARIZATION = "horizontal"
SUPPORTED_N_ELEMENTS = list(range(2, 19))
POLARIZATION_CHOICES = ["horizontal"]

def clamp(value, lo, hi):
    return max(lo, min(hi, value))

def pref_mult(value, lo, hi):
    v = clamp(float(value), 0.0, 100.0) / 100.0
    return float(lo + v * (hi - lo))

def prompt_float(prompt, default):
    s = input(f"{prompt} [{default}]: ").strip()
    if s == "":
        return float(default)
    try:
        return float(s)
    except Exception:
        print("Invalid input, using default.")
        return float(default)

def prompt_int(name, default, lo, hi, desc=""):
    if desc: print(desc)
    s = input(f"{name} [{lo}-{hi}] (default {default}): ").strip()
    if s == "": return int(default)
    try: return int(clamp(int(s), lo, hi))
    except Exception: print("Invalid, using default."); return int(default)


def prompt_choice(name, choices, default_idx=0):
    print(f"\n{name}:")
    for i, c in enumerate(choices, 1):
        mark = " (default)" if i-1 == default_idx else ""
        print(f"  {i}. {c}{mark}")
    s = input(f"Choice [1-{len(choices)}]: ").strip()
    if s == "": return choices[default_idx]
    try:
        idx = int(s) - 1
        if 0 <= idx < len(choices): return choices[idx]
    except Exception: pass
    print("Invalid, using default."); return choices[default_idx]


def prompt_scale(name, default, desc=""):
    print()
    print(name)
    if desc:
        print(desc)
    print("Low  0 ---- 25 ---- 50 ---- 75 ---- 100  High")
    s = input(f"Enter {name} [0-100] (default {default}): ").strip()
    if s == "":
        return int(default)
    try:
        return int(clamp(int(s), 0, 100))
    except Exception:
        print("Invalid input, using default.")
        return int(default)

def interactive_startup(center_freq, tune_prefs):
    prefs = dict(tune_prefs)
    print("\nINTERACTIVE TUNING SETUP")
    print("========================")
    print("Press Enter to keep the default shown in brackets.\n")
    global USER_N_ELEMENTS, USER_POLARIZATION
    USER_N_ELEMENTS = prompt_int(
        "Number of elements", USER_N_ELEMENTS, 2, 18,
        desc="2 = ref+driven; 7 = standard long-Yagi; up to 12 for long boom",
    )
    USER_POLARIZATION = prompt_choice(
        "Antenna polarization / mount", POLARIZATION_CHOICES, default_idx=0,
    )
    center_freq = prompt_float("Center frequency in MHz", center_freq)
    prefs["gain"] = prompt_scale("Gain priority", prefs["gain"],
        desc="Higher = chase more forward gain, even if bandwidth may narrow.")
    prefs["swr"] = prompt_scale("SWR priority", prefs["swr"],
        desc="Higher = push harder for lower SWR and a safer match.")
    prefs["return_loss"] = prompt_scale("Return-loss priority", prefs["return_loss"],
        desc="Higher = reward stronger return loss and penalize RL shortfall more.")
    prefs["bandwidth"] = prompt_scale("Bandwidth priority", prefs["bandwidth"],
        desc="Higher = favor broader usable tuning across the band.")
    prefs["front_to_back"] = prompt_scale("Front-to-back priority", prefs["front_to_back"],
        desc="Higher = favor better rear rejection / cleaner pattern.")
    return float(center_freq), prefs

def print_tune_preferences(center_freq, prefs):
    print("\nUSER TUNING PROFILE")
    print("===================")
    print(f"Center frequency:     {center_freq:.3f} MHz")
    print(f"Gain priority:        {prefs['gain']:3d} / 100")
    print(f"SWR priority:         {prefs['swr']:3d} / 100")
    print(f"Return-loss priority: {prefs['return_loss']:3d} / 100")
    print(f"Bandwidth priority:   {prefs['bandwidth']:3d} / 100")
    print(f"Front/back priority:  {prefs['front_to_back']:3d} / 100")

def geometry_is_valid(lengths, spacings, height_ft):
    lengths = np.asarray(lengths, dtype=float)
    spacings = np.asarray(spacings, dtype=float)

    if lengths.ndim != 1 or spacings.ndim != 1 or len(lengths) < 2 or len(lengths) > 18 or len(spacings) != len(lengths) - 1:
        return False
    if not np.all(np.isfinite(lengths)) or not np.all(np.isfinite(spacings)) or not np.isfinite(height_ft):
        return False

    if np.any(lengths <= 0.0) or np.any(spacings <= 0.0) or height_ft <= 0.0:
        return False

    if np.any(lengths < 8.0) or np.any(lengths > 30.0):
        return False
    if np.any(spacings < 1.0) or np.any(spacings > 15.0):
        return False
    if height_ft < 5.0 or height_ft > 200.0:
        return False

    boom = np.sum(spacings)
    # BOOM_LOCK_v2: loose gate, strong penalty (gate must allow DE to explore)
    if BOOM_LOCK and BOOM_TARGET_FT > 0:
        # Loose validity gate (+/- 25%); design_penalty pulls hard toward exact target
        lo = BOOM_TARGET_FT * 0.75
        hi = BOOM_TARGET_FT * 1.25
        if boom < lo or boom > hi:
            return False
    else:
        # Scale boom limit with element count, capped at 120 ft
        boom_max = min(120.0, 40.0 + 10.0 * len(lengths))
        if BOOM_TARGET_FT > 0:
            boom_max = min(boom_max, BOOM_TARGET_FT * 1.25)
        if boom < 3.0 or boom > boom_max:
            return False

    y = [0.0]
    for s in spacings:
        y.append(y[-1] + s)
    y = np.array(y, dtype=float)
    if len(np.unique(np.round(y, 12))) != len(y):
        return False

    ref_minus_de = lengths[0] - lengths[1]
    if ref_minus_de < REFLECTOR_MIN_OVER_DE_FT:
        return False
    if ref_minus_de > REFLECTOR_MAX_OVER_DE_FT:
        return False

    return True


def design_penalty(lengths, spacings, height):
    lengths = np.asarray(lengths, dtype=float)
    spacings = np.asarray(spacings, dtype=float)
    height = float(height)

    p = 0.0

    ref_minus_de = lengths[0] - lengths[1]
    if ref_minus_de < REFLECTOR_MIN_OVER_DE_FT:
        p += 1500.0 * (REFLECTOR_MIN_OVER_DE_FT - ref_minus_de) ** 2
    if ref_minus_de > REFLECTOR_MAX_OVER_DE_FT:
        p += 1500.0 * (ref_minus_de - REFLECTOR_MAX_OVER_DE_FT) ** 2

    if np.any(spacings < 2.5):
        p += 1200.0 * np.sum((2.5 - spacings[spacings < 2.5]) ** 2)

    for i in range(2, len(lengths)):
        max_dir_allowed = lengths[1] - 0.05
        if lengths[i] > max_dir_allowed:
            p += 450.0 * (lengths[i] - max_dir_allowed) ** 2

    # OWA_FIX_v1: allow centre-director rise up to 0.15 ft (OWA/DK7ZB topology), softer coeff
    for i in range(2, len(lengths) - 1):
        excess = lengths[i + 1] - lengths[i] - 0.15
        if excess > 0:
            p += 200.0 * excess ** 2

    # OWA_FIX_v1: allow OWA "pulse" spacing pattern; only penalise dips > max(1.5ft, 30% of prev).
    for i in range(1, len(spacings) - 1):
        dip = spacings[i] - spacings[i + 1]
        threshold = max(1.5, 0.30 * spacings[i])
        if dip > threshold:
            p += 50.0 * (dip - threshold) ** 2

    boom = np.sum(spacings)
    # BOOM_LOCK_v1
    if BOOM_TARGET_FT > 0:
        if BOOM_LOCK:
            # Hard penalty if straying from target
            p += 500.0 * (boom - BOOM_TARGET_FT) ** 2
        else:
            # Soft penalty: prefer +/- 15% of target
            lo = BOOM_TARGET_FT * 0.85
            hi = BOOM_TARGET_FT * 1.15
            if boom < lo: p += 50.0 * (lo - boom) ** 2
            if boom > hi: p += 50.0 * (boom - hi) ** 2
    else:
        if boom < 22.0: p += 20.0 * (22.0 - boom) ** 2
        if boom > 40.0: p += 20.0 * (boom - 40.0) ** 2
    # Spacing-style nudge
    avg_sp = boom / max(1, len(spacings))
    if SPACING_STYLE == "tight" and avg_sp > 4.0:
        p += 30.0 * (avg_sp - 4.0) ** 2
    elif SPACING_STYLE == "long" and avg_sp < 5.0:
        p += 30.0 * (5.0 - avg_sp) ** 2

    if height < 20.0:
        p += 12.0 * (20.0 - height) ** 2
    if height > 100.0:
        p += 3.0 * (height - 100.0) ** 2
    # OWA_FIX_v1: mild height reward in 25..70ft window to prevent drift-down during long-boom runs
    if 25.0 <= height <= 70.0:
        p -= 0.5 * (height - 25.0)

    return float(p)


def solve_impedance(lengths, spacings, height, freq_mhz, use_real_ground=DEFAULT_USE_REAL_GROUND):
    return solve_nec_impedance(
        lengths, spacings, height, freq_mhz,
        geometry_validator=geometry_is_valid,
        use_real_ground=use_real_ground,
    )


def estimate_pattern(lengths, spacings, height, freq_mhz, use_real_ground=DEFAULT_USE_REAL_GROUND):
    return estimate_pattern_metrics(
        lengths, spacings, height, freq_mhz,
        geometry_validator=geometry_is_valid,
        use_real_ground=use_real_ground,
    )


def evaluate_metrics(lengths, spacings, height, freqs_opt, center_freq, target_rl_db,
                     use_real_ground, measure_gain):
    return evaluate_candidate_metrics(
        lengths, spacings, height,
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=target_rl_db,
        use_real_ground=use_real_ground,
        measure_gain=measure_gain,
        solve_impedance_fn=solve_impedance,
        estimate_pattern_fn=estimate_pattern,
    )


def compute_stage_cost(stage_name, metrics, lengths, spacings, height):
    penalty = design_penalty(lengths, spacings, height)

    ref_dev = abs(lengths[0] - SEED_LENGTHS_FT[0])
    de_dev = abs(lengths[1] - SEED_LENGTHS_FT[1])
    d1_dev = abs(lengths[2] - SEED_LENGTHS_FT[2])
    refde_dev = abs(spacings[0] - SEED_SPACINGS_FT[0])
    ded1_dev = abs(spacings[1] - SEED_SPACINGS_FT[1])

    feed_shape_pen = 0.0
    if ref_dev > 2.0:
        feed_shape_pen += 20.0 * (ref_dev - 2.0) ** 2
    if de_dev > 2.0:
        feed_shape_pen += 20.0 * (de_dev - 2.0) ** 2
    if d1_dev > 2.0:
        feed_shape_pen += 20.0 * (d1_dev - 2.0) ** 2
    if refde_dev > 2.5:
        feed_shape_pen += 20.0 * (refde_dev - 2.5) ** 2
    if ded1_dev > 2.5:
        feed_shape_pen += 20.0 * (ded1_dev - 2.5) ** 2

    if stage_name == "swrmatch":
        cost = 0.0
        cost += 60.0 * max(0.0, metrics["center_swr"] - 1.02) ** 2
        cost += 30.0 * max(0.0, metrics["local_max_swr"] - 1.35) ** 2
        cost += 18.0 * (metrics["r_err"] ** 2)
        cost += 26.0 * (metrics["x_err"] ** 2)
        cost += 20.0 * metrics["weighted_shortfall"]
        cost -= 0.9 * metrics["center_rl_reward"]
        cost -= 0.6 * metrics["local_mean_rl"]
        cost += penalty + feed_shape_pen
        return float(cost)

    if stage_name == "returnloss":
        cost = 0.0
        cost += 40.0 * metrics["weighted_shortfall"]
        cost += 12.0 * (metrics["r_err"] ** 2)
        cost += 18.0 * (metrics["x_err"] ** 2)
        cost += 26.0 * max(0.0, metrics["center_swr"] - 1.20) ** 2
        cost += 14.0 * max(0.0, metrics["local_max_swr"] - 1.60) ** 2
        cost -= 1.8 * metrics["center_rl_reward"]
        cost -= 1.4 * metrics["local_min_rl"]
        cost -= 0.8 * metrics["local_mean_rl"]
        cost -= 6.0 * metrics["bandwidth_mhz"]
        cost += penalty + 0.8 * feed_shape_pen
        return float(cost)

    return 1e9


def combined_layout_score(metrics, target_rl_db, gain_enabled):
    global USER_TUNE_PREFS
    required = ["center_R","center_X","center_rl_raw","center_rl_reward",
                "center_swr","local_min_rl","local_mean_rl","local_max_swr",
                "bandwidth_mhz","r_err","x_err"]
    if not all(np.isfinite(metrics[k]) for k in required):
        return -1e12
    if gain_enabled:
        if not (np.isfinite(metrics["forward_gain_db"]) and np.isfinite(metrics["front_to_back_db"])):
            return -1e12

    gain_mult = pref_mult(USER_TUNE_PREFS["gain"], 0.6, 1.8)
    swr_mult  = pref_mult(USER_TUNE_PREFS["swr"], 0.7, 2.2)
    rl_mult   = pref_mult(USER_TUNE_PREFS["return_loss"], 0.7, 2.0)
    bw_mult   = pref_mult(USER_TUNE_PREFS["bandwidth"], 0.7, 2.4)
    fb_mult   = pref_mult(USER_TUNE_PREFS["front_to_back"], 0.7, 1.8)

    score = 0.0
    bw18 = float(metrics.get("bandwidth_mhz", 0.0))
    bw15 = float(metrics.get("bandwidth15_mhz", bw18))
    bw12 = float(metrics.get("bandwidth12_mhz", bw15))

    score += bw_mult * 10.0 * bw18
    score += bw_mult * 6.0  * bw15
    score += bw_mult * 3.0  * bw12

    score += rl_mult * 1.4 * metrics["center_rl_reward"]
    score += rl_mult * 1.2 * metrics["local_mean_rl"]
    score += rl_mult * 1.0 * metrics["local_min_rl"]

    if np.isfinite(metrics.get("lower_035_rl", np.nan)) and np.isfinite(metrics.get("upper_035_rl", np.nan)):
        score += rl_mult * 1.5 * min(metrics["lower_035_rl"], metrics["upper_035_rl"])
    if np.isfinite(metrics.get("lower_050_rl", np.nan)) and np.isfinite(metrics.get("upper_050_rl", np.nan)):
        score += rl_mult * 1.2 * min(metrics["lower_050_rl"], metrics["upper_050_rl"])

    if np.isfinite(metrics.get("shoulder_balance_db", np.nan)):
        score -= bw_mult * 2.5 * metrics["shoulder_balance_db"]
    if np.isfinite(metrics.get("outer_balance_db", np.nan)):
        score -= bw_mult * 3.5 * metrics["outer_balance_db"]
    if np.isfinite(metrics.get("upper_collapse_db", np.nan)):
        score -= bw_mult * 0.9 * (metrics["upper_collapse_db"] ** 2)

    if np.isfinite(metrics.get("upper_035_rl", np.nan)):
        score -= rl_mult * 1.0 * max(0.0, target_rl_db - metrics["upper_035_rl"]) ** 2
    if np.isfinite(metrics.get("upper_050_rl", np.nan)):
        score -= bw_mult * 1.5 * max(0.0, 12.0 - metrics["upper_050_rl"]) ** 2

    if gain_enabled:
        score += gain_mult * 3.5 * metrics["forward_gain_db"]
        score += fb_mult * 5.0 * max(0.0, min(metrics["front_to_back_db"], 35.0))
        score -= fb_mult * 20.0 * max(0.0, 20.0 - metrics["front_to_back_db"]) ** 2
        if np.isfinite(metrics["rear_gain_db"]):
            score -= fb_mult * 3.0 * max(0.0, metrics["rear_gain_db"] + 3.0) ** 2

    score -= swr_mult * 35.0 * max(0.0, metrics["center_swr"] - 1.12) ** 2
    score -= swr_mult * 24.0 * max(0.0, metrics["local_max_swr"] - 1.35) ** 2
    score -= swr_mult * 4.0 * (metrics["r_err"] ** 2)
    score -= swr_mult * 5.0 * (metrics["x_err"] ** 2)

    score -= bw_mult * 250.0 * max(0.0, 0.60 - bw18) ** 2
    score -= bw_mult * 120.0 * max(0.0, 0.90 - bw15) ** 2

    if metrics["center_swr"] > 1.60: score -= 1e6
    if metrics["local_max_swr"] > 2.00: score -= 1e6
    if metrics["center_rl_raw"] < 10.0: score -= 1e6
    if np.isfinite(metrics.get("upper_050_rl", np.nan)) and metrics["upper_050_rl"] < 8.0:
        score -= 2e5
    return float(score)


def get_stage_active_idx(stage_name, n=7):
    # Indices into the [N lengths, N-1 spacings, 1 height] design vector.
    # Derived from N so any element count works (was hardcoded to the 7-element
    # / 14-slot layout, e.g. height at index 13, which overflowed for N!=7).
    n = int(n)
    total = 2 * n
    height_idx = total - 1
    sp0 = n  # first spacing index

    def _clip(idxs):
        return sorted({i for i in idxs if 0 <= i < total})

    if stage_name == "swrmatch":
        return _clip([0, 1, 2, sp0, sp0 + 1, height_idx])
    if stage_name == "returnloss":
        return _clip([0, 1, 2, sp0, sp0 + 1, sp0 + 2, height_idx])
    return list(range(total))


def build_stage_bounds_from_base(base_x, stage_name):
    lengths, spacings, height = unpack_design(base_x)
    bounds = []

    for i, L in enumerate(lengths):
        if stage_name == "swrmatch":
            lo, hi = ((0.90 * L, 1.10 * L) if i in (0, 1, 2) else (0.99 * L, 1.01 * L))
        elif stage_name == "returnloss":
            lo, hi = ((0.92 * L, 1.08 * L) if i in (0, 1, 2) else (0.98 * L, 1.02 * L))
        else:
            lo, hi = (0.95 * L, 1.05 * L)

        bounds.append(sanitize_bound(lo, hi, 8.0, 30.0))

    for i, s in enumerate(spacings):
        if stage_name == "swrmatch":
            lo, hi = ((0.60 * s, 1.60 * s) if i in (0, 1) else (0.99 * s, 1.01 * s))
        elif stage_name == "returnloss":
            lo, hi = ((0.75 * s, 1.30 * s) if i in (0, 1, 2) else (0.98 * s, 1.02 * s))
        else:
            lo, hi = (0.90 * s, 1.10 * s)

        bounds.append(sanitize_bound(lo, hi, 2.50, 8.50))

    if stage_name == "swrmatch":
        hb = (height - 5.0, height + 5.0)
    else:
        hb = (height - 4.0, height + 4.0)

    bounds.append(sanitize_bound(hb[0], hb[1], 45.0, 55.0))
    return bounds


def make_stage_objective(stage_name, freqs_opt, center_freq, target_rl_db, use_real_ground,
                         measure_gain, active_idx, base_full_x,
                         initial_best_x=None, initial_best_metrics=None, initial_best_cost=np.inf):
    state = {
        "best_cost": float(initial_best_cost),
        "best_x": None if initial_best_x is None else np.array(initial_best_x, dtype=float).copy(),
        "best_metrics": None if initial_best_metrics is None else dict(initial_best_metrics),
    }
    cache = {}

    def objective(x_active):
        x_full = full_from_active(x_active, base_full_x, active_idx)
        lengths, spacings, height = unpack_design(x_full)

        if not geometry_is_valid(lengths, spacings, height):
            return 1e9

        key = tuple(np.round(np.asarray(x_full, dtype=float), 5))
        if key in cache:
            metrics = cache[key]
        else:
            try:
                metrics = evaluate_metrics(
                    lengths, spacings, height,
                    freqs_opt=freqs_opt,
                    center_freq=center_freq,
                    target_rl_db=target_rl_db,
                    use_real_ground=use_real_ground,
                    measure_gain=measure_gain,
                )
            except Exception:
                return 1e9

            required = [
                "center_R", "center_X", "center_rl_raw", "center_swr", "center_eta",
                "local_min_rl", "local_mean_rl", "local_max_swr", "local_mean_swr",
                "weighted_shortfall", "bandwidth_mhz",
            ]
            if not all(np.isfinite(metrics[k]) for k in required):
                return 1e9

            cache[key] = metrics

        cost = compute_stage_cost(stage_name, metrics, lengths, spacings, height)
        if not np.isfinite(cost):
            return 1e9

        if cost < state["best_cost"]:
            state["best_cost"] = float(cost)
            state["best_x"] = np.array(x_full, dtype=float).copy()
            state["best_metrics"] = dict(metrics)
            objective.best_cost = state["best_cost"]
            objective.best_x = state["best_x"]
            objective.best_metrics = state["best_metrics"]

        return float(cost)

    objective.best_cost = state["best_cost"]
    objective.best_x = state["best_x"]
    objective.best_metrics = state["best_metrics"]
    return objective


def run_stage(stage_name, freqs_opt, center_freq, target_rl_db, use_real_ground,
              measure_gain, maxiter, popsize, seed, workers, seed_x):
    if workers != 1:
        print("[WARN] workers > 1 is not reliable here with necpp and local best tracking; forcing workers=1")
        workers = 1

    active_idx = get_stage_active_idx(stage_name, len(np.asarray(seed_x)) // 2)
    full_bounds = build_stage_bounds_from_base(seed_x, stage_name)
    active_bounds = [full_bounds[i] for i in active_idx]

    initial_best_x = None
    initial_best_metrics = None
    initial_best_cost = np.inf

    try:
        seed_lengths, seed_spacings, seed_height = unpack_design(seed_x)
        if geometry_is_valid(seed_lengths, seed_spacings, seed_height):
            seed_metrics = evaluate_metrics(
                seed_lengths, seed_spacings, seed_height,
                freqs_opt=freqs_opt,
                center_freq=center_freq,
                target_rl_db=target_rl_db,
                use_real_ground=use_real_ground,
                measure_gain=measure_gain,
            )
            seed_cost = compute_stage_cost(
                stage_name, seed_metrics,
                seed_lengths, seed_spacings, seed_height
            )
            print(f"[DEBUG] stage={stage_name} seed cost={seed_cost:.6f}")
            if np.isfinite(seed_cost):
                initial_best_x = np.array(seed_x, dtype=float).copy()
                initial_best_metrics = dict(seed_metrics)
                initial_best_cost = float(seed_cost)
    except Exception as e:
        print(f"[DEBUG] seed evaluation failed in stage '{stage_name}': {repr(e)}")

    objective = make_stage_objective(
        stage_name=stage_name,
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=target_rl_db,
        use_real_ground=use_real_ground,
        measure_gain=measure_gain,
        active_idx=active_idx,
        base_full_x=seed_x,
        initial_best_x=initial_best_x,
        initial_best_metrics=initial_best_metrics,
        initial_best_cost=initial_best_cost,
    )

    seed_active = active_from_full(seed_x, active_idx)
    init_population = build_de_init_population(active_bounds, seed_active, popsize, seed)

    result = run_differential_evolution(
        objective=objective,
        bounds=active_bounds,
        maxiter=maxiter,
        popsize=popsize,
        seed=seed,
        workers=workers,
        init_population=init_population,
    )

    candidate_records = []

    try:
        x_full_candidate = full_from_active(result.x, seed_x, active_idx)
        lengths, spacings, height = unpack_design(x_full_candidate)
        if geometry_is_valid(lengths, spacings, height):
            metrics = evaluate_metrics(
                lengths, spacings, height,
                freqs_opt=freqs_opt,
                center_freq=center_freq,
                target_rl_db=target_rl_db,
                use_real_ground=use_real_ground,
                measure_gain=measure_gain,
            )
            final_cost = compute_stage_cost(stage_name, metrics, lengths, spacings, height)
            if np.isfinite(final_cost):
                candidate_records.append({
                    "stage": stage_name,
                    "lengths": lengths,
                    "spacings": spacings,
                    "height": height,
                    "metrics": metrics,
                    "stage_cost": float(final_cost),
                    "x_full": pack_design(lengths, spacings, height),
                })
    except Exception as e:
        print(f"[DEBUG] final result evaluation failed in stage '{stage_name}': {repr(e)}")

    if objective.best_x is not None and objective.best_metrics is not None and np.isfinite(objective.best_cost):
        try:
            lengths, spacings, height = unpack_design(objective.best_x)
            if geometry_is_valid(lengths, spacings, height):
                candidate_records.append({
                    "stage": stage_name,
                    "lengths": lengths,
                    "spacings": spacings,
                    "height": height,
                    "metrics": dict(objective.best_metrics),
                    "stage_cost": float(objective.best_cost),
                    "x_full": pack_design(lengths, spacings, height),
                })
        except Exception:
            pass

    if not candidate_records:
        raise RuntimeError(
            f"Stage '{stage_name}' did not produce any valid geometry. "
            f"Seed and DE population both failed."
        )

    return min(candidate_records, key=lambda rec: rec["stage_cost"])


def evaluate_layout_candidate_factory(freqs_opt, center_freq, target_rl_db,
                                      use_real_ground, gain_enabled, logbook):
    def evaluate_layout_candidate(x_full, label, accepted=False, note=""):
        lengths, spacings, height = unpack_design(x_full)

        if not geometry_is_valid(lengths, spacings, height):
            return None

        try:
            metrics = evaluate_metrics(
                lengths, spacings, height,
                freqs_opt=freqs_opt,
                center_freq=center_freq,
                target_rl_db=target_rl_db,
                use_real_ground=use_real_ground,
                measure_gain=gain_enabled,
            )
        except Exception:
            return None

        score = combined_layout_score(metrics, target_rl_db, gain_enabled)

        rec = {
            "label": str(label),
            "accepted": bool(accepted),
            "note": str(note),
            "lengths": lengths,
            "spacings": spacings,
            "height": float(height),
            "metrics": dict(metrics),
            "score": float(score),
            "x_full": pack_design(lengths, spacings, height),
        }

        if logbook is not None:
            logbook.append(rec)

        return rec

    return evaluate_layout_candidate


def run_preflight(use_real_ground, center_freq, requested_gain_probe):
    print("Running NEC preflight check on seed geometry...")
    print(f"Requested mode: {'real ground' if use_real_ground else 'free space'}")
    print(f"Center frequency: {center_freq:.3f} MHz")

    try:
        z = solve_impedance(SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT, center_freq, use_real_ground)
        print(f"Seed geometry @ {center_freq:.3f} MHz: {z.real:.2f} {z.imag:+.2f}j ohms")
    except Exception as e:
        print(f"Primary preflight failed: {e}")

        if use_real_ground:
            print("\nTrying free-space diagnostic...")
            try:
                z = solve_impedance(SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT, center_freq, False)
                print(f"Free-space diagnostic worked: {z.real:.2f} {z.imag:+.2f}j ohms")
                print("This suggests the geometry/excitation is probably okay, and the issue is ground-model behavior in this necpp build.")
            except Exception as e2:
                print(f"Free-space diagnostic also failed: {e2}")

        return False, False

    gain_enabled = False
    if requested_gain_probe:
        print("\nProbing radiation-pattern / gain support...")
        gain_enabled = probe_gain_support(
            SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT,
            center_freq, geometry_is_valid, use_real_ground
        )
        if gain_enabled:
            print("Gain support detected: enabled")
        else:
            print("Gain support not detected from this necpp build: gain-aware search will be disabled")

    return True, gain_enabled



# OPT_LEARN_v1: load top historical seed from yagi_history.db
def _load_promoted_seed(n_elements):
    """OPT_LEARN_v2_promoted: check ~/scripts/yagi_seeds/seed_n{N}.json first."""
    import json, os
    seeds_dir = os.path.expanduser("~/scripts/yagi_seeds")
    seed_file = os.path.join(seeds_dir, f"seed_n{n_elements}.json")
    if not os.path.exists(seed_file):
        return None
    try:
        with open(seed_file, "r") as f:
            p = json.load(f)
        if int(p.get("n_elements", -1)) != int(n_elements):
            return None
        lengths = p.get("lengths_ft") or p.get("lengths")
        spacings = p.get("spacings_ft") or p.get("spacings")
        height = p.get("height_ft") or p.get("height")
        if lengths is None or spacings is None or height is None:
            return None
        print(f"[learn] PROMOTED SEED loaded from {seed_file}")
        print(f"[learn]   source run #{p.get('source_run_id','?')}, gain={p.get('gain_db','?')}dB, score={p.get('score','?')}, cf={p.get('center_freq_mhz','?')}MHz")
        return list(lengths), list(spacings), float(height)
    except Exception as e:
        print(f"[learn] promoted seed read failed: {e}")
        return None


def _load_top_historical_seed(n_elements, center_freq_mhz, limit, min_gain, min_score, db_path):
    import sqlite3, json, os
    if not db_path:
        db_path = os.path.expanduser("~/scripts/yagi_history.db")
    if not os.path.exists(db_path):
        print(f"[learn] history DB not found at {db_path}")
        return None
    try:
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, center_freq_mhz, final_score, final_gain_db, final_swr, geometry_json "
            "FROM runs WHERE final_score IS NOT NULL AND geometry_json IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        c.close()
    except Exception as e:
        print(f"[learn] history DB read failed: {e}")
        return None
    cands = []
    for r in rows:
        try:
            g = json.loads(r["geometry_json"])
            if isinstance(g, dict):
                lengths = g.get("lengths") or g.get("lengths_ft") or g.get("lengths_feet")
                spacings = g.get("spacings") or g.get("spacings_ft") or g.get("spacings_feet")
                height = g.get("height") or g.get("height_ft")
            else:
                continue
            if lengths is None or spacings is None or height is None:
                continue
            if len(lengths) != n_elements:
                continue
            gain = float(r["final_gain_db"] or 0.0)
            score = float(r["final_score"] or 0.0)
            cf = float(r["center_freq_mhz"] or 0.0)
            if gain < min_gain or score < min_score:
                continue
            freq_pen = abs(cf - center_freq_mhz) * 10.0
            adj_score = score - freq_pen
            cands.append((adj_score, gain, score, cf, lengths, spacings, height, r["id"]))
        except Exception:
            continue
    if not cands:
        return None
    cands.sort(key=lambda t: -t[0])
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage-maxiter", type=int, default=5, help="DE iterations per baseline stage")
    ap.add_argument("--stage-popsize", type=int, default=5, help="DE popsize per baseline stage")
    ap.add_argument("--target-rl", type=float, default=18.0, help="Desired RL near center frequency in dB")
    ap.add_argument("--workers", type=int, default=1, help="Parallel workers (forced to 1)")
    ap.add_argument("--seed", type=int, default=1, help="Random seed")
    ap.add_argument("--free-space", action="store_true", help="Use free space instead of real ground")
    ap.add_argument("--quick", action="store_true", help="Use fewer frequency points")
    ap.add_argument("--fast", action="store_true", help="Very fast testing mode")
    ap.add_argument("--preflight-only", action="store_true", help="Run diagnostics only, then exit")
    ap.add_argument("--center-freq", type=float, default=27.195, help="Center frequency in MHz")
    ap.add_argument("--position-passes", type=int, default=2, help="Passes per position-step size")
    ap.add_argument("--length-passes", type=int, default=2, help="Passes per length-step size")
    ap.add_argument("--export-nec", type=str, default=None, help="Write final design to this .nec file for EZNEC/4nec2/xnec2c import")
    ap.add_argument("--interactive", action="store_true", help="Prompt for center frequency and tuning priorities")
    ap.add_argument("--gain-priority", type=int, default=None, help="0..100, higher favors gain more")
    ap.add_argument("--swr-priority", type=int, default=None, help="0..100, higher favors SWR more")
    ap.add_argument("--rl-priority", type=int, default=None, help="0..100, higher favors return loss more")
    ap.add_argument("--bw-priority", type=int, default=None, help="0..100, higher favors bandwidth more")
    ap.add_argument("--fb-priority", type=int, default=None, help="0..100, higher favors front-to-back more")
    ap.add_argument("--tag", type=str, default=None)
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--strategy", type=str, default=None, help="Use a named strategy from yagiopt/strategies.py")
    ap.add_argument("--list-strategies", action="store_true", help="List available strategies and exit")
    ap.add_argument("--elements", type=int, default=None, help="Number of elements (2-18)")
    ap.add_argument("--polarization", type=str, default=None, choices=["horizontal"])
    # BOOM_LOCK_v1
    ap.add_argument("--boom-length-ft", type=float, default=0.0, help="Target boom length in feet (0 = unconstrained)")
    ap.add_argument("--boom-diameter-in", type=float, default=1.5, help="Boom diameter in inches (metadata)")
    ap.add_argument("--lock-boom", action="store_true", help="Strictly enforce --boom-length-ft (REF at 0, last director at end)")
    ap.add_argument("--spacing-style", type=str, default="auto", choices=["auto","tight","long"], help="Element spacing profile")
    # OPT_LEARN_v1: self-learning from yagi_history.db
    ap.add_argument("--learn-from", type=int, default=100, help="Inspect last N history rows for seed candidates (0 disables)")
    ap.add_argument("--no-learn", action="store_true", help="Disable history-based seed warm-start")
    ap.add_argument("--learn-min-gain", type=float, default=15.0, help="Min final_gain_db for a history row to be usable")
    ap.add_argument("--learn-min-score", type=float, default=100.0, help="Min final_score for a history row to be usable")
    ap.add_argument("--learn-db", type=str, default="", help="Override path to history DB (default ~/scripts/yagi_history.db)")
    args = ap.parse_args()

    # BOOM_LOCK_v1: apply CLI args to module globals
    global BOOM_TARGET_FT, BOOM_LOCK, BOOM_DIAMETER_IN, SPACING_STYLE
    BOOM_TARGET_FT   = float(getattr(args, "boom_length_ft", 0.0) or 0.0)
    BOOM_LOCK        = bool(getattr(args, "lock_boom", False))
    BOOM_DIAMETER_IN = float(getattr(args, "boom_diameter_in", 1.5) or 1.5)
    SPACING_STYLE    = str(getattr(args, "spacing_style", "auto") or "auto")
    if BOOM_TARGET_FT > 0 or BOOM_LOCK or SPACING_STYLE != "auto":
        print(f"[boom] target={BOOM_TARGET_FT:.2f}ft  lock={BOOM_LOCK}  diam={BOOM_DIAMETER_IN:.3f}in  style={SPACING_STYLE}")



    # ---- strategies handling ----
    from yagiopt import strategies as _strat
    if getattr(args, "list_strategies", False):
        print("\nAvailable strategies:")
        for name in _strat.list_strategies():
            s = _strat.get_strategy(name)
            print(f"\n  {name}")
            print(f"    {s['description']}")
            p = s["priorities"]
            print(f"    priorities: gain={p['gain']} swr={p['swr']} rl={p['rl']} bw={p['bw']} fb={p['fb']}")
            print(f"    preferred_seed: {s.get('preferred_seed')}")
            print(f"    expected: score={s.get('expected_score')}  F/B={s.get('expected_fb_db')}dB  BW={s.get('expected_bw_mhz')}MHz")
        sys.exit(0)
    if getattr(args, "strategy", None):
        _s = _strat.get_strategy(args.strategy)
        _p = _s["priorities"]
        # Don't override CLI-set priorities; only fill in unset ones
        if args.gain_priority is None: args.gain_priority = _p["gain"]
        if args.swr_priority is None:  args.swr_priority  = _p["swr"]
        if args.rl_priority is None:   args.rl_priority   = _p["rl"]
        if args.bw_priority is None:   args.bw_priority   = _p["bw"]
        if args.fb_priority is None:   args.fb_priority   = _p["fb"]
        if args.seed == 1 and _s.get("preferred_seed") is not None:
            args.seed = _s["preferred_seed"]
        if not args.tag:
            args.tag = "strategy:" + args.strategy
        print(f"\n[strategy] Using '{args.strategy}': {_s['description']}")
        print(f"[strategy] Priorities: gain={args.gain_priority} swr={args.swr_priority} rl={args.rl_priority} bw={args.bw_priority} fb={args.fb_priority}  seed={args.seed}")
    use_real_ground = not args.free_space
    center_freq = float(args.center_freq)

    global USER_TUNE_PREFS
    USER_TUNE_PREFS = DEFAULT_TUNE_PREFS.copy()
    if args.gain_priority is not None:
        USER_TUNE_PREFS["gain"] = int(clamp(args.gain_priority, 0, 100))
    if args.swr_priority is not None:
        USER_TUNE_PREFS["swr"] = int(clamp(args.swr_priority, 0, 100))
    if args.rl_priority is not None:
        USER_TUNE_PREFS["return_loss"] = int(clamp(args.rl_priority, 0, 100))
    if args.bw_priority is not None:
        USER_TUNE_PREFS["bandwidth"] = int(clamp(args.bw_priority, 0, 100))
    if args.fb_priority is not None:
        USER_TUNE_PREFS["front_to_back"] = int(clamp(args.fb_priority, 0, 100))
    if args.interactive:
        center_freq, USER_TUNE_PREFS = interactive_startup(center_freq, USER_TUNE_PREFS)


    global USER_N_ELEMENTS, USER_POLARIZATION
    if args.elements is not None:
        USER_N_ELEMENTS = int(clamp(args.elements, 2, 18))
    if args.polarization is not None:
        USER_POLARIZATION = args.polarization
    if USER_N_ELEMENTS not in SUPPORTED_N_ELEMENTS:
        print("")
        print("[error] " + str(USER_N_ELEMENTS) + "-element design not supported. Choose 2-18.")
        sys.exit(2)
    _requested_n = USER_N_ELEMENTS
    global SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT, ELEMENT_NAMES
    SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT = make_seed(USER_N_ELEMENTS, center_freq, SEED_HEIGHT_FT)
    # OPT_LEARN_v2_promoted: check for permanent seed first
    _promoted = _load_promoted_seed(USER_N_ELEMENTS) if not getattr(args, 'no_learn', False) else None
    if _promoted is not None:
        SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT = _promoted
    # OPT_LEARN_v1: historical-seed override (runs before boom rescale)
    elif not getattr(args, 'no_learn', False) and int(getattr(args, 'learn_from', 0)) > 0:
        _learn_cands = _load_top_historical_seed(
            n_elements=USER_N_ELEMENTS,
            center_freq_mhz=center_freq,
            limit=int(args.learn_from),
            min_gain=float(args.learn_min_gain),
            min_score=float(args.learn_min_score),
            db_path=getattr(args, 'learn_db', '') or '',
        )
        if _learn_cands:
            _adj, _gain, _sc, _cf, _L, _S, _H, _rid = _learn_cands[0]
            print(f"[learn] {len(_learn_cands)} viable historical run(s) match {USER_N_ELEMENTS}-el regime")
            print(f"[learn] using best as seed: run #{_rid}, gain={_gain:.2f}dB, score={_sc:.1f}, cf={_cf:.3f}MHz")
            print("[learn] top 3: " + ", ".join(f"#{c[7]}({c[1]:.2f}dB)" for c in _learn_cands[:3]))
            SEED_LENGTHS_FT = list(_L)
            SEED_SPACINGS_FT = list(_S)
            SEED_HEIGHT_FT = float(_H)
        else:
            print(f"[learn] no viable historical runs in last {int(args.learn_from)} (filters: gain>={args.learn_min_gain}dB, score>={args.learn_min_score}); using uniform seed")
    # BOOM_RESCALE_v1: rescale seed spacings to match locked/target boom length
    if BOOM_TARGET_FT > 0 and len(SEED_SPACINGS_FT) > 0:
        _cur_boom = float(sum(SEED_SPACINGS_FT))
        if _cur_boom > 0:
            _scale = BOOM_TARGET_FT / _cur_boom
            if BOOM_LOCK or abs(_scale - 1.0) > 0.15:
                import numpy as _np
                SEED_SPACINGS_FT = (_np.asarray(SEED_SPACINGS_FT, dtype=float) * _scale).tolist()
                print(f"[boom] seed spacings rescaled by {_scale:.3f} "
                      f"(old boom={_cur_boom:.2f}ft -> new boom={sum(SEED_SPACINGS_FT):.2f}ft)")
    ELEMENT_NAMES = make_element_names(USER_N_ELEMENTS)
    _PLAN = make_plan(USER_N_ELEMENTS)
    print("[geometry] N=" + str(USER_N_ELEMENTS) + " elements, freq=" + format(center_freq, ".3f") + " MHz, boom=" + format(sum(SEED_SPACINGS_FT), ".2f") + " ft")
    print_tune_preferences(center_freq, USER_TUNE_PREFS)
    if args.workers != 1:
        print("[WARN] forcing workers=1 for necpp stability")
        args.workers = 1

    freqs_opt = make_centered_freqs(center_freq, fast=args.fast, quick=(args.quick or args.fast))
    freqs_final = make_final_sweep_freqs(center_freq, fast=args.fast, quick=(args.quick or args.fast))

    ok, gain_enabled = run_preflight(use_real_ground, center_freq, requested_gain_probe=True)
    if not ok:
        print("\nFix the preflight problem before running optimization.")
        sys.exit(2)

    if args.preflight_only:
        print("\nPreflight-only mode requested. Exiting.")
        sys.exit(0)

    seed_x = pack_design(SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT)
    logbook = []

    print("\nStarting optimization with coordinate placement search...")
    print(f"Ground mode: {'real ground' if use_real_ground else 'free space'}")
    print(f"Center frequency target: {center_freq:.3f} MHz")
    print(f"Optimization frequencies: {', '.join(f'{f:.3f}' for f in freqs_opt)}")
    print(f"Final sweep range: {freqs_final[0]:.3f} to {freqs_final[-1]:.3f} MHz ({len(freqs_final)} points)")
    print(f"Target RL near center: {args.target_rl:.1f} dB")
    print(f"Gain-aware search: {'enabled' if gain_enabled else 'disabled'}")
    print(f"Baseline DE maxiter={args.stage_maxiter}, popsize={args.stage_popsize}")
    print(f"Position passes per step: {args.position_passes}")
    print(f"Length passes per step:   {args.length_passes}")
    print("No files will be written.\n")

    # ================================================================
    # 9-STAGE PIPELINE
    # ----------------------------------------------------------------
    # 1. SWR baseline      (DE global)           -- drive SWR low
    # 2. RL baseline + all-spacings refine       -- max RL via geometry
    # 3. Match lock        (REF, DE, D1) pos+len -- center freq + 50 ohms
    # 4. Length sweep      (REF, DE, D1, D2)     -- length-only
    # 5. Spacing refine    (DE, D1, D2)          -- position-only
    # 6. Gain region       (D2, D3) pos+len      -- forward gain
    # 7. Polish            (D4, D5) pos+len      -- F/B + pattern
    # 8. Micro-refit #1    (REF, DE, D1) small steps
    # 9. Micro-refit #2    (REF, DE, D1) HALF steps
    # ================================================================

    print("\n=== Stage 1: SWR baseline (DE global) ===")
    baseline_swr = run_stage(
        stage_name="swrmatch",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        measure_gain=False,
        maxiter=args.stage_maxiter,
        popsize=args.stage_popsize,
        seed=args.seed,
        workers=args.workers,
        seed_x=seed_x,
    )

    evaluate_layout_candidate = evaluate_layout_candidate_factory(
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
    )

    baseline_swr_scored = evaluate_layout_candidate(
        baseline_swr["x_full"], label="stage1_swr", accepted=True,
        note="Stage 1: SWR baseline",
    )
    print_stage_result("STAGE 1: SWR BASELINE", baseline_swr_scored)

    print("\n=== Stage 2: Return-loss baseline + all-spacings refine ===")
    baseline_rl = run_stage(
        stage_name="returnloss",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        measure_gain=False,
        maxiter=args.stage_maxiter,
        popsize=args.stage_popsize,
        seed=args.seed + 1,
        workers=args.workers,
        seed_x=baseline_swr["x_full"],
    )
    baseline_rl_scored = evaluate_layout_candidate(
        baseline_rl["x_full"], label="stage2_rl", accepted=True,
        note="Stage 2: RL baseline",
    )
    print_stage_result("STAGE 2: RETURN-LOSS BASELINE", baseline_rl_scored)
    current = baseline_rl_scored

    current = coordinate_region_position_search(
        start_rec=current, search_name="stage2_all_spacings",
        step_sizes_in=[1.5, 0.75], passes_per_step=args.position_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage2_all_spacings"],
        print_fn=print_search_record,
    )

    # Stage 3 - MATCH LOCK (REF, DE, D1)
    current = coordinate_region_position_search(
        start_rec=current, search_name="stage3_match_position",
        step_sizes_in=[1.5, 0.75], passes_per_step=args.position_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage3_match_pos"], print_fn=print_search_record,
    )
    current = coordinate_region_length_search(
        start_rec=current, search_name="stage3_match_length",
        step_sizes_in=[0.25, 0.125], passes_per_step=args.length_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage3_match_len"], print_fn=print_search_record,
    )

    # Stage 4 - LENGTH SWEEP (REF, DE, D1, D2)
    current = coordinate_region_length_search(
        start_rec=current, search_name="stage4_length_sweep",
        step_sizes_in=[0.25, 0.125], passes_per_step=args.length_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage4_length_sweep"], print_fn=print_search_record,
    )

    # Stage 5 - SPACING REFINE (DE, D1, D2)
    current = coordinate_region_position_search(
        start_rec=current, search_name="stage5_spacing_refine",
        step_sizes_in=[0.75, 0.375], passes_per_step=args.position_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage5_spacing_refine"], print_fn=print_search_record,
    )

    # Stage 6 - GAIN REGION (D2, D3)
    current = coordinate_region_position_search(
        start_rec=current, search_name="stage6_gain_position",
        step_sizes_in=[1.5, 0.75], passes_per_step=args.position_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage6_gain_pos"], print_fn=print_search_record,
    )
    current = coordinate_region_length_search(
        start_rec=current, search_name="stage6_gain_length",
        step_sizes_in=[0.25, 0.125], passes_per_step=args.length_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage6_gain_len"], print_fn=print_search_record,
    )

    # Stage 7 - POLISH (D4, D5)
    current = coordinate_region_position_search(
        start_rec=current, search_name="stage7_polish_position",
        step_sizes_in=[0.75, 0.375], passes_per_step=args.position_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage7_polish_pos"], print_fn=print_search_record,
    )
    current = coordinate_region_length_search(
        start_rec=current, search_name="stage7_polish_length",
        step_sizes_in=[0.125, 0.0625], passes_per_step=args.length_passes,
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage7_polish_len"], print_fn=print_search_record,
    )

    # Stage 8 - MICRO-REFIT #1 (REF, DE, D1 small steps)
    current = coordinate_region_position_search(
        start_rec=current, search_name="stage8_refit1_position",
        step_sizes_in=[0.25, 0.125],
        passes_per_step=max(1, args.position_passes - 1),
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage8_refit_pos"], print_fn=print_search_record,
    )
    current = coordinate_region_length_search(
        start_rec=current, search_name="stage8_refit1_length",
        step_sizes_in=[0.0625],
        passes_per_step=max(1, args.length_passes - 1),
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage8_refit_len"], print_fn=print_search_record,
    )

    # Stage 9 - MICRO-REFIT #2 (HALF step sizes)
    current = coordinate_region_position_search(
        start_rec=current, search_name="stage9_refit2_position",
        step_sizes_in=[0.125, 0.0625],
        passes_per_step=max(1, args.position_passes - 1),
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage9_refit_pos"], print_fn=print_search_record,
    )
    current = coordinate_region_length_search(
        start_rec=current, search_name="stage9_refit2_length",
        step_sizes_in=[0.03125],
        passes_per_step=max(1, args.length_passes - 1),
        evaluate_fn=evaluate_layout_candidate, element_names=ELEMENT_NAMES,
        element_indices=_PLAN["stage9_refit_len"], print_fn=print_search_record,
    )

    print_top_layouts(logbook, top_n=12)

    final_record = choose_best_logged_layout(logbook)

    print("\nFINAL LAYOUT DECISION")
    print("=====================")
    print("Selected the highest-scoring logged layout using the combined score.")
    print_search_record(final_record, prefix="  ")

    lengths = final_record["lengths"]
    spacings = final_record["spacings"]
    height = final_record["height"]

    print_design(
        lengths, spacings, height,
        element_names=ELEMENT_NAMES,
        reflector_min_over_de_ft=REFLECTOR_MIN_OVER_DE_FT,
        reflector_max_over_de_ft=REFLECTOR_MAX_OVER_DE_FT,
        taper_center_diameter_in=TAPER_CENTER_DIAMETER_IN,
        taper_outer_diameter_in=TAPER_OUTER_DIAMETER_IN,
    )

    print_center_result(
        lengths, spacings, height,
        center_freq=center_freq,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        solve_impedance_fn=solve_impedance,
        estimate_pattern_fn=estimate_pattern,
    )

    print("\nRunning final sweep...")
    z, rl_report, rl_raw, swr, eta = sweep_design(
        lengths, spacings, height, freqs_final, use_real_ground, solve_impedance
    )
    print_sweep_summary(freqs_final, z, rl_report, rl_raw, swr, eta, args.target_rl, center_freq)

    if gain_enabled:
        fwd_gain, rear_gain, f2b = estimate_pattern(lengths, spacings, height, center_freq, use_real_ground)
        if np.isfinite(fwd_gain):
            print("\nCENTER PATTERN PROXY")
            print("====================")
            print(f"Forward gain (+Y): {fwd_gain:.2f} dB")
            print(f"Rear gain (-Y):    {rear_gain:.2f} dB")
            print(f"Front-to-back:     {f2b:.2f} dB")

    # --- auto-save: if user did not specify a path, generate one ---
    if not args.export_nec or args.export_nec == "AUTO":
        _stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") if "datetime" in dir() else __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        _outdir = __import__("pathlib").Path(__file__).resolve().parent
        _outdir.mkdir(parents=True, exist_ok=True)
        args.export_nec = str(_outdir / f"yagi_auto_{_stamp}.nec")
        print(f"Auto-saving NEC deck to: {args.export_nec}")
    if args.export_nec:
        extra = [
            f"Feed Z at {center_freq:.3f} MHz: {final_record['metrics']['center_R']:.2f} {final_record['metrics']['center_X']:+.2f}j ohms",
            f"Return loss (raw): {final_record['metrics']['center_rl_raw']:.2f} dB",
            f"SWR: {final_record['metrics']['center_swr']:.3f}",
        ]
        if np.isfinite(final_record['metrics'].get('forward_gain_db', np.nan)):
            extra.append(f"Forward gain proxy: {final_record['metrics']['forward_gain_db']:.2f} dB")
            extra.append(f"Front-to-back:      {final_record['metrics']['front_to_back_db']:.2f} dB")
        path = write_nec_deck(
            args.export_nec,
            lengths, spacings, height,
            freq_mhz=center_freq,
            use_real_ground=use_real_ground,
            element_names=ELEMENT_NAMES,
            extra_comments=extra,
        )
        print(f"\nNEC deck written to: {path}")
        print("Load it in EZNEC via  File -> Import  (or 4nec2 / xnec2c etc.)")
    else:
        print("\nNo .nec file written (pass --export-nec FILE to save one).")

    if not args.no_history:
        try:
            from yagiopt import history as _H
            _m = final_record.get("metrics", {}) or {}
            _fm = {
                "swr":      _m.get("center_swr"),
                "rl_db":    _m.get("center_rl_raw"),
                "local_rl": _m.get("local_min_rl"),
                "bw_mhz":   _m.get("bandwidth_mhz"),
                "gain_db":  _m.get("forward_gain_db"),
                "fb_db":    _m.get("front_to_back_db"),
                "score":    final_record.get("score"),
            }
            _g = {"lengths_ft": list(final_record.get("lengths", [])),
                  "spacings_ft": list(final_record.get("spacings", [])),
                  "height_ft": final_record.get("height")}
            import re as _re
            def _parse_note(n):
                if not n: return {}
                m = _re.match(r"(REF|DE|D[1-5])\s+(longer|shorter|forward|backward)\s+([0-9.]+)\s*in", str(n))
                if m: return {"element": m.group(1), "action": m.group(2), "delta_in": float(m.group(3))}
                return {}
            _st = []
            for r in (logbook or []):
                _rm = r.get("metrics") or {}
                _entry = {
                    "name":     r.get("search_name") or r.get("label"),
                    "note":     r.get("note"),
                    "score":    r.get("score"),
                    "accepted": r.get("accepted"),
                    "swr":      _rm.get("center_swr"),
                    "rl_db":    _rm.get("center_rl_raw"),
                    "local_rl": _rm.get("local_min_rl"),
                    "bw_mhz":   _rm.get("bandwidth_mhz"),
                    "gain_db":  _rm.get("forward_gain_db"),
                    "fb_db":    _rm.get("front_to_back_db"),
                    "local_max_swr": _rm.get("local_max_swr"),
                }
                _entry.update(_parse_note(r.get("note")))
                _st.append(_entry)
            _rid = _H.save_run(
                center_freq=center_freq,
                seed=getattr(args,"seed",None),
                priorities=USER_TUNE_PREFS,
                final_metrics=_fm, geometry=_g, stages=_st,
                winner_stage=final_record.get("search_name") or final_record.get("label"),
                winner_note=final_record.get("note"),
                tag=(lambda _t: (f"n{_requested_n}p={USER_POLARIZATION}" + (f"|{_t}" if _t else "")))(getattr(args,"tag",None)),
                nec_file_path=getattr(args,"export_nec",None),
            )
            print("\n[history] run saved as #" + str(_rid) + " in ~/scripts/yagi_history.db")
            print("[history] browse with:  yagihist recent 5   |   yagihist show " + str(_rid))
        except Exception as _e:
            print("\n[history] WARNING could not save run:", _e)


if __name__ == "__main__":
    main()
