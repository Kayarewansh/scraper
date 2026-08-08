"""Heuristic "is this probably a bigger business" proxy.

Google Places has no revenue field, and no free/legitimate public source
reliably publishes a private local business's revenue. This scores each
result from signals that actually correlate with size — review volume
(when available), whether it has its own website, and legal-entity naming
(Pvt Ltd, LLC, LLP, etc.) — and labels it plainly as a signal, not a real
financial figure, so it's never mistaken for verified revenue data.
"""
import re

LEGAL_ENTITY_RE = re.compile(
    r"\b(private limited|pvt\.?\s*ltd\.?|llc|llp|ltd\.?|inc\.?|incorporated|corp\.?|"
    r"corporation|enterprises|industries|group|holdings|international|"
    r"& co\.?|and co\.?)\b",
    re.IGNORECASE,
)


def compute_size_signal(company_name: str, review_count: int, has_website: bool, reviews_available: bool = True) -> dict:
    """Returns {"score": int, "label": str}. Higher score = more signals
    consistent with an established/larger business. This is a rough proxy
    built from public listing data, not a revenue figure.

    `reviews_available` must be False for data sources (like OpenStreetMap)
    that have no review/popularity field at all — as opposed to a business
    that genuinely has zero reviews. Without it, the max achievable score
    would be capped at 2 (website + naming) and could never reach the
    "Likely larger business" tier, which was calibrated assuming review
    counts contribute up to +3. Tier thresholds are rescaled accordingly.
    """
    score = 0

    if reviews_available:
        if review_count >= 200:
            score += 3
        elif review_count >= 50:
            score += 2
        elif review_count >= 10:
            score += 1

    if has_website:
        score += 1

    if company_name and LEGAL_ENTITY_RE.search(company_name):
        score += 1

    if reviews_available:
        large_threshold, mid_threshold = 4, 2
    else:
        large_threshold, mid_threshold = 2, 1  # max possible score is 2 without review data

    if score >= large_threshold:
        label = "Likely larger business"
    elif score >= mid_threshold:
        label = "Mid-size / unclear"
    else:
        label = "Small / unclear"

    return {"score": score, "label": label}
