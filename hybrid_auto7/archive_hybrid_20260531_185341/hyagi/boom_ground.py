"""Boom grounding v2: handles tapered wire models.
Groups wires by Y-position (boom location) to identify each element,
splits the CENTER wire of grounded elements at x=0, then inserts a
boom wire connecting those split points.
"""
from collections import defaultdict
from hyagi.cell_rules import get_rules

def apply_boom_ground(nec_text, elements):
    r = get_rules()
    if not r.get("BOOM_GROUNDED"): return nec_text
    gset = {n.upper() for n in (r.get("BOOM_GROUND_NAMES") or [])}
    if not gset: return nec_text
    boom_rad_in = float(r.get("BOOM_RADIUS_IN", 0.08))
    in_to_m = 0.0254
    boom_rad_m = boom_rad_in * in_to_m

    lines = nec_text.splitlines()
    gws = []
    for i, ln in enumerate(lines):
        p = ln.split()
        if p[:1] != ["GW"]: continue
        try:
            gws.append({"line_i": i, "tag": int(p[1]), "nseg": int(p[2]),
                "x1": float(p[3]), "y1": float(p[4]), "z1": float(p[5]),
                "x2": float(p[6]), "y2": float(p[7]), "z2": float(p[8]),
                "rad": float(p[9])})
        except Exception: pass
    if not gws:
        return nec_text + "\nCM [boom] no GW lines found\n"

    # Group wires by Y position (boom direction)
    by_y = defaultdict(list)
    for g in gws:
        by_y[round(g["y1"], 4)].append(g)

    # Map each grounded element to its Y group + center wire
    grounded_info = []
    for el in elements:
        if el.name.upper() not in gset: continue
        y_target = el.position_in * in_to_m
        # find closest Y key (tolerance 0.05 m = ~2")
        if not by_y: continue
        closest = min(by_y.keys(), key=lambda y: abs(y - y_target))
        if abs(closest - y_target) > 0.05: continue
        # center wire = the one straddling x=0
        center = None
        for g in by_y[closest]:
            if min(g["x1"], g["x2"]) <= 0.0 <= max(g["x1"], g["x2"]):
                center = g; break
        if center: grounded_info.append((el, closest, center))

    if len(grounded_info) < 2:
        return nec_text + f"\nCM [boom] need >=2 grounded ({len(grounded_info)} found)\n"
    grounded_info.sort(key=lambda t: t[1])

    max_tag = max(g["tag"] for g in gws)
    new_lines = list(lines)

    # Split each grounded center wire at x=0 -> two halves sharing (0, y, z)
    for el, y, cw in grounded_info:
        nseg_half = max(3, cw["nseg"] // 2)
        left  = (f"GW {cw['tag']} {nseg_half} "
                 f"{cw['x1']:.6f} {cw['y1']:.6f} {cw['z1']:.6f} "
                 f"0.000000 {cw['y1']:.6f} {cw['z1']:.6f} {cw['rad']:.6f}")
        max_tag += 1
        right = (f"GW {max_tag} {nseg_half} "
                 f"0.000000 {cw['y1']:.6f} {cw['z1']:.6f} "
                 f"{cw['x2']:.6f} {cw['y2']:.6f} {cw['z2']:.6f} {cw['rad']:.6f}")
        new_lines[cw["line_i"]] = left + "\n" + right

    # Boom segments between consecutive grounded element Y positions
    z_h = grounded_info[0][2]["z1"]
    inserts = []
    for i in range(len(grounded_info) - 1):
        ya = grounded_info[i][1]
        yb = grounded_info[i+1][1]
        max_tag += 1
        inserts.append(f"GW {max_tag} 5 0.000000 {ya:.6f} {z_h:.6f} "
                       f"0.000000 {yb:.6f} {z_h:.6f} {boom_rad_m:.6f}")

    # Insert just before GE/GS/EK/FR/GN/EX
    pos = len(new_lines)
    for i, ln in enumerate(new_lines):
        if ln.strip().upper().startswith(("GE","GS","EK","FR","GN","EX")):
            pos = i; break
    new_lines = new_lines[:pos] + inserts + new_lines[pos:]
    return "\n".join(new_lines) + "\n"
