import sys
import numpy as np

from .constants import (
    FT,
    IN,
    TAPER_CENTER_DIAMETER_IN,
    TAPER_OUTER_DIAMETER_IN,
    TAPER_CENTER_SECTION_FT,
    TAPER_MIN_OUTER_SECTION_FT,
    TAPER_MIN_CENTER_SECTION_FT,
    OUTER_SECTION_SEGMENTS,
    CENTER_SECTION_SEGMENTS,
    ALUMINUM_SIGMA,
    GROUND_EPSR,
    GROUND_SIGMA,
    DEFAULT_USE_REAL_GROUND,
)
from .geometry import y_positions_from_spacings
from .rfmath import is_finite_complex

try:
    import necpp
except ImportError:
    print("ERROR: necpp is not installed. Try: pip3 install necpp")
    sys.exit(1)


NEC_IMPEDANCE_CACHE = {}


def clear_nec_caches():
    NEC_IMPEDANCE_CACHE.clear()


def ground_variants(use_real_ground):
    if use_real_ground:
        return ["real2", "real0", "perfect"]
    return ["free"]


def apply_ground(nec, ground_variant):
    if ground_variant == "free":
        return necpp.nec_gn_card(nec, -1, 0, 0, 0, 0, 0, 0, 0)

    if ground_variant == "real2":
        return necpp.nec_gn_card(nec, 2, 0, GROUND_EPSR, GROUND_SIGMA, 0, 0, 0, 0)

    if ground_variant == "real0":
        return necpp.nec_gn_card(nec, 0, 0, GROUND_EPSR, GROUND_SIGMA, 0, 0, 0, 0)

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


def build_nec_context(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant, geometry_validator):
    if CENTER_SECTION_SEGMENTS % 2 == 0:
        raise ValueError("CENTER_SECTION_SEGMENTS must be odd")

    if not geometry_validator(lengths_ft, spacings_ft, height_ft):
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
        for i in range(len(lengths_ft)):
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


def _solve_nec_impedance_once(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant, geometry_validator):
    nec = build_nec_context(lengths_ft, spacings_ft, height_ft, freq_mhz, ground_variant, geometry_validator)
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
                        geometry_validator,
                        use_real_ground=DEFAULT_USE_REAL_GROUND):
    key = _impedance_cache_key(lengths_ft, spacings_ft, height_ft, freq_mhz, use_real_ground)
    if key in NEC_IMPEDANCE_CACHE:
        return NEC_IMPEDANCE_CACHE[key]

    last_err = None

    for ground_variant in ground_variants(use_real_ground):
        try:
            z = _solve_nec_impedance_once(
                lengths_ft, spacings_ft, height_ft, freq_mhz,
                ground_variant, geometry_validator
            )
            if is_finite_complex(z):
                NEC_IMPEDANCE_CACHE[key] = z
                return z
        except Exception as e:
            last_err = e

    if last_err is not None:
        raise RuntimeError(str(last_err))

    raise RuntimeError("No valid ground variant produced a finite impedance")
