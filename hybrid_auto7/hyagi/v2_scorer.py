"""
hyagi/v2_scorer.py  --  Unified scorer for hybrid_auto7 v2.

Calling styles (both accepted; auto-detected):
    1. Legacy / runner style:
           score(swr=1.13, gain_dbi=10.4, fb_db=18.0, x_ohm=4.1,
                 max_swr=1.5, min_gain=8.0, ...)
       Any legacy kwargs (max_swr, min_gain, target_freq, etc.) are
       silently absorbed -- the SWR profile system replaces them.

    2. New style:
           score(metrics_dict, mode="composite", profile_key="tight_1.0")

SWR target profiles (read from data/run_options_v2.json; set via the
sidebar selector on Run / Learning pages):

    tight_1.0  -> ideal <=1.10, soft to 1.20, heavy above
    good_1.2   -> ideal <=1.20, soft to 1.35, heavy above
    ok_1.5     -> ideal <=1.50, soft to 1.70, heavy above

Score modes:
    composite  -> gain + 0.15*F/B - SWR_penalty           (higher better)
    resonance  -> -|X| + 0.10*gain                        (higher better,
                                                          used for bare-DE pass)
"""
from __future__ import annotations
from pathlib import Path
import json

_DATA  = Path(__file__).resolve().parent.parent / "data"
_OPTS  = _DATA / "run_options_v2.json"

SWR_PROFILES = {
    "tight_1.0": {
        "label":               "Tight  -- target ~1.0:1  (lab / contest)",
        "ideal_max":           1.05,
        "soft_max":            1.15,
        "soft_penalty_slope":  25.0,
        "hard_penalty_slope":  200.0,
    },
    "good_1.2": {
        "label":               "Good   -- target ~1.2:1  (high-end customer)",
        "ideal_max":           1.20,
        "soft_max":            1.35,
        "soft_penalty_slope":  4.0,
        "hard_penalty_slope":  18.0,
    },
    "wideband_1.2": {
        "label":               "Wideband -- hard target <=1.2:1 across band",
        "ideal_max":           1.20,
        "soft_max":            1.25,
        "soft_penalty_slope":  120.0,
        "hard_penalty_slope":  600.0,
    },
    "ok_1.5": {
        "label":               "OK     -- target ~1.5:1  (customer build)",
        "ideal_max":           1.50,
        "soft_max":            1.70,
        "soft_penalty_slope":  3.0,
        "hard_penalty_slope":  12.0,
    },
}

DEFAULT_PROFILE_KEY = "tight_1.0"

_LEGACY_IGNORE_KEYS = {
    "min_gain", "min_fb", "target_freq", "freq_mhz",
    "weight_gain", "weight_fb", "weight_swr", "target_z",
    "spacing_min_in", "spacing_max_in",
}


def _load_active_options() -> dict:
    try:
        if _OPTS.exists():
            return json.loads(_OPTS.read_text())
    except Exception:
        pass
    return {"swr_profile": DEFAULT_PROFILE_KEY, "score_mode": "composite"}


def get_active_profile_key() -> str:
    key = _load_active_options().get("swr_profile", DEFAULT_PROFILE_KEY)
    return key if key in SWR_PROFILES else DEFAULT_PROFILE_KEY


def get_profile(profile_key=None) -> dict:
    if profile_key and profile_key in SWR_PROFILES:
        return SWR_PROFILES[profile_key]
    return SWR_PROFILES[get_active_profile_key()]


def swr_penalty(swr, profile_key=None):
    p = get_profile(profile_key)
    if swr <= p["ideal_max"]:
        return 0.0
    if swr <= p["soft_max"]:
        return (swr - p["ideal_max"]) * p["soft_penalty_slope"]
    soft_band = (p["soft_max"] - p["ideal_max"]) * p["soft_penalty_slope"]
    return soft_band + (swr - p["soft_max"]) * p["hard_penalty_slope"]


def _coerce_metrics(args, kwargs):
    mode        = kwargs.pop("mode",        None)
    profile_key = kwargs.pop("profile_key", None)
    for k in list(kwargs.keys()):
        if k in _LEGACY_IGNORE_KEYS:
            kwargs.pop(k, None)
    metrics = None
    if args:
        first = args[0]
        if isinstance(first, dict):
            metrics = dict(first)
        if len(args) >= 2 and mode is None and isinstance(args[1], str):
            mode = args[1]
        if len(args) >= 3 and profile_key is None and isinstance(args[2], str):
            profile_key = args[2]
    if metrics is None:
        metrics = dict(kwargs)
    else:
        metrics.update(kwargs)
    return metrics, mode, profile_key


def score(*args, **kwargs):
    metrics, mode, profile_key = _coerce_metrics(list(args), kwargs)
    if mode is None:
        mode = _load_active_options().get("score_mode", "composite")
    swr  = float(metrics.get("swr", metrics.get("max_swr", 99.0)))
    gain = float(metrics.get("gain_dbi", 0.0))
    fb   = float(metrics.get("fb_db",    0.0))
    xr   = abs(float(metrics.get("x_ohm", 0.0)))
    if mode == "resonance":
        return -xr + 0.10 * gain
    return gain + 0.15 * fb - swr_penalty(swr, profile_key)


def score_breakdown(*args, **kwargs):
    metrics, mode, profile_key = _coerce_metrics(list(args), kwargs)
    if mode is None:
        mode = _load_active_options().get("score_mode", "composite")
    pkey = profile_key or get_active_profile_key()
    swr  = float(metrics.get("swr", metrics.get("max_swr", 99.0)))
    gain = float(metrics.get("gain_dbi", 0.0))
    fb   = float(metrics.get("fb_db",    0.0))
    xr   = abs(float(metrics.get("x_ohm", 0.0)))
    if mode == "resonance":
        return {"mode": "resonance", "profile": pkey,
                "gain_term": round(0.10 * gain, 4),
                "reactance_pen": round(xr, 4),
                "total": round(-xr + 0.10 * gain, 4)}
    pen = swr_penalty(swr, pkey)
    return {"mode": "composite", "profile": pkey,
            "gain_term":   round(gain, 4),
            "fb_term":     round(0.15 * fb, 4),
            "swr":         round(swr, 4),
            "swr_penalty": round(pen, 4),
            "total":       round(gain + 0.15 * fb - pen, 4)}
