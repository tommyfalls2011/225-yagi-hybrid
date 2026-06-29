"""hyagi.cell_rules -- physical-build & pattern-realism rules.
JSON-backed (Streamlit-editable) + legacy test contract.
"""
from __future__ import annotations
import json, os, sys, functools
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
RULES_PATH = _ROOT / "data" / "physics_rules.json"

DEFAULTS = {
    "MIN_SPACING_IN": 4.0, "MAX_SPACING_IN": 36.0,
    "MIN_LEN_GAP_FROM_DE": 4.0, "MAX_PEAK_ELEV_DEG": 25.0,
    "DIRECTOR_MODE": "strict_progressive",
    "XFRMR_LT_DE": True, "COUPLER_LT_DE": True,
    "DIR_MIN_LEN_IN": 80.0, "DIR_MAX_LEN_IN": 108.0,
    "DE_MIN_LEN_IN": 100.0, "DE_MAX_LEN_IN": 112.0,
    "REFL_MIN_LEN_IN": 108.0, "REFL_MAX_LEN_IN": 120.0,
    "STRICT_PROGRESSION": True, "REJECT_SKY_BOUNCER": True,
    "REFL_GE_DE": True, "DIR1_LT_COUPLER": True,
    "SENTINEL_REAR_GAIN": -900.0,
    "BOOM_GROUNDED": False, "BOOM_AXIS": "x", "BOOM_GROUND_NAMES": ["XFRMR","COUPLER"], "BOOM_RADIUS_IN": 0.5,
}

class CellRulesViolation(Exception):
    pass

def get_rules() -> dict:
    try:
        with open(RULES_PATH) as f: data = json.load(f)
        merged = dict(DEFAULTS)
        for k, v in data.items():
            if k.startswith("_"): continue
            merged[k] = v
    except FileNotFoundError:
        merged = dict(DEFAULTS)
    except Exception as e:
        print(f"[cell_rules] WARN bad json: {e}", file=sys.stderr)
        merged = dict(DEFAULTS)
    env_mode = os.environ.get("HYAGI_DIRECTOR_MODE")
    if env_mode:
        merged["DIRECTOR_MODE"] = env_mode
    return merged

def save_rules(d: dict) -> None:
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in d.items() if not k.startswith("_")}
    with open(RULES_PATH, "w") as f: json.dump(clean, f, indent=2)

def __getattr__(name: str):
    r = get_rules()
    if name in r: return r[name]
    raise AttributeError(name)

def describe_active_rules() -> str:
    r = get_rules()
    return "[active physics rules]\n" + "\n".join(f"  {k:24s} = {r[k]}" for k in sorted(r))

# ---------------------------------------------------------------------------
def _attr(el, *names, default=None):
    if isinstance(el, dict):
        for n in names:
            if n in el and el[n] is not None: return el[n]
        return default
    for n in names:
        v = getattr(el, n, None)
        if v is not None: return v
    return default

def extract_cell_geom(geom):
    out = {"de_len": None, "x_len": None, "c_len": None, "ref_len": None,
           "directors": [], "director_lengths": [],
           "x_spacing": None, "c_spacing": None,
           "spacings": [], "peak_elev_deg": None}
    if geom is None: return out

    if isinstance(geom, dict):
        for k in out:
            if k in geom and geom[k] is not None: out[k] = geom[k]
        if not out["directors"]:
            out["directors"] = geom.get("director_lengths") or geom.get("dir_lens") or []
        out["director_lengths"] = list(out["directors"])
        return out

    de_pos = x_pos = c_pos = None
    dirs = []
    for el in geom:
        name = (_attr(el, "name", "tag", "label", default="") or "").upper()
        length = _attr(el, "length_in", "length", "len_in", "L")
        pos = _attr(el, "position_in", "position", "pos", "x", "boom_pos")
        if length is None: continue
        if name in ("XFRMR","X","XF"):       out["x_len"] = length;  x_pos = pos
        elif name in ("DE","DRIVEN","DRIVER"): out["de_len"] = length; de_pos = pos
        elif name in ("COUPLER","C","CPL"):   out["c_len"] = length;  c_pos = pos
        elif name in ("REF","REFL","REFLECTOR"): out["ref_len"] = length
        elif name.startswith("DIR") or name in ("D1","D2","D3","D4","D5"):
            dirs.append((pos if pos is not None else len(dirs), length))
    if dirs:
        dirs.sort(key=lambda t: t[0])
        out["directors"] = [l for _, l in dirs]
        out["director_lengths"] = list(out["directors"])
    if de_pos is not None and x_pos is not None: out["x_spacing"] = abs(de_pos - x_pos)
    if de_pos is not None and c_pos is not None: out["c_spacing"] = abs(c_pos - de_pos)
    return out

# ---------------------------------------------------------------------------
def violates_cell_rules(de_len=None, x_len=None, c_len=None,
                        ref_len=None, director_lengths=None, directors=None,
                        x_spacing=None, c_spacing=None, **_):
    r = get_rules()
    gap = r["MIN_LEN_GAP_FROM_DE"]
    dirs = directors if directors is not None else director_lengths

    if r["XFRMR_LT_DE"] and de_len is not None and x_len is not None:
        if x_len > de_len:
            return f"XFRMR length {x_len} exceeds DE length {de_len}"
        if x_len > (de_len - gap):
            return f"XFRMR length {x_len} within {gap}\" of DE length {de_len}"
    if r["COUPLER_LT_DE"] and de_len is not None and c_len is not None:
        if c_len > de_len:
            return f"COUPLER length {c_len} exceeds DE length {de_len}"
        if c_len > (de_len - gap):
            return f"COUPLER length {c_len} within {gap}\" of DE length {de_len}"
    if x_len is not None and c_len is not None and c_len > x_len:
        if r["XFRMR_LT_DE"] and r["COUPLER_LT_DE"]:
            return f"XFRMR length {x_len} below COUPLER length {c_len}"
    if r.get("REFL_GE_DE", True) and de_len is not None and ref_len is not None:
        if ref_len < de_len:
            return f"Reflector length {ref_len} shorter than DE length {de_len}"
    if (r["DIRECTOR_MODE"] == "strict_progressive"
            and r.get("DIR1_LT_COUPLER", True)
            and dirs and c_len is not None):
        if dirs[0] >= c_len:
            return f"DIR1 length {dirs[0]} not shorter than COUPLER length {c_len} (strict mode)"
    if r["STRICT_PROGRESSION"] and r["DIRECTOR_MODE"] == "strict_progressive" and dirs:
        for i in range(1, len(dirs)):
            if dirs[i] >= dirs[i-1]:
                return f"Director progression broken at DIR{i+1} (length {dirs[i]} >= DIR{i} length {dirs[i-1]})"
    if x_spacing is not None:
        if x_spacing < r["MIN_SPACING_IN"]:
            return f"XFRMR spacing {x_spacing} below MIN_SPACING_IN {r['MIN_SPACING_IN']}"
        if x_spacing > r["MAX_SPACING_IN"]:
            return f"XFRMR spacing {x_spacing} above MAX_SPACING_IN {r['MAX_SPACING_IN']}"
    if c_spacing is not None:
        if c_spacing < r["MIN_SPACING_IN"]:
            return f"COUPLER spacing {c_spacing} below MIN_SPACING_IN {r['MIN_SPACING_IN']}"
        if c_spacing > r["MAX_SPACING_IN"]:
            return f"COUPLER spacing {c_spacing} above MAX_SPACING_IN {r['MAX_SPACING_IN']}"
    return None

violates_rules = violates_cell_rules

# ---------------------------------------------------------------------------
def violates_pattern(res: dict):
    if not res: return None
    r = get_rules()
    sentinel = r.get("SENTINEL_REAR_GAIN", -900.0)

    if r["REJECT_SKY_BOUNCER"]:
        pe = res.get("peak_elev_deg")
        if pe is not None and pe > r["MAX_PEAK_ELEV_DEG"]:
            return f"sky-bouncer: peak_elev_deg {pe} > {r['MAX_PEAK_ELEV_DEG']}"

    fb = res.get("front_back_db", res.get("fb_db"))
    if fb is not None and fb < 0:
        return f"negative F/B ({fb} dB) -- pattern reversed"

    fwd = res.get("horizon_gain_dbi", res.get("forward_gain_dbi"))
    rear = res.get("horizon_rear_gain_dbi", res.get("rear_gain_dbi"))
    if fwd is not None and rear is not None and rear > sentinel:
        if rear >= fwd:
            return f"horizon rear gain {rear} >= horizon forward gain {fwd} -- pattern reversed"
    return None

# ---------------------------------------------------------------------------
def validate_final(geom):
    g = extract_cell_geom(geom)
    reason = violates_cell_rules(
        de_len=g["de_len"], x_len=g["x_len"], c_len=g["c_len"],
        ref_len=g["ref_len"], directors=g["directors"] or None,
        x_spacing=g["x_spacing"], c_spacing=g["c_spacing"],
    )
    if reason: raise CellRulesViolation(reason)
    return True

def guard_eval(fn):
    @functools.wraps(fn)
    def wrapper(geom, *a, **kw):
        validate_final(geom)
        result = fn(geom, *a, **kw)
        if isinstance(result, dict):
            p = violates_pattern(result)
            if p: raise CellRulesViolation(p)
        return result
    return wrapper

# convenience predicates (in-app use)
def violates_xfrmr(de_len_in, xfrmr_len_in):
    r = get_rules()
    if not r["XFRMR_LT_DE"]: return False
    return xfrmr_len_in >= (de_len_in - r["MIN_LEN_GAP_FROM_DE"])

def violates_coupler(de_len_in, coupler_len_in):
    r = get_rules()
    if not r["COUPLER_LT_DE"]: return False
    return coupler_len_in >= (de_len_in - r["MIN_LEN_GAP_FROM_DE"])

def violates_director_progression(dir_lens_in):
    r = get_rules()
    if not r["STRICT_PROGRESSION"]: return False
    if r["DIRECTOR_MODE"] == "experimental_progressive": return False
    for i in range(1, len(dir_lens_in)):
        if dir_lens_in[i] >= dir_lens_in[i-1]: return True
    return False

def violates_sky_bouncer(peak_elev_deg):
    r = get_rules()
    if not r["REJECT_SKY_BOUNCER"]: return False
    return peak_elev_deg > r["MAX_PEAK_ELEV_DEG"]

def violates_spacing(spacings_in):
    r = get_rules()
    for s in spacings_in:
        if s < r["MIN_SPACING_IN"] or s > r["MAX_SPACING_IN"]: return True
    return False

def check_geometry(geom):
    try: validate_final(geom); return None
    except CellRulesViolation as e: return str(e)
