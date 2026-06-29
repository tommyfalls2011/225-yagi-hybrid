## boom_free_v3.patch — STOP undoing your hand-tuned geometry & make the silent revert visible

**Apply this on top of `boom_free_fix.patch` + `boom_free_steps.patch` (the two you already applied).**

```bash
cd ~/scripts
git pull
git apply boom_free_v3.patch
# restart Streamlit
```

### What was actually broken (root cause, finally)

Your tune logs proved the optimizer **was** exploring boom lengths.  But you
were always seeing 24'6" back at the end because of TWO interacting bugs:

1. **The midpoint reseed was overriding your hand-tune.**  Your input geometry
   (REF→DIR3 = 294", band-max 1.220) was already in a hand-tuned local
   optimum.  The reseed blindly jumped it to ~304" (rules midpoints), and
   the descent then couldn't beat your baseline from that new starting
   point.  So the optimizer ended worse than your input.

2. **`auto_learn` silently reverted to baseline** when the optimizer's
   result was worse than your input.  No log line, no warning -- you just
   saw "24'6"" again.  Specifically `hyagi/auto_learn.py:562`:

   ```python
   if band_max < best_metrics.get("band_max_swr", 99.0) - 1e-6 or band_max <= cfg.target_max_swr:
       best_geo = ... new_geo ...   # only adopted IF strictly better
   ```

### What this patch does

1. **Compression sniff-test before reseed.**  The reseed now only fires if
   at least one director-to-director gap is within 5" of its rules MINIMUM
   (i.e., the geometry was genuinely compressed by a prior FIXED tune).
   If your gaps are already in a healthy range, the reseed is SKIPPED and
   the optimizer starts from your hand-tuned geometry.  New log line:
   ```
   [boom-free] director spacings are already in a healthy range (boom span 294.00") -- NOT reseeding (would undo your hand-tune). Optimizer still free to grow or shrink from here.
   ```

2. **Explicit revert logging.**  When `auto_learn` keeps your baseline
   because the optimizer couldn't beat it, you now see:
   ```
   [keep-baseline] optimizer best band-max 1.286 > YOUR INPUT'S baseline 1.220 -- your starting geometry (boom 294.00") was already better, keeping it.  Optimizer DID explore -- it tried a boom of 304.37" (delta +10.37") at SWR 1.286, but reverted because your input scored better.  To force more exploration: raise restart count, raise target SWR, or reseed the cell.
   ```
   And when it DOES adopt the optimizer's geometry:
   ```
   [adopt] band-max 1.180 <= baseline 1.220 -- ADOPTING optimizer geometry. boom span 294.00" -> 286.50" (delta -7.50").
   ```

### Why the boom isn't changing on your design

The numbers from your last log are pretty clear: **294" IS the optimum for
your Boss-Hogg design**.  The optimizer tried 304" and reported back SWR
1.286 (worse than your 1.220 baseline).  Your hand-tune is genuinely at a
local minimum.

To actually move the boom on this design you need to either:

- **Raise target SWR** above 1.05 so the matcher has a wider "good enough"
  basin to wander in, OR
- **Increase Search restarts** (currently 0) so the optimizer perturbs the
  geometry and tries random jumps to escape the 294" basin, OR
- **Change the seeded cell** (XFRMR/COUPLER lengths or gaps on the Antenna
  Setup page) -- the directors will then need a different boom length to
  match the new cell, and the optimizer will move them.

### Regression
All 19 existing tests still pass.
