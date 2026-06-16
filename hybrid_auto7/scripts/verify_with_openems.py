"""CLI: verify a hybrid_auto7 geometry with OpenEMS (FDTD).

Reads a current_geometry_v2.json (or any geometry JSON with the same
'elements' schema) and runs an OpenEMS FDTD simulation as a sanity check
on the nec2c-based tune.  OpenEMS is FDTD-based -- volumetric, no Method-
of-Moments assumptions -- so it's materially more accurate than nec2c for
closely-coupled hybrid driven cells.

Usage:
    python -m scripts.verify_with_openems \\
        --geometry data/current_geometry_v2.json \\
        --setup data/setup_v2.json \\
        --output /tmp/openems_run \\
        [--mesh 25]    # cells per wavelength (15-40, default 25)
        [--threads 0]  # 0 = auto

Output: prints SWR/R/X/Gain/F-B at the centre frequency and a band sweep
to stdout, writes results to <output>/openems_results.json.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hyagi import openems_export


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--geometry", required=True,
                   help="path to current_geometry_v2.json")
    p.add_argument("--setup", required=True, help="path to setup_v2.json")
    p.add_argument("--fc", type=float, default=27.195,
                   help="centre frequency MHz (default 27.195)")
    p.add_argument("--bandwidth", type=float, default=4.0,
                   help="excitation bandwidth MHz (default 4.0)")
    p.add_argument("--output", default="/tmp/openems_run",
                   help="output dir for run files")
    p.add_argument("--mesh", type=int, default=25,
                   help="mesh cells per wavelength (default 25)")
    p.add_argument("--threads", type=int, default=0,
                   help="OpenEMS thread count (0 = auto)")
    p.add_argument("--ground", default="real",
                   choices=("real", "pec", "none"),
                   help="ground type (default real soil)")
    args = p.parse_args()

    geo = json.loads(pathlib.Path(args.geometry).read_text())
    setup = json.loads(pathlib.Path(args.setup).read_text())

    elements = geo["elements"]
    height_ft = float(setup.get("height_ft", 30.0))
    print(f"Verifying: {len(elements)} elements, fc={args.fc} MHz, "
          f"height={height_ft} ft, mesh λ/{args.mesh}, "
          f"ground={args.ground}")
    print("Elements:")
    for e in elements:
        print(f"  {e['name']:<8} pos={float(e['position_in']):7.2f}\"  "
              f"len={float(e['length_in']):7.2f}\"")

    sim = openems_export.build_simulation(
        elements,
        height_ft=height_ft, fc_mhz=args.fc, bandwidth_mhz=args.bandwidth,
        output_dir=args.output, mesh_per_wavelength=args.mesh,
        ground_type=args.ground, numthreads=args.threads,
    )
    print(f"FDTD model built.  Sim dir: {args.output}")
    print("Running... (5-30 minutes depending on mesh + threads)")
    results = openems_export.run_simulation(sim)

    # Summary
    print("\n=== OpenEMS RESULTS ===")
    print(f"Centre R:    {results['R_centre']:.2f} Ω")
    print(f"Centre X:    {results['X_centre']:+.2f} Ω")
    print(f"Centre SWR:  {results['centre_swr']:.3f}:1")
    print(f"Gain:        {results['gain_dbi']:.2f} dBi")
    print(f"F/B:         {results['fb_db']:.2f} dB")
    print(f"Peak elev:   {results['peak_elev_deg']:.2f}°")

    # Save full
    out_json = pathlib.Path(args.output) / "openems_results.json"
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\nFull results written to {out_json}")


if __name__ == "__main__":
    main()
