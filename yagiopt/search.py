import math
import numpy as np
from scipy.optimize import differential_evolution

from .geometry import move_element_position, move_element_length, inches_to_ft


def sanitize_bound(lo, hi, hard_lo, hard_hi):
    lo = max(float(lo), float(hard_lo))
    hi = min(float(hi), float(hard_hi))

    if not (np.isfinite(lo) and np.isfinite(hi)):
        raise ValueError(f"Non-finite bound after sanitization: lo={lo}, hi={hi}")

    if lo > hi:
        lo, hi = hi, lo

    if math.isclose(lo, hi, rel_tol=0.0, abs_tol=1e-9):
        pad = max(1e-5, 1e-4 * max(1.0, abs(lo)))
        lo = max(hard_lo, lo - pad)
        hi = min(hard_hi, hi + pad)

    if lo >= hi:
        hi = min(hard_hi, lo + 1e-5)
        if lo >= hi:
            lo = max(hard_lo, hi - 1e-5)

    return float(lo), float(hi)


def build_de_init_population(active_bounds, seed_active, popsize, seed):
    rng = np.random.default_rng(seed)

    lower = np.array([b[0] for b in active_bounds], dtype=float)
    upper = np.array([b[1] for b in active_bounds], dtype=float)
    span = upper - lower

    dim = len(active_bounds)
    npop = max(8, popsize * dim)

    init = np.empty((npop, dim), dtype=float)
    init[0] = np.clip(np.asarray(seed_active, dtype=float), lower, upper)

    for i in range(1, npop):
        if i < max(3, npop // 2):
            jitter = rng.normal(0.0, 0.08, size=dim) * span
            candidate = init[0] + jitter
        else:
            candidate = lower + rng.random(dim) * span

        init[i] = np.clip(candidate, lower, upper)

    return init


def run_differential_evolution(objective, bounds, maxiter, popsize, seed, workers, init_population):
    return differential_evolution(
        objective,
        bounds,
        maxiter=maxiter,
        popsize=popsize,
        tol=0.01,
        mutation=(0.5, 1.0),
        recombination=0.7,
        polish=False,
        seed=seed,
        workers=workers,
        updating="immediate" if workers == 1 else "deferred",
        disp=True,
        init=init_population,
    )


def coordinate_position_search(start_rec, search_name, step_sizes_in, passes_per_step,
                               evaluate_fn, element_names, print_fn=None):
    element_order = [1, 2, 3, 4, 5, 6]   # DE..D5, REF fixed
    current = start_rec

    if print_fn is not None:
        print(f"\n=== {search_name.upper()} ===")
        print_fn(current, prefix="  ")

    for step_in in step_sizes_in:
        step_ft = inches_to_ft(step_in)
        print(f"\n  Step size: {step_in:.3f} in")

        for pass_idx in range(1, passes_per_step + 1):
            improved_this_pass = False
            print(f"    Pass {pass_idx}")

            for elem_idx in element_order:
                best_trial = None

                for sign, direction_name in ((+1.0, "forward"), (-1.0, "backward")):
                    try:
                        x_trial = move_element_position(current["x_full"], elem_idx, sign * step_ft)
                    except Exception:
                        continue

                    trial = evaluate_fn(
                        x_trial,
                        label=f"{search_name}:pos",
                        accepted=False,
                        note=f"{element_names[elem_idx]} {direction_name} {step_in:.3f} in",
                    )

                    if trial is None:
                        continue

                    if trial["score"] > current["score"] + 1e-9:
                        if best_trial is None or trial["score"] > best_trial["score"]:
                            best_trial = trial

                if best_trial is not None:
                    best_trial["accepted"] = True
                    current = best_trial
                    improved_this_pass = True
                    if print_fn is not None:
                        print_fn(current, prefix="      ACCEPT ")

            if not improved_this_pass:
                print("      No improving moves this pass.")
                break

    return current


def coordinate_length_search(start_rec, search_name, step_sizes_in, passes_per_step,
                             evaluate_fn, element_names, print_fn=None):
    element_order = [0, 1, 2, 3, 4, 5, 6]
    current = start_rec

    if print_fn is not None:
        print(f"\n=== {search_name.upper()} ===")
        print_fn(current, prefix="  ")

    for step_in in step_sizes_in:
        step_ft = inches_to_ft(step_in)
        print(f"\n  Length step size: {step_in:.3f} in")

        for pass_idx in range(1, passes_per_step + 1):
            improved_this_pass = False
            print(f"    Pass {pass_idx}")

            for elem_idx in element_order:
                best_trial = None

                for sign, direction_name in ((+1.0, "longer"), (-1.0, "shorter")):
                    try:
                        x_trial = move_element_length(current["x_full"], elem_idx, sign * step_ft)
                    except Exception:
                        continue

                    trial = evaluate_fn(
                        x_trial,
                        label=f"{search_name}:len",
                        accepted=False,
                        note=f"{element_names[elem_idx]} {direction_name} {step_in:.3f} in",
                    )

                    if trial is None:
                        continue

                    if trial["score"] > current["score"] + 1e-9:
                        if best_trial is None or trial["score"] > best_trial["score"]:
                            best_trial = trial

                if best_trial is not None:
                    best_trial["accepted"] = True
                    current = best_trial
                    improved_this_pass = True
                    if print_fn is not None:
                        print_fn(current, prefix="      ACCEPT ")

            if not improved_this_pass:
                print("      No improving moves this pass.")
                break

    return current


def coordinate_region_position_search(start_rec, search_name, step_sizes_in, passes_per_step,
                                      evaluate_fn, element_names, element_indices,
                                      print_fn=None):
    """Position search restricted to a specific set of element indices.
    element_indices: list of ints in 1..6 (REF is always fixed at y=0)."""
    current = start_rec

    if print_fn is not None:
        print(f"\n=== {search_name.upper()} (moving: {', '.join(element_names[i] for i in element_indices)}) ===")
        print_fn(current, prefix="  ")

    for step_in in step_sizes_in:
        step_ft = inches_to_ft(step_in)
        print(f"\n  Step size: {step_in:.3f} in")

        for pass_idx in range(1, passes_per_step + 1):
            improved_this_pass = False
            print(f"    Pass {pass_idx}")

            for elem_idx in element_indices:
                best_trial = None

                for sign, direction_name in ((+1.0, "forward"), (-1.0, "backward")):
                    try:
                        x_trial = move_element_position(current["x_full"], elem_idx, sign * step_ft)
                    except Exception:
                        continue

                    trial = evaluate_fn(
                        x_trial,
                        label=f"{search_name}:pos",
                        accepted=False,
                        note=f"{element_names[elem_idx]} {direction_name} {step_in:.3f} in",
                    )

                    if trial is None:
                        continue

                    if trial["score"] > current["score"] + 1e-9:
                        if best_trial is None or trial["score"] > best_trial["score"]:
                            best_trial = trial

                if best_trial is not None:
                    best_trial["accepted"] = True
                    current = best_trial
                    improved_this_pass = True
                    if print_fn is not None:
                        print_fn(current, prefix="      ACCEPT ")

            if not improved_this_pass:
                print("      No improving moves this pass.")
                break

    return current


def coordinate_region_length_search(start_rec, search_name, step_sizes_in, passes_per_step,
                                    evaluate_fn, element_names, element_indices,
                                    print_fn=None):
    """Length trim restricted to a specific set of element indices (0..6)."""
    current = start_rec

    if print_fn is not None:
        print(f"\n=== {search_name.upper()} (trimming: {', '.join(element_names[i] for i in element_indices)}) ===")
        print_fn(current, prefix="  ")

    for step_in in step_sizes_in:
        step_ft = inches_to_ft(step_in)
        print(f"\n  Length step size: {step_in:.3f} in")

        for pass_idx in range(1, passes_per_step + 1):
            improved_this_pass = False
            print(f"    Pass {pass_idx}")

            for elem_idx in element_indices:
                best_trial = None

                for sign, direction_name in ((+1.0, "longer"), (-1.0, "shorter")):
                    try:
                        x_trial = move_element_length(current["x_full"], elem_idx, sign * step_ft)
                    except Exception:
                        continue

                    trial = evaluate_fn(
                        x_trial,
                        label=f"{search_name}:len",
                        accepted=False,
                        note=f"{element_names[elem_idx]} {direction_name} {step_in:.3f} in",
                    )

                    if trial is None:
                        continue

                    if trial["score"] > current["score"] + 1e-9:
                        if best_trial is None or trial["score"] > best_trial["score"]:
                            best_trial = trial

                if best_trial is not None:
                    best_trial["accepted"] = True
                    current = best_trial
                    improved_this_pass = True
                    if print_fn is not None:
                        print_fn(current, prefix="      ACCEPT ")

            if not improved_this_pass:
                print("      No improving moves this pass.")
                break

    return current


def dedupe_logbook(logbook, decimals=5):
    best_by_key = {}

    for rec in logbook:
        x = np.asarray(rec["x_full"], dtype=float)
        key = tuple(np.round(x, decimals))
        old = best_by_key.get(key)
        if old is None or rec["score"] > old["score"]:
            best_by_key[key] = rec

    return list(best_by_key.values())


def choose_best_logged_layout(logbook):
    uniq = dedupe_logbook(logbook)
    if not uniq:
        raise RuntimeError("No valid logged layouts available for final selection")
    return max(uniq, key=lambda r: r["score"])
