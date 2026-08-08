"""Lead Scraper — search OpenStreetMap for businesses (free, no API key),
or upload any Excel file and filter it by column. Two tabs, one app.

Run with:  python3 app.py
"""
import os
import queue
import threading
import concurrent.futures
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk

from overpass_api import OverpassClient, OverpassError
from enrichment import find_contact_info
from excel_export import export_to_excel, export_generic, read_excel
from condition_utils import CONDITION_OPERATORS, row_matches, is_numeric_column

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

LEADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads")
os.makedirs(LEADS_DIR, exist_ok=True)

SEARCH_COLUMNS = [
    ("company", "Company Name", 180),
    ("first_name", "First Name", 90),
    ("last_name", "Last Name", 90),
    ("email", "Email", 180),
    ("phone", "Contact Number", 130),
    ("address", "Full Address", 260),
    ("city", "City", 110),
    ("country", "Country", 100),
    ("website", "Website", 180),
    ("review_count", "Google Reviews", 100),
    ("size_label", "Size Signal", 150),
]

ENRICH_WORKERS = 6


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Lead Scraper")
        self.geometry("1300x780")
        self.minsize(1050, 620)

        self.msg_queue = queue.Queue()
        self.all_results = []  # full unfiltered search-tab result rows
        self.search_running = False

        self.browse_columns = []  # ordered header list from the uploaded file
        self.browse_all_rows = []
        self.browse_visible_rows = []
        self.browse_filter_vars = {}  # {column: (StringVar, "All ..." sentinel)}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, sticky="nswe")
        self.tabview.add("Search")
        self.tabview.add("Browse Excel")

        self._build_search_tab(self.tabview.tab("Search"))
        self._build_browse_tab(self.tabview.tab("Browse Excel"))
        self._style_treeview()

        self.after(100, self._poll_queue)

    # ---------- shared table helper ----------
    def _make_scrollable_tree(self, table_frame, col_ids, headers_widths):
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(table_frame, columns=col_ids, show="headings", selectmode="extended")
        for key, header, width in headers_widths:
            tree.heading(key, text=header)
            tree.column(key, width=width, anchor="w")
        tree.grid(row=0, column=0, sticky="nswe")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        hsb.grid(row=1, column=0, sticky="we")
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        return tree

    def _style_treeview(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#111827",
            fieldbackground="#111827",
            foreground="#E5E7EB",
            rowheight=26,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#1F2937",
            foreground="#F9FAFB",
            relief="flat",
            font=("Helvetica", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#2563EB")])

    # =====================================================================
    # SEARCH TAB
    # =====================================================================
    def _build_search_tab(self, parent):
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        self._build_sidebar(parent)
        self._build_search_main(parent)

    def _build_sidebar(self, parent):
        sidebar = ctk.CTkFrame(parent, width=300, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nswe")
        sidebar.grid_propagate(False)

        pad = {"padx": 16, "pady": (10, 0)}

        ctk.CTkLabel(sidebar, text="Search", font=ctk.CTkFont(size=20, weight="bold")).pack(
            anchor="w", padx=16, pady=(20, 4)
        )
        ctk.CTkLabel(
            sidebar,
            text="Free search via OpenStreetMap —\nno API key needed. Export to Excel.",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 16))

        ctk.CTkLabel(sidebar, text="Business type / keyword", **_lbl()).pack(**pad, anchor="w")
        self.keyword_entry = ctk.CTkEntry(sidebar, placeholder_text="e.g. digital marketing agency")
        self.keyword_entry.pack(padx=16, pady=(4, 0), fill="x")

        ctk.CTkLabel(sidebar, text="Location", **_lbl()).pack(**pad, anchor="w")
        self.location_entry = ctk.CTkEntry(sidebar, placeholder_text="e.g. Pune, India")
        self.location_entry.pack(padx=16, pady=(4, 0), fill="x")

        ctk.CTkLabel(sidebar, text="Max results", **_lbl()).pack(**pad, anchor="w")
        self.max_results_var = ctk.StringVar(value="20")
        ctk.CTkOptionMenu(sidebar, values=["20", "40", "60"], variable=self.max_results_var).pack(
            padx=16, pady=(4, 0), fill="x"
        )

        self.require_website_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            sidebar, text="Only results with a website", variable=self.require_website_var
        ).pack(padx=16, pady=(14, 0), anchor="w")

        self.require_phone_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            sidebar, text="Only results with a phone number", variable=self.require_phone_var
        ).pack(padx=16, pady=(8, 0), anchor="w")

        self.find_emails_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            sidebar, text="Find emails from websites (slower)", variable=self.find_emails_var
        ).pack(padx=16, pady=(8, 0), anchor="w")

        self.search_button = ctk.CTkButton(sidebar, text="Search", command=self.start_search)
        self.search_button.pack(padx=16, pady=(18, 0), fill="x")

        self.progress_bar = ctk.CTkProgressBar(sidebar)
        self.progress_bar.set(0)
        self.progress_bar.pack(padx=16, pady=(12, 0), fill="x")

        self.status_label = ctk.CTkLabel(
            sidebar, text="Ready.", font=ctk.CTkFont(size=11), text_color="gray60", wraplength=260, justify="left"
        )
        self.status_label.pack(padx=16, pady=(6, 0), anchor="w")

        ctk.CTkLabel(
            sidebar,
            text=(
                "Data source: OpenStreetMap (free, no key). "
                "Coverage is patchier than Google — many businesses will be "
                "missing or have no phone/website listed."
            ),
            font=ctk.CTkFont(size=10),
            text_color="gray50",
            wraplength=260,
            justify="left",
        ).pack(side="bottom", padx=16, pady=16, anchor="w")

    def _build_search_main(self, parent):
        main = ctk.CTkFrame(parent, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nswe", padx=(0, 0), pady=0)
        main.grid_rowconfigure(2, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # filter bar
        filter_bar = ctk.CTkFrame(main, fg_color="transparent")
        filter_bar.grid(row=0, column=0, sticky="we", padx=16, pady=(16, 0))

        self.filter_text_var = ctk.StringVar()
        self.filter_text_var.trace_add("write", lambda *_: self._apply_filters())
        ctk.CTkEntry(
            filter_bar, textvariable=self.filter_text_var, placeholder_text="Filter results (any column)...", width=260
        ).pack(side="left", padx=(0, 8))

        self.city_filter_var = ctk.StringVar(value="All cities")
        self.city_filter_menu = ctk.CTkOptionMenu(
            filter_bar, values=["All cities"], variable=self.city_filter_var, command=lambda *_: self._apply_filters()
        )
        self.city_filter_menu.pack(side="left", padx=(0, 8))

        self.country_filter_var = ctk.StringVar(value="All countries")
        self.country_filter_menu = ctk.CTkOptionMenu(
            filter_bar,
            values=["All countries"],
            variable=self.country_filter_var,
            command=lambda *_: self._apply_filters(),
        )
        self.country_filter_menu.pack(side="left", padx=(0, 8))

        size_options = ["All sizes", "Likely larger business", "Mid-size / unclear", "Small / unclear"]
        self.size_filter_var = ctk.StringVar(value="All sizes")
        self.size_filter_menu = ctk.CTkOptionMenu(
            filter_bar, values=size_options, variable=self.size_filter_var, command=lambda *_: self._apply_filters()
        )
        self.size_filter_menu.pack(side="left", padx=(0, 8))

        ctk.CTkButton(filter_bar, text="Clear filters", width=100, command=self._clear_filters).pack(side="left")

        self.row_count_label = ctk.CTkLabel(filter_bar, text="0 rows", text_color="gray60")
        self.row_count_label.pack(side="right")

        ctk.CTkLabel(
            main,
            text=(
                "\"Size Signal\" is a heuristic from review count, website presence, and legal-entity naming "
                "(Pvt Ltd / LLP / Enterprises...) — it is NOT verified revenue data."
            ),
            font=ctk.CTkFont(size=10),
            text_color="gray50",
            wraplength=1000,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(4, 8))

        # table
        table_frame = ctk.CTkFrame(main, fg_color="#111827")
        table_frame.grid(row=2, column=0, sticky="nswe", padx=16, pady=(0, 8))
        col_ids = [c[0] for c in SEARCH_COLUMNS]
        self.search_tree = self._make_scrollable_tree(table_frame, col_ids, SEARCH_COLUMNS)

        # bottom bar
        bottom_bar = ctk.CTkFrame(main, fg_color="transparent")
        bottom_bar.grid(row=3, column=0, sticky="we", padx=16, pady=(0, 16))
        ctk.CTkButton(bottom_bar, text="Export visible rows to Excel", command=self.export_results).pack(
            side="left"
        )
        ctk.CTkButton(
            bottom_bar, text="Clear results", fg_color="#4B5563", hover_color="#374151", command=self.clear_results
        ).pack(side="left", padx=(8, 0))

    # ---------- search ----------
    def start_search(self):
        if self.search_running:
            return

        keyword = self.keyword_entry.get().strip()
        location = self.location_entry.get().strip()

        if not keyword:
            messagebox.showwarning("Missing keyword", "Enter a business type / keyword to search for.")
            return
        if not location:
            messagebox.showwarning("Missing location", "Enter a location (e.g. 'Dubai, UAE') to search in.")
            return

        max_results = int(self.max_results_var.get())
        require_website = self.require_website_var.get()
        require_phone = self.require_phone_var.get()
        find_emails = self.find_emails_var.get()

        self.search_running = True
        self.search_button.configure(state="disabled", text="Searching...")
        self.progress_bar.set(0)
        self._set_status("Searching OpenStreetMap...")

        thread = threading.Thread(
            target=self._run_search,
            args=(keyword, location, max_results, require_website, require_phone, find_emails),
            daemon=True,
        )
        thread.start()

    def _run_search(self, keyword, location, max_results, require_website, require_phone, find_emails):
        try:
            client = OverpassClient()
            results = client.search(
                keyword, location, max_results, progress_callback=lambda m: self.msg_queue.put(("status", m))
            )

            if require_website:
                results = [r for r in results if r["website"]]
            if require_phone:
                results = [r for r in results if r["phone"]]

            if find_emails:
                candidates = [r for r in results if r["website"]]
                total = len(candidates)
                done = 0
                if total:
                    self.msg_queue.put(("status", f"Looking for emails on {total} websites..."))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
                        future_map = {pool.submit(find_contact_info, r["website"]): r for r in candidates}
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
                            self.msg_queue.put(("enrich_progress", done, total))

            self.msg_queue.put(("done", results))
        except OverpassError as e:
            self.msg_queue.put(("error", str(e)))
        except Exception as e:
            self.msg_queue.put(("error", f"Unexpected error: {e}"))

    # ---------- queue polling (runs on main thread) ----------
    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self._set_status(msg[1])
                elif kind == "enrich_progress":
                    done, total = msg[1], msg[2]
                    self.progress_bar.set(done / total if total else 0)
                    self._set_status(f"Finding emails: {done}/{total}")
                elif kind == "done":
                    self._on_search_done(msg[1])
                elif kind == "error":
                    self._on_search_error(msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_search_done(self, results):
        self.all_results = results
        self.search_running = False
        self.search_button.configure(state="normal", text="Search")
        self.progress_bar.set(1)
        self._set_status(f"Done. Found {len(results)} results.")
        self._refresh_filter_options()
        self._apply_filters()

    def _on_search_error(self, message):
        self.search_running = False
        self.search_button.configure(state="normal", text="Search")
        self.progress_bar.set(0)
        self._set_status("Error — see popup.")
        messagebox.showerror("Search failed", message)

    def _set_status(self, text):
        self.status_label.configure(text=text)

    # ---------- search-tab filters ----------
    def _refresh_filter_options(self):
        cities = sorted({r["city"] for r in self.all_results if r["city"]})
        countries = sorted({r["country"] for r in self.all_results if r["country"]})
        self.city_filter_menu.configure(values=["All cities"] + cities)
        self.country_filter_menu.configure(values=["All countries"] + countries)

    def _clear_filters(self):
        self.filter_text_var.set("")
        self.city_filter_var.set("All cities")
        self.country_filter_var.set("All countries")
        self.size_filter_var.set("All sizes")
        self._apply_filters()

    def _apply_filters(self):
        text = self.filter_text_var.get().strip().lower()
        city = self.city_filter_var.get()
        country = self.country_filter_var.get()
        size = self.size_filter_var.get()

        rows = self.all_results
        if city != "All cities":
            rows = [r for r in rows if r["city"] == city]
        if country != "All countries":
            rows = [r for r in rows if r["country"] == country]
        if size != "All sizes":
            rows = [r for r in rows if r["size_label"] == size]
        if text:
            rows = [
                r
                for r in rows
                if text in " ".join(str(r.get(k, "")) for k, _, _ in SEARCH_COLUMNS).lower()
            ]

        self._populate_search_table(rows)

    def _populate_search_table(self, rows):
        self.search_tree.delete(*self.search_tree.get_children())
        for r in rows:
            values = [r.get(key, "") for key, _, _ in SEARCH_COLUMNS]
            self.search_tree.insert("", "end", values=values)
        self.row_count_label.configure(text=f"{len(rows)} of {len(self.all_results)} rows")
        self._visible_rows = rows

    # ---------- search-tab export / clear ----------
    def export_results(self):
        rows = getattr(self, "_visible_rows", [])
        if not rows:
            messagebox.showinfo("Nothing to export", "No rows to export. Run a search first.")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialdir=LEADS_DIR,
            initialfile="leads.xlsx",
        )
        if not filepath:
            return
        try:
            export_to_excel(rows, filepath)
            messagebox.showinfo("Exported", f"Saved {len(rows)} rows to:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def clear_results(self):
        self.all_results = []
        self._refresh_filter_options()
        self._apply_filters()
        self._set_status("Cleared.")
        self.progress_bar.set(0)

    # =====================================================================
    # BROWSE EXCEL TAB
    # =====================================================================
    def _build_browse_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(5, weight=1)

        top_bar = ctk.CTkFrame(parent, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="we", padx=16, pady=(16, 0))
        ctk.CTkButton(top_bar, text="Upload Excel...", command=self._upload_excel).pack(side="left")
        self.browse_file_label = ctk.CTkLabel(
            top_bar, text="No file loaded.", text_color="gray60"
        )
        self.browse_file_label.pack(side="left", padx=(12, 0))

        ctk.CTkLabel(
            parent,
            text="Add conditions to filter to rows where a column equals a specific value. Combine as many as you like.",
            font=ctk.CTkFont(size=10),
            text_color="gray50",
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(6, 0))

        text_bar = ctk.CTkFrame(parent, fg_color="transparent")
        text_bar.grid(row=2, column=0, sticky="we", padx=16, pady=(10, 0))
        self.browse_text_var = ctk.StringVar()
        self.browse_text_var.trace_add("write", lambda *_: self._apply_browse_filters())
        ctk.CTkEntry(
            text_bar, textvariable=self.browse_text_var, placeholder_text="Filter (any column)...", width=260
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(text_bar, text="Clear all filters", width=120, command=self._clear_browse_filters).pack(
            side="left"
        )
        self.browse_row_count_label = ctk.CTkLabel(text_bar, text="0 rows", text_color="gray60")
        self.browse_row_count_label.pack(side="right")

        self.browse_conditions_container = ctk.CTkFrame(parent, fg_color="transparent")
        self.browse_conditions_container.grid(row=3, column=0, sticky="we", padx=16, pady=(8, 0))

        ctk.CTkButton(parent, text="+ Add condition", width=140, command=self._add_browse_condition).grid(
            row=4, column=0, sticky="w", padx=16, pady=(4, 0)
        )

        table_frame = ctk.CTkFrame(parent, fg_color="#111827")
        table_frame.grid(row=5, column=0, sticky="nswe", padx=16, pady=(8, 8))
        self.browse_tree = self._make_scrollable_tree(table_frame, [], [])

        bottom_bar = ctk.CTkFrame(parent, fg_color="transparent")
        bottom_bar.grid(row=6, column=0, sticky="we", padx=16, pady=(0, 16))
        ctk.CTkButton(bottom_bar, text="Export visible rows to Excel", command=self._export_browse_results).pack(
            side="left"
        )

        self.browse_conditions = []  # list of {"column": str, "value": str}

    def _upload_excel(self):
        filepath = filedialog.askopenfilename(
            title="Upload Excel file",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialdir=LEADS_DIR,
        )
        if not filepath:
            return
        try:
            columns, rows = read_excel(filepath)
        except Exception as e:
            messagebox.showerror("Upload failed", str(e))
            return
        if not columns:
            messagebox.showwarning("Empty file", "That file has no columns to load.")
            return

        self.browse_columns = columns
        self.browse_all_rows = rows
        self.browse_conditions = []
        self.browse_text_var.set("")
        self.browse_file_label.configure(text=f"{os.path.basename(filepath)} ({len(rows)} rows)")

        self._rebuild_browse_table_columns(columns)
        self._render_browse_conditions()
        self._apply_browse_filters()

    def _rebuild_browse_table_columns(self, columns):
        self.browse_tree["columns"] = columns
        self.browse_tree["show"] = "headings"
        for col in columns:
            self.browse_tree.heading(col, text=col)
            width = min(max(len(col) * 9, 100), 260)
            self.browse_tree.column(col, width=width, anchor="w")

    def _unique_values_for_column(self, col):
        return sorted({str(r.get(col, "")).strip() for r in self.browse_all_rows if str(r.get(col, "")).strip()})

    def _add_browse_condition(self):
        if not self.browse_columns:
            return
        default_col = self.browse_columns[0]
        if is_numeric_column(self.browse_all_rows, default_col):
            default_val = ""
        else:
            vals = self._unique_values_for_column(default_col)
            default_val = vals[0] if vals else ""
        self.browse_conditions.append(
            {"column": default_col, "operator": CONDITION_OPERATORS[0], "value": default_val}
        )
        self._render_browse_conditions()
        self._apply_browse_filters()

    def _render_browse_conditions(self):
        for child in self.browse_conditions_container.winfo_children():
            child.destroy()

        for i, cond in enumerate(self.browse_conditions):
            row = ctk.CTkFrame(self.browse_conditions_container, fg_color="transparent")
            row.pack(fill="x", pady=(0, 6))

            col_var = ctk.StringVar(value=cond["column"])

            def on_col_change(choice, i=i):
                self.browse_conditions[i]["column"] = choice
                if is_numeric_column(self.browse_all_rows, choice):
                    self.browse_conditions[i]["value"] = ""
                else:
                    vals = self._unique_values_for_column(choice)
                    self.browse_conditions[i]["value"] = vals[0] if vals else ""
                self._render_browse_conditions()
                self._apply_browse_filters()

            ctk.CTkOptionMenu(
                row, values=self.browse_columns, variable=col_var, width=150, command=on_col_change
            ).pack(side="left", padx=(0, 8))

            op_var = ctk.StringVar(value=cond.get("operator", CONDITION_OPERATORS[0]))

            def on_op_change(choice, i=i):
                self.browse_conditions[i]["operator"] = choice
                self._apply_browse_filters()

            ctk.CTkOptionMenu(
                row, values=CONDITION_OPERATORS, variable=op_var, width=150, command=on_op_change
            ).pack(side="left", padx=(0, 8))

            if is_numeric_column(self.browse_all_rows, cond["column"]):
                val_var = ctk.StringVar(value=cond["value"])

                def on_val_typed(*_args, i=i, var=val_var):
                    self.browse_conditions[i]["value"] = var.get()
                    self._apply_browse_filters()

                val_var.trace_add("write", on_val_typed)
                ctk.CTkEntry(row, textvariable=val_var, width=150, placeholder_text="Enter a number").pack(
                    side="left", padx=(0, 8)
                )
            else:
                unique_vals = self._unique_values_for_column(cond["column"])
                current_val = cond["value"] if cond["value"] in unique_vals else (unique_vals[0] if unique_vals else "")
                cond["value"] = current_val
                val_var = ctk.StringVar(value=current_val)

                def on_val_change(choice, i=i):
                    self.browse_conditions[i]["value"] = choice
                    self._apply_browse_filters()

                ctk.CTkOptionMenu(
                    row, values=unique_vals or [""], variable=val_var, width=150, command=on_val_change
                ).pack(side="left", padx=(0, 8))

            def on_remove(i=i):
                self.browse_conditions.pop(i)
                self._render_browse_conditions()
                self._apply_browse_filters()

            ctk.CTkButton(
                row, text="✕", width=28, fg_color="#4B5563", hover_color="#374151", command=on_remove
            ).pack(side="left")

    def _clear_browse_filters(self):
        self.browse_text_var.set("")
        self.browse_conditions = []
        self._render_browse_conditions()
        self._apply_browse_filters()

    def _apply_browse_filters(self):
        text = self.browse_text_var.get().strip().lower()
        rows = self.browse_all_rows

        for cond in self.browse_conditions:
            if cond["column"] and cond["value"]:
                rows = [r for r in rows if row_matches(r, cond["column"], cond["operator"], cond["value"])]

        if text:
            rows = [
                r
                for r in rows
                if text in " ".join(str(r.get(c, "")) for c in self.browse_columns).lower()
            ]

        self._populate_browse_table(rows)

    def _populate_browse_table(self, rows):
        self.browse_tree.delete(*self.browse_tree.get_children())
        for r in rows:
            values = [r.get(c, "") for c in self.browse_columns]
            self.browse_tree.insert("", "end", values=values)
        self.browse_row_count_label.configure(text=f"{len(rows)} of {len(self.browse_all_rows)} rows")
        self.browse_visible_rows = rows

    def _export_browse_results(self):
        rows = self.browse_visible_rows
        if not rows:
            messagebox.showinfo("Nothing to export", "No rows to export. Upload a file first.")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialdir=LEADS_DIR,
            initialfile="filtered.xlsx",
        )
        if not filepath:
            return
        try:
            export_generic(rows, self.browse_columns, filepath)
            messagebox.showinfo("Exported", f"Saved {len(rows)} rows to:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))


def _lbl():
    return {"font": ctk.CTkFont(size=12, weight="bold")}


if __name__ == "__main__":
    app = App()
    app.mainloop()
