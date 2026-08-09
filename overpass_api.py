"""Free, no-key business search using OpenStreetMap data (Nominatim for
geocoding a location to a bounding box, Overpass API for the actual POI
search within it).

No signup, no API key, no billing — but coverage is a real trade-off vs
Google Places: OSM is crowd-sourced, so many businesses are missing
entirely, or present without a phone/website. There's also no review-count
/ popularity data, so the "size signal" heuristic falls back to just
website presence + legal-entity naming (see size_signal.py).

Be a good citizen of these free public services: this client makes one
geocode call per search, plus one Overpass call per grid cell (see
_split_bbox — most searches are a single cell), and does not parallelize
across mirrors. Don't remove the delays between calls.
"""
import math
import re
import time
import requests

from size_signal import compute_size_signal

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
USER_AGENT = "LeadScraperTool/1.0 (free personal lead-gen tool)"
# OSM tag keys that mark a place as a business. "amenity" covers a lot of
# common searches (dentist, clinic, restaurant, pharmacy, bank...) that
# aren't shops/offices/crafts.
CATEGORY_KEYS = ["shop", "office", "craft", "amenity"]
OVERPASS_QUERY_TIMEOUT_SECONDS = 55  # kept under mirrors' own gateway timeouts (seen: 504 at ~60s)
OVERPASS_HTTP_TIMEOUT_SECONDS = 75  # must exceed the internal budget above

# Large areas (e.g. a whole country) make for a genuinely heavy query — split
# into a grid of smaller regions so each request stays fast enough to avoid
# gateway timeouts. A city-sized search (e.g. "Dubai, UAE") stays one query.
GRID_SPLIT_THRESHOLD_DEGREES = 2.0
MAX_GRID_CELLS_PER_SIDE = 3
DELAY_BETWEEN_GRID_QUERIES_SECONDS = 2


class OverpassError(Exception):
    pass


def _geocode(location: str) -> dict:
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": location, "format": "json", "addressdetails": 1, "limit": 1},
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=15,
        )
    except requests.RequestException as e:
        raise OverpassError(f"Network error contacting Nominatim: {e}")

    if resp.status_code != 200:
        raise OverpassError(f"Nominatim error ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    if not data:
        raise OverpassError(f"Could not find location: {location!r}")

    item = data[0]
    south, north, west, east = (float(v) for v in item["boundingbox"])
    address = item.get("address", {})
    country = address.get("country", "")
    city = (
        address.get("city")
        or address.get("town")
        or address.get("municipality")
        or location.split(",")[0].strip()
    )
    return {"bbox": (south, west, north, east), "country": country, "city": city}


def _split_bbox(bbox: tuple) -> list:
    """Splits a large bounding box into a grid of smaller ones so each
    Overpass query stays fast enough to avoid gateway timeouts on big areas
    (e.g. a whole country). Small areas (e.g. a city) come back as a
    single-item list — unchanged, one-query behavior."""
    south, west, north, east = bbox
    lat_span = north - south
    lon_span = east - west

    rows = min(MAX_GRID_CELLS_PER_SIDE, max(1, math.ceil(lat_span / GRID_SPLIT_THRESHOLD_DEGREES)))
    cols = min(MAX_GRID_CELLS_PER_SIDE, max(1, math.ceil(lon_span / GRID_SPLIT_THRESHOLD_DEGREES)))

    if rows == 1 and cols == 1:
        return [bbox]

    lat_step = lat_span / rows
    lon_step = lon_span / cols
    cells = []
    for r in range(rows):
        for c in range(cols):
            cell_south = south + r * lat_step
            cell_north = south + (r + 1) * lat_step
            cell_west = west + c * lon_step
            cell_east = west + (c + 1) * lon_step
            cells.append((cell_south, cell_west, cell_north, cell_east))
    return cells


def _escape_overpass_regex(text: str) -> str:
    """Escapes regex metacharacters for Overpass's regex engine. Deliberately
    does NOT use Python's re.escape() — as of Python 3.7+ it also
    backslash-escapes spaces (e.g. "a b" -> "a\\ b"), which Overpass's regex
    engine hangs on until the query times out rather than erroring."""
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", text)


def _build_query(keyword: str, bbox: tuple) -> str:
    south, west, north, east = bbox
    escaped = _escape_overpass_regex(keyword.strip())
    clauses = []
    for key in CATEGORY_KEYS:
        clauses.append(f'node["name"~"{escaped}",i]["{key}"]({south},{west},{north},{east});')
        clauses.append(f'way["name"~"{escaped}",i]["{key}"]({south},{west},{north},{east});')
    body = "\n  ".join(clauses)
    return f"[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_SECONDS}];\n(\n  {body}\n);\nout center tags;"


def _run_overpass(query: str) -> dict:
    """Tries each mirror in turn. A mirror that responds 200 with valid JSON
    but an empty `remark`-flagged result (Overpass's own internal query
    timeout, or another server-side runtime error) is treated as a failure
    so the next mirror gets a chance, rather than silently returning zero
    results to the caller."""
    last_error = None
    for mirror in OVERPASS_MIRRORS:
        try:
            resp = requests.post(
                mirror,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=OVERPASS_HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            last_error = f"{mirror}: {e}"
            continue

        if resp.status_code != 200:
            last_error = f"{mirror}: HTTP {resp.status_code}"
            continue

        try:
            data = resp.json()
        except ValueError:
            last_error = f"{mirror}: response was not valid JSON"
            continue

        if data.get("remark") and not data.get("elements"):
            last_error = f"{mirror}: {data['remark']}"
            continue

        return data

    raise OverpassError(f"All Overpass mirrors failed. Last error: {last_error}")


def _parse_element(el: dict, fallback_city: str, fallback_country: str) -> dict:
    tags = el.get("tags", {})
    name = tags.get("name", "")
    if not name:
        return None

    phone = tags.get("phone") or tags.get("contact:phone") or ""
    website = tags.get("website") or tags.get("contact:website") or ""
    email = tags.get("email") or tags.get("contact:email") or ""

    address = tags.get("addr:full", "")
    if not address:
        parts = [tags.get("addr:housenumber", ""), tags.get("addr:street", "")]
        address = " ".join(p for p in parts if p)

    city = tags.get("addr:city") or fallback_city
    country = fallback_country

    size = compute_size_signal(name, 0, bool(website), reviews_available=False)

    return {
        "place_id": f"osm_{el.get('type')}_{el.get('id')}",
        "company": name,
        "first_name": "",
        "last_name": "",
        "email": email,
        "phone": phone,
        "address": address,
        "city": city,
        "country": country,
        "website": website,
        "rating": "",
        "review_count": 0,
        "size_label": size["label"],
        "size_score": size["score"],
    }


class OverpassClient:
    """Drop-in equivalent of GooglePlacesClient, but free and keyless."""

    def search(self, keyword: str, location: str, max_results: int = 20, progress_callback=None) -> list:
        if not keyword.strip():
            raise OverpassError("Search keyword is empty.")
        if not location.strip():
            raise OverpassError("Location is required (e.g. 'Dubai, UAE') to search OpenStreetMap.")

        if progress_callback:
            progress_callback(f"Locating '{location}'...")
        geo = _geocode(location)
        time.sleep(1)  # be polite to Nominatim before hitting Overpass

        cells = _split_bbox(geo["bbox"])
        if len(cells) > 1 and progress_callback:
            progress_callback(
                f"'{location}' is a large area — splitting into {len(cells)} sub-regions to avoid timeouts..."
            )

        results = []
        seen_ids = set()
        cell_errors = []

        for idx, cell in enumerate(cells):
            if len(results) >= max_results:
                break

            if progress_callback:
                region_note = f" (sub-region {idx + 1}/{len(cells)})" if len(cells) > 1 else ""
                progress_callback(f"Searching OpenStreetMap for '{keyword}' in {location}{region_note}...")

            query = _build_query(keyword, cell)
            try:
                data = _run_overpass(query)
            except OverpassError as e:
                cell_errors.append(str(e))
                if progress_callback:
                    progress_callback(f"Sub-region {idx + 1}/{len(cells)} failed, continuing: {e}")
                continue

            if data.get("remark") and progress_callback:
                progress_callback(f"Warning: results may be incomplete ({data['remark']})")

            for el in data.get("elements", []):
                row = _parse_element(el, geo["city"], geo["country"])
                if not row or row["place_id"] in seen_ids:
                    continue
                seen_ids.add(row["place_id"])
                results.append(row)
                if progress_callback:
                    progress_callback(f"Found {len(results)} businesses so far...")
                if len(results) >= max_results:
                    break

            if idx < len(cells) - 1 and len(results) < max_results:
                time.sleep(DELAY_BETWEEN_GRID_QUERIES_SECONDS)

        if not results and cell_errors:
            if len(cells) == 1:
                raise OverpassError(cell_errors[-1])
            raise OverpassError(f"All {len(cells)} sub-region queries failed. Last error: {cell_errors[-1]}")

        return results
