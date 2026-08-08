"""Headless batch lead scraper: runs a sweep of (industry, city) searches
against OpenStreetMap (free, no API key), enriches results with a website
email where findable, filters to a size-signal tier, and writes one Excel
file to leads/.

Examples:
  python3 batch_scrape.py
  python3 batch_scrape.py --industries "marketing agency" "software company" --cities "Dubai, UAE"
  python3 batch_scrape.py --size-filter "" --no-require-website
"""
import argparse
import concurrent.futures
import datetime
import os
import time

from overpass_api import OverpassClient, OverpassError
from enrichment import find_contact_info
from excel_export import export_to_excel

LEADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads")

DEFAULT_INDUSTRIES = [
    "trading company",
    "real estate company",
    "construction company",
    "manufacturing company",
    "logistics company",
    "consulting company",
]
DEFAULT_CITIES = ["Dubai, UAE", "Abu Dhabi, UAE", "Sharjah, UAE"]

ENRICH_WORKERS = 6
DELAY_BETWEEN_SEARCHES_SECONDS = 2  # be polite to the free Nominatim/Overpass services


def run_sweep(industries, cities, max_per_query, require_website, size_filter, enrich_emails):
    client = OverpassClient()
    seen_place_ids = set()
    results = []

    queries = [(industry, city) for industry in industries for city in cities]
    print(f"Running {len(queries)} searches ({len(industries)} industries x {len(cities)} cities)...")

    for i, (industry, city) in enumerate(queries, start=1):
        print(f"[{i}/{len(queries)}] {industry!r} in {city!r}")
        try:
            places = client.search(industry, city, max_per_query)
        except OverpassError as e:
            print(f"  ! search failed: {e}")
            continue
        new_count = 0
        for place in places:
            if place["place_id"] in seen_place_ids:
                continue
            seen_place_ids.add(place["place_id"])
            results.append(place)
            new_count += 1
        print(f"  -> {len(places)} results ({new_count} new)")
        if i < len(queries):
            time.sleep(DELAY_BETWEEN_SEARCHES_SECONDS)

    print(f"Total unique businesses found: {len(results)}")

    if require_website:
        results = [r for r in results if r["website"]]
        print(f"After requiring a website: {len(results)}")

    if size_filter:
        results = [r for r in results if r["size_label"] == size_filter]
        print(f"After size filter ({size_filter!r}): {len(results)}")

    if enrich_emails:
        candidates = [r for r in results if r["website"]]
        print(f"Looking for emails on {len(candidates)} websites...")
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
                if done % 10 == 0 or done == len(candidates):
                    print(f"  ...{done}/{len(candidates)}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Batch lead scrape via OpenStreetMap (free, no API key).")
    parser.add_argument("--industries", nargs="*", default=DEFAULT_INDUSTRIES)
    parser.add_argument("--cities", nargs="*", default=DEFAULT_CITIES)
    parser.add_argument("--max-per-query", type=int, default=20)
    parser.add_argument("--require-website", action="store_true", default=True)
    parser.add_argument("--no-require-website", dest="require_website", action="store_false")
    parser.add_argument(
        "--size-filter",
        default="Likely larger business",
        choices=["", "Likely larger business", "Mid-size / unclear", "Small / unclear"],
        help="Filter to a Size Signal tier. Empty string = no filter.",
    )
    parser.add_argument("--no-emails", dest="enrich_emails", action="store_false", default=True)
    parser.add_argument("--out", default=None, help="Output .xlsx path. Defaults to leads/<timestamp>.xlsx")
    args = parser.parse_args()

    results = run_sweep(
        args.industries,
        args.cities,
        args.max_per_query,
        args.require_website,
        args.size_filter,
        args.enrich_emails,
    )

    os.makedirs(LEADS_DIR, exist_ok=True)
    if args.out:
        filepath = args.out
    else:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        filepath = os.path.join(LEADS_DIR, f"uae_leads_{stamp}.xlsx")

    export_to_excel(results, filepath)
    print(f"Saved {len(results)} rows to {filepath}")


if __name__ == "__main__":
    main()
