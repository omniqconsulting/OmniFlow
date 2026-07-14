"""
Shared Jinja2 templates environment.

Every router imports `templates` from here instead of creating its own
Jinja2Templates instance. This guarantees all custom filters (from_json,
tojson, ist, format_tat) are available in every template regardless of
which router renders it.
"""
import json as _json
import os as _os
import urllib.parse as _urlparse
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

from fastapi.templating import Jinja2Templates
from markupsafe import Markup as _Markup

from .services.msg91 import normalize_mobile as _normalize_mobile

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


def _tojson(v, indent=None):
    """Serialize v to a JSON string safe for inline HTML/JS."""
    return _Markup(_json.dumps(v, cls=_OrmEncoder, indent=indent))


def _to_ist(dt, fmt="%d %b, %I:%M %p"):
    """Convert a naive UTC datetime to IST (UTC+5:30) and format it."""
    if dt is None:
        return ""
    IST = _tz(_td(hours=5, minutes=30))
    return dt.replace(tzinfo=_tz.utc).astimezone(IST).strftime(fmt)


_CF_PALETTE = {
    "red":    ("#f87171", "rgba(239,68,68,0.15)"),
    "orange": ("#fb923c", "rgba(249,115,22,0.15)"),
    "yellow": ("#eab308", "rgba(234,179,8,0.15)"),
    "green":  ("#34d399", "rgba(16,185,129,0.15)"),
    "blue":   ("#60a5fa", "rgba(59,130,246,0.15)"),
    "gray":   ("#94a3b8", "rgba(148,163,184,0.15)"),
}


def _cond_format_style(fdef, value):
    """Return an inline CSS style string if any of fdef's conditional_format
    rules match value (checked in order, first match wins); '' otherwise."""
    rules = (fdef or {}).get("conditional_format") or []
    if not rules:
        return ""
    sval = "" if value in (None, "—") else str(value)
    for rule in rules:
        op = rule.get("op")
        rv = rule.get("value", "")
        matched = False
        if op == "empty":
            matched = not sval.strip()
        elif op == "not_empty":
            matched = bool(sval.strip())
        elif op == "contains":
            matched = str(rv).lower() in sval.lower()
        elif op in ("eq", "neq"):
            try:
                is_eq = float(sval) == float(rv)
            except (ValueError, TypeError):
                is_eq = sval.strip().lower() == str(rv).strip().lower()
            matched = is_eq if op == "eq" else not is_eq
        elif op in ("gt", "gte", "lt", "lte"):
            try:
                a, b = float(sval), float(rv)
            except (ValueError, TypeError):
                continue
            if op == "gt":
                matched = a > b
            elif op == "gte":
                matched = a >= b
            elif op == "lt":
                matched = a < b
            elif op == "lte":
                matched = a <= b
        if matched:
            fg, bg = _CF_PALETTE.get(rule.get("color", "gray"), _CF_PALETTE["gray"])
            return f"color:{fg};background:{bg};font-weight:600;border-radius:4px;padding:2px 6px"
    return ""


def _priority_label(p):
    """Display label for a stored priority value — CRITICAL shows as TOP PRIORITY,
    everything else is unchanged. The stored/compared value stays 'CRITICAL'."""
    return "TOP PRIORITY" if p == "CRITICAL" else p


def _tel_link(phone):
    """tel: URI for a stored customer/contact phone number — triggers the
    device's native dialer with the number pre-filled."""
    if not phone:
        return ""
    return "tel:+" + _normalize_mobile(phone)


def _wa_link(phone, text=""):
    """wa.me deep link for a stored phone number, opening the native WhatsApp
    app (mobile) or WhatsApp Web (desktop) with the message pre-filled but
    not sent — the user still taps Send inside WhatsApp itself."""
    if not phone:
        return ""
    url = f"https://wa.me/{_normalize_mobile(phone)}"
    if text:
        url += "?text=" + _urlparse.quote(text)
    return url


def _format_tat(hours):
    """Format TAT hours as '30m', '2h', '1d', '1d 4h' etc. Returns '' for null/zero."""
    if not hours:
        return ""
    if hours < 1:
        return f"{round(hours * 60)}m"
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
templates.env.filters["cond_format"] = _cond_format_style
templates.env.filters["priority_label"] = _priority_label
templates.env.filters["tel_link"] = _tel_link
templates.env.filters["wa_link"] = _wa_link