"""Shared helpers for the bulk-upload validate/confirm flow used across modules."""

from fastapi import HTTPException


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


def run_bulk_upload(raw: bytes, filename: str, *, read_rows_fn, required_headers: list,
                     all_expected_cols: list, validation_fn, tenant_id: str, db) -> dict:
    """Shared upload-step boilerplate for the tickets/checklists/employees bulk-import
    endpoints: parse the CSV, check its header row looks like the expected template,
    tag each row with its 1-based line number, then hand off to the domain-specific
    validation_fn(rows, tenant_id, db) -> {"total", "valid", "errors", "rows"}.
    """
    reader, fieldnames = read_rows_fn(raw, filename)
    fmt_err = check_required_headers(fieldnames, required_headers, all_expected_cols)
    if fmt_err:
        return {"format_error": fmt_err}
    for i, row in enumerate(reader, start=2):
        row["_row"] = i
    return validation_fn(reader, tenant_id, db)


def run_bulk_revalidate(rows_in: list, *, validation_fn, tenant_id: str, db, max_rows: int) -> dict:
    """Shared revalidate-step boilerplate: enforce the row cap, then re-run the same
    domain-specific validation_fn used by run_bulk_upload on the (possibly edited) rows.
    """
    if len(rows_in) > max_rows:
        raise HTTPException(400, f"Too many rows — maximum allowed is {max_rows}.")
    return validation_fn(rows_in, tenant_id, db)
