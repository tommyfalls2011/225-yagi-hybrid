"""Rich end-of-run report for hybrid_auto7 learn_smart runs.

Reads data/smart_kb.json, picks the best design by score, prints:
  - per-element pos/length (inches + ft-in to 1/16")
  - ΔDE spacing
  - director-to-director spacings
  - boom span used + utilization vs Phase-1 seed
  - score, gain, F/B, SWR (max & avg), boom_ft, n_directors, label, saved_at
"""
import json, pathlib
from math import gcd

def _ft_in(inches):
    sign = "-" if inches < 0 else ""
    inches = abs(inches)
    ft = int(inches // 12)
    rem_in = inches - ft * 12
    sixteenths = round(rem_in * 16)
    if sixteenths == 192:
        ft += 1
        sixteenths = 0
    whole = sixteenths // 16
    frac  = sixteenths % 16
    if frac:
        g  = gcd(frac, 16)
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
    print("=" * 78)
    print(f"RICH DESIGN REPORT   ({best.get('label','(unlabeled)')})")
    print("=" * 78)
    print(f"{'Element':<10} {'pos in':>9} {'\u0394DE in':>9} {'len in':>9}  "
          f"{'pos (ft-in)':>14}  {'len (ft-in)':>14}  {'\u0394prev':>8}")
    print("-" * 78)
    prev = None
    for nm, pos, ln in elems:
        rel = pos - de_pos
        dprev = "" if prev is None else f"{pos - prev:+.1f}"
        print(f"{nm:<10} {pos:>9.2f} {rel:>+9.2f} {ln:>9.2f}  "
              f"{_ft_in(pos):>14}  {_ft_in(ln):>14}  {dprev:>8}")
        prev = pos

    print()
    print(f"Boom span used:        {boom_span:7.2f} in  ({_ft_in(boom_span)})")
    boom_ft_stored = best.get("boom_ft")
    if boom_ft_stored is not None:
        print(f"Boom (stored):         {float(boom_ft_stored):7.2f} ft")
    if seed_boom_in:
        util  = boom_span / seed_boom_in * 100.0
        delta = boom_span - seed_boom_in
        print(f"Phase-1 seed boom:     {seed_boom_in:7.2f} in  ({_ft_in(seed_boom_in)})")
        print(f"Phase-2 vs seed:       {util:6.1f}%  ({delta:+.1f} in beyond seed)")

    fcs = best.get("final_coupler_spacing")
    fxs = best.get("final_xfrmr_spacing")
    fdp = best.get("final_de_pos")
    print()
    print("Final tuned spacings (Phase 2 winners):")
    if fdp is not None: print(f"  DE position:         {float(fdp):6.2f} in  ({_ft_in(float(fdp))})")
    if fxs is not None: print(f"  XFRMR \u2194 DE:          {float(fxs):6.2f} in  ({_ft_in(float(fxs))})  [operator sweet: 5.5-6.5\"]")
    if fcs is not None: print(f"  DE \u2194 COUPLER:        {float(fcs):6.2f} in  ({_ft_in(float(fcs))})  [operator sweet: 12-23\"]")

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
    print(f"  score:           {_g('score', fmt='{:+.1f}')}")
    print(f"  gain:            {_g('gain_dbi', 'gain')} dBi")
    print(f"  F/B:             {_g('fb_db', 'fb', 'f_b', 'FB')} dB")
    print(f"  max SWR:         {_g('max_swr', 'maxSWR', 'swr_max')}")
    print(f"  avg SWR:         {_g('avg_swr', 'swr_avg')}")
    print(f"  n directors:     {_g('n_directors', fmt='{:.0f}')}")
    src_cell = best.get("from_cell_de_pos", best.get("from_cell"))
    if src_cell is not None:
        print(f"  seed cell:       DE pos = {src_cell} in  ({_ft_in(float(src_cell))})")
    saved = best.get("saved_at")
    if saved:
        print(f"  saved_at:        {saved}")
    print("=" * 78)
    print("NOTE: F/S, R, X, and bandwidth (MHz) are not stored in KB.")
    print("      Re-run a single-point eval via hyagi.engine to get those metrics.")
    print("=" * 78)
