"""Streamlit dashboard for the scraped data.

    pip install streamlit
    python -m streamlit run dashboard/app.py
(set SCRAPER_CONFIG=path/to/config.yaml to use a different config)
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper.config import load_config  # noqa: E402
from scraper.storage import Storage  # noqa: E402

st.set_page_config(page_title="AutoScraper Dashboard", page_icon="🕸️", layout="wide")
cfg = load_config(os.environ.get("SCRAPER_CONFIG", ROOT / "config.yaml"))


@st.cache_data(ttl=20)
def load(db: str, target: str):
    with Storage(Path(db)) as s:
        return s.load_df(target), s.history_df(target)


@st.cache_data(ttl=20)
def load_runs(db: str):
    with Storage(Path(db)) as s:
        return s.targets(), s.runs_df(50)


st.title("🕸️ AutoScraper Dashboard")
targets, runs = load_runs(str(cfg.db_path))
if not targets:
    st.info("No data yet. Run `python main.py run` first.")
    st.stop()

target = st.sidebar.selectbox("Target", targets)
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
    st.rerun()

df, hist = load(str(cfg.db_path), target)
tab_data, tab_trends, tab_runs = st.tabs(["Data", "Trends", "Runs"])

with tab_data:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    c1, c2, c3 = st.columns(3)
    c1.metric("Items", len(df))
    c2.metric("New in last 24h", int((df["first_seen"] >= cutoff).sum()))
    c3.metric("Last seen", df["last_seen"].max().replace("T", " ")[:16] + " UTC")

    query = st.text_input("Search all columns")
    view = df
    if query:
        mask = df.astype(str).apply(lambda col: col.str.contains(query, case=False, regex=False)).any(axis=1)
        view = df[mask]
    st.dataframe(view, width="stretch", hide_index=True)
    st.download_button("Download CSV", view.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{target}.csv", mime="text/csv")

with tab_trends:
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not numeric:
        st.write("No numeric columns to chart. Use `type: price`, `int`, `float` or `rating` on a field.")
    else:
        col = st.selectbox("Numeric column", numeric)
        counts, edges = np.histogram(df[col].dropna(), bins=min(15, max(len(df) // 2, 3)))
        labels = [f"{edges[i]:.0f}-{edges[i + 1]:.0f}" for i in range(len(counts))]
        st.caption(f"Distribution of {col}")
        st.bar_chart(pd.Series(counts, index=labels))

        st.subheader(f"{col} over time")
        label_col = next((c for c in df.columns if df[c].dtype == object and c not in ("first_seen", "last_seen")), None)
        if label_col and not hist.empty:
            changed = hist.groupby(label_col).size()
            changed = changed[changed > 1].index.tolist()
            if changed:
                pick = st.selectbox("Item that changed", changed)
                series = hist[hist[label_col] == pick].set_index("scraped_at")[col]
                st.line_chart(series)
            else:
                st.write("No item has changed between scrapes yet. Run the scraper again later to see history.")

with tab_runs:
    st.dataframe(runs, width="stretch", hide_index=True)
