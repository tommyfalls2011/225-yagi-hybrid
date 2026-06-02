"""Auto-generate Yagi seed geometry for any N from 2 to 12 at any frequency.
Preserves the EXACT original 7-element @ 27.195 MHz seeds so the champion stays bit-identical.
"""
import numpy as np

ORIGINAL_7EL_LENGTHS_FT  = np.array([18.90, 17.80, 17.05, 16.65, 16.30, 16.00, 15.75], dtype=float)
ORIGINAL_7EL_SPACINGS_FT = np.array([4.50, 3.80, 4.70, 5.10, 5.40, 5.70], dtype=float)

def make_element_names(n):
    if n < 2: raise ValueError("need at least 2 elements (REF + DE)")
    names = ["REF", "DE"]
    for i in range(1, n - 1):
        names.append(f"D{i}")
    return names

def make_seed(n_elements, freq_mhz, height_ft=50.0):
    n = int(n_elements); f = float(freq_mhz)
    if n < 2 or n > 12: raise ValueError("n_elements must be 2..12")
    # Preserve champion bit-exact when N=7 and freq near 27.195
    if n == 7 and abs(f - 27.195) < 0.05:
        return ORIGINAL_7EL_LENGTHS_FT.copy(), ORIGINAL_7EL_SPACINGS_FT.copy(), float(height_ft)
    lam_ft = 983.6 / f
    lengths_lam = np.zeros(n, dtype=float)
    lengths_lam[0] = 0.523                              # REF
    if n >= 2: lengths_lam[1] = 0.492                   # DE
    for i in range(2, n):                               # D1, D2, ...
        d_idx = i - 1
        lengths_lam[i] = max(0.40, 0.471 - 0.009 * (d_idx - 1))
    spacings_lam = np.zeros(max(0, n - 1), dtype=float)
    if n >= 2: spacings_lam[0] = 0.124                  # REF→DE
    if n >= 3: spacings_lam[1] = 0.105                  # DE→D1 (close-coupled)
    for i in range(2, n - 1):                           # D(i-1)→Di
        step = i - 1
        spacings_lam[i] = min(0.18, 0.130 + 0.010 * (step - 1))
    return lengths_lam * lam_ft, spacings_lam * lam_ft, float(height_ft)
