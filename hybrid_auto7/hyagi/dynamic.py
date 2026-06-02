from dataclasses import dataclass

REFERENCE_CENTER_MHZ = 27.185


BASE_LENGTHS_27MHZ = {
    "REF": 222.0,
    "XFRMR": 199.0,
    "DE": 210.0,
    "COUPLER": 173.0,
    "DIR1": 194.5,
    "DIR2": 188.0,
    "DIR3": 182.5,
}


@dataclass
class GeneratedElement:
    name: str
    role: str
    position_in: float
    length_in: float


def scale_factor(freq_start_mhz, freq_stop_mhz):
    center = (freq_start_mhz + freq_stop_mhz) / 2.0
    return REFERENCE_CENTER_MHZ / center




def roles_for(element_count, mode="hybrid"):
    """
    Generate roles for supported antenna modes.

    User element count means normal beam count:
        REF + DE + directors

    hybrid:
        adds XFRMR behind DE and COUPLER ahead of DE
        physical count = user element count + 2

    yagi:
        standard REF + DE + directors
        physical count = user element count
    """

    if not (3 <= element_count <= 12):
        raise ValueError("element_count must be 3-12")

    if mode == "hybrid":
        roles = ["REF", "XFRMR", "DE", "COUPLER"]
        director_count = element_count - 2

    elif mode == "yagi":
        roles = ["REF", "DE"]
        director_count = element_count - 2

    else:
        raise ValueError("mode must be 'hybrid' or 'yagi'")

    for i in range(1, director_count + 1):
        roles.append(f"DIR{i}")

    return roles

def director_length_27mhz(index):
    """
    Starting director lengths at 27 MHz.

    Uses known first three from the 7-element champion, then tapers additional
    directors progressively shorter.
    """

    if index == 1:
        return BASE_LENGTHS_27MHZ["DIR1"]
    if index == 2:
        return BASE_LENGTHS_27MHZ["DIR2"]
    if index == 3:
        return BASE_LENGTHS_27MHZ["DIR3"]

    # Extra directors progressively shorter.
    return max(150.0, BASE_LENGTHS_27MHZ["DIR3"] - 4.5 * (index - 3))


def length_for_role_27mhz(role):
    if role in BASE_LENGTHS_27MHZ:
        return BASE_LENGTHS_27MHZ[role]

    if role.startswith("DIR"):
        idx = int(role.replace("DIR", ""))
        return director_length_27mhz(idx)

    raise ValueError(f"Unknown role {role}")


def generate_starting_model(
    element_count,
    mode,
    freq_start_mhz,
    freq_stop_mhz,
    boom_length_ft,
):
    """
    Generate a mechanically valid starting model.

    This is not yet optimized. It is the seed geometry for the future generalized tuner.
    """

    roles = roles_for(element_count, mode)
    sf = scale_factor(freq_start_mhz, freq_stop_mhz)

    boom_in = boom_length_ft * 12.0

    elements = []

    # Use a small rear margin for REF = 0.
    ref_pos = 0.0

    if mode == "hybrid":
        # Use proven 7el champion cell ratios as initial seed, scaled by frequency.
        # For other boom lengths, keep cell near rear and spread directors to the remaining boom.
        de_pos = 48.0 * sf
        xsp = 8.5 * sf
        csp = 36.0 * sf

        fixed_positions = {
            "REF": ref_pos,
            "XFRMR": de_pos - xsp,
            "DE": de_pos,
            "COUPLER": de_pos + csp,
        }

        director_roles = [r for r in roles if r.startswith("DIR")]
        director_count = len(director_roles)

        # Start directors after coupler. Leave a little end margin.
        first_dir_min = fixed_positions["COUPLER"] + max(36.0 * sf, 24.0)
        last_dir_pos = boom_in - 25.0

        if director_count == 1:
            dir_positions = [min(max(first_dir_min, boom_in * 0.45), last_dir_pos)]
        else:
            usable = max(12.0, last_dir_pos - first_dir_min)
            step = usable / (director_count - 1)
            dir_positions = [first_dir_min + step * i for i in range(director_count)]

        for role in roles:
            if role in fixed_positions:
                pos = fixed_positions[role]
            elif role.startswith("DIR"):
                idx = int(role.replace("DIR", "")) - 1
                pos = dir_positions[idx]
            else:
                raise ValueError(f"Unknown role {role}")

            length = length_for_role_27mhz(role) * sf

            elements.append(
                GeneratedElement(
                    name=role,
                    role=role,
                    position_in=pos,
                    length_in=length,
                )
            )

    else:
        # Standard Yagi seed.
        # REF at 0, DE around 0.18 wavelength/boom region, directors spread forward.
        de_pos = min(0.20 * boom_in, 70.0 * sf)

        fixed_positions = {
            "REF": ref_pos,
            "DE": de_pos,
        }

        director_roles = [r for r in roles if r.startswith("DIR")]
        director_count = len(director_roles)

        first_dir_min = de_pos + max(45.0 * sf, 24.0)
        last_dir_pos = boom_in - 25.0

        if director_count == 1:
            dir_positions = [min(max(first_dir_min, boom_in * 0.55), last_dir_pos)]
        else:
            usable = max(12.0, last_dir_pos - first_dir_min)
            step = usable / (director_count - 1)
            dir_positions = [first_dir_min + step * i for i in range(director_count)]

        for role in roles:
            if role in fixed_positions:
                pos = fixed_positions[role]
            elif role.startswith("DIR"):
                idx = int(role.replace("DIR", "")) - 1
                pos = dir_positions[idx]
            else:
                raise ValueError(f"Unknown role {role}")

            length = length_for_role_27mhz(role) * sf

            # Standard yagi DE should start near regular DE length.
            if role == "DE":
                length = 203.0 * sf

            elements.append(
                GeneratedElement(
                    name=role,
                    role=role,
                    position_in=pos,
                    length_in=length,
                )
            )

    # Sort by boom position.
    elements.sort(key=lambda e: e.position_in)

    # Validate fit.
    for e in elements:
        if e.position_in < -1e-9:
            raise ValueError(f"{e.name} is behind REF: {e.position_in}")
        if e.position_in > boom_in + 1e-9:
            raise ValueError(f"{e.name} exceeds boom: {e.position_in} > {boom_in}")

    return elements


def print_roles(element_count, mode):
    roles = roles_for(element_count, mode)

    print()
    print(f"Roles for {element_count}-element {mode} antenna")
    print("=" * (20 + len(str(element_count)) + len(mode)))
    print(f"User element count:     {element_count}")
    print(f"Physical element count: {len(roles)}")
    print()

    for i, role in enumerate(roles, start=1):
        print(f"{i:2d}: {role}")


def print_generated_model(elements, boom_length_ft):
    boom_in = boom_length_ft * 12.0

    print()
    print("Generated starting model")
    print("========================")
    print(f"Boom length: {boom_length_ft:.3f} ft / {boom_in:.3f} in")
    print()
    print("Element    Position from REF in   Spacing in   Length in   Half length in")
    print("-------    --------------------   ----------   ---------   --------------")

    prev = None
    for e in elements:
        spacing = 0.0 if prev is None else e.position_in - prev
        print(
            f"{e.name:<8s} "
            f"{e.position_in:20.3f} "
            f"{spacing:12.3f} "
            f"{e.length_in:11.3f} "
            f"{e.length_in / 2.0:16.3f}"
        )
        prev = e.position_in
