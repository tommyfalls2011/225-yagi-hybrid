from .project import load_project, apply_element_overrides
from .dynamic import generate_starting_model, print_generated_model
from .model import Element, validate_elements, generate_nec_text
from .config import AntennaConfig, frange
from .engine import NecppEngine
from .physics import summarize, return_loss_db
from .paths import MODELS_DIR, ensure_dirs


def generated_to_elements(generated):
    return [
        Element(
            name=e.name,
            position_in=e.position_in,
            length_in=e.length_in,
        )
        for e in generated
    ]


def _safe_name(name: str) -> str:
    return str(name).replace("/", "_").replace("\\", "_").replace(" ", "_")


def project_sim(project_name):
    ensure_dirs()

    cfg = load_project(project_name)

    print()
    print(f"Dynamic project simulation: {cfg.name}")
    print("=" * (28 + len(cfg.name)))
    print(f"Elements user-count: {cfg.element_count}")
    print(f"Mode:                {cfg.mode}")
    print(f"Band:                {cfg.freq_start_mhz:.3f}-{cfg.freq_stop_mhz:.3f} MHz")
    print(f"Boom:                {cfg.boom_length_ft:.3f} ft")
    print(f"Ground mode:         {cfg.ground_mode}")
    print()

    generated = generate_starting_model(
        element_count=cfg.element_count,
        mode=cfg.mode,
        freq_start_mhz=cfg.freq_start_mhz,
        freq_stop_mhz=cfg.freq_stop_mhz,
        boom_length_ft=cfg.boom_length_ft,
    )

    apply_element_overrides(cfg, generated)
    print_generated_model(generated, cfg.boom_length_ft)

    elements = generated_to_elements(generated)

    ant = AntennaConfig(
        boom_length_in=cfg.boom_length_ft * 12.0,
        boom_diameter_in=cfg.boom_diameter_in,
        center_od_in=cfg.center_od_in,
        outer_od_in=cfg.outer_od_in,
        center_half_len_in=cfg.center_half_len_in,
        model_height_in=cfg.height_ft * 12.0,
        ground_mode=cfg.ground_mode,
        ground_epsr=cfg.ground_epsr,
        ground_sigma_s_per_m=cfg.ground_sigma_s_per_m,
        cell_mounting_style=cfg.cell_mounting_style,
    )

    validate_elements(elements, ant)

    f_step = 0.01
    freqs = frange(cfg.freq_start_mhz, cfg.freq_stop_mhz, f_step)

    nec = generate_nec_text(
        elements=elements,
        ant=ant,
        f_start=cfg.freq_start_mhz,
        f_stop=cfg.freq_stop_mhz,
        f_step=f_step,
    )

    nec_file = MODELS_DIR / f"project_{_safe_name(cfg.name)}_start.nec"
    nec_file.write_text(nec, encoding="utf-8")

    print()
    print("Running impedance simulation...")
    print(f"Frequency points: {len(freqs)}")

    engine = NecppEngine()
    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)

    print()
    print("Project starting model result")
    print("=============================")
    print(f"Min SWR:             {summary.min_swr:.3f}")
    print(f"Max SWR:             {summary.max_swr:.3f}")
    print(f"Avg SWR:             {summary.avg_swr:.3f}")
    print(f"Worst return loss:   {return_loss_db(summary.max_swr):.2f} dB")
    print(f"Points <= 1.5:       {summary.points_under_1p5}")
    print(f"Points <= 2.0:       {summary.points_under_2p0}")
    print(f"Avg R:               {summary.avg_r:.3f} ohm")
    print(f"Avg |X|:             {summary.avg_abs_x:.3f} ohm")
    print()
    print("Pattern at band center")
    print("----------------------")
    pattern = None
    try:
        from .pattern import evaluate_pattern_for_cell
        center_freq = (cfg.freq_start_mhz + cfg.freq_stop_mhz) / 2.0
        pattern = evaluate_pattern_for_cell(elements, center_freq, ant)
        print(f"Real gain (ground):   {pattern.real_gain_dbi:.2f} dBi")
        print(f"Peak elev angle:      {pattern.peak_elev_deg:.1f} deg")
        print(f"Peak azimuth:         {pattern.peak_phi_deg:.1f} deg")
        print(f"Front/back:           {pattern.front_back_db:.2f} dB")
        print(f"Beamwidth (3dB):      {pattern.beamwidth_deg:.1f} deg")
    except Exception as e:
        print(f"Pattern eval failed: {e}")
    print()
    print(f"NEC file:             {nec_file}")

    return summary, pattern
