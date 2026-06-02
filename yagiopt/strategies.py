"""Named optimization strategies derived from history DB analysis."""

# Element direction bias mined from 22 runs / 1,415 accepted moves.
# Format: {element: (preferred_action, confidence_pct)}
DIRECTION_BIAS = {
    "REF": ("shorter",  100),
    "DE":  ("longer",    48),
    "D1":  ("backward",  44),
    "D2":  ("backward",  51),
    "D3":  ("forward",   81),
    "D4":  ("backward",  67),
    "D5":  ("forward",   70),
}

# Named priority profiles (gain, swr, rl, bw, fb on 0-100)
STRATEGIES = {
    "deep-match": {
        "description": "Champion all-rounder. Best avg score across 5 test runs.",
        "priorities":  {"gain": 40, "swr": 98, "rl": 98, "bw": 55, "fb": 45},
        "preferred_seed": 7,
        "expected_score": 365,
        "expected_fb_db": 19.3,
        "expected_bw_mhz": 0.48,
    },
    "broadband": {
        "description": "Widest bandwidth + lowest SWR. Best for wide-tuning rigs.",
        "priorities":  {"gain": 35, "swr": 85, "rl": 85, "bw": 98, "fb": 40},
        "preferred_seed": 42,
        "expected_score": 312,
        "expected_fb_db": 18.5,
        "expected_bw_mhz": 0.60,
    },
    "tight-fb-fixed": {
        "description": "F/B emphasis WITHOUT bandwidth collapse (rebalanced from learn-tight-fb failure).",
        "priorities":  {"gain": 50, "swr": 78, "rl": 78, "bw": 70, "fb": 85},
        "preferred_seed": 7,
        "expected_score": 280,
        "expected_fb_db": 19.0,
        "expected_bw_mhz": 0.45,
    },
    "champion": {
        "description": "Reproduces best-recorded run (#20: score 365.6, F/B 19.32 dB).",
        "priorities":  {"gain": 40, "swr": 98, "rl": 98, "bw": 55, "fb": 45},
        "preferred_seed": 7,
        "expected_score": 365,
        "expected_fb_db": 19.3,
        "expected_bw_mhz": 0.48,
    },
}

def list_strategies():
    return sorted(STRATEGIES.keys())

def get_strategy(name):
    if name not in STRATEGIES:
        raise ValueError("Unknown strategy: " + name + ". Available: " + ", ".join(list_strategies()))
    return STRATEGIES[name]
