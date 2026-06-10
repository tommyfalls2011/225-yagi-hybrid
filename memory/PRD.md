# Antenna Designer (Yagi + Hybrid) — PRD / Working Notes

## Problem statement
User (tommyfalls2011) has a Python3 antenna-design toolset (Yagi + hybrid) that
optimizes CB-band antennas via NEC (necpp). Complaint: "can't get a single tune
done correct" — SWR not < 1.5 across the CB band, and poor gain / front-to-back
even when SWR looks ok. Prior agents (OpenAI, Opus 4.6/4.7) failed.

## Source
- Their code lives on their Ubuntu machine (~/scripts) and was pushed (code-only,
  ~88MB) to GitHub: https://github.com/tommyfalls2011/225-yagi-hybrid
- Cloned here to /app/src_repo for analysis (necpp builds/installs fine).

## Architecture
- Standalone Yagi optimizers: opt_7el_yagi.py / 2 / 3 (3 = newest, 27.195 MHz).
- Modular yagiopt/ package (geometry, rfmath, nec_engine, search, strategies...).
- hybrid_auto7/: Streamlit app + hyagi/ engine (physics, tuner, pattern, etc.).
- Engine: necpp (NEC-2++ python bindings).

## Targets (from user)
- Default center 27.195 MHz, REAL ground, maximize gain + F/B, keep SWR < 1.5.
- Wants frequency selectable anywhere ~25-30 MHz, up to 400 MHz, and 6m (50 MHz).

## ROOT CAUSES FOUND (opt_7el_yagi3.py) — FIXED 2026-06-02
1. apply_ground(): GN card argument order WRONG for real2/real0. epsr/sigma were
   placed in F3/F4 (second-medium) slots, leaving real ground with epsr=0,sigma=0
   -> invalid ground -> NaN impedance/pattern. Fixed to F1/F2 slots.
2. Gain reader: never called nec_gain with its required 4 args
   (nec, freq_idx, theta_idx, phi_idx) and used a malformed RP card -> gain probe
   always failed -> "gain-aware search DISABLED" -> optimizer tuned SWR only.
   Replaced with a single full-hemisphere RP solve + correct nec_gain readback,
   plus ground fallback (real2->real0->perfect->free) for pattern.

## VALIDATION (real ground, --fast)
- Preflight: gain support now DETECTED/enabled.
- Tune @ 27.195 MHz: SWR 1.008, Z=49.88+0.40j, fwd gain 16.14 dBi, F/B 16.56 dB,
  SWR<1.5 ~26.6-27.5 MHz. CORRECT TUNE.

## Delivered
- Fix committed in /app/src_repo; patch handed to user to apply + push.

## BACKLOG / Next
- P0: Multi-band support 25-400 MHz — seed + geometry bounds + taper sections are
  hardcoded for 27 MHz; need wavelength-scaled seed/bounds when --center-freq set.
- P1: Apply same gain/ground audit to opt_7el_yagi2.py, opt_7el_yagi.py, yagiopt/.
- P1: Hybrid app (hybrid_auto7/) — audit hyagi/ engine (physics/pattern/tuner) for
  same GN-card / gain-reading issues; align hybrid rules vs yagi rules.
- P2: Clean junk files committed to repo (datetime ~55MB, re ~13MB shell-redirect
  artifacts).

## HYBRID self-learning (2026-06-03)
- DB audit: yagi_history.db (3.2M, 1071 runs) actually holds HYBRID data (REF/XFRMR/DE/COUPLER/DIR..) - misnamed. auto7_history.db was EMPTY (user cleared it). Yagi opt_7el_yagi3.py saves nothing. yagiopt/history.py schema != actual file.
- Hybrid engines: engine.py (necpp, impedance only, ground OK) + v2_runner.py (nec2c binary, impedance+gain, GN/RP text cards OK). nec2c installed via apt here.
- Old "learning": Run page -> learning_v2.json (JSON, not SQL). Manual "adopt geometry". No closed loop. move_history/insert_run never called.
- BUILT: hyagi/auto_learn.py (closed-loop self-learner) + auto_learn_run.py (CLI).
  Loop: warm-start from DB best (by project signature) -> run procedure (v2_runner) -> fine band sweep -> save EVERY gen to auto7_history.db (runs+elements+freq_results) -> learn (narrow search around proven values, MoveMemory) -> auto-adopt -> stop at SWR<=target across band or plateau(patience) or max gens.
  Added v2_runner.EVAL_FREQ_POINTS (full-band scoring) + v2_scorer wideband_1.2 profile (steep) + SWR-first adoption rule.
- VALIDATED here: baseline band_max_swr 1.894 -> 1.30 (XFRMR+COUPLER proc) ; saved 3 runs/24 elements/27 freqs to DB; learning narrowed search gen2. Reaching exactly <=1.2 depends on procedure+physical geometry (user expertise) - engine works.
- Target per user: SWR<=1.2 wideband. Stop defaults: target 1.2, patience 3.

## NEXT
- Tune/extend procedures (position + spacing sweeps) to reliably cross 1.2 on real builds; let user pick procedure.
- Optional: Streamlit "Auto-Learn" button wiring run_learning into Run page.
- Rescue 1071 hybrid runs from misnamed yagi_history.db -> proper hybrid DB (user said start clean, so optional).
- Yagi opt_7el_yagi3.py self-learning (deferred per user; hybrid first).

## YAGI-2 fixes (2026-06-03, via Streamlit "Yagi Designer" = opt_7el_yagi2.py + yagiopt)
- PRE-EXISTING bug (not introduced by us): hardcoded-7-element assumptions crashed any N!=7:
  * yagiopt/geometry.py unpack_design (x[13] height) -> N-agnostic (len//2)
  * yagiopt/geometry.py move_element_position (1..6, spacings[5]) -> N-agnostic
  * opt_7el_yagi2.py get_stage_active_idx ([0,1,2,7,8,13], range(14)) -> derived from N
  Fix verified: 6-el tune completes -> SWR 1.167, gain 15.1 dBi, F/B 17 dB. (commit b0ed333)
- Yagi history/learning was DEAD: opt_7el_yagi2 + yagiopt/history.py pointed at ~/scripts/yagi_history.db
  = the MISNAMED HYBRID db (wrong schema) -> 'no such column: timestamp/center_freq_mhz' on read+write.
  Fix: point Yagi to its own yagi_opt_history.db (history.py _DEFAULT_DB + read default + save msg).
  Also learn-seed score floor was 100 but good designs score ~ -90 -> lowered min_score default to -1e9
  (rank by score+freq match), min_gain 15->10. Verified warm-start: run#1 saves -> run#2 '[learn] using
  best as seed: run #1' and seeds from its geometry. (commit c8bc1d7)
- DB map now: yagi_history.db = OLD hybrid data (misnamed, untouched) ; auto7_history.db = hybrid auto_learn ;
  yagi_opt_history.db = NEW clean Yagi optimizer history (self-learning).

## WIDEBAND MATCHER + SWR PLATEAU FIX (2026-06-03)
- USER TARGET CONFIRMED: wideband SWR <= 1.2 across the FREEBAND 26.665-27.855 MHz
  (not just the 40-ch 26.965-27.405). rules_v2.json global low/high updated to freeband.
- ROOT CAUSE of the 1.334 plateau: the old per-element greedy procedure (cell_tune_3x)
  got trapped in a local minimum AND it was only scoring the narrower 40-ch band, so the
  steep band-edge SWR rise on the freeband was never fought. Raw current geometry over the
  freeband peaked at SWR 3.44 (matched too low, high-Q, R-vs-freq slope).
- FIX (delivered as /app/wideband_matcher.patch, applies cleanly on clean HEAD):
  * v2_runner.build_nec_card(pattern=) + new v2_runner.band_swr_curve() = fast SWR-only eval
    (single-direction RP, ~0.7s vs 2.1s; ~3x faster) for the inner search loop.
  * NEW hyagi/match_opt.py = coordinate-descent wideband matcher. Objective = WORST in-band
    SWR (+tiny avg tiebreak). Tunes DE len + REF/XFRMR/COUPLER lengths & DE-relative spacings
    + director lengths, multi-resolution steps (8->0.25 in), rounds-to-convergence, optional
    perturbation restarts. Then _polish_gain() recovers gain/F-B on REF+director lengths under
    the FULL pattern, rejecting any move that lifts band-max SWR above target.
  * auto_learn.run_learning() now uses the matcher (cfg.use_matcher=True default) instead of the
    greedy procedure; warm-start + DB saving (runs/elements/freq_results) + final pattern eval kept.
  * auto_learn_run.py: --band-low/--band-high/--no-matcher/--no-polish flags.
  * NEW pages/8_Auto_Learn.py = Streamlit "Auto-Learn" page (band, target, height, points,
    restarts, polish; live log; SWR curve chart; adopt/download). Wired to the same engine.
  * diag_sweep.py = standalone band-SWR diagnostic tool.
- VALIDATED (real ground, 30 ft, freeband 26.665-27.855, 21 pts):
  baseline band-max SWR 3.44 -> 1.196 (<=1.2 target MET), gain 14.73 dBi, F/B 12.81 dB,
  center R=57.7 X=5.5. SWR-only base pass alone hits 1.067 (then gain polish trades back up
  to the 1.20 ceiling for max gain). DB saved 2 runs/16 els/42 freq pts; warm-start from DB
  confirmed across runs and live in the UI. Patch re-verified on a clean checkout.
- DELIVERY: /app/wideband_matcher.patch (git apply). Recommend "Save to Github" to avoid the
  large-paste corruption the user hit before. Do NOT direct-push (user revoked PAT).

## SMART GROUP-MATCH LOOPING PROCEDURE (2026-06-09)
- Added `smart_group_match_4x` to data/procedures_v2.json: one-click looping
  Group-Match methodology per user's spoken sequence (place cell -> place ref ->
  move cell -> set DIR1-3 -> slide dirs for best return loss/X -> move cell+dirs ->
  retune DE resonance -> recover loss on dir lengths). `repeat: 4`,
  `repeat_min_improve: 0.3` (auto-stops when a pass no longer improves composite).
- Steps map to existing mini-tunes (sweep_XFRMR/COUPLER_pos_wide, nudge/tune REF,
  group_cell_move, tune_DIR1-3 pos/len, window2_dirs_move (match mode),
  all_dirs_move, tune_DE_length_resonance, window2_dirs_length).
- run_procedure loop support (repeat/repeat_min_improve) already in v2_runner.
- TESTS: tests/test_smart_group_match.py (3 passing) — JSON valid, all 16 steps
  resolve, repeat loop executes + early-stops. Smoke-ran the group/window steps
  end-to-end on real geometry (nec2c) OK.
- PENDING (user deferred): P1 delete duplicate UI pages 6_Run.py/7_Learning.py
  (keep 8_Auto_Learn.py); P2 remove git junk blobs `datetime` (~55MB) & `re`
  (~13MB) + empty stray files. User confirmed PAT is valid (not revoked).

## TUNING-MATH AUDIT + USER'S REAL 7-EL BUILD (2026-06-09)
- User reported the procedure (smart_group_match_4x) gave SWR 1.421 after ~102 min.
  Diagnosed: that procedure is the greedy/local-minimum path; matcher hits ~1.20.
- AUDITED the physics. Found + FIXED real bugs in hyagi/v2_runner.py (pushed):
  1. parse_nec_output now returns PER-FREQUENCY pattern blocks (was merging every
     frequency's RP into one list -> gain/F-B compared forward peak of one freq vs
     rear lobe of another). evaluate() now reads gain/F-B from the centre-freq block.
  2. F/B measured at the forward MAIN-LOBE elevation (+/-10 deg), not over all
     elevations (which conflated high-angle rear lobes). F/B on seed went 9.76 -> 18.24 dB.
  3. centre R/X/SWR interpolated at the TRUE operating centre (freq_mhz_center,
     27.195) instead of the band midpoint sample (27.26). Added _interp_rx().
  - perf_report.py + diag_sweep.py updated to flatten the new block format.
  - TESTS: tests/test_physics_math.py (3 passing).
  3rd fix: rules_v2.json DIR1_DIR2 min spacing 48->40 (was REJECTING the user's
     real 45.25" DIR1-DIR2 spacing in validate()).
- TOPOLOGY confirmed by user: DE insulated + coax-fed; XFRMR/COUPLER grounded to
  boom at centre (~= floating parasitic for symmetric mode, so current model OK).
  User plans to change the grounding later -> revisit model then.
- USER'S REAL ANTENNA (7-element, 3 directors), given by user:
  lengths REF223 XFRMR199 DE210 COUPLER173 DIR1 194.5 DIR2 188 DIR3 182.5;
  spacings XFRMR-DE 6.5, DE-COUPLER 23, COUPLER-DIR1 73.5, DIR1-DIR2 45.25,
  DIR2-DIR3 75; boom 22 ft -> SOLVED REF->XFRMR = 40.75" (REF at 0, DIR3 at 264).
  Model (fixed) predicts as-built dips ~26.6 MHz (low), SWR 1.84 @27.195 rising to
  ~3 @27.855; user confirmed model matches bench and asked to retune lengths to 27.195.
- RETUNED (lengths-only, spacings preserved as built) -> band-max SWR 1.21,
  centre 1.21, gain 14.36 dBi, F/B 15.4 dB. Loaded into data/current_geometry_v2.json
  and pushed. (Full matcher that also moves spacings gave 1.20 / 13.9 / 14.7.)
- ENV: nec2c binary keeps VANISHING from this pod (recurring). Reinstall on demand:
  sudo apt-get install -y nec2c. Not a code bug; user's own Ubuntu unaffected.
- STILL PENDING (user deferred): delete duplicate UI pages; remove git junk blobs.
  Open question for user: exact tubing OD/section schedule (taper_v2.json still the
  0.625"/0.5" placeholder) — user said model matches bench so taper accepted for now.


## NEXT
- Issue 2 (P1): confirm 14-15 dBi over real ground is physical (free-space ~12-13 dBi + up to
  ~6 dB ground reflection at peak elevation => 14-15 dBi realistic; quick free-space vs ground
  compare in v2_runner still TODO to document).
- Yagi UI slider 2-18 vs seeder N<=12 (P2): cap UI to 12 or extend seeding.
- Optionally rescue 1071 hybrid runs from misnamed yagi_history.db.
- Yagi opt_7el_yagi3.py self-learning (deferred per user; hybrid first).
