# Lead Scraper

A desktop tool that searches for businesses matching a keyword and location,
pulls their company name / address / phone / website, tries to find a public
contact email on each business's own website, and exports the results to
Excel with filters on the results table.

**Data source: OpenStreetMap — free, no signup, no API key, no billing.**
Geocoding via Nominatim + business search via the Overpass API, both free
public OSM services. The trade-off vs Google Places: OSM is crowd-sourced,
so coverage is patchier — many businesses are missing entirely, or listed
without a phone/website. There's also no review/popularity data, so lead
quality signals are weaker (see "Size Signal" below). A Google Places-based
client (`places_api.py`) is still in the repo if you get a Google Cloud API
key later and want denser, more reliable results — ask to have it wired
back in.

There are two ways to run it — a native desktop window, or a browser-based
version. Both share the same backend code and behave identically; pick
whichever you prefer.

## 1. Install

```bash
cd ~/Desktop/scrapper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

No account, key, or config step needed.

## 2a. Run as a desktop app

```bash
cd ~/Desktop/scrapper
source venv/bin/activate
python3 app.py
```

Opens a native window on your Mac.

### Building a standalone .app (no Terminal needed)

To get a double-clickable **Lead Scraper.app** you can launch from
Launchpad/Spotlight like any other Mac app, instead of running `python3
app.py` from Terminal each time:

```bash
cd ~/Desktop/scrapper
source venv/bin/activate
pip install pyinstaller
pyinstaller --windowed --name "Lead Scraper" --noconfirm app.py
cp -R "dist/Lead Scraper.app" /Applications/
```

The built app is self-contained (bundles Python + all dependencies, ~200MB)
and doesn't need the venv or Terminal to run afterwards. Rebuild it with the
same command any time `app.py` or its imports change — `dist/` and `build/`
are gitignored since they're regenerated output, not source.

## 2b. Run as a browser app (a link you can open)

```bash
cd ~/Desktop/scrapper
source venv/bin/activate
streamlit run streamlit_app.py
```

Starts a local web server and opens your browser to **http://localhost:8501**
automatically. It only runs on your machine (not reachable from outside your
network) — closing the terminal stops it. Press Ctrl+C in that terminal to
stop it manually.

Both versions have the same two sections: **Search** (run a live OpenStreetMap
search) and **Browse Excel** (upload any spreadsheet and filter it by column).

## 2. Search tab

- **Business type / keyword** + **Location** — both required, e.g. "dentist"
  + "Dubai, UAE". Location is geocoded first to find the search area.
- **Max results** — caps how many results are kept per search.
- Every matching result is kept regardless of whether it has a website or
  phone number — those fields are just left blank when OSM doesn't have
  them, rather than dropping the row. Use the filter bar after the search
  (below) if you want to narrow down to only rows that have one.
- **Find emails from websites** — after the search, the app visits each
  result's own website (homepage + likely contact/about pages) looking for a
  published email address. OSM occasionally has an email tagged directly on
  the listing too, in which case that's used without needing to visit the
  site. Either way, this is best-effort: many business sites don't publish
  one, in which case Email (and First/Last Name) are left blank rather than
  guessed.
- Once results load, use the filter bar (free-text search, city dropdown,
  country dropdown, size-signal dropdown) to narrow down what you see, then
  click **Export visible rows to Excel** — the save dialog opens in your
  chosen **Save folder** by default (see below).

### Save folder

Both the desktop app and the browser app let you pick where exports go,
instead of a fixed location:

- **Desktop app** — the sidebar shows the current **Save folder** with a
  **Change save folder...** button; pick any folder and it's remembered
  for next time.
- **Browser app** — a **Save folder** box in the left sidebar; type or
  paste a path and it's remembered the same way (only meaningful when
  running locally — see the caption next to "Save to folder" for the
  hosted-link caveat).

The choice is saved to `~/Library/Application Support/Lead
Scraper/config.json` and persists across restarts. It defaults to `~/Documents/Lead
Scraper Leads` the first time you run it.

## 3. Browse Excel tab

Click **Upload Excel/TSV...** and pick any `.xlsx` or `.tsv` file — leads you
exported earlier, or any other spreadsheet. The app reads its header row and builds
the results table with whatever columns that file has (not fixed to the
leads schema).

Filter it with:

- **Conditions** — click **+ Add condition** to get **Column**, **Condition**
  (Equals, Not equals, Contains, Does not contain, Greater than, Less than),
  and **Value**. The Value field adapts to the column's data: a text-column
  (like City or Status) gets a dropdown of that column's actual values, so
  you can't pick a combination that doesn't exist; a numeric-looking column
  (like Revenue) gets a free-typed number box instead, so you can filter on
  a threshold that isn't already in the data (e.g. "Revenue > 250000" even
  if no row is exactly 250000). Greater/Less than just won't match on a
  non-numeric column rather than erroring. Stack as many conditions as you
  like — they combine with AND. Each has a **✕** to remove just that one.
- **Free-text search** — matches across every column at once, on top of
  whatever conditions are set.

Combine conditions and free-text search (all applied together), then
**Export visible rows to Excel** to save just the filtered subset — handy
for slicing a big leads file down before importing it into a CRM or mail
tool.

## 4. Batch sweeps from the command line

For running many (industry × city) searches in one go instead of clicking
through the GUI repeatedly:

```bash
source venv/bin/activate
python3 batch_scrape.py
python3 batch_scrape.py --industries "marketing agency" "software company" --cities "Dubai, UAE"
python3 batch_scrape.py --size-filter "" --no-require-website
```

Defaults to 6 common industries × Dubai/Abu Dhabi/Sharjah, deduped across
searches, filtered to businesses with a website and a "Likely larger
business" size signal, enriched with emails, and saved to
`leads/uae_leads_<timestamp>.xlsx`. Run `python3 batch_scrape.py --help` for
all options.

The script adds a short delay between searches — Nominatim and Overpass are
free public services with fair-use limits, so don't remove it or fire many
instances in parallel.

## What gets exported

Company Name, First Name, Last Name, Email, Contact Number, Full Address,
City, Country, Website, Google Reviews (blank for OSM — see below), Size
Signal.

### About "Size Signal" — there is no revenue filter

No free/legitimate public source publishes a private local business's
revenue. Filtering results directly by a revenue figure (e.g. "over ₹5 Cr")
isn't possible with this data source — any tool claiming to do that from
public listings is either using a paid firmographic database (see below) or
making it up.

Instead, each result gets a **Size Signal** — a transparent heuristic
(`size_signal.py`) scored from:

- review count, when the data source has one (Google Places does; OSM doesn't)
- whether the business has its own website
- whether the listed name uses legal-entity naming (Pvt Ltd, LLC, LLP,
  Enterprises, Industries, Group, Inc, Corp, etc.)

Results are labeled **"Likely larger business"**, **"Mid-size / unclear"**,
or **"Small / unclear"**. With OSM (no review data), this only has two real
signals to work with, so it's a coarser proxy than it would be with Google
Places — treat it as a starting point for manual qualification, not ground
truth.

If you need actual revenue/firmographic data, that requires a paid B2B data
provider (e.g. ZoomInfo, Apollo.io, Crunchbase, Dun & Bradstreet) with its
own API and licensing — happy to wire one of those in if you have an account.

**Note on names:** neither OSM nor Google Places return personal contact
names — only business listing data. First/Last Name are filled in only when
an email address clearly encodes a two-part name (e.g. `john.smith@…`);
role inboxes like `info@` or single-word addresses are left blank rather
than fabricated.

## Responsible use

This tool only pulls data that's already public (OpenStreetMap's listings +
a business's own public website). If you use the exported emails for
outreach, follow the anti-spam laws that apply to you and your recipients —
e.g. include a clear sender identity and an easy opt-out, and check
requirements like CAN-SPAM (US), GDPR (EU/UK), or the UAE's relevant data
protection rules depending on where your contacts are located.

## Files

- `app.py` — desktop UI (Search tab + Browse Excel tab), customtkinter + tkinter.
- `streamlit_app.py` — browser UI, same two sections, same backend — run with
  `streamlit run streamlit_app.py`.
- `condition_utils.py` — the Browse Excel condition operators (Equals,
  Contains, Greater than, etc.), shared by both UIs.
- `batch_scrape.py` — headless CLI for running a sweep of searches at once.
- `overpass_api.py` — free OSM-based search client (Nominatim + Overpass).
- `places_api.py` / `config.py` — optional Google Places (New) client, unused
  by default; ask to have it wired back in if you get a Google API key later.
- `size_signal.py` — the "is this probably a bigger business" heuristic.
- `enrichment.py` — best-effort website email/name extraction.
- `excel_export.py` — reads/writes `.xlsx`: the fixed leads schema and a
  generic reader/writer for any uploaded file, both as files and in-memory
  bytes (for the browser version's download button).
- `leads/` — default export location for `.xlsx` output.
