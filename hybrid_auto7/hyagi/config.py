from dataclasses import dataclass

INCH_TO_M = 0.0254

ELEMENT_ORDER = [
    "REF",
    "XFRMR",
    "DE",
    "COUPLER",
    "DIR1",
    "DIR2",
    "DIR3",
]

BASE_LENGTHS_IN = {
    "REF": 218.0,
    "XFRMR": 209.0,
    "DE": 203.0,
    "COUPLER": 197.0,
    "DIR1": 191.0,
    "DIR2": 185.0,
    "DIR3": 180.0,
}

BASE_POSITIONS_IN = {
    "REF": 0.0,
    "DE": 61.0,
    "DIR1": 135.0,
    "DIR2": 240.0,
    "DIR3": 335.0,
}


@dataclass
class AntennaConfig:
    boom_length_in: float = 360.0
    boom_diameter_in: float = 2.0

    center_od_in: float = 0.625
    outer_od_in: float = 0.500
    center_half_len_in: float = 36.0

    center_segments: int = 11
    outer_segments: int = 15

    model_height_in: float = 360.0

    ground_mode: str = "average"
    ground_epsr: float = 13.0
    ground_sigma_s_per_m: float = 0.005

    # NEW
    cell_mounting_style: str = "full_cell_insulated"


@dataclass
class Design:
    de_position_in: float = 61.0
    xfrmr_spacing_in: float = 12.0
    coupler_spacing_in: float = 12.0
    xfrmr_length_in: float = 209.0
    coupler_length_in: float = 197.0
    de_length_in: float = 203.0

    dir1_position_in: float | None = None
    dir1_length_in: float | None = None
    dir2_position_in: float | None = None
    dir2_length_in: float | None = None
    dir3_position_in: float | None = None
    dir3_length_in: float | None = None


def inch_to_m(value_in):
    return float(value_in) * INCH_TO_M


def frange(start, stop, step):
    start = float(start)
    stop = float(stop)
    step = float(step)

    if step <= 0:
        raise ValueError("step must be greater than zero")

    vals = []
    x = start

    while x <= stop + 1e-9:
        vals.append(round(x, 6))
        x += step

    return vals
