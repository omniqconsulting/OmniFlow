"""
app/services/gupshup.py
Single integration point for all outbound Gupshup WhatsApp calls, per-tenant.
No other file should call the Gupshup API directly — mirrors the existing
app/services/msg91.py convention for the platform this replaces.

API: POST https://api.gupshup.io/sm/api/v1/template/msg, form-urlencoded,
`apikey: <secret token>` header — per docs.gupshup.io's official
whatsapp-business-api reference (confirmed 2026-07-16). Two earlier
attempts at this integration got this wrong in different ways:
  1. This exact endpoint/apikey combo, but with `template.id` set to the
     *Facebook* numeric template ID instead of Gupshup's own template UUID
     ("Gupshup temp ID" in the console's template list) — produced a
     misleading 401 "Portal User Not Found With APIKey" even with a
     correct, freshly-verified API key, because a malformed template
     reference apparently surfaces as a generic auth-style rejection here
     rather than a clean "template not found" error.
  2. Switching to the account's alternate Gateway API
     (mediaapi.smsgupshup.com/GatewayAPI/rest, Bearer auth) — this
     authenticated fine and returned success synchronously, but the
     message never actually reached the recipient (no delivery webhook,
     nothing in the WhatsApp thread) — likely a routing/config gap in that
     legacy API for this account, never fully root-caused.
This endpoint expects `template.id` = the Gupshup template UUID and a
separate `params` array (not a pre-rendered message body) — see
WHATSAPP_TEMPLATES[...]['gupshup_template_id'] vs
['gupshup_facebook_template_id'] in constants.py.
"""
import json
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
    if not template_id:
        return False, f"No Gupshup template UUID configured for {template_name}", template_id, template_category, None
    mobile_norm = normalize_mobile(mobile)
    source_norm = normalize_mobile(tenant.gupshup_source_number)

    form_data = {
        "source": source_norm,
        "destination": mobile_norm,
        "template": json.dumps({
            "id": template_id,
            "params": [str(v) for v in variables],
        }),
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                GUPSHUP_API_BASE,
                headers={
                    "apikey": tenant.gupshup_secret_token,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=form_data,
            )
        if resp.status_code in range(200, 300):
            gupshup_message_id = None
            resp_status = None
            try:
                resp_json = resp.json()
                gupshup_message_id = resp_json.get("messageId")
                resp_status = resp_json.get("status")
            except Exception:
                pass
            if resp_status and resp_status not in ("submitted", "success"):
                return False, f"Gupshup returned {resp.status_code} but status={resp_status}: {resp.text[:300]}", template_id, template_category, None
            return True, None, template_id, template_category, gupshup_message_id
        return False, f"Gupshup returned {resp.status_code}: {resp.text[:300]}", template_id, template_category, None
    except httpx.TimeoutException:
        return False, "Gupshup request timed out", template_id, template_category, None
    except Exception as exc:
        logger.exception("Gupshup send failed for tenant=%s template=%s", getattr(tenant, "id", None), template_name)
        return False, str(exc), template_id, template_category, None
