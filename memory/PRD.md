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

## NEXT
- Streamlit Auto-Learn button for hybrid (pending user 1a/1b choice).
- Optionally rescue 1071 hybrid runs from misnamed yagi_history.db.
- Seeder supports N=2..12; UI slider goes to 18 - cap UI to 12 or extend seeding.
