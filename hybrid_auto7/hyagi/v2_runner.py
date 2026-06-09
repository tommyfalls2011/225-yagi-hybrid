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


def _emit_element(out, tag, p, L, H, taper):
    """Append GW cards for one stepped-diameter element centred on the y axis at
    boom position p, height H. Returns (last_tag, feed_tag, feed_seg)."""
    half = L / 2.0
    secs = _half_sections(half, taper)
    r0, l0 = secs[0]
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
                   pattern=True, taper="auto", conductor_sigma=ALUMINUM_SIGMA):
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
    H = height_ft * FT
    out = ["CM hybrid_auto7 v2 (tapered Al)", "CE"]
    de_feed_tag = None
    de_feed_seg = None
    tag = 0
    for el in elements:
        p = float(el["position_in"]) * INCH
        L = float(el["length_in"]) * INCH
        if taper:
            tag, ftag, fseg = _emit_element(out, tag, p, L, H, taper)
        else:
            a = wire_radius_in * INCH
            segs = max(11, (int(L / (10.0 * INCH))) | 1)
            tag += 1
            out.append(f"GW {tag} {segs} {p:.6f} {-L/2:.6f} {H:.6f} {p:.6f} {L/2:.6f} {H:.6f} {a:.6f}")
            ftag, fseg = tag, (segs + 1) // 2
        if el["name"].upper() == "DE":
            de_feed_tag, de_feed_seg = ftag, fseg
    if de_feed_tag is None:
        raise ValueError("No DE element")
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
    impedances = []
    pattern = []
    in_imp = False
    in_pat = False
    for ln in text.splitlines():
        if "ANTENNA INPUT PARAMETERS" in ln:
            in_imp = True; in_pat = False; continue
        if "RADIATION PATTERNS" in ln:
            in_imp = False; in_pat = True; continue
        if in_imp:
            m = _IMP_RE.match(ln)
            if m:
                impedances.append((float(m.group(1)), float(m.group(2))))
                in_imp = False
        elif in_pat:
            m = _PAT_RE.match(ln)
            if m:
                try:
                    pattern.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
                except ValueError:
                    pass
    return impedances, pattern

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
    impedances, pattern = parse_nec_output(text)
    if not impedances:
        return {"error": "no impedance parsed"}
    if not pattern:
        return {"error": "no pattern parsed"}
    swrs = [swr(R, X) for (R, X) in impedances]
    max_swr = max(swrs)
    avg_swr = sum(swrs) / len(swrs)
    Rc, Xc = impedances[len(impedances)//2]
    max_gain = max(t[2] for t in pattern)
    peak = max(pattern, key=lambda t: t[2])
    peak_theta, peak_phi, _ = peak
    back_phi = (peak_phi + 180.0) % 360.0
    # Front-to-back must compare the forward peak against the REAR LOBE maximum,
    # i.e. the strongest gain within +/-REAR_HALF deg of the back azimuth (over
    # all elevations).  Reading a single 180-deg point can land in a sharp null
    # and produce a physically impossible F/B of 60-100 dB.
    REAR_HALF = 30.0
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
        # Best MATCH: drive reactance X -> 0, SWR -> 1 (max return loss). No R or
        # gain term, so group moves are free to hunt the lowest-SWR / X=0 point.
        x_pen   = abs(m.get("center_x", 99)) * 120.0
        swr_pen = max(0.0, m.get("max_swr", 99) - 1.0) * 1500.0
        return 5000.0 - x_pen - swr_pen
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


def run_mini_tune(mt, elements, rules, log_fn=None, max_candidates=400):
    mtype = mt["type"]
    if mtype in ("group_position", "group_length"):
        return _run_group_tune(mt, elements, rules, log_fn, max_candidates)
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
    for stepname in proc.get("steps", []):
        mt = minis_by_name.get(stepname)
        if mt is None:
            if log_fn: log_fn(f"\nSTEP {stepname} NOT FOUND, skipping")
            continue
        if log_fn:
            _tgt = mt.get("element") or ", ".join(mt.get("elements", [])) or "?"
            log_fn(f"\nSTEP {stepname} ({mt['type']} on {_tgt})")
        current, sc, mtr, mlog = run_mini_tune(mt, current, rules, log_fn)
        step_results.append({"step": stepname, "best_score": sc, "best_metrics": mtr, "candidates": mlog})
        if sc is not None and sc > best_score:
            best_score = sc; best_m = mtr
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
