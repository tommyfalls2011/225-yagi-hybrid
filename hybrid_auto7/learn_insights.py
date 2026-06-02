#!/usr/bin/env python3
"""
LEARN_INSIGHTS_v1
Walks every move-log JSONL on disk and aggregates stats per stage / parameter.
Tells you which moves actually help the optimizer vs which are noise.

Reads:
  data/learning_runs/learn*_moves_*.jsonl
  data/cell_learning_runs/learn_cell_moves_*.jsonl
  data/full_hybrid_runs/*moves*.jsonl

Usage:
  python3 ./learn_insights.py            # print report to stdout
  python3 ./learn_insights.py --json     # raw JSON to stdout
"""
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

LOG_PATTERNS = [
    "learning_runs/learn*_moves_*.jsonl",
    "cell_learning_runs/learn_cell_moves_*.jsonl",
    "full_hybrid_runs/*moves*.jsonl",
]


def find_logs():
    found = []
    for pat in LOG_PATTERNS:
        found.extend(DATA_DIR.glob(pat))
    return sorted(set(found), key=lambda p: p.stat().st_mtime)


def parse_log(path):
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def parse_move(move_str):
    """'xsp=13.0' -> ('xsp', 13.0). Returns (name, value_or_None)."""
    if not isinstance(move_str, str) or "=" not in move_str:
        return None, None
    name, _, val = move_str.partition("=")
    try:
        return name.strip(), float(val.strip())
    except ValueError:
        return name.strip(), None


def compute_insights():
    """Top-level entry. Returns a fully-built insights dict (JSON-safe)."""
    logs = find_logs()
    all_rows = []
    for p in logs:
        all_rows.extend(parse_log(p))

    by_stage = defaultdict(list)
    by_param = defaultdict(list)
    grand_deltas = []
    wins = 0

    for r in all_rows:
        d = r.get("score_delta")
        if d is None:
            b = r.get("before_score"); a = r.get("after_score")
            if b is None or a is None:
                continue
            d = a - b
        stage = r.get("stage", "unknown")
        by_stage[stage].append(d)
        grand_deltas.append(d)
        if d > 0:
            wins += 1
        pname, pval = parse_move(r.get("move", ""))
        if pname is not None and pval is not None:
            by_param[pname].append((pval, d))

    total = len(grand_deltas)

    # Stage table
    stages = []
    for stage, deltas in by_stage.items():
        s_wins = sum(1 for d in deltas if d > 0)
        stages.append({
            "stage": stage,
            "moves": len(deltas),
            "wins": s_wins,
            "win_rate": (s_wins / len(deltas)) if deltas else 0.0,
            "mean_delta": statistics.mean(deltas) if deltas else 0.0,
            "median_delta": statistics.median(deltas) if deltas else 0.0,
            "max_delta": max(deltas) if deltas else 0.0,
            "min_delta": min(deltas) if deltas else 0.0,
        })
    stages.sort(key=lambda r: r["mean_delta"], reverse=True)

    # Parameter winning ranges
    params = {}
    for pname, pairs in by_param.items():
        bins = defaultdict(list)
        for v, d in pairs:
            bins[round(v)].append(d)
        bin_stats = []
        for b, deltas in bins.items():
            bin_stats.append({
                "value": b,
                "n": len(deltas),
                "mean_delta": statistics.mean(deltas),
                "win_rate": sum(1 for d in deltas if d > 0) / len(deltas),
            })
        bin_stats.sort(key=lambda r: r["mean_delta"], reverse=True)
        params[pname] = {
            "total_samples": len(pairs),
            "top_values": bin_stats[:8],
            "worst_values": bin_stats[-5:][::-1],
        }

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "log_count": len(logs),
        "log_files": [str(p) for p in logs[-10:]],  # last 10 paths for brevity
        "summary": {
            "total_moves": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": (wins / total) if total else 0.0,
            "mean_delta": statistics.mean(grand_deltas) if grand_deltas else 0.0,
            "median_delta": statistics.median(grand_deltas) if grand_deltas else 0.0,
            "max_delta": max(grand_deltas) if grand_deltas else 0.0,
            "min_delta": min(grand_deltas) if grand_deltas else 0.0,
        },
        "stages": stages,
        "parameters": params,
    }


def print_report(ins):
    s = ins["summary"]
    print()
    print("=" * 70)
    print("LEARNING INSIGHTS REPORT")
    print("=" * 70)
    print(f"Log files scanned:  {ins['log_count']}")
    print(f"Total moves:        {s['total_moves']:,}")
    print(f"Wins / losses:      {s['wins']:,} / {s['losses']:,}")
    print(f"Overall win rate:   {s['win_rate']*100:.1f}%")
    print(f"Mean score delta:   {s['mean_delta']:+.2f}")
    print(f"Median delta:       {s['median_delta']:+.2f}")
    print(f"Best move ever:     {s['max_delta']:+.2f}")
    print(f"Worst move ever:    {s['min_delta']:+.2f}")
    print()
    print("STAGE EFFECTIVENESS (sorted by mean score delta)")
    print("-" * 70)
    print(f"{'Stage':<24} {'N':>7} {'Win%':>7} {'Mean':>10} {'Median':>10} {'Best':>10}")
    for r in ins["stages"]:
        print(
            f"{r['stage']:<24} {r['moves']:>7} {r['win_rate']*100:>6.1f}% "
            f"{r['mean_delta']:>+10.2f} {r['median_delta']:>+10.2f} {r['max_delta']:>+10.2f}"
        )
    print()
    print("PARAMETER WINNING RANGES (top 8 / param)")
    print("-" * 70)
    for pname, info in sorted(ins["parameters"].items()):
        print(f"  [{pname}]  ({info['total_samples']:,} samples)")
        for r in info["top_values"]:
            print(
                f"    value={r['value']:>6}  n={r['n']:>5}  "
                f"win%={r['win_rate']*100:>5.1f}  mean_delta={r['mean_delta']:+.2f}"
            )
        print()


def main(argv=None):
    argv = argv or sys.argv[1:]
    ins = compute_insights()
    if ins["log_count"] == 0:
        print("No move log files found under data/. Run a learning script first.")
        return 1

    if "--json" in argv:
        print(json.dumps(ins, indent=2))
    else:
        print_report(ins)

    out_dir = DATA_DIR / "learning_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"insights_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_file.write_text(json.dumps(ins, indent=2), encoding="utf-8")
    print(f"\nSnapshot saved: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
