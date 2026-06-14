"""Single-source-of-truth F/B (and F/R) calculator.

Textbook definition (matches ARRL Antenna Book, EZNEC, MMANA-GAL, 4nec2):

    F/B = gain_at_forward_peak  -  gain_at_(peak_theta, peak_phi + 180 deg)

i.e. ONE point at exactly the back azimuth at the SAME elevation as the
forward peak. NOT a max over a rear cone, NOT a max over all elevations
in the rear hemisphere -- those are different conventions different code
paths have used in this project before and caused the F/B value to read
differently on the matcher panel vs the Report panel.

Both v2_runner.evaluate() and perf_report.analyze() now call this one
function so the F/B number is bitwise identical wherever it appears.

The pattern grid out of nec2c is a 37x73 hemisphere (theta 0..180 step 5,
phi 0..360 step 5).  Whenever (peak_theta, peak_phi+180) isn't on the
grid we bilinearly interpolate between the 4 surrounding samples.
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple


_Pattern = Iterable[Tuple[float, float, float]]   # (theta_deg, phi_deg, gain_dBi)


def _grid(pattern: _Pattern) -> dict:
    """Index the pattern as {theta: {phi: gain}} with phi mapped into 0..360."""
    grid: dict = {}
    for t, p, g in pattern:
        t = float(t)
        p = float(p) % 360.0
        g = float(g)
        grid.setdefault(t, {})[p] = g
    return grid


def _gain_at(pattern: _Pattern, theta: float, phi: float,
             grid: Optional[dict] = None) -> float:
    """Bilinear interpolation of gain (dBi) at an arbitrary (theta, phi).

    Interpolates in dB.  That isn't perfect physics (linear is in power) but
    matches what every other antenna sim tool does and reads the same as
    MMANA-GAL / EZNEC F/B numbers."""
    if grid is None:
        grid = _grid(pattern)
    if not grid:
        return -100.0
    thetas = sorted(grid.keys())
    phi = phi % 360.0
    # Theta bracket.  If exactly on grid, use that row.  Otherwise the two
    # neighbours.  Clamp to the available range -- the nec2c hemisphere RP is
    # theta 0..180, so we won't usually need clamping for sensible patterns.
    le = [t for t in thetas if t <= theta]
    ge = [t for t in thetas if t >= theta]
    t0 = le[-1] if le else thetas[0]
    t1 = ge[0] if ge else thetas[-1]

    def gain_at_theta(t_grid, phi):
        row = grid[t_grid]
        phis = sorted(row.keys())
        if not phis:
            return -100.0
        # Phi bracket; if 360 isn't in the row, wrap to 0.
        le_p = [p for p in phis if p <= phi]
        ge_p = [p for p in phis if p >= phi]
        p0 = le_p[-1] if le_p else (phis[-1] - 360.0)   # phi just below 0 via wrap
        p1 = ge_p[0] if ge_p else (phis[0] + 360.0)     # phi just above 360 via wrap
        g0 = row.get(p0 % 360.0, row.get(phis[-1], -100.0))
        g1 = row.get(p1 % 360.0, row.get(phis[0], -100.0))
        if p1 == p0:
            return g0
        frac = (phi - p0) / (p1 - p0)
        return g0 * (1.0 - frac) + g1 * frac

    g_t0 = gain_at_theta(t0, phi)
    g_t1 = gain_at_theta(t1, phi)
    if t1 == t0:
        return g_t0
    frac_t = (theta - t0) / (t1 - t0)
    return g_t0 * (1.0 - frac_t) + g_t1 * frac_t


def forward_peak(pattern: _Pattern) -> Tuple[float, float, float]:
    """Brightest (theta, phi, gain) in the pattern."""
    return max(pattern, key=lambda t: t[2])


def front_to_back(pattern: _Pattern,
                  peak: Optional[Tuple[float, float, float]] = None) -> float:
    """Textbook front-to-back ratio in dB.

    F/B = peak_gain - gain_at_(peak_theta, peak_phi + 180 deg)

    Single point.  Same elevation as the forward peak.  Exactly 180 degrees
    around in azimuth.  Bilinear interp when the grid doesn't sample it
    exactly.  If the rear point happens to sit in a deep null and reads a
    physically silly value (e.g. < -60 dBi) the result will be a very large
    F/B -- that's the textbook answer; do not silently clamp."""
    pts = [(float(t), float(p), float(g)) for (t, p, g) in pattern]
    if not pts:
        return 0.0
    if peak is None:
        peak = max(pts, key=lambda t: t[2])
    pk_theta, pk_phi, pk_gain = peak
    rear_phi = (pk_phi + 180.0) % 360.0
    rear_gain = _gain_at(pts, pk_theta, rear_phi)
    return pk_gain - rear_gain


def front_to_rear(pattern: _Pattern,
                  peak: Optional[Tuple[float, float, float]] = None) -> float:
    """Front-to-REAR ratio in dB.

    F/R = peak_gain - max(gain across the whole REAR hemisphere)

    Rear hemisphere = phi within +/-90 deg of (peak_phi + 180), at any
    elevation.  This is the worst-case rear lobe -- always >= F/B because
    the max(...) only goes up if there are stronger off-axis rear lobes."""
    pts = [(float(t), float(p), float(g)) for (t, p, g) in pattern]
    if not pts:
        return 0.0
    if peak is None:
        peak = max(pts, key=lambda t: t[2])
    _pk_theta, pk_phi, pk_gain = peak
    rear = [g for (t, p, g) in pts
            if abs(((p - pk_phi + 180.0) % 360.0) - 180.0) > 90.0]
    if not rear:
        return 0.0
    return pk_gain - max(rear)
