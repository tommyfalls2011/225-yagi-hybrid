import numpy as np

from .constants import RL_REPORT_CLIP_DB, RL_REWARD_CLIP_DB
from .rfmath import (
    return_loss_db_raw,
    return_loss_db,
    swr_from_z,
    mismatch_efficiency_percent,
    is_finite_complex,
)


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


def sweep_design(lengths, spacings, height, freqs_mhz, use_real_ground, solve_impedance_fn):
    freqs = np.asarray(freqs_mhz, dtype=float)

    z_list = []
    rl_report_list = []
    rl_raw_list = []
    swr_list = []
    eta_list = []

    for f in freqs:
        try:
            zi = solve_impedance_fn(lengths, spacings, height, float(f), use_real_ground)
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


def _find_center_impedance_from_sweep(freqs_opt, z, center_freq):
    freqs_opt = np.asarray(freqs_opt, dtype=float)
    z = np.asarray(z, dtype=complex)

    idx = np.where(np.isclose(freqs_opt, center_freq, atol=1e-12))[0]
    if idx.size == 0:
        return None

    zi = z[idx[0]]
    if is_finite_complex(zi):
        return complex(zi.real, zi.imag)
    return None



def _sample_nearest_valid(freqs_mhz, values, target_freq_mhz):
    freqs = np.asarray(freqs_mhz, dtype=float)
    vals = np.asarray(values, dtype=float)

    valid = np.isfinite(freqs) & np.isfinite(vals)
    if not np.any(valid):
        return np.nan

    idx_valid = np.where(valid)[0]
    idx = idx_valid[np.argmin(np.abs(freqs[idx_valid] - float(target_freq_mhz)))]
    return float(vals[idx])


def evaluate_candidate_metrics(lengths, spacings, height, freqs_opt, center_freq, target_rl_db,
                               use_real_ground, measure_gain,
                               solve_impedance_fn, estimate_pattern_fn):
    z, rl_report, rl_raw, swr, eta = sweep_design(
        lengths, spacings, height, freqs_opt, use_real_ground, solve_impedance_fn
    )

    zc = _find_center_impedance_from_sweep(freqs_opt, z, center_freq)
    if zc is None:
        zc = solve_impedance_fn(lengths, spacings, height, center_freq, use_real_ground)

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

    bw18, _, _ = approximate_bandwidth(freqs_opt, rl_reward, target_rl_db)
    bw15, _, _ = approximate_bandwidth(freqs_opt, rl_reward, 15.0)
    bw12, _, _ = approximate_bandwidth(freqs_opt, rl_reward, 12.0)

    lower_035_rl = _sample_nearest_valid(freqs_opt, rl_reward, center_freq - 0.35)
    upper_035_rl = _sample_nearest_valid(freqs_opt, rl_reward, center_freq + 0.35)

    lower_050_rl = _sample_nearest_valid(freqs_opt, rl_reward, center_freq - 0.50)
    upper_050_rl = _sample_nearest_valid(freqs_opt, rl_reward, center_freq + 0.50)

    lower_080_rl = _sample_nearest_valid(freqs_opt, rl_reward, center_freq - 0.80)
    upper_080_rl = _sample_nearest_valid(freqs_opt, rl_reward, center_freq + 0.80)

    shoulder_balance_db = (
        abs(lower_035_rl - upper_035_rl)
        if np.isfinite(lower_035_rl) and np.isfinite(upper_035_rl) else np.nan
    )

    outer_balance_db = (
        abs(lower_050_rl - upper_050_rl)
        if np.isfinite(lower_050_rl) and np.isfinite(upper_050_rl) else np.nan
    )

    upper_collapse_db = (
        max(0.0, lower_050_rl - upper_050_rl)
        if np.isfinite(lower_050_rl) and np.isfinite(upper_050_rl) else np.nan
    )

    forward_gain_db = np.nan
    rear_gain_db = np.nan
    front_to_back_db = np.nan

    if measure_gain:
        forward_gain_db, rear_gain_db, front_to_back_db = estimate_pattern_fn(
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
        "bandwidth_mhz": float(bw18),
        "bandwidth15_mhz": float(bw15),
        "bandwidth12_mhz": float(bw12),
        "lower_035_rl": float(lower_035_rl) if np.isfinite(lower_035_rl) else np.nan,
        "upper_035_rl": float(upper_035_rl) if np.isfinite(upper_035_rl) else np.nan,
        "lower_050_rl": float(lower_050_rl) if np.isfinite(lower_050_rl) else np.nan,
        "upper_050_rl": float(upper_050_rl) if np.isfinite(upper_050_rl) else np.nan,
        "lower_080_rl": float(lower_080_rl) if np.isfinite(lower_080_rl) else np.nan,
        "upper_080_rl": float(upper_080_rl) if np.isfinite(upper_080_rl) else np.nan,
        "shoulder_balance_db": float(shoulder_balance_db) if np.isfinite(shoulder_balance_db) else np.nan,
        "outer_balance_db": float(outer_balance_db) if np.isfinite(outer_balance_db) else np.nan,
        "upper_collapse_db": float(upper_collapse_db) if np.isfinite(upper_collapse_db) else np.nan,
        "forward_gain_db": float(forward_gain_db) if np.isfinite(forward_gain_db) else np.nan,
        "rear_gain_db": float(rear_gain_db) if np.isfinite(rear_gain_db) else np.nan,
        "front_to_back_db": float(front_to_back_db) if np.isfinite(front_to_back_db) else np.nan,
        "r_err": (float(zc.real) - 50.0) / 8.0,
        "x_err": float(zc.imag) / 8.0,
    }
