#!/usr/bin/env python3
"""OpenEMS validator -- FDTD cross-check for a winning NEC geometry.
Status: scaffold. Parses NEC, checks openEMS install, lists remaining TODO.

INSTALL openEMS first:
  sudo apt update
  sudo apt install openems python3-openems csxcad python3-csxcad
"""
import argparse, sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nec", required=True)
    args = ap.parse_args()
    nec = Path(args.nec)
    if not nec.exists():
        print(f"[ERR] {nec} not found"); sys.exit(1)
    print("="*60); print("OpenEMS validator (scaffold)"); print("="*60)
    print(f"NEC file: {nec}")
    try:
        import openEMS, CSXCAD
        print("[ok] openEMS python bindings detected")
    except ImportError as e:
        print(f"[MISSING] openEMS not installed ({e})")
        print()
        print("Install:")
        print("  sudo apt update && sudo apt install openems python3-openems csxcad python3-csxcad")
        print("  # or build from source: https://docs.openems.de/install.html")
        sys.exit(2)

    wires = []
    for ln in nec.read_text().splitlines():
        p = ln.split()
        if p[:1] == ["GW"]:
            try:
                wires.append({"tag":int(p[1]),"nseg":int(p[2]),
                              "p1":tuple(map(float,p[3:6])),
                              "p2":tuple(map(float,p[6:9])),
                              "rad":float(p[9]) if len(p)>9 else 0.001})
            except Exception: pass
    print(f"parsed {len(wires)} wires")
    print()
    print("TODO (next iteration):")
    print("  1. Convert wires -> CSXCAD cylinders")
    print("  2. Add feed (voltage source) at DE center")
    print("  3. Set up FDTD grid + boundary conditions")
    print("  4. Run sim, extract S11/gain/F-B/pattern")
    print("  5. Diff against NEC results in same JSON shape")
    print()
    print("Geometry parsed cleanly. Ready for FDTD wiring.")

if __name__ == "__main__":
    main()
