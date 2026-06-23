"""
Shared Jinja2 templates environment.

Every router imports `templates` from here instead of creating its own
Jinja2Templates instance. This guarantees all custom filters (from_json,
tojson, ist, format_tat) are available in every template regardless of
which router renders it.
"""
import json as _json
import os as _os
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

from fastapi.templating import Jinja2Templates
from markupsafe import Markup as _Markup

_BASE_DIR = _os.path.dirname(__file__)
templates = Jinja2Templates(directory=_os.path.join(_BASE_DIR, "templates"))


# ── Shared encoder (handles ORM objects and datetimes) ───────────────────────

class _OrmEncoder(_json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, _dt):
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        return super().default(obj)


# ── Filter definitions ────────────────────────────────────────────────────────

def _from_json(s):
    """Parse a JSON string to a Python object; returns [] on null/empty."""
    try:
        return _json.loads(s) if s else []
    except Exception:
        return []


def _tojson(v):
    """Serialize v to a JSON string safe for inline HTML/JS."""
    return _Markup(_json.dumps(v, cls=_OrmEncoder))


def _to_ist(dt, fmt="%d %b, %I:%M %p"):
    """Convert a naive UTC datetime to IST (UTC+5:30) and format it."""
    if dt is None:
        return ""
    IST = _tz(_td(hours=5, minutes=30))
    return dt.replace(tzinfo=_tz.utc).astimezone(IST).strftime(fmt)


def _format_tat(hours):
    """Format TAT hours as '2h', '1d', '1d 4h' etc. Returns '' for null/zero."""
    if not hours:
        return ""
    h = int(hours)
    if h < 24:
        return f"{h}h"
    d, r = divmod(h, 24)
    return f"{d}d" if r == 0 else f"{d}d {r}h"


# ── Register all filters on the shared instance ───────────────────────────────

templates.env.filters["from_json"]  = _from_json
templates.env.filters["tojson"]     = _tojson
templates.env.filters["ist"]        = _to_ist
templates.env.filters["format_tat"] = _format_tat