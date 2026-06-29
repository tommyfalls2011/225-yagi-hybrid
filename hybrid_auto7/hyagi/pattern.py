from dataclasses import dataclass
from pathlib import Path
import subprocess
import uuid
import math

from .config import AntennaConfig, Design
from .model import build_elements, validate_elements, generate_nec_text
from .paths import MODELS_DIR
from . import db


@dataclass
class PatternResult:
    freq_mhz: float
    forward_gain_dbi: float
    rear_gain_dbi: float
    front_back_db: float
    max_gain_dbi: float
    max_gain_phi_deg: float
    beamwidth_deg: float | None
    real_gain_dbi: float = 0.0
    peak_elev_deg: float = 90.0
    peak_phi_deg: float = 90.0
    horizon_gain_dbi: float = -999.0
    horizon_rear_gain_dbi: float = -999.0


def design_from_row(row):
    return Design(
        de_position_in=float(row["de_position_in"]),
        xfrmr_spacing_in=float(row["xfrmr_spacing_in"]),
        coupler_spacing_in=float(row["coupler_spacing_in"]),
        xfrmr_length_in=float(row["xfrmr_length_in"]),
        coupler_length_in=float(row["coupler_length_in"]),
        de_length_in=float(row["de_length_in"]),
        dir1_position_in=float(row["dir1_position_in"]) if "dir1_position_in" in row.keys() and row["dir1_position_in"] is not None else None,
        dir1_length_in=float(row["dir1_length_in"]) if "dir1_length_in" in row.keys() and row["dir1_length_in"] is not None else None,
        dir2_position_in=float(row["dir2_position_in"]) if "dir2_position_in" in row.keys() and row["dir2_position_in"] is not None else None,
        dir2_length_in=float(row["dir2_length_in"]) if "dir2_length_in" in row.keys() and row["dir2_length_in"] is not None else None,
        dir3_position_in=float(row["dir3_position_in"]) if "dir3_position_in" in row.keys() and row["dir3_position_in"] is not None else None,
        dir3_length_in=float(row["dir3_length_in"]) if "dir3_length_in" in row.keys() and row["dir3_length_in"] is not None else None,
    )


def get_run_by_id(run_id):
    if hasattr(db, "run_by_id"):
        return db.run_by_id(run_id)

    con = db.connect()
    try:
        cur = con.cursor()
        cur.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        return cur.fetchone()
    finally:
        con.close()


def _estimate_beamwidth(gains, forward_idx=90):
    n = len(gains)
    if n < 3 or not (0 <= forward_idx < n):
        return None

    fwd = gains[forward_idx]
    threshold = fwd - 3.0

    left = forward_idx
    while left > 0 and gains[left] >= threshold:
        left -= 1

    right = forward_idx
    while right < n - 1 and gains[right] >= threshold:
        right += 1

    if left == 0 or right == n - 1:
        return None

    bw = float(right - left)
    return bw if bw > 0 else None


def _build_nec_text_for_pattern(elements, ant, freq_mhz):
    # Single-frequency NEC deck
    return generate_nec_text(
        elements=elements,
        ant=ant,
        f_start=float(freq_mhz),
        f_stop=float(freq_mhz),
        f_step=1.0,
    )


# BOOM_DEBUG_STDERR injected
def _run_nec2c(nec_text, tag="pattern"):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex[:8]
    nec_path = MODELS_DIR / f"{tag}_{uid}.nec"
    out_path = MODELS_DIR / f"{tag}_{uid}.out"

    nec_path.write_text(nec_text, encoding="utf-8")

    cmd = ["nec2c", "-i", str(nec_path), "-o", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        raise RuntimeError(
            "nec2c failed\n"
            f"CMD: {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

    if not out_path.exists():
        raise RuntimeError("nec2c did not produce an output file")

    return nec_path, out_path, out_path.read_text(encoding="utf-8", errors="ignore")


def _find_total_gain_column(lines):
    for line in lines:
        up = line.upper()
        if "THETA" in up and "PHI" in up and "TOTAL" in up:
            headers = line.split()
            for i, token in enumerate(headers):
                if token.upper() == "TOTAL":
                    return i
    # fallback: NEC2 usually has TOTAL around column 4
    return 4


def _parse_radiation_pattern(out_text):
    """Parse NEC2 RP output. Returns dict with 2D gain grid + metadata.

    Output format:
      {
        "gains_2d": list[ list[float] ],  # [theta_idx][phi_idx] in dBi
        "thetas":   list[float],           # theta values (deg from zenith)
        "phis":     list[float],           # phi values (deg)
        "peak_dbi": float,
        "peak_theta_deg": float,
        "peak_phi_deg":   float,
        "horizon_gains_by_phi": dict[int, float],  # theta=90 slice (legacy)
      }
    """
    lines = out_text.splitlines()
    rad_idx = None
    for i, line in enumerate(lines):
        if "RADIATION PATTERNS" in line.upper():
            rad_idx = i; break
    if rad_idx is None:
        raise RuntimeError("No RADIATION PATTERNS block in nec2c output")

    total_col = _find_total_gain_column(lines[rad_idx:rad_idx + 20])

    rows = []
    for line in lines[rad_idx:]:
        parts = line.split()
        if len(parts) < 4: continue
        try:
            theta = float(parts[0]); phi = float(parts[1])
        except ValueError: continue
        gain_idx = total_col if total_col < len(parts) else min(4, len(parts) - 1)
        try:
            g = float(parts[gain_idx])
        except ValueError: continue
        rows.append((theta, phi, g))

    if not rows:
        raise RuntimeError("No radiation rows parsed")

    thetas = sorted(set(round(r[0], 3) for r in rows))
    phis   = sorted(set(round(r[1], 3) for r in rows))
    grid = {(round(r[0], 3), round(r[1], 3)): r[2] for r in rows}
    gains_2d = [[grid.get((t, p), -999.0) for p in phis] for t in thetas]

    peak_dbi = -1e9; peak_t = 90.0; peak_p = 90.0
    for t, p, g in rows:
        if g > peak_dbi:
            peak_dbi = g; peak_t = t; peak_p = p

    if peak_dbi < -50.0 or peak_dbi > 30.0:
        raise RuntimeError(f"Invalid peak gain parsed: {peak_dbi}")

    horizon = {}
    for t, p, g in rows:
        if abs(t - 90.0) < 0.5:
            horizon[int(round(p))] = g
    # FB_REALISM_v1: industry-honest F/B = forward gain minus the *worst-case*
    # rear-sector gain at peak elevation. Single-point back samples often land
    # in pattern nulls and produce unrealistic 50+ dB F/B values.
    back_phi_target = (peak_p + 180.0) % 360.0
    def _phi_dist(p):
        d = abs(p - back_phi_target)
        return min(d, 360.0 - d)
    def _is_valid(g):
        return -100.0 < g < 30.0
    # Rear sector: phi within +/- 30 deg of back direction, theta within
    # +/- 5 deg of peak elevation. Take the MAX gain in that sector
    # (worst-case rear -> honest F/B).
    rear_sector = []
    for (t, p), g in grid.items():
        if not _is_valid(g):
            continue
        if abs(t - peak_t) > 5.0:
            continue
        if _phi_dist(p) <= 30.0:
            rear_sector.append((t, p, g))
    if rear_sector:
        # worst-case rear (max gain in sector)
        back_t_actual, back_phi_actual, back_dbi_at_peak = max(rear_sector, key=lambda r: r[2])
    else:
        # Wider fallback: rear hemisphere within +/- 90 deg at peak elev +/- 5
        wider = []
        for (t, p), g in grid.items():
            if not _is_valid(g):
                continue
            if abs(t - peak_t) > 5.0:
                continue
            if _phi_dist(p) <= 90.0:
                wider.append((t, p, g))
        if wider:
            back_t_actual, back_phi_actual, back_dbi_at_peak = max(wider, key=lambda r: r[2])
        else:
            back_t_actual = peak_t
            back_phi_actual = back_phi_target
            back_dbi_at_peak = peak_dbi - 30.0
    front_back_db = float(peak_dbi - back_dbi_at_peak)
    # Loosened sanity clamp: real Yagi F/B rarely > 35 dB but allow up to 80
    # to keep optimizer honest without runaway values.
    if front_back_db > 80.0 or front_back_db < -10.0:
        front_back_db = max(-10.0, min(80.0, front_back_db))

    return {
        "gains_2d": gains_2d, "thetas": thetas, "phis": phis,
        "peak_dbi": float(peak_dbi),
        "peak_theta_deg": float(peak_t),
        "peak_phi_deg":   float(peak_p),
        "back_dbi_at_peak": float(back_dbi_at_peak),
        "back_phi_deg":     float(back_phi_actual),
        "front_back_db":    front_back_db,
        "horizon_gains_by_phi": horizon,
    }





# BW_INTERP_v1: interpolated -3dB beamwidth (sub-degree, no NEC2 cost)
def _beamwidth_interp(elev_slice, phis):
    """Return -3dB beamwidth in degrees from a phi-slice using linear interp
    between sample crossings. Returns 0.0 if unable to compute."""
    if not elev_slice or len(elev_slice) < 3:
        return 0.0
    n = len(elev_slice)
    pmax = max(elev_slice)
    pmax_i = elev_slice.index(pmax)
    target = pmax - 3.0
    phi_step = abs(phis[1] - phis[0]) if len(phis) > 1 else 5.0

    def _crossing(idx_above, idx_below):
        """Linear interp -3dB crossing between two phi sample indices."""
        g_above = elev_slice[idx_above]
        g_below = elev_slice[idx_below]
        if g_above == g_below:
            return phis[idx_above]
        frac = (target - g_below) / (g_above - g_below)
        # Clamp pathological extrapolation
        frac = max(0.0, min(1.0, frac))
        # Direction-aware phi step (handles wraparound at 360)
        dphi = phis[idx_above] - phis[idx_below]
        if dphi > 180.0:   dphi -= 360.0
        if dphi < -180.0:  dphi += 360.0
        return phis[idx_below] + frac * dphi

    # Walk left until sample drops below target -- record bracketing pair
    li = pmax_i
    li_below = None
    for _ in range(n):
        li_next = (li - 1) % n
        if elev_slice[li_next] < target:
            li_below = li_next
            break
        li = li_next
    if li_below is None:
        return 0.0
    left_crossing = _crossing(li, li_below)

    # Walk right symmetrically
    ri = pmax_i
    ri_below = None
    for _ in range(n):
        ri_next = (ri + 1) % n
        if elev_slice[ri_next] < target:
            ri_below = ri_next
            break
        ri = ri_next
    if ri_below is None:
        return 0.0
    right_crossing = _crossing(ri, ri_below)

    # Arc length, respecting phi wraparound at 360
    arc = right_crossing - left_crossing
    while arc < 0:    arc += 360.0
    while arc >= 360: arc -= 360.0
    return float(arc)



def _horizon_gain_from_grid(grid_2d, thetas, phis, phi_target, theta_horizon=90.0):
    """Robust horizon-gain lookup from gains_2d. None if grid unusable."""
    if not thetas or not phis or not grid_2d:
        return None
    t_idx = min(range(len(thetas)), key=lambda i: abs(thetas[i] - theta_horizon))
    p_idx = min(range(len(phis)),   key=lambda i: abs(phis[i]   - phi_target))
    try:
        g = grid_2d[t_idx][p_idx]
    except IndexError:
        return None
    return float(g) if g > -100.0 else None

def _evaluate_pattern(elements, freq_mhz, ant):
    validate_elements(elements, ant)
    nec_text = _build_nec_text_for_pattern(elements, ant, freq_mhz)
    _, _, out_text = _run_nec2c(nec_text, tag="pattern")
    parsed = _parse_radiation_pattern(out_text)

    horizon = parsed["horizon_gains_by_phi"]
    forward_gain = float(horizon.get(90, horizon.get(91, horizon.get(89, -999.0))))
    rear_gain    = float(horizon.get(270, horizon.get(271, horizon.get(269, -999.0))))
    # HORIZON_FIX_v1: robust horizon gain via 2D grid lookup (fixes -999 sentinel)
    _gf = _horizon_gain_from_grid(parsed.get("gains_2d"), parsed.get("thetas"), parsed.get("phis"), 90.0)
    _gr = _horizon_gain_from_grid(parsed.get("gains_2d"), parsed.get("thetas"), parsed.get("phis"), 270.0)
    if forward_gain < -100.0 and _gf is not None: forward_gain = _gf
    if rear_gain    < -100.0 and _gr is not None: rear_gain    = _gr
    horizon_gain_fwd  = _gf if _gf is not None else forward_gain
    horizon_gain_rear = _gr if _gr is not None else rear_gain

    real_gain = float(parsed["peak_dbi"])
    peak_elev = 90.0 - float(parsed["peak_theta_deg"])
    peak_phi  = float(parsed["peak_phi_deg"])

    # Use TRUE F/B at peak elevation, not at horizon
    front_back = float(parsed.get("front_back_db", forward_gain - rear_gain))

    # Beamwidth at peak elevation, not horizon (horizon has nulls for ground antennas)
    gains_horiz = [horizon.get(p, horizon.get(p - 1, -999.0)) for p in range(360)]
    max_gain_horiz = max(gains_horiz) if gains_horiz else forward_gain
    max_idx_horiz = gains_horiz.index(max_gain_horiz) if gains_horiz else 90

    # Build a phi-slice at the actual peak elevation
    thetas = parsed.get("thetas", [])
    grid_p = parsed.get("gains_2d", [])
    phis_p = parsed.get("phis", [])
    beamwidth = 0.0
    if thetas and grid_p and phis_p:
        # closest theta index to peak_t
        t_idx = min(range(len(thetas)), key=lambda i: abs(thetas[i] - parsed["peak_theta_deg"]))
        elev_slice = grid_p[t_idx]
        # BW_INTERP_v1: sub-degree -3dB beamwidth via linear interpolation
        beamwidth = _beamwidth_interp(elev_slice, phis_p)
    if beamwidth <= 0.0:
        beamwidth = float(_estimate_beamwidth(gains_horiz, forward_idx=90))

    if abs(front_back) > 100.0:
        # Parser edge case clamp -- loosened to 80 dB to match parser
        front_back = max(-50.0, min(80.0, front_back))

    return PatternResult(
        freq_mhz=float(freq_mhz),
        forward_gain_dbi=forward_gain,
        rear_gain_dbi=rear_gain,
        front_back_db=front_back,
        max_gain_dbi=max_gain_horiz,
        max_gain_phi_deg=float(max_idx_horiz),
        beamwidth_deg=beamwidth,
        real_gain_dbi=real_gain,
        peak_elev_deg=peak_elev,
        peak_phi_deg=peak_phi,
        horizon_gain_dbi=horizon_gain_fwd,
        horizon_rear_gain_dbi=horizon_gain_rear,
    )



def evaluate_pattern_for_design(design, freq_mhz=27.185, ant=None):
    if ant is None:
        ant = AntennaConfig()
    elements = build_elements(design)
    return _evaluate_pattern(elements, freq_mhz, ant)


def evaluate_pattern_for_cell(elements, freq_mhz=27.185, ant=None):
    """Pattern evaluation for DE-only cell (skips full-array REF/DIR validation)."""
    if ant is None:
        ant = AntennaConfig()
    nec_text = _build_nec_text_for_pattern(elements, ant, freq_mhz)
    _, _, out_text = _run_nec2c(nec_text, tag="pattern_cell")
    parsed = _parse_radiation_pattern(out_text)

    horizon = parsed["horizon_gains_by_phi"]
    forward_gain = float(horizon.get(90, horizon.get(91, horizon.get(89, -999.0))))
    rear_gain    = float(horizon.get(270, horizon.get(271, horizon.get(269, -999.0))))
    # HORIZON_FIX_v1: robust horizon gain via 2D grid lookup (fixes -999 sentinel)
    _gf = _horizon_gain_from_grid(parsed.get("gains_2d"), parsed.get("thetas"), parsed.get("phis"), 90.0)
    _gr = _horizon_gain_from_grid(parsed.get("gains_2d"), parsed.get("thetas"), parsed.get("phis"), 270.0)
    if forward_gain < -100.0 and _gf is not None: forward_gain = _gf
    if rear_gain    < -100.0 and _gr is not None: rear_gain    = _gr
    horizon_gain_fwd  = _gf if _gf is not None else forward_gain
    horizon_gain_rear = _gr if _gr is not None else rear_gain

    real_gain = float(parsed["peak_dbi"])
    peak_elev = 90.0 - float(parsed["peak_theta_deg"])
    peak_phi  = float(parsed["peak_phi_deg"])

    # Use TRUE F/B at peak elevation (computed by parser), not horizon
    front_back = float(parsed.get("front_back_db", forward_gain - rear_gain))
    if abs(front_back) > 100.0:
        front_back = max(-10.0, min(80.0, front_back))

    gains_horiz = [horizon.get(p, horizon.get(p - 1, -999.0)) for p in range(360)]
    max_gain_horiz = max(gains_horiz) if gains_horiz else forward_gain
    max_idx_horiz = gains_horiz.index(max_gain_horiz) if gains_horiz else 90

    # Beamwidth at peak elevation, not horizon (horizon is null for ground antennas)
    thetas = parsed.get("thetas", [])
    grid_p = parsed.get("gains_2d", [])
    phis_p = parsed.get("phis", [])
    beamwidth = 0.0
    if thetas and grid_p and phis_p:
        t_idx = min(range(len(thetas)), key=lambda i: abs(thetas[i] - parsed["peak_theta_deg"]))
        elev_slice = grid_p[t_idx]
        beamwidth = _beamwidth_interp(elev_slice, phis_p)  # BW_INTERP_v1
    if beamwidth <= 0.0:
        beamwidth = float(_estimate_beamwidth(gains_horiz, forward_idx=90))

    return PatternResult(
        freq_mhz=float(freq_mhz),
        forward_gain_dbi=forward_gain,
        rear_gain_dbi=rear_gain,
        front_back_db=front_back,
        max_gain_dbi=max_gain_horiz,
        max_gain_phi_deg=float(max_idx_horiz),
        beamwidth_deg=beamwidth,
        real_gain_dbi=real_gain,
        peak_elev_deg=peak_elev,
        peak_phi_deg=peak_phi,
        horizon_gain_dbi=horizon_gain_fwd,
        horizon_rear_gain_dbi=horizon_gain_rear,
    )


def evaluate_pattern_for_elements(elements, freq_mhz=27.185, ant=None):
    if ant is None:
        ant = AntennaConfig()
    return _evaluate_pattern(elements, freq_mhz, ant)


def pattern_for_best(freq_mhz=27.185):
    row = db.best_run()
    if row is None:
        raise RuntimeError("No best run found. Run autotune first.")
    design = design_from_row(row)
    result = evaluate_pattern_for_design(design, freq_mhz=freq_mhz)
    return row, result


def pattern_for_run(run_id, freq_mhz=27.185):
    row = get_run_by_id(run_id)
    if row is None:
        raise RuntimeError(f"No run found for id={run_id}")
    design = design_from_row(row)
    result = evaluate_pattern_for_design(design, freq_mhz=freq_mhz)
    return row, result
