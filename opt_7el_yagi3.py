#!/usr/bin/env python3
"""
opt_7el_yagi2.py

7-element flat/horizontal Yagi staged optimizer centered around 27.195 MHz.

Workflow in this version:
    1) Baseline SWR / match tune
    2) Baseline return-loss tune
    3) Coordinate placement search:
         - move elements forward/backward
         - keep only improving moves
         - coarse then fine passes
    4) Small length-trim search
    5) Final best-layout selection from the full logged candidate set

Console-only debug version:
    - no file output
    - prints search history and top layouts

Requires:
    pip3 install numpy scipy necpp
"""

import argparse
import math
import sys
from typing import Optional

import numpy as np
from scipy.optimize import differential_evolution

try:
    import necpp
except ImportError:
    print("ERROR: necpp is not installed. Try: pip3 install necpp")
    sys.exit(1)


FT = 0.3048
IN = 0.0254
Z0 = 50.0

RL_REPORT_CLIP_DB = 40.0
RL_REWARD_CLIP_DB = 30.0

REFLECTOR_MIN_OVER_DE_FT = 2.0 / 12.0
REFLECTOR_MAX_OVER_DE_FT = 18.0 / 12.0

TAPER_CENTER_DIAMETER_IN = 0.625   # 5/8"
TAPER_OUTER_DIAMETER_IN = 0.500    # 1/2"
TAPER_CENTER_SECTION_FT = 6.0
TAPER_MIN_OUTER_SECTION_FT = 2.0
TAPER_MIN_CENTER_SECTION_FT = 2.0

OUTER_SECTION_SEGMENTS = 11
CENTER_SECTION_SEGMENTS = 21  # must be odd for center feed

ALUMINUM_SIGMA = 3.5e7

DEFAULT_USE_REAL_GROUND = True
GROUND_EPSR = 13.0
GROUND_SIGMA = 0.005

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

NEC_IMPEDANCE_CACHE = {}
NEC_GAIN_CACHE = {}


def ftin(value_ft, denom=16):
    sign = "-" if value_ft < 0 else ""
    v = abs(float(value_ft))
    feet = int(math.floor(v))
    inches = (v - feet) * 12.0
    ticks = int(round(inches * denom))

    if ticks >= 12 * denom:
        feet += 1
        ticks -= 12 * denom

    whole_in = ticks // denom
    frac = ticks % denom

    if frac == 0:
        return f"{sign}{feet} ft {whole_in} in"

    g = math.gcd(frac, denom)
    frac_n = frac // g
    frac_d = denom // g
    return f"{sign}{feet} ft {whole_in} {frac_n}/{frac_d} in"


def inches_to_ft(inches):
    return float(inches) / 12.0


def pack_design(lengths, spacings, height):
    return np.concatenate([
        np.asarray(lengths, dtype=float),
        np.asarray(spacings, dtype=float),
        np.array([float(height)], dtype=float),
    ])


def unpack_design(x):
    x = np.asarray(x, dtype=float)
    lengths = np.array(x[:7], dtype=float)
    spacings = np.array(x[7:13], dtype=float)
    height = float(x[13])
    return lengths, spacings, height


def active_from_full(x_full, active_idx):
    return np.asarray(x_full, dtype=float)[np.asarray(active_idx, dtype=int)]


def full_from_active(x_active, base_full, active_idx):
    x = np.asarray(base_full, dtype=float).copy()
    x[np.asarray(active_idx, dtype=int)] = np.asarray(x_active, dtype=float)
    return x


def y_positions_from_spacings(spacings_ft):
    y = [0.0]
    for s in np.asarray(spacings_ft, dtype=float):
        y.append(y[-1] + s)
    return np.array(y, dtype=float)


def is_finite_complex(z):
    return np.isfinite(z.real) and np.isfinite(z.imag)


def gamma_from_z(z, z0=Z0):
    if not is_finite_complex(z):
        return complex(np.nan, np.nan)
    den = z + z0
    if den == 0:
        return complex(np.nan, np.nan)
    g = (z - z0) / den
    if not is_finite_complex(g):
        return complex(np.nan, np.nan)
    return g


def return_loss_db_raw(z, z0=Z0):
    g = gamma_from_z(z, z0)
    if not is_finite_complex(g):
        return np.nan
    mag = abs(g)
    if not np.isfinite(mag):
        return np.nan
    mag = max(mag, 1e-15)
    return -20.0 * math.log10(mag)


def return_loss_db(z, z0=Z0, clip_db=RL_REPORT_CLIP_DB):
    rl = return_loss_db_raw(z, z0)
    if not np.isfinite(rl):
        return np.nan
    return min(rl, clip_db)


def swr_from_z(z, z0=Z0):
    g = gamma_from_z(z, z0)
    if not is_finite_complex(g):
        return 999.0
    mag = abs(g)
    if not np.isfinite(mag):
        return 999.0
    mag = max(0.0, min(float(mag), 0.999999))
    return (1.0 + mag) / (1.0 - mag)


def mismatch_efficiency_percent(z, z0=Z0):
    g = gamma_from_z(z, z0)
    if not is_finite_complex(g):
        return np.nan
    eta = 1.0 - abs(g) ** 2
    if not np.isfinite(eta):
        return np.nan
    eta = max(0.0, min(1.0, eta))
    return 100.0 * eta


def geometry_is_valid(lengths, spacings, height_ft):
    lengths = np.asarray(lengths, dtype=float)
    spacings = np.asarray(spacings, dtype=float)

    if lengths.shape != (7,) or spacings.shape != (6,):
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
    if boom < 10.0 or boom > 80.0:
        return False

    y = y_positions_from_spacings(spacings)
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

    for i in range(2, 6):
        if lengths[i + 1] > lengths[i]:
            p += 700.0 * (lengths[i + 1] - lengths[i]) ** 2

    for i in range(1, 5):
        if spacings[i + 1] < spacings[i]:
            p += 650.0 * (spacings[i] - spacings[i + 1]) ** 2

    boom = np.sum(spacings)
    if boom < 22.0:
        p += 20.0 * (22.0 - boom) ** 2
    if boom > 40.0:
        p += 20.0 * (boom - 40.0) ** 2

    if height < 20.0:
        p += 12.0 * (20.0 - height) ** 2
    if height > 100.0:
        p += 3.0 * (height - 100.0) ** 2

    return float(p)


def _ground_variants(use_real_ground):
    if use_real_ground:
        return ["real2", "real0", "perfect"]
    return ["free"]


def apply_ground(nec, ground_variant):
    if ground_variant == "free":
        return necpp.nec_gn_card(nec, -1, 0, 0, 0, 0, 0, 0, 0)

    if ground_variant == "real2":
        return necpp.nec_gn_card(nec, 2, 0, 0, 0, GROUND_EPSR, GROUND_SIGMA, 0, 0)

    if ground_variant == "real0":
        return necpp.nec_gn_card(nec, 0, 0, 0, 0, GROUND_EPSR, GROUND_SIGMA, 0, 0)

    if ground_variant == "perfect":
        return necpp.nec_gn_card(nec, 1, 0, 0, 0, 0, 0, 0, 0)

    raise ValueError(f"Unknown ground variant: {ground_variant}")


def element_part_tags(element_index_zero_based):
    base = 3 * int(element_index_zero_based) + 1
    return base, base + 1, base + 2


def stepped_element_section_lengths_ft(total_length_ft):
    total = float(total_length_ft)

    center_len = min(TAPER_CENTER_SECTION_FT, total - 2.0 * TAPER_MIN_OUTER_SECTION_FT)
    center_len = max(center_len, TAPER_MIN_CENTER_SECTION_FT)

    if total - center_len < 2.0 * TAPER_MIN_OUTER_SECTION_FT:
        center_len = total - 2.0 * TAPER_MIN_OUTER_SECTION_FT

    center_len = max(center_len, TAPER_MIN_CENTER_SECTION_FT)

    if center_len >= total:
        center_len = 0.5 * total

    outer_each = 0.5 * (total - center_len)
    if outer_each <= 0.0:
        outer_each = max(0.25, 0.25 * total)
        center_len = total - 2.0 * outer_each

    center_len = max(0.1, center_len)
    outer_each = max(0.05, outer_each)
    return center_len, outer_each


def add_tapered_element(nec, element_index_zero_based, length_ft, y_ft, z_ft):
    tag_left, tag_center, tag_right = element_part_tags(element_index_zero_based)
    center_len_ft, _ = stepped_element_section_lengths_ft(length_ft)

    half_total_m = 0.5 * float(length_ft) * FT
    half_center_m = 0.5 * float(center_len_ft) * FT
    y_m = float(y_ft) * FT
    z_m = float(z_ft) * FT

    r_center = 0.5 * TAPER_CENTER_DIAMETER_IN * IN
    r_outer = 0.5 * TAPER_OUTER_DIAMETER_IN * IN

    ret = necpp.nec_wire(
        nec, tag_left, OUTER_SECTION_SEGMENTS,
        -half_total_m, y_m, z_m,
        -half_center_m, y_m, z_m,
        r_outer, 1.0, 1.0
    )
    if ret != 0:
        raise RuntimeError(
            f"nec_wire failed on left outer section of element {element_index_zero_based + 1} with code {ret}"
        )

    ret = necpp.nec_wire(
        nec, tag_center, CENTER_SECTION_SEGMENTS,
        -half_center_m, y_m, z_m,
        half_center_m, y_m, z_m,
        r_center, 1.0, 1.0
    )
    if ret != 0:
        raise RuntimeError(
            f"nec_wire failed on center section of element {element_index_zero_based + 1} with code {ret}"
        )

    ret = necpp.nec_wire(
        nec, tag_right, OUTER_SECTION_SEGMENTS,
        half_center_m, y_m, z_m,
        half_total_m, y_m, z_m,
        r_outer, 1.0, 1.0
    )
    if ret != 0:
        raise RuntimeError(
            f"nec_wire failed on right outer section of element {element_index_zero_based + 1} with code {ret}"
        )


def _build_nec_context(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant):
    if CENTER_SECTION_SEGMENTS % 2 == 0:
        raise ValueError("CENTER_SECTION_SEGMENTS must be odd")

    if not geometry_is_valid(lengths_ft, spacings_ft, height_ft):
        raise RuntimeError(
            f"Invalid geometry sent to NEC: "
            f"height={height_ft}, lengths={lengths_ft}, spacings={spacings_ft}"
        )

    if not np.isfinite(freq_mhz) or freq_mhz <= 0.0:
        raise RuntimeError(f"Invalid frequency sent to NEC: {freq_mhz}")

    nec = necpp.nec_create()
    if nec is None:
        raise RuntimeError("nec_create returned None")

    y_positions_ft = y_positions_from_spacings(spacings_ft)

    try:
        for i in range(7):
            add_tapered_element(nec, i, lengths_ft[i], y_positions_ft[i], height_ft)

        ge_flag = 0 if ground_variant == "free" else 1
        ret = necpp.nec_geometry_complete(nec, ge_flag)
        if ret != 0:
            raise RuntimeError(f"nec_geometry_complete failed with code {ret}")

        ret = apply_ground(nec, ground_variant)
        if ret != 0:
            raise RuntimeError(f"nec_gn_card failed with code {ret} for ground_variant={ground_variant}")

        ret = necpp.nec_ld_card(nec, 5, 0, 0, 0, ALUMINUM_SIGMA, 0, 0)
        if ret != 0:
            raise RuntimeError(f"nec_ld_card failed with code {ret}")

        ret = necpp.nec_fr_card(nec, 0, 1, float(freq_mhz), 0.0)
        if ret != 0:
            raise RuntimeError(f"nec_fr_card failed with code {ret}")

        _, de_center_tag, _ = element_part_tags(1)
        feed_seg = (CENTER_SECTION_SEGMENTS + 1) // 2

        ret = necpp.nec_ex_card(nec, 0, de_center_tag, feed_seg, 0, 1.0, 0.0, 0, 0, 0, 0)
        if ret != 0:
            raise RuntimeError(f"nec_ex_card failed with code {ret}")

        return nec

    except Exception:
        necpp.nec_delete(nec)
        raise


def _round_tuple(values, ndigits=6):
    return tuple(round(float(v), ndigits) for v in np.asarray(values, dtype=float))


def _impedance_cache_key(lengths_ft, spacings_ft, height_ft, freq_mhz, use_real_ground):
    return (
        _round_tuple(lengths_ft),
        _round_tuple(spacings_ft),
        round(float(height_ft), 6),
        round(float(freq_mhz), 6),
        bool(use_real_ground),
    )


def _gain_cache_key(lengths_ft, spacings_ft, height_ft, freq_mhz, phi_deg, elev_deg, use_real_ground):
    return (
        _round_tuple(lengths_ft),
        _round_tuple(spacings_ft),
        round(float(height_ft), 6),
        round(float(freq_mhz), 6),
        round(float(phi_deg), 3),
        round(float(elev_deg), 3),
        bool(use_real_ground),
    )


def _solve_nec_impedance_once(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant):
    nec = _build_nec_context(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant)
    try:
        ret = necpp.nec_xq_card(nec, 0)
        if ret != 0:
            raise RuntimeError(f"nec_xq_card failed with code {ret}")

        r = float(necpp.nec_impedance_real(nec, 0))
        x = float(necpp.nec_impedance_imag(nec, 0))

        if not (np.isfinite(r) and np.isfinite(x)):
            raise RuntimeError(
                f"NEC returned invalid impedance at {freq_mhz:.3f} MHz: "
                f"R={r}, X={x}, ground_variant={ground_variant}"
            )

        return complex(r, x)
    finally:
        necpp.nec_delete(nec)


def solve_nec_impedance(lengths_ft, spacings_ft, height_ft, freq_mhz,
                        use_real_ground=DEFAULT_USE_REAL_GROUND):
    key = _impedance_cache_key(lengths_ft, spacings_ft, height_ft, freq_mhz, use_real_ground)
    if key in NEC_IMPEDANCE_CACHE:
        return NEC_IMPEDANCE_CACHE[key]

    last_err = None

    for ground_variant in _ground_variants(use_real_ground):
        try:
            z = _solve_nec_impedance_once(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant)
            if is_finite_complex(z):
                NEC_IMPEDANCE_CACHE[key] = z
                return z
        except Exception as e:
            last_err = e

    if last_err is not None:
        raise RuntimeError(str(last_err))

    raise RuntimeError("No valid ground variant produced a finite impedance")


def _extract_first_numeric(value):
    if isinstance(value, (int, float, np.integer, np.floating)):
        v = float(value)
        return v if np.isfinite(v) else None

    if isinstance(value, complex):
        if np.isfinite(value.real) and abs(value.imag) < 1e-12:
            return float(value.real)
        return None

    if isinstance(value, np.ndarray):
        for item in value.flat:
            v = _extract_first_numeric(item)
            if v is not None:
                return v
        return None

    if isinstance(value, (list, tuple)):
        for item in value:
            v = _extract_first_numeric(item)
            if v is not None:
                return v
        return None

    return None


def _try_add_rp_card(nec, theta_deg, phi_deg):
    rp = getattr(necpp, "nec_rp_card", None)
    if rp is None:
        return False

    attempts = [
        (0, 1, 1, 0, 0, 0, 0, theta_deg, phi_deg, 0.0, 0.0, 0.0, 0.0),
        (0, 1, 1, 0, 0, 0, 0, theta_deg, phi_deg, 0.0, 0.0, 0.0, 1.0),
        (0, 1, 1, 0, 0, 0, theta_deg, phi_deg, 0.0, 0.0, 0.0, 0.0),
        (0, 1, 1, 0, 0, theta_deg, phi_deg, 0.0, 0.0, 0.0, 0.0),
    ]

    for args in attempts:
        try:
            ret = rp(nec, *args)
            if ret == 0:
                return True
        except Exception:
            continue
    return False


def _try_get_gain_from_context(nec):
    candidate_names = [
        "nec_gain_max",
        "nec_gain",
        "nec_rp_gain",
        "nec_gain_db",
        "nec_radiation_pattern_gain",
    ]
    candidate_argsets = [
        (nec,),
        (nec, 0),
        (nec, 0, 0),
        (nec, 1),
        (nec, 1, 1),
    ]

    for name in candidate_names:
        fn = getattr(necpp, name, None)
        if fn is None:
            continue
        for args in candidate_argsets:
            try:
                value = fn(*args)
            except Exception:
                continue

            num = _extract_first_numeric(value)
            if num is None:
                continue
            if np.isfinite(num) and -50.0 <= num <= 50.0:
                return float(num)

    return None


def _solve_nec_gain_at_point_once(lengths_ft, spacings_ft, height_ft, freq_mhz,
                                  phi_deg, elev_deg, ground_variant):
    theta_deg = 90.0 - float(elev_deg)
    nec = _build_nec_context(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant)
    try:
        if not _try_add_rp_card(nec, theta_deg, float(phi_deg)):
            return None

        ret = necpp.nec_xq_card(nec, 0)
        if ret != 0:
            return None

        return _try_get_gain_from_context(nec)
    finally:
        necpp.nec_delete(nec)


def solve_nec_gain_at_point(lengths_ft, spacings_ft, height_ft, freq_mhz, phi_deg, elev_deg,
                            use_real_ground=DEFAULT_USE_REAL_GROUND):
    key = _gain_cache_key(lengths_ft, spacings_ft, height_ft, freq_mhz, phi_deg, elev_deg, use_real_ground)
    if key in NEC_GAIN_CACHE:
        return NEC_GAIN_CACHE[key]

    for ground_variant in _ground_variants(use_real_ground):
        try:
            g = _solve_nec_gain_at_point_once(
                lengths_ft, spacings_ft, height_ft, freq_mhz,
                phi_deg, elev_deg, ground_variant
            )
            if g is not None and np.isfinite(g):
                NEC_GAIN_CACHE[key] = float(g)
                return float(g)
        except Exception:
            continue

    NEC_GAIN_CACHE[key] = None
    return None


def estimate_pattern_metrics(lengths_ft, spacings_ft, height_ft, freq_mhz, use_real_ground,
                             elev_samples_deg=(0.0, 5.0, 10.0, 15.0)):
    fwd_samples = []
    rear_samples = []

    for elev in elev_samples_deg:
        gf = solve_nec_gain_at_point(lengths_ft, spacings_ft, height_ft, freq_mhz, 90.0, elev, use_real_ground)
        gr = solve_nec_gain_at_point(lengths_ft, spacings_ft, height_ft, freq_mhz, 270.0, elev, use_real_ground)

        if gf is None or gr is None:
            return np.nan, np.nan, np.nan
        if not (np.isfinite(gf) and np.isfinite(gr)):
            return np.nan, np.nan, np.nan

        fwd_samples.append(float(gf))
        rear_samples.append(float(gr))

    if not fwd_samples:
        return np.nan, np.nan, np.nan

    fwd = float(np.max(fwd_samples))
    rear = float(np.max(rear_samples))
    f2b = fwd - rear
    return fwd, rear, f2b


def probe_gain_support(use_real_ground, center_freq):
    try:
        fwd_gain, rear_gain, f2b = estimate_pattern_metrics(
            SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT, center_freq, use_real_ground
        )
        return np.isfinite(fwd_gain) and np.isfinite(rear_gain) and np.isfinite(f2b)
    except Exception:
        return False


def make_centered_freqs(center_freq, fast=False, quick=False):
    if fast:
        offsets = np.array([-0.35, 0.00, 0.35], dtype=float)
    elif quick:
        offsets = np.array([-0.50, -0.25, 0.00, 0.25, 0.50], dtype=float)
    else:
        offsets = np.array(
            [-0.80, -0.50, -0.30, -0.18, -0.08, 0.00, 0.08, 0.18, 0.30, 0.50, 0.80],
            dtype=float
        )
    return center_freq + offsets


def make_final_sweep_freqs(center_freq, fast=False, quick=False):
    if fast:
        return np.linspace(center_freq - 0.8, center_freq + 0.8, 17)
    if quick:
        return np.linspace(center_freq - 1.0, center_freq + 1.0, 33)
    return np.linspace(center_freq - 1.5, center_freq + 1.5, 61)


def approximate_bandwidth(freqs_mhz, rl_db, target_rl_db):
    freqs = np.asarray(freqs_mhz, dtype=float)
    rl = np.asarray(rl_db, dtype=float)

    if freqs.ndim != 1 or rl.ndim != 1 or len(freqs) != len(rl) or len(freqs) == 0:
        return 0.0, np.nan, np.nan

    mask = np.isfinite(freqs) & np.isfinite(rl) & (rl >= float(target_rl_db))
    if not np.any(mask):
        return 0.0, np.nan, np.nan

    idx = np.where(mask)[0]
    best_start = idx[0]
    best_end = idx[0]
    cur_start = idx[0]
    cur_end = idx[0]

    for k in idx[1:]:
        if k == cur_end + 1:
            cur_end = k
        else:
            if freqs[cur_end] - freqs[cur_start] > freqs[best_end] - freqs[best_start]:
                best_start, best_end = cur_start, cur_end
            cur_start = k
            cur_end = k

    if freqs[cur_end] - freqs[cur_start] > freqs[best_end] - freqs[best_start]:
        best_start, best_end = cur_start, cur_end

    f1 = float(freqs[best_start])
    f2 = float(freqs[best_end])
    bw = max(0.0, f2 - f1)
    return bw, f1, f2


def sweep_design(lengths, spacings, height, freqs_mhz, use_real_ground):
    freqs = np.asarray(freqs_mhz, dtype=float)

    z_list = []
    rl_report_list = []
    rl_raw_list = []
    swr_list = []
    eta_list = []

    for f in freqs:
        try:
            zi = solve_nec_impedance(lengths, spacings, height, float(f), use_real_ground)
            rl_raw_i = return_loss_db_raw(zi)
            rl_report_i = return_loss_db(zi, clip_db=RL_REPORT_CLIP_DB)
            swr_i = swr_from_z(zi)
            eta_i = mismatch_efficiency_percent(zi)

            if not np.isfinite(eta_i):
                eta_i = 0.0

        except Exception:
            zi = complex(np.nan, np.nan)
            rl_raw_i = np.nan
            rl_report_i = np.nan
            swr_i = 999.0
            eta_i = 0.0

        z_list.append(zi)
        rl_report_list.append(float(rl_report_i) if np.isfinite(rl_report_i) else np.nan)
        rl_raw_list.append(float(rl_raw_i) if np.isfinite(rl_raw_i) else np.nan)
        swr_list.append(float(swr_i) if np.isfinite(swr_i) else 999.0)
        eta_list.append(float(eta_i) if np.isfinite(eta_i) else 0.0)

    return (
        np.asarray(z_list, dtype=complex),
        np.asarray(rl_report_list, dtype=float),
        np.asarray(rl_raw_list, dtype=float),
        np.asarray(swr_list, dtype=float),
        np.asarray(eta_list, dtype=float),
    )


def _find_center_impedance_from_sweep(freqs_opt, z, center_freq) -> Optional[complex]:
    freqs_opt = np.asarray(freqs_opt, dtype=float)
    z = np.asarray(z, dtype=complex)

    idx = np.where(np.isclose(freqs_opt, center_freq, atol=1e-12))[0]
    if idx.size == 0:
        return None

    zi = z[idx[0]]
    if is_finite_complex(zi):
        return complex(zi.real, zi.imag)
    return None


def evaluate_candidate_metrics(lengths, spacings, height, freqs_opt, center_freq, target_rl_db,
                               use_real_ground, measure_gain):
    z, rl_report, rl_raw, swr, eta = sweep_design(lengths, spacings, height, freqs_opt, use_real_ground)

    zc = _find_center_impedance_from_sweep(freqs_opt, z, center_freq)
    if zc is None:
        zc = solve_nec_impedance(lengths, spacings, height, center_freq, use_real_ground)

    if not (np.isfinite(zc.real) and np.isfinite(zc.imag)):
        raise RuntimeError(f"Invalid center impedance at {center_freq:.3f} MHz: {zc}")

    rl_report = np.asarray(rl_report, dtype=float)
    rl_raw = np.asarray(rl_raw, dtype=float)
    swr = np.asarray(swr, dtype=float)
    eta = np.asarray(eta, dtype=float)
    freqs_opt = np.asarray(freqs_opt, dtype=float)

    rl_reward = np.where(np.isfinite(rl_raw), np.minimum(rl_raw, RL_REWARD_CLIP_DB), -20.0)
    swr_safe = np.where(np.isfinite(swr), swr, 999.0)

    center_rl_raw = return_loss_db_raw(zc)
    if not np.isfinite(center_rl_raw):
        raise RuntimeError(f"Center RL is non-finite for Z={zc}")

    center_rl_report = min(center_rl_raw, RL_REPORT_CLIP_DB)
    center_rl_reward = min(center_rl_raw, RL_REWARD_CLIP_DB)

    center_swr = swr_from_z(zc)
    center_eta = mismatch_efficiency_percent(zc)
    if not np.isfinite(center_eta):
        center_eta = 0.0

    weights = np.exp(-((freqs_opt - center_freq) / 0.20) ** 2)
    weights = weights / max(np.mean(weights), 1e-12)

    shortfall = np.maximum(0.0, target_rl_db - rl_reward)
    weighted_shortfall = float(np.mean(weights * (shortfall ** 2)))

    local_mask = np.abs(freqs_opt - center_freq) <= 0.35
    if not np.any(local_mask):
        local_mask[np.argmin(np.abs(freqs_opt - center_freq))] = True

    local_min_rl = float(np.min(rl_reward[local_mask]))
    local_mean_rl = float(np.mean(rl_reward[local_mask]))
    local_max_swr = float(np.max(swr_safe[local_mask]))
    local_mean_swr = float(np.mean(swr_safe[local_mask]))

    bw, _, _ = approximate_bandwidth(freqs_opt, rl_reward, target_rl_db)

    forward_gain_db = np.nan
    rear_gain_db = np.nan
    front_to_back_db = np.nan

    if measure_gain:
        forward_gain_db, rear_gain_db, front_to_back_db = estimate_pattern_metrics(
            lengths, spacings, height, center_freq, use_real_ground
        )

    return {
        "center_freq": center_freq,
        "center_R": float(zc.real),
        "center_X": float(zc.imag),
        "center_rl_raw": float(center_rl_raw),
        "center_rl_report": float(center_rl_report),
        "center_rl_reward": float(center_rl_reward),
        "center_swr": float(center_swr),
        "center_eta": float(center_eta),
        "local_min_rl": local_min_rl,
        "local_mean_rl": local_mean_rl,
        "local_max_swr": local_max_swr,
        "local_mean_swr": local_mean_swr,
        "weighted_shortfall": weighted_shortfall,
        "bandwidth_mhz": float(bw),
        "forward_gain_db": float(forward_gain_db) if np.isfinite(forward_gain_db) else np.nan,
        "rear_gain_db": float(rear_gain_db) if np.isfinite(rear_gain_db) else np.nan,
        "front_to_back_db": float(front_to_back_db) if np.isfinite(front_to_back_db) else np.nan,
        "r_err": (float(zc.real) - 50.0) / 8.0,
        "x_err": float(zc.imag) / 8.0,
    }


def compute_stage_cost(stage_name, metrics, lengths, spacings, height, use_gain_in_cost):
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


def get_stage_active_idx(stage_name):
    if stage_name == "swrmatch":
        return [0, 1, 2, 7, 8, 13]
    if stage_name == "returnloss":
        return [0, 1, 2, 7, 8, 9, 13]
    return list(range(14))


def _sanitize_bound(lo, hi, hard_lo, hard_hi):
    lo = max(float(lo), float(hard_lo))
    hi = min(float(hi), float(hard_hi))

    if not (np.isfinite(lo) and np.isfinite(hi)):
        raise ValueError(f"Non-finite bound after sanitization: lo={lo}, hi={hi}")

    if lo > hi:
        lo, hi = hi, lo

    if math.isclose(lo, hi, rel_tol=0.0, abs_tol=1e-9):
        pad = max(1e-5, 1e-4 * max(1.0, abs(lo)))
        lo = max(hard_lo, lo - pad)
        hi = min(hard_hi, hi + pad)

    if lo >= hi:
        hi = min(hard_hi, lo + 1e-5)
        if lo >= hi:
            lo = max(hard_lo, hi - 1e-5)

    return float(lo), float(hi)


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

        bounds.append(_sanitize_bound(lo, hi, 8.0, 30.0))

    for i, s in enumerate(spacings):
        if stage_name == "swrmatch":
            lo, hi = ((0.60 * s, 1.60 * s) if i in (0, 1) else (0.99 * s, 1.01 * s))
        elif stage_name == "returnloss":
            lo, hi = ((0.75 * s, 1.30 * s) if i in (0, 1, 2) else (0.98 * s, 1.02 * s))
        else:
            lo, hi = (0.90 * s, 1.10 * s)

        bounds.append(_sanitize_bound(lo, hi, 2.50, 8.50))

    if stage_name == "swrmatch":
        hb = (height - 5.0, height + 5.0)
    else:
        hb = (height - 4.0, height + 4.0)

    bounds.append(_sanitize_bound(hb[0], hb[1], 45.0, 55.0))
    return bounds


def build_de_init_population(active_bounds, seed_active, popsize, seed):
    rng = np.random.default_rng(seed)

    lower = np.array([b[0] for b in active_bounds], dtype=float)
    upper = np.array([b[1] for b in active_bounds], dtype=float)
    span = upper - lower

    dim = len(active_bounds)
    npop = max(8, popsize * dim)

    init = np.empty((npop, dim), dtype=float)
    init[0] = np.clip(np.asarray(seed_active, dtype=float), lower, upper)

    for i in range(1, npop):
        if i < max(3, npop // 2):
            jitter = rng.normal(0.0, 0.08, size=dim) * span
            candidate = init[0] + jitter
        else:
            candidate = lower + rng.random(dim) * span

        init[i] = np.clip(candidate, lower, upper)

    return init


def make_stage_objective(stage_name, freqs_opt, center_freq, target_rl_db, use_real_ground,
                         measure_gain, use_gain_in_cost, active_idx, base_full_x,
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
                metrics = evaluate_candidate_metrics(
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

        cost = compute_stage_cost(stage_name, metrics, lengths, spacings, height, use_gain_in_cost)
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
              measure_gain, use_gain_in_cost, maxiter, popsize, seed, workers, seed_x):
    if workers != 1:
        print("[WARN] workers > 1 is not reliable here with necpp and local best tracking; forcing workers=1")
        workers = 1

    active_idx = get_stage_active_idx(stage_name)
    full_bounds = build_stage_bounds_from_base(seed_x, stage_name)
    active_bounds = [full_bounds[i] for i in active_idx]

    initial_best_x = None
    initial_best_metrics = None
    initial_best_cost = np.inf

    try:
        seed_lengths, seed_spacings, seed_height = unpack_design(seed_x)
        if geometry_is_valid(seed_lengths, seed_spacings, seed_height):
            seed_metrics = evaluate_candidate_metrics(
                seed_lengths, seed_spacings, seed_height,
                freqs_opt=freqs_opt,
                center_freq=center_freq,
                target_rl_db=target_rl_db,
                use_real_ground=use_real_ground,
                measure_gain=measure_gain,
            )
            seed_cost = compute_stage_cost(
                stage_name, seed_metrics,
                seed_lengths, seed_spacings, seed_height,
                use_gain_in_cost
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
        use_gain_in_cost=use_gain_in_cost,
        active_idx=active_idx,
        base_full_x=seed_x,
        initial_best_x=initial_best_x,
        initial_best_metrics=initial_best_metrics,
        initial_best_cost=initial_best_cost,
    )

    seed_active = active_from_full(seed_x, active_idx)
    init_population = build_de_init_population(active_bounds, seed_active, popsize, seed)

    result = differential_evolution(
        objective,
        active_bounds,
        maxiter=maxiter,
        popsize=popsize,
        tol=0.01,
        mutation=(0.5, 1.0),
        recombination=0.7,
        polish=False,
        seed=seed,
        workers=workers,
        updating="immediate",
        disp=True,
        init=init_population,
    )

    candidate_records = []

    try:
        x_full_candidate = full_from_active(result.x, seed_x, active_idx)
        lengths, spacings, height = unpack_design(x_full_candidate)
        if geometry_is_valid(lengths, spacings, height):
            metrics = evaluate_candidate_metrics(
                lengths, spacings, height,
                freqs_opt=freqs_opt,
                center_freq=center_freq,
                target_rl_db=target_rl_db,
                use_real_ground=use_real_ground,
                measure_gain=measure_gain,
            )
            final_cost = compute_stage_cost(
                stage_name, metrics, lengths, spacings, height, use_gain_in_cost
            )
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

    best_rec = min(candidate_records, key=lambda rec: rec["stage_cost"])
    return best_rec


def move_element_position(x_full, element_index, delta_ft):
    """
    Move one element along the boom while keeping all other element positions fixed.
    REF is treated as fixed at y=0, so valid movable indices here are 1..6.

    element_index:
        1 = DE
        2 = D1
        ...
        6 = D5

    Positive delta moves element forward (+Y), negative backward (-Y).
    """
    lengths, spacings, height = unpack_design(x_full)
    lengths = lengths.copy()
    spacings = spacings.copy()

    i = int(element_index)
    if i < 1 or i > 6:
        raise ValueError("element_index for move_element_position must be in 1..6")

    if i < 6:
        spacings[i - 1] += float(delta_ft)
        spacings[i] -= float(delta_ft)
    else:
        spacings[5] += float(delta_ft)

    return pack_design(lengths, spacings, height)


def move_element_length(x_full, element_index, delta_ft):
    lengths, spacings, height = unpack_design(x_full)
    lengths = lengths.copy()
    lengths[int(element_index)] += float(delta_ft)
    return pack_design(lengths, spacings, height)


def combined_layout_score(metrics, target_rl_db, gain_enabled):
    required = [
        "center_R", "center_X", "center_rl_raw", "center_rl_reward",
        "center_swr", "local_min_rl", "local_mean_rl", "local_max_swr",
        "bandwidth_mhz", "r_err", "x_err"
    ]
    if not all(np.isfinite(metrics[k]) for k in required):
        return -1e12

    if gain_enabled:
        if not (np.isfinite(metrics["forward_gain_db"]) and np.isfinite(metrics["front_to_back_db"])):
            return -1e12

    score = 0.0

    # Reward good electrical behavior
    score += 1.8 * metrics["center_rl_reward"]
    score += 1.3 * metrics["local_mean_rl"]
    score += 1.0 * metrics["local_min_rl"]
    score += 10.0 * metrics["bandwidth_mhz"]

    # Reward pattern if available
    if gain_enabled:
        score += 2.4 * metrics["forward_gain_db"]
        score += 2.5 * max(0.0, min(metrics["front_to_back_db"], 25.0))
        if np.isfinite(metrics["rear_gain_db"]):
            score -= 1.0 * max(0.0, metrics["rear_gain_db"])

    # Penalize bad match
    score -= 45.0 * max(0.0, metrics["center_swr"] - 1.10) ** 2
    score -= 28.0 * max(0.0, metrics["local_max_swr"] - 1.35) ** 2

    # Penalize impedance drift
    score -= 4.0 * (metrics["r_err"] ** 2)
    score -= 5.0 * (metrics["x_err"] ** 2)

    # Penalize RL shortfall near center
    score -= 2.0 * max(0.0, target_rl_db - metrics["local_min_rl"]) ** 2
    score -= 1.0 * max(0.0, target_rl_db - metrics["center_rl_reward"]) ** 2

    # Hard-ish reject region
    if metrics["center_swr"] > 1.60:
        score -= 1e6
    if metrics["local_max_swr"] > 2.00:
        score -= 1e6
    if metrics["center_rl_raw"] < 10.0:
        score -= 1e6

    return float(score)


def evaluate_layout_candidate(x_full, label, freqs_opt, center_freq, target_rl_db,
                              use_real_ground, gain_enabled, logbook=None,
                              accepted=False, note=""):
    lengths, spacings, height = unpack_design(x_full)

    if not geometry_is_valid(lengths, spacings, height):
        return None

    try:
        metrics = evaluate_candidate_metrics(
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


def print_stage_result(title, rec):
    m = rec["metrics"]
    print(f"\n{title}")
    print("=" * len(title))
    print(f"Center Z: {m['center_R']:.4f} {m['center_X']:+.4f}j ohms")
    print(f"Center RL(raw): {m['center_rl_raw']:.2f} dB")
    print(f"Center SWR:     {m['center_swr']:.3f}")
    print(f"Local min RL*:  {m['local_min_rl']:.2f} dB")
    print(f"Local mean RL*: {m['local_mean_rl']:.2f} dB")
    print(f"Local max SWR:  {m['local_max_swr']:.3f}")
    print(f"Bandwidth*:     {m['bandwidth_mhz']:.3f} MHz")
    if np.isfinite(m["forward_gain_db"]):
        print(f"Forward gain:   {m['forward_gain_db']:.2f} dB")
        print(f"Rear gain:      {m['rear_gain_db']:.2f} dB")
        print(f"Front/back:     {m['front_to_back_db']:.2f} dB")
    if "stage_cost" in rec:
        print(f"Stage cost: {rec['stage_cost']:.4f}")
    if "score" in rec:
        print(f"Layout score: {rec['score']:.4f}")
    print("* RL values here use reward clip at {:.1f} dB".format(RL_REWARD_CLIP_DB))


def print_search_record(rec, prefix=""):
    if rec is None:
        return
    m = rec["metrics"]
    move_note = f" | {rec['note']}" if rec.get("note") else ""
    print(
        f"{prefix}{rec['label']}: "
        f"score={rec['score']:.3f}, "
        f"SWR={m['center_swr']:.3f}, "
        f"RL={m['center_rl_raw']:.2f} dB, "
        f"localRL={m['local_min_rl']:.2f} dB, "
        f"BW={m['bandwidth_mhz']:.3f} MHz, "
        f"Gain={m['forward_gain_db']:.2f} dB, "
        f"F/B={m['front_to_back_db']:.2f} dB"
        f"{move_note}"
    )


def coordinate_position_search(start_x, search_name, step_sizes_in, passes_per_step,
                               freqs_opt, center_freq, target_rl_db,
                               use_real_ground, gain_enabled, logbook):
    element_order = [1, 2, 3, 4, 5, 6]   # DE through D5, REF fixed at y=0

    current = evaluate_layout_candidate(
        start_x,
        label=f"{search_name}:start",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=target_rl_db,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
        accepted=True,
        note="search start",
    )
    if current is None:
        raise RuntimeError(f"{search_name} could not evaluate the starting layout")

    print(f"\n=== {search_name.upper()} ===")
    print_search_record(current, prefix="  ")

    for step_in in step_sizes_in:
        step_ft = inches_to_ft(step_in)
        print(f"\n  Step size: {step_in:.3f} in")

        for pass_idx in range(1, passes_per_step + 1):
            improved_this_pass = False
            print(f"    Pass {pass_idx}")

            for elem_idx in element_order:
                best_trial = None

                for sign, direction_name in ((+1.0, "forward"), (-1.0, "backward")):
                    try:
                        x_trial = move_element_position(current["x_full"], elem_idx, sign * step_ft)
                    except Exception:
                        continue

                    trial = evaluate_layout_candidate(
                        x_trial,
                        label=f"{search_name}:pos",
                        freqs_opt=freqs_opt,
                        center_freq=center_freq,
                        target_rl_db=target_rl_db,
                        use_real_ground=use_real_ground,
                        gain_enabled=gain_enabled,
                        logbook=logbook,
                        accepted=False,
                        note=f"{ELEMENT_NAMES[elem_idx]} {direction_name} {step_in:.3f} in",
                    )

                    if trial is None:
                        continue

                    if trial["score"] > current["score"] + 1e-9:
                        if best_trial is None or trial["score"] > best_trial["score"]:
                            best_trial = trial

                if best_trial is not None:
                    best_trial["accepted"] = True
                    current = best_trial
                    improved_this_pass = True
                    print_search_record(current, prefix="      ACCEPT ")

            if not improved_this_pass:
                print("      No improving moves this pass.")
                break

    return current


def coordinate_length_search(start_x, search_name, step_sizes_in, passes_per_step,
                             freqs_opt, center_freq, target_rl_db,
                             use_real_ground, gain_enabled, logbook):
    # Slight trims to all element lengths, small steps only.
    element_order = [0, 1, 2, 3, 4, 5, 6]

    current = evaluate_layout_candidate(
        start_x,
        label=f"{search_name}:start",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=target_rl_db,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
        accepted=True,
        note="search start",
    )
    if current is None:
        raise RuntimeError(f"{search_name} could not evaluate the starting layout")

    print(f"\n=== {search_name.upper()} ===")
    print_search_record(current, prefix="  ")

    for step_in in step_sizes_in:
        step_ft = inches_to_ft(step_in)
        print(f"\n  Length step size: {step_in:.3f} in")

        for pass_idx in range(1, passes_per_step + 1):
            improved_this_pass = False
            print(f"    Pass {pass_idx}")

            for elem_idx in element_order:
                best_trial = None

                for sign, direction_name in ((+1.0, "longer"), (-1.0, "shorter")):
                    try:
                        x_trial = move_element_length(current["x_full"], elem_idx, sign * step_ft)
                    except Exception:
                        continue

                    trial = evaluate_layout_candidate(
                        x_trial,
                        label=f"{search_name}:len",
                        freqs_opt=freqs_opt,
                        center_freq=center_freq,
                        target_rl_db=target_rl_db,
                        use_real_ground=use_real_ground,
                        gain_enabled=gain_enabled,
                        logbook=logbook,
                        accepted=False,
                        note=f"{ELEMENT_NAMES[elem_idx]} {direction_name} {step_in:.3f} in",
                    )

                    if trial is None:
                        continue

                    if trial["score"] > current["score"] + 1e-9:
                        if best_trial is None or trial["score"] > best_trial["score"]:
                            best_trial = trial

                if best_trial is not None:
                    best_trial["accepted"] = True
                    current = best_trial
                    improved_this_pass = True
                    print_search_record(current, prefix="      ACCEPT ")

            if not improved_this_pass:
                print("      No improving moves this pass.")
                break

    return current


def dedupe_logbook(logbook, decimals=5):
    best_by_key = {}

    for rec in logbook:
        x = np.asarray(rec["x_full"], dtype=float)
        key = tuple(np.round(x, decimals))
        old = best_by_key.get(key)
        if old is None or rec["score"] > old["score"]:
            best_by_key[key] = rec

    return list(best_by_key.values())


def print_top_layouts(logbook, top_n=12):
    uniq = dedupe_logbook(logbook)
    uniq.sort(key=lambda r: r["score"], reverse=True)

    print("\nTOP LOGGED LAYOUTS")
    print("==================")
    if not uniq:
        print("No valid layouts were logged.")
        return

    print("Rank  Score      SWR    RL(dB)  LocalRL  BW(MHz)  Gain(dB)  F/B(dB)  Accepted  Note")
    for i, rec in enumerate(uniq[:top_n], start=1):
        m = rec["metrics"]
        print(
            f"{i:>4d}  "
            f"{rec['score']:>8.3f}  "
            f"{m['center_swr']:>5.3f}  "
            f"{m['center_rl_raw']:>6.2f}  "
            f"{m['local_min_rl']:>7.2f}  "
            f"{m['bandwidth_mhz']:>7.3f}  "
            f"{m['forward_gain_db']:>8.2f}  "
            f"{m['front_to_back_db']:>7.2f}  "
            f"{str(rec['accepted']):>8s}  "
            f"{rec.get('note', '')}"
        )


def choose_best_logged_layout(logbook):
    uniq = dedupe_logbook(logbook)
    if not uniq:
        raise RuntimeError("No valid logged layouts available for final selection")
    return max(uniq, key=lambda r: r["score"])


def print_design(lengths, spacings, height):
    y = y_positions_from_spacings(spacings)

    print("\nBEST GEOMETRY")
    print("=============")
    print(f"Height: {height:.4f} ft    {ftin(height)}")
    print(f"Boom length REF to D5: {np.sum(spacings):.4f} ft    {ftin(np.sum(spacings))}")
    print(f"Element taper: center {TAPER_CENTER_DIAMETER_IN:.3f} in OD, outer {TAPER_OUTER_DIAMETER_IN:.3f} in OD")
    print(f"Reflector over DE allowed range: {REFLECTOR_MIN_OVER_DE_FT*12.0:.1f} in to {REFLECTOR_MAX_OVER_DE_FT*12.0:.1f} in")
    print()

    print("Elements:")
    print("  Name    Length decimal ft      Length ft/in        Position from REF")
    for name, L, pos in zip(ELEMENT_NAMES, lengths, y):
        print(f"  {name:>3s}    {L:10.4f} ft      {ftin(L):>16s}      {ftin(pos):>16s}")

    print()
    print("Spacings:")
    for i, s in enumerate(spacings):
        print(f"  {ELEMENT_NAMES[i]} to {ELEMENT_NAMES[i+1]}: {s:.4f} ft    {ftin(s)}")


def print_center_result(lengths, spacings, height, center_freq, use_real_ground, gain_enabled):
    zc = solve_nec_impedance(lengths, spacings, height, center_freq, use_real_ground)
    rl_raw = return_loss_db_raw(zc)
    rl_report = return_loss_db(zc, clip_db=RL_REPORT_CLIP_DB)
    swr = swr_from_z(zc)
    eta = mismatch_efficiency_percent(zc)
    if not np.isfinite(eta):
        eta = 0.0
    ref_over_de_in = (lengths[0] - lengths[1]) * 12.0

    print("\nCENTER-FREQUENCY RESULT")
    print("=======================")
    print(f"Center frequency: {center_freq:.3f} MHz")
    print(f"Feed Z: {zc.real:.4f} {zc.imag:+.4f}j ohms")
    print(f"Return loss (report clipped): {rl_report:.2f} dB")
    print(f"Return loss (raw):            {rl_raw:.2f} dB")
    print(f"SWR: {swr:.4f}")
    print(f"Mismatch efficiency: {eta:.4f} %")
    print(f"Reflector longer than DE by: {ref_over_de_in:.2f} in")

    if gain_enabled:
        fwd_gain, rear_gain, f2b = estimate_pattern_metrics(lengths, spacings, height, center_freq, use_real_ground)
        if np.isfinite(fwd_gain):
            print(f"Forward gain proxy (+Y): {fwd_gain:.2f} dB")
            print(f"Rear gain proxy (-Y):    {rear_gain:.2f} dB")
            print(f"Front-to-back proxy:     {f2b:.2f} dB")


def print_sweep_summary(freqs, z, rl_report, rl_raw, swr, eta, target_rl_db, center_freq):
    z = np.asarray(z, dtype=complex)
    rl_report = np.asarray(rl_report, dtype=float)
    rl_raw = np.asarray(rl_raw, dtype=float)
    swr = np.asarray(swr, dtype=float)
    eta = np.asarray(eta, dtype=float)
    freqs = np.asarray(freqs, dtype=float)

    valid = (
        np.isfinite(z.real) &
        np.isfinite(z.imag) &
        np.isfinite(rl_report) &
        np.isfinite(rl_raw) &
        np.isfinite(swr) &
        np.isfinite(eta)
    )

    print("\nSWEEP SUMMARY")
    print("=============")
    print(f"Frequency range: {freqs[0]:.3f} to {freqs[-1]:.3f} MHz")
    print(f"Center target:   {center_freq:.3f} MHz")
    print(f"RL report clip:  {RL_REPORT_CLIP_DB:.1f} dB")
    print(f"RL reward clip:  {RL_REWARD_CLIP_DB:.1f} dB")

    if not np.any(valid):
        print("No valid sweep points were produced.")
        return

    rl_reward = np.minimum(np.where(np.isfinite(rl_raw), rl_raw, -20.0), RL_REWARD_CLIP_DB)
    bw, f1, f2 = approximate_bandwidth(freqs, rl_reward, target_rl_db)

    valid_idx = np.where(valid)[0]
    best_idx = valid_idx[np.argmax(rl_report[valid])]
    worst_idx = valid_idx[np.argmin(rl_report[valid])]
    center_idx = int(np.argmin(np.abs(freqs - center_freq)))

    print(f"Best return loss:  {rl_report[best_idx]:.2f} dB at {freqs[best_idx]:.3f} MHz")
    print(f"Worst return loss: {rl_report[worst_idx]:.2f} dB at {freqs[worst_idx]:.3f} MHz")
    print(f"Average RL:        {np.mean(rl_report[valid]):.2f} dB")
    print(f"Average SWR:       {np.mean(swr[valid]):.2f}")
    print(f"Worst SWR:         {np.max(swr[valid]):.2f}")
    print(f"Average mismatch efficiency: {np.mean(eta[valid]):.2f} %")

    if valid[center_idx]:
        print(
            f"At center {freqs[center_idx]:.3f} MHz: "
            f"RL(report) {rl_report[center_idx]:.2f} dB, "
            f"RL(raw) {rl_raw[center_idx]:.2f} dB, "
            f"SWR {swr[center_idx]:.3f}, "
            f"Z = {z[center_idx].real:.4f} {z[center_idx].imag:+.4f}j"
        )
    else:
        print(f"At center {freqs[center_idx]:.3f} MHz: no valid sampled point")

    if bw > 0:
        print(f"Bandwidth with RL >= {target_rl_db:.1f} dB: {bw:.3f} MHz, {f1:.3f} to {f2:.3f} MHz")
    else:
        print(f"No sampled bandwidth met RL >= {target_rl_db:.1f} dB")

    print()
    print("Selected frequency points:")
    print("  MHz       R+jX ohms             RL dB     SWR     Eff %")

    sample_idx = np.unique(np.linspace(0, len(freqs) - 1, min(9, len(freqs))).astype(int))
    for idx in sample_idx:
        fi = freqs[idx]
        zi = z[idx]
        rr = rl_report[idx] if np.isfinite(rl_report[idx]) else np.nan
        ss = swr[idx] if np.isfinite(swr[idx]) else 999.0
        ee = eta[idx] if np.isfinite(eta[idx]) else 0.0
        zr = zi.real if np.isfinite(zi.real) else np.nan
        zx = zi.imag if np.isfinite(zi.imag) else np.nan
        print(f"  {fi:6.3f}   {zr:8.2f} {zx:+8.2f}j   {rr:7.2f}   {ss:6.2f}   {ee:7.2f}")


def run_preflight(use_real_ground, center_freq, requested_gain_probe):
    print("Running NEC preflight check on seed geometry...")
    print(f"Requested mode: {'real ground' if use_real_ground else 'free space'}")
    print(f"Center frequency: {center_freq:.3f} MHz")

    try:
        z = solve_nec_impedance(SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT, center_freq, use_real_ground)
        print(f"Seed geometry @ {center_freq:.3f} MHz: {z.real:.2f} {z.imag:+.2f}j ohms")
    except Exception as e:
        print(f"Primary preflight failed: {e}")

        if use_real_ground:
            print("\nTrying free-space diagnostic...")
            try:
                z = solve_nec_impedance(SEED_LENGTHS_FT, SEED_SPACINGS_FT, SEED_HEIGHT_FT, center_freq, False)
                print(f"Free-space diagnostic worked: {z.real:.2f} {z.imag:+.2f}j ohms")
                print("This suggests the geometry/excitation is probably okay, and the issue is ground-model behavior in this necpp build.")
            except Exception as e2:
                print(f"Free-space diagnostic also failed: {e2}")

        return False, False

    gain_enabled = False
    if requested_gain_probe:
        print("\nProbing radiation-pattern / gain support...")
        gain_enabled = probe_gain_support(use_real_ground, center_freq)
        if gain_enabled:
            print("Gain support detected: enabled")
        else:
            print("Gain support not detected from this necpp build: gain-aware search will be disabled")

    return True, gain_enabled


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
    args = ap.parse_args()

    use_real_ground = not args.free_space
    center_freq = float(args.center_freq)

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

    print("\n=== Baseline stage: swrmatch ===")
    baseline_swr = run_stage(
        stage_name="swrmatch",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        measure_gain=False,
        use_gain_in_cost=False,
        maxiter=args.stage_maxiter,
        popsize=args.stage_popsize,
        seed=args.seed,
        workers=args.workers,
        seed_x=seed_x,
    )

    baseline_swr_scored = evaluate_layout_candidate(
        baseline_swr["x_full"],
        label="baseline_swr",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
        accepted=True,
        note="baseline SWR/match result",
    )
    print_stage_result("BASELINE SWR/MATCH", baseline_swr_scored)

    print("\n=== Baseline stage: returnloss ===")
    baseline_rl = run_stage(
        stage_name="returnloss",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        measure_gain=False,
        use_gain_in_cost=False,
        maxiter=args.stage_maxiter,
        popsize=args.stage_popsize,
        seed=args.seed + 1,
        workers=args.workers,
        seed_x=baseline_swr["x_full"],
    )

    baseline_rl_scored = evaluate_layout_candidate(
        baseline_rl["x_full"],
        label="baseline_rl",
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
        accepted=True,
        note="baseline return-loss result",
    )
    print_stage_result("BASELINE RETURN-LOSS", baseline_rl_scored)

    current = baseline_rl_scored

    current = coordinate_position_search(
        current["x_full"],
        search_name="coarse_position_search",
        step_sizes_in=[3.0, 1.5],
        passes_per_step=args.position_passes,
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
    )

    current = coordinate_length_search(
        current["x_full"],
        search_name="length_trim_search",
        step_sizes_in=[0.5, 0.25],
        passes_per_step=args.length_passes,
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
    )

    current = coordinate_position_search(
        current["x_full"],
        search_name="fine_position_search",
        step_sizes_in=[0.75, 0.375],
        passes_per_step=args.position_passes,
        freqs_opt=freqs_opt,
        center_freq=center_freq,
        target_rl_db=args.target_rl,
        use_real_ground=use_real_ground,
        gain_enabled=gain_enabled,
        logbook=logbook,
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

    print_design(lengths, spacings, height)
    print_center_result(lengths, spacings, height, center_freq, use_real_ground, gain_enabled)

    print("\nRunning final sweep...")
    z, rl_report, rl_raw, swr, eta = sweep_design(lengths, spacings, height, freqs_final, use_real_ground)
    print_sweep_summary(freqs_final, z, rl_report, rl_raw, swr, eta, args.target_rl, center_freq)

    if gain_enabled:
        fwd_gain, rear_gain, f2b = estimate_pattern_metrics(lengths, spacings, height, center_freq, use_real_ground)
        if np.isfinite(fwd_gain):
            print("\nCENTER PATTERN PROXY")
            print("====================")
            print(f"Forward gain (+Y): {fwd_gain:.2f} dB")
            print(f"Rear gain (-Y):    {rear_gain:.2f} dB")
            print(f"Front-to-back:     {f2b:.2f} dB")

    print("\nNo files written in this debug version.")


if __name__ == "__main__":
    main()
