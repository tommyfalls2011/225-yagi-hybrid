## LATEST FILES — bypass the patch mess entirely

Patches keep failing because we've been incrementally applying v1→v2→v3
and the line offsets don't match what `git apply` expects.  This folder
contains the **complete latest** versions of the two files that have
changed.  Just copy them into place.

```bash
cd ~/scripts
git pull                                                # picks up latest_files/
cp latest_files/hybrid_auto7/hyagi/match_opt.py  hybrid_auto7/hyagi/match_opt.py
cp latest_files/hybrid_auto7/hyagi/auto_learn.py hybrid_auto7/hyagi/auto_learn.py
# verify the new strings are present (expect ~5 hits across both files)
grep -n "FRESH START\|free-mode forcing\|FREE perturbation" \
     hybrid_auto7/hyagi/match_opt.py hybrid_auto7/hyagi/auto_learn.py
# restart Streamlit
```

These two files contain everything from boom_free_fix.patch +
boom_free_steps.patch + v3 + v4 + v5 (explore) all rolled in.  Just
overwrite your local copies with these.

## What's in them (5 fixes)

1. **FREE mode always reseeds** director gaps to rules midpoints
   (XFRMR/COUPLER tight cell preserved) — no more sniff-test that left
   the 294" leftover in place.
2. **FREE mode adopts the optimizer's result unconditionally** — no
   silent baseline-revert.  FIXED mode keeps the safety revert.
3. **FREE mode forces ≥ 3 restarts** even if you set `Search restarts = 0`
   — your UI setting was the single biggest reason the boom never moved.
4. **Hybrid iterations perturb the boom between iters** by ±50% of the
   rules range (sp_DIR2 40-96" → ±14" jumps) so each beam+cell pass
   starts from a genuinely different boom basin.
5. **Warm-start DB is used in FREE mode** — the `run #167` candidate
   the log kept rejecting will now actually be the seed.

## Expected log lines after copying & restarting Streamlit

```
[boom-free] FRESH START -- discarded leftover director positions from disk...
[free-mode] forcing optimizer to 3 restarts (you set 0)...
[hybrid] iter 2 -- FREE perturbation: jumped director gaps by ~50%...
[adopt] FREE mode -- adopting whatever the optimizer found. boom span 294.00" -> XXX.XX"
```
