#!/usr/bin/env python3

from pathlib import Path
import html
import io
import subprocess
import json
from contextlib import redirect_stdout
from dataclasses import asdict

import pandas as pd
import streamlit as st

from hyagi import db
from hyagi.design import run_design
from hyagi.dir1_tuner import tune_dir1
from hyagi.dir2_tuner import tune_dir2
from hyagi.dir3_tuner import tune_dir3
from hyagi.dynamic import generate_starting_model, roles_for
from hyagi.dynamic_sim import project_sim
from hyagi.pattern import pattern_for_best, pattern_for_run
from hyagi.physics import return_loss_db
from hyagi.project import (
    ProjectConfig,
    apply_element_overrides,
    list_projects,
    load_project,
    save_project,
    set_champion,
    set_element_override,
)
from hyagi.project_director_tuner import tune_project_director
from hyagi.report import write_report_files
from hyagi.paths import BACKUPS_DIR, DATA_DIR, DB_PATH, LOGS_DIR, MODELS_DIR, PROJECT_DIR


st.set_page_config(
    page_title="Hybrid Auto7 Antenna Designer",
    page_icon="📡",
    layout="wide",
)

st.markdown("""
<style>
.progress-log-wrap {
    border: 1px solid #444;
    border-radius: 8px;
    padding: 0.5rem;
    background: #0f1116;
}
.progress-log-title {
    font-weight: 700;
    margin-bottom: 0.35rem;
    color: #ddd;
}
.progress-log-box {
    height: 420px;
    overflow-y: auto;
    white-space: pre-wrap;
    font-family: monospace;
    font-size: 0.86rem;
    line-height: 1.25rem;
    color: #d7e0ea;
    background: #0b0d12;
    border: 1px solid #2f3540;
    border-radius: 6px;
    padding: 0.75rem;
}
</style>
""", unsafe_allow_html=True)


class LiveLogWriter:
    def __init__(self, placeholder, title="Progress"):
        self.placeholder = placeholder
        self.title = title
        self.buffer = ""

    def write(self, text):
        if not text:
            return
        self.buffer += str(text)
        if len(self.buffer) > 120000:
            self.buffer = self.buffer[-120000:]
        self.render()

    def flush(self):
        return

    def render(self):
        escaped = html.escape(self.buffer)
        self.placeholder.markdown(
            f"""
            <div class="progress-log-wrap">
                <div class="progress-log-title">{html.escape(self.title)}</div>
                <div id="progress-log-box" class="progress-log-box">{escaped}</div>
            </div>
            <script>
                const box = window.parent.document.querySelector('#progress-log-box');
                if (box) {{
                    box.scrollTop = box.scrollHeight;
                }}
            </script>
            """,
            unsafe_allow_html=True,
        )


def capture_output(func, *args, **kwargs):
    buf = io.StringIO()
    result = None
    with redirect_stdout(buf):
        result = func(*args, **kwargs)
    return buf.getvalue(), result


def run_with_live_log(func, *args, title="Progress", **kwargs):
    placeholder = st.empty()
    writer = LiveLogWriter(placeholder, title=title)

    result = None
    with redirect_stdout(writer):
        result = func(*args, **kwargs)

    writer.render()
    return result


def latest_file(folder, pattern):
    folder = Path(folder)
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def run_subprocess_live(cmd, title="Process"):
    # VENV_SUBPROC_FIX_v2: replace plain "python3"/"python" with sys.executable
    # so child processes use the same interpreter that's running Streamlit
    # (which has necpp + emergentintegrations installed in the venv).
    import sys as _sys
    cmd = list(cmd)
    if cmd and cmd[0] in ("python3", "python"):
        cmd[0] = _sys.executable

    placeholder = st.empty()
    writer = LiveLogWriter(placeholder, title=title)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    captured = []
    try:
        for line in proc.stdout:
            captured.append(line)
            writer.write(line)
        proc.wait()
    finally:
        writer.render()

    output = "".join(captured)
    return proc.returncode, output




def project_names():
    return [p.name for p in list_projects()]


def safe_df(rows):
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) if not isinstance(r, dict) else r for r in rows])


def safe_run_by_id(run_id):
    if hasattr(db, "run_by_id"):
        return db.run_by_id(run_id)
    rows = db.best_rows(1000000)
    for r in rows:
        if r["id"] == run_id:
            return r
    return None


def render_build_sheet(run_row):
    if run_row is None:
        st.warning("Run not found.")
        return

    elements = db.elements_for_run(run_row["id"])

    st.subheader(f"Build sheet for run id={run_row['id']}")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Run summary**")
        st.write(f"Stage: {run_row['stage']}")
        st.write(f"DE position: {run_row['de_position_in']:.3f} in from REF")
        st.write(f"XFRMR-DE spacing: {run_row['xfrmr_spacing_in']:.3f} in")
        st.write(f"DE-Coupler spacing: {run_row['coupler_spacing_in']:.3f} in")
        st.write(f"XFRMR length: {run_row['xfrmr_length_in']:.3f} in")
        st.write(f"Coupler length: {run_row['coupler_length_in']:.3f} in")
        st.write(f"DE length: {run_row['de_length_in']:.3f} in")


        latest_cfirst_summary = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_cfirst_summary_*.txt")
        latest_cfirst_json = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_cfirst_best_*.json")
        latest_cfirst_log = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_cfirst_moves_*.jsonl")

        if latest_cfirst_summary:
            st.download_button(
                "Download latest coupler-first summary",
                data=latest_cfirst_summary.read_text(encoding="utf-8"),
                file_name=latest_cfirst_summary.name,
                mime="text/plain"
            )

        if latest_cfirst_json:
            st.download_button(
                "Download latest coupler-first best JSON",
                data=latest_cfirst_json.read_text(encoding="utf-8"),
                file_name=latest_cfirst_json.name,
                mime="application/json"
            )

        if latest_cfirst_log:
            st.download_button(
                "Download latest coupler-first move log",
                data=latest_cfirst_log.read_text(encoding="utf-8"),
                file_name=latest_cfirst_log.name,
                mime="text/plain"
            )

    with col2:
        st.markdown("**SWR summary**")
        st.write(f"Min SWR: {run_row['min_swr']:.3f}")
        st.write(f"Max SWR: {run_row['max_swr']:.3f}")
        st.write(f"Avg SWR: {run_row['avg_swr']:.3f}")
        st.write(f"Worst return loss: {return_loss_db(run_row['max_swr']):.2f} dB")
        st.write(f"Points <= 1.5: {run_row['points_under_1p5']}")
        st.write(f"Points <= 2.0: {run_row['points_under_2p0']}")
        st.write(f"Avg R: {run_row['avg_r']:.3f} ohm")
        st.write(f"Avg |X|: {run_row['avg_abs_x']:.3f} ohm")

    rows = []
    prev = None
    for e in elements:
        pos = e["position_in"]
        spacing = 0.0 if prev is None else pos - prev
        rows.append({
            "Element": e["name"],
            "Position from REF (in)": round(pos, 3),
            "Spacing (in)": round(spacing, 3),
            "Length (in)": round(e["length_in"], 3),
            "Half length (in)": round(e["length_in"] / 2.0, 3),
        })
        prev = pos

    st.markdown("**Elements**")
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def generated_model_df(cfg):
    generated = generate_starting_model(
        element_count=cfg.element_count,
        mode=cfg.mode,
        freq_start_mhz=cfg.freq_start_mhz,
        freq_stop_mhz=cfg.freq_stop_mhz,
        boom_length_ft=cfg.boom_length_ft,
    )
    apply_element_overrides(cfg, generated)

    rows = []
    prev = None
    for e in sorted(generated, key=lambda x: x.position_in):
        spacing = 0.0 if prev is None else e.position_in - prev
        rows.append({
            "Element": e.name,
            "Role": e.role,
            "Position (in)": round(e.position_in, 3),
            "Spacing (in)": round(spacing, 3),
            "Length (in)": round(e.length_in, 3),
            "Half length (in)": round(e.length_in / 2.0, 3),
        })
        prev = e.position_in
    return pd.DataFrame(rows)


def power_multiplier_from_dbi(gain_dbi):
    return 10 ** (float(gain_dbi) / 10.0)


def gain_dbd_from_dbi(gain_dbi):
    return float(gain_dbi) - 2.15


def erp_from_dbi(tx_power_watts, gain_dbi):
    return float(tx_power_watts) * (10 ** ((float(gain_dbi) - 2.15) / 10.0))


def eirp_from_dbi(tx_power_watts, gain_dbi):
    return float(tx_power_watts) * (10 ** (float(gain_dbi) / 10.0))




st.title("📡 Hybrid Auto7 Antenna Designer")
st.caption("Local web UI for your hybrid antenna tuning engine")

with st.sidebar:
    st.header("Project")
    names = project_names()

    if names:
        selected_project_name = st.selectbox("Saved projects", names, index=0)
        selected_project = load_project(selected_project_name)
    else:
        selected_project_name = None
        selected_project = None
        st.info("No saved projects yet.")

    st.header("System")
    st.write(f"Project dir: `{PROJECT_DIR}`")
    st.write(f"Data dir: `{DATA_DIR}`")
    st.write(f"Models dir: `{MODELS_DIR}`")
    st.write(f"Logs dir: `{LOGS_DIR}`")
    st.write(f"Backups dir: `{BACKUPS_DIR}`")
    st.write(f"DB: `{DB_PATH}`")

tab_projects, tab_model, tab_design, tab_db, tab_report, tab_learning = st.tabs(
    ["Projects", "Model / Sim", "Design / Tuning", "Database / Patterns", "Report", "Learning Lab"]
)



# NEC_VIEWER_v2: paginated sidebar NEC viewer (handles 50k+ files)
with st.sidebar.expander("NEC Files (view / import)", expanded=False):
    _models_dir = MODELS_DIR
    _imports_dir = _models_dir / "imports"
    _imports_dir.mkdir(parents=True, exist_ok=True)

    _uploaded = st.file_uploader(
        "Import .nec file",
        type=["nec", "txt"],
        key="nec_uploader",
        help="Drop a NEC2 deck here. It will be saved to models/imports/."
    )
    if _uploaded is not None:
        _target = _imports_dir / _uploaded.name
        _target.write_bytes(_uploaded.getvalue())
        st.success(f"Saved to {_target}")

    _filter = st.text_input(
        "Filter (substring match)",
        value="",
        key="nec_filter",
        help="e.g. 'project_my_7' or 'pattern_' or 'cell_best'. Empty = show newest."
    )

    @st.cache_data(show_spinner=False, ttl=30)
    def _list_nec(filter_str: str, max_results: int = 200):
        all_paths = list(_models_dir.glob("*.nec")) + list(_imports_dir.glob("*.nec"))
        total = len(all_paths)
        if filter_str:
            f_lower = filter_str.lower()
            all_paths = [p for p in all_paths if f_lower in p.name.lower()]
        # Sort newest-first, cap to max_results
        all_paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [str(p) for p in all_paths[:max_results]], total, len(all_paths)

    _paths, _total, _matching = _list_nec(_filter)

    st.caption(f"{_matching:,} match / {_total:,} total (showing newest {len(_paths)})")

    if _paths:
        _labels = [Path(p).name for p in _paths]
        _idx = st.selectbox(
            "Pick a NEC file",
            range(len(_paths)),
            format_func=lambda i: _labels[i],
            key="nec_picker_idx",
        )
        _picked = Path(_paths[_idx])
        try:
            _txt = _picked.read_text(encoding="utf-8", errors="ignore")
            st.caption(f"{_picked.name} -- {len(_txt.splitlines())} lines, {_picked.stat().st_size} bytes")
            st.download_button(
                "Download this NEC",
                data=_txt,
                file_name=_picked.name,
                mime="text/plain",
                key="nec_download_btn",
            )
            st.code(_txt, language="text")
        except Exception as _exc:
            st.error(f"Read failed: {_exc}")
    elif _filter:
        st.caption("No matches. Try a different filter substring.")
    else:
        st.caption("No NEC files yet.")

with tab_projects:
    st.subheader("Create or update project")

    default_cfg = selected_project if selected_project is not None else ProjectConfig(name="new_project")

    with st.form("project_form"):
        col1, col2, col3 = st.columns(3)

        with col1:
            name = st.text_input("Project name", value=default_cfg.name)
            element_count = st.number_input("Element count", min_value=3, max_value=12, value=int(default_cfg.element_count), step=1)
            mode = st.selectbox("Mode", ["hybrid", "yagi"], index=0 if default_cfg.mode == "hybrid" else 1)
            target_z_ohm = st.number_input("Target impedance (ohm)", value=float(default_cfg.target_z_ohm), step=1.0)

            tuning_choices = ["legacy_hybrid", "wide_cell_owa", "cell_then_directors_repeat"]
            current_tuning = getattr(default_cfg, "tuning_procedure", "legacy_hybrid")
            if current_tuning not in tuning_choices:
                current_tuning = "legacy_hybrid"
            tuning_procedure = st.selectbox(
                "Tuning procedure",
                tuning_choices,
                index=tuning_choices.index(current_tuning),
            )

            design_priority = st.selectbox(
                "Design priority",
                ["wideband", "balanced", "high_gain"],
                index=["wideband", "balanced", "high_gain"].index(getattr(default_cfg, "design_priority", "balanced"))
            )

            tx_power_watts = st.number_input(
                "TX power watts",
                value=float(getattr(default_cfg, "tx_power_watts", 100.0)),
                step=100.0
            )

            cell_mounting_style = st.selectbox(
                "Cell mounting style",
                ["full_cell_insulated", "de_only_insulated"],
                index=0 if getattr(default_cfg, "cell_mounting_style", "full_cell_insulated") == "full_cell_insulated" else 1,
            )

        with col2:
            freq_start_mhz = st.number_input("Start frequency MHz", value=float(default_cfg.freq_start_mhz), step=0.001, format="%.6f")
            freq_stop_mhz = st.number_input("Stop frequency MHz", value=float(default_cfg.freq_stop_mhz), step=0.001, format="%.6f")
            height_ft = st.number_input("Height ft", value=float(default_cfg.height_ft), step=1.0)
            boom_length_ft = st.number_input("Boom length ft", value=float(default_cfg.boom_length_ft), step=1.0)

        with col3:
            boom_diameter_in = st.number_input("Boom diameter in", value=float(default_cfg.boom_diameter_in), step=0.1)
            center_od_in = st.number_input("Center OD in", value=float(default_cfg.center_od_in), step=0.01, format="%.3f")
            outer_od_in = st.number_input("Outer OD in", value=float(default_cfg.outer_od_in), step=0.01, format="%.3f")
            center_half_len_in = st.number_input("Center half length in", value=float(default_cfg.center_half_len_in), step=1.0)

        col4, col5 = st.columns(2)
        with col4:
            target_max_swr = st.number_input("Target max SWR", value=float(default_cfg.target_max_swr), step=0.1, format="%.3f")
        with col5:
            min_front_back_db = st.number_input("Minimum front/back dB", value=float(default_cfg.min_front_back_db), step=1.0, format="%.3f")

        st.markdown("### Ground settings")
        ground_modes = ["average", "good", "poor", "perfect", "free_space", "custom"]
        current_ground = getattr(default_cfg, "ground_mode", "average")
        if current_ground not in ground_modes:
            current_ground = "average"

        ground_mode = st.selectbox("Ground mode", ground_modes, index=ground_modes.index(current_ground))

        colg1, colg2 = st.columns(2)
        with colg1:
            ground_epsr = st.number_input(
                "Ground epsr",
                value=float(getattr(default_cfg, "ground_epsr", 13.0)),
                step=0.1,
                format="%.3f",
                disabled=(ground_mode != "custom"),
            )
        with colg2:
            ground_sigma = st.number_input(
                "Ground sigma S/m",
                value=float(getattr(default_cfg, "ground_sigma_s_per_m", 0.005)),
                step=0.001,
                format="%.6f",
                disabled=(ground_mode != "custom"),
            )

        prefer_gain = st.checkbox("Prefer gain", value=bool(default_cfg.prefer_gain))
        notes = st.text_area("Notes", value=default_cfg.notes)

        save_btn = st.form_submit_button("Save project")

    if save_btn:
        try:
            cfg = ProjectConfig(
                name=name,
                element_count=int(element_count),
                mode=mode,
                tuning_procedure=tuning_procedure,
                design_priority=design_priority,
                tx_power_watts=float(tx_power_watts),
                cell_mounting_style=cell_mounting_style,
                freq_start_mhz=float(freq_start_mhz),
                freq_stop_mhz=float(freq_stop_mhz),
                target_z_ohm=float(target_z_ohm),
                height_ft=float(height_ft),
                boom_length_ft=float(boom_length_ft),
                boom_diameter_in=float(boom_diameter_in),
                center_od_in=float(center_od_in),
                outer_od_in=float(outer_od_in),
                center_half_len_in=float(center_half_len_in),
                ground_mode=ground_mode,
                ground_epsr=float(ground_epsr),
                ground_sigma_s_per_m=float(ground_sigma),
                target_max_swr=float(target_max_swr),
                min_front_back_db=float(min_front_back_db),
                prefer_gain=bool(prefer_gain),
                champion_run_id=default_cfg.champion_run_id if default_cfg.name == name else None,
                element_overrides=default_cfg.element_overrides if default_cfg.name == name else {},
                notes=notes,
            )
            path = save_project(cfg)
            st.success(f"Saved project: {path}")
            st.rerun()
        except Exception as exc:
            st.exception(exc)

    if selected_project is not None:
        st.subheader("Selected project")
        st.code(json.dumps(asdict(selected_project), indent=2), language="json")

        st.subheader("Element overrides")
        roles = roles_for(selected_project.element_count, selected_project.mode)

        with st.form("override_form"):
            element_name = st.selectbox("Element", roles)
            set_pos = st.checkbox("Set position override")
            pos_val = st.number_input("Position in", value=0.0, step=1.0)
            set_len = st.checkbox("Set length override")
            len_val = st.number_input("Length in", value=0.0, step=1.0)
            override_btn = st.form_submit_button("Save override")

        if override_btn:
            try:
                set_element_override(
                    selected_project.name,
                    element_name,
                    position_in=pos_val if set_pos else None,
                    length_in=len_val if set_len else None,
                )
                st.success("Override saved.")
                st.rerun()
            except Exception as exc:
                st.exception(exc)

        st.subheader("Champion run")
        champion_run_id = st.number_input("Champion run id", min_value=1, value=int(selected_project.champion_run_id or 1), step=1)
        if st.button("Set champion run"):
            try:
                set_champion(selected_project.name, champion_run_id)
                st.success("Champion updated.")
                st.rerun()
            except Exception as exc:
                st.exception(exc)

with tab_model:
    if selected_project is None:
        st.info("Create or select a project first.")
    else:
        st.subheader("Generated starting model")
        try:
            df = generated_model_df(selected_project)
            st.dataframe(df, use_container_width=True)
        except Exception as exc:
            st.exception(exc)

        if st.button("Run project simulation"):
            try:
                with st.spinner("Running project simulation..."):
                    run_with_live_log(project_sim, selected_project.name, title="Project simulation progress")
            except Exception as exc:
                st.exception(exc)

with tab_design:
    if selected_project is None:
        st.info("Create or select a project first.")
    else:
        level = st.selectbox("Autotune level", ["quick", "normal", "deep"], index=0)

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Project design")
            if st.button("Run design workflow"):
                try:
                    with st.spinner("Running design workflow..."):
                        run_with_live_log(run_design, selected_project.name, level, title="Design workflow progress")
                except Exception as exc:
                    st.exception(exc)

            st.subheader("Project director tuning")
            director_choices = [r for r in roles_for(selected_project.element_count, selected_project.mode) if r.startswith("DIR")]
            if director_choices:
                selected_director = st.selectbox("Director", director_choices)
                if st.button("Tune selected project director"):
                    try:
                        with st.spinner("Tuning project director..."):
                            run_with_live_log(
                                tune_project_director,
                                selected_project.name,
                                selected_director,
                                level,
                                title=f"{selected_director} tuning progress",
                            )
                    except Exception as exc:
                        st.exception(exc)

        with col2:
            st.subheader("Specialized director tuners")
            base_run_id = st.number_input("Base run id for DIR2 / DIR3", min_value=1, value=1, step=1)

            if st.button("Tune DIR1 from current best run"):
                try:
                    with st.spinner("Tuning DIR1..."):
                        run_with_live_log(tune_dir1, level, title="DIR1 tuning progress")
                except Exception as exc:
                    st.exception(exc)

            if st.button("Tune DIR2 from base run"):
                try:
                    with st.spinner("Tuning DIR2..."):
                        run_with_live_log(tune_dir2, base_run_id, level, title="DIR2 tuning progress")
                except Exception as exc:
                    st.exception(exc)

            if st.button("Tune DIR3 from base run"):
                try:
                    with st.spinner("Tuning DIR3..."):
                        run_with_live_log(tune_dir3, base_run_id, level, title="DIR3 tuning progress")
                except Exception as exc:
                    st.exception(exc)

with tab_db:
    st.subheader("Best runs")
    limit = st.slider("How many rows", min_value=5, max_value=100, value=20, step=5)
    rows = db.best_rows(limit)
    best_df = safe_df(rows)

    if best_df.empty:
        st.info("No runs in database yet.")
    else:
        st.dataframe(best_df, use_container_width=True)

    st.subheader("Inspect one run")
    inspect_run_id = st.number_input("Run id", min_value=1, value=1, step=1, key="inspect_run_id")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Show build sheet"):
            try:
                run_row = safe_run_by_id(inspect_run_id)
                render_build_sheet(run_row)
            except Exception as exc:
                st.exception(exc)

    with col2:
        if st.button("Show frequency results"):
            try:
                freq_rows = db.freqs_for_run(inspect_run_id)
                if not freq_rows:
                    st.warning("No frequency results found.")
                else:
                    st.dataframe(safe_df(freq_rows), use_container_width=True)
            except Exception as exc:
                st.exception(exc)

    with col3:
        if st.button("Pattern for run"):
            try:
                row, pat = pattern_for_run(inspect_run_id)
                tx = float(getattr(selected_project, "tx_power_watts", 100.0)) if selected_project is not None else 100.0
                mult = power_multiplier_from_dbi(pat.forward_gain_dbi)
                st.json({
                    "run_id": row["id"],
                    "stage": row["stage"],
                    "frequency_mhz": pat.freq_mhz,
                    "forward_gain_dbi": pat.forward_gain_dbi,
                    "forward_gain_dbd": gain_dbd_from_dbi(pat.forward_gain_dbi),
                    "rear_gain_dbi": pat.rear_gain_dbi,
                    "front_back_db": pat.front_back_db,
                    "max_gain_dbi": pat.max_gain_dbi,
                    "max_gain_phi_deg": pat.max_gain_phi_deg,
                    "beamwidth_deg": pat.beamwidth_deg,
                    "power_multiplier_x": mult,
                    "tx_power_watts": tx,
                    "eirp_watts": eirp_from_dbi(tx, pat.forward_gain_dbi),
                    "erp_watts": erp_from_dbi(tx, pat.forward_gain_dbi),
                })
            except Exception as exc:
                st.exception(exc)

    st.subheader("Pattern for best run")
    best_freq = st.number_input("Pattern frequency MHz", value=27.185, step=0.01, format="%.3f")
    if st.button("Run best pattern check"):
        try:
            row, pat = pattern_for_best(freq_mhz=best_freq)
            tx = float(getattr(selected_project, "tx_power_watts", 100.0)) if selected_project is not None else 100.0
            mult = power_multiplier_from_dbi(pat.forward_gain_dbi)
            st.json({
                "run_id": row["id"],
                "stage": row["stage"],
                "frequency_mhz": pat.freq_mhz,
                "forward_gain_dbi": pat.forward_gain_dbi,
                "forward_gain_dbd": gain_dbd_from_dbi(pat.forward_gain_dbi),
                "rear_gain_dbi": pat.rear_gain_dbi,
                "front_back_db": pat.front_back_db,
                "max_gain_dbi": pat.max_gain_dbi,
                "max_gain_phi_deg": pat.max_gain_phi_deg,
                "beamwidth_deg": pat.beamwidth_deg,
                "power_multiplier_x": mult,
                "tx_power_watts": tx,
                "eirp_watts": eirp_from_dbi(tx, pat.forward_gain_dbi),
                "erp_watts": erp_from_dbi(tx, pat.forward_gain_dbi),
            })
        except Exception as exc:
            st.exception(exc)


with tab_report:
    if selected_project is None:
        st.info("Create or select a project first.")
    else:
        st.subheader("Printable report")

        pattern_freq = st.number_input("Report pattern frequency MHz", value=27.185, step=0.01, format="%.3f", key="report_pattern_freq")

        use_pattern = st.checkbox("Include pattern / multiplier data if available", value=True)

        if st.button("Generate report"):
            try:
                pattern_result = None
                if use_pattern:
                    try:
                        _, pattern_result = pattern_for_best(freq_mhz=pattern_freq)
                    except Exception:
                        pattern_result = None

                data, text_report, html_report = write_report_files(
                    selected_project.name,
                    pattern_result=pattern_result,
                )

                st.success("Report generated")

                st.markdown("### Report preview")
                st.components.v1.html(html_report, height=800, scrolling=True)

                st.download_button(
                    "Download HTML report",
                    data=html_report,
                    file_name=f"{selected_project.name}_report.html",
                    mime="text/html"
                )

                st.download_button(
                    "Download TXT report",
                    data=text_report,
                    file_name=f"{selected_project.name}_report.txt",
                    mime="text/plain"
                )

                st.info("To print nicely: download/open the HTML report, then use Ctrl+P and Save as PDF or print to your printer.")

                # NEC_VIEWER_v2: inline NEC viewer scoped to current project
                st.markdown("---")
                st.markdown("### NEC for this design")
                _proj_name_safe = str(selected_project.name).replace("/", "_").replace("\\", "_").replace(" ", "_")
                _matches = sorted(
                    MODELS_DIR.glob(f"project_{_proj_name_safe}*.nec"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:20]
                if _matches:
                    _idx = st.selectbox(
                        f"NEC files for '{selected_project.name}' (newest 20)",
                        range(len(_matches)),
                        format_func=lambda i: _matches[i].name,
                        key="report_nec_picker",
                    )
                    _np = _matches[_idx]
                    try:
                        _ntxt = _np.read_text(encoding="utf-8", errors="ignore")
                        st.caption(f"{_np.name} -- {len(_ntxt.splitlines())} lines")
                        st.download_button(
                            "Download this NEC",
                            data=_ntxt,
                            file_name=_np.name,
                            mime="text/plain",
                            key="report_nec_dl",
                        )
                        st.code(_ntxt, language="text")
                    except Exception as _exc:
                        st.error(f"Read failed: {_exc}")
                else:
                    st.caption("No NEC files saved yet for this project. Run a sim or design pass first.")


            except Exception as exc:
                st.exception(exc)


with tab_learning:
    st.subheader("Learning Lab")
    st.write("Run repeatable learning procedures and save outputs to disk.")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Cell placement tune")
        st.write('Tunes only XFRMR / DE / COUPLER. Starts DE at 60.0", finds spacing sweet spot, then lengths, then DE-only move, then fine nudges.')
        if st.button("Run Cell Placement Tune"):
            try:
                code, out = run_subprocess_live(
                    ["python3", "-u", "./learn_cell_only.py"],
                    title="Cell-only learning progress"
                )
                if code == 0:
                    st.success("Cell-only learning finished.")
                else:
                    st.error(f"Cell-only learning failed with exit code {code}")
            except Exception as exc:
                st.exception(exc)


        st.write("Temporary test mode: same cell tune, but coupler-first order.")
        if st.button("Run Cell Placement Tune (Coupler First Test)"):
            try:
                code, out = run_subprocess_live(
                    ["python3", "-u", "./learn_cell_only_cfirst.py"],
                    title="Cell placement tune (coupler-first) progress"
                )
                if code == 0:
                    st.success("Coupler-first cell tune finished.")
                else:
                    st.error(f"Coupler-first cell tune failed with exit code {code}")
            except Exception as exc:
                st.exception(exc)

        st.markdown("### Cascading procedure tune")
        st.write("User research procedure: Phase 1 (DE only) -> Phase 2 (add XFRMR, R->50) -> Phase 3 (add COUPLER, |X|->0). Overwrites best_cell_seed.json ONLY if it beats the existing score.")
        if st.button("Run Cascading Procedure Tune"):
            try:
                code, out = run_subprocess_live(
                    ["python3", "-u", "./learn_cell_cascade.py"],
                    title="Cascading procedure tune progress"
                )
                if code == 0:
                    st.success("Cascading procedure tune finished.")
                else:
                    st.error(f"Cascading procedure tune failed with exit code {code}")
            except Exception as exc:
                st.exception(exc)

        latest_summary = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_summary_*.txt")
        latest_json = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_best_*.json")
        latest_log = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_moves_*.jsonl")

        if latest_summary:
            st.download_button(
                "Download latest cell summary",
                data=latest_summary.read_text(encoding="utf-8"),
                file_name=latest_summary.name,
                mime="text/plain"
            )

        if latest_json:
            st.download_button(
                "Download latest cell best JSON",
                data=latest_json.read_text(encoding="utf-8"),
                file_name=latest_json.name,
                mime="application/json"
            )

        if latest_log:
            st.download_button(
                "Download latest cell move log",
                data=latest_log.read_text(encoding="utf-8"),
                file_name=latest_log.name,
                mime="text/plain"
            )

    with col2:
        st.markdown("### 3-element full learning")
        st.write("Runs the older full 3-element hybrid learning procedure.")
        st.markdown("---")
        st.subheader("Full Hybrid Tune (from cell seed)")
        st.write("Uses the latest tuned cell seed (XFRMR+DE+COUPLER locked) and tunes REF + N directors around it, scoring on real-ground gain + F/B + SWR every iteration.")
        seed_file_hint = DATA_DIR / "cell_learning_runs" / "best_cell_seed.json"
        st.caption(f"Cell seed file: {seed_file_hint} {'(found)' if seed_file_hint.exists() else '(MISSING - run Cell Placement Tune first)'}")
        fh_n_directors = st.slider("Number of directors", 3, 8, 4, key="fh_n_directors")
        fh_priority = st.selectbox("Priority profile", ["balanced", "gain", "swr"], index=0, key="fh_priority")
        if st.button("Run Full Hybrid Tune from Cell Seed"):
            if not seed_file_hint.exists():
                st.error("Cell seed file not found. Run 'Run Cell Placement Tune' first.")
            else:
                with st.spinner(f"Tuning {4 + fh_n_directors}-element hybrid..."):
                    code, out = run_subprocess_live(
                        ["python3", "-u", "./learn_from_cell_seed.py",
                         "--cell-seed", str(seed_file_hint),
                         "--n-directors", str(fh_n_directors),
                         "--priority", fh_priority],
                        title=f"Full Hybrid Tune ({4 + fh_n_directors}-el, {fh_priority})",
                    )
                if code == 0:
                    st.success("Full hybrid tune complete. See data/full_hybrid_runs/ for results.")
                else:
                    st.error(f"Tune failed (exit {code})")
                latest_fh_json = latest_file(DATA_DIR / "full_hybrid_runs", "full_hybrid_*.json")
                latest_fh_txt  = latest_file(DATA_DIR / "full_hybrid_runs", "full_hybrid_*.txt")
                if latest_fh_txt:
                    st.code(latest_fh_txt.read_text(), language="text")
                if latest_fh_json:
                    st.download_button("Download JSON", latest_fh_json.read_text(),
                                       file_name=latest_fh_json.name, key="fh_dl_json")
        
        st.markdown("---")
        _use_smart = st.checkbox(
            "Use Learning Insights for seeds (smart-seeds)",
            value=False,
            key="smart_seeds_chk",
            help="Reads the latest insights_*.json and seeds the search from historical winners "
                 "(xsp, csp, de_pos, ref_len). Falls back to hardcoded defaults if no snapshot."
        )
        if st.button("Run 3-Element Full Learning"):
            try:
                _env = {"LEARN_SMART_SEEDS": "1"} if _use_smart else None
                _cmd_args = []
                # run_subprocess_live doesn't expose env -- inline that here
                import os as _os
                _full_env = _os.environ.copy()
                if _env:
                    _full_env.update(_env)
                # Use the helper but with env override via shell -- simplest is sys.executable
                import sys as _sys, subprocess as _sp
                _placeholder = st.empty()
                _writer = LiveLogWriter(_placeholder, title="3-element full learning progress")
                _proc = _sp.Popen(
                    [_sys.executable, "-u", "./learn_3el_hybrid.py"],
                    stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True, bufsize=1,
                    env=_full_env,
                )
                _captured = []
                try:
                    for _line in _proc.stdout:
                        _captured.append(_line)
                        _writer.write(_line)
                    _proc.wait()
                finally:
                    _writer.render()
                code = _proc.returncode
                out = "".join(_captured)
                # legacy 4-line removed below
                if code == 0:
                    st.success("3-element full learning finished.")
                else:
                    st.error(f"3-element full learning failed with exit code {code}")
            except Exception as exc:
                st.exception(exc)

        latest_summary = latest_file(DATA_DIR / "learning_runs", "learn3el_summary_*.txt")
        latest_json = latest_file(DATA_DIR / "learning_runs", "learn3el_best_*.json")
        latest_log = latest_file(DATA_DIR / "learning_runs", "learn3el_moves_*.jsonl")
        # LEARN_SMART_BTN_v1: 2-phase smart learner with persistent KB
        st.markdown("---")
        st.markdown("### Smart Learner (2-phase, persistent KB)")
        st.caption(
            "Phase 1: random DE position search on 18ft boom. "
            "Phase 2: build out REF + DIR1-3 from top-K good cells. "
            "Logs winners to data/smart_kb.json and avoids known-bad starts on future runs."
        )
        _sm_c1, _sm_c2, _sm_c3 = st.columns(3)
        with _sm_c1:
            _sm_trials = st.number_input("Phase 1 trials", min_value=5, max_value=200, value=20, step=5, key="sm_trials")
        with _sm_c2:
            _sm_topk = st.number_input("Phase 2 top-K cells", min_value=1, max_value=10, value=3, step=1, key="sm_topk")
        with _sm_c3:
            _sm_usekb = st.checkbox("Use KB only (skip Phase 1)", value=False, key="sm_usekb")

        if st.button("Run Smart Learner"):
            try:
                _args = ["--trials", str(_sm_trials), "--top-k", str(_sm_topk)]
                if _sm_usekb:
                    _args.append("--use-kb")
                code, _ = run_subprocess_live(
                    ["python3", "-u", "./learn_smart.py", *_args],
                    title=f"Smart Learner (trials={_sm_trials}, top-K={_sm_topk})"
                )
                if code == 0:
                    st.success("Smart Learner finished. KB updated at data/smart_kb.json.")
                else:
                    st.error(f"Smart Learner exit code {code}")
            except Exception as exc:
                st.exception(exc)

        # Show KB summary if present
        _kb_path = DATA_DIR / "smart_kb.json"
        if _kb_path.exists():
            try:
                import json as _json
                _kb = _json.loads(_kb_path.read_text())
                _kc1, _kc2, _kc3, _kc4 = st.columns(4)
                _kc1.metric("Good cells", len(_kb.get("good_cells", [])))
                _kc2.metric("Bad starts", len(_kb.get("bad_starts", [])))
                _kc3.metric("Best designs", len(_kb.get("best_full_designs", [])))
                _kc4.metric("Dead paths", len(_kb.get("dead_paths", [])))
                if _kb.get("best_full_designs"):
                    _bd = max(_kb["best_full_designs"], key=lambda d: d["score"])
                    st.caption(
                        f"KB best: score {_bd['score']:+.1f}  |  "
                        f"gain {_bd.get('gain_dbi','?')} dBi  |  "
                        f"F/B {_bd.get('fb_db','?')} dB  |  "
                        f"max SWR {_bd.get('max_swr',0):.3f}"
                    )
                st.download_button(
                    "Download KB JSON",
                    data=_kb_path.read_text(),
                    file_name="smart_kb.json",
                    mime="application/json",
                    key="smart_kb_dl"
                )
            except Exception as _exc:
                st.warning(f"Could not parse KB: {_exc}")



    # LEARN_INSIGHTS_TAB_v1: cross-run move-history miner (no NEC cost)
    st.markdown("---")
    st.markdown("### Learning Insights (cross-run analysis)")
    st.caption(
        "Aggregates every move-log JSONL on disk and shows which stages "
        "and parameter ranges historically gave the biggest score gains. "
        "Use this to decide what to focus on next."
    )

    if st.button("Refresh Insights", key="refresh_insights_btn"):
        try:
            import importlib
            import learn_insights as _li
            importlib.reload(_li)
            _ins = _li.compute_insights()
            st.session_state["_insights_cache"] = _ins
            st.success(
                f"Analyzed {_ins['log_count']} log files / "
                f"{_ins['summary']['total_moves']:,} moves."
            )
        except Exception as _exc:
            st.exception(_exc)

    _ins = st.session_state.get("_insights_cache")
    if _ins is not None and _ins["log_count"] > 0:
        _s = _ins["summary"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total moves", f"{_s['total_moves']:,}")
        c2.metric("Win rate", f"{_s['win_rate']*100:.1f}%")
        c3.metric("Mean delta", f"{_s['mean_delta']:+.1f}")
        c4.metric("Best ever", f"{_s['max_delta']:+.1f}")

        st.markdown("#### Stage effectiveness")
        st.caption("Higher mean delta = stage produces bigger score gains on average.")
        try:
            import pandas as _pd
            _df = _pd.DataFrame(_ins["stages"])
            _df["win_rate_pct"] = (_df["win_rate"] * 100).round(1)
            _show = _df[["stage", "moves", "wins", "win_rate_pct",
                         "mean_delta", "median_delta", "max_delta", "min_delta"]]
            st.dataframe(_show, hide_index=True, use_container_width=True)
        except Exception:
            st.json(_ins["stages"])

        st.markdown("#### Parameter winning ranges")
        st.caption(
            "For each tuned parameter, the integer bins (rounded value) "
            "that historically gave the highest mean score delta. "
            "Use these as starting hints for new runs."
        )
        _param_names = sorted(_ins["parameters"].keys())
        if _param_names:
            _pick = st.selectbox(
                "Parameter", _param_names, key="insights_param_pick"
            )
            _pdata = _ins["parameters"][_pick]
            st.caption(f"{_pdata['total_samples']:,} samples for `{_pick}`")
            try:
                import pandas as _pd
                _tdf = _pd.DataFrame(_pdata["top_values"])
                _wdf = _pd.DataFrame(_pdata["worst_values"])
                colA, colB = st.columns(2)
                with colA:
                    st.markdown("**Top values (highest mean delta)**")
                    st.dataframe(_tdf, hide_index=True, use_container_width=True)
                with colB:
                    st.markdown("**Worst values (avoid)**")
                    st.dataframe(_wdf, hide_index=True, use_container_width=True)
            except Exception:
                st.json(_pdata)

        st.download_button(
            "Download insights JSON",
            data=__import__("json").dumps(_ins, indent=2),
            file_name=f"learning_insights_{_ins['generated_utc'].replace(':','-')}.json",
            mime="application/json",
            key="insights_dl_btn",
        )
    elif _ins is not None:
        st.warning("No move logs found yet. Run a learning script first to populate data/.")
    else:
        st.info("Click 'Refresh Insights' to scan all move-log files.")


        if latest_summary:
            st.download_button(
                "Download latest 3el summary",
                data=latest_summary.read_text(encoding="utf-8"),
                file_name=latest_summary.name,
                mime="text/plain"
            )

        if latest_json:
            st.download_button(
                "Download latest 3el best JSON",
                data=latest_json.read_text(encoding="utf-8"),
                file_name=latest_json.name,
                mime="application/json"
            )

        if latest_log:
            st.download_button(
                "Download latest 3el move log",
                data=latest_log.read_text(encoding="utf-8"),
                file_name=latest_log.name,
                mime="text/plain"
            )

    st.markdown("### Latest summaries")

    latest_cfirst_summary_bottom = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_cfirst_summary_*.txt")
    if latest_cfirst_summary_bottom:
        st.text_area(
            "Latest coupler-first summary",
            latest_cfirst_summary_bottom.read_text(encoding="utf-8"),
            height=320,
            key="latest_cfirst_summary_bottom"
        )

    col3, col4 = st.columns(2)

    with col3:
        latest_cell_summary = latest_file(DATA_DIR / "cell_learning_runs", "learn_cell_summary_*.txt")
        if latest_cell_summary:
            st.text_area(
                "Latest Cell Learning Summary",
                latest_cell_summary.read_text(encoding="utf-8"),
                height=300
            )

    with col4:
        latest_3el_summary = latest_file(DATA_DIR / "learning_runs", "learn3el_summary_*.txt")
        if latest_3el_summary:
            st.text_area(
                "Latest 3-Element Learning Summary",
                latest_3el_summary.read_text(encoding="utf-8"),
                height=300
            )
