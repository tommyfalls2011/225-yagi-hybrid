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
from hyagi.units import fmt_in  # noqa: E402

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
    antenna_name = st.text_input(
        "Antenna name", value=str(setup.get("antenna_name", "my_antenna")),
        key="su_aname", max_chars=64,
        help="Free-form name for THIS build.  Shown on the printable report, "
             "in the move log, and used as the project key in the learning DB "
             "so different antennas don't share warm-starts.",
    )
    n_dir = st.slider("Number of directors", 0, 14,
                      int(setup.get("n_directors", n_dirs_now or 3)),
                      key="su_ndir",
                      help="Hybrid is always REF + XFRMR + DE + COUPLER, plus the "
                           "directors you choose. 0–14 directors = 4–18 total.")
    st.caption(f"→ {n_dir + 4} total elements (REF, XFRMR, DE, COUPLER + {n_dir} directors)")
    boom_mode = st.radio(
        "Boom length", ["fixed", "free"],
        index=0 if setup.get("boom_mode", "fixed") == "fixed" else 1,
        format_func=lambda m: ("FIXED — boom is locked to a length; nothing can "
                               "exceed it (tuner moves lengths only)"
                               if m == "fixed"
                               else "FREE — let the tuner move spacings / boom length"),
        key="su_boommode")

    # Locked boom length (only when FIXED).  Stored as boom_length_in in the
    # setup JSON; passed into the seeder + import + tuner so directors are
    # always compressed to fit instead of letting the geometry drift past the
    # physical boom the user actually built.
    cur_span_in = (max(float(e["position_in"]) for e in geo["elements"])
                   if geo.get("elements") else 264.0)
    _default_lock_ft = (float(setup["boom_length_in"]) / 12.0
                        if setup.get("boom_length_in") else
                        round(cur_span_in / 12.0, 2))
    if boom_mode == "fixed":
        boom_length_ft = st.number_input(
            "Boom length (ft) — HARD CAP", min_value=2.0, max_value=120.0,
            value=float(_default_lock_ft), step=0.25, format="%.2f",
            key="su_boomlen_ft",
            help="With boom LOCKED, no element can exceed this distance from "
                 "the reflector. Build/reseed and .maa import both compress to "
                 "fit; the tuner only moves lengths (positions are held).")
        st.caption(f"→ locked at **{fmt_in(boom_length_ft * 12.0)}**")
    else:
        boom_length_ft = None
        st.caption("Boom is FREE — the tuner can grow / shrink the boom as needed.")

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
    # Three-way grounding tristate.  Legacy 'insulated' / 'grounded' values
    # from older setup.json files are auto-promoted to the new names so an
    # in-flight install keeps working without forcing a re-save.
    LEGACY_MAP = {"insulated": "all_insulated", "grounded": "all_grounded"}
    cur_grnd = setup.get("grounding", "all_insulated")
    cur_grnd = LEGACY_MAP.get(cur_grnd, cur_grnd)
    options = ["all_insulated", "cell_insulated", "all_grounded"]
    try:
        cur_idx = options.index(cur_grnd)
    except ValueError:
        cur_idx = 0
    grounding = st.radio(
        "Elements", options, index=cur_idx,
        format_func=lambda g: ({
            "all_insulated":
                "🔌 ALL INSULATED — every element (REF + XFRMR + DE + COUPLER + DIRn) "
                "sits on insulators; DE is coax-fed.  Standard hybrid build.",
            "cell_insulated":
                "⚡ INSULATED CELL — XFRMR + DE + COUPLER on insulators (the whole "
                "driven cell floats); REF + every DIRn bonded to the boom.",
            "all_grounded":
                "🔩 ALL GROUNDED — every parasitic (REF + every DIRn) bonded to "
                "the boom; DE stays coax-fed.  Lightning-bonded / high-power build.",
        }[g]),
        key="su_ground",
        help="Bonded elements are modelled with a short vertical drop wire to a "
             "metal boom of the diameter above.  Bonding shifts the tuning, the "
             "centre R, and the F/B vs an insulated build — choose the mode that "
             "matches the antenna you ACTUALLY built.")

st.markdown("---")
b1, b2 = st.columns(2)
with b1:
    if st.button("💾 Save setup", type="primary", use_container_width=True, key="su_save"):
        # Persist the boom lock as inches (single canonical unit).  `free` mode
        # clears the lock so the tuner is allowed to move spacings.
        new_cap_in = (float(boom_length_ft) * 12.0
                      if (boom_mode == "fixed" and boom_length_ft) else None)
        _save = {
            "n_directors": int(n_dir),
            "antenna_name": str(antenna_name or "my_antenna").strip() or "my_antenna",
            "boom_mode": str(boom_mode),
            "boom_length_in": new_cap_in,
            "height_ft": float(height_ft),
            "boom_diameter_in": float(boom_dia),
            "grounding": str(grounding),
        }
        SETUP_PATH.write_text(json.dumps(_save, indent=2))
        # Auto-rescale the current geometry to fit EXACTLY when FIXED+cap is
        # saved.  User's new spec: 'the boom should be the locked length, not
        # shorter and not longer.'  REF -> 0, last DIR -> cap, middle elements
        # scaled proportionally (the matcher can re-slide them next tune).
        rescale_msg = ""
        if new_cap_in and geo.get("elements"):
            els_now = sorted(geo["elements"], key=lambda x: float(x["position_in"]))
            p0 = float(els_now[0]["position_in"])
            span = float(els_now[-1]["position_in"]) - p0
            if span > 0 and abs(span - new_cap_in) > 0.5:
                for el in els_now:
                    el["position_in"] = round(
                        (float(el["position_in"]) - p0) * new_cap_in / span, 4)
                GEO_PATH.write_text(json.dumps({"elements": els_now}, indent=2))
                rescale_msg = (f"  Geometry rescaled: REF @ 0\", last DIR @ "
                               f"{fmt_in(new_cap_in)}.")
        st.cache_data.clear()
        st.success("Setup saved." + rescale_msg + "  Element count applies "
                   "after you Build / reseed (if you changed it).")
with b2:
    if st.button("🛠️ Build / reseed geometry to this element count",
                 use_container_width=True, key="su_build"):
        # Pass the locked boom length so the seeder COMPRESSES director spacings
        # to fit; nothing can exceed it.
        max_boom_in = (float(boom_length_ft) * 12.0
                       if (boom_mode == "fixed" and boom_length_ft) else None)
        new_geo = hybrid_seed.build_geometry(int(n_dir), center_mhz=center_mhz,
                                             max_boom_in=max_boom_in)
        GEO_PATH.write_text(json.dumps(new_geo, indent=2))
        last_pos = max(float(e["position_in"]) for e in new_geo["elements"])
        msg = (f"Built a fresh {len(new_geo['elements'])}-element hybrid "
               f"(boom span {fmt_in(last_pos)}).")
        if max_boom_in:
            msg += f"  Compressed to fit the locked {fmt_in(max_boom_in)} boom."
        st.success(msg + "  Now work down to Tune & Learn to tune it.")
        st.rerun()

# ---- Boom-lock enforcement banner -----------------------------------------
# If the user has the boom LOCKED and the current geometry overflows it,
# offer a one-click rescale that compresses director spacings to fit.  This
# catches imported .maa files, leftover geometries from a longer boom, and
# any seed that pre-dated the lock.
if boom_mode == "fixed" and boom_length_ft and geo.get("elements"):
    locked_in = float(boom_length_ft) * 12.0
    actual_span = max(float(e["position_in"]) for e in geo["elements"]) \
        - min(float(e["position_in"]) for e in geo["elements"])
    if actual_span > locked_in + 0.5:        # >1/2" over -> notify
        st.warning(
            f"⚠️ Current geometry boom span is **{fmt_in(actual_span)}**, "
            f"longer than the locked **{fmt_in(locked_in)}** boom."
        )
        if st.button("📏 Rescale positions to fit the locked boom",
                     key="su_rescale_to_boom", use_container_width=True):
            els_now = sorted(geo["elements"], key=lambda e: float(e["position_in"]))
            p0 = float(els_now[0]["position_in"])
            scale = locked_in / max(1e-9, actual_span)
            for e in els_now:
                e["position_in"] = round(p0 + (float(e["position_in"]) - p0) * scale, 3)
            GEO_PATH.write_text(json.dumps({"elements": els_now}, indent=2))
            st.cache_data.clear()
            st.success(f"Rescaled positions by ×{scale:.4f}.  Boom span is now "
                       f"{fmt_in(locked_in)}.  Re-tune lengths on Tune & Learn.")
            st.rerun()

st.markdown("### Current geometry")
els = geo.get("elements", [])
if els:
    cols = st.columns(min(4, len(els)))
    for i, e in enumerate(els):
        with cols[i % len(cols)]:
            st.caption(f"`{e['name']}`  pos {fmt_in(e['position_in'])}  "
                       f"len {fmt_in(e['length_in'])}")
else:
    st.info("No geometry yet — set the element count and hit Build / reseed.")

# ---------- Migrate hybrid runs from legacy yagi_history.db -----------------
# The legacy `opt_7el_yagi2.py` optimizer (still used by the Yagi Designer page)
# writes everything -- pure Yagis AND hybrids -- to ~/scripts/yagi_history.db.
# This runs the read-only migration: hybrid rows (those with XFRMR + COUPLER
# elements) get copied into auto7_history.db so the self-learning loop can
# benefit from them.  Pure-Yagi rows are skipped; the source DB is never
# modified; the import is idempotent (UNIQUE design_key catches duplicates).
with st.expander("📦 Import hybrid runs from legacy `yagi_history.db` "
                 "(does NOT touch the Yagi Designer)", expanded=False):
    legacy_default = str(pathlib.Path.home() / "scripts/yagi_history.db")
    legacy_path = st.text_input("Legacy DB path", value=legacy_default,
                                key="su_yagi_legacy_path")
    cdry, capp = st.columns(2)
    with cdry:
        if st.button("🔎 Dry-run (count only, no writes)",
                     key="su_yagi_migrate_dry", use_container_width=True):
            try:
                from scripts.migrate_yagi_history import migrate as _migrate
                stats = _migrate(pathlib.Path(legacy_path).expanduser(),
                                 ROOT / "data/auto7_history.db", dry_run=True)
                st.success(f"DRY RUN: would insert {stats['inserted']} "
                           f"hybrid runs · "
                           f"skip {stats['skipped_dup']} duplicates · "
                           f"skip {stats['skipped_no_xfrmr']} pure-Yagi runs · "
                           f"skip {stats['skipped_missing']} incomplete rows.")
            except Exception as ex:
                st.error(f"Migration failed: {ex}")
    with capp:
        if st.button("📥 Run migration", key="su_yagi_migrate_apply",
                     type="primary", use_container_width=True):
            try:
                from scripts.migrate_yagi_history import migrate as _migrate
                stats = _migrate(pathlib.Path(legacy_path).expanduser(),
                                 ROOT / "data/auto7_history.db", dry_run=False)
                st.success(f"Inserted **{stats['inserted']}** hybrid runs into "
                           f"`auto7_history.db`.  Skipped: "
                           f"{stats['skipped_dup']} dup · "
                           f"{stats['skipped_no_xfrmr']} pure-Yagi · "
                           f"{stats['skipped_missing']} incomplete.  Source "
                           f"file untouched (read-only).")
            except Exception as ex:
                st.error(f"Migration failed: {ex}")
    st.caption("Source DB is opened with `mode=ro` — nothing is modified.  "
               "Re-running is safe (skips rows already imported).  The "
               "Yagi Designer keeps using `yagi_history.db` exactly as before.")

# ---------- Import from MMANA-GAL .maa --------------------------------------
st.markdown("---")
st.markdown("### 📥 Import geometry from MMANA-GAL (`.maa`)")
st.caption("If you've micro-tuned an antenna in MMANA-GAL, upload its `.maa` "
           "file here to compare side-by-side and (optionally) adopt the new "
           "geometry as the working antenna.  Element span is read on Y, boom "
           "on X, height on Z; the DE is the fed wire (`w<n>c`).  Tune & Learn "
           "and Report then run against the imported lengths and positions, so "
           "what hybrid_auto7 shows == what MMANA-GAL shows.")
up = st.file_uploader("Upload .maa file", type=["maa", "txt"], key="su_maa_upload")
if up is not None:
    try:
        text = up.read().decode("utf-8", errors="replace")
        parsed = exporters.from_maa(text)
        new_els = parsed["elements"]
        st.success(f"Parsed {len(new_els)} elements from `{up.name}` "
                   f"(centre {parsed.get('center_mhz') or '?'} MHz).")

        # Side-by-side diff (per element, by name) vs the current geometry --
        # so the user can see exactly what MMANA-GAL changed during their
        # micro-tune.  Lookup by name keeps it sensible even if the boom
        # length shifted between the two geometries.
        cur_by_name = {str(e["name"]).upper(): e for e in els}
        st.markdown("**Imported vs current** (delta = imported − current)")
        diff_rows = []
        for e in new_els:
            nm = str(e["name"]).upper()
            old = cur_by_name.get(nm)
            old_pos = float(old["position_in"]) if old else None
            old_len = float(old["length_in"]) if old else None
            d_pos = (e["position_in"] - old_pos) if old_pos is not None else None
            d_len = (e["length_in"] - old_len) if old_len is not None else None
            diff_rows.append({
                "Element": nm,
                "pos (current)": fmt_in(old_pos) if old_pos is not None else "—",
                "pos (imported)": fmt_in(e["position_in"]),
                "Δ pos": fmt_in(d_pos) if d_pos is not None else "—",
                "len (current)": fmt_in(old_len) if old_len is not None else "—",
                "len (imported)": fmt_in(e["length_in"]),
                "Δ len": fmt_in(d_len) if d_len is not None else "—",
            })
        st.dataframe(diff_rows, hide_index=True, use_container_width=True)

        # Boom-lock check on import: warn if the .maa overruns the locked
        # boom, and offer an inline rescale on adoption.
        imp_span = (max(e["position_in"] for e in new_els)
                    - min(e["position_in"] for e in new_els))
        rescale_on_adopt = False
        if boom_mode == "fixed" and boom_length_ft:
            locked_in = float(boom_length_ft) * 12.0
            if imp_span > locked_in + 0.5:
                st.warning(
                    f"⚠️ Imported boom span **{fmt_in(imp_span)}** is longer "
                    f"than the locked boom **{fmt_in(locked_in)}**."
                )
                rescale_on_adopt = st.checkbox(
                    "Rescale imported positions to fit the locked boom on adopt",
                    value=True, key="su_maa_rescale",
                )

        if st.button("✅ Adopt imported geometry as current",
                     type="primary", key="su_adopt_maa"):
            adopted = [dict(e) for e in new_els]
            if rescale_on_adopt:
                els_sorted = sorted(adopted,
                                    key=lambda e: float(e["position_in"]))
                p0 = float(els_sorted[0]["position_in"])
                scale = (float(boom_length_ft) * 12.0) / max(1e-9, imp_span)
                for e in els_sorted:
                    e["position_in"] = round(
                        p0 + (float(e["position_in"]) - p0) * scale, 3)
                adopted = els_sorted
            GEO_PATH.write_text(json.dumps({"elements": adopted}, indent=2))
            # Sync setup's director count with what was actually imported.
            n_dirs_imp = sum(1 for e in adopted
                             if str(e["name"]).upper().startswith("DIR"))
            new_setup = dict(setup)
            new_setup["n_directors"] = n_dirs_imp
            SETUP_PATH.write_text(json.dumps(new_setup, indent=2))
            st.cache_data.clear()
            extra = (" (rescaled to fit the locked boom)" if rescale_on_adopt
                     else "")
            st.success(f"Adopted {len(adopted)} elements "
                       f"({n_dirs_imp} directors){extra}. "
                       "Now go to Tune & Learn to tune it.")
            st.rerun()
    except Exception as ex:
        st.error(f"Could not parse .maa: {ex}")

