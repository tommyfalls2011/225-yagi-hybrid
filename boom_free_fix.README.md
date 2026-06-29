## boom_free_fix.patch — FREE boom mode no longer stuck at the previously-locked length

Apply on top of `wideband_matcher.patch` (or on top of an already-patched repo):

```bash
git apply boom_free_fix.patch
# regression test (requires the nec2c binary on $PATH)
python -m pytest tests/test_free_boom_expands.py -v
```

### What it fixes
Switching boom mode FIXED → FREE used to leave the optimizer stuck at the
previously locked length. The hard constraint was off (`boom_max_in=0`
correctly bypasses the endpoint pin), but coordinate descent step sizes
(0.5"–8") cannot walk director gaps the 30–100" needed to reach a
healthier spread from a compressed starting geometry. Diagnostic showed
`sp_DIR1/2/3` DOFs were probed only ±4–8" out of an 80–120" rules
window before the descent gave up.

### What it does
In `optimize()`, when `boom_max_in <= 0` AND `tune_spacings=True`,
reseeds each director gap (and the REF gap) to the **midpoint** of its
rules spacing window **before** descent runs.

### What it does NOT touch
The XFRMR_DE and DE_COUPLER tight-cell spacings (4–32" rules) are
**intentionally not rewritten** — the hybrid/OWA wideband performance
comes from the tightly coupled XFRMR / DE / COUPLER resonator triple at
the user-tuned operating sweet spot (~5–7"). Only the REF gap and the
director chain are repositioned. After repositioning the array is shifted
so the leftmost element stays at position 0.

No-op for FIXED mode (`cap_in > 0`) and no-op when spacings are frozen.

### Test coverage
`hybrid_auto7/tests/test_free_boom_expands.py` adds two tests (both pass
in ~2 min with nec2c installed):

1. **FREE + compressed 183" start** → final span grows past 233"
2. **FIXED + same compressed start** → final span pinned to 240" cap
   (proves the fix is no-op in FIXED mode)

All 17 existing `test_boom_cap.py` + `test_hybrid_seed.py` tests still pass.
