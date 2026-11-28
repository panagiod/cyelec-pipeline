"""Stripe billing plans, checkout/portal sessions, webhooks & admin tenant provisioning routes."""

from __future__ import annotations

import json
import os
from typing import Annotated

import anyio
from fastapi import APIRouter, Header, HTTPException, Request

from cyelec.api.context import AppContext
from cyelec.api.schemas import (
    AdminUpdatePlanRequest,
    ApiKeyIssuedResponse,
    CheckoutRequest,
    CheckoutSessionResponse,
    PortalSessionResponse,
)
from cyelec.api.workers import safe_base_url, safe_redirect_url, validate_safe_id
from cyelec.saas.auth import AuthPrincipal
from cyelec.saas.billing import (
    PLAN_CATALOG,
    PlanSpec,
    StripeWebhookOutcome,
    SubscriptionStatus,
    create_stripe_checkout_session,
    create_stripe_portal_session,
    is_stripe_configured,
    process_stripe_event,
    resolve_plan_spec,
    verify_stripe_signature,
)
from cyelec.saas.store import TenantProfile


def build_billing_router(ctx: AppContext) -> APIRouter:
    """Register Stripe billing, webhook, and admin tenant provisioning/plan routes."""
    router = APIRouter()

    @router.post("/v1/admin/tenants/{tenant_id}/api-key", response_model=ApiKeyIssuedResponse)
    def admin_provision_tenant_api_key(tenant_id: str) -> ApiKeyIssuedResponse:
        """Provision or rotate a tenant API key using CYELEC_ADMIN_KEY (X-Admin-Key)."""
        safe_tid = validate_safe_id(tenant_id, "tenant_id")
        raw_key = ctx.store.issue_tenant_api_key(safe_tid)
        ctx.evict_auth_cache(safe_tid)
        return ApiKeyIssuedResponse(tenant_id=safe_tid, api_key=raw_key)

    @router.get("/v1/billing/plans", response_model=list[PlanSpec])
    def list_billing_plans() -> list[PlanSpec]:
        """Return public Cyprus B2B SaaS subscription tiers, quotas, and seat limits."""
        return [PLAN_CATALOG[k] for k in ("free", "pilot", "pro", "enterprise")]

    @router.get("/v1/billing/subscription", response_model=SubscriptionStatus)
    def get_my_subscription(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SubscriptionStatus:
        tid = ctx.effective_tenant(request, x_tenant_id)
        tenant = ctx.store.get_or_create_tenant(tid)
        plan = resolve_plan_spec(tenant.plan)
        quota = ctx.store.get_quota_status(tid)
        seats_used = len(ctx.store.list_users(tid))
        return SubscriptionStatus(
            tenant_id=tid,
            plan=plan,
            subscription_status=tenant.subscription_status,
            stripe_customer_id=tenant.stripe_customer_id,
            stripe_subscription_id=tenant.stripe_subscription_id,
            stripe_configured=is_stripe_configured(),
            seats_used=seats_used,
            seats_limit=plan.max_seats,
            quota=quota,
        )

    @router.post("/v1/billing/checkout", response_model=CheckoutSessionResponse)
    async def create_billing_checkout(
        request: Request,
        payload: CheckoutRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> CheckoutSessionResponse:
        tid = ctx.effective_tenant(request, x_tenant_id)
        plan_id = payload.plan_id.strip().lower()
        if plan_id not in ("pilot", "pro", "enterprise"):
            raise HTTPException(
                status_code=422, detail="plan_id must be 'pilot', 'pro', or 'enterprise'."
            )
        tenant = ctx.store.get_or_create_tenant(tid)
        base = safe_base_url(request) or "https://localhost"
        success_url = safe_redirect_url(payload.success_url, f"{base}/?billing=success")
        cancel_url = safe_redirect_url(payload.cancel_url, f"{base}/?billing=cancel")
        principal = getattr(request.state, "principal", None)
        cust_email = principal.email if isinstance(principal, AuthPrincipal) else None

        try:
            sess = await anyio.to_thread.run_sync(
                lambda: create_stripe_checkout_session(
                    tenant_id=tid,
                    plan_id=plan_id,
                    success_url=success_url,
                    cancel_url=cancel_url,
                    customer_email=cust_email,
                    stripe_customer_id=tenant.stripe_customer_id,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        return CheckoutSessionResponse(
            session_id=sess["session_id"],
            checkout_url=sess["checkout_url"],
        )

    @router.post("/v1/billing/portal", response_model=PortalSessionResponse)
    async def create_billing_portal(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> PortalSessionResponse:
        tid = ctx.effective_tenant(request, x_tenant_id)
        tenant = ctx.store.get_or_create_tenant(tid)
        if not tenant.stripe_customer_id:
            raise HTTPException(
                status_code=400, detail="No active Stripe billing profile for this tenant yet."
            )
        base = safe_base_url(request) or "https://localhost"
        try:
            sess = await anyio.to_thread.run_sync(
                lambda: create_stripe_portal_session(
                    stripe_customer_id=tenant.stripe_customer_id or "",
                    return_url=f"{base}/",
                )
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return PortalSessionResponse(portal_url=sess["portal_url"])

    @router.post("/v1/billing/webhook", response_model=StripeWebhookOutcome)
    async def stripe_billing_webhook(request: Request) -> StripeWebhookOutcome:
        """Verify Stripe-Signature HMAC-SHA256 webhook and update tenant subscription atomically."""
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
        if not webhook_secret:
            raise HTTPException(
                status_code=503,
                detail="Stripe webhooks disabled (STRIPE_WEBHOOK_SECRET not set).",
            )
        raw_body = await request.body()
        sig_hdr = request.headers.get("Stripe-Signature", "").strip()
        if not verify_stripe_signature(raw_body, sig_hdr, webhook_secret):
            raise HTTPException(status_code=400, detail="Invalid Stripe-Signature header.")
        try:
            event = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON webhook body.") from exc
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="Invalid Stripe event payload.")
        return await anyio.to_thread.run_sync(process_stripe_event, event, ctx.store)

    @router.put("/v1/admin/tenants/{tenant_id}/plan", response_model=TenantProfile)
    def admin_update_tenant_plan(
        tenant_id: str,
        payload: AdminUpdatePlanRequest,
    ) -> TenantProfile:
        """Admin-only manual B2B plan & monthly quota override (requires X-Admin-Key)."""
        safe_tid = validate_safe_id(tenant_id, "tenant_id")
        plan_id = payload.plan_id.strip().lower()
        if plan_id not in PLAN_CATALOG:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown plan_id '{plan_id}'. Use: {', '.join(PLAN_CATALOG)}.",
            )
        return ctx.store.update_tenant_billing(
            safe_tid,
            plan=plan_id,
            subscription_status=payload.subscription_status,
            monthly_quota_override=payload.monthly_quota_override,
            clear_quota_override=payload.clear_quota_override,
        )

    return router
