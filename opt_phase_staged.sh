#!/usr/bin/env bash
# opt_phase_staged.sh - Run opt_7el_yagi2.py in progressive-complexity phases.
#
# Each phase trains on a smaller geometry, then the next phase grows N.
# Phase N's results feed Phase N+1 via --learn-from (history-based seeding,
# but ONLY within the same N because filter is exact). To bridge across N,
# the script writes a promoted seed JSON for the next N using the prior
# phase's geometry, padded with default directors.
#
# Usage: ./opt_phase_staged.sh [N_FINAL] [CENTER_FREQ] [BOOM_FT] [TAG]
set -e
N_FINAL=${1:-7}
CF=${2:-27.025}
BOOM=${3:-50}
TAG=${4:-staged_$(date +%H%M%S)}
SCRIPT=~/scripts/opt_7el_yagi2.py
SEEDS=~/scripts/yagi_seeds

echo "============================================================"
echo "Phase-Staged Optimization"
echo "  Final N=${N_FINAL}  boom=${BOOM}ft  cf=${CF}MHz  tag=${TAG}"
echo "============================================================"

PHASES=(2 4 5 ${N_FINAL})
P=1
for N in "${PHASES[@]}"; do
    echo ""
    echo "*** PHASE $P (N=$N elements) ***"
    python3 $SCRIPT         --elements $N         --boom-length-ft $BOOM         --lock-boom         --spacing-style long         --strategy broadband         --center-freq $CF         --learn-from 100         --tag "${TAG}_p${P}_n${N}" 2>&1 | tail -60
    P=$((P+1))
done

echo ""
echo "============================================================"
echo "All phases complete."
echo "Compare scores in Streamlit -> Learning Lab -> Section 2."
echo "Filter recent runs by tag prefix '${TAG}' to see the curve."
echo "============================================================"
