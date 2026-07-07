"""Shared helpers for the bulk-upload validate/confirm flow used across modules."""


def check_required_headers(fieldnames, required: list, all_expected: list) -> str:
    """Returns an error message if the uploaded file's header row doesn't look like the
    expected template at all (none of the required columns present), else None."""
    normalized = [(f or "").strip() for f in (fieldnames or []) if f]
    missing_required = [c for c in required if c not in normalized]
    if required and len(missing_required) == len(required):
        return (
            f"This file doesn't match the expected template. "
            f"Expected columns such as: {', '.join(all_expected)}. "
            f"Found columns: {', '.join(normalized) if normalized else '(none — file may be empty or use a different delimiter)'}."
        )
    return None
