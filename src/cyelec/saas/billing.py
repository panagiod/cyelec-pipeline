"""Commercial B2B SaaS plan tiers, monthly solve quotas, and zero-SDK Stripe billing adapter."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import time
import urllib.parse
from base64 import b64encode
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp string (YYYY-MM-DDTHH:MM:SSZ)."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class BillingStoreProtocol(Protocol):
    """Minimal store interface required by Stripe webhook processing (ISP/DIP)."""

    def find_tenant_by_stripe_customer(self, stripe_customer_id: str) -> str | None: ...

    def update_tenant_billing(
        self,
        tenant_id: str,
        *,
        plan: str | None = None,
        subscription_status: str | None = None,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        monthly_quota_override: int | None = None,
        clear_quota_override: bool = False,
    ) -> object: ...


class PlanSpec(BaseModel):
    """Commercial Cyprus B2B SaaS subscription tier definition."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    display_name: str
    monthly_price_eur: float
    monthly_solve_quota: int
    max_seats: int
    features: list[str] = Field(default_factory=list)


PLAN_CATALOG: dict[str, PlanSpec] = {
    "free": PlanSpec(
        plan_id="free",
        display_name="Starter / Evaluation",
        monthly_price_eur=0.0,
        monthly_solve_quota=15,
        max_seats=1,
        features=[
            "15 monthly deterministic DXF/IFC solves",
            "1 ETEK engineer seat",
            "Full BS 7671 + Cyprus EAC reports & ZIP packs",
        ],
    ),
    "pilot": PlanSpec(
        plan_id="pilot",
        display_name="Cyprus Solo ETEK Practice",
        monthly_price_eur=29.0,
        monthly_solve_quota=100,
        max_seats=3,
        features=[
            "100 monthly solves (Residential + Commercial MEP)",
            "Up to 3 firm seats (Owner / Engineer / Viewer RBAC)",
            "Custom firm BOQ rates, branding & revision diffing",
        ],
    ),
    "pro": PlanSpec(
        plan_id="pro",
        display_name="Cyprus MEP Engineering Firm (Pro)",
        monthly_price_eur=99.0,
        monthly_solve_quota=500,
        max_seats=10,
        features=[
            "500 monthly solves (Residential + 250+ kVA Commercial MEP)",
            "Up to 10 firm seats + API key automation",
            "Official EAC application schedules, A3/A1 CAD sheets & native PDFs",
        ],
    ),
    "enterprise": PlanSpec(
        plan_id="enterprise",
        display_name="Enterprise Multi-Office MEP",
        monthly_price_eur=299.0,
        monthly_solve_quota=10_000,
        max_seats=100,
        features=[
            "10,000 monthly solves with priority SLA",
            "Up to 100 firm seats + Google/OIDC SSO",
            "Dedicated DWG worker & custom EAC/ETEK templates",
        ],
    ),
}

_LEGACY_PLAN_MAP: dict[str, str] = {
    "pro_cyprus": "pro",
}


def resolve_plan_spec(plan_id: str) -> PlanSpec:
    """Resolve a tenant plan ID (supporting legacy 'pro_cyprus') to a PlanSpec."""
    canonical = _LEGACY_PLAN_MAP.get(plan_id.strip().lower(), plan_id.strip().lower())
    return PLAN_CATALOG.get(canonical, PLAN_CATALOG["pro"])


def current_month_key() -> str:
    """Return the current UTC billing month key (YYYY-MM)."""
    return datetime.now(tz=UTC).strftime("%Y-%m")


class QuotaStatus(BaseModel):
    """Real-time monthly solve quota status for a tenant."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    plan_id: str
    month_key: str
    solves_used: int
    solves_limit: int
    solves_remaining: int
    exceeded: bool


class SubscriptionStatus(BaseModel):
    """Complete tenant billing, seat allocation, and quota summary."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    plan: PlanSpec
    subscription_status: str
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    stripe_configured: bool = False
    seats_used: int
    seats_limit: int
    quota: QuotaStatus


class StripeWebhookOutcome(BaseModel):
    """Structured result returned after verifying and applying a Stripe webhook event."""

    model_config = ConfigDict(extra="forbid")

    received: bool = True
    event_type: str
    tenant_id: str | None = None
    action: str


def is_stripe_configured() -> bool:
    """Return True when STRIPE_SECRET_KEY is configured in the environment."""
    return bool(os.environ.get("STRIPE_SECRET_KEY", "").strip())


def get_stripe_price_id(plan_id: str) -> str:
    """Lookup Stripe Price ID for a plan tier from environment variables."""
    env_map = {
        "pilot": "STRIPE_PRICE_PILOT",
        "pro": "STRIPE_PRICE_PRO",
        "enterprise": "STRIPE_PRICE_ENTERPRISE",
    }
    env_var = env_map.get(plan_id.strip().lower(), "")
    return os.environ.get(env_var, "").strip() if env_var else ""


def plan_id_from_stripe_price(price_id: str) -> str | None:
    """Reverse-map a Stripe Price ID back to a CyElec plan tier."""
    if not price_id:
        return None
    for pid in ("pilot", "pro", "enterprise"):
        configured = get_stripe_price_id(pid)
        if configured and hmac.compare_digest(configured, price_id):
            return pid
    return None


def verify_stripe_signature(
    payload: bytes,
    sig_header: str,
    webhook_secret: str,
    *,
    tolerance_s: int = 300,
    now_ts: int | None = None,
) -> bool:
    """Verify Stripe's `Stripe-Signature` HMAC-SHA256 header with replay protection."""
    if not sig_header or not webhook_secret:
        return False

    timestamp_str = ""
    signatures: list[str] = []
    for part in sig_header.split(","):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if k == "t":
            timestamp_str = v.strip()
        elif k == "v1":
            signatures.append(v.strip())

    if not timestamp_str or not signatures:
        return False
    try:
        ts = int(timestamp_str)
    except ValueError:
        return False

    now = int(time.time()) if now_ts is None else now_ts
    if abs(now - ts) > tolerance_s:
        return False

    signed_payload = f"{ts}.".encode("ascii") + payload
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return any(hmac.compare_digest(expected, cand) for cand in signatures)


def _stripe_post_form(
    path: str,
    form_fields: dict[str, str],
    *,
    secret_key: str,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Execute an authenticated form-encoded POST against api.stripe.com using stdlib HTTPS."""
    auth_raw = f"{secret_key}:".encode()
    auth_hdr = "Basic " + b64encode(auth_raw).decode("ascii")
    body = urllib.parse.urlencode(form_fields).encode("utf-8")
    conn = http.client.HTTPSConnection("api.stripe.com", timeout=timeout_s)
    try:
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "Authorization": auth_hdr,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        data = json.loads(raw) if raw else {}
        if resp.status >= 400:
            err_msg = (
                data.get("error", {}).get("message", "Stripe API request failed.")
                if isinstance(data, dict)
                else "Stripe API request failed."
            )
            raise RuntimeError(f"Stripe API error ({resp.status}): {err_msg}")
        return data if isinstance(data, dict) else {}
    finally:
        conn.close()


def create_stripe_checkout_session(
    *,
    tenant_id: str,
    plan_id: str,
    success_url: str,
    cancel_url: str,
    customer_email: str | None = None,
    stripe_customer_id: str | None = None,
) -> dict[str, str]:
    """Create a Stripe Hosted Checkout Session for upgrading a Cyprus B2B tenant."""
    secret_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret_key:
        raise RuntimeError(
            "Stripe billing is not enabled yet (set STRIPE_SECRET_KEY & STRIPE_PRICE_* "
            "in GCP Secret Manager)."
        )
    price_id = get_stripe_price_id(plan_id)
    if not price_id:
        raise ValueError(
            f"No Stripe Price ID configured for plan '{plan_id}' "
            f"(set STRIPE_PRICE_{plan_id.upper()})."
        )

    fields: dict[str, str] = {
        "mode": "subscription",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": tenant_id,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "metadata[tenant_id]": tenant_id,
        "metadata[plan_id]": plan_id,
        "subscription_data[metadata][tenant_id]": tenant_id,
        "subscription_data[metadata][plan_id]": plan_id,
    }
    if stripe_customer_id:
        fields["customer"] = stripe_customer_id
    elif customer_email:
        fields["customer_email"] = customer_email

    data = _stripe_post_form("/v1/checkout/sessions", fields, secret_key=secret_key)
    return {
        "session_id": str(data.get("id", "")),
        "checkout_url": str(data.get("url", "")),
    }


def create_stripe_portal_session(
    *,
    stripe_customer_id: str,
    return_url: str,
) -> dict[str, str]:
    """Create a Stripe Billing Customer Portal session for invoices & payment method management."""
    secret_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not secret_key:
        raise RuntimeError("Stripe billing is not enabled yet (STRIPE_SECRET_KEY not set).")
    data = _stripe_post_form(
        "/v1/billing_portal/sessions",
        {"customer": stripe_customer_id, "return_url": return_url},
        secret_key=secret_key,
    )
    return {"portal_url": str(data.get("url", ""))}


def process_stripe_event(
    event: dict[str, Any],
    store: BillingStoreProtocol,
) -> StripeWebhookOutcome:
    """Apply a verified Stripe webhook event to the tenant's subscription state atomically."""
    event_type = str(event.get("type", "")).strip()
    obj = event.get("data", {}).get("object", {})
    if not isinstance(obj, dict):
        return StripeWebhookOutcome(event_type=event_type, action="ignored_empty_object")

    raw_meta = obj.get("metadata")
    metadata: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    customer_id = str(obj.get("customer") or "").strip() or None
    tenant_id = (
        str(metadata.get("tenant_id") or obj.get("client_reference_id") or "").strip() or None
    )
    if tenant_id is None and customer_id:
        tenant_id = store.find_tenant_by_stripe_customer(customer_id)

    if not tenant_id:
        return StripeWebhookOutcome(event_type=event_type, action="ignored_unknown_tenant")

    if event_type == "checkout.session.completed":
        plan_id = str(metadata.get("plan_id") or "pro").strip().lower()
        if plan_id not in PLAN_CATALOG:
            plan_id = "pro"
        sub_id = str(obj.get("subscription") or "").strip() or None
        store.update_tenant_billing(
            tenant_id,
            plan=plan_id,
            subscription_status="active",
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub_id,
        )
        return StripeWebhookOutcome(
            event_type=event_type,
            tenant_id=tenant_id,
            action=f"activated_plan:{plan_id}",
        )

    if event_type == "customer.subscription.updated":
        sub_status = str(obj.get("status") or "active").strip().lower()
        sub_id = str(obj.get("id") or "").strip() or None
        plan_id_meta = str(metadata.get("plan_id") or "").strip().lower()
        resolved_plan: str | None = plan_id_meta if plan_id_meta in PLAN_CATALOG else None
        if resolved_plan is None:
            items_data = (
                obj.get("items", {}).get("data", [])
                if isinstance(obj.get("items"), dict)
                else []
            )
            if items_data and isinstance(items_data[0], dict):
                price_obj = items_data[0].get("price", {})
                if isinstance(price_obj, dict):
                    resolved_plan = plan_id_from_stripe_price(str(price_obj.get("id", "")))
        store.update_tenant_billing(
            tenant_id,
            plan=resolved_plan,
            subscription_status=sub_status,
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub_id,
        )
        return StripeWebhookOutcome(
            event_type=event_type,
            tenant_id=tenant_id,
            action=f"updated_subscription:{sub_status}",
        )

    if event_type == "customer.subscription.deleted":
        store.update_tenant_billing(
            tenant_id,
            plan="free",
            subscription_status="canceled",
            stripe_customer_id=customer_id,
        )
        return StripeWebhookOutcome(
            event_type=event_type,
            tenant_id=tenant_id,
            action="downgraded_to_free",
        )

    if event_type == "invoice.payment_failed":
        store.update_tenant_billing(
            tenant_id,
            subscription_status="past_due",
            stripe_customer_id=customer_id,
        )
        return StripeWebhookOutcome(
            event_type=event_type,
            tenant_id=tenant_id,
            action="marked_past_due",
        )

    return StripeWebhookOutcome(
        event_type=event_type,
        tenant_id=tenant_id,
        action="ignored_unhandled_event",
    )
