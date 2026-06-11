"""hybrid_auto7 v2 runner — executes procedures against current geometry.
Calls nec2c directly. Validates geometry against rules. Scores with v2_scorer.
"""
# ruff: noqa: E701, E702  (legacy one-line style throughout this module)
import subprocess
import math
import re
import pathlib
import copy
import tempfile
import os
import json
from . import v2_scorer

INCH = 0.0254
FT   = 0.3048

# --- Tapered aluminum element model -----------------------------------------
# Real elements are telescoping aluminum tubing: a fat tube at the centre
# stepping down to a thin tip.  (OD inches, section length inches), centre ->
# tip, per half element.  At a given element length the schedule is consumed
# centre-outward and truncated, so a short element shows a fatter tip and a
# long one exposes the thin tip tubing -- exactly how sliding the tip in and
# out of the bigger tubes behaves on the bench.  Diameter strongly sets the
# resonant length, which is why a uniform-wire model never matched real builds.
#
# This is only the FALLBACK default.  The active schedule is read from
# data/taper_v2.json (editable in the Auto-Learn UI) so every antenna can use
# its own tubing sizes.  Use a big section length (e.g. 999) for the piece that
# runs all the way to the tip.
TAPER_SCHEDULE = [(0.625, 36.0), (0.50, 999.0)]
ALUMINUM_SIGMA = 2.5e7   # 6061-T6 conductivity, S/m (for NEC LD type-5 card)
_SEG_TARGET_IN = 6.0     # target NEC segment length
_DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# Antenna construction options (set from the Setup / Tune page before tuning so
# every solve, sweep, export and the performance report all use the same model).
#   GROUNDED          -> True: parasitic elements are bonded to a metal boom of
#                        BOOM_DIAMETER_IN (DE stays insulated/coax-fed); changes
#                        the tuning vs the default insulated build.
#   BOOM_DIAMETER_IN  -> boom outer diameter (inches), used when grounded.
GROUNDED = False
BOOM_DIAMETER_IN = 1.5


def get_active_taper():
    """Active element taper schedule from data/taper_v2.json, else the default.
    Returns a list of (OD_in, section_len_in) centre -> tip."""
    p = _DATA_DIR / "taper_v2.json"
    try:
        if p.exists():
            d = json.loads(p.read_text())
            sch = d.get("default") or d.get("schedule")
            if sch:
                return [(float(od), float(L)) for od, L in sch]
    except Exception:
        pass
    return TAPER_SCHEDULE


def taper_signature(taper=None):
    """Short stable string identifying a taper, for matching learned runs."""
    t = taper if taper not in (None, "auto") else get_active_taper()
    if not t:
        return "uniform"
    return ";".join(f"{od:g}x{L:g}" for od, L in t)



# ---------- SWR ----------
def swr(R, X, Z0=50.0):
    num = (R - Z0)**2 + X**2
    den = (R + Z0)**2 + X**2
    if den <= 0: return 99.0
    rho = math.sqrt(num / den)
    if rho >= 0.999: return 99.0
    return (1.0 + rho) / (1.0 - rho)


def _interp_rx(freqs, impedances, f):
    """Linear-interpolate (R, X) at frequency f from the swept grid so the
    'centre' metrics are read at the true operating centre, not just whichever
    sample happens to sit in the middle of the band."""
    n = min(len(freqs), len(impedances))
    if n == 0:
        return 0.0, 0.0
    if n == 1 or f <= freqs[0]:
        return impedances[0]
    if f >= freqs[n - 1]:
        return impedances[n - 1]
    for i in range(1, n):
        if f <= freqs[i]:
            f0, f1 = freqs[i - 1], freqs[i]
            (r0, x0), (r1, x1) = impedances[i - 1], impedances[i]
            t = 0.0 if f1 == f0 else (f - f0) / (f1 - f0)
            return r0 + t * (r1 - r0), x0 + t * (x1 - x0)
    return impedances[n - 1]

# ---------- NEC card builder ----------
def _half_sections(half_len_m, taper):
    """Return [(radius_m, length_m), ...] from centre to tip for one half
    element of length half_len_m, consuming the taper schedule centre-outward
    and truncating (or extending the thin tip) to hit the length."""
    secs = []
    remaining = half_len_m
    for od_in, sec_in in taper:
        if remaining <= 1e-9:
            break
        seglen = min(sec_in * INCH, remaining)
        secs.append((od_in * INCH / 2.0, seglen))
        remaining -= seglen
    if remaining > 1e-9:               # element longer than schedule -> extend tip
        secs.append((taper[-1][0] * INCH / 2.0, remaining))
    if not secs:
        secs = [(taper[0][0] * INCH / 2.0, max(half_len_m, 1e-3))]
    return secs


def _emit_element(out, tag, p, L, H, taper, split_center=False):
    """Append GW cards for one stepped-diameter element centred on the y axis at
    boom position p, height H. Returns (last_tag, feed_tag, feed_seg).

    split_center=True emits the centre section as TWO half-wires meeting at
    (p,0,H) so that point is a NODE (used to bond a grounded element to the
    boom). split_center=False (default) emits a single continuous centre wire
    with the feed at its middle segment."""
    half = L / 2.0
    secs = _half_sections(half, taper)
    r0, l0 = secs[0]
    if split_center:
        ns = max(2, int(l0 / (_SEG_TARGET_IN * INCH)))
        tag += 1
        out.append(f"GW {tag} {ns} {p:.6f} 0.000000 {H:.6f} {p:.6f} {l0:.6f} {H:.6f} {r0:.6f}")
        feed_tag, feed_seg = tag, 1
        tag += 1
        out.append(f"GW {tag} {ns} {p:.6f} 0.000000 {H:.6f} {p:.6f} {-l0:.6f} {H:.6f} {r0:.6f}")
    else:
        nseg = max(3, int((2 * l0) / (_SEG_TARGET_IN * INCH))) | 1   # odd -> centre seg
        tag += 1
        out.append(f"GW {tag} {nseg} {p:.6f} {-l0:.6f} {H:.6f} {p:.6f} {l0:.6f} {H:.6f} {r0:.6f}")
        feed_tag, feed_seg = tag, (nseg + 1) // 2
    inner = l0
    for (r, seglen) in secs[1:]:
        ns = max(2, int(seglen / (_SEG_TARGET_IN * INCH)))
        tag += 1
        out.append(f"GW {tag} {ns} {p:.6f} {inner:.6f} {H:.6f} {p:.6f} {inner+seglen:.6f} {H:.6f} {r:.6f}")
        tag += 1
        out.append(f"GW {tag} {ns} {p:.6f} {-inner:.6f} {H:.6f} {p:.6f} {-(inner+seglen):.6f} {H:.6f} {r:.6f}")
        inner += seglen
    return tag, feed_tag, feed_seg


def build_nec_card(elements, freqs_mhz, height_ft=30.0, wire_radius_in=0.25,
                   pattern=True, taper="auto", conductor_sigma=ALUMINUM_SIGMA,
                   grounded=None, boom_diameter_in=None):
    """Build a NEC2 input deck for the tapered aluminium hybrid.

    taper="auto" -> read the active schedule from data/taper_v2.json.
    taper=list   -> use that [(OD_in, len_in), ...] schedule.
    taper=None/[] -> single uniform wire of wire_radius_in (legacy).
    conductor_sigma adds an LD type-5 aluminium-loss card (None = lossless).
    pattern=True  -> full hemisphere RP (37x73) so gain / F/B can be read.
    pattern=False -> a cheap single-direction RP (~3x faster) for the SWR loop.
    """
    if taper == "auto":
        taper = get_active_taper()
    if grounded is None:
        grounded = GROUNDED
    if boom_diameter_in is None:
        boom_diameter_in = BOOM_DIAMETER_IN
    H = height_ft * FT
    out = ["CM hybrid_auto7 v2 (tapered Al)", "CE"]
    de_feed_tag = None
    de_feed_seg = None
    tag = 0
    ground_nodes = []   # boom x-positions (m) of grounded (non-DE) elements
    for el in elements:
        p = float(el["position_in"]) * INCH
        L = float(el["length_in"]) * INCH
        is_de = el["name"].upper() == "DE"
        bond = bool(grounded) and not is_de
        if taper:
            tag, ftag, fseg = _emit_element(out, tag, p, L, H, taper, split_center=bond)
        else:
            a = wire_radius_in * INCH
            if bond:
                segs = max(3, (int((L / 2) / (10.0 * INCH))))
                tag += 1
                out.append(f"GW {tag} {segs} {p:.6f} 0.000000 {H:.6f} {p:.6f} {L/2:.6f} {H:.6f} {a:.6f}")
                ftag, fseg = tag, 1
                tag += 1
                out.append(f"GW {tag} {segs} {p:.6f} 0.000000 {H:.6f} {p:.6f} {-L/2:.6f} {H:.6f} {a:.6f}")
            else:
                segs = max(11, (int(L / (10.0 * INCH))) | 1)
                tag += 1
                out.append(f"GW {tag} {segs} {p:.6f} {-L/2:.6f} {H:.6f} {p:.6f} {L/2:.6f} {H:.6f} {a:.6f}")
                ftag, fseg = tag, (segs + 1) // 2
        if bond:
            ground_nodes.append(p)
        if is_de:
            de_feed_tag, de_feed_seg = ftag, fseg
    if de_feed_tag is None:
        raise ValueError("No DE element")
    # Grounded build: bond each grounded element's centre to a common metal boom
    # (diameter boom_diameter_in) modelled just below the elements, with a short
    # vertical drop wire per element.  The DE is left insulated/coax-fed.  This
    # genuinely shifts the tuning vs the default insulated model.
    if grounded and len(ground_nodes) >= 2:
        boom_r = max(0.003, boom_diameter_in * INCH / 2.0)
        drop = boom_r + 0.03
        zb = H - drop
        for x in ground_nodes:
            tag += 1
            out.append(f"GW {tag} 1 {x:.6f} 0.000000 {H:.6f} {x:.6f} 0.000000 {zb:.6f} {boom_r:.6f}")
        xs = sorted(set(ground_nodes))
        for a, b in zip(xs, xs[1:]):
            tag += 1
            nseg = max(1, int((b - a) / (_SEG_TARGET_IN * INCH)))
            out.append(f"GW {tag} {nseg} {a:.6f} 0.000000 {zb:.6f} {b:.6f} 0.000000 {zb:.6f} {boom_r:.6f}")
    out.append("GE -1")
    if conductor_sigma:
        out.append(f"LD 5 0 0 0 {conductor_sigma:.4E}")
    out.append("GN 2 0 0 0 13.0 0.005")
    f0 = freqs_mhz[0]
    if len(freqs_mhz) == 1:
        out.append(f"FR 0 1 0 0 {f0:.4f} 0")
    else:
        step = (freqs_mhz[-1] - f0) / (len(freqs_mhz) - 1)
        out.append(f"FR 0 {len(freqs_mhz)} 0 0 {f0:.4f} {step:.4f}")
    out.append(f"EX 0 {de_feed_tag} {de_feed_seg} 0 1.0 0.0")
    if pattern:
        out.append("RP 0 37 73 1000 0 0 5 5")
    else:
        out.append("RP 0 1 1 1000 90 0 1 1")
    out.append("EN")
    return "\n".join(out) + "\n"

# ---------- NEC output parser ----------
_IMP_RE = re.compile(
    r"^\s*\d+\s+\d+\s+[+-]?[\d.E+-]+\s+[+-]?[\d.E+-]+\s+"
    r"[+-]?[\d.E+-]+\s+[+-]?[\d.E+-]+\s+"
    r"([+-]?[\d.E+-]+)\s+([+-]?[\d.E+-]+)"
)
_PAT_RE = re.compile(
    r"^\s*([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\s+"
    r"[+-]?\d+\.?\d*\s+[+-]?\d+\.?\d*\s+([+-]?\d+\.?\d*)"
)

def parse_nec_output(text):
    """Return (impedances, pattern_blocks).

    A multi-frequency deck (FR 0 N ...) prints one ANTENNA INPUT PARAMETERS
    block AND one RADIATION PATTERNS block per frequency.  impedances[i] is the
    (R, X) for frequency i; pattern_blocks[i] is that frequency's list of
    (theta, phi, gain) points.  Keeping them separated is essential: gain / F-B
    must be read from a SINGLE frequency's pattern (the design centre), never
    from a mix of all frequencies merged together."""
    impedances = []
    pattern_blocks = []
    cur_block = None
    in_imp = False
    in_pat = False
    for ln in text.splitlines():
        if "ANTENNA INPUT PARAMETERS" in ln:
            in_imp = True; in_pat = False; cur_block = None; continue
        if "RADIATION PATTERNS" in ln:
            in_imp = False; in_pat = True
            cur_block = []
            pattern_blocks.append(cur_block); continue
        if in_imp:
            m = _IMP_RE.match(ln)
            if m:
                impedances.append((float(m.group(1)), float(m.group(2))))
                in_imp = False
        elif in_pat and cur_block is not None:
            m = _PAT_RE.match(ln)
            if m:
                try:
                    cur_block.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
                except ValueError:
                    pass
    return impedances, pattern_blocks

# ---------- Fast SWR-only band evaluator (no radiation pattern) ----------
def band_swr_curve(elements, f_low, f_high, points, height_ft=30.0):
    """Fast band sweep returning (curve, max_swr, avg_swr) using a
    single-direction RP (no full pattern). ~3x faster than evaluate(); used by
    the wideband matcher's inner loop where only SWR matters."""
    points = max(2, int(points))
    freqs = [f_low + i * (f_high - f_low) / (points - 1) for i in range(points)]
    try:
        nec = build_nec_card(elements, freqs, height_ft=height_ft, pattern=False)
    except Exception:
        return [], 99.0, 99.0
    with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as f:
        f.write(nec); nec_path = f.name
    out_path = nec_path.replace(".nec", ".out")
    try:
        try:
            subprocess.run(["nec2c", "-i", nec_path, "-o", out_path],
                           capture_output=True, text=True, timeout=60)
        except Exception:
            return [], 99.0, 99.0
        if not pathlib.Path(out_path).exists():
            return [], 99.0, 99.0
        text = pathlib.Path(out_path).read_text()
    finally:
        for p in (nec_path, out_path):
            try: os.unlink(p)
            except Exception: pass
    impedances, _ = parse_nec_output(text)
    if not impedances:
        return [], 99.0, 99.0
    curve = []
    for i, (R, X) in enumerate(impedances):
        fz = freqs[i] if i < len(freqs) else freqs[-1]
        curve.append((round(fz, 4), float(R), float(X), float(swr(R, X))))
    swrs = [c[3] for c in curve]
    return curve, max(swrs), sum(swrs) / len(swrs)


# ---------- Evaluator ----------
# Number of frequency points the scorer samples across the band.  Default 5
# (legacy Run-page behaviour).  The self-learning loop raises this so the
# optimizer actually "sees" the whole band and can be driven to a wideband
# low-SWR target instead of only matching at 5 spot frequencies.
EVAL_FREQ_POINTS = 5


def evaluate(elements, rules, height_ft=30.0, n_points=None):
    glb = rules["global"]
    flow   = float(glb["freq_mhz_low"])
    fhigh  = float(glb["freq_mhz_high"])
    n = int(n_points) if n_points else int(EVAL_FREQ_POINTS)
    n = max(2, n)
    freqs = [flow + i * (fhigh - flow) / (n - 1) for i in range(n)]
    try:
        nec = build_nec_card(elements, freqs, height_ft=height_ft)
    except Exception as e:
        return {"error": f"build: {e}"}
    with tempfile.NamedTemporaryFile("w", suffix=".nec", delete=False) as f:
        f.write(nec); nec_path = f.name
    out_path = nec_path.replace(".nec", ".out")
    try:
        try:
            subprocess.run(["nec2c", "-i", nec_path, "-o", out_path],
                           capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            return {"error": "nec2c binary not found in PATH"}
        except subprocess.TimeoutExpired:
            return {"error": "nec2c timeout"}
        if not pathlib.Path(out_path).exists():
            return {"error": "nec2c produced no output file"}
        text = pathlib.Path(out_path).read_text()
    finally:
        for p in (nec_path, out_path):
            try: os.unlink(p)
            except Exception: pass
    impedances, pattern_blocks = parse_nec_output(text)
    if not impedances:
        return {"error": "no impedance parsed"}
    if not pattern_blocks or not any(pattern_blocks):
        return {"error": "no pattern parsed"}
    swrs = [swr(R, X) for (R, X) in impedances]
    max_swr = max(swrs)
    avg_swr = sum(swrs) / len(swrs)
    # Centre metrics at the TRUE operating centre frequency (interpolated on the
    # swept grid) rather than just the middle sample of the band.
    fcenter = float(glb.get("freq_mhz_center", freqs[len(freqs) // 2]))
    Rc, Xc = _interp_rx(freqs, impedances, fcenter)
    # Gain / F-B must come from ONE frequency's pattern (the design centre), not
    # a mix of all frequencies.  Pick the pattern block whose frequency is
    # nearest fcenter (skip any empty blocks).
    ci = min(range(len(freqs)), key=lambda i: abs(freqs[i] - fcenter))
    pattern = pattern_blocks[ci] if ci < len(pattern_blocks) and pattern_blocks[ci] else \
        next((b for b in pattern_blocks if b), [])
    max_gain = max(t[2] for t in pattern)
    peak = max(pattern, key=lambda t: t[2])
    peak_theta, peak_phi, _ = peak
    back_phi = (peak_phi + 180.0) % 360.0
    # Front-to-back must compare the forward peak against the REAR LOBE max
    # *at the forward main-lobe elevation* (theta within +/-ELEV_HALF of the
    # peak), inside +/-REAR_HALF deg of the back azimuth.  Searching ALL
    # elevations conflated high-angle ground/rear lobes into F/B and understated
    # it; reading a single 180-deg point can hit a null and overstate it to a
    # physically impossible 60-100 dB.  This cut avoids both.
    REAR_HALF = 30.0
    ELEV_HALF = 10.0
    rear_vals = [g for (t, p, g) in pattern
                 if abs(t - peak_theta) <= ELEV_HALF
                 and abs(((p - back_phi + 180.0) % 360.0) - 180.0) <= REAR_HALF]
    if not rear_vals:   # fallback: whole rear hemisphere if the elev cut is empty
        rear_vals = [g for (t, p, g) in pattern
                     if abs(((p - back_phi + 180.0) % 360.0) - 180.0) <= REAR_HALF]
    back_gain = max(rear_vals) if rear_vals else (max_gain - 40.0)
    fb_db = max_gain - back_gain
    return {
        "max_swr": max_swr, "avg_swr": avg_swr,
        "center_swr": swr(Rc, Xc),
        "center_r": Rc, "center_x": Xc,
        "gain_dbi": max_gain, "fb_db": fb_db,
        "peak_elev_deg": 90.0 - peak_theta,
        "peak_az_deg": peak_phi,
    }

# ---------- Geometry validator ----------
def validate(elements, rules):
    el_rules = rules["elements"]; sp_rules = rules["spacings"]
    by_name = {e["name"]: e for e in elements}
    for el in elements:
        nm = el["name"]
        r = el_rules.get(nm, {})
        L = float(el["length_in"])
        lmin = float(r.get("length_min_in", 0)); lmax = float(r.get("length_max_in", 9999))
        if not (lmin <= L <= lmax):
            return False, f"{nm} length {L:.2f} outside [{lmin},{lmax}]"
    for pair, b in sp_rules.items():
        parts = pair.split("_")
        if len(parts) != 2: continue
        a, c = parts
        if a not in by_name or c not in by_name: continue
        dist = abs(float(by_name[c]["position_in"]) - float(by_name[a]["position_in"]))
        smin = float(b.get("min_in", 0)); smax = float(b.get("max_in", 9999))
        if not (smin <= dist <= smax):
            return False, f"{pair} spacing {dist:.2f} outside [{smin},{smax}]"
    # Physical rule: the XFRMR and COUPLER must stay SHORTER than the DE, or
    # they take over the resonance and flip the pattern backwards.
    de = by_name.get("DE")
    if de is not None:
        de_len = float(de["length_in"])
        for nm in ("XFRMR", "COUPLER"):
            if nm in by_name and float(by_name[nm]["length_in"]) >= de_len:
                return False, f"{nm} length {float(by_name[nm]['length_in']):.2f} >= DE {de_len:.2f} (must be shorter)"
    return True, "ok"

# SCORE_MODE_V1
def _score_for_mode(mode, m):
    """mode: 'composite' (default), 'resonance', or 'match'."""
    if mode == "resonance":
        # Reward: |X| near 0, R near 50, max_swr low. No gain reward (would push longer = wrong).
        x_pen   = abs(m.get("center_x", 99))   * 100.0
        r_pen   = abs(m.get("center_r", 0) - 50.0) * 20.0
        swr_pen = max(0.0, m.get("max_swr", 99) - 1.5) * 1000.0
        return 5000.0 - x_pen - r_pen - swr_pen
    if mode == "match":
        # Best MATCH = the lowest WORST-CASE band SWR (what the user actually
        # reads on the meter).  SWR dominates the score; reactance is only a
        # tiny tiebreaker so that two points with the same SWR prefer the more
        # resonant one.  (Previously X was weighted so heavily that the search
        # would ACCEPT a higher SWR just to zero out X -- e.g. it picked
        # SWR 1.43 / X=0 over SWR 1.31 / X=-2, raising the SWR the user cares
        # about.  Now lowering band SWR always wins.)
        swr_pen = max(0.0, m.get("max_swr", 99) - 1.0) * 3000.0
        x_pen   = abs(m.get("center_x", 99)) * 5.0
        return 5000.0 - swr_pen - x_pen
    # default composite
    return v2_scorer.score(**m)

# ---------- Mini-tune executor ----------
def _frange(start, stop, step):
    vals = []
    v = float(start); stop = float(stop); step = float(step)
    if step <= 0: return [v]
    while v <= stop + 1e-9:
        vals.append(round(v, 4)); v += step
    return vals

def _run_group_tune(mt, elements, rules, log_fn, max_candidates):
    """Move a GROUP of elements together by the same offset (everything not in
    the group is locked). type group_position shifts boom positions; group_length
    shifts lengths. Sweeps offset in [-delta..+delta] and keeps the best."""
    names = [n for n in mt.get("elements", []) if any(e["name"] == n for e in elements)]
    if not names:
        if log_fn: log_fn("  SKIP: none of the group's elements are in the geometry")
        return elements, None, None, []
    param = "position_in" if mt["type"] == "group_position" else "length_in"
    d = float(mt["delta_in"])
    offsets = _frange(-d, d, mt["step_in"])
    if len(offsets) > max_candidates:
        offsets = offsets[:max_candidates]
    base = {e["name"]: float(e[param]) for e in elements if e["name"] in names}
    locked = [e["name"] for e in elements if e["name"] not in names]
    if log_fn:
        log_fn(f"  GROUP {param} on [{', '.join(names)}]  (locked: {', '.join(locked) or 'none'})")
    best_score = None; best_el = elements; best_m = None; log = []
    for off in offsets:
        cand = copy.deepcopy(elements)
        for e in cand:
            if e["name"] in names:
                e[param] = round(base[e["name"]] + off, 4)
        ok, reason = validate(cand, rules)
        if not ok:
            log.append({"v": off, "skip": reason})
            if log_fn: log_fn(f"    offset={off:+6.2f}  SKIP  {reason}")
            continue
        m = evaluate(cand, rules)
        if "error" in m:
            log.append({"v": off, "err": m["error"]})
            continue
        s = _score_for_mode(mt.get("score_mode", "composite"), m)
        log.append({"v": off, "score": s, "gain": m["gain_dbi"], "fb": m["fb_db"], "max_swr": m["max_swr"]})
        marker = ""
        if best_score is None or s > best_score:
            best_score = s; best_el = cand; best_m = m; marker = "  <-- BEST"
        if log_fn:
            log_fn(f"    offset={off:+6.2f}  score={s:+9.1f}  swr={m['max_swr']:.3f}  "
                   f"X={m.get('center_x',0):+5.1f}  gain={m['gain_dbi']:5.2f}{marker}")
    return best_el, best_score, best_m, log


def _run_window_tune(mt, elements, rules, log_fn, max_candidates):
    """Auto-tiling group move: slide a window of `window` directors across the
    whole array (DIR1+2, DIR3+4, ... for window=2), optimising each window's
    position/length in turn. Auto-fits however many directors exist. If
    `window_max` > `window`, it repeats with growing windows (2, then 3, ...).
    No element list needed -- it discovers the directors itself."""
    pos_param = mt["type"] == "group_window_position"
    sub_type = "group_position" if pos_param else "group_length"
    w0 = int(mt.get("window", 2))
    wmax = int(mt.get("window_max", w0))
    delta = float(mt.get("delta_in", 8.0))
    step = float(mt.get("step_in", 0.5))
    mode = mt.get("score_mode", "match")

    cur = elements
    best_m = None; best_score = None; log = []
    for w in range(max(1, w0), max(w0, wmax) + 1):
        dirs = sorted([e for e in cur if str(e["name"]).upper().startswith("DIR")],
                      key=lambda e: float(e["position_in"]))
        names = [e["name"] for e in dirs]
        if not names:
            if log_fn: log_fn("  SKIP: no directors in geometry")
            break
        if log_fn:
            log_fn(f"  -- window size {w}: {len(names)} directors -> "
                   f"{ -(-len(names)//w) } group(s)")
        for i in range(0, len(names), w):
            grp = names[i:i + w]
            submt = {"type": sub_type, "elements": grp, "delta_in": delta,
                     "step_in": step, "score_mode": mode}
            cur, sc, m, _sub = _run_group_tune(submt, cur, rules, log_fn, max_candidates)
            if m is not None:
                best_m, best_score = m, sc
            log.append({"window": w, "group": grp, "score": sc})
    return cur, best_score, best_m, log


def run_mini_tune(mt, elements, rules, log_fn=None, max_candidates=400):
    mtype = mt["type"]
    if mtype in ("group_position", "group_length"):
        return _run_group_tune(mt, elements, rules, log_fn, max_candidates)
    if mtype in ("group_window_position", "group_window_length"):
        return _run_window_tune(mt, elements, rules, log_fn, max_candidates)
    target_name = mt["element"]
    if not any(e["name"] == target_name for e in elements):
        if log_fn: log_fn(f"  SKIP: {target_name} not in geometry")
        return elements, None, None, []
    target = next(e for e in elements if e["name"] == target_name)
    if mtype == "sweep_length":
        param = "length_in"; vals = _frange(mt["start_in"], mt["stop_in"], mt["step_in"])
    elif mtype == "sweep_position":
        param = "position_in"; vals = _frange(mt["start_in"], mt["stop_in"], mt["step_in"])
    elif mtype == "nudge_length":
        param = "length_in"; c = float(target[param]); d = float(mt["delta_in"])
        vals = _frange(c - d, c + d, mt["step_in"])
    elif mtype == "nudge_position":
        param = "position_in"; c = float(target[param]); d = float(mt["delta_in"])
        vals = _frange(c - d, c + d, mt["step_in"])
    else:
        if log_fn: log_fn(f"  unknown type: {mtype}")
        return elements, None, None, []
    if len(vals) > max_candidates:
        if log_fn: log_fn(f"  WARNING: {len(vals)} candidates > max {max_candidates}, truncating")
        vals = vals[:max_candidates]
    best_score = None; best_el = elements; best_m = None; log = []
    for v in vals:
        cand = copy.deepcopy(elements)
        for e in cand:
            if e["name"] == target_name:
                e[param] = v
        ok, reason = validate(cand, rules)
        if not ok:
            log.append({"v": v, "skip": reason})
            if log_fn: log_fn(f"    {target_name}.{param}={v:6.2f}  SKIP  {reason}")
            continue
        m = evaluate(cand, rules)
        if "error" in m:
            log.append({"v": v, "err": m["error"]})
            if log_fn: log_fn(f"    {target_name}.{param}={v:6.2f}  ERROR  {m['error']}")
            continue
        s = _score_for_mode(mt.get("score_mode", "composite"), m)
        log.append({"v": v, "score": s, "gain": m["gain_dbi"], "fb": m["fb_db"], "max_swr": m["max_swr"]})
        marker = ""
        if best_score is None or s > best_score:
            best_score = s; best_el = cand; best_m = m; marker = "  <-- BEST"
        if log_fn:
            log_fn(f"    {target_name}.{param}={v:6.2f}  score={s:+9.1f}  gain={m['gain_dbi']:5.2f}  fb={m['fb_db']:5.2f}  swr={m['max_swr']:.3f}{marker}")
    return best_el, best_score, best_m, log

def run_procedure(proc, minis_by_name, elements, rules, log_fn=None):
    current = copy.deepcopy(elements)
    ok, reason = validate(current, rules)
    if not ok:
        if log_fn: log_fn(f"INVALID INITIAL: {reason}")
        return current, None, None, []
    init = evaluate(current, rules)
    if "error" in init:
        if log_fn: log_fn(f"INITIAL EVAL FAILED: {init['error']}")
        return current, None, None, []
    init_score = v2_scorer.score(**init)
    if log_fn:
        log_fn(f"INITIAL score={init_score:+.1f} gain={init['gain_dbi']:.2f} fb={init['fb_db']:.2f} swr={init['max_swr']:.3f}")
        def _find_freq(d):
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(k, str) and "freq" in k.lower() and isinstance(v, (int, float, str)):
                        return v
                    r = _find_freq(v)
                    if r is not None: return r
            elif isinstance(d, list):
                for v in d:
                    r = _find_freq(v)
                    if r is not None: return r
            return None
        _f = _find_freq(rules)
        log_fn("TARGET freq = " + str(_f) + " MHz   (set in Rules tab)")
    best_score = init_score; best_m = init
    step_results = []
    repeat = max(1, int(proc.get("repeat", 1)))
    min_improve = float(proc.get("repeat_min_improve", 0.3))
    prev_pass_score = None
    for p in range(1, repeat + 1):
        if repeat > 1 and log_fn:
            log_fn(f"\n========== PASS {p} of {repeat} ==========")
        for stepname in proc.get("steps", []):
            mt = minis_by_name.get(stepname)
            if mt is None:
                if log_fn: log_fn(f"\nSTEP {stepname} NOT FOUND, skipping")
                continue
            if log_fn:
                _tgt = (mt.get("element") or ", ".join(mt.get("elements", []))
                        or (f"window {mt.get('window')}" + (f"-{mt.get('window_max')}" if mt.get('window_max') else "") + " directors"
                            if "window" in mt else "?"))
                log_fn(f"\nSTEP {stepname} ({mt['type']} on {_tgt})")
            current, sc, mtr, mlog = run_mini_tune(mt, current, rules, log_fn)
            step_results.append({"pass": p, "step": stepname, "best_score": sc,
                                 "best_metrics": mtr, "candidates": mlog})
            if sc is not None and sc > best_score:
                best_score = sc; best_m = mtr
        # Convergence check between passes (composite score on the real geometry).
        pm = evaluate(current, rules)
        pass_score = v2_scorer.score(**pm) if "error" not in pm else -1e9
        if log_fn:
            log_fn(f"\n----- after PASS {p}: composite={pass_score:+.1f} "
                   f"gain={pm.get('gain_dbi',0):.2f} fb={pm.get('fb_db',0):.2f} swr={pm.get('max_swr',0):.3f} -----")
        if prev_pass_score is not None and pass_score <= prev_pass_score + min_improve:
            if log_fn:
                log_fn(f"[converged] pass {p} gained <= {min_improve}; stopping early (can't get better).")
            break
        prev_pass_score = pass_score
    # Re-evaluate the actual final geometry under composite.
    # Prevents resonance-mode step scores (~5000) from hijacking best.
    final_m = evaluate(current, rules)
    if "error" not in final_m:
        try:
            final_score = v2_scorer.score(**final_m)
            if log_fn:
                log_fn("\nFINAL-EVAL  score=%+.2f  gain=%.2f  fb=%.2f  swr=%.3f" % (final_score, final_m.get("gain_dbi", 0), final_m.get("fb_db", 0), final_m.get("max_swr", 0)))
            return current, final_score, final_m, step_results
        except Exception as _e:
            if log_fn: log_fn("final re-eval failed: " + str(_e))
    return current, best_score, best_m, step_results
