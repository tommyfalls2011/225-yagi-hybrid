import streamlit as st
st.set_page_config(page_title="hybrid_auto7 v2", layout="wide")
st.title("hybrid_auto7  ·  v2")
st.caption("Pick a page from the sidebar.")
st.markdown("""
- **Yagi Designer** — pure Yagi-Uda optimizer (untouched)
- **Rules** — per-element length bounds + per-pair spacing bounds
- **Cell Definition** — initial cell geometry + tune order
- **Mini-Tunes** — small reusable tune primitives  *(coming next turn)*
- **Procedures** — chains of mini-tunes  *(coming next turn)*
- **Run** — execute a procedure  *(coming turn after)*
- **Learning** — accumulated results  *(coming turn after)*
""")
