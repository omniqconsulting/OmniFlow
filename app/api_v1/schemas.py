"""Shared Pydantic schemas for /api/v1. Response models use
model_config = ConfigDict(from_attributes=True) so they serialize
SQLAlchemy ORM objects directly (no manual dict-building per endpoint)."""
from datetime import datetime, timezone
from typing import Annotated, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

T = TypeVar("T")


def _utc_iso(v: datetime) -> str:
    """Every datetime stored by this backend is naive UTC (datetime.utcnow()
    convention, see app/notifications.py's business-hours helpers). Plain
    `datetime` fields serialize a naive value with no offset suffix, which
    JS's `new Date(...)` then parses as the DEVICE's local time instead of
    UTC — correct only by coincidence on an IST-zoned device, wrong
    everywhere else. Stamping an explicit UTC offset here removes the
    ambiguity so every client (native app, any future JS consumer) parses
    it correctly regardless of device timezone."""
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.isoformat()


# Use in place of `datetime`/`Optional[datetime]` on any api_v1 response
# field the client displays as a wall-clock time.
UtcDateTime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


class Page(BaseModel, Generic[T]):
    items: List[T]
    next_cursor: Optional[str] = None


class ErrorResponse(BaseModel):
    detail: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    phone: str
    role: str
    tenant_id: str


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    slug: str
