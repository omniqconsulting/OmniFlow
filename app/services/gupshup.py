"""
app/services/gupshup.py
Single integration point for all outbound Gupshup WhatsApp calls, per-tenant.
No other file should call the Gupshup API directly — mirrors the existing
app/services/msg91.py convention for the platform this replaces.

API confirmed against the live Apolo Industry Gupshup account (brief v1.1,
Decision #12): Gateway/Enterprise API at
https://mediaapi.smsgupshup.com/GatewayAPI/rest, authenticated with a
per-tenant Client ID + Secret Token. whatsAppTemplateId in send requests is
the Facebook template ID (numeric), not the Gupshup template ID (UUID) —
Decision #13.
"""
import httpx
import logging
from app.constants import WHATSAPP_TEMPLATES, GUPSHUP_API_BASE

logger = logging.getLogger("gupshup")


def normalize_mobile(phone: str, country_code: str = "91") -> str:
    """
    Locked E.164-style normalization (no '+', no spaces/dashes) — Section 6.1
    of the brief requires this exact format to be used consistently for both
    stored employee numbers and inbound webhook sender numbers, to avoid
    silent opt-in match failures from formatting drift.
    """
    digits = "".join(c for c in phone if c.isdigit())
    digits = digits.lstrip("0")
    if len(digits) == 10:
        digits = country_code + digits
    return digits


def to_e164(phone: str, country_code: str = "91") -> str:
    """Same normalization as normalize_mobile, with a leading '+' — used for
    display fields (gupshup_source_number, matched_phone) per Section 4."""
    return "+" + normalize_mobile(phone, country_code)


def send_whatsapp_template(tenant, mobile: str, template_name: str, variables: list):
    """
    Send a single WhatsApp message using a tenant's own Gupshup WABA.

    tenant: Tenant ORM instance — must have gupshup_client_id, gupshup_secret_token,
            gupshup_source_number populated (caller is responsible for checking
            gupshup_waba_status != SUSPENDED before calling, per Decision #12).
    mobile: any format — normalized internally.
    template_name: key into WHATSAPP_TEMPLATES (app/constants.py).
    variables: ordered list matching the template's approved variable order exactly.

    Returns (success: bool, error_message: str | None, template_id: str | None,
             template_category: str | None, gupshup_message_id: str | None) — the
    message id lets webhook status events (Section 6.3) be matched back to
    this send's WhatsAppMessageLog row via raw_status_webhook_payloads.
    NEVER raises — every failure path returns success=False with a reason.
    """
    template = WHATSAPP_TEMPLATES.get(template_name)
    if not template:
        return False, f"Unknown template: {template_name}", None, None, None

    if not (tenant and tenant.gupshup_client_id and tenant.gupshup_secret_token and tenant.gupshup_source_number):
        return False, "Tenant has no Gupshup WhatsApp configuration", None, None, None

    if tenant.gupshup_waba_status == "SUSPENDED":
        return False, "Tenant's Gupshup WABA is SUSPENDED — send blocked", None, None, None

    if len(variables) != len(template["variable_order"]):
        return False, (
            f"Variable count mismatch for {template_name}: "
            f"expected {len(template['variable_order'])}, got {len(variables)}"
        ), None, None, None

    template_id = template.get("gupshup_template_id")
    template_category = template.get("gupshup_template_category", "UTILITY")
    mobile_norm = normalize_mobile(mobile)

    payload = {
        "channel": "whatsapp",
        "source": normalize_mobile(tenant.gupshup_source_number),
        "destination": mobile_norm,
        "message": {
            "isHSM": "true",
            "type": "template",
            "template": {
                "id": template_id,
                "params": [str(v) for v in variables],
            },
        },
        "whatsAppTemplateId": template_id,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"{GUPSHUP_API_BASE}",
                params={"format": "json"},
                headers={
                    "apikey": tenant.gupshup_secret_token,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code == 200:
            gupshup_message_id = None
            try:
                gupshup_message_id = resp.json().get("messageId") or resp.json().get("id")
            except Exception:
                pass
            return True, None, template_id, template_category, gupshup_message_id
        return False, f"Gupshup returned {resp.status_code}: {resp.text[:300]}", template_id, template_category, None
    except httpx.TimeoutException:
        return False, "Gupshup request timed out", template_id, template_category, None
    except Exception as exc:
        logger.exception("Gupshup send failed for tenant=%s template=%s", getattr(tenant, "id", None), template_name)
        return False, str(exc), template_id, template_category, None
