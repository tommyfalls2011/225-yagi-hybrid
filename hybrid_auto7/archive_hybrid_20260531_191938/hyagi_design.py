from .project import load_project, print_project, apply_element_overrides, save_project
from .dynamic import generate_starting_model, print_generated_model, roles_for
from .project_cell_tuner import tune_project_cell
from .project_director_tuner import tune_project_director
from .ref_length_tuner import tune_project_ref
from .dynamic_sim import project_sim


def run_design(project_name, level="quick"):
    cfg = load_project(project_name)

    print()
    print("DESIGN PROJECT")
    print("==============")
    print_project(cfg)

    if cfg.mode != "hybrid":
        raise NotImplementedError("Only hybrid mode is active right now.")

    roles = roles_for(cfg.element_count, cfg.mode)

    print()
    print("Dynamic role map")
    print("----------------")
    print(f"User element count:     {cfg.element_count}")
    print(f"Physical element count: {len(roles)}")
    for i, r in enumerate(roles, start=1):
        print(f"{i:2d}: {r}")

    elements = generate_starting_model(
        element_count=cfg.element_count,
        mode=cfg.mode,
        freq_start_mhz=cfg.freq_start_mhz,
        freq_stop_mhz=cfg.freq_stop_mhz,
        boom_length_ft=cfg.boom_length_ft,
    )

    apply_element_overrides(cfg, elements)

    print()
    print("Generated starting model")
    print("------------------------")
    print_generated_model(elements, cfg.boom_length_ft)

    print()
    print("Selected tuning procedure")
    print("-------------------------")
    print(cfg.tuning_procedure)
    print()

    print(f"Running tuning workflow at level: {level}")
    print()

    cell_result = tune_project_cell(
        project_name,
        level=level,
        procedure=cfg.tuning_procedure,
        repeat_pass=False,
    )

    cfg = load_project(project_name)
    roles = roles_for(cfg.element_count, cfg.mode)
    director_roles = [r for r in roles if r.startswith("DIR")]

    if cfg.tuning_procedure == "cell_then_directors_repeat" and director_roles:
        coupler = cfg.element_overrides.get("COUPLER")
        if coupler and "position_in" in coupler:
            dir1_seed_pos = float(coupler["position_in"]) + 36.0
            cfg.element_overrides["DIR1"] = {
                **cfg.element_overrides.get("DIR1", {}),
                "position_in": dir1_seed_pos,
            }
            save_project(cfg)
            print()
            print(f"DIR1 seeded to {dir1_seed_pos:.3f} in (3 ft ahead of coupler) before director tuning.")

    director_results = []

    for director in director_roles:
        print()
        print(f"Now tuning {director} ...")
        result = tune_project_director(
            project_name=project_name,
            director=director,
            level=level,
        )
        director_results.append(result)

    # DIR_PAIR_COTUNE_v1: pair co-tune to escape single-element greedy local optima
    pair_results = []
    if len(director_roles) >= 2 and cfg.tuning_procedure == "cell_then_directors_repeat":
        from .director_pair_tuner import tune_project_director_pair
        for _i in range(len(director_roles) - 1):
            _a, _b = director_roles[_i], director_roles[_i + 1]
            print()
            print(f"Now co-tuning director pair {_a}+{_b} ...")
            pair_results.append(
                tune_project_director_pair(
                    project_name=project_name,
                    dir_a=_a,
                    dir_b=_b,
                    level=level,
                )
            )

    repeat_cell_result = None

    print()
    print("Tuning REF length (constrained to DE_len+4..DE_len+24) ...")
    ref_result = tune_project_ref(project_name, level=level)

    if cfg.tuning_procedure == "cell_then_directors_repeat":
        print()
        print("Repeating cell tuning with directors left in place ...")
        repeat_cell_result = tune_project_cell(
            project_name,
            level=level,
            procedure="cell_then_directors_repeat",
            repeat_pass=True,
        )

    print()
    print("Running final project simulation ...")
    final_summary, final_pattern = project_sim(project_name)

    print()
    print("Generalized design run complete.")
    print("Use:")
    print(f"  python3 ./run.py show-project {project_name}")
    print(f"  python3 ./run.py project-model {project_name}")
    print(f"  python3 ./run.py project-sim {project_name}")

    return {
        "cell_result": cell_result,
        "director_results": director_results,
        "repeat_cell_result": repeat_cell_result,
        "final_summary": final_summary,
        "final_pattern": final_pattern,
    }
