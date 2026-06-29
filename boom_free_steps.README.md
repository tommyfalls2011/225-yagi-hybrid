## boom_free_steps.patch — FREE-mode optimizer can now actually walk the boom

**Apply this on top of `boom_free_fix.patch` (you've already applied that one).**

```bash
cd ~/scripts
git pull
git apply boom_free_steps.patch
# verify it landed (expect 2 hits)
grep -n "free_boom" hybrid_auto7/hyagi/match_opt.py | head -5
# restart Streamlit
```

### What it fixes (the second-layer issue from your last tune log)

The reseed (`boom_free_fix.patch`) plants the optimizer at a healthy starting
spread.  But coordinate descent uses step sizes `(8.0, 4.0, 2.0, 1.0, 0.5,
0.25)` for the matcher and `(2.0, 1.0, 0.5)` for the beam phase.  In your last
run those steps were **too small** to walk director gaps — the log showed
the beam phase rejecting **14 + 38 + 52 position probes** because none of
them escaped the local SWR basin:

```
[beam] step=2.0 gain=15.29 fb=21.16  rej-swr 14 ceiling 1.54
[beam] step=1.0 gain=15.29 fb=22.69  rej-swr 38 ceiling 1.54
[beam] step=0.5 gain=15.29 fb=22.76  rej-swr 52 ceiling 1.54
```

End result: the boom never moves significantly even though it's nominally FREE.

### What this patch does

When `boom_max_in <= 0` (FREE mode), it multiplies step sizes **4×** for:

- Director-to-director spacing DOFs in `_descend` (`sp_DIR1`, `sp_DIR2`, ...)
- The `ref_gap` (REF→DE)
- Director **position** moves in `_optimize_beam`

So with the existing `(8.0, 4.0, 2.0, 1.0, 0.5, 0.25)` step tuple, FREE-mode
spacings effectively probe at `(32, 16, 8, 4, 2, 1)`.  That gives the
optimizer enough reach to actually walk a director from 50" out to 100" if
that helps SWR — instead of being stuck nudging ±2".

### What it does NOT touch

- XFRMR/COUPLER tight-cell gaps (`xf_gap`, `cp_gap`) — they stay at the
  normal step size, so the wideband resonator triple is unaffected
- Element LENGTHS — still tuned at the normal step size (lengths are
  sub-inch sensitive to frequency tuning)
- FIXED mode — `free_boom=False` when `boom_max_in > 0`, so behavior is
  unchanged when the user has the boom locked

### What you should see in the next tune log

The beam phase rejection count should drop sharply because the bigger probes
will find spacings outside the local SWR basin.  The boom span may actually
change between runs as the optimizer explores.

### Regression
All 19 existing tests still pass (`test_free_boom_expands.py`,
`test_boom_cap.py`, `test_hybrid_seed.py`).
