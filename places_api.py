"""Wrapper around Google's Places API (New) Text Search endpoint.

Docs: https://developers.google.com/maps/documentation/places/web-service/text-search
Requires a Google Cloud API key with the "Places API (New)" enabled.
"""
import time
import requests

from size_signal import compute_size_signal

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.internationalPhoneNumber,places.nationalPhoneNumber,"
    "places.websiteUri,places.addressComponents,"
    "places.rating,places.userRatingCount,places.businessStatus"
)
PAGE_SIZE = 20
MAX_PAGES = 3  # Google caps Text Search at ~60 results (3 pages of 20)
NEXT_PAGE_DELAY_SECONDS = 2  # Google requires a short delay before a page token becomes valid


class PlacesAPIError(Exception):
    pass


def _extract_component(address_components, wanted_types):
    for comp in address_components or []:
        types = comp.get("types", [])
        if any(t in types for t in wanted_types):
            return comp.get("longText") or comp.get("long_name") or ""
    return ""


def _parse_place(place: dict) -> dict:
    address_components = place.get("addressComponents", [])
    city = _extract_component(address_components, ["locality", "postal_town"])
    if not city:
        city = _extract_component(address_components, ["sublocality", "administrative_area_level_2"])
    country = _extract_component(address_components, ["country"])
    phone = place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber") or ""
    display_name = place.get("displayName", {})
    company = display_name.get("text", "") if isinstance(display_name, dict) else str(display_name)
    website = place.get("websiteUri", "")
    review_count = place.get("userRatingCount", 0) or 0

    size = compute_size_signal(company, review_count, bool(website))

    return {
        "place_id": place.get("id", ""),
        "company": company,
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": phone,
        "address": place.get("formattedAddress", ""),
        "city": city,
        "country": country,
        "website": website,
        "rating": place.get("rating", ""),
        "review_count": review_count,
        "size_label": size["label"],
        "size_score": size["score"],
    }


class GooglePlacesClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise PlacesAPIError("No Google Places API key configured. Add one in Settings.")
        self.api_key = api_key

    def search(self, query: str, max_results: int = 20, progress_callback=None) -> list:
        if not query.strip():
            raise PlacesAPIError("Search query is empty.")

        results = []
        page_token = None
        pages_fetched = 0

        while len(results) < max_results and pages_fetched < MAX_PAGES:
            body = {"textQuery": query, "pageSize": PAGE_SIZE}
            if page_token:
                body["pageToken"] = page_token

            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": FIELD_MASK if not page_token else FIELD_MASK + ",nextPageToken",
            }
            # Always request nextPageToken too
            headers["X-Goog-FieldMask"] = FIELD_MASK + ",nextPageToken"

            try:
                resp = requests.post(SEARCH_URL, json=body, headers=headers, timeout=15)
            except requests.RequestException as e:
                raise PlacesAPIError(f"Network error contacting Google Places API: {e}")

            if resp.status_code != 200:
                try:
                    err = resp.json().get("error", {})
                    message = err.get("message", resp.text)
                except ValueError:
                    message = resp.text
                raise PlacesAPIError(f"Google Places API error ({resp.status_code}): {message}")

            data = resp.json()
            places = data.get("places", [])
            for place in places:
                results.append(_parse_place(place))
                if progress_callback:
                    progress_callback(f"Found {len(results)} businesses so far...")
                if len(results) >= max_results:
                    break

            pages_fetched += 1
            page_token = data.get("nextPageToken")
            if not page_token or len(results) >= max_results:
                break
            time.sleep(NEXT_PAGE_DELAY_SECONDS)

        return results
