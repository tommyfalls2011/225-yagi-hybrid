from dataclasses import replace
from itertools import product

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


def fmt(v, digits=3):
    if v is None:
        return "None"
    return f"{v:.{digits}f}"


def row_to_design(row):
    return Design(
        de_position_in=float(row["de_position_in"]),
        xfrmr_spacing_in=float(row["xfrmr_spacing_in"]),
        coupler_spacing_in=float(row["coupler_spacing_in"]),
        xfrmr_length_in=float(row["xfrmr_length_in"]),
        coupler_length_in=float(row["coupler_length_in"]),
        de_length_in=float(row["de_length_in"]),
    )


def print_best(row, title):
    if row is None:
        print()
        print(title)
        print("=" * len(title))
        print("No result.")
        return

    print()
    print(title)
    print("=" * len(title))
    print(f"id:              {row['id']}")
    print(f"stage:           {row['stage']}")
    print(f"DE position:     {fmt(row['de_position_in'])} in from REF")
    print(f"XFRMR spacing:   {fmt(row['xfrmr_spacing_in'])} in")
    print(f"Coupler spacing: {fmt(row['coupler_spacing_in'])} in")
    print(f"XFRMR length:    {fmt(row['xfrmr_length_in'])} in")
    print(f"Coupler length:  {fmt(row['coupler_length_in'])} in")
    print(f"DE length:       {fmt(row['de_length_in'])} in")
    print(f"Min SWR:         {fmt(row['min_swr'])}")
    print(f"Max SWR:         {fmt(row['max_swr'])}")
    print(f"Avg SWR:         {fmt(row['avg_swr'])}")
    print(f"Worst RL:        {fmt(return_loss_db(row['max_swr']), 2)} dB")
    print(f"Avg R:           {fmt(row['avg_r'])} ohm")
    print(f"Avg |X|:         {fmt(row['avg_abs_x'])} ohm")
    print(f"Points <= 1.5:   {row['points_under_1p5']}")
    print(f"Points <= 2.0:   {row['points_under_2p0']}")


def clamp_range(center, low, high, half_width, step):
    start = max(low, center - half_width)
    stop = min(high, center + half_width)
    return round(start, 3), round(stop, 3), step


class AutoTuner:
    def __init__(self, level="quick"):
        self.level = level
        self.ant = AntennaConfig()
        self.engine = NecppEngine()

        self.f_start = 26.965
        self.f_stop = 27.405
        self.f_step = 0.01
        self.freqs = frange(self.f_start, self.f_stop, self.f_step)

        if level == "deep":
            self.params = {
                "x": (3, 24, 1),
                "c": (8, 60, 2),
                "pos": (40, 130, 2),
                "xl": (175, 235, 5),
                "cl": (165, 225, 5),
                "de": (170, 240, 2),
                "fine_x": (5, 0.5),
                "fine_c": (10, 1),
                "fine_l": (10, 1),
                "fine_de": (10, 0.5),
                "fine_pos": (10, 1),
            }
        elif level == "normal":
            self.params = {
                "x": (3, 22, 1),
                "c": (8, 60, 2),
                "pos": (40, 125, 3),
                "xl": (180, 230, 5),
                "cl": (170, 220, 5),
                "de": (175, 235, 2),
                "fine_x": (4, 0.5),
                "fine_c": (8, 1),
                "fine_l": (8, 1),
                "fine_de": (8, 0.5),
                "fine_pos": (8, 1),
            }
        else:
            self.params = {
                "x": (4, 18, 2),
                "c": (12, 52, 4),
                "pos": (45, 115, 5),
                "xl": (185, 225, 10),
                "cl": (175, 215, 10),
                "de": (180, 230, 5),
                "fine_x": (4, 1),
                "fine_c": (8, 2),
                "fine_l": (8, 2),
                "fine_de": (8, 1),
                "fine_pos": (8, 2),
            }

    def best(self):
        return db.best_run()

    def run_design(self, design, stage):
        ensure_dirs()

        key = design_key(design, self.f_start, self.f_stop, self.f_step)
        existing = db.existing_run(key)

        if existing is not None:
            return existing

        elements = build_elements(design)
        validate_elements(elements, self.ant)

        nec = generate_nec_text(
            elements=elements,
            ant=self.ant,
            f_start=self.f_start,
            f_stop=self.f_stop,
            f_step=self.f_step,
        )

        nec_file = MODELS_DIR / f"{key}.nec"
        nec_file.write_text(nec, encoding="utf-8")

        results = self.engine.evaluate(elements, self.ant, self.freqs)
        summary = summarize(results)

        run_id = db.insert_run(
            design_key=key,
            stage=stage,
            design=design,
            f_start=self.f_start,
            f_stop=self.f_stop,
            f_step=self.f_step,
            summary=summary,
            elements=elements,
            results=results,
            nec_file=nec_file,
        )

        row = db.run_by_id(run_id)
        return row

    def run_grid(self, stage, depos, xsp, csp, xl, cl, de):
        depos_vals = frange(*depos)
        x_vals = frange(*xsp)
        c_vals = frange(*csp)
        xl_vals = frange(*xl)
        cl_vals = frange(*cl)
        de_vals = frange(*de)

        total = (
            len(depos_vals)
            * len(x_vals)
            * len(c_vals)
            * len(xl_vals)
            * len(cl_vals)
            * len(de_vals)
        )

        print()
        print("#" * 72)
        print(f"AUTO STAGE: {stage}")
        print("#" * 72)
        print(f"Total geometries: {total}")

        count = 0
        failed = 0

        for vals in product(depos_vals, x_vals, c_vals, xl_vals, cl_vals, de_vals):
            count += 1

            design = Design(
                de_position_in=vals[0],
                xfrmr_spacing_in=vals[1],
                coupler_spacing_in=vals[2],
                xfrmr_length_in=vals[3],
                coupler_length_in=vals[4],
                de_length_in=vals[5],
            )

            try:
                self.run_design(design, stage)
            except Exception as exc:
                failed += 1
                print(f"FAILED {count}/{total}: {exc}")
                continue

            if count == 1 or count == total or count % 25 == 0:
                b = self.best()
                print(
                    f"{count}/{total} "
                    f"best_id={b['id']} "
                    f"maxSWR={b['max_swr']:.3f} "
                    f"avgSWR={b['avg_swr']:.3f} "
                    f"<=2.0={b['points_under_2p0']} "
                    f"DEpos={b['de_position_in']:.1f} "
                    f"Xsp={b['xfrmr_spacing_in']:.1f} "
                    f"Csp={b['coupler_spacing_in']:.1f}"
                )

        b = self.best()
        print()
        print(f"Stage done. Failed: {failed}")
        print_best(b, f"Best after {stage}")
        return b

    def maybe_expand_spacing(self, best):
        p = self.params

        bx = float(best["xfrmr_spacing_in"])
        bc = float(best["coupler_spacing_in"])

        x0, x1, xs = p["x"]
        c0, c1, cs = p["c"]

        edge = False

        if abs(bx - x0) < 1e-9 or abs(bx - x1) < 1e-9:
            edge = True

        if abs(bc - c0) < 1e-9 or abs(bc - c1) < 1e-9:
            edge = True

        if not edge:
            return best

        print()
        print("Best spacing landed on sweep edge. Expanding spacing search.")

        ex = clamp_range(bx, 2, 45, 10, xs)
        ec = clamp_range(bc, 4, 80, 20, cs)

        return self.run_grid(
            "1b_edge_expanded_spacing",
            (61, 61, 1),
            ex,
            ec,
            (209, 209, 1),
            (197, 197, 1),
            (203, 203, 1),
        )

    def autotune(self):
        print()
        print("AUTOMATIC TRUE 7-ELEMENT TAPERED HYBRID YAGI TUNER")
        print("====================================================")
        print(f"Level: {self.level}")
        print(f"Band:  {self.f_start:.3f}-{self.f_stop:.3f} MHz")
        print(f"Points:{len(self.freqs)}")
        print()

        p = self.params

        # Stage 0: baseline
        self.run_grid(
            "0_baseline",
            (61, 61, 1),
            (12, 12, 1),
            (12, 12, 1),
            (209, 209, 1),
            (197, 197, 1),
            (203, 203, 1),
        )

        # Stage 1: coarse xfrmr/coupler spacing
        best = self.run_grid(
            "1_coarse_spacing",
            (61, 61, 1),
            p["x"],
            p["c"],
            (209, 209, 1),
            (197, 197, 1),
            (203, 203, 1),
        )

        best = self.maybe_expand_spacing(best)
        d = row_to_design(best)

        # Stage 2: move matching cell
        best = self.run_grid(
            "2_move_cell",
            p["pos"],
            (d.xfrmr_spacing_in, d.xfrmr_spacing_in, 1),
            (d.coupler_spacing_in, d.coupler_spacing_in, 1),
            (209, 209, 1),
            (197, 197, 1),
            (203, 203, 1),
        )
        d = row_to_design(best)

        # Stage 3: broad XFRMR/Coupler length sweep
        best = self.run_grid(
            "3_broad_xl_cl",
            (d.de_position_in, d.de_position_in, 1),
            (d.xfrmr_spacing_in, d.xfrmr_spacing_in, 1),
            (d.coupler_spacing_in, d.coupler_spacing_in, 1),
            p["xl"],
            p["cl"],
            (203, 203, 1),
        )
        d = row_to_design(best)

        # Stage 4: broad DE length sweep
        best = self.run_grid(
            "4_broad_de_length",
            (d.de_position_in, d.de_position_in, 1),
            (d.xfrmr_spacing_in, d.xfrmr_spacing_in, 1),
            (d.coupler_spacing_in, d.coupler_spacing_in, 1),
            (d.xfrmr_length_in, d.xfrmr_length_in, 1),
            (d.coupler_length_in, d.coupler_length_in, 1),
            p["de"],
        )
        d = row_to_design(best)

        # Stage 5: fine spacing
        hx, sx = p["fine_x"]
        hc, sc = p["fine_c"]

        best = self.run_grid(
            "5_fine_spacing",
            (d.de_position_in, d.de_position_in, 1),
            clamp_range(d.xfrmr_spacing_in, 2, 45, hx, sx),
            clamp_range(d.coupler_spacing_in, 4, 80, hc, sc),
            (d.xfrmr_length_in, d.xfrmr_length_in, 1),
            (d.coupler_length_in, d.coupler_length_in, 1),
            (d.de_length_in, d.de_length_in, 1),
        )
        d = row_to_design(best)

        # Stage 6: fine XL/CL
        hl, sl = p["fine_l"]

        best = self.run_grid(
            "6_fine_xl_cl",
            (d.de_position_in, d.de_position_in, 1),
            (d.xfrmr_spacing_in, d.xfrmr_spacing_in, 1),
            (d.coupler_spacing_in, d.coupler_spacing_in, 1),
            clamp_range(d.xfrmr_length_in, 140, 270, hl, sl),
            clamp_range(d.coupler_length_in, 130, 260, hl, sl),
            (d.de_length_in, d.de_length_in, 1),
        )
        d = row_to_design(best)

        # Stage 7: fine DE length
        hd, sd = p["fine_de"]

        best = self.run_grid(
            "7_fine_de_length",
            (d.de_position_in, d.de_position_in, 1),
            (d.xfrmr_spacing_in, d.xfrmr_spacing_in, 1),
            (d.coupler_spacing_in, d.coupler_spacing_in, 1),
            (d.xfrmr_length_in, d.xfrmr_length_in, 1),
            (d.coupler_length_in, d.coupler_length_in, 1),
            clamp_range(d.de_length_in, 140, 280, hd, sd),
        )
        d = row_to_design(best)

        # Stage 8: final cell position
        hp, sp = p["fine_pos"]

        best = self.run_grid(
            "8_final_cell_position",
            clamp_range(d.de_position_in, 35, 140, hp, sp),
            (d.xfrmr_spacing_in, d.xfrmr_spacing_in, 1),
            (d.coupler_spacing_in, d.coupler_spacing_in, 1),
            (d.xfrmr_length_in, d.xfrmr_length_in, 1),
            (d.coupler_length_in, d.coupler_length_in, 1),
            (d.de_length_in, d.de_length_in, 1),
        )

        print_best(best, "FINAL BEST MATCH FOUND")

        if best["points_under_2p0"] >= len(self.freqs):
            print()
            print("RESULT: Entire CB sweep is under 2.0:1 SWR.")
        else:
            print()
            print("RESULT: Best found is still not under 2.0:1 over the whole CB sweep.")
            print("Try --level normal or --level deep, or later allow reflector/director changes.")

        return best
