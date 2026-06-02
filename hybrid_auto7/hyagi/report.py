# REAR_GAIN_DISPLAY_v1
# REPORT_FIX_v2
from datetime import datetime, timezone
import html

from .project import load_project
from .physics import return_loss_db
from . import db


def safe_run_by_id(run_id):
    if hasattr(db, "run_by_id"):
        return db.run_by_id(run_id)

    rows = db.best_rows(1000000)
    for r in rows:
        if r["id"] == run_id:
            return r
    return None


def best_or_champion_run(project_name):
    cfg = load_project(project_name)

    if cfg.champion_run_id:
        row = safe_run_by_id(cfg.champion_run_id)
        if row is not None:
            return cfg, row

    row = db.best_run()
    return cfg, row


def elements_table(run_id):
    rows = db.elements_for_run(run_id)
    out = []
    prev = None

    for e in rows:
        pos = float(e["position_in"])
        length = float(e["length_in"])
        spacing = 0.0 if prev is None else pos - prev
        out.append({
            "name": e["name"],
            "position_in": pos,
            "spacing_in": spacing,
            "length_in": length,
            "half_length_in": length / 2.0,
        })
        prev = pos

    return out




# REPORT_FIX_v2: prefer peak forward gain (real_gain_dbi) over horizon-null gain
def _best_gain_dbi(pat):
    g = getattr(pat, "real_gain_dbi", None)
    if g is not None and -100.0 < float(g) < 30.0:
        return float(g)
    return float(getattr(pat, "forward_gain_dbi", 0.0))

def power_multiplier_from_dbi(gain_dbi):
    return 10 ** (float(gain_dbi) / 10.0)


def gain_dbd_from_dbi(gain_dbi):
    return float(gain_dbi) - 2.15


def eirp_from_dbi(tx_power_watts, gain_dbi):
    return float(tx_power_watts) * power_multiplier_from_dbi(gain_dbi)


def erp_from_dbi(tx_power_watts, gain_dbi):
    return float(tx_power_watts) * (10 ** ((float(gain_dbi) - 2.15) / 10.0))


def build_report_data(project_name, pattern_result=None):
    cfg, run_row = best_or_champion_run(project_name)

    if run_row is None:
        raise RuntimeError("No run available for report.")

    elems = elements_table(run_row["id"])

    data = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": cfg,
        "run": run_row,
        "elements": elems,
        "pattern": pattern_result,
    }

    return data


def render_text_report(data):
    cfg = data["project"]
    r = data["run"]
    elems = data["elements"]
    pat = data["pattern"]

    lines = []
    lines.append(f"ANTENNA DESIGN REPORT")
    lines.append(f"=====================")
    lines.append(f"Generated UTC:        {data['generated_utc']}")
    lines.append("")
    lines.append(f"Project:              {cfg.name}")
    lines.append(f"Elements:             {cfg.element_count}")
    lines.append(f"Mode:                 {cfg.mode}")
    lines.append(f"Tuning procedure:     {cfg.tuning_procedure}")
    lines.append(f"Design priority:      {cfg.design_priority}")
    lines.append(f"TX power:             {cfg.tx_power_watts:.1f} W")
    lines.append(f"Cell mounting style:  {cfg.cell_mounting_style}")
    lines.append("")
    lines.append(f"Band:                 {cfg.freq_start_mhz:.3f}-{cfg.freq_stop_mhz:.3f} MHz")
    lines.append(f"Target impedance:     {cfg.target_z_ohm:.1f} ohm")
    lines.append(f"Height:               {cfg.height_ft:.3f} ft")
    lines.append(f"Boom length:          {cfg.boom_length_ft:.3f} ft")
    lines.append(f"Boom diameter:        {cfg.boom_diameter_in:.3f} in")
    lines.append(f"Ground mode:          {cfg.ground_mode}")
    lines.append("")
    lines.append("RUN SUMMARY")
    lines.append("-----------")
    lines.append(f"Run id:               {r['id']}")
    lines.append(f"Stage:                {r['stage']}")
    lines.append(f"Min SWR:              {r['min_swr']:.3f}")
    lines.append(f"Max SWR:              {r['max_swr']:.3f}")
    lines.append(f"Avg SWR:              {r['avg_swr']:.3f}")
    lines.append(f"Worst return loss:    {return_loss_db(r['max_swr']):.2f} dB")
    lines.append(f"Points <= 1.5:        {r['points_under_1p5']}")
    lines.append(f"Points <= 2.0:        {r['points_under_2p0']}")
    lines.append(f"Avg R:                {r['avg_r']:.3f} ohm")
    lines.append(f"Avg |X|:              {r['avg_abs_x']:.3f} ohm")
    lines.append("")

    if pat is not None:
        lines.append("PATTERN / POWER")
        lines.append("---------------")
        lines.append(f"Frequency:            {pat.freq_mhz:.3f} MHz")
        lines.append(f"Forward gain:         {_best_gain_dbi(pat):.3f} dBi")
        lines.append(f"Forward gain:         {gain_dbd_from_dbi(_best_gain_dbi(pat)):.3f} dBd")
        _rg = float(getattr(pat, "rear_gain_dbi", -999.99))
        if _rg < -100.0:
            # compute rear from F/B + peak forward (handles -999 sentinel)
            _rg = _best_gain_dbi(pat) - float(getattr(pat, "front_back_db", 0.0))
        lines.append(f"Rear gain:            {_rg:.3f} dBi  (derived from F/B)")
        lines.append(f"Front/back:           {pat.front_back_db:.3f} dB")
        lines.append(f"Beamwidth:            {pat.beamwidth_deg}")
        lines.append(f"Max gain phi:         {pat.max_gain_phi_deg:.1f} deg")
        _g = _best_gain_dbi(pat)
        _mult_iso = power_multiplier_from_dbi(_g)
        _mult_dip = 10.0 ** ((_g - 2.15) / 10.0)
        lines.append(f"Power x (vs dipole):  {_mult_dip:.2f}x")
        lines.append(f"Power x (vs iso):     {_mult_iso:.2f}x")
        lines.append(f"EIRP:                 {eirp_from_dbi(cfg.tx_power_watts, _best_gain_dbi(pat)):.1f} W")
        lines.append(f"ERP:                  {erp_from_dbi(cfg.tx_power_watts, _best_gain_dbi(pat)):.1f} W")
        lines.append("")
    else:
        lines.append("PATTERN / POWER")
        lines.append("---------------")
        lines.append("Pattern data unavailable in current workflow.")
        lines.append("")

    lines.append("ELEMENT BUILD SHEET")
    lines.append("-------------------")
    lines.append("Element    Position(in)   Spacing(in)   Length(in)   Half Length(in)")
    for e in elems:
        lines.append(
            f"{e['name']:<8s} "
            f"{e['position_in']:12.3f} "
            f"{e['spacing_in']:12.3f} "
            f"{e['length_in']:11.3f} "
            f"{e['half_length_in']:16.3f}"
        )

    lines.append("")
    lines.append("TAPER NOTE")
    lines.append("----------")
    lines.append(f"Center OD:            {cfg.center_od_in:.3f} in")
    lines.append(f"Outer OD:             {cfg.outer_od_in:.3f} in")
    lines.append(f"Center half length:   {cfg.center_half_len_in:.3f} in")

    if cfg.notes:
        lines.append("")
        lines.append("NOTES")
        lines.append("-----")
        lines.append(cfg.notes)

    return "\n".join(lines) + "\n"


def render_html_report(data):
    cfg = data["project"]
    r = data["run"]
    elems = data["elements"]
    pat = data["pattern"]

    def esc(x):
        return html.escape(str(x))

    rows_html = ""
    for e in elems:
        rows_html += f"""
        <tr>
            <td>{esc(e['name'])}</td>
            <td>{e['position_in']:.3f}</td>
            <td>{e['spacing_in']:.3f}</td>
            <td>{e['length_in']:.3f}</td>
            <td>{e['half_length_in']:.3f}</td>
        </tr>
        """

    if pat is not None:
        mult = power_multiplier_from_dbi(_best_gain_dbi(pat))
        pattern_block = f"""
        <h2>Pattern / Power</h2>
        <table>
            <tr><td>Frequency</td><td>{pat.freq_mhz:.3f} MHz</td></tr>
            <tr><td>Forward gain</td><td>{_best_gain_dbi(pat):.3f} dBi</td></tr>
            <tr><td>Forward gain</td><td>{gain_dbd_from_dbi(_best_gain_dbi(pat)):.3f} dBd</td></tr>
            <tr><td>Rear gain</td><td>{(_best_gain_dbi(pat) - float(getattr(pat, "front_back_db", 0.0))) if float(getattr(pat, "rear_gain_dbi", -999.99)) < -100.0 else float(pat.rear_gain_dbi):.3f} dBi</td></tr>
            <tr><td>Front/back</td><td>{pat.front_back_db:.3f} dB</td></tr>
            <tr><td>Beamwidth</td><td>{pat.beamwidth_deg}</td></tr>
            <tr><td>Max gain phi</td><td>{pat.max_gain_phi_deg:.1f} deg</td></tr>
            <tr><td>Power x (vs dipole)</td><td>{10.0 ** ((_best_gain_dbi(pat) - 2.15) / 10.0):.2f}x</td></tr>
            <tr><td>Power x (vs isotropic)</td><td>{mult:.2f}x</td></tr>
            <tr><td>EIRP</td><td>{eirp_from_dbi(cfg.tx_power_watts, _best_gain_dbi(pat)):.1f} W</td></tr>
            <tr><td>ERP</td><td>{erp_from_dbi(cfg.tx_power_watts, _best_gain_dbi(pat)):.1f} W</td></tr>
        </table>
        """
    else:
        pattern_block = """
        <h2>Pattern / Power</h2>
        <p>Pattern data unavailable in current workflow.</p>
        """

    notes_block = f"<h2>Notes</h2><p>{esc(cfg.notes)}</p>" if cfg.notes else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Antenna Design Report</title>
<style>
body {{
    font-family: Arial, Helvetica, sans-serif;
    margin: 30px;
    color: #111;
    background: #ffffff;
}}
h1, h2 {{
    margin-bottom: 6px;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 18px;
}}
td, th {{
    border: 1px solid #999;
    padding: 6px 8px;
    text-align: left;
}}
th {{
    background: #eee;
}}
.small {{
    color: #555;
    font-size: 0.92em;
}}
</style>
</head>
<body>
<h1>Antenna Design Report</h1>
<p class="small">Generated UTC: {esc(data['generated_utc'])}</p>

<h2>Project</h2>
<table>
<tr><td>Project</td><td>{esc(cfg.name)}</td></tr>
<tr><td>Elements</td><td>{cfg.element_count}</td></tr>
<tr><td>Mode</td><td>{esc(cfg.mode)}</td></tr>
<tr><td>Tuning procedure</td><td>{esc(cfg.tuning_procedure)}</td></tr>
<tr><td>Design priority</td><td>{esc(cfg.design_priority)}</td></tr>
<tr><td>TX power</td><td>{cfg.tx_power_watts:.1f} W</td></tr>
<tr><td>Cell mounting style</td><td>{esc(cfg.cell_mounting_style)}</td></tr>
<tr><td>Ground mode</td><td>{esc(cfg.ground_mode)}</td></tr>
<tr><td>Band</td><td>{cfg.freq_start_mhz:.3f} - {cfg.freq_stop_mhz:.3f} MHz</td></tr>
<tr><td>Target impedance</td><td>{cfg.target_z_ohm:.1f} ohm</td></tr>
<tr><td>Height</td><td>{cfg.height_ft:.3f} ft</td></tr>
<tr><td>Boom length</td><td>{cfg.boom_length_ft:.3f} ft</td></tr>
<tr><td>Boom diameter</td><td>{cfg.boom_diameter_in:.3f} in</td></tr>
<tr><td>Center OD</td><td>{cfg.center_od_in:.3f} in</td></tr>
<tr><td>Outer OD</td><td>{cfg.outer_od_in:.3f} in</td></tr>
<tr><td>Center half length</td><td>{cfg.center_half_len_in:.3f} in</td></tr>
</table>

<h2>Run Summary</h2>
<table>
<tr><td>Run id</td><td>{r['id']}</td></tr>
<tr><td>Stage</td><td>{esc(r['stage'])}</td></tr>
<tr><td>Min SWR</td><td>{r['min_swr']:.3f}</td></tr>
<tr><td>Max SWR</td><td>{r['max_swr']:.3f}</td></tr>
<tr><td>Avg SWR</td><td>{r['avg_swr']:.3f}</td></tr>
<tr><td>Worst return loss</td><td>{return_loss_db(r['max_swr']):.2f} dB</td></tr>
<tr><td>Points ≤ 1.5</td><td>{r['points_under_1p5']}</td></tr>
<tr><td>Points ≤ 2.0</td><td>{r['points_under_2p0']}</td></tr>
<tr><td>Avg R</td><td>{r['avg_r']:.3f} ohm</td></tr>
<tr><td>Avg |X|</td><td>{r['avg_abs_x']:.3f} ohm</td></tr>
</table>

{pattern_block}

<h2>Element Build Sheet</h2>
<table>
<tr>
    <th>Element</th>
    <th>Position (in)</th>
    <th>Spacing (in)</th>
    <th>Length (in)</th>
    <th>Half Length (in)</th>
</tr>
{rows_html}
</table>

{notes_block}
</body>
</html>
"""


def write_report_files(project_name, pattern_result=None, out_dir=None):
    data = build_report_data(project_name, pattern_result=pattern_result)
    text = render_text_report(data)
    html_report = render_html_report(data)
    return data, text, html_report
