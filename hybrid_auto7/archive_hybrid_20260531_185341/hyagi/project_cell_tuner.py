import random

from .project import load_project, apply_element_overrides, save_project
from .dynamic import generate_starting_model
from .dynamic_sim import generated_to_elements
from .model import Element, validate_elements, generate_nec_text
from .config import AntennaConfig, frange
from .engine import NecppEngine
from .physics import summarize
from .paths import MODELS_DIR, ensure_dirs


def _safe_name(name: str) -> str:
    return str(name).replace("/", "_").replace("\\", "_").replace(" ", "_")


def find_element(elements, name):
    target = str(name).upper().strip()
    for e in elements:
        if str(e.name).upper() == target:
            return e
    raise RuntimeError(f"Element {name} not found")


def override_element(elements, name, position=None, length=None):
    out = []
    found = False
    target = str(name).upper().strip()

    for e in elements:
        if str(e.name).upper() == target:
            found = True
            out.append(
                Element(
                    name=e.name,
                    position_in=e.position_in if position is None else float(position),
                    length_in=e.length_in if length is None else float(length),
                )
            )
        else:
            out.append(e)

    if not found:
        raise RuntimeError(f"Element {name} not found")

    return out


def set_cell_positions(elements, de_pos, xsp, csp):
    out = elements
    out = override_element(out, "DE", position=de_pos)
    out = override_element(out, "XFRMR", position=de_pos - xsp)
    out = override_element(out, "COUPLER", position=de_pos + csp)
    return out


def score_candidate(summary):
    score = 0.0
    score += summary.points_under_2p0 * 220.0
    score += summary.points_under_1p5 * 160.0
    score -= summary.max_swr * 180.0
    score -= summary.avg_swr * 80.0
    score -= summary.avg_abs_x * 8.0

    if summary.max_swr > 2.0:
        score -= 8000.0
    elif summary.max_swr > 1.5:
        score -= 1500.0

    return score


def centered_values(center, half_width, step, low=None, high=None):
    start = center - half_width
    stop = center + half_width
    if low is not None:
        start = max(low, start)
    if high is not None:
        stop = min(high, stop)
    return frange(start, stop, step)


def _make_ant(cfg):
    return AntennaConfig(
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


def _save_cell_overrides(cfg, elements):
    for name in ("XFRMR", "DE", "COUPLER"):
        e = find_element(elements, name)
        cfg.element_overrides[name] = {
            "position_in": float(e.position_in),
            "length_in": float(e.length_in),
        }
    save_project(cfg)


def _run_candidate(elements, ant, freqs, engine):
    validate_elements(elements, ant)
    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)
    return summary


def _best_cell_geometry(elements):
    x = find_element(elements, "XFRMR")
    de = find_element(elements, "DE")
    c = find_element(elements, "COUPLER")
    xsp = de.position_in - x.position_in
    csp = c.position_in - de.position_in
    return x, de, c, xsp, csp


def _try_de_seed_positions(best_elements, ant, freqs, engine):
    seed_positions = [36.0, 42.0, 48.0, 54.0, 60.0, 66.0, 72.0]
    random.shuffle(seed_positions)

    best_score = None
    best_info = None

    print()
    print("Stage 0: DE seed exploration (3ft to 6ft from REF)")
    print("Trying DE seeds with XFRMR=13in behind and COUPLER=13in ahead")
    print(f"Seed order: {seed_positions}")

    for de_pos in seed_positions:
        try:
            elements = set_cell_positions(best_elements, de_pos, 13.0, 13.0)
            summary = _run_candidate(elements, ant, freqs, engine)
            score = score_candidate(summary)

            print(
                f"Seed DE={de_pos:.1f} in "
                f"maxSWR={summary.max_swr:.3f} "
                f"avgSWR={summary.avg_swr:.3f} "
                f"|X|={summary.avg_abs_x:.3f} "
                f"score={score:.1f}"
            )

            if best_score is None or score > best_score:
                best_score = score
                best_info = (elements, summary, score)

        except Exception as exc:
            print(f"FAILED seed DE={de_pos:.1f}: {exc}")

    return best_info


def tune_project_cell(project_name, level="quick", procedure="legacy_hybrid", repeat_pass=False):
    cfg = load_project(project_name)

    if cfg.mode != "hybrid":
        raise NotImplementedError("Project cell tuner currently supports hybrid mode only.")

    generated = generate_starting_model(
        element_count=cfg.element_count,
        mode=cfg.mode,
        freq_start_mhz=cfg.freq_start_mhz,
        freq_stop_mhz=cfg.freq_stop_mhz,
        boom_length_ft=cfg.boom_length_ft,
    )

    apply_element_overrides(cfg, generated)
    base_elements = generated_to_elements(generated)

    ant = _make_ant(cfg)
    engine = NecppEngine()

    f_step = 0.01
    freqs = frange(cfg.freq_start_mhz, cfg.freq_stop_mhz, f_step)

    validate_elements(base_elements, ant)

    if level == "deep":
        broad_spacing_half = 12.0
        broad_spacing_step = 1.0
        broad_pos_half = 12.0
        broad_pos_step = 1.0
        broad_len_half = 15.0
        broad_len_step = 1.0
        broad_de_half = 15.0
        broad_de_step = 1.0
        fine_spacing_half = 3.0
        fine_spacing_step = 0.5
        fine_len_half = 4.0
        fine_len_step = 0.5
        fine_de_half = 4.0
        fine_de_step = 0.5
        final_pos_half = 3.0
        final_pos_step = 0.5
    elif level == "normal":
        broad_spacing_half = 10.0
        broad_spacing_step = 1.0
        broad_pos_half = 10.0
        broad_pos_step = 1.0
        broad_len_half = 12.0
        broad_len_step = 1.0
        broad_de_half = 12.0
        broad_de_step = 1.0
        fine_spacing_half = 2.0
        fine_spacing_step = 0.5
        fine_len_half = 3.0
        fine_len_step = 0.5
        fine_de_half = 3.0
        fine_de_step = 0.5
        final_pos_half = 2.0
        final_pos_step = 0.5
    else:
        broad_spacing_half = 8.0
        broad_spacing_step = 2.0
        broad_pos_half = 9.0
        broad_pos_step = 2.0
        broad_len_half = 10.0
        broad_len_step = 2.0
        broad_de_half = 10.0
        broad_de_step = 2.0
        fine_spacing_half = 2.0
        fine_spacing_step = 1.0
        fine_len_half = 2.0
        fine_len_step = 1.0
        fine_de_half = 2.0
        fine_de_step = 1.0
        final_pos_half = 2.0
        final_pos_step = 1.0

    if repeat_pass:
        broad_spacing_half = max(2.0, fine_spacing_half)
        broad_spacing_step = fine_spacing_step
        broad_pos_half = max(2.0, final_pos_half)
        broad_pos_step = final_pos_step
        broad_len_half = max(2.0, fine_len_half)
        broad_len_step = fine_len_step
        broad_de_half = max(2.0, fine_de_half)
        broad_de_step = fine_de_step

    print()
    print("Project cell tuner (match-only mode)")
    print("====================================")
    print(f"Project:      {project_name}")
    print(f"Level:        {level}")
    print(f"Procedure:    {procedure}")
    print(f"Repeat pass:  {repeat_pass}")
    print(f"Ground mode:  {cfg.ground_mode}")
    print()

    best_elements = base_elements

    if procedure == "cell_then_directors_repeat" and not repeat_pass:
        seed_best = _try_de_seed_positions(best_elements, ant, freqs, engine)
        if seed_best is not None:
            best_elements, best_summary, best_score = seed_best
        else:
            best_summary = _run_candidate(best_elements, ant, freqs, engine)
            best_score = score_candidate(best_summary)
    else:
        best_summary = _run_candidate(best_elements, ant, freqs, engine)
        best_score = score_candidate(best_summary)

    print(f"Baseline score: {best_score:.1f}")

    def maybe_update(elements):
        nonlocal best_score, best_elements, best_summary
        summary = _run_candidate(elements, ant, freqs, engine)
        score = score_candidate(summary)
        if score > best_score:
            best_score = score
            best_elements, best_summary = elements, summary

    if procedure == "cell_then_directors_repeat":
        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        xsp_vals = centered_values(xsp, broad_spacing_half, broad_spacing_step, low=2.0, high=45.0)
        print("Stage 1: XFRMR spacing")
        for xs in xsp_vals:
            try:
                maybe_update(set_cell_positions(best_elements, de.position_in, xs, csp))
            except Exception:
                pass

        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        csp_vals = centered_values(csp, broad_spacing_half, broad_spacing_step, low=4.0, high=80.0)
        print("Stage 2: COUPLER spacing")
        for cs in csp_vals:
            try:
                maybe_update(set_cell_positions(best_elements, de.position_in, xsp, cs))
            except Exception:
                pass

        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        de_pos_vals = centered_values(de.position_in, broad_pos_half, broad_pos_step, low=10.0, high=ant.boom_length_in - 10.0)
        print("Stage 3: DE position only")
        for dp in de_pos_vals:
            try:
                maybe_update(override_element(best_elements, "DE", position=dp))
            except Exception:
                pass

        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        xl_vals = centered_values(x.length_in, broad_len_half, broad_len_step, low=120.0, high=280.0)
        print("Stage 4: XFRMR length")
        for xl in xl_vals:
            try:
                maybe_update(override_element(best_elements, "XFRMR", length=xl))
            except Exception:
                pass

        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        cl_vals = centered_values(c.length_in, broad_len_half, broad_len_step, low=120.0, high=280.0)
        print("Stage 5: COUPLER length")
        for cl in cl_vals:
            try:
                maybe_update(override_element(best_elements, "COUPLER", length=cl))
            except Exception:
                pass

        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        de_len_vals = centered_values(de.length_in, broad_de_half, broad_de_step, low=120.0, high=280.0)
        print("Stage 6: DE length")
        for dl in de_len_vals:
            try:
                maybe_update(override_element(best_elements, "DE", length=dl))
            except Exception:
                pass

        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        cell_pos_vals = centered_values(de.position_in, final_pos_half, final_pos_step, low=10.0, high=ant.boom_length_in - 10.0)
        print("Stage 7: whole cell move")
        for dp in cell_pos_vals:
            try:
                maybe_update(set_cell_positions(best_elements, dp, xsp, csp))
            except Exception:
                pass

        if repeat_pass:
            x, de, c, xsp, csp = _best_cell_geometry(best_elements)
            xsp_vals = centered_values(xsp, fine_spacing_half, fine_spacing_step, low=2.0, high=45.0)
            print("Stage 8: repeat XFRMR spacing")
            for xs in xsp_vals:
                try:
                    maybe_update(set_cell_positions(best_elements, de.position_in, xs, csp))
                except Exception:
                    pass

            x, de, c, xsp, csp = _best_cell_geometry(best_elements)
            csp_vals = centered_values(csp, fine_spacing_half, fine_spacing_step, low=4.0, high=80.0)
            print("Stage 9: repeat COUPLER spacing")
            for cs in csp_vals:
                try:
                    maybe_update(set_cell_positions(best_elements, de.position_in, xsp, cs))
                except Exception:
                    pass

            x, de, c, xsp, csp = _best_cell_geometry(best_elements)
            de_pos_vals = centered_values(de.position_in, final_pos_half, final_pos_step, low=10.0, high=ant.boom_length_in - 10.0)
            print("Stage 10: repeat DE position only")
            for dp in de_pos_vals:
                try:
                    maybe_update(override_element(best_elements, "DE", position=dp))
                except Exception:
                    pass

            x, de, c, xsp, csp = _best_cell_geometry(best_elements)
            xl_vals = centered_values(x.length_in, fine_len_half, fine_len_step, low=120.0, high=280.0)
            print("Stage 11: repeat XFRMR length")
            for xl in xl_vals:
                try:
                    maybe_update(override_element(best_elements, "XFRMR", length=xl))
                except Exception:
                    pass

            x, de, c, xsp, csp = _best_cell_geometry(best_elements)
            cl_vals = centered_values(c.length_in, fine_len_half, fine_len_step, low=120.0, high=280.0)
            print("Stage 12: repeat COUPLER length")
            for cl in cl_vals:
                try:
                    maybe_update(override_element(best_elements, "COUPLER", length=cl))
                except Exception:
                    pass

            x, de, c, xsp, csp = _best_cell_geometry(best_elements)
            de_len_vals = centered_values(de.length_in, fine_de_half, fine_de_step, low=120.0, high=280.0)
            print("Stage 13: repeat DE length")
            for dl in de_len_vals:
                try:
                    maybe_update(override_element(best_elements, "DE", length=dl))
                except Exception:
                    pass

            x, de, c, xsp, csp = _best_cell_geometry(best_elements)
            cell_pos_vals = centered_values(de.position_in, final_pos_half, final_pos_step, low=10.0, high=ant.boom_length_in - 10.0)
            print("Stage 14: repeat whole cell move")
            for dp in cell_pos_vals:
                try:
                    maybe_update(set_cell_positions(best_elements, dp, xsp, csp))
                except Exception:
                    pass

    else:
        x, de, c, xsp, csp = _best_cell_geometry(best_elements)
        xsp_vals = centered_values(xsp, broad_spacing_half, broad_spacing_step, low=2.0, high=45.0)
        csp_vals = centered_values(csp, broad_spacing_half, broad_spacing_step, low=4.0, high=80.0)

        print("Stage 1: spacing")
        for xs in xsp_vals:
            for cs in csp_vals:
                try:
                    maybe_update(set_cell_positions(best_elements, de.position_in, xs, cs))
                except Exception:
                    pass

    ensure_dirs()
    nec = generate_nec_text(
        elements=best_elements,
        ant=ant,
        f_start=cfg.freq_start_mhz,
        f_stop=cfg.freq_stop_mhz,
        f_step=f_step,
    )
    suffix = "cell_repeat_best" if repeat_pass else "cell_best"
    nec_file = MODELS_DIR / f"project_{_safe_name(project_name)}_{suffix}.nec"
    nec_file.write_text(nec, encoding="utf-8")

    _save_cell_overrides(cfg, best_elements)

    x, de, c, xsp, csp = _best_cell_geometry(best_elements)

    print()
    print("Best project cell candidate")
    print("===========================")
    print(f"Project:             {project_name}")
    print(f"Procedure:           {procedure}")
    print(f"Repeat pass:         {repeat_pass}")
    print(f"Ground mode:         {cfg.ground_mode}")
    print(f"Score:               {best_score:.1f}")
    print(f"XFRMR position:      {x.position_in:.3f} in")
    print(f"DE position:         {de.position_in:.3f} in")
    print(f"COUPLER position:    {c.position_in:.3f} in")
    print(f"XFRMR-DE spacing:    {xsp:.3f} in")
    print(f"DE-COUPLER spacing:  {csp:.3f} in")
    print(f"XFRMR length:        {x.length_in:.3f} in")
    print(f"DE length:           {de.length_in:.3f} in")
    print(f"COUPLER length:      {c.length_in:.3f} in")
    print(f"Max SWR:             {best_summary.max_swr:.3f}")
    print(f"Avg SWR:             {best_summary.avg_swr:.3f}")
    print(f"Points <= 1.5:       {best_summary.points_under_1p5}")
    print(f"Points <= 2.0:       {best_summary.points_under_2p0}")
    print(f"Avg R:               {best_summary.avg_r:.3f} ohm")
    print(f"Avg |X|:             {best_summary.avg_abs_x:.3f} ohm")
    print(f"Saved NEC:           {nec_file}")
    print("Saved best cell into project overrides.")

    return {
        "project": project_name,
        "procedure": procedure,
        "repeat_pass": repeat_pass,
        "score": best_score,
        "xfrmr_position_in": x.position_in,
        "de_position_in": de.position_in,
        "coupler_position_in": c.position_in,
        "xfrmr_spacing_in": xsp,
        "coupler_spacing_in": csp,
        "xfrmr_length_in": x.length_in,
        "de_length_in": de.length_in,
        "coupler_length_in": c.length_in,
        "summary": best_summary,
        "nec_file": str(nec_file),
    }
