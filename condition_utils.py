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
