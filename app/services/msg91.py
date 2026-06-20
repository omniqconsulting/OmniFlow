"""
app/services/msg91.py
Single integration point for all outbound MSG91 WhatsApp calls.
No other file should call the MSG91 API directly.
Foundation module — built once, reused by every WhatsApp pipeline.
"""
import httpx
import logging
from app.constants import WHATSAPP_TEMPLATES, MSG91_AUTH_KEY, MSG91_WA_NUMBER

logger = logging.getLogger("msg91")

MSG91_BASE = "https://api.msg91.com/api/v5"


def normalize_mobile(phone: str, country_code: str = "91") -> str:
    """
    Ensure phone is in MSG91 international format: digits only, no '+',
    with country code. Length is used to decide whether a country code
    is already present, not a startswith check — a 10-digit mobile can
    legitimately begin with the same digits as the country code itself
    (e.g. 9123456789), which a startswith match would misread as already
    prefixed.
    """
    digits = "".join(c for c in phone if c.isdigit())
    digits = digits.lstrip("0")  # strip legacy trunk-prefix zero, if stored that way
    if len(digits) == 10:
        digits = country_code + digits
    return digits

def send_whatsapp_template(mobile: str, template_name: str, variables: list):
    """
    Send a single WhatsApp message using a pre-approved Meta template.

    mobile: international format, e.g. '919876543210' — no '+', no spaces
    template_name: key into WHATSAPP_TEMPLATES (app/constants.py)
    variables: ordered list matching the template's approved variable order exactly

    Returns (success: bool, error_message: str | None).
    NEVER raises — every failure path returns (False, reason).
    """
    mobile = normalize_mobile(mobile)
    template = WHATSAPP_TEMPLATES.get(template_name)
    if not template:
        return False, f"Unknown template: {template_name}"

    if len(variables) != len(template["variable_order"]):
        return False, (
            f"Variable count mismatch for {template_name}: "
            f"expected {len(template['variable_order'])}, got {len(variables)}"
        )

    components = {
        f"body_{i + 1}": {"type": "text", "value": str(v)}
        for i, v in enumerate(variables)
    }

    payload = {
        "integrated_number": MSG91_WA_NUMBER,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "namespace": template.get("namespace", ""),
                "language": {"code": "en", "policy": "deterministic"},
                "to_and_components": [
                    {
                        "to": [mobile],
                        "components": components,
                    }
                ],
            },
        },
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{MSG91_BASE}/whatsapp/whatsapp-outbound-message/bulk/",
                headers={"authkey": MSG91_AUTH_KEY, "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code == 200:
            return True, None
        return False, f"MSG91 returned {resp.status_code}: {resp.text[:300]}"
    except httpx.TimeoutException:
        return False, "MSG91 request timed out"
    except Exception as exc:
        logger.exception("MSG91 send failed for template=%s", template_name)
        return False, str(exc)


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{  {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')  }"


def format_wa_date(dt) -> str:
    """
    Matches the exact format approved in the omniflow_ticket_assigned Meta template.
    Example: '24th Oct 2026'. Use this for all date template variables so every
    template stays consistent with what Meta approved.
    """
    return f"{_ordinal(dt.day)} {dt.strftime('%b %Y')}"
