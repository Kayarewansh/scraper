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

import config
from overpass_api import OverpassClient, OverpassError
from enrichment import find_contact_info
from excel_export import COLUMNS as LEADS_COLUMNS, export_to_excel, export_generic, read_table, leads_workbook_bytes, generic_workbook_bytes
from condition_utils import CONDITION_OPERATORS, row_matches, is_numeric_column

ENRICH_WORKERS = 6

st.set_page_config(page_title="Lead Scraper", layout="wide")
st.title("Lead Scraper")

st.sidebar.markdown("**Save folder**")
st.sidebar.caption("Where \"Save to folder\" writes .xlsx files on this machine.")
save_dir = st.sidebar.text_input(
    "Save folder", value=config.load_save_folder(), label_visibility="collapsed", key="save_folder"
)
if save_dir.strip():
    try:
        os.makedirs(save_dir, exist_ok=True)
        config.save_save_folder(save_dir)
        LEADS_DIR = save_dir
    except OSError as e:
        st.sidebar.error(f"Can't use that folder: {e}")
        LEADS_DIR = config.load_save_folder()
else:
    LEADS_DIR = config.load_save_folder()

tab_search, tab_browse = st.tabs(["Search", "Browse Excel"])


# =====================================================================
# SEARCH TAB
# =====================================================================
def run_search(keyword, location, max_results, find_emails):
    status = st.empty()
    progress_bar = st.progress(0.0)

    client = OverpassClient()
    results = client.search(keyword, location, max_results, progress_callback=lambda m: status.caption(m))

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

    find_emails = st.checkbox("Find emails from websites (slower)", value=True)

    if st.button("Search", type="primary"):
        if not keyword.strip():
            st.warning("Enter a business type / keyword to search for.")
        elif not location.strip():
            st.warning("Enter a location (e.g. 'Dubai, UAE') to search in.")
        else:
            try:
                st.session_state["search_results"] = run_search(
                    keyword.strip(), location.strip(), max_results, find_emails
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
        if ec1.button("Save to folder"):
            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path = os.path.join(LEADS_DIR, f"leads_{stamp}.xlsx")
            export_to_excel(filtered, path)
            st.success(f"Saved {len(filtered)} rows to {path}")
        st.caption(
            "\"Save to folder\" writes to the Save folder set in the sidebar, on whatever machine is running "
            "this app — your Mac if you're running it locally, or a temporary cloud filesystem if you're using "
            "the hosted link (which won't persist). \"Download .xlsx\" always saves to your own computer, so "
            "prefer that on the hosted version."
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
        "Upload any .xlsx or .tsv — a dropdown filter is auto-built for each column with a small set of repeated values."
    )

    uploaded = st.file_uploader("Upload Excel or TSV file", type=["xlsx", "tsv"])
    if uploaded is not None:
        try:
            columns, rows = read_table(uploaded, filename=uploaded.name)
            if columns:
                if uploaded.name != st.session_state.get("browse_filename"):
                    st.session_state["browse_conditions"] = []  # new file — old conditions may not apply
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
        st.info("Upload an .xlsx or .tsv file to get started.")
    else:
        st.caption(f"Loaded {st.session_state.get('browse_filename', 'file')} — {len(rows)} rows, {len(columns)} columns.")

        text_query = st.text_input("Filter (any column)", key="browse_text_filter")

        st.markdown("**Conditions** — filter to rows matching a column/condition/value. Combine as many as you like.")
        conditions = st.session_state.setdefault("browse_conditions", [])

        remove_idx = None
        for i, cond in enumerate(conditions):
            cc1, cc2, cc3, cc4 = st.columns([3, 3, 3, 1])
            col_choice = cc1.selectbox("Column", columns, key=f"cond_col_{i}",
                                        index=columns.index(cond["column"]) if cond.get("column") in columns else 0)
            conditions[i]["column"] = col_choice

            op_choice = cc2.selectbox("Condition", CONDITION_OPERATORS, key=f"cond_op_{i}",
                                       index=CONDITION_OPERATORS.index(cond["operator"]) if cond.get("operator") in CONDITION_OPERATORS else 0)
            conditions[i]["operator"] = op_choice

            if is_numeric_column(rows, col_choice):
                val_choice = cc3.text_input("Value (number)", value=str(cond.get("value") or ""), key=f"cond_val_{i}")
                conditions[i]["value"] = val_choice if val_choice.strip() else None
            else:
                unique_vals = sorted({str(r.get(col_choice, "")).strip() for r in rows if str(r.get(col_choice, "")).strip()})
                val_index = unique_vals.index(cond["value"]) if cond.get("value") in unique_vals else 0
                val_choice = cc3.selectbox("Value", unique_vals, key=f"cond_val_{i}", index=val_index if unique_vals else 0)
                conditions[i]["value"] = val_choice if unique_vals else None

            cc4.markdown("<br>", unsafe_allow_html=True)
            if cc4.button("✕", key=f"cond_remove_{i}"):
                remove_idx = i

        if remove_idx is not None:
            conditions.pop(remove_idx)
            st.rerun()

        if st.button("+ Add condition"):
            default_col = columns[0]
            default_vals = sorted({str(r.get(default_col, "")).strip() for r in rows if str(r.get(default_col, "")).strip()})
            conditions.append({"column": default_col, "operator": "Equals", "value": default_vals[0] if default_vals else None})
            st.rerun()

        filtered_rows = rows
        for cond in conditions:
            if cond.get("column") and cond.get("value") is not None:
                filtered_rows = [r for r in filtered_rows if row_matches(r, cond["column"], cond["operator"], cond["value"])]
        if text_query.strip():
            q = text_query.strip().lower()
            filtered_rows = [r for r in filtered_rows if q in " ".join(str(r.get(c, "")) for c in columns).lower()]

        st.caption(f"{len(filtered_rows)} of {len(rows)} rows")
        st.dataframe(pd.DataFrame(filtered_rows, columns=columns), use_container_width=True, height=420)

        ec1, ec2 = st.columns(2)
        if ec1.button("Save to folder", key="browse_save"):
            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path = os.path.join(LEADS_DIR, f"filtered_{stamp}.xlsx")
            export_generic(filtered_rows, columns, path)
            st.success(f"Saved {len(filtered_rows)} rows to {path}")
        st.caption(
            "\"Save to folder\" writes to the Save folder set in the sidebar, on whatever machine is running "
            "this app — prefer \"Download .xlsx\" if you're using the hosted link, since that always saves to "
            "your own computer."
        )
        ec2.download_button(
            "Download .xlsx",
            data=generic_workbook_bytes(filtered_rows, columns),
            file_name="filtered.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="browse_download",
        )
