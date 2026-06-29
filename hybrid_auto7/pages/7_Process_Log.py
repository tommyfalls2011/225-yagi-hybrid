"""Process Log page -- every tuning move + an antenna-physics rationale.

Each move logged by the matcher is one row in `learned_moves`:
  (timestamp, design_signature, dof, value, band_max_swr, accepted)

This page walks that list for the most recent run and translates each move
into plain antenna language using hyagi.process_log: which element changed,
in which direction, what the predicted physics effect was, what the matcher
actually observed, and whether it accepted the move.

Read-only.  Nothing on this page tunes or saves; it just explains.
"""
import pathlib
import sqlite3
import sys

import streamlit as st

st.set_page_config(page_title="Process Log", layout="wide")

ROOT = pathlib.Path.home() / "scripts/hybrid_auto7"
DB_PATH = ROOT / "data/auto7_history.db"

sys.path.insert(0, str(ROOT))
from hyagi.units import fmt_in                 # noqa: E402
from hyagi import process_log as plg           # noqa: E402


st.title("🔬 Process Log · move-by-move tune diary")
st.caption("Every coordinate-descent move the matcher tried in the most "
           "recent run, with an antenna-physics reason for why it was kept "
           "or thrown out.  Read-only; nothing here re-tunes.")

if not DB_PATH.exists():
    st.info("No learning DB yet — run a tune on **Tune & Learn** first.")
    st.stop()

con = sqlite3.connect(str(DB_PATH))
con.row_factory = sqlite3.Row

# Latest design signature on top so the page always opens on whatever was
# just tuned; user can switch to an older design if they want to inspect it.
sigs = [r["signature"] for r in con.execute(
    "SELECT signature, MAX(created_utc) AS t FROM learned_moves "
    "GROUP BY signature ORDER BY t DESC LIMIT 20"
).fetchall()]
if not sigs:
    st.info("No moves logged yet — run an auto-matcher tune on **Tune & Learn**.")
    con.close()
    st.stop()

selected_sig = st.selectbox("Design signature (taper | band | height | n_elements)",
                            sigs, index=0, key="pl_sig")

limit = st.slider("Show last N moves", 20, 2000, 200, step=20, key="pl_limit",
                  help="The matcher logs every accepted AND rejected move "
                       "during a run.  Showing 200 is usually enough to "
                       "explain the tail end of the tune; raise it to walk "
                       "the entire descent.")

rows = list(con.execute(
    "SELECT created_utc, dof, value, band_max_swr, accepted "
    "FROM learned_moves WHERE signature = ? "
    "ORDER BY id DESC LIMIT ?", (selected_sig, int(limit))
).fetchall())
rows = list(reversed(rows))            # chronological order on screen

# Aggregate stats for the design (separate from the visible window so the
# user sees the bigger picture even when only the last N moves are shown).
total = con.execute("SELECT COUNT(*) FROM learned_moves WHERE signature=?",
                    (selected_sig,)).fetchone()[0]
kept = con.execute("SELECT COUNT(*) FROM learned_moves "
                   "WHERE signature=? AND accepted=1",
                   (selected_sig,)).fetchone()[0]
best = con.execute("SELECT MIN(band_max_swr) FROM learned_moves WHERE signature=?",
                   (selected_sig,)).fetchone()[0]
worst = con.execute("SELECT MAX(band_max_swr) FROM learned_moves WHERE signature=?",
                    (selected_sig,)).fetchone()[0]
con.close()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total moves logged", f"{total:,}")
c2.metric("Kept (accepted)", f"{kept:,}",
          help=f"Acceptance rate: {100.0 * kept / max(1, total):.1f}%")
c3.metric("Best band-max SWR seen", f"{(best or 0):.3f}")
c4.metric("Worst band-max SWR seen", f"{(worst or 0):.3f}")

st.markdown("---")

# Per-DOF previous value tracking so each move reads as a DIRECTION of
# change ('+0.50 in longer') rather than just the new absolute value.
prev_val: dict[str, float] = {}
prev_swr: dict[str, float] = {}

for i, r in enumerate(rows, start=1):
    dof = str(r["dof"])
    new_val = float(r["value"])
    new_swr_val = float(r["band_max_swr"])
    accepted = bool(r["accepted"])
    name, kind = plg.parse_dof(dof)
    pv = prev_val.get(dof)
    ps = prev_swr.get(dof)
    direction_txt = plg.direction_label(pv, new_val, kind)
    physics_txt = plg.physics_prediction(name, kind,
                                         (new_val - pv) if pv is not None else 0.0)
    reason_txt = plg.accept_reason(accepted, ps, new_swr_val)

    val_disp = fmt_in(new_val) if kind in ("len", "gap") else f"{new_val:.3f}"
    icon = "✅" if accepted else "❌"

    with st.expander(
        f"{icon}  Move #{i}  ·  `{dof}` → {val_disp}  ·  "
        f"band-max {new_swr_val:.3f}",
        expanded=False,
    ):
        st.markdown(f"**Element:** `{name}`  ·  **Parameter:** {kind}")
        st.caption(plg.element_role(name))
        st.markdown(f"**Change:** {direction_txt}")
        if physics_txt:
            st.markdown(f"**Why try it:** {physics_txt}")
        st.markdown(f"**Result:** band-max SWR after this move = "
                    f"**{new_swr_val:.3f}** · "
                    f"{'✅ ' if accepted else '❌ '}{reason_txt}")
        if r["created_utc"]:
            st.caption(f"logged {r['created_utc']}")

    if accepted:
        # Only carry FORWARD an accepted move; rejected moves don't update
        # the working geometry, so the next attempt on the same DOF compares
        # against the same previous value.
        prev_val[dof] = new_val
        prev_swr[dof] = new_swr_val

st.markdown("---")
st.markdown(
    "**Legend** — "
    "**Longer** elements resonate at LOWER frequency. "
    "**Shorter** elements resonate at HIGHER frequency. "
    "**Closer to DE** = stronger coupling (lower centre R, capacitive). "
    "**Further from DE** = weaker coupling (higher R, inductive). "
    "The matcher's objective ranks moves by your priority ladder: "
    "1) |X|≤2.5 Ω, 2) high return loss, 3) SWR ≤ 1.07, "
    "4) gain over F/B (F/B floor 12 dB).  Rejected moves violated one of "
    "those constraints even if pure band-max SWR looked tempting."
)
