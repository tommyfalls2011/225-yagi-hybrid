import argparse

from .paths import print_paths


def add_level_arg(parser, default="quick"):
    parser.add_argument("--level", choices=["quick", "normal", "deep"], default=default)



def main():
    parser = argparse.ArgumentParser(
        description="hybrid_auto7 clean automatic 7-element antenna tuner"
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("paths", help="show project paths")
    p.set_defaults(func=cmd_paths)

    p = sub.add_parser("hello", help="test command")
    p.set_defaults(func=cmd_hello)

    p = sub.add_parser("model-test", help="test 7-element tapered model")
    p.set_defaults(func=cmd_model_test)

    p = sub.add_parser("sim-test", help="run one NEC/necpp baseline simulation")
    p.set_defaults(func=cmd_sim_test)

    p = sub.add_parser("autotune", help="run automatic staged tuner")
    add_level_arg(p)
    p.set_defaults(func=cmd_autotune)

    p = sub.add_parser("best", help="show best database rows")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_best)

    p = sub.add_parser("inspect", help="inspect one run id")
    p.add_argument("run_id", type=int)
    p.set_defaults(func=cmd_inspect)


    p = sub.add_parser("pattern-test", help="calculate gain/F-B/beamwidth for best or selected run")
    p.add_argument("--run-id", type=int, default=None)
    p.add_argument("--freq", type=float, default=27.185)
    p.set_defaults(func=cmd_pattern_test)


    p = sub.add_parser("tune-dir1", help="sweep DIR1 position/length for gain while protecting SWR")
    add_level_arg(p)
    p.set_defaults(func=cmd_tune_dir1)


    p = sub.add_parser("build", help="show build sheet for selected run id")
    p.add_argument("run_id", type=int)
    p.set_defaults(func=cmd_build)


    p = sub.add_parser("tune-dir2", help="sweep DIR2 position/length for gain while protecting SWR")
    p.add_argument("--base-run", type=str, default="auto", help="run id or \"auto\" to pick latest DIR1 winner")
    add_level_arg(p)
    p.set_defaults(func=cmd_tune_dir2)


    p = sub.add_parser("tune-dir3", help="sweep DIR3 position/length for gain while protecting SWR")
    p.add_argument("--base-run", type=str, default="auto", help="run id or \"auto\" to pick latest DIR1 winner")
    add_level_arg(p)
    p.set_defaults(func=cmd_tune_dir3)

    p = sub.add_parser("build-best", help="show build sheet for best run")
    p.set_defaults(func=cmd_build_best)


    p = sub.add_parser("necpp-info", help="show available necpp functions")
    p.set_defaults(func=cmd_necpp_info)


    p = sub.add_parser("new-project", help="create a user antenna project")
    p.add_argument("--name", required=True)
    p.add_argument("--elements", type=int, default=7)
    p.add_argument("--mode", choices=["hybrid"], default="hybrid")
    p.add_argument("--freq-start", type=float, default=26.965)
    p.add_argument("--freq-stop", type=float, default=27.405)
    p.add_argument("--target-z", type=float, default=50.0)
    p.add_argument("--height-ft", type=float, default=36.0)
    p.add_argument("--boom-length-ft", type=float, default=30.0)
    p.add_argument("--boom-diameter-in", type=float, default=2.0)
    p.add_argument("--center-od-in", type=float, default=0.625)
    p.add_argument("--outer-od-in", type=float, default=0.500)
    p.add_argument("--center-half-len-in", type=float, default=36.0)
    p.add_argument("--target-max-swr", type=float, default=1.5)
    p.add_argument("--min-front-back-db", type=float, default=20.0)
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_new_project)

    p = sub.add_parser("show-project", help="show a saved project")
    p.add_argument("name")
    p.set_defaults(func=cmd_show_project)

    p = sub.add_parser("list-projects", help="list saved projects")
    p.set_defaults(func=cmd_list_projects)

    p = sub.add_parser("set-champion", help="attach a champion run id to a project")
    p.add_argument("name")
    p.add_argument("--run-id", type=int, required=True)
    p.set_defaults(func=cmd_set_champion)


    p = sub.add_parser("roles", help="show dynamic roles for 3-12 element antenna")
    p.add_argument("--elements", type=int, required=True)
    p.add_argument("--mode", choices=["hybrid"], default="hybrid")
    p.set_defaults(func=cmd_roles)

    p = sub.add_parser("project-model", help="show generated starting model for a project")
    p.add_argument("name")
    p.set_defaults(func=cmd_project_model)


    p = sub.add_parser("design", help="run design workflow for a saved project")
    p.add_argument("--project", required=True)
    add_level_arg(p)
    p.set_defaults(func=cmd_design)


    p = sub.add_parser("project-sim", help="simulate generated starting model for a project")
    p.add_argument("name")
    p.set_defaults(func=cmd_project_sim)


    p = sub.add_parser("project-tune-director", help="tune one director in a dynamic project starting model")
    p.add_argument("name")
    p.add_argument("--director", required=True)
    add_level_arg(p)
    p.set_defaults(func=cmd_project_tune_director)


    p = sub.add_parser("set-element", help="set project element position/length override")
    p.add_argument("name")
    p.add_argument("--element", required=True)
    p.add_argument("--position-in", type=float, default=None)
    p.add_argument("--length-in", type=float, default=None)
    p.set_defaults(func=cmd_set_element)

    args = parser.parse_args()
    args.func(args)


def cmd_paths(args):
    print_paths()


def cmd_hello(args):
    print("hybrid_auto7 is installed and running")

def _get_run_by_id(db_module, run_id):
    for attr in ("run_by_id", "get_run", "fetch_run", "find_run"):
        func = getattr(db_module, attr, None)
        if callable(func):
            return func(run_id)

    rows = db_module.best_rows(1000000)
    for row in rows:
        if row["id"] == run_id:
            return row
    return None


def _print_build_sheet(r, elements, return_loss_db, title):
    print()
    print(title)
    print("=" * len(title))
    print(f"Stage:              {r['stage']}")
    print(f"DE position:        {r['de_position_in']:.3f} in from REF")
    print(f"XFRMR-DE spacing:   {r['xfrmr_spacing_in']:.3f} in")
    print(f"DE-Coupler spacing: {r['coupler_spacing_in']:.3f} in")
    print(f"XFRMR length:       {r['xfrmr_length_in']:.3f} in")
    print(f"Coupler length:     {r['coupler_length_in']:.3f} in")
    print(f"DE length:          {r['de_length_in']:.3f} in")
    print()
    print("SWR summary")
    print("-----------")
    print(f"Min SWR:            {r['min_swr']:.3f}")
    print(f"Max SWR:            {r['max_swr']:.3f}")
    print(f"Avg SWR:            {r['avg_swr']:.3f}")
    print(f"Worst return loss:  {return_loss_db(r['max_swr']):.2f} dB")
    print(f"Points <= 1.5:      {r['points_under_1p5']}")
    print(f"Points <= 2.0:      {r['points_under_2p0']}")
    print()
    print("Elements")
    print("--------")
    print("Element    Position from REF in   Spacing in   Length in   Half length in")

    prev = None
    for e in elements:
        pos = e["position_in"]
        spacing = 0.0 if prev is None else pos - prev
        print(
            f"{e['name']:<8s} "
            f"{pos:20.3f} "
            f"{spacing:12.3f} "
            f"{e['length_in']:11.3f} "
            f"{e['length_in']/2.0:16.3f}"
        )
        prev = pos

    print()
    print("Taper model")
    print("-----------")
    print("5/8 inch OD center section, 36 inches each side from boom center.")
    print("1/2 inch OD outer sections from 36 inches to each tip.")




def cmd_model_test(args):
    from .config import AntennaConfig, Design
    from .model import (
        build_elements,
        validate_elements,
        build_wires,
        generate_nec_text,
    )

    ant = AntennaConfig()
    design = Design()

    elements = build_elements(design)
    validate_elements(elements, ant)

    wires, feed_tag, feed_seg = build_wires(elements, ant)

    print("Model test")
    print("==========")
    print(f"Elements: {len(elements)}")
    print(f"Wires:    {len(wires)}")
    print(f"Feed tag: {feed_tag}")
    print(f"Feed seg: {feed_seg}")
    print()

    print("Elements")
    print("--------")

    prev = None
    for e in elements:
        spacing = 0.0 if prev is None else e.position_in - prev
        print(
            f"{e.name:<8s} "
            f"pos={e.position_in:8.3f} in "
            f"spacing={spacing:8.3f} in "
            f"length={e.length_in:8.3f} in"
        )
        prev = e.position_in

    nec = generate_nec_text(
        elements=elements,
        ant=ant,
        f_start=26.965,
        f_stop=27.405,
        f_step=0.01,
    )

    print()
    print(f"NEC lines: {len(nec.splitlines())}")
    print("First 8 NEC lines:")
    for line in nec.splitlines()[:8]:
        print(line)

    print()
    print("OK")


def cmd_sim_test(args):
    from .config import AntennaConfig, Design, frange
    from .model import (
        build_elements,
        validate_elements,
        design_key,
        generate_nec_text,
    )
    from .engine import NecppEngine
    from .physics import summarize, return_loss_db
    from .paths import MODELS_DIR, ensure_dirs
    from . import db

    ensure_dirs()

    ant = AntennaConfig()
    design = Design()

    f_start = 26.965
    f_stop = 27.405
    f_step = 0.01

    key = design_key(design, f_start, f_stop, f_step)
    existing = db.existing_run(key)

    if existing is not None:
        print(f"Existing run found: id={existing['id']}")
        print(f"Max SWR: {existing['max_swr']:.3f}")
        return

    elements = build_elements(design)
    validate_elements(elements, ant)

    nec = generate_nec_text(elements, ant, f_start, f_stop, f_step)
    nec_file = MODELS_DIR / f"{key}.nec"
    nec_file.write_text(nec, encoding="utf-8")

    freqs = frange(f_start, f_stop, f_step)

    print("Running baseline simulation...")
    print(f"Frequency points: {len(freqs)}")

    engine = NecppEngine()
    results = engine.evaluate(elements, ant, freqs)
    summary = summarize(results)

    run_id = db.insert_run(
        design_key=key,
        stage="sim_test_baseline",
        design=design,
        f_start=f_start,
        f_stop=f_stop,
        f_step=f_step,
        summary=summary,
        elements=elements,
        results=results,
        nec_file=nec_file,
    )

    print()
    print("Simulation complete")
    print("===================")
    print(f"Run id:          {run_id}")
    print(f"Min SWR:         {summary.min_swr:.3f}")
    print(f"Max SWR:         {summary.max_swr:.3f}")
    print(f"Avg SWR:         {summary.avg_swr:.3f}")
    print(f"Worst RL:        {return_loss_db(summary.max_swr):.2f} dB")
    print(f"Avg R:           {summary.avg_r:.3f} ohm")
    print(f"Avg |X|:         {summary.avg_abs_x:.3f} ohm")
    print(f"Points <= 1.5:   {summary.points_under_1p5}")
    print(f"Points <= 2.0:   {summary.points_under_2p0}")
    print(f"NEC file:        {nec_file}")


def cmd_autotune(args):
    from .tuner import AutoTuner

    AutoTuner(level=args.level).autotune()


def cmd_best(args):
    from . import db
    from .physics import return_loss_db

    rows = db.best_rows(args.limit)

    if not rows:
        print("No runs in database yet.")
        return

    print()
    print("Best runs")
    print("=========")

    for r in rows:
        print(
            f"id={r['id']:4d} "
            f"stage={r['stage']} "
            f"DEpos={r['de_position_in']:.3f} "
            f"Xsp={r['xfrmr_spacing_in']:.3f} "
            f"Csp={r['coupler_spacing_in']:.3f} "
            f"XL={r['xfrmr_length_in']:.3f} "
            f"CL={r['coupler_length_in']:.3f} "
            f"DE={r['de_length_in']:.3f} "
            f"min={r['min_swr']:.3f} "
            f"max={r['max_swr']:.3f} "
            f"avg={r['avg_swr']:.3f} "
            f"RLmax={return_loss_db(r['max_swr']):.2f}dB "
            f"<=1.5={r['points_under_1p5']} "
            f"<=2.0={r['points_under_2p0']}"
        )


def cmd_inspect(args):
    from . import db

    rows = db.freqs_for_run(args.run_id)

    if not rows:
        print(f"No frequency results for run id={args.run_id}")
        return

    print()
    print(f"Frequency results for run id={args.run_id}")
    print("=======================================")
    print("Freq MHz       R ohm       X ohm        SWR")

    for r in rows:
        print(
            f"{r['freq_mhz']:8.3f} "
            f"{r['r_ohm']:11.2f} "
            f"{r['x_ohm']:11.2f} "
            f"{r['swr_50']:10.3f}"
        )


def cmd_build_best(args):
    from . import db
    from .physics import return_loss_db

    r = db.best_run()

    if r is None:
        print("No runs in database yet.")
        return

    elements = db.elements_for_run(r["id"])
    _print_build_sheet(r, elements, return_loss_db, f"Build sheet for best run id={r['id']}")


def cmd_necpp_info(args):
    from .necpp_info import show_necpp_info

    show_necpp_info()


def cmd_pattern_test(args):
    from .pattern import pattern_for_best, pattern_for_run

    if args.run_id is None:
        row, pat = pattern_for_best(freq_mhz=args.freq)
    else:
        row, pat = pattern_for_run(args.run_id, freq_mhz=args.freq)

    print()
    print("Pattern test")
    print("============")
    print(f"Run id:              {row['id']}")
    print(f"Stage:               {row['stage']}")
    print(f"Frequency:           {pat.freq_mhz:.3f} MHz")
    print()
    print("Design")
    print("------")
    print(f"DE position:         {row['de_position_in']:.3f} in")
    print(f"XFRMR-DE spacing:    {row['xfrmr_spacing_in']:.3f} in")
    print(f"DE-Coupler spacing:  {row['coupler_spacing_in']:.3f} in")
    print(f"XFRMR length:        {row['xfrmr_length_in']:.3f} in")
    print(f"Coupler length:      {row['coupler_length_in']:.3f} in")
    print(f"DE length:           {row['de_length_in']:.3f} in")
    print()
    print("Pattern")
    print("-------")
    print(f"Forward gain +Y:     {pat.forward_gain_dbi:.3f} dBi")
    print(f"Rear gain -Y:        {pat.rear_gain_dbi:.3f} dBi")
    print(f"Front/back:          {pat.front_back_db:.3f} dB")
    print(f"Max gain:            {pat.max_gain_dbi:.3f} dBi")
    print(f"Max gain phi:        {pat.max_gain_phi_deg:.1f} deg")
    if pat.beamwidth_deg is None:
        print("Beamwidth:           not found")
    else:
        print(f"Beamwidth approx:    {pat.beamwidth_deg:.1f} deg")




# AUTO_BASE_v1: auto-pick latest valid DIR1/CELL winner as base run
def _auto_pick_base_run(prefer_stage_prefix=("dir1", "cell", "ref")):
    import sqlite3, os
    db = os.path.expanduser("~/scripts/hybrid_auto7/data/auto7_history.db")
    con = sqlite3.connect(db)
    cur = con.execute(
        "SELECT id, stage, max_swr FROM runs "
        "WHERE max_swr < 2.0 AND points_under_1p5 >= 40 AND status='DONE' "
        "ORDER BY id DESC LIMIT 30"
    )
    rows = cur.fetchall()
    con.close()
    for pref in prefer_stage_prefix:
        for rid, stg, swr in rows:
            if stg and stg.lower().startswith(pref):
                print(f"[auto-base] picked run {rid} (stage={stg} max_swr={swr:.3f})")
                return rid
    if rows:
        rid, stg, swr = rows[0]
        print(f"[auto-base] fallback to latest valid run {rid} (stage={stg} max_swr={swr:.3f})")
        return rid
    raise RuntimeError("auto-base: no valid runs found (need max_swr<2.0 + 40+ pts under 1.5)")


def cmd_tune_dir1(args):
    from .dir1_tuner import tune_dir1

    tune_dir1(level=args.level)


def cmd_build(args):
    from . import db
    from .physics import return_loss_db

    r = _get_run_by_id(db, args.run_id)

    if r is None:
        print(f"No run found for id={args.run_id}")
        return

    elements = db.elements_for_run(r["id"])
    _print_build_sheet(r, elements, return_loss_db, f"Build sheet for run id={r['id']}")


def cmd_tune_dir2(args):
    from .dir2_tuner import tune_dir2

    base = _auto_pick_base_run() if args.base_run == "auto" else int(args.base_run)
    tune_dir2(base_run_id=base, level=args.level)


def cmd_tune_dir3(args):
    from .dir3_tuner import tune_dir3

    base = _auto_pick_base_run() if args.base_run == "auto" else int(args.base_run)
    tune_dir3(base_run_id=base, level=args.level)


def cmd_new_project(args):
    from .project import ProjectConfig, save_project, print_project

    cfg = ProjectConfig(
        name=args.name,
        element_count=args.elements,
        mode=args.mode,
        freq_start_mhz=args.freq_start,
        freq_stop_mhz=args.freq_stop,
        target_z_ohm=args.target_z,
        height_ft=args.height_ft,
        boom_length_ft=args.boom_length_ft,
        boom_diameter_in=args.boom_diameter_in,
        center_od_in=args.center_od_in,
        outer_od_in=args.outer_od_in,
        center_half_len_in=args.center_half_len_in,
        target_max_swr=args.target_max_swr,
        min_front_back_db=args.min_front_back_db,
        notes=args.notes,
    )

    path = save_project(cfg)
    print(f"Saved project: {path}")
    print_project(cfg)


def cmd_show_project(args):
    from .project import load_project, print_project

    cfg = load_project(args.name)
    print_project(cfg)


def cmd_list_projects(args):
    from .project import list_projects

    projects = list_projects()

    if not projects:
        print("No projects saved yet.")
        return

    print()
    print("Saved projects")
    print("==============")

    for cfg in projects:
        print(
            f"{cfg.name:24s} "
            f"elements={cfg.element_count:2d} "
            f"mode={cfg.mode:6s} "
            f"band={cfg.freq_start_mhz:.3f}-{cfg.freq_stop_mhz:.3f} MHz "
            f"boom={cfg.boom_length_ft:.1f} ft "
            f"champion={cfg.champion_run_id}"
        )


def cmd_set_champion(args):
    from .project import set_champion, load_project, print_project

    path = set_champion(args.name, args.run_id)
    print(f"Updated project champion: {path}")

    cfg = load_project(args.name)
    print_project(cfg)


def cmd_roles(args):
    from .dynamic import print_roles

    print_roles(args.elements, args.mode)


def cmd_project_model(args):
    from .project import load_project, apply_element_overrides
    from .dynamic import generate_starting_model, print_generated_model

    cfg = load_project(args.name)

    elements = generate_starting_model(
        element_count=cfg.element_count,
        mode=cfg.mode,
        freq_start_mhz=cfg.freq_start_mhz,
        freq_stop_mhz=cfg.freq_stop_mhz,
        boom_length_ft=cfg.boom_length_ft,
    )

    apply_element_overrides(cfg, elements)

    print()
    print(f"Project model: {cfg.name}")
    print("=" * (15 + len(cfg.name)))
    print(f"Elements: {cfg.element_count}")
    print(f"Mode:     {cfg.mode}")
    print(f"Band:     {cfg.freq_start_mhz:.3f}-{cfg.freq_stop_mhz:.3f} MHz")

    print_generated_model(elements, cfg.boom_length_ft)


def cmd_design(args):
    from .design import run_design

    run_design(project_name=args.project, level=args.level)


def cmd_project_sim(args):
    from .dynamic_sim import project_sim

    project_sim(args.name)


def cmd_project_tune_director(args):
    from .project_director_tuner import tune_project_director

    tune_project_director(
        project_name=args.name,
        director=args.director,
        level=args.level,
    )


def cmd_set_element(args):
    from .project import set_element_override, load_project, print_project

    if args.position_in is None and args.length_in is None:
        print("Nothing to set. Use --position-in and/or --length-in.")
        return

    set_element_override(
        project_name=args.name,
        element_name=args.element,
        position_in=args.position_in,
        length_in=args.length_in,
    )

    cfg = load_project(args.name)
    print_project(cfg)
