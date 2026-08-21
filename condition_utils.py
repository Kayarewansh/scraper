"""Shared condition-matching logic for the Browse Excel condition builder,
used by both the desktop app and the Streamlit web app."""

CONDITION_OPERATORS = [
    "Equals",
    "Not equals",
    "Contains",
    "Does not contain",
    "Greater than",
    "Less than",
]

NUMERIC_THRESHOLD = 0.8  # fraction of non-empty values that must parse as a number


def is_numeric_column(rows: list, column: str) -> bool:
    """True if most of a column's non-empty values look like numbers — used
    to decide whether the Value field should be a free-typed number input
    (so you can filter on a threshold that isn't already in the data, e.g.
    "Revenue > 250000") or a dropdown of existing values. A few stray
    non-numeric entries (blanks, "N/A") don't flip the column to text."""
    non_empty = [str(r.get(column, "")).strip() for r in rows if str(r.get(column, "")).strip()]
    if not non_empty:
        return False
    numeric_count = 0
    for v in non_empty:
        try:
            float(v)
            numeric_count += 1
        except ValueError:
            pass
    return (numeric_count / len(non_empty)) >= NUMERIC_THRESHOLD


def row_matches(row: dict, column: str, operator: str, value) -> bool:
    """True if `row[column]` satisfies `operator` against `value`. Numeric
    operators (Greater/Less than) return False rather than raising when
    either side isn't a number — a non-numeric column just won't match
    those, instead of crashing the filter."""
    cell = str(row.get(column, "")).strip()
    value = "" if value is None else str(value)

    if operator == "Equals":
        return cell == value
    if operator == "Not equals":
        return cell != value
    if operator == "Contains":
        return value.lower() in cell.lower()
    if operator == "Does not contain":
        return value.lower() not in cell.lower()
    if operator in ("Greater than", "Less than"):
        try:
            cell_num, value_num = float(cell), float(value)
        except ValueError:
            return False
        return cell_num > value_num if operator == "Greater than" else cell_num < value_num
    return True


def filter_rows(rows: list, conditions: list, combine: str = "AND") -> list:
    """Filters `rows` by a list of {"column", "operator", "value"} dicts,
    combined with AND (every condition must match) or OR (any condition
    matches). Conditions with no column or no value set are ignored,
    matching the previous behavior of skipping incomplete conditions."""
    active = [c for c in conditions if c.get("column") and c.get("value") not in (None, "")]
    if not active:
        return rows
    if combine == "OR":
        return [r for r in rows if any(row_matches(r, c["column"], c["operator"], c["value"]) for c in active)]
    return [r for r in rows if all(row_matches(r, c["column"], c["operator"], c["value"]) for c in active)]
