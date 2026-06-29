## boom_free_v4.patch — FREE mode = fresh start + always adopt result

**Apply on top of `boom_free_v3.patch`** (which you've already applied).

```bash
cd ~/scripts
git pull
git apply boom_free_v4.patch
# restart Streamlit
```

### What was wrong (corrected diagnosis)

You said: *"I never added 294" — it's under FIXED, not FREE."*

You're right.  The 294" was leftover on disk from a previous **FIXED**-mode
tune.  My v3 patch made it WORSE because it added a "compression sniff-test"
that decided your 294" gaps were "already healthy" and **didn't reseed at all**.
So FREE mode kept inheriting your FIXED geometry.

### What FREE mode now does (correct behavior)

When `boom_mode=free`:

1. **Discard the on-disk director positions** at the start of every tune.
   They came from a previous FIXED tune and have nothing to do with FREE.
2. **Reseed** director gaps to the rules midpoints as a neutral fresh start
   (XFRMR / DE / COUPLER tight cell still untouched).
3. **Optimize** from that fresh start.
4. **Adopt the optimizer's result UNCONDITIONALLY** — no silent revert to
   any "baseline".  Whatever the optimizer ends with is what you see.

FIXED mode is unchanged — your hand-locked geometry is still protected by the
baseline-revert safety, so a worse tune can't overwrite your locked design.

### What you'll see in the log

```
[boom-free] FRESH START -- discarded leftover director positions from disk
            (these were from your previous FIXED tune).  Reseeded director
            gaps to rules midpoints (XFRMR/COUPLER cell untouched) ->
            starting span 304.37".  Optimizer free to grow or shrink from
            here -- result will NOT revert to the old disk geometry.

[matcher] done  band_max_swr=1.286  ...
[adopt] FREE mode -- adopting whatever the optimizer found.  boom span
        294.00" -> 304.37" (delta +10.37").
```

So you'll see the boom span actually change between runs, and the Result
panel will reflect the optimizer's geometry — not a stale FIXED leftover.

### Note on the SWR

In FREE mode the result may have a slightly higher band-max than your
FIXED tune did (the previous FIXED 294" was a tight local optimum).  That
is the intentional trade-off of FREE: you wanted the optimizer to pick
the boom, so you get its pick, even if it's not the global minimum.  Use
`Search restarts ≥ 2` to let the optimizer try multiple random perturbations
and find better basins.

### Regression
All 19 existing tests still pass.
