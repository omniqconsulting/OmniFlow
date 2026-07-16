"""
app/services/gupshup.py
Single integration point for all outbound Gupshup WhatsApp calls, per-tenant.
No other file should call the Gupshup API directly — mirrors the existing
app/services/msg91.py convention for the platform this replaces.

API: this tenant's Gupshup account (and per the account's own console docs,
Integrations > APIs > WhatsApp API) is provisioned on the Gateway API —
https://mediaapi.smsgupshup.com/GatewayAPI/rest, authenticated via
`Authorization: Bearer <secret token>` with `userid=<Client ID>` in the form
body — NOT the newer api.gupshup.io Partner API with a raw `apikey` header.
Sending to the wrong API/auth style produces a live-but-misleading 401
"Portal User Not Found With APIKey" (confirmed against this account,
2026-07-16), since the token is checked against the wrong user registry.
This Gateway API validates template sends against the fully-rendered
message text (see WHATSAPP_TEMPLATES[...]['body']) rather than accepting
separate params — the caller must substitute {{n}} placeholders itself.
whatsAppTemplateId is the Facebook template ID (numeric), not the Gupshup
template ID (UUID) — Decision #13.
"""
import re
import uuid
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


def get_platform_tenant(db):
    """
    Returns the designated tenant whose Gupshup WABA sends pre-onboarding
    prospect messages (registration received/rejected, SA alerts) — these
    fire before the prospect's own tenant has any WABA configured. Returns
    None (caller should no-op + log) if PLATFORM_ALERT_TENANT_ID isn't set
    or doesn't resolve to a tenant.
    """
    from app.constants import PLATFORM_ALERT_TENANT_ID
    if not PLATFORM_ALERT_TENANT_ID:
        return None
    from app.database import Tenant
    return db.query(Tenant).filter(Tenant.id == PLATFORM_ALERT_TENANT_ID).first()


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
    body = template.get("body")
    if not body:
        return False, f"No approved body text configured for {template_name} — cannot render for the Gateway API", template_id, template_category, None
    mobile_norm = normalize_mobile(mobile)

    rendered = body
    for i, value in enumerate(variables, start=1):
        rendered = rendered.replace("{{%d}}" % i, str(value))
    if re.search(r"\{\{\d+\}\}", rendered):
        return False, f"Unfilled {{n}} placeholder remains in rendered {template_name} body", template_id, template_category, None

    form_data = {
        "send_to": mobile_norm,
        "msg_type": "text",
        "userid": tenant.gupshup_client_id,
        "auth_scheme": "plain",
        "v": "1.1",
        "format": "json",
        "method": "SendMessage",
        "isHSM": "true",
        "isTemplate": "true",
        "msg_id": uuid.uuid4().hex,
        "whatsAppTemplateId": template_id,
        "msg": rendered,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                GUPSHUP_API_BASE,
                headers={
                    "Authorization": f"Bearer {tenant.gupshup_secret_token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=form_data,
            )
        if resp.status_code in range(200, 300):
            gupshup_message_id = None
            resp_status = None
            try:
                resp_json = resp.json().get("response", {})
                gupshup_message_id = resp_json.get("id")
                resp_status = resp_json.get("status")
            except Exception:
                pass
            if resp_status and resp_status != "success":
                return False, f"Gupshup returned {resp.status_code} but status={resp_status}: {resp.text[:300]}", template_id, template_category, None
            return True, None, template_id, template_category, gupshup_message_id
        return False, f"Gupshup returned {resp.status_code}: {resp.text[:300]}", template_id, template_category, None
    except httpx.TimeoutException:
        return False, "Gupshup request timed out", template_id, template_category, None
    except Exception as exc:
        logger.exception("Gupshup send failed for tenant=%s template=%s", getattr(tenant, "id", None), template_name)
        return False, str(exc), template_id, template_category, None
