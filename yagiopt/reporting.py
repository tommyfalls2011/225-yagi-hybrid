import numpy as np

from .constants import RL_REPORT_CLIP_DB, RL_REWARD_CLIP_DB
from .geometry import ftin, y_positions_from_spacings
from .rfmath import (
    return_loss_db_raw,
    return_loss_db,
    swr_from_z,
    mismatch_efficiency_percent,
)
from .search import dedupe_logbook


def print_stage_result(title, rec):
    m = rec["metrics"]
    print(f"\n{title}")
    print("=" * len(title))
    print(f"Center Z: {m['center_R']:.4f} {m['center_X']:+.4f}j ohms")
    print(f"Center RL(raw): {m['center_rl_raw']:.2f} dB")
    print(f"Center SWR:     {m['center_swr']:.3f}")
    print(f"Local min RL*:  {m['local_min_rl']:.2f} dB")
    print(f"Local mean RL*: {m['local_mean_rl']:.2f} dB")
    print(f"Local max SWR:  {m['local_max_swr']:.3f}")
    print(f"Bandwidth RL>=target:  {m['bandwidth_mhz']:.3f} MHz")
    if "bandwidth15_mhz" in m:
        print(f"Bandwidth RL>=15 dB:   {m.get('bandwidth15_mhz', 0.0):.3f} MHz")
    if "bandwidth12_mhz" in m:
        print(f"Bandwidth RL>=12 dB:   {m.get('bandwidth12_mhz', 0.0):.3f} MHz")
    if np.isfinite(m["forward_gain_db"]):
        print(f"Forward gain:   {m['forward_gain_db']:.2f} dB")
        print(f"Rear gain:      {m['rear_gain_db']:.2f} dB")
        print(f"Front/back:     {m['front_to_back_db']:.2f} dB")
    if "stage_cost" in rec:
        print(f"Stage cost: {rec['stage_cost']:.4f}")
    if "score" in rec:
        print(f"Layout score: {rec['score']:.4f}")
    print("* RL values here use reward clip at {:.1f} dB".format(RL_REWARD_CLIP_DB))


def print_search_record(rec, prefix=""):
    if rec is None:
        return
    m = rec["metrics"]
    move_note = f" | {rec['note']}" if rec.get("note") else ""
    print(
        f"{prefix}{rec['label']}: "
        f"score={rec['score']:.3f}, "
        f"SWR={m['center_swr']:.3f}, "
        f"RL={m['center_rl_raw']:.2f} dB, "
        f"localRL={m['local_min_rl']:.2f} dB, "
        f"BW={m['bandwidth_mhz']:.3f} MHz, "
        f"Gain={m['forward_gain_db']:.2f} dB, "
        f"F/B={m['front_to_back_db']:.2f} dB"
        f"{move_note}"
    )


def print_top_layouts(logbook, top_n=12):
    uniq = dedupe_logbook(logbook)
    uniq.sort(key=lambda r: r["score"], reverse=True)

    print("\nTOP LOGGED LAYOUTS")
    print("==================")
    if not uniq:
        print("No valid layouts were logged.")
        return

    print("Rank  Score      SWR    RL(dB)  LocalRL  BW(MHz)  Gain(dB)  F/B(dB)  Accepted  Note")
    for i, rec in enumerate(uniq[:top_n], start=1):
        m = rec["metrics"]
        print(
            f"{i:>4d}  "
            f"{rec['score']:>8.3f}  "
            f"{m['center_swr']:>5.3f}  "
            f"{m['center_rl_raw']:>6.2f}  "
            f"{m['local_min_rl']:>7.2f}  "
            f"{m['bandwidth_mhz']:>7.3f}  "
            f"{m['forward_gain_db']:>8.2f}  "
            f"{m['front_to_back_db']:>7.2f}  "
            f"{str(rec['accepted']):>8s}  "
            f"{rec.get('note', '')}"
        )


def print_design(lengths, spacings, height, element_names,
                 reflector_min_over_de_ft, reflector_max_over_de_ft,
                 taper_center_diameter_in, taper_outer_diameter_in):
    y = y_positions_from_spacings(spacings)

    print("\nBEST GEOMETRY")
    print("=============")
    print(f"Height: {height:.4f} ft    {ftin(height)}")
    print(f"Boom length REF to D5: {np.sum(spacings):.4f} ft    {ftin(np.sum(spacings))}")
    print(f"Element taper: center {taper_center_diameter_in:.3f} in OD, outer {taper_outer_diameter_in:.3f} in OD")
    print(
        "Reflector over DE allowed range: "
        f"{reflector_min_over_de_ft*12.0:.1f} in to {reflector_max_over_de_ft*12.0:.1f} in"
    )
    print()

    print("Elements:")
    print("  Name    Length decimal ft      Length ft/in        Position from REF")
    for name, L, pos in zip(element_names, lengths, y):
        print(f"  {name:>3s}    {L:10.4f} ft      {ftin(L):>16s}      {ftin(pos):>16s}")

    print()
    print("Spacings:")
    for i, s in enumerate(spacings):
        print(f"  {element_names[i]} to {element_names[i+1]}: {s:.4f} ft    {ftin(s)}")


def print_center_result(lengths, spacings, height, center_freq, use_real_ground, gain_enabled,
                        solve_impedance_fn, estimate_pattern_fn):
    zc = solve_impedance_fn(lengths, spacings, height, center_freq, use_real_ground)
    rl_raw = return_loss_db_raw(zc)
    rl_report = return_loss_db(zc, clip_db=RL_REPORT_CLIP_DB)
    swr = swr_from_z(zc)
    eta = mismatch_efficiency_percent(zc)
    if not np.isfinite(eta):
        eta = 0.0

    print("\nCENTER-FREQUENCY RESULT")
    print("=======================")
    print(f"Center frequency: {center_freq:.3f} MHz")
    print(f"Feed Z: {zc.real:.4f} {zc.imag:+.4f}j ohms")
    print(f"Return loss (report clipped): {rl_report:.2f} dB")
    print(f"Return loss (raw):            {rl_raw:.2f} dB")
    print(f"SWR: {swr:.4f}")
    print(f"Mismatch efficiency: {eta:.4f} %")

    if gain_enabled:
        fwd_gain, rear_gain, f2b = estimate_pattern_fn(lengths, spacings, height, center_freq, use_real_ground)
        if np.isfinite(fwd_gain):
            print(f"Forward gain proxy (+Y): {fwd_gain:.2f} dB")
            print(f"Rear gain proxy (max rear-cone): {rear_gain:.2f} dB")
            print(f"Front-to-back proxy:     {f2b:.2f} dB")


def print_sweep_summary(freqs, z, rl_report, rl_raw, swr, eta, target_rl_db, center_freq):
    z = np.asarray(z, dtype=complex)
    rl_report = np.asarray(rl_report, dtype=float)
    rl_raw = np.asarray(rl_raw, dtype=float)
    swr = np.asarray(swr, dtype=float)
    eta = np.asarray(eta, dtype=float)
    freqs = np.asarray(freqs, dtype=float)

    valid = (
        np.isfinite(z.real) &
        np.isfinite(z.imag) &
        np.isfinite(rl_report) &
        np.isfinite(rl_raw) &
        np.isfinite(swr) &
        np.isfinite(eta)
    )

    print("\nSWEEP SUMMARY")
    print("=============")
    print(f"Frequency range: {freqs[0]:.3f} to {freqs[-1]:.3f} MHz")
    print(f"Center target:   {center_freq:.3f} MHz")
    print(f"RL report clip:  {RL_REPORT_CLIP_DB:.1f} dB")
    print(f"RL reward clip:  {RL_REWARD_CLIP_DB:.1f} dB")

    if not np.any(valid):
        print("No valid sweep points were produced.")
        return

    rl_reward = np.minimum(np.where(np.isfinite(rl_raw), rl_raw, -20.0), RL_REWARD_CLIP_DB)

    from .sweep import approximate_bandwidth
    bw, f1, f2 = approximate_bandwidth(freqs, rl_reward, target_rl_db)

    valid_idx = np.where(valid)[0]
    best_idx = valid_idx[np.argmax(rl_report[valid])]
    worst_idx = valid_idx[np.argmin(rl_report[valid])]
    center_idx = int(np.argmin(np.abs(freqs - center_freq)))

    print(f"Best return loss:  {rl_report[best_idx]:.2f} dB at {freqs[best_idx]:.3f} MHz")
    print(f"Worst return loss: {rl_report[worst_idx]:.2f} dB at {freqs[worst_idx]:.3f} MHz")
    print(f"Average RL:        {np.mean(rl_report[valid]):.2f} dB")
    print(f"Average SWR:       {np.mean(swr[valid]):.2f}")
    print(f"Worst SWR:         {np.max(swr[valid]):.2f}")
    print(f"Average mismatch efficiency: {np.mean(eta[valid]):.2f} %")

    if valid[center_idx]:
        print(
            f"At center {freqs[center_idx]:.3f} MHz: "
            f"RL(report) {rl_report[center_idx]:.2f} dB, "
            f"RL(raw) {rl_raw[center_idx]:.2f} dB, "
            f"SWR {swr[center_idx]:.3f}, "
            f"Z = {z[center_idx].real:.4f} {z[center_idx].imag:+.4f}j"
        )
    else:
        print(f"At center {freqs[center_idx]:.3f} MHz: no valid sampled point")

    if bw > 0:
        print(f"Bandwidth with RL >= {target_rl_db:.1f} dB: {bw:.3f} MHz, {f1:.3f} to {f2:.3f} MHz")
    else:
        print(f"No sampled bandwidth met RL >= {target_rl_db:.1f} dB")

    print()
    print("Selected frequency points:")
    print("  MHz       R+jX ohms             RL dB     SWR     Eff %")

    sample_idx = np.unique(np.linspace(0, len(freqs) - 1, min(9, len(freqs))).astype(int))
    for idx in sample_idx:
        fi = freqs[idx]
        zi = z[idx]
        rr = rl_report[idx] if np.isfinite(rl_report[idx]) else np.nan
        ss = swr[idx] if np.isfinite(swr[idx]) else 999.0
        ee = eta[idx] if np.isfinite(eta[idx]) else 0.0
        zr = zi.real if np.isfinite(zi.real) else np.nan
        zx = zi.imag if np.isfinite(zi.imag) else np.nan
        print(f"  {fi:6.3f}   {zr:8.2f} {zx:+8.2f}j   {rr:7.2f}   {ss:6.2f}   {ee:7.2f}")
