"""hybrid_auto7 — full antenna performance report.

Given a geometry, runs NEC over real ground and in free space at the centre
frequency, plus a fine SWR sweep, and returns the metrics a builder actually
wants on the bench:

  * forward gain (dBi over real ground, dBd, free-space dBi, ground-reflection
    gain, linear power multipliers vs isotropic and vs dipole)
  * front/back and front/rear ratios
  * azimuth & elevation -3 dB beamwidths
  * take-off (peak-elevation) angle and antenna height
  * radiation efficiency (NEC conductor model)
  * resonant / minimum-SWR frequency and the SWR bandwidth held under
    1.2 / 1.5 / 2.0 : 1
"""
from __future__ import annotations

import math
import os
import pathlib
import re
import subprocess
import tempfile

from . import v2_runner

_EFF_RE = re.compile(r"EFFICIENCY\s*=\s*([\d.+\-Ee]+)\s*Percent")


def _solve(card, timeout=120):
    with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as f:
        f.write(card)
        p = f.name
    o = p.replace(".nec", ".out")
    try:
        subprocess.run(["nec2c", "-i", p, "-o", o],
                       capture_output=True, text=True, timeout=timeout)
        if not pathlib.Path(o).exists():
            return [], [], ""
        text = pathlib.Path(o).read_text()
    finally:
        for q in (p, o):
            try:
                os.unlink(q)
            except Exception:
                pass
    imps, pattern_blocks = v2_runner.parse_nec_output(text)
    # perf_report solves one frequency at a time, so flatten the per-frequency
    # blocks back into a single pattern list for this caller.
    pat = [pt for blk in pattern_blocks for pt in blk]
    return imps, pat, text


def _free_space_card(card):
    return (card.replace("GN 2 0 0 0 13.0 0.005\n", "")
                .replace("GE -1", "GE 0"))


def _beamwidth(samples, peak_angle, peak_gain, wrap360=False):
    """samples: list of (angle_deg, gain_db) for one cut. Return -3 dB beamwidth
    (deg) of the main lobe around peak_angle via linear interpolation; None if
    the lobe never drops 3 dB inside the cut."""
    if len(samples) < 3:
        return None
    pts = sorted(samples, key=lambda s: s[0])
    n = len(pts)
    pk = min(range(n), key=lambda i: abs(pts[i][0] - peak_angle))
    thr = peak_gain - 3.0

    def cross(i_from, step):
        a0, g0 = pts[i_from]
        i = i_from
        while True:
            j = i + step
            if j < 0 or j >= n:
                if wrap360:
                    j %= n
                else:
                    return None
            a1, g1 = pts[j]
            if g1 <= thr:
                if abs(g1 - g0) < 1e-9:
                    return a1
                frac = (g0 - thr) / (g0 - g1)
                da = a1 - a0
                if wrap360 and abs(da) > 180:
                    da -= math.copysign(360, da)
                return a0 + frac * da
            a0, g0, i = a1, g1, j
            if i == pk:
                return None
    left = cross(pk, -1)
    right = cross(pk, +1)
    if left is None or right is None:
        return None
    bw = right - left
    if wrap360 and bw < 0:
        bw += 360
    return abs(bw)


def _swr_bandwidth(curve, threshold, f_min_swr):
    """Contiguous freq span (low, high, kHz) around the min-SWR freq where
    SWR <= threshold. curve = [(f,R,X,swr)]."""
    if not curve:
        return None
    pts = sorted(curve, key=lambda c: c[0])
    pk = min(range(len(pts)), key=lambda i: abs(pts[i][0] - f_min_swr))
    if pts[pk][3] > threshold:
        return None
    lo = pts[pk][0]
    i = pk
    while i - 1 >= 0 and pts[i - 1][3] <= threshold:
        i -= 1
        lo = pts[i][0]
    hi = pts[pk][0]
    j = pk
    while j + 1 < len(pts) and pts[j + 1][3] <= threshold:
        j += 1
        hi = pts[j][0]
    return (round(lo, 4), round(hi, 4), round((hi - lo) * 1000.0, 1))


def analyze(elements, rules, height_ft=30.0, center_mhz=None,
            sweep_lo=None, sweep_hi=None, sweep_step=0.05):
    glb = rules["global"]
    f_low = float(glb["freq_mhz_low"])
    f_high = float(glb["freq_mhz_high"])
    fc = float(center_mhz if center_mhz is not None
               else glb.get("freq_mhz_center", 0.5 * (f_low + f_high)))

    # --- over real ground at centre freq (full pattern) ---
    card = v2_runner.build_nec_card(elements, [fc], height_ft=height_ft, pattern=True)
    imps, pat, text = _solve(card)
    if not pat:
        return {"error": "no radiation pattern from nec2c"}

    peak = max(pat, key=lambda t: t[2])
    pk_theta, pk_phi, pk_gain = peak
    takeoff = 90.0 - pk_theta

    back_phi = (pk_phi + 180.0) % 360.0

    # F/B and F/R must be measured AT the same elevation as the forward main
    # lobe, otherwise high-angle ground-reflection rear lobes that exist on
    # any horizontal beam over real ground get mixed into the rejection
    # number and the answer comes back 4-7 dB pessimistic.  This is exactly
    # the cut v2_runner.evaluate() uses; the two paths must agree or users
    # see two different F/B values for the same antenna (one on the Tune &
    # Learn result panel, one on the Report).
    REAR_AZ_HALF = 30.0   # +/-30 deg around back azimuth -> F/B
    ELEV_HALF    = 10.0   # +/-10 deg around forward peak elevation
    rear = [g for (t, p, g) in pat
            if abs(t - pk_theta) <= ELEV_HALF
            and abs(((p - back_phi + 180.0) % 360.0) - 180.0) <= REAR_AZ_HALF]
    if not rear:          # fallback: same azimuth cone, ANY elevation
        rear = [g for (t, p, g) in pat
                if abs(((p - back_phi + 180.0) % 360.0) - 180.0) <= REAR_AZ_HALF]
    fb = pk_gain - (max(rear) if rear else pk_gain - 40.0)

    # Front-to-rear: whole rear HEMISPHERE (|delta-phi| > 90) at the main-lobe
    # elevation -- still elevation-aware for the same reason.
    rear_hemi = [g for (t, p, g) in pat
                 if abs(t - pk_theta) <= ELEV_HALF
                 and abs(((p - pk_phi + 180.0) % 360.0) - 180.0) > 90.0]
    if not rear_hemi:
        rear_hemi = [g for (t, p, g) in pat
                     if abs(((p - pk_phi + 180.0) % 360.0) - 180.0) > 90.0]
    fr = pk_gain - (max(rear_hemi) if rear_hemi else pk_gain - 40.0)

    az_cut = [(p, g) for (t, p, g) in pat if abs(t - pk_theta) < 1e-6]
    el_cut = [(90.0 - t, g) for (t, p, g) in pat if abs(p - pk_phi) < 1e-6]
    az_bw = _beamwidth(az_cut, pk_phi, pk_gain, wrap360=True)
    el_bw = _beamwidth(el_cut, takeoff, pk_gain, wrap360=False)

    m_eff = _EFF_RE.search(text)
    efficiency = float(m_eff.group(1)) if m_eff else None

    fs_imps, fs_pat, _ = _solve(_free_space_card(card))
    fs_gain = max((t[2] for t in fs_pat), default=float("nan")) if fs_pat else float("nan")

    dbd = pk_gain - 2.15
    lin_iso = 10 ** (pk_gain / 10.0)
    lin_dipole = 10 ** (dbd / 10.0)

    lo = sweep_lo if sweep_lo is not None else min(f_low, fc) - 0.6
    hi = sweep_hi if sweep_hi is not None else max(f_high, fc) + 0.6
    npts = max(5, int(round((hi - lo) / sweep_step)) + 1)
    curve, band_max, band_avg = v2_runner.band_swr_curve(elements, lo, hi, npts, height_ft)
    # Make sure the design centre is sampled exactly so 'min SWR @ ...' lines
    # up with what the matcher reports as the centre SWR.  The uniform grid
    # rarely lands on fc, which made the Report show e.g. 'min 1.013 @ 27.170'
    # while the Result panel showed centre SWR 1.000 at 27.195.  Inserting fc
    # as an extra sample point keeps the two views in sync.
    if curve and all(abs(c[0] - fc) > 1e-3 for c in curve):
        extra_curve, _emx, _eav = v2_runner.band_swr_curve(
            elements, fc, fc, 1, height_ft)
        if extra_curve:
            curve = sorted(curve + extra_curve, key=lambda c: c[0])
    fmin_swr, min_swr = (fc, 99.0)
    if curve:
        fmin_swr, _R, _X, min_swr = min(curve, key=lambda c: c[3])
    in_band = [c for c in curve if f_low - 1e-6 <= c[0] <= f_high + 1e-6]
    band_max_swr = max((c[3] for c in in_band), default=band_max)

    return {
        "center_mhz": round(fc, 4),
        "band_low_mhz": round(f_low, 4),
        "band_high_mhz": round(f_high, 4),
        "height_ft": round(float(height_ft), 2),
        "gain_dbi": round(pk_gain, 2),
        "gain_dbd": round(dbd, 2),
        "gain_free_space_dbi": round(fs_gain, 2),
        "ground_gain_db": round(pk_gain - fs_gain, 2),
        "power_mult_isotropic": round(lin_iso, 1),
        "power_mult_dipole": round(lin_dipole, 2),
        "fb_db": round(fb, 2),
        "fr_db": round(fr, 2),
        "takeoff_deg": round(takeoff, 1),
        "az_beamwidth_deg": round(az_bw, 1) if az_bw else None,
        "el_beamwidth_deg": round(el_bw, 1) if el_bw else None,
        "efficiency_pct": efficiency,
        "min_swr": round(min_swr, 3),
        "min_swr_mhz": round(fmin_swr, 4),
        "band_max_swr": round(band_max_swr, 3),
        "bw_swr_1p2": _swr_bandwidth(curve, 1.2, fmin_swr),
        "bw_swr_1p5": _swr_bandwidth(curve, 1.5, fmin_swr),
        "bw_swr_2p0": _swr_bandwidth(curve, 2.0, fmin_swr),
        "boom_in": round(max(float(e["position_in"]) for e in elements)
                         - min(float(e["position_in"]) for e in elements), 2),
    }
