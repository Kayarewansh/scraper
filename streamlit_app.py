"""Lead Scraper — browser version. Same backend as app.py (the desktop
app), different UI layer: this one runs a local web server you open in
your browser instead of a native window.

Run with:  streamlit run streamlit_app.py
Then open the URL it prints (default http://localhost:8501).
"""
import concurrent.futures
import datetime
import os

import pandas as pd
import streamlit as st

from overpass_api import OverpassClient, OverpassError
from enrichment import find_contact_info
from excel_export import COLUMNS as LEADS_COLUMNS, export_to_excel, export_generic, read_excel, leads_workbook_bytes, generic_workbook_bytes
from filter_utils import determine_filterable_columns

LEADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads")
os.makedirs(LEADS_DIR, exist_ok=True)

ENRICH_WORKERS = 6

st.set_page_config(page_title="Lead Scraper", layout="wide")
st.title("Lead Scraper")

tab_search, tab_browse = st.tabs(["Search", "Browse Excel"])


# =====================================================================
# SEARCH TAB
# =====================================================================
def run_search(keyword, location, max_results, require_website, require_phone, find_emails):
    status = st.empty()
    progress_bar = st.progress(0.0)

    client = OverpassClient()
    results = client.search(keyword, location, max_results, progress_callback=lambda m: status.caption(m))

    if require_website:
        results = [r for r in results if r["website"]]
    if require_phone:
        results = [r for r in results if r["phone"]]

    if find_emails:
        candidates = [r for r in results if r["website"]]
        total = len(candidates)
        if total:
            status.caption(f"Looking for emails on {total} websites...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
                future_map = {pool.submit(find_contact_info, r["website"]): r for r in candidates}
                done = 0
                for future in concurrent.futures.as_completed(future_map):
                    row = future_map[future]
                    try:
                        info = future.result()
                        row["email"] = info["email"]
                        row["first_name"] = info["first_name"]
                        row["last_name"] = info["last_name"]
                    except Exception:
                        pass
                    done += 1
                    progress_bar.progress(done / total)
                    status.caption(f"Finding emails: {done}/{total}")

    progress_bar.empty()
    status.caption(f"Done — {len(results)} results.")
    return results


with tab_search:
    st.caption("Free search via OpenStreetMap — no API key needed.")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        keyword = st.text_input("Business type / keyword", placeholder="e.g. digital marketing agency")
    with col2:
        location = st.text_input("Location", placeholder="e.g. Dubai, UAE")
    with col3:
        max_results = st.selectbox("Max results", [20, 40, 60], index=0)

    c1, c2, c3 = st.columns(3)
    require_website = c1.checkbox("Only results with a website")
    require_phone = c2.checkbox("Only results with a phone number")
    find_emails = c3.checkbox("Find emails from websites (slower)", value=True)

    if st.button("Search", type="primary"):
        if not keyword.strip():
            st.warning("Enter a business type / keyword to search for.")
        elif not location.strip():
            st.warning("Enter a location (e.g. 'Dubai, UAE') to search in.")
        else:
            try:
                st.session_state["search_results"] = run_search(
                    keyword.strip(), location.strip(), max_results, require_website, require_phone, find_emails
                )
            except OverpassError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Unexpected error: {e}")

    results = st.session_state.get("search_results", [])

    if results:
        st.divider()
        st.caption(
            "\"Size Signal\" is a heuristic from review count, website presence, and legal-entity naming "
            "(Pvt Ltd / LLP / Enterprises...) — it is NOT verified revenue data."
        )

        fc1, fc2, fc3, fc4 = st.columns(4)
        text_query = fc1.text_input("Filter (any column)", key="search_text_filter")
        cities = ["All cities"] + sorted({r["city"] for r in results if r["city"]})
        city_choice = fc2.selectbox("City", cities, key="search_city_filter")
        countries = ["All countries"] + sorted({r["country"] for r in results if r["country"]})
        country_choice = fc3.selectbox("Country", countries, key="search_country_filter")
        size_choice = fc4.selectbox(
            "Size signal",
            ["All sizes", "Likely larger business", "Mid-size / unclear", "Small / unclear"],
            key="search_size_filter",
        )

        filtered = results
        if city_choice != "All cities":
            filtered = [r for r in filtered if r["city"] == city_choice]
        if country_choice != "All countries":
            filtered = [r for r in filtered if r["country"] == country_choice]
        if size_choice != "All sizes":
            filtered = [r for r in filtered if r["size_label"] == size_choice]
        if text_query.strip():
            q = text_query.strip().lower()
            filtered = [r for r in filtered if q in " ".join(str(v) for v in r.values()).lower()]

        st.caption(f"{len(filtered)} of {len(results)} rows")

        display_rows = [{header: r.get(key, "") for key, header in LEADS_COLUMNS} for r in filtered]
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, height=420)

        ec1, ec2 = st.columns(2)
        if ec1.button("Save to leads/ folder"):
            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path = os.path.join(LEADS_DIR, f"leads_{stamp}.xlsx")
            export_to_excel(filtered, path)
            st.success(f"Saved {len(filtered)} rows to {path}")
        st.caption(
            "\"Save to leads/ folder\" writes on whatever machine is running this app — your Mac if you're "
            "running it locally, or a temporary cloud filesystem if you're using the hosted link (which won't "
            "persist). \"Download .xlsx\" always saves to your own computer, so prefer that on the hosted version."
        )
        ec2.download_button(
            "Download .xlsx",
            data=leads_workbook_bytes(filtered),
            file_name="leads.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# =====================================================================
# BROWSE EXCEL TAB
# =====================================================================
with tab_browse:
    st.caption(
        "Upload any .xlsx — a dropdown filter is auto-built for each column with a small set of repeated values."
    )

    uploaded = st.file_uploader("Upload Excel file", type=["xlsx"])
    if uploaded is not None:
        try:
            columns, rows = read_excel(uploaded)
            if columns:
                st.session_state["browse_columns"] = columns
                st.session_state["browse_rows"] = rows
                st.session_state["browse_filename"] = uploaded.name
            else:
                st.warning("That file has no columns to load.")
        except Exception as e:
            st.error(f"Could not read file: {e}")

    columns = st.session_state.get("browse_columns", [])
    rows = st.session_state.get("browse_rows", [])

    if not rows:
        st.info("Upload an .xlsx file to get started.")
    else:
        st.caption(f"Loaded {st.session_state.get('browse_filename', 'file')} — {len(rows)} rows, {len(columns)} columns.")

        filterable = determine_filterable_columns(columns, rows)
        text_query = st.text_input("Filter (any column)", key="browse_text_filter")

        selected = {}
        if filterable:
            filter_cols = st.columns(len(filterable))
            for i, (col, unique_vals) in enumerate(filterable):
                choice = filter_cols[i].selectbox(col, ["All"] + unique_vals, key=f"browse_filter_{col}")
                if choice != "All":
                    selected[col] = choice

        filtered_rows = rows
        for col, val in selected.items():
            filtered_rows = [r for r in filtered_rows if str(r.get(col, "")).strip() == val]
        if text_query.strip():
            q = text_query.strip().lower()
            filtered_rows = [r for r in filtered_rows if q in " ".join(str(r.get(c, "")) for c in columns).lower()]

        st.caption(f"{len(filtered_rows)} of {len(rows)} rows")
        st.dataframe(pd.DataFrame(filtered_rows, columns=columns), use_container_width=True, height=420)

        ec1, ec2 = st.columns(2)
        if ec1.button("Save to leads/ folder", key="browse_save"):
            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path = os.path.join(LEADS_DIR, f"filtered_{stamp}.xlsx")
            export_generic(filtered_rows, columns, path)
            st.success(f"Saved {len(filtered_rows)} rows to {path}")
        st.caption(
            "\"Save to leads/ folder\" writes on whatever machine is running this app — prefer "
            "\"Download .xlsx\" if you're using the hosted link, since that always saves to your own computer."
        )
        ec2.download_button(
            "Download .xlsx",
            data=generic_workbook_bytes(filtered_rows, columns),
            file_name="filtered.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="browse_download",
        )
