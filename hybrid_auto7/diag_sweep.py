#!/usr/bin/env python3
"""Diagnostic: sweep current geometry across an arbitrary band and print SWR curve."""
import sys, json, argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from hyagi import v2_runner

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--low", type=float, default=26.665)
    ap.add_argument("--high", type=float, default=27.855)
    ap.add_argument("--points", type=int, default=25)
    ap.add_argument("--height-ft", type=float, default=30.0)
    ap.add_argument("--geo", default="current_geometry_v2.json")
    args = ap.parse_args()

    geo = json.loads((ROOT / "data" / args.geo).read_text())
    els = geo["elements"]
    freqs = [args.low + i*(args.high-args.low)/(args.points-1) for i in range(args.points)]
    nec = v2_runner.build_nec_card(els, freqs, height_ft=args.height_ft)
    import subprocess, tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as fh:
        fh.write(nec); p = fh.name
    o = p.replace(".nec", ".out")
    subprocess.run(["nec2c","-i",p,"-o",o], capture_output=True, text=True, timeout=120)
    text = Path(o).read_text()
    imps, _blocks = v2_runner.parse_nec_output(text)
    pat = [pt for blk in _blocks for pt in blk]
    os.unlink(p); os.unlink(o)
    print(f"{'freq':>9} {'R':>8} {'X':>8} {'SWR':>7}")
    worst = (0,0); maxs=0
    for i,(R,X) in enumerate(imps):
        f = freqs[i]
        s = v2_runner.swr(R,X)
        flag = "  <-- band edge" if (i==0 or i==len(imps)-1) else ""
        print(f"{f:9.3f} {R:8.2f} {X:8.2f} {s:7.3f}{flag}")
        if s>maxs: maxs=s; worst=(f,s)
    print(f"\nMAX SWR {maxs:.3f} at {worst[0]:.3f} MHz across {args.low}-{args.high}")
    print(f"gain peak (over real ground) {max(t[2] for t in pat):.2f} dBi" if pat else "no pattern")

    # --- Gain realism check: free-space vs over-ground (Issue 2) ---
    # Over real ground the forward lobe gets up to ~6 dB of ground-reflection
    # gain on top of the free-space figure, so ~14-15 dBi over ground for an
    # 8-element array (free-space ~12-13 dBi) is physically realistic.
    fc = 0.5 * (args.low + args.high)
    nec_g = v2_runner.build_nec_card(els, [fc], height_ft=args.height_ft, pattern=True)
    nec_fs = nec_g.replace("GN 2 0 0 0 13.0 0.005\n", "")  # drop ground card
    def _peak_gain(card):
        with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as fh:
            fh.write(card); pp = fh.name
        oo = pp.replace(".nec", ".out")
        subprocess.run(["nec2c","-i",pp,"-o",oo], capture_output=True, text=True, timeout=120)
        t = Path(oo).read_text(); os.unlink(pp); os.unlink(oo)
        _i, _b = v2_runner.parse_nec_output(t)
        p2 = [pt for blk in _b for pt in blk]
        return max((x[2] for x in p2), default=float("nan"))
    g_fs = _peak_gain(nec_fs)
    g_gnd = _peak_gain(nec_g)
    print(f"\n[gain realism @ {fc:.3f} MHz]  free-space {g_fs:.2f} dBi  |  "
          f"over real ground {g_gnd:.2f} dBi  |  ground delta {g_gnd-g_fs:+.2f} dB "
          f"(<= ~6 dB expected)")

if __name__ == "__main__":
    main()
