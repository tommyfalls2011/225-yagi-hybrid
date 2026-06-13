"""Antenna Setup — the TOP of the workflow. Set every physical aspect of the
antenna here; the rest of the pages (Rules → Mini-Tunes → Procedures → Tune &
Learn → Report) work down from this.

Controls:
  * number of elements (REF + XFRMR + DE + COUPLER + N directors)
  * boom length: FIXED (positions locked as built) or FREE (tuner moves spacings)
  * height above ground
  * boom diameter
  * elements INSULATED (default) or GROUNDED to the boom

Saves to data/setup_v2.json and (re)seeds data/current_geometry_v2.json.
"""
import json
import pathlib
import sys

import streamlit as st

st.set_page_config(page_title="Antenna Setup", layout="wide")
st.title("Antenna Setup  ·  start here")
st.caption("Set the antenna up, then work DOWN the pages on the left: Rules → "
           "Mini-Tunes → Procedures → Tune & Learn → Report.")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
GEO_PATH = ROOT / "data/current_geometry_v2.json"
RULES_PATH = ROOT / "data/rules_v2.json"
SETUP_PATH = ROOT / "data/setup_v2.json"

sys.path.insert(0, str(ROOT))
from hyagi import hybrid_seed  # noqa: E402
from hyagi import exporters  # noqa: E402

DEFAULT = {"n_directors": 3, "boom_mode": "fixed", "boom_length_in": None,
           "height_ft": 30.0, "boom_diameter_in": 1.5, "grounding": "insulated"}


def _load(p, fallback):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return fallback


setup = _load(SETUP_PATH, dict(DEFAULT))
rules = _load(RULES_PATH, {"global": {"freq_mhz_center": 27.195}})
geo = _load(GEO_PATH, {"elements": []})
center_mhz = float(rules.get("global", {}).get("freq_mhz_center", 27.195))

n_dirs_now = sum(1 for e in geo.get("elements", [])
                 if str(e["name"]).upper().startswith("DIR"))

c1, c2 = st.columns(2)
with c1:
    st.markdown("#### Elements & boom")
    n_dir = st.slider("Number of directors", 0, 14,
                      int(setup.get("n_directors", n_dirs_now or 3)),
                      key="su_ndir",
                      help="Hybrid is always REF + XFRMR + DE + COUPLER, plus the "
                           "directors you choose. 0–14 directors = 4–18 total.")
    st.caption(f"→ {n_dir + 4} total elements (REF, XFRMR, DE, COUPLER + {n_dir} directors)")
    boom_mode = st.radio(
        "Boom length", ["fixed", "free"],
        index=0 if setup.get("boom_mode", "fixed") == "fixed" else 1,
        format_func=lambda m: ("FIXED — keep element positions as built (tune lengths only)"
                               if m == "fixed"
                               else "FREE — let the tuner move spacings / boom length"),
        key="su_boommode")
    boom_dia = st.number_input("Boom diameter (inches)",
                               value=float(setup.get("boom_diameter_in", 1.5)),
                               min_value=0.25, max_value=6.0, step=0.125, format="%.3f",
                               key="su_boomdia",
                               help="Used when elements are GROUNDED to the boom.")
with c2:
    st.markdown("#### Ground & mounting")
    height_ft = st.number_input("Height above ground (ft)",
                                value=float(setup.get("height_ft", 30.0)),
                                min_value=5.0, max_value=200.0, step=1.0, key="su_height")
    grounding = st.radio(
        "Elements", ["insulated", "grounded"],
        index=0 if setup.get("grounding", "insulated") == "insulated" else 1,
        format_func=lambda g: ("INSULATED from the boom (DE coax-fed) — standard"
                               if g == "insulated"
                               else "GROUNDED to the boom (parasitics bonded; DE stays fed)"),
        key="su_ground",
        help="Grounded models a metal boom of the diameter above, bonding each "
             "parasitic element's centre to it. Changes the tuning vs insulated.")

st.markdown("---")
b1, b2 = st.columns(2)
with b1:
    if st.button("💾 Save setup", type="primary", use_container_width=True, key="su_save"):
        SETUP_PATH.write_text(json.dumps({
            "n_directors": int(n_dir),
            "boom_mode": str(boom_mode),
            "boom_length_in": setup.get("boom_length_in"),
            "height_ft": float(height_ft),
            "boom_diameter_in": float(boom_dia),
            "grounding": str(grounding),
        }, indent=2))
        st.success("Setup saved. Element count applies after you Build / reseed "
                   "(if you changed it). Height / boom / grounding apply on the "
                   "next tune.")
with b2:
    if st.button("🛠️ Build / reseed geometry to this element count",
                 use_container_width=True, key="su_build"):
        new_geo = hybrid_seed.build_geometry(int(n_dir), center_mhz=center_mhz)
        GEO_PATH.write_text(json.dumps(new_geo, indent=2))
        st.success(f"Built a fresh {len(new_geo['elements'])}-element hybrid. "
                   f"Now work down to Tune & Learn to tune it.")
        st.rerun()

st.markdown("### Current geometry")
els = geo.get("elements", [])
if els:
    cols = st.columns(min(4, len(els)))
    for i, e in enumerate(els):
        with cols[i % len(cols)]:
            st.caption(f"`{e['name']}`  pos={float(e['position_in']):.1f} in  "
                       f"len={float(e['length_in']):.1f} in")
else:
    st.info("No geometry yet — set the element count and hit Build / reseed.")

# ---------- Import from MMANA-GAL .maa --------------------------------------
st.markdown("---")
st.markdown("### 📥 Import geometry from MMANA-GAL (`.maa`)")
st.caption("If you've micro-tuned an antenna in MMANA-GAL, upload its `.maa` "
           "file here to replace the current geometry with the MMANA wires. "
           "Element span is read on Y, boom on X, height on Z; the DE is the "
           "fed wire. Other pages will then tune / report against the imported "
           "lengths and positions.")
up = st.file_uploader("Upload .maa file", type=["maa", "txt"], key="su_maa_upload")
if up is not None:
    try:
        text = up.read().decode("utf-8", errors="replace")
        parsed = exporters.from_maa(text)
        new_els = parsed["elements"]
        st.success(f"Parsed {len(new_els)} elements from `{up.name}` "
                   f"(centre {parsed.get('center_mhz') or '?'} MHz).")
        prev_cols = st.columns(min(4, len(new_els)))
        for i, e in enumerate(new_els):
            with prev_cols[i % len(prev_cols)]:
                st.caption(f"`{e['name']}`  pos={e['position_in']:.2f} in  "
                           f"len={e['length_in']:.2f} in")
        if st.button("✅ Adopt imported geometry as current",
                     type="primary", key="su_adopt_maa"):
            GEO_PATH.write_text(json.dumps({"elements": new_els}, indent=2))
            # Sync setup's director count with what was actually imported.
            n_dirs_imp = sum(1 for e in new_els
                             if str(e["name"]).upper().startswith("DIR"))
            new_setup = dict(setup)
            new_setup["n_directors"] = n_dirs_imp
            SETUP_PATH.write_text(json.dumps(new_setup, indent=2))
            st.cache_data.clear()
            st.success(f"Adopted {len(new_els)} elements ({n_dirs_imp} directors). "
                       "Now go to Tune & Learn to tune it.")
            st.rerun()
    except Exception as ex:
        st.error(f"Could not parse .maa: {ex}")

