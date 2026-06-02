import sys
import numpy as np

from .constants import DEFAULT_USE_REAL_GROUND
from .nec_engine import build_nec_context, ground_variants

try:
    import necpp
except ImportError:
    print("ERROR: necpp is not installed. Try: pip3 install necpp")
    sys.exit(1)


NEC_GAIN_CACHE = {}


def clear_pattern_caches():
    NEC_GAIN_CACHE.clear()


def _round_tuple(values, ndigits=6):
    return tuple(round(float(v), ndigits) for v in np.asarray(values, dtype=float))


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
        (nec, 0, 0, 0),
        (nec, 0, 0),
        (nec, 0),
        (nec,),
        (nec, 1, 0, 0),
        (nec, 1, 1),
        (nec, 1),
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
            if np.isfinite(num) and -200.0 <= num <= 100.0:
                return float(num)

    return None


def _solve_nec_gain_at_point_once(lengths_ft, spacings_ft, height_ft, freq_mhz,
                                  phi_deg, elev_deg, ground_variant, geometry_validator):
    theta_deg = 90.0 - float(elev_deg)
    nec = build_nec_context(
        lengths_ft, spacings_ft, height_ft, freq_mhz,
        ground_variant, geometry_validator
    )
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
                            geometry_validator,
                            use_real_ground=DEFAULT_USE_REAL_GROUND):
    key = _gain_cache_key(lengths_ft, spacings_ft, height_ft, freq_mhz, phi_deg, elev_deg, use_real_ground)
    if key in NEC_GAIN_CACHE:
        return NEC_GAIN_CACHE[key]

    for ground_variant in ground_variants(use_real_ground):
        try:
            g = _solve_nec_gain_at_point_once(
                lengths_ft, spacings_ft, height_ft, freq_mhz,
                phi_deg, elev_deg, ground_variant, geometry_validator
            )
            if g is not None and np.isfinite(g):
                NEC_GAIN_CACHE[key] = float(g)
                return float(g)
        except Exception:
            continue

    NEC_GAIN_CACHE[key] = None
    return None


def estimate_pattern_metrics(lengths_ft, spacings_ft, height_ft, freq_mhz, geometry_validator,
                             use_real_ground, elev_samples_deg=(5.0, 10.0, 15.0, 20.0),
                             rear_phi_samples_deg=(240.0, 270.0, 300.0)):
    """Samples forward (phi=90) and a rear cone (default 240/270/300) at multiple elevations.
    Returns (max_fwd, max_rear_across_whole_rear_cone, fwd - rear) so F/B penalizes rear lobes
    anywhere in the back half-plane, not just dead-aft."""
    fwd_samples = []
    rear_samples = []

    for elev in elev_samples_deg:
        gf = solve_nec_gain_at_point(
            lengths_ft, spacings_ft, height_ft, freq_mhz, 90.0, elev,
            geometry_validator, use_real_ground
        )
        if gf is None or not np.isfinite(gf):
            return np.nan, np.nan, np.nan
        fwd_samples.append(float(gf))

        for phi in rear_phi_samples_deg:
            gr = solve_nec_gain_at_point(
                lengths_ft, spacings_ft, height_ft, freq_mhz, float(phi), elev,
                geometry_validator, use_real_ground
            )
            if gr is None or not np.isfinite(gr):
                return np.nan, np.nan, np.nan
            rear_samples.append(float(gr))

    if not fwd_samples:
        return np.nan, np.nan, np.nan

    fwd = float(np.max(fwd_samples))
    rear = float(np.max(rear_samples))  # max across entire rear cone
    f2b = fwd - rear
    return fwd, rear, f2b


def probe_gain_support(seed_lengths, seed_spacings, seed_height, center_freq, geometry_validator, use_real_ground):
    try:
        fwd_gain, rear_gain, f2b = estimate_pattern_metrics(
            seed_lengths, seed_spacings, seed_height, center_freq,
            geometry_validator, use_real_ground
        )
        return np.isfinite(fwd_gain) and np.isfinite(rear_gain) and np.isfinite(f2b)
    except Exception:
        return False
