"""Cursor (keyset) pagination shared by every /api/v1 list endpoint.

Offset pagination (LIMIT/OFFSET) skews under concurrent writes — a row
inserted ahead of the current page shifts every subsequent page by one,
so a client can miss or duplicate rows while paging through a live list
(Tickets, FMS). Keyset pagination instead orders by (created_at, id) and
asks "give me rows before this exact position", which is stable regardless
of what else gets inserted concurrently.
"""
import base64
import binascii
from datetime import datetime
from typing import Optional, Tuple

from fastapi import HTTPException


def encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> Tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_str, row_id = raw.rsplit("|", 1)
        return datetime.fromisoformat(ts_str), row_id
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=400, detail="Invalid cursor")


def paginate_cursor(query, model, cursor: Optional[str], limit: int, id_col="id", created_col="created_at"):
    """Applies keyset filtering + ordering to `query` and returns
    (rows, next_cursor). Orders newest-first (created_at DESC, id DESC).
    `limit` is clamped to [1, 100] by the caller's Pydantic query param."""
    order_created = getattr(model, created_col)
    order_id = getattr(model, id_col)

    if cursor:
        cur_created, cur_id = decode_cursor(cursor)
        query = query.filter(
            (order_created < cur_created)
            | ((order_created == cur_created) & (order_id < cur_id))
        )

    query = query.order_by(order_created.desc(), order_id.desc())
    rows = query.limit(limit + 1).all()

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(getattr(last, created_col), getattr(last, id_col))

    return rows, next_cursor
