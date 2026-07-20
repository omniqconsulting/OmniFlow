"""Shared Pydantic schemas for /api/v1. Response models use
model_config = ConfigDict(from_attributes=True) so they serialize
SQLAlchemy ORM objects directly (no manual dict-building per endpoint)."""
from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


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
