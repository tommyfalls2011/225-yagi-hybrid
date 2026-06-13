"""Antenna Report — printable single-page spec sheet.

Renders ONE styled HTML document containing the full antenna report
(metrics, cut sheet, SWR curve as inline SVG, build notes).  Streamlit's
own chrome (sidebar / header / toolbar / buttons) is hidden by
`@media print`, so Ctrl/Cmd+P prints exactly the report body and nothing
else.  A "Download printable HTML" button packages the same document as a
self-contained .html file so the user can print or Save-as-PDF from any
machine without running the app.
"""
import datetime
import html
import json
import math
import pathlib
import sqlite3
import sys

import streamlit as st

st.set_page_config(page_title="Report", layout="wide")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
GEO_PATH = ROOT / "data/current_geometry_v2.json"
RULES_PATH = ROOT / "data/rules_v2.json"
SETUP_PATH = ROOT / "data/setup_v2.json"
DB_PATH = ROOT / "data/auto7_history.db"

sys.path.insert(0, str(ROOT))
from hyagi import v2_runner          # noqa: E402
from hyagi import perf_report        # noqa: E402
from hyagi import exporters          # noqa: E402
from hyagi.units import fmt_in, fmt_inches_only  # noqa: E402


# ---------------------------------------------------------------------------
# Print CSS -- hides Streamlit chrome and resets layout for paper output.
# Lives once at the top of the page so EVERY render gets the same print
# behaviour (Ctrl/Cmd+P will Just Work).
# ---------------------------------------------------------------------------
_PRINT_CSS = """
<style>
/* Screen styling for the report body. */
.ant-report {
  --ink: #0f172a;
  --muted: #475569;
  --line: #cbd5e1;
  --soft: #f1f5f9;
  --accent: #0b3b8c;
  --warn: #b45309;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
  font-size: 13px;
  line-height: 1.45;
  background: white;
  padding: 24px 28px;
  border: 1px solid var(--line);
  border-radius: 6px;
  margin-top: 6px;
}
.ant-report header.hero {
  border-bottom: 2px solid var(--ink);
  padding-bottom: 10px;
  margin-bottom: 18px;
}
.ant-report header.hero h1 {
  margin: 0 0 4px 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.2px;
}
.ant-report header.hero .meta {
  color: var(--muted);
  font-size: 12px;
}
.ant-report h2 {
  font-size: 14px;
  text-transform: uppercase;
  letter-spacing: 1.4px;
  color: var(--accent);
  border-bottom: 1px solid var(--line);
  padding-bottom: 4px;
  margin: 18px 0 10px 0;
}
.ant-report .kpis {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 14px;
}
.ant-report .kpi {
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 10px 12px;
  background: var(--soft);
}
.ant-report .kpi .label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1.2px;
  color: var(--muted);
  margin-bottom: 4px;
}
.ant-report .kpi .value {
  font-size: 20px;
  font-weight: 700;
  color: var(--ink);
}
.ant-report .kpi .sub {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}
.ant-report table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin: 4px 0 12px 0;
}
.ant-report th, .ant-report td {
  border: 1px solid var(--line);
  padding: 5px 8px;
  vertical-align: top;
  text-align: left;
}
.ant-report th {
  background: var(--soft);
  font-weight: 600;
  color: var(--ink);
}
.ant-report td.num, .ant-report th.num { text-align: right; font-variant-numeric: tabular-nums; }
.ant-report .swr-svg { width: 100%; height: auto; max-height: 320px; }
.ant-report footer.foot {
  margin-top: 18px;
  border-top: 1px solid var(--line);
  padding-top: 8px;
  font-size: 10px;
  color: var(--muted);
}
.ant-report .pill {
  display: inline-block;
  padding: 2px 8px;
  background: var(--soft);
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 11px;
  color: var(--muted);
  margin-right: 4px;
}

/* PRINT: strip everything Streamlit, print just the report body. */
@media print {
  @page { size: Letter portrait; margin: 0.5in; }
  body, [data-testid="stAppViewContainer"], section.main, .block-container,
  .stApp { background: white !important; }
  /* Hide Streamlit chrome */
  [data-testid="stSidebar"],
  [data-testid="stSidebarNav"],
  [data-testid="stSidebarCollapseButton"],
  [data-testid="stHeader"],
  [data-testid="stToolbar"],
  [data-testid="stDecoration"],
  [data-testid="stStatusWidget"],
  [data-testid="stBottomBlockContainer"],
  header, footer,
  div[data-testid="stToolbar"],
  .stDeployButton, .no-print { display: none !important; }
  /* Drop Streamlit's gutters */
  .main .block-container { padding: 0 !important; max-width: none !important; }
  /* Print-only report tweaks */
  .ant-report {
    border: none !important; padding: 0 !important; margin: 0 !important;
    box-shadow: none !important; font-size: 11.5px !important;
  }
  .ant-report header.hero h1 { font-size: 18px !important; }
  .ant-report .kpi .value { font-size: 16px !important; }
  .ant-report h2 { font-size: 12px !important; margin-top: 14px !important; }
  .ant-report .page-break { page-break-before: always; }
  .ant-report .avoid-break { page-break-inside: avoid; }
  .ant-report a { color: inherit !important; text-decoration: none !important; }
}
</style>
"""
st.markdown(_PRINT_CSS, unsafe_allow_html=True)


@st.cache_data(ttl=2)
def _load(p, fb):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return fb


geo = _load(str(GEO_PATH), {"elements": []})
rules = _load(str(RULES_PATH), {"global": {}})
setup = _load(str(SETUP_PATH), {})
glb = rules.get("global", {})

v2_runner.GROUNDED = (str(setup.get("grounding", "insulated")) == "grounded")
v2_runner.BOOM_DIAMETER_IN = float(setup.get("boom_diameter_in", 1.5))
height_ft = float(setup.get("height_ft", 30.0))

els = geo.get("elements", [])

# -------- top control bar (these have class no-print so they don't print) ----
st.markdown('<div class="no-print">', unsafe_allow_html=True)
st.title("Report  ·  printable spec sheet")
st.caption("One-page antenna report.  Hit **Build / refresh** to compute, "
           "then **Print** (or Ctrl/Cmd+P) to send to your printer — Streamlit's "
           "chrome will be hidden automatically.  Or **Download printable HTML** "
           "to print from any machine.")

if not els:
    st.info("No geometry yet — set it up on Antenna Setup and tune it first.")
    st.stop()

st.markdown(
    f"**Antenna:** {len(els)} elements · {height_ft:.0f} ft · "
    f"boom Ø {float(setup.get('boom_diameter_in', 1.5)):.2f}\" · "
    f"elements {str(setup.get('grounding', 'insulated')).upper()} · "
    f"taper `{v2_runner.taper_signature()}`"
)

if st.button("📊 Build / refresh full report", type="primary", key="rp_build"):
    with st.spinner("Solving over real ground + free space and sweeping SWR…"):
        st.session_state["rp_report"] = perf_report.analyze(
            els, rules, height_ft=height_ft)

st.markdown('</div>', unsafe_allow_html=True)
# -----------------------------------------------------------------------------

rep = st.session_state.get("rp_report")
if not rep:
    st.info("Hit **Build / refresh full report** above to compute the specs.")
    st.stop()
if "error" in rep:
    st.error(f"Report failed: {rep['error']}")
    st.stop()


def _bw(b):
    return f"{b[0]:.3f}&ndash;{b[1]:.3f} MHz  ({b[2]:.0f} kHz)" if b else "—"


def _esc(s):
    return html.escape(str(s))


def _svg_swr_curve(curve, f_low, f_high, target_swr, width=760, height=260):
    """Print-friendly inline SVG of band SWR.

    Drawn with hand-placed primitives so it survives Save-as-PDF and paper
    printing without any chart library (matplotlib / vega / d3) loaded."""
    if not curve:
        return ""
    PL, PR, PT, PB = 56, 14, 12, 32
    w_in = width - PL - PR
    h_in = height - PT - PB
    swrs = [c[3] for c in curve]
    s_max = max(2.5, math.ceil(max(swrs) * 2.0) / 2.0)   # snap up to 0.5
    s_min = 1.0

    def sx(f):
        return PL + (f - f_low) / max(1e-9, (f_high - f_low)) * w_in

    def sy(s):
        return PT + (1.0 - (s - s_min) / (s_max - s_min)) * h_in

    parts = [
        f'<svg class="swr-svg" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<rect x="{PL}" y="{PT}" width="{w_in}" height="{h_in}" '
        f'fill="white" stroke="#0f172a" stroke-width="1"/>',
    ]
    # Y gridlines + labels every 0.5 SWR
    n = int(round((s_max - s_min) / 0.5)) + 1
    for i in range(n):
        s = s_min + i * 0.5
        y = sy(s)
        parts.append(f'<line x1="{PL}" y1="{y:.1f}" x2="{width-PR}" '
                     f'y2="{y:.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(f'<text x="{PL-6}" y="{y+3:.1f}" text-anchor="end" '
                     f'font-size="10" fill="#475569">{s:.1f}</text>')
    # X gridlines + labels (5 ticks)
    for i in range(5):
        f = f_low + i * (f_high - f_low) / 4.0
        x = sx(f)
        parts.append(f'<line x1="{x:.1f}" y1="{PT}" x2="{x:.1f}" '
                     f'y2="{height-PB}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{height-PB+14}" '
                     f'text-anchor="middle" font-size="10" '
                     f'fill="#475569">{f:.2f}</text>')
    # Target SWR dashed line
    yt = sy(target_swr)
    parts.append(f'<line x1="{PL}" y1="{yt:.1f}" x2="{width-PR}" y2="{yt:.1f}" '
                 f'stroke="#b45309" stroke-width="1" stroke-dasharray="4,3"/>')
    parts.append(f'<text x="{width-PR-6}" y="{yt-4:.1f}" text-anchor="end" '
                 f'font-size="10" fill="#b45309">target {target_swr:.2f}</text>')
    # SWR polyline
    pts = " ".join(f"{sx(c[0]):.1f},{sy(c[3]):.1f}" for c in curve)
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#0b3b8c" '
                 f'stroke-width="2"/>')
    # Axis labels
    parts.append(f'<text x="{PL + w_in/2:.1f}" y="{height-4}" '
                 f'text-anchor="middle" font-size="11" fill="#0f172a">'
                 f'Frequency (MHz)</text>')
    parts.append(f'<text x="12" y="{PT + h_in/2:.1f}" font-size="11" '
                 f'fill="#0f172a" transform="rotate(-90 12 {PT + h_in/2:.1f})" '
                 f'text-anchor="middle">SWR</text>')
    parts.append('</svg>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Build the printable HTML body.
# ---------------------------------------------------------------------------
f_low = float(rep.get("band_low_mhz", glb.get("freq_mhz_low", 26.665)))
f_high = float(rep.get("band_high_mhz", glb.get("freq_mhz_high", 27.855)))
curve, _mx, _av = v2_runner.band_swr_curve(els, f_low, f_high, 31, height_ft)
swr_svg = _svg_swr_curve(curve, f_low, f_high, target_swr=1.5)

# KPI cards (top of report).
kpi_html = f"""
<section class="kpis avoid-break">
  <div class="kpi"><div class="label">Forward gain</div>
       <div class="value">{rep['gain_dbi']:.2f} dBi</div>
       <div class="sub">{rep['gain_dbd']:.2f} dBd · {rep['power_mult_isotropic']:.1f}× isotropic</div></div>
  <div class="kpi"><div class="label">Front / back</div>
       <div class="value">{rep['fb_db']:.2f} dB</div>
       <div class="sub">F/R {rep['fr_db']:.2f} dB</div></div>
  <div class="kpi"><div class="label">Take-off</div>
       <div class="value">{rep['takeoff_deg']:.1f}°</div>
       <div class="sub">@ {rep['height_ft']:.0f} ft</div></div>
  <div class="kpi"><div class="label">Band-max SWR</div>
       <div class="value">{rep['band_max_swr']:.3f}:1</div>
       <div class="sub">min {rep['min_swr']:.3f} @ {rep['min_swr_mhz']:.3f} MHz</div></div>
</section>
"""

# Performance table rows.
perf_rows = [
    ("Gain over real ground", f"{rep['gain_dbi']:.2f} dBi  ({rep['gain_dbd']:.2f} dBd)"),
    ("Gain in free space", f"{rep['gain_free_space_dbi']:.2f} dBi"),
    ("Ground-reflection gain", f"+{rep['ground_gain_db']:.2f} dB"),
    ("Power multiplier",
     f"{rep['power_mult_isotropic']:.1f}× isotropic · {rep['power_mult_dipole']:.2f}× a dipole"),
    ("Front-to-back / front-to-rear",
     f"{rep['fb_db']:.2f} dB / {rep['fr_db']:.2f} dB"),
    ("Azimuth beamwidth (−3 dB)",
     f"{rep['az_beamwidth_deg']}°" if rep['az_beamwidth_deg'] else "—"),
    ("Elevation beamwidth (−3 dB)",
     f"{rep['el_beamwidth_deg']}°" if rep['el_beamwidth_deg'] else "—"),
    ("Take-off (peak elevation) angle", f"{rep['takeoff_deg']:.1f}°"),
    ("Radiation efficiency",
     f"{rep['efficiency_pct']:.1f}%" if rep['efficiency_pct'] is not None else "—"),
    ("Antenna height / boom length",
     f"{rep['height_ft']:.0f} ft  /  {fmt_in(rep['boom_in'])}"),
    ("Resonant (min-SWR) freq",
     f"{rep['min_swr']:.3f}:1 @ {rep['min_swr_mhz']:.3f} MHz"),
    ("In-band max SWR",
     f"{rep['band_max_swr']:.3f}:1  ({rep['band_low_mhz']:.3f}–{rep['band_high_mhz']:.3f} MHz)"),
    ("Bandwidth ≤ 1.2:1", _bw(rep['bw_swr_1p2'])),
    ("Bandwidth ≤ 1.5:1", _bw(rep['bw_swr_1p5'])),
    ("Bandwidth ≤ 2.0:1", _bw(rep['bw_swr_2p0'])),
]
perf_table = (
    '<table class="avoid-break"><thead><tr><th>Metric</th><th>Value</th></tr></thead>'
    '<tbody>'
    + "".join(f"<tr><td>{_esc(a)}</td><td>{b}</td></tr>" for a, b in perf_rows)
    + '</tbody></table>'
)

# Cut sheet table (build numbers).
taper = v2_runner.get_active_taper()
INCH = v2_runner.INCH
els_sorted = sorted(els, key=lambda e: float(e["position_in"]))
p0 = float(els_sorted[0]["position_in"])
cut_rows = []
for i, e in enumerate(els_sorted):
    L = float(e["length_in"])
    half_m = (L * INCH) / 2.0
    secs = v2_runner._half_sections(half_m, taper) if taper else []
    sect_txt = " + ".join(
        f"{(r * 2) / INCH:.3f}\"OD × {fmt_in(ln / INCH)}" for r, ln in secs
    ) or "uniform"
    pos_in = float(e["position_in"]) - p0
    spacing = (float(els_sorted[i + 1]["position_in"]) - float(e["position_in"])
               if i + 1 < len(els_sorted) else None)
    cut_rows.append((e["name"], fmt_inches_only(L), fmt_in(L / 2.0),
                     fmt_in(pos_in),
                     fmt_in(spacing) if spacing is not None else "—",
                     sect_txt))
cut_table = (
    '<table class="avoid-break"><thead><tr>'
    '<th>Element</th>'
    '<th class="num">Overall length</th>'
    '<th class="num">Half (centre→tip)</th>'
    '<th class="num">Boom position</th>'
    '<th class="num">Spacing → next</th>'
    '<th>Tubing (centre→tip, per half)</th>'
    '</tr></thead><tbody>'
    + "".join(
        f"<tr><td><strong>{_esc(n)}</strong></td>"
        f"<td class=\"num\">{_esc(L)}</td>"
        f"<td class=\"num\">{_esc(H)}</td>"
        f"<td class=\"num\">{_esc(pos)}</td>"
        f"<td class=\"num\">{_esc(sp)}</td>"
        f"<td>{_esc(s)}</td></tr>"
        for n, L, H, pos, sp, s in cut_rows
    )
    + '</tbody></table>'
)
boom_len_in = float(els_sorted[-1]["position_in"]) - float(els_sorted[0]["position_in"])

# Best-logged-run pill (if any).
best_db = ""
try:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT created_utc, max_swr, center_swr, center_r, center_x, center_rl_db "
        "FROM runs WHERE status='DONE' ORDER BY max_swr ASC, avg_swr ASC LIMIT 1"
    ).fetchone()
    con.close()
    if row:
        best_db = (
            f'<p style="font-size:11px;color:#475569;margin:6px 0 0 0;">'
            f'Best DB run · band-max SWR <strong>{row["max_swr"]:.3f}</strong> · '
            f'center R={row["center_r"]:.1f}Ω X={row["center_x"]:+.2f}Ω SWR '
            f'{row["center_swr"]:.3f} · return loss {row["center_rl_db"]:.1f} dB '
            f'· {row["created_utc"]}</p>'
        )
except Exception:
    pass

generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

report_body = f"""
<div class="ant-report" id="ant-report">
  <header class="hero">
    <h1>Hybrid Yagi Report — {len(els)} elements</h1>
    <div class="meta">
      <span class="pill">{rep['height_ft']:.0f} ft AGL</span>
      <span class="pill">boom Ø {float(setup.get('boom_diameter_in', 1.5)):.2f}"</span>
      <span class="pill">{str(setup.get('grounding', 'insulated')).upper()}</span>
      <span class="pill">taper {v2_runner.taper_signature()}</span>
      <span class="pill">{rep['band_low_mhz']:.3f}–{rep['band_high_mhz']:.3f} MHz</span>
      <span class="pill">generated {generated}</span>
    </div>
  </header>

  {kpi_html}

  <section class="avoid-break">
    <h2>Performance</h2>
    {perf_table}
    {best_db}
  </section>

  <section class="avoid-break">
    <h2>SWR across the band</h2>
    {swr_svg}
  </section>

  <section class="page-break">
    <h2>Cut sheet · build numbers (ft / in / 16ths)</h2>
    {cut_table}
    <p style="margin:6px 0 0 0;font-size:11.5px;">
      <strong>Boom length (REF → last director):</strong> {fmt_in(boom_len_in)}.
      Tubing sections are per HALF element, centre&nbsp;→&nbsp;tip (mirror for the
      other half). Overlap / insertion not included.
    </p>
  </section>

  <footer class="foot">
    Generated by <strong>hybrid_auto7</strong> on {generated} ·
    nec2c real-ground model · taper {v2_runner.taper_signature()} ·
    height {rep['height_ft']:.0f} ft AGL ·
    band {rep['band_low_mhz']:.3f}–{rep['band_high_mhz']:.3f} MHz.
  </footer>
</div>
"""

# Render in Streamlit.
st.markdown(report_body, unsafe_allow_html=True)

# Top-of-page action buttons (hidden when printing).
st.markdown('<div class="no-print" style="margin-top:14px;">',
            unsafe_allow_html=True)

# Print button -- runs in the page DOM so window.print() prints the report,
# not an iframe.
st.markdown(
    '<button onclick="window.print()" '
    'style="background:#0b3b8c;color:white;border:none;padding:10px 22px;'
    'border-radius:6px;font-size:15px;font-weight:600;cursor:pointer;'
    'margin-right:10px;">🖨️  Print this report</button>',
    unsafe_allow_html=True,
)

# Self-contained printable HTML (also hides 'no-print' bits) -- the user can
# email it, store it, or print it on any machine without running the app.
standalone_html = (
    '<!doctype html><html><head><meta charset="utf-8">'
    f'<title>Hybrid Yagi Report — {len(els)} elements — {generated}</title>'
    + _PRINT_CSS +
    '<style>body{background:#f8fafc;margin:0;padding:24px;}'
    '.no-print{margin-bottom:14px;}'
    'button{background:#0b3b8c;color:white;border:none;padding:8px 16px;'
    'border-radius:6px;cursor:pointer;font-size:13px;}</style>'
    '</head><body>'
    '<div class="no-print"><button onclick="window.print()">'
    '🖨️ Print this report</button></div>'
    + report_body +
    '</body></html>'
)

st.download_button(
    "⬇️  Download printable HTML (self-contained)",
    data=standalone_html,
    file_name=f"antenna_report_{datetime.datetime.now():%Y%m%d_%H%M}.html",
    mime="text/html",
    key="rp_dl_html",
)

# Existing downloads kept available below.
st.markdown("**Other downloads**")
rules_exp = json.loads(json.dumps(rules))
rules_exp["global"]["freq_mhz_low"] = f_low
rules_exp["global"]["freq_mhz_high"] = f_high
d1, d2, d3 = st.columns(3)
with d1:
    st.download_button("Report (JSON)", data=json.dumps(rep, indent=2),
                       file_name="antenna_report.json",
                       use_container_width=True, key="rp_dl_json")
with d2:
    st.download_button(".nec (NEC-2)",
                       data=exporters.to_nec(els, rules_exp, height_ft=height_ft),
                       file_name="antenna.nec", mime="text/plain",
                       use_container_width=True, key="rp_dl_nec")
with d3:
    st.download_button(
        ".maa (MMANA-GAL)",
        data=exporters.to_maa(
            els, rules_exp, height_ft=height_ft,
            center_mhz=float(glb.get("freq_mhz_center", 27.195))),
        file_name="antenna.maa", mime="text/plain",
        use_container_width=True, key="rp_dl_maa")

st.info("🖨️  **To print**: hit the blue Print button above, or use the browser's "
        "Print (Ctrl/Cmd+P) and Save as PDF — Streamlit's sidebar/toolbar is "
        "auto-hidden, only the report body prints. For a clean file you can "
        "email or print elsewhere, use **Download printable HTML**.")

st.markdown('</div>', unsafe_allow_html=True)
