"""Shared logic for auto-detecting which spreadsheet columns are worth a
dropdown filter (a handful of repeated values) vs free-text-only (mostly
unique per row, like an email or address). Used by both the desktop app's
Browse Excel tab and the Streamlit web app."""

MAX_DYNAMIC_FILTERS = 5
MAX_DROPDOWN_VALUES = 40


def determine_filterable_columns(columns, rows, max_filters=MAX_DYNAMIC_FILTERS, max_dropdown_values=MAX_DROPDOWN_VALUES):
    """Returns up to `max_filters` (column, sorted_unique_values) pairs,
    lowest-cardinality first. A column qualifies if it has more than one
    distinct value, at most `max_dropdown_values` of them, and isn't mostly
    unique per row (more than 60% unique disqualifies it — a filter there
    wouldn't meaningfully narrow anything down)."""
    n = len(rows)
    if n == 0:
        return []
    candidates = []
    for col in columns:
        non_empty = [str(r.get(col, "")).strip() for r in rows if str(r.get(col, "")).strip()]
        unique_vals = sorted(set(non_empty))
        if 1 < len(unique_vals) <= max_dropdown_values and len(unique_vals) <= n * 0.6:
            candidates.append((col, unique_vals))
    candidates.sort(key=lambda c: len(c[1]))
    return candidates[:max_filters]
