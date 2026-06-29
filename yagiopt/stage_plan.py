"""Build the 9-stage element-index plan dynamically for any N from 2 to 12.
Preserves the EXACT 7-element plan when N=7 so the champion stays bit-identical.
"""

ORIGINAL_7EL_PLAN = {
    "stage2_all_spacings":    [1, 2, 3, 4, 5, 6],
    "stage3_match_pos":       [1, 2],
    "stage3_match_len":       [0, 1, 2],
    "stage4_length_sweep":    [0, 1, 2, 3],
    "stage5_spacing_refine":  [1, 2, 3],
    "stage6_gain_pos":        [3, 4],
    "stage6_gain_len":        [3, 4],
    "stage7_polish_pos":      [5, 6],
    "stage7_polish_len":      [5, 6],
    "stage8_refit_pos":       [1, 2],
    "stage8_refit_len":       [0, 1, 2],
    "stage9_refit_pos":       [1, 2],
    "stage9_refit_len":       [0, 1, 2],
}

def make_plan(n):
    n = int(n)
    if n < 2 or n > 12: raise ValueError("n must be 2..12")
    if n == 7: return {k: list(v) for k, v in ORIGINAL_7EL_PLAN.items()}
    last = n - 1
    def clip(idxs): return [i for i in idxs if 0 <= i <= last]
    match_pos = clip([1, 2])
    match_len = clip([0, 1, 2])
    all_spacings = list(range(1, n))
    length_sweep = clip([0, 1, 2, 3])
    spacing_refine = clip([1, 2, 3])
    # Gain: directors between D2 (idx 3) and the last 2 (which are polish)
    gain = clip(list(range(3, max(3, last - 1))))
    # Polish: last 1-2 directors, but never overlap match region
    polish_candidates = [last - 1, last] if last >= 4 else ([last] if last >= 3 else [])
    polish = [i for i in polish_candidates if i not in match_pos and i not in match_len]
    return {
        "stage2_all_spacings":   all_spacings,
        "stage3_match_pos":      match_pos,
        "stage3_match_len":      match_len,
        "stage4_length_sweep":   length_sweep,
        "stage5_spacing_refine": spacing_refine,
        "stage6_gain_pos":       gain,
        "stage6_gain_len":       gain,
        "stage7_polish_pos":     polish,
        "stage7_polish_len":     polish,
        "stage8_refit_pos":      match_pos,
        "stage8_refit_len":      match_len,
        "stage9_refit_pos":      match_pos,
        "stage9_refit_len":      match_len,
    }
