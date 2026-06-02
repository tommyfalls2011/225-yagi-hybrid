"""Rich end-of-run report for hybrid_auto7 learn_smart runs.

Reads data/smart_kb.json, picks the best design by score, prints:
  - per-element pos / length in inches AND ft-in (nearest 1/16")
  - relative-to-DE spacing
  - director-to-director spacings
  - total boom span used + utilization vs Phase-1 seed
  - score, gain, F/B, F/S, max SWR, BW, R, X (whatever the KB stored)
"""
import json, pathlib
from math import gcd

def _ft_in(inches):
    sign = "-" if inches < 0 else ""
    inches = abs(inches)
    ft = int(inches // 12)
    rem_in = inches - ft * 12
    sixteenths = round(rem_in * 16)
    if sixteenths == 16:
        ft += 1 if False else 0  # keep simple; rounding pushes into next ft only when whole_in == 12
    whole = sixteenths // 16
    frac = sixteenths % 16
    if frac:
        g = gcd(frac, 16)
        fs = f" {frac//g}/{16//g}"
    else:
        fs = ""
    if ft:
        return f"{sign}{ft}\u2032 {whole}{fs}\u2033"
    return f"{sign}{whole}{fs}\u2033"

def _norm_elements(elems):
    out = []
    for e in elems:
        if isinstance(e, dict):
            nm  = e.get("name", "?")
            pos = float(e.get("position_in", e.get("pos", 0.0)))
            ln  = float(e.get("length_in",   e.get("len", 0.0)))
        elif isinstance(e, (list, tuple)) and len(e) >= 3:
            nm, pos, ln = e[0], float(e[1]), float(e[2])
        else:
            continue
        out.append((nm, pos, ln))
    return out

def _pick_best(kb):
    designs = kb.get("best_full_designs") or []
    if not designs:
        return None
    return max(designs, key=lambda d: d.get("score", -1e18))

def print_rich_report(kb_path=None, seed_boom_in=216.0):
    if kb_path is None:
        kb_path = pathlib.Path.home() / "scripts/hybrid_auto7/data/smart_kb.json"
    p = pathlib.Path(kb_path)
    if not p.exists():
        print(f"[rich_report] no KB at {p}")
        return
    try:
        kb = json.loads(p.read_text())
    except Exception as e:
        print(f"[rich_report] cannot parse KB: {e}")
        return
    best = _pick_best(kb)
    if not best:
        print("[rich_report] no designs in KB yet")
        return

    raw = best.get("elements") or best.get("design") or best.get("elems") or []
    elems = _norm_elements(raw)
    if not elems:
        print("[rich_report] design has no element list")
        return

    positions = [pos for _, pos, _ in elems]
    boom_span = max(positions) - min(positions)
    de_pos = next((pos for nm, pos, _ in elems if nm.upper() == "DE"), positions[0])

    print()
    print("=" * 76)
    print("RICH DESIGN REPORT")
    print("=" * 76)
    print(f"{'Element':<10} {'pos in':>9} {'\u0394DE in':>9} {'len in':>9}  "
          f"{'pos (ft-in)':>14}  {'len (ft-in)':>14}  {'\u0394prev':>8}")
    print("-" * 76)
    prev = None
    for nm, pos, ln in elems:
        rel = pos - de_pos
        dprev = "" if prev is None else f"{pos - prev:+.1f}"
        print(f"{nm:<10} {pos:>9.2f} {rel:>+9.2f} {ln:>9.2f}  "
              f"{_ft_in(pos):>14}  {_ft_in(ln):>14}  {dprev:>8}")
        prev = pos

    print()
    print(f"Boom span actually used: {boom_span:.2f} in  ({_ft_in(boom_span)})")
    if seed_boom_in:
        util = boom_span / seed_boom_in * 100.0
        delta = boom_span - seed_boom_in
        print(f"Phase-1 seed boom:       {seed_boom_in:.2f} in  ({_ft_in(seed_boom_in)})")
        print(f"Phase-2 used vs seed:    {util:.1f}%  ({delta:+.1f} in beyond seed)")

    dirs = [(nm, pos) for nm, pos, _ in elems if nm.upper().startswith("DIR")]
    if len(dirs) >= 2:
        print()
        print("Director-to-director spacings:")
        for i in range(1, len(dirs)):
            d = dirs[i][1] - dirs[i-1][1]
            print(f"  {dirs[i-1][0]:<6} \u2192 {dirs[i][0]:<6}  {d:6.2f} in  ({_ft_in(d)})")

    def _g(*keys, fmt="{:.3f}"):
        for k in keys:
            if k in best and best[k] is not None:
                v = best[k]
                try:
                    return fmt.format(float(v))
                except Exception:
                    return str(v)
        return "n/a"

    print()
    print("Performance:")
    print(f"  score:     {_g('score', fmt='{:+.1f}')}")
    print(f"  gain:      {_g('gain', 'gain_dbi')} dBi")
    print(f"  F/B:       {_g('fb', 'f_b', 'FB')} dB")
    print(f"  F/S:       {_g('fs', 'f_s', 'FS')} dB")
    print(f"  max SWR:   {_g('max_swr', 'maxSWR', 'swr_max')}")
    print(f"  R:         {_g('r_ohm', 'R')} \u03a9")
    print(f"  X:         {_g('x_ohm', 'X')} \u03a9")
    print(f"  BW (2:1):  {_g('bandwidth_mhz', 'bw_mhz')} MHz")
    src_cell = best.get("from_cell")
    if src_cell:
        print(f"  from cell: DE pos = {src_cell}")
    print("=" * 76)
