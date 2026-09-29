"""Streamlit dashboard for scraped data and Website / Company / HR Analysis.

    pip install streamlit
    python -m streamlit run dashboard/app.py
(set SCRAPER_CONFIG=path/to/config.yaml to use a different config)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper.analyzer import AnalysisResult, WebsiteAnalyzer  # noqa: E402
from scraper.config import load_config  # noqa: E402
from scraper.storage import Storage  # noqa: E402

st.set_page_config(
    page_title="AutoScraper & Company/HR Analyzer",
    page_icon="🕸️",
    layout="wide",
)

cfg = load_config(os.environ.get("SCRAPER_CONFIG", ROOT / "config.yaml"))


@st.cache_data(ttl=20)
def load(db: str, target: str):
    with Storage(Path(db)) as s:
        return s.load_df(target), s.history_df(target)


@st.cache_data(ttl=20)
def load_runs(db: str):
    with Storage(Path(db)) as s:
        return s.targets(), s.runs_df(50)


st.title("🕸️ AutoScraper & Intelligence Dashboard")

# Top Navigation Tabs
tab_analyzer, tab_data, tab_trends, tab_runs = st.tabs([
    "🔍 Company & HR Analyzer",
    "📊 Scraped Database",
    "📈 Trends & History",
    "📋 Scraper Runs",
])

# ==============================================================================
# TAB 1: WEBSITE / COMPANY & HR ANALYZER
# ==============================================================================
with tab_analyzer:
    st.subheader("🌐 Website Analysis: Company, HR, Email & Contact Extractor")
    st.markdown(
        "Enter any website URL. The analyzer explores the website, scans relevant pages "
        "(e.g. *About*, *Contact*, *Careers*, *Team*), and extracts **Company Details**, "
        "**HR / Key People Contacts**, **Categorized Emails**, **Phone Numbers**, and **Social Profiles**."
    )

    preset_urls = list(cfg.analysis_urls)
    for t in cfg.targets:
        for u in t.start_urls:
            if u not in preset_urls:
                preset_urls.append(u)

    sel_preset = "-- Custom URL --"
    if preset_urls:
        sel_preset = st.selectbox(
            "Quick select a configured URL from config.yaml (or enter a custom URL below):",
            ["-- Custom URL --"] + preset_urls,
            key="analyzer_preset_select",
        )

    col_input, col_btn = st.columns([4, 1])
    with col_input:
        default_input = "" if sel_preset == "-- Custom URL --" else sel_preset
        target_url = st.text_input(
            "Website URL to analyze:",
            value=default_input,
            placeholder="e.g. https://openai.com or https://stripe.com",
            key="analyzer_url_input",
        )
    with col_btn:
        st.write("")
        st.write("")
        analyze_clicked = st.button("🚀 Analyze Website", type="primary", use_container_width=True)

    with st.expander("⚙️ Advanced Analyzer Options (Crawler & AI Model)", expanded=False):
        c_opt1, c_opt2 = st.columns(2)
        with c_opt1:
            crawl_subpages = st.checkbox("Crawl About, Team, Contact & Careers subpages", value=True)
            max_pages = st.slider("Max subpages to scan", min_value=1, max_value=8, value=4)
        with c_opt2:
            use_ai = st.checkbox("Enhance with AI Model (LLM)", value=False,
                                 help="Uses Gemini or OpenAI to synthesize unstructured team & company text.")
            ai_provider = st.selectbox("AI Provider", ["gemini", "openai", "ollama"], index=0)
            api_key = st.text_input(
                f"{ai_provider.upper()} API Key (optional if env var is set):",
                type="password",
                value=os.environ.get(f"{ai_provider.upper()}_API_KEY", ""),
            )

    if analyze_clicked and target_url.strip():
        with st.spinner(f"Analyzing {target_url.strip()} across key pages..."):
            analyzer = WebsiteAnalyzer(request_config=cfg.request)
            try:
                res = analyzer.analyze(
                    url=target_url.strip(),
                    crawl_subpages=crawl_subpages,
                    max_subpages=max_pages,
                    use_ai=use_ai,
                    ai_provider=ai_provider,
                    api_key=api_key.strip() or None,
                )
                st.session_state["analysis_result"] = res
            finally:
                analyzer.close()

    result: AnalysisResult | None = st.session_state.get("analysis_result")

    if result:
        if result.error:
            st.error(f"Analysis encountered an issue: {result.error}")
        else:
            st.success(f"Analysis complete for **{result.url}** using *{result.model_used}* ({len(result.scanned_urls)} pages scanned).")

            # Metrics Row
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("🏢 Company", result.company.get("name") or result.domain)
            m2.metric("👥 HR / Team Contacts", len(result.hr_contacts))
            m3.metric("📧 Emails Discovered", len(result.emails))
            m4.metric("📞 Phone Numbers", len(result.phones))

            # View Mode Selector
            view_mode = st.radio(
                "Display Format:",
                ["📊 Table View", "📝 List View", "💾 Raw JSON"],
                horizontal=True,
            )

            st.divider()

            # -------------------------------------------------------------
            # Format: Table View
            # -------------------------------------------------------------
            if view_mode == "📊 Table View":
                col_left, col_right = st.columns([1, 1])

                with col_left:
                    st.subheader("🏢 Company Information")
                    comp_df = result.to_company_df()
                    st.dataframe(comp_df, hide_index=True, use_container_width=True)
                    st.download_button(
                        "📥 Download Company Info (CSV)",
                        comp_df.to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"{result.domain}_company.csv",
                        mime="text/csv",
                        key="dl_comp_csv",
                    )

                with col_right:
                    st.subheader("👥 HR & Key People Contacts")
                    hr_df = result.to_hr_df()
                    if not hr_df.empty:
                        st.dataframe(hr_df, hide_index=True, use_container_width=True)
                        st.download_button(
                            "📥 Download HR Contacts (CSV)",
                            hr_df.to_csv(index=False).encode("utf-8-sig"),
                            file_name=f"{result.domain}_hr_contacts.csv",
                            mime="text/csv",
                            key="dl_hr_csv",
                        )
                    else:
                        st.info("No specific individuals identified by name. Check emails table below for HR/recruiting inboxes.")

                st.subheader("📧 Discovered Email Addresses")
                email_df = result.to_emails_df()
                if not email_df.empty:
                    st.dataframe(email_df, hide_index=True, use_container_width=True)
                    st.download_button(
                        "📥 Download Emails (CSV)",
                        email_df.to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"{result.domain}_emails.csv",
                        mime="text/csv",
                        key="dl_email_csv",
                    )
                else:
                    st.info("No direct email addresses found on scanned pages.")

                # Social Profiles & Open Positions
                c_soc, c_jobs = st.columns(2)
                with c_soc:
                    st.subheader("🔗 Social & Web Links")
                    soc_df = result.to_socials_df()
                    if not soc_df.empty:
                        st.dataframe(soc_df, hide_index=True, use_container_width=True)
                    else:
                        st.caption("No social links detected.")

                with c_jobs:
                    st.subheader("💼 Detected Career Openings")
                    openings = result.careers_info.get("openings_found", [])
                    if openings:
                        st.dataframe(pd.DataFrame(openings, columns=["Job Title / Position"]),
                                     hide_index=True, use_container_width=True)
                    elif result.careers_info.get("careers_url"):
                        st.markdown(f"Careers Page: [{result.careers_info['careers_url']}]({result.careers_info['careers_url']})")
                    else:
                        st.caption("No specific job postings detected.")

            # -------------------------------------------------------------
            # Format: List View
            # -------------------------------------------------------------
            elif view_mode == "📝 List View":
                st.markdown("### 🏢 Company Profile")
                st.markdown(f"- **Company Name:** {result.company.get('name') or 'N/A'}")
                st.markdown(f"- **Domain:** `{result.domain}`")
                st.markdown(f"- **Website URL:** [{result.url}]({result.url})")
                st.markdown(f"- **Description / Tagline:** {result.company.get('description') or 'N/A'}")
                st.markdown(f"- **Industry:** {result.company.get('industry') or 'N/A'}")
                st.markdown(f"- **Headquarters / Location:** {result.company.get('location') or 'N/A'}")
                if result.phones:
                    st.markdown(f"- **Phone Numbers:** {', '.join(result.phones)}")
                if result.careers_info.get("careers_url"):
                    st.markdown(f"- **Careers Page:** [{result.careers_info['careers_url']}]({result.careers_info['careers_url']})")

                st.markdown("### 👥 HR & Key Personnel")
                if result.hr_contacts:
                    for idx, c in enumerate(result.hr_contacts, 1):
                        name = c.get("name") or "Unnamed Contact"
                        role = c.get("role") or "Role not specified"
                        email = c.get("email") or "No direct email"
                        phone = c.get("phone") or "No direct phone"
                        st.markdown(f"{idx}. **{name}** — *{role}*\n   - Email: `{email}` | Phone: `{phone}`")
                else:
                    st.markdown("*(No individual HR personnel identified by name.)*")

                st.markdown("### 📧 Email Inboxes")
                if result.emails:
                    for e in result.emails:
                        st.markdown(f"- `{e['email']}` — **{e['category']}** *(Source: {e['source_url']})*")
                else:
                    st.markdown("*(No emails found on the scanned pages.)*")

                if result.social_links:
                    st.markdown("### 🔗 Social Profiles")
                    for plat, link in result.social_links.items():
                        st.markdown(f"- **{plat.capitalize()}:** [{link}]({link})")

                openings = result.careers_info.get("openings_found", [])
                if openings:
                    st.markdown("### 💼 Job Openings")
                    for op in openings:
                        st.markdown(f"- {op}")

            # -------------------------------------------------------------
            # Format: Raw JSON
            # -------------------------------------------------------------
            else:
                st.subheader("💾 Raw Analysis JSON")
                json_str = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
                st.json(result.to_dict())
                st.download_button(
                    "📥 Download Complete Analysis JSON",
                    json_str.encode("utf-8"),
                    file_name=f"{result.domain}_analysis.json",
                    mime="application/json",
                )

# ==============================================================================
# TAB 2: SCRAPED DATABASE
# ==============================================================================
with tab_data:
    targets, runs = load_runs(str(cfg.db_path))
    if not targets:
        st.info("Database is currently empty. Run `python main.py run` in terminal to scrape configured targets.")
    else:
        target = st.selectbox("Select Target", targets, key="data_target_select")
        df, hist = load(str(cfg.db_path), target)

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        c1, c2, c3 = st.columns(3)
        c1.metric("Items", len(df))
        c2.metric("New in last 24h", int((df["first_seen"] >= cutoff).sum()))
        c3.metric("Last seen", df["last_seen"].max().replace("T", " ")[:16] + " UTC")

        query = st.text_input("Search all columns", key="data_search_query")
        view = df
        if query:
            mask = df.astype(str).apply(lambda col: col.str.contains(query, case=False, regex=False)).any(axis=1)
            view = df[mask]
        st.dataframe(view, width="stretch", hide_index=True)
        st.download_button("Download CSV", view.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{target}.csv", mime="text/csv", key="dl_target_csv")

# ==============================================================================
# TAB 3: TRENDS
# ==============================================================================
with tab_trends:
    targets, _ = load_runs(str(cfg.db_path))
    if not targets:
        st.info("No targets to display trends for yet.")
    else:
        target_trend = st.selectbox("Target for Trends", targets, key="trends_target_select")
        df_trend, hist_trend = load(str(cfg.db_path), target_trend)
        numeric = [c for c in df_trend.columns if pd.api.types.is_numeric_dtype(df_trend[c])]
        if not numeric:
            st.write("No numeric columns to chart. Use `type: price`, `int`, `float` or `rating` on a field.")
        else:
            col = st.selectbox("Numeric column", numeric)
            counts, edges = np.histogram(df_trend[col].dropna(), bins=min(15, max(len(df_trend) // 2, 3)))
            labels = [f"{edges[i]:.0f}-{edges[i + 1]:.0f}" for i in range(len(counts))]
            st.caption(f"Distribution of {col}")
            st.bar_chart(pd.Series(counts, index=labels))

            st.subheader(f"{col} over time")
            label_col = next((c for c in df_trend.columns if df_trend[c].dtype == object and c not in ("first_seen", "last_seen")), None)
            if label_col and not hist_trend.empty:
                changed = hist_trend.groupby(label_col).size()
                changed = changed[changed > 1].index.tolist()
                if changed:
                    pick = st.selectbox("Item that changed", changed)
                    series = hist_trend[hist_trend[label_col] == pick].set_index("scraped_at")[col]
                    st.line_chart(series)
                else:
                    st.write("No item has changed between scrapes yet. Run the scraper again later to see history.")

# ==============================================================================
# TAB 4: RUNS
# ==============================================================================
with tab_runs:
    _, runs = load_runs(str(cfg.db_path))
    if runs.empty:
        st.info("No scraper runs logged yet.")
    else:
        st.dataframe(runs, width="stretch", hide_index=True)
