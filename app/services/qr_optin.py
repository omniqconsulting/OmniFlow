"""
app/services/qr_optin.py
Builds the per-tenant wa.me opt-in deep link and renders a QR code for it
on request — Section 5 of the Gupshup migration brief.

No file storage: the QR PNG is generated in-memory from the stored link
every time it's requested (5.1 — "does not need to be stored as a file").
"""
import io
import urllib.parse

import qrcode

from .gupshup import normalize_mobile


def build_opt_in_link(source_number: str, entity_label: str = "Employee") -> str:
    """
    Build the wa.me deep link for a tenant's Gupshup WhatsApp number.
    entity_label should come from the tenant's TenantLabelConfig (employee_s)
    rather than being hardcoded, per Section 5.1.
    """
    if not source_number:
        return ""
    number = normalize_mobile(source_number)
    message = f"Hi, I'd like to receive WhatsApp updates from {entity_label} via OmniFlow"
    return f"https://wa.me/{number}?text={urllib.parse.quote(message)}"


def entity_label_for_tenant(db, tenant_id: str) -> str:
    """Pull the tenant's configured singular label for its 'employee' concept,
    falling back to 'Employee' if no label config row exists yet."""
    from ..database import TenantLabelConfig
    cfg = db.query(TenantLabelConfig).filter(TenantLabelConfig.tenant_id == tenant_id).first()
    if cfg and cfg.employee_s:
        return cfg.employee_s
    return "Employee"


def render_qr_png(link: str) -> bytes:
    """Render a QR code encoding `link` as PNG bytes, in-memory only."""
    img = qrcode.make(link)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
