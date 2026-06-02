import math
import numpy as np


def ftin(value_ft, denom=16):
    sign = "-" if value_ft < 0 else ""
    v = abs(float(value_ft))
    feet = int(math.floor(v))
    inches = (v - feet) * 12.0
    ticks = int(round(inches * denom))

    if ticks >= 12 * denom:
        feet += 1
        ticks -= 12 * denom

    whole_in = ticks // denom
    frac = ticks % denom

    if frac == 0:
        return f"{sign}{feet} ft {whole_in} in"

    g = math.gcd(frac, denom)
    frac_n = frac // g
    frac_d = denom // g
    return f"{sign}{feet} ft {whole_in} {frac_n}/{frac_d} in"


def inches_to_ft(inches):
    return float(inches) / 12.0


def pack_design(lengths, spacings, height):
    return np.concatenate([
        np.asarray(lengths, dtype=float),
        np.asarray(spacings, dtype=float),
        np.array([float(height)], dtype=float),
    ])


def unpack_design(x):
    x = np.asarray(x, dtype=float)
    lengths = np.array(x[:7], dtype=float)
    spacings = np.array(x[7:13], dtype=float)
    height = float(x[13])
    return lengths, spacings, height


def active_from_full(x_full, active_idx):
    return np.asarray(x_full, dtype=float)[np.asarray(active_idx, dtype=int)]


def full_from_active(x_active, base_full, active_idx):
    x = np.asarray(base_full, dtype=float).copy()
    x[np.asarray(active_idx, dtype=int)] = np.asarray(x_active, dtype=float)
    return x


def y_positions_from_spacings(spacings_ft):
    y = [0.0]
    for s in np.asarray(spacings_ft, dtype=float):
        y.append(y[-1] + s)
    return np.array(y, dtype=float)


def move_element_position(x_full, element_index, delta_ft):
    """
    Move one element along the boom while keeping other element positions fixed.
    REF is fixed at y=0, so valid movable indices are 1..6:
        1=DE, 2=D1, ..., 6=D5
    """
    lengths, spacings, height = unpack_design(x_full)
    lengths = lengths.copy()
    spacings = spacings.copy()

    i = int(element_index)
    if i < 1 or i > 6:
        raise ValueError("element_index must be in 1..6")

    if i < 6:
        spacings[i - 1] += float(delta_ft)
        spacings[i] -= float(delta_ft)
    else:
        spacings[5] += float(delta_ft)

    return pack_design(lengths, spacings, height)


def move_element_length(x_full, element_index, delta_ft):
    lengths, spacings, height = unpack_design(x_full)
    lengths = lengths.copy()
    lengths[int(element_index)] += float(delta_ft)
    return pack_design(lengths, spacings, height)
