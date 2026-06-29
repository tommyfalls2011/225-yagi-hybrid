import math
import numpy as np

from .constants import Z0, RL_REPORT_CLIP_DB


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
