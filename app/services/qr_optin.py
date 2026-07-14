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


def build_opt_in_link(source_number: str, tenant_name: str) -> str:
    """
    Build the wa.me deep link for a tenant's Gupshup WhatsApp number.
    tenant_name should be that tenant's actual display name (tenant.name),
    since this same function generates the QR/link for every tenant.
    """
    if not source_number:
        return ""
    number = normalize_mobile(source_number)
    message = (
        f"Hi, I'd like to receive WhatsApp updates from {tenant_name} for delegations, "
        "checklist and other updates. Consider this message as a consent to opt-in "
        "for receiving the regular updates."
    )
    return f"https://wa.me/{number}?text={urllib.parse.quote(message)}"


def render_qr_png(link: str) -> bytes:
    """Render a QR code encoding `link` as PNG bytes, in-memory only."""
    img = qrcode.make(link)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
