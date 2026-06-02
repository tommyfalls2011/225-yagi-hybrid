from dataclasses import dataclass
import math


@dataclass
class FreqResult:
    freq_mhz: float
    r_ohm: float
    x_ohm: float
    swr_50: float


@dataclass
class Summary:
    min_swr: float
    max_swr: float
    avg_swr: float
    points_under_1p5: int
    points_under_2p0: int
    avg_r: float
    avg_abs_x: float

    center_r: float
    center_x: float
    center_swr: float
    center_rl_db: float

    bw_1p5_mhz: float
    bw_2p0_mhz: float
    low_edge_1p5_mhz: float | None
    high_edge_1p5_mhz: float | None
    low_edge_2p0_mhz: float | None
    high_edge_2p0_mhz: float | None


def swr_from_impedance(r_ohm, x_ohm, z0=50.0):
    z0 = float(z0)
    if z0 <= 0:
        raise ValueError("z0 must be positive")

    z = complex(float(r_ohm), float(x_ohm))
    denom = z + z0

    if abs(denom) < 1e-12:
        return 999.0

    gamma = (z - z0) / denom
    mag = abs(gamma)

    if mag >= 1.0:
        return 999.0

    mag = max(0.0, min(mag, 0.999999999999))
    return (1.0 + mag) / (1.0 - mag)


def return_loss_db(swr):
    if swr is None:
        return None

    swr = float(swr)

    if swr <= 1.0:
        return 99.0

    gamma = (swr - 1.0) / (swr + 1.0)

    if gamma <= 0:
        return 99.0

    gamma = max(gamma, 1e-12)
    return -20.0 * math.log10(gamma)


def center_result(results, center_freq=None):
    if not results:
        return None

    if center_freq is None:
        center_freq = (results[0].freq_mhz + results[-1].freq_mhz) / 2.0

    return min(results, key=lambda r: abs(r.freq_mhz - center_freq))


def contiguous_bandwidth(results, swr_limit):
    good = [r for r in results if r.swr_50 <= swr_limit]
    if not good:
        return 0.0, None, None

    best_start = None
    best_stop = None
    cur_start = good[0].freq_mhz
    prev = good[0].freq_mhz

    best_bw = 0.0

    step_guess = 0.0
    if len(results) >= 2:
        step_guess = results[1].freq_mhz - results[0].freq_mhz

    for r in good[1:]:
        if step_guess > 0 and abs((r.freq_mhz - prev) - step_guess) > 1e-6:
            bw = prev - cur_start
            if bw > best_bw:
                best_bw = bw
                best_start = cur_start
                best_stop = prev
            cur_start = r.freq_mhz
        prev = r.freq_mhz

    bw = prev - cur_start
    if bw > best_bw:
        best_bw = bw
        best_start = cur_start
        best_stop = prev

    return round(best_bw, 6), best_start, best_stop


def summarize(results):
    if not results:
        return Summary(
            min_swr=999.0,
            max_swr=999.0,
            avg_swr=999.0,
            points_under_1p5=0,
            points_under_2p0=0,
            avg_r=999.0,
            avg_abs_x=999.0,
            center_r=999.0,
            center_x=999.0,
            center_swr=999.0,
            center_rl_db=0.0,
            bw_1p5_mhz=0.0,
            bw_2p0_mhz=0.0,
            low_edge_1p5_mhz=None,
            high_edge_1p5_mhz=None,
            low_edge_2p0_mhz=None,
            high_edge_2p0_mhz=None,
        )

    swrs = [float(r.swr_50) for r in results]
    rs = [float(r.r_ohm) for r in results]
    abs_xs = [abs(float(r.x_ohm)) for r in results]

    center = center_result(results)
    bw_1p5, lo_1p5, hi_1p5 = contiguous_bandwidth(results, 1.5)
    bw_2p0, lo_2p0, hi_2p0 = contiguous_bandwidth(results, 2.0)

    return Summary(
        min_swr=round(min(swrs), 6),
        max_swr=round(max(swrs), 6),
        avg_swr=round(sum(swrs) / len(swrs), 6),
        points_under_1p5=sum(1 for s in swrs if s <= 1.5),
        points_under_2p0=sum(1 for s in swrs if s <= 2.0),
        avg_r=round(sum(rs) / len(rs), 6),
        avg_abs_x=round(sum(abs_xs) / len(abs_xs), 6),

        center_r=round(float(center.r_ohm), 6),
        center_x=round(float(center.x_ohm), 6),
        center_swr=round(float(center.swr_50), 6),
        center_rl_db=round(float(return_loss_db(center.swr_50)), 6),

        bw_1p5_mhz=bw_1p5,
        bw_2p0_mhz=bw_2p0,
        low_edge_1p5_mhz=lo_1p5,
        high_edge_1p5_mhz=hi_1p5,
        low_edge_2p0_mhz=lo_2p0,
        high_edge_2p0_mhz=hi_2p0,
    )
