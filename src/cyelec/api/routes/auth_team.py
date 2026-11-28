"""Commercial B2B authentication (JWT + Google OIDC SSO), team RBAC & password recovery routes."""

from __future__ import annotations

import hmac
import os
import sys
from collections.abc import Callable
from typing import Annotated, cast

import anyio
from fastapi import APIRouter, Header, HTTPException, Request

from cyelec.api.context import AppContext
from cyelec.api.schemas import (
    AddMemberRequest,
    AdminResetPasswordRequest,
    AuthMeResponse,
    AuthTokenResponse,
    ChangePasswordRequest,
    GoogleAuthRequest,
    LoginRequest,
    MemberDeletedResponse,
    PasswordUpdatedResponse,
    RegisterRequest,
    ResetPasswordRequest,
    UpdateMemberRoleRequest,
)
from cyelec.api.workers import env_flag, slugify_tenant, validate_safe_id
from cyelec.saas.auth import (
    AuthPrincipal,
    Role,
    create_access_token,
    verify_google_id_token,
)
from cyelec.saas.billing import QuotaStatus
from cyelec.saas.store import TenantProfile, UserRecord


def _resolve_google_verifier() -> Callable[[str, str], dict[str, str]]:
    """Honor monkeypatches on cyelec.api.app.verify_google_id_token for testability."""
    app_mod = sys.modules.get("cyelec.api.app")
    fn = getattr(app_mod, "verify_google_id_token", None) if app_mod is not None else None
    if callable(fn):
        return cast(Callable[[str, str], dict[str, str]], fn)
    return verify_google_id_token


def build_auth_team_router(ctx: AppContext) -> APIRouter:
    """Register auth, Google SSO, team RBAC management, and password reset endpoints."""
    router = APIRouter()

    @router.post("/v1/auth/register", response_model=AuthTokenResponse, status_code=201)
    async def auth_register(payload: RegisterRequest) -> AuthTokenResponse:
        """Self-serve firm registration creating a new tenant workspace and Owner seat."""
        if env_flag("CYELEC_DISABLE_SELF_REGISTER"):
            raise HTTPException(status_code=403, detail="Self-serve registration is disabled.")

        expected_invite = os.environ.get("CYELEC_PILOT_INVITE_CODE", "").strip()
        initial_plan = "free"
        if expected_invite:
            if not hmac.compare_digest(payload.invite_code.strip(), expected_invite):
                raise HTTPException(
                    status_code=403,
                    detail="Closed pilot registration requires a valid pilot invite code.",
                )
            initial_plan = "pilot"

        firm = payload.firm_name.strip()
        if len(firm) < 2:
            raise HTTPException(status_code=422, detail="firm_name must be at least 2 characters.")

        tid = slugify_tenant(payload.tenant_id or firm)

        def _do_register() -> tuple[TenantProfile, UserRecord, QuotaStatus]:
            ctx.store.get_or_create_tenant(tid)
            tenant = ctx.store.update_tenant_profile(tid, display_name=firm)
            tenant = ctx.store.update_tenant_billing(
                tid, plan=initial_plan, subscription_status="active"
            )
            user = ctx.store.create_user(
                tid,
                payload.email,
                payload.display_name.strip() or firm,
                role=Role.OWNER,
                password=payload.password,
                auth_provider="password",
            )
            quota = ctx.store.get_quota_status(tid)
            return tenant, user, quota

        try:
            tenant, user, quota = await anyio.to_thread.run_sync(_do_register)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        token = create_access_token(
            user_id=user.user_id,
            tenant_id=tenant.tenant_id,
            email=user.email,
            role=user.role,
        )
        return AuthTokenResponse(access_token=token, user=user, tenant=tenant, quota=quota)

    @router.post("/v1/auth/change-password", response_model=PasswordUpdatedResponse)
    async def auth_change_password(
        request: Request,
        payload: ChangePasswordRequest,
    ) -> PasswordUpdatedResponse:
        """Allow an authenticated JWT firm user to change their own password."""
        principal = getattr(request.state, "principal", None)
        if not isinstance(principal, AuthPrincipal) or not principal.user_id or not principal.email:
            raise HTTPException(
                status_code=401,
                detail="Changing password requires an active signed-in user session.",
            )
        verified = await anyio.to_thread.run_sync(
            ctx.store.authenticate_user, principal.email, payload.current_password
        )
        if verified is None or verified.user_id != principal.user_id:
            raise HTTPException(status_code=401, detail="Current password is incorrect.")
        try:
            updated = await anyio.to_thread.run_sync(
                ctx.store.update_user_password,
                principal.tenant_id,
                principal.user_id,
                payload.new_password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="User account not found.")
        return PasswordUpdatedResponse(updated=True, email=updated.email)

    @router.post("/v1/auth/login", response_model=AuthTokenResponse)
    async def auth_login(payload: LoginRequest) -> AuthTokenResponse:
        """Authenticate an email + password firm member and return a signed HS256 JWT."""
        user = await anyio.to_thread.run_sync(
            ctx.store.authenticate_user, payload.email, payload.password
        )
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        tenant = await anyio.to_thread.run_sync(ctx.store.get_or_create_tenant, user.tenant_id)
        quota = await anyio.to_thread.run_sync(ctx.store.get_quota_status, user.tenant_id)
        token = create_access_token(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            email=user.email,
            role=user.role,
        )
        return AuthTokenResponse(access_token=token, user=user, tenant=tenant, quota=quota)

    @router.post("/v1/auth/google", response_model=AuthTokenResponse)
    async def auth_google_sso(payload: GoogleAuthRequest) -> AuthTokenResponse:
        """Sign in or provision a firm workspace via verified Google OIDC ID token."""
        client_id = os.environ.get("CYELEC_GOOGLE_CLIENT_ID", "").strip()
        if not client_id:
            raise HTTPException(
                status_code=503,
                detail="Google OIDC SSO is not enabled (set CYELEC_GOOGLE_CLIENT_ID).",
            )
        verifier = _resolve_google_verifier()
        try:
            claims = await anyio.to_thread.run_sync(verifier, payload.id_token, client_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        def _find_or_provision() -> tuple[TenantProfile, UserRecord, QuotaStatus]:
            existing = ctx.store.get_user_by_email(claims["email"])
            if existing is not None:
                t = ctx.store.get_or_create_tenant(existing.tenant_id)
                return t, existing, ctx.store.get_quota_status(existing.tenant_id)
            firm = payload.firm_name.strip() or f"{claims['name']} Practice"
            new_tid = slugify_tenant(firm)
            ctx.store.get_or_create_tenant(new_tid)
            t = ctx.store.update_tenant_profile(new_tid, display_name=firm)
            t = ctx.store.update_tenant_billing(new_tid, plan="free", subscription_status="active")
            u = ctx.store.create_user(
                new_tid,
                claims["email"],
                claims["name"],
                role=Role.OWNER,
                password=None,
                auth_provider="google",
            )
            return t, u, ctx.store.get_quota_status(new_tid)

        tenant, user, quota = await anyio.to_thread.run_sync(_find_or_provision)
        token = create_access_token(
            user_id=user.user_id,
            tenant_id=tenant.tenant_id,
            email=user.email,
            role=user.role,
        )
        return AuthTokenResponse(access_token=token, user=user, tenant=tenant, quota=quota)

    @router.get("/v1/auth/me", response_model=AuthMeResponse)
    def auth_me(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> AuthMeResponse:
        """Return resolved caller identity, RBAC role, tenant profile, and monthly solve quota."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        principal = getattr(request.state, "principal", None)
        if not isinstance(principal, AuthPrincipal):
            principal = AuthPrincipal(tenant_id=tid, role=Role.OWNER, auth_method="demo")
        tenant = ctx.store.get_or_create_tenant(tid)
        quota = ctx.store.get_quota_status(tid)
        return AuthMeResponse(principal=principal, tenant=tenant, quota=quota)

    @router.get("/v1/team/members", response_model=list[UserRecord])
    def list_team_members(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> list[UserRecord]:
        tid = ctx.effective_tenant(request, x_tenant_id)
        return ctx.store.list_users(tid)

    @router.post("/v1/team/members", response_model=UserRecord, status_code=201)
    async def add_team_member(
        request: Request,
        payload: AddMemberRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> UserRecord:
        tid = ctx.effective_tenant(request, x_tenant_id)
        try:
            return await anyio.to_thread.run_sync(
                lambda: ctx.store.create_user(
                    tid,
                    payload.email,
                    payload.display_name,
                    role=payload.role,
                    password=payload.password,
                    auth_provider="password",
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/v1/team/members/{user_id}/role", response_model=UserRecord)
    def update_team_member_role(
        request: Request,
        user_id: str,
        payload: UpdateMemberRoleRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> UserRecord:
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_uid = validate_safe_id(user_id, "user_id")
        try:
            updated = ctx.store.update_user_role(tid, safe_uid, payload.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="Team member not found.")
        return updated

    @router.delete("/v1/team/members/{user_id}", response_model=MemberDeletedResponse)
    def remove_team_member(
        request: Request,
        user_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> MemberDeletedResponse:
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_uid = validate_safe_id(user_id, "user_id")
        try:
            deleted = ctx.store.delete_user(tid, safe_uid)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Team member not found.")
        return MemberDeletedResponse(deleted=True, user_id=safe_uid)

    @router.post(
        "/v1/team/members/{user_id}/reset-password",
        response_model=PasswordUpdatedResponse,
    )
    async def owner_reset_member_password(
        request: Request,
        user_id: str,
        payload: ResetPasswordRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> PasswordUpdatedResponse:
        """Firm Owner password reset for a colleague in the same tenant workspace."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_uid = validate_safe_id(user_id, "user_id")
        try:
            updated = await anyio.to_thread.run_sync(
                ctx.store.update_user_password, tid, safe_uid, payload.new_password
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="Team member not found.")
        return PasswordUpdatedResponse(updated=True, email=updated.email)

    @router.post("/v1/admin/users/reset-password", response_model=PasswordUpdatedResponse)
    async def admin_reset_user_password(
        payload: AdminResetPasswordRequest,
    ) -> PasswordUpdatedResponse:
        """Admin-only password recovery by email for locked-out pilot owners (X-Admin-Key)."""
        user = await anyio.to_thread.run_sync(ctx.store.get_user_by_email, payload.email)
        if user is None:
            raise HTTPException(status_code=404, detail="User account not found.")
        try:
            updated = await anyio.to_thread.run_sync(
                ctx.store.update_user_password,
                user.tenant_id,
                user.user_id,
                payload.new_password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="User account not found.")
        return PasswordUpdatedResponse(updated=True, email=updated.email)

    return router
