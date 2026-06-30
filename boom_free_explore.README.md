## boom_free_explore.patch — Make FREE mode actually explore (single combined patch)

**Apply this ONE patch — it contains v4 + v5 together, on top of your current v3 state.**

```bash
cd ~/scripts
git pull
git apply boom_free_explore.patch
# verify (expect new strings about FRESH START + force-restarts + perturbation)
grep -n "FRESH START\|force.*restart\|FREE perturbation" hybrid_auto7/hyagi/match_opt.py hybrid_auto7/hyagi/auto_learn.py | head
# restart Streamlit
```

(Ignore the previous v4 patch — this one replaces it.)

### Why your last log still showed 24'6"

You'd applied v3 only.  The v3 sniff-test decided your 294" gaps were
"healthy" and skipped the reseed.  Combined with `Search restarts = 0`,
the optimizer had NO mechanism to ever leave the 294" basin.

### What this patch changes (4 fixes, all combined)

**1. FREE mode is now a fresh start, always** (`match_opt.py` reseed):
- Drops the v3 sniff-test entirely.
- Every FREE-mode tune discards the leftover director positions from disk
  (those came from your previous FIXED tune) and reseeds gaps to the rules
  midpoints.
- XFRMR / DE / COUPLER tight cell is still preserved.
- New log:
  ```
  [boom-free] FRESH START -- discarded leftover director positions from disk
              (these were from your previous FIXED tune)... starting span 304.37"
  ```

**2. Optimizer's result is adopted unconditionally in FREE mode** (`auto_learn.py`):
- No more silent baseline-revert in FREE mode -- if you picked FREE you
  want the optimizer's answer, period.
- FIXED mode keeps the safety revert (protects a hand-locked design).
- New log lines:
  ```
  [adopt] FREE mode -- adopting whatever the optimizer found.
          boom span 294.00" -> 286.50" (delta -7.50").
  ```

**3. FREE mode forces ≥ 3 restarts** (`auto_learn.py`):
- Your `Search restarts = 0` in the UI was preventing any exploration.
- In FREE mode that's now overridden to a minimum of 3 -- the optimizer
  perturbs director gaps and tries 3 different basins.
- New log:
  ```
  [free-mode] forcing optimizer to 3 restarts (you set 0) so it can try
              genuinely different boom lengths.
  ```

**4. Between hybrid iterations, jump basins in FREE mode** (`match_opt.py`):
- Currently `_optimize_hybrid` polishes the same local minimum every
  iteration -- beam->cell->beam->cell, never moving the boom.
- New code: between iters, perturb director gaps by ~50% of their rules
  range (e.g. sp_DIR2 ranges 40-96" -> ±14" jumps).  Each iter now
  starts from a genuinely different boom span.
- New log per iter shows the boom length and SWR result:
  ```
  [hybrid] iter 2 -- FREE perturbation: jumped director gaps by ~50% of
           their rules range -> new starting boom 312.50".  Beam + cell
           match will now refine from this fresh basin.
  [hybrid] iter 2: band_max_swr=1.195  gain=15.41  fb=21.55  boom=312.50"  score=+19.65
  ```

**5. Warm-start is now used in FREE mode** (`auto_learn.py`):
- Previously, if the on-disk geometry had a "stronger beam" than the
  warm-start candidate, warm-start was rejected.  This pinned you to
  the 294" geometry every tune.
- In FREE mode that override is skipped -- if the DB has a candidate
  with a different boom, USE IT as the seed.  Your run #167 with
  SWR 1.27 will actually be tried now.

### Expected behavior on the next tune

You should see the boom span actually change in the Result panel.  The
optimizer will explore 3+ different boom lengths via restarts plus the
iter-to-iter perturbations, and adopt whichever produced the best score.

If after all that the optimizer STILL converges back to ~294", that's
genuine evidence your hand-tune is the global optimum for this design
-- but at least you'll see the explored alternatives in the log.

### Regression
All 19 existing tests still pass.
