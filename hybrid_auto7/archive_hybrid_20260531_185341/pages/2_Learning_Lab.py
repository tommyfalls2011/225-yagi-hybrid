"""Learning Lab - visualize Yagi history DB, promote permanent seeds.

Reads ~/scripts/yagi_history.db (written by opt_7el_yagi2.py).
Promoted seeds live in ~/scripts/yagi_seeds/seed_n{N}.json and override
history-based seed selection in opt_7el_yagi2.py at run time.
"""
import json, sqlite3, os
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Yagi Learning Lab", layout="wide")
st.title("Yagi Learning Lab")
st.caption("Visualize history, promote winners as permanent seeds, watch the optimizer get smarter.")

HOME = Path(os.path.expanduser("~"))
DB_PATH = HOME / "scripts" / "yagi_history.db"
SEEDS_DIR = HOME / "scripts" / "yagi_seeds"
SEEDS_DIR.mkdir(parents=True, exist_ok=True)

st.caption(f"DB: `{DB_PATH}` {'(found)' if DB_PATH.exists() else '(missing)'}  |  Seeds: `{SEEDS_DIR}`")

if not DB_PATH.exists():
    st.error("yagi_history.db not found. Run the optimizer at least once.")
    st.stop()

# ---------- helpers ----------
@st.cache_data(ttl=10)
def load_runs():
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, timestamp, center_freq_mhz, final_score, final_gain_db, "
            "final_swr, final_fb_db, final_bw_mhz, geometry_json, tag, winner_stage "
            "FROM runs WHERE final_score IS NOT NULL ORDER BY id DESC"
        ).fetchall()
        con.close()
    except Exception as e:
        st.error(f"DB read failed: {e}")
        return pd.DataFrame()
    recs = []
    for r in rows:
        try:
            g = json.loads(r["geometry_json"]) if r["geometry_json"] else {}
        except Exception:
            g = {}
        lengths = g.get("lengths_ft") or g.get("lengths") or []
        spacings = g.get("spacings_ft") or g.get("spacings") or []
        height = g.get("height_ft") or g.get("height")
        boom = float(sum(spacings)) if spacings else None
        recs.append({
            "id": r["id"],
            "timestamp": r["timestamp"],
            "n_elements": len(lengths) if lengths else None,
            "center_freq_mhz": r["center_freq_mhz"],
            "boom_ft": round(boom, 2) if boom else None,
            "score": round(float(r["final_score"]), 1) if r["final_score"] is not None else None,
            "gain_db": round(float(r["final_gain_db"]), 2) if r["final_gain_db"] is not None else None,
            "swr": round(float(r["final_swr"]), 3) if r["final_swr"] is not None else None,
            "fb_db": round(float(r["final_fb_db"]), 1) if r["final_fb_db"] is not None else None,
            "bw_mhz": round(float(r["final_bw_mhz"]), 3) if r["final_bw_mhz"] is not None else None,
            "tag": r["tag"] or "",
            "height_ft": round(float(height), 1) if height else None,
            "lengths_ft": lengths,
            "spacings_ft": spacings,
        })
    return pd.DataFrame(recs)

def promote(row):
    n = int(row["n_elements"])
    seed_file = SEEDS_DIR / f"seed_n{n}.json"
    payload = {
        "n_elements": n,
        "lengths_ft": list(row["lengths_ft"]),
        "spacings_ft": list(row["spacings_ft"]),
        "height_ft": float(row["height_ft"]),
        "source_run_id": int(row["id"]),
        "score": float(row["score"]),
        "gain_db": float(row["gain_db"]),
        "center_freq_mhz": float(row["center_freq_mhz"]),
        "promoted_at": datetime.now().isoformat(timespec="seconds"),
        "note": f"Promoted from run #{row['id']} via Learning Lab",
    }
    seed_file.write_text(json.dumps(payload, indent=2))
    return seed_file

def unpromote(n_elements):
    seed_file = SEEDS_DIR / f"seed_n{n_elements}.json"
    if seed_file.exists():
        seed_file.unlink()
        return True
    return False

def list_promoted():
    out = []
    for f in sorted(SEEDS_DIR.glob("seed_n*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            pass
    return out

# ---------- load ----------
df = load_runs()
if df.empty:
    st.warning("No completed runs yet.")
    st.stop()

st.success(f"Loaded {len(df)} runs from history DB.")

# ---------- Section 1: Best by Element Count ----------
st.header("1. Best by Element Count")
st.caption("The top scoring run for each element count. Promote one to make it the permanent seed for that N.")

if df["n_elements"].notna().any():
    best = (df.dropna(subset=["n_elements", "score"])
              .sort_values("score", ascending=False)
              .groupby("n_elements", as_index=False)
              .first()
              .sort_values("n_elements"))
    promoted_now = {p["n_elements"]: p for p in list_promoted()}

    for _, row in best.iterrows():
        n = int(row["n_elements"])
        cols = st.columns([1, 1, 1, 1, 1, 1, 1, 1, 2])
        cols[0].metric("N", n)
        cols[1].metric("Score", row["score"])
        cols[2].metric("Gain dB", row["gain_db"])
        cols[3].metric("SWR", row["swr"])
        cols[4].metric("F/B dB", row["fb_db"])
        cols[5].metric("Boom ft", row["boom_ft"])
        cols[6].metric("cf MHz", row["center_freq_mhz"])
        cols[7].caption(f"Run #{int(row['id'])}")
        is_promoted = n in promoted_now and int(promoted_now[n].get("source_run_id", -1)) == int(row["id"])
        if is_promoted:
            cols[8].success("🌟 Currently promoted")
        else:
            if cols[8].button(f"🌟 Promote N={n}", key=f"prom_{n}_{int(row['id'])}"):
                f = promote(row)
                st.success(f"Promoted! Wrote {f}")
                st.cache_data.clear()
                st.rerun()
else:
    st.info("No element-count metadata in runs yet.")

# ---------- Section 2: Score History Chart ----------
st.header("2. Score History")
st.caption("Watch the optimizer get better over time.")

chart_df = df.dropna(subset=["score"]).copy()
chart_df["id"] = chart_df["id"].astype(int)
chart_df = chart_df.sort_values("id")
chart_df["n_elements"] = chart_df["n_elements"].fillna(0).astype(int).astype(str)

if not chart_df.empty:
    try:
        import altair as alt
        pivot = chart_df.pivot_table(index="id", columns="n_elements", values="score", aggfunc="max")
        st.line_chart(pivot, height=300)
    except Exception:
        st.line_chart(chart_df.set_index("id")["score"], height=300)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total runs", len(chart_df))
    c2.metric("Best score", round(chart_df["score"].max(), 1))
    c3.metric("Median score", round(chart_df["score"].median(), 1))
    c4.metric("Latest score", round(chart_df.iloc[-1]["score"], 1))

# ---------- Section 3: Recent Runs Browser ----------
st.header("3. Recent Runs")
n_show = st.slider("Show last N runs", 10, min(500, len(df)), 50)
sort_by = st.selectbox("Sort by", ["id", "score", "gain_db", "swr", "fb_db", "bw_mhz"], index=0)
ascending = st.checkbox("Ascending", value=False)
view = df.head(n_show).sort_values(sort_by, ascending=ascending)
st.dataframe(
    view[["id", "timestamp", "n_elements", "center_freq_mhz", "boom_ft",
          "score", "gain_db", "swr", "fb_db", "bw_mhz", "height_ft", "tag"]],
    use_container_width=True,
    hide_index=True,
)

# ---------- Section 4: Active Promoted Seeds ----------
st.header("4. Active Promoted Seeds")
promoted = list_promoted()
if not promoted:
    st.info("No permanent seeds yet. Promote a winner from Section 1 to install one.")
else:
    st.caption(f"These geometries override history-based seed selection in `opt_7el_yagi2.py`.")
    for p in promoted:
        n = p["n_elements"]
        cols = st.columns([1, 2, 1, 1, 1, 2, 1])
        cols[0].metric("N", n)
        cols[1].caption(f"Run #{p.get('source_run_id','?')}  •  {p.get('promoted_at','')[:19]}")
        cols[2].metric("Score", round(float(p.get("score", 0)), 1))
        cols[3].metric("Gain", round(float(p.get("gain_db", 0)), 2))
        cols[4].metric("cf MHz", p.get("center_freq_mhz"))
        cols[5].caption(f"`{SEEDS_DIR}/seed_n{n}.json`")
        if cols[6].button(f"Unpromote N={n}", key=f"unp_{n}"):
            if unpromote(n):
                st.warning(f"Unpromoted N={n}. Optimizer will fall back to history-based seeding.")
                st.rerun()
