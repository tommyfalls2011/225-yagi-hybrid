"""Fast antenna report from nec2c .out -- ft/in fractions + all RF metrics."""
import json, subprocess, re, shutil, tempfile, math
from fractions import Fraction
from pathlib import Path

def in_to_ftin(inches):
    sign = "-" if inches < 0 else ""
    inches = abs(inches)
    ft = int(inches // 12); rem = inches - ft*12
    whole = int(rem); sixteenths = round((rem - whole)*16)
    if sixteenths == 16: whole += 1; sixteenths = 0
    if sixteenths == 0:
        inpart = f'{whole}"'
    else:
        f = Fraction(sixteenths, 16)
        inpart = f'{whole}-{f.numerator}/{f.denominator}"' if whole else f'{f.numerator}/{f.denominator}"'
    return f"{sign}{ft}' {inpart}" if ft else f"{sign}{inpart}"

def parse_nec_out(out_text):
    """nec2c format:
       FREQUENCY : 2.6965E+01 MHz
       ANTENNA INPUT PARAMETERS ... col6=Z_real col7=Z_imag
       RADIATION PATTERNS table: theta phi vertc horiz total ...
    """
    freqs, zs = [], []
    cur_freq = None
    pat = []
    in_pat = False; pat_freq = None
    pat_by_freq = {}

    for ln in out_text.splitlines():
        # frequency
        m = re.search(r'FREQUENCY\s*:\s*([\d.E+-]+)\s*MHz', ln)
        if m:
            try:
                cur_freq = float(m.group(1))
                pat_freq = cur_freq
                in_pat = False
            except: pass
            continue

        # antenna input parameters row (TAG SEG V V I I R X G B P)
        # match: starts with whitespace + digits + digits + 9 scientific numbers
        m = re.match(r'\s+\d+\s+\d+'+r'(\s+[+-]?\d+\.\d+E[+-]\d+){9}\s*$', ln)
        if m and cur_freq is not None:
            p = ln.split()
            try:
                R = float(p[6]); X = float(p[7])
                freqs.append(cur_freq); zs.append((R, X))
            except: pass
            continue

        # radiation pattern block
        if "RADIATION PATTERNS" in ln:
            in_pat = True
            pat_by_freq.setdefault(pat_freq, [])
            continue

        if in_pat:
            # data lines: theta phi vertc horiz total ...
            p = ln.split()
            if len(p) >= 5:
                try:
                    th = float(p[0]); ph = float(p[1]); tot = float(p[4])
                    if -90 <= th <= 180 and 0 <= ph <= 361 and -200 < tot < 100:
                        pat_by_freq[pat_freq].append((th, ph, tot))
                except: pass

    # use pattern from center-most freq
    if pat_by_freq:
        center = 27.205
        best_f = min(pat_by_freq.keys(), key=lambda f: abs((f or 0)-center) if f else 999)
        pat = pat_by_freq[best_f]
    return {"freqs": freqs, "zs": zs, "pat": pat}

def swr50(r, x):
    z0 = 50.0
    num = ((r-z0)**2 + x**2)**0.5
    den = ((r+z0)**2 + x**2)**0.5
    rho = num/den
    return (1+rho)/(1-rho) if rho < 1 else 99.0

def compute(parsed, center_mhz=27.205):
    o = {}
    freqs, zs, pat = parsed["freqs"], parsed["zs"], parsed["pat"]
    if not freqs or not zs: return o
    n = min(len(freqs), len(zs)); freqs=freqs[:n]; zs=zs[:n]
    ci = min(range(n), key=lambda i: abs(freqs[i]-center_mhz))
    R, X = zs[ci]
    o["freq_mhz"]=freqs[ci]; o["R"]=R; o["X"]=X
    swrs = [swr50(r,x) for r,x in zs]
    o["SWR_c"]=swr50(R,X); o["SWR_min"]=min(swrs); o["SWR_max"]=max(swrs); o["SWR_avg"]=sum(swrs)/len(swrs)
    rho = abs(complex(R-50, X)/complex(R+50, X))
    o["RL"] = -20*math.log10(rho) if rho>0 else 99.0
    bw = [f for f,s in zip(freqs,swrs) if s<2.0]
    if bw: o["BW_lo"]=min(bw); o["BW_hi"]=max(bw); o["BW"]=max(bw)-min(bw)
    bw15 = [f for f,s in zip(freqs,swrs) if s<1.5]
    if bw15: o["BW15"]=max(bw15)-min(bw15)
    if pat:
        fwd = max(pat, key=lambda t: t[2])
        o["gain"]=fwd[2]; o["fwd_th"]=fwd[0]; o["fwd_ph"]=fwd[1]
        o["elev"]=90-fwd[0]
        rear_ph = (fwd[1]+180) % 360
        rear = [p for p in pat if abs(p[0]-fwd[0])<6 and abs(p[1]-rear_ph)<11]
        if rear:
            r = max(rear, key=lambda t: t[2])
            o["rear"]=r[2]; o["FB"]=fwd[2]-r[2]
        side_l=(fwd[1]-90)%360; side_r=(fwd[1]+90)%360
        sides=[p for p in pat if abs(p[0]-fwd[0])<6 and (abs(p[1]-side_l)<11 or abs(p[1]-side_r)<11)]
        if sides:
            s=max(sides, key=lambda t:t[2]); o["FS"]=fwd[2]-s[2]
        cutoff=fwd[2]-3.0
        e=[p for p in pat if abs(p[1]-fwd[1])<3 and p[2]>=cutoff]
        if len(e)>=2: o["E_bw"]=max(p[0] for p in e)-min(p[0] for p in e)
        h=[p for p in pat if abs(p[0]-fwd[0])<3 and p[2]>=cutoff]
        if len(h)>=2: o["H_bw"]=max(p[1] for p in h)-min(p[1] for p in h)
        # rough efficiency vs isotropic+directivity (dipole ref 2.15)
        # NEC2 default gain is in dBi already; "efficiency" needs RP NORM=1.
        # Skip for now; surface raw gain.
    return o


def extract_elements_from_nec(nec_path):
    """Parse GW cards, auto-detect boom axis (X or Y)."""
    import re
    from collections import defaultdict
    lines = Path(nec_path).read_text().splitlines()
    names = []
    for ln in lines:
        m = re.match(r"^CM\s+(\w[\w\-]+)", ln)
        if m and "-" in m.group(1):
            names = m.group(1).split("-"); break
    wires = []
    for ln in lines:
        p = ln.split()
        if p[:1]==["GW"] and len(p)>=10:
            try: wires.append((float(p[3]),float(p[4]),float(p[6]),float(p[7])))
            except: pass
    if not wires: return []
    M_TO_IN = 1/0.0254
    span_x = sum(abs(w[2]-w[0]) for w in wires)/len(wires)
    span_y = sum(abs(w[3]-w[1]) for w in wires)/len(wires)
    boom_along_y = span_x > span_y
    by_boom = defaultdict(list)
    for x1,y1,x2,y2 in wires:
        if boom_along_y: bp = round(y1,4); a,b = x1,x2
        else:            bp = round(x1,4); a,b = y1,y2
        by_boom[bp].append((a,b))
    bps = sorted(by_boom.keys())
    class E: pass
    els = []
    for i, bp in enumerate(bps):
        ws = by_boom[bp]
        emin = min(min(w) for w in ws); emax = max(max(w) for w in ws)
        e = E()
        e.name = names[i] if i<len(names) else f"EL{i+1}"
        e.position_in = bp * M_TO_IN
        e.length_in = (emax-emin) * M_TO_IN
        els.append(e)
    return els

def report_for_nec(nec_path, elements=None, title="Tune Result", center_mhz=27.205):
    if elements is None:
        try: elements = extract_elements_from_nec(nec_path)
        except Exception: elements = None
    nec_path = Path(nec_path)
    tmp_nec = Path(tempfile.gettempdir())/"r_report.nec"
    tmp_out = Path(tempfile.gettempdir())/"r_report.out"
    shutil.copy(nec_path, tmp_nec)
    if not tmp_out.exists() or tmp_out.stat().st_mtime < tmp_nec.stat().st_mtime:
        subprocess.run(["nec2c","-i",str(tmp_nec),"-o",str(tmp_out)],
                       capture_output=True, timeout=120)
    if not tmp_out.exists():
        return f"[report] no .out produced"
    out_text = tmp_out.read_text(errors="ignore")
    print(f"[report] parsing {len(out_text):,} chars...")
    parsed = parse_nec_out(out_text)
    print(f"[report] found {len(parsed['freqs'])} freqs, {len(parsed['pat'])} pattern points")
    m = compute(parsed, center_mhz)

    L=[]
    L.append("="*72); L.append(f"  {title}"); L.append("="*72)
    if elements:
        L.append(""); L.append("  DIMENSIONS"); L.append("  "+"-"*60)
        L.append(f"  {'Element':<10}{'Position':<22}{'Length':<22}")
        for el in elements:
            L.append(f"  {el.name:<10}{in_to_ftin(el.position_in):<22}{in_to_ftin(el.length_in):<22}")
    L.append(""); L.append("  IMPEDANCE / MATCH"); L.append("  "+"-"*60)
    if "R" in m:
        L.append(f"  Center freq      : {m['freq_mhz']:.3f} MHz")
        L.append(f"  R (resistance)   : {m['R']:.2f} ohm")
        L.append(f"  X (reactance)    : {m['X']:+.2f} ohm")
        L.append(f"  SWR center       : {m['SWR_c']:.3f}")
        L.append(f"  SWR min/avg/max  : {m['SWR_min']:.3f} / {m['SWR_avg']:.3f} / {m['SWR_max']:.3f}")
        L.append(f"  Return loss      : {m['RL']:.2f} dB")
    L.append(""); L.append("  BANDWIDTH"); L.append("  "+"-"*60)
    if "BW" in m:
        L.append(f"  SWR<2.0          : {m['BW_lo']:.3f}-{m['BW_hi']:.3f} MHz ({m['BW']*1000:.0f} kHz wide)")
    if "BW15" in m:
        L.append(f"  SWR<1.5          : {m['BW15']*1000:.0f} kHz wide")
    L.append(""); L.append("  PATTERN / GAIN"); L.append("  "+"-"*60)
    if "gain" in m:
        L.append(f"  Max gain         : {m['gain']:.2f} dBi")
        L.append(f"  Peak elevation   : {m['elev']:.1f} deg above horizon")
        L.append(f"  Peak azimuth     : {m['fwd_ph']:.1f} deg")
        if "FB" in m: L.append(f"  F/B ratio        : {m['FB']:.2f} dB  (rear gain {m['rear']:+.2f} dBi)")
        if "FS" in m: L.append(f"  F/S ratio        : {m['FS']:.2f} dB")
        if "E_bw" in m: L.append(f"  E-plane -3dB BW  : {m['E_bw']:.1f} deg")
        if "H_bw" in m: L.append(f"  H-plane -3dB BW  : {m['H_bw']:.1f} deg")
    else:
        L.append("  (no RP pattern in .out -- the .nec sweep doesn't include full pattern)")
    L.append(""); L.append(f"  Files: {nec_path}")
    L.append("="*72)
    return "\n".join(L)

if __name__ == "__main__":
    import sys
    if len(sys.argv)<2:
        print("usage: full_report.py path/to/winning.nec [center_mhz]"); sys.exit(1)
    cf = float(sys.argv[2]) if len(sys.argv)>2 else 27.205
    print(report_for_nec(sys.argv[1], title=f"Report: {Path(sys.argv[1]).name}", center_mhz=cf))
