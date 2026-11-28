"""Security, authentication, RBAC, rate-limiting, quota & observability HTTP middleware."""

from __future__ import annotations

import hmac
import os
import time
import uuid
from collections.abc import Awaitable, Callable

import anyio
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from cyelec.api.context import AppContext
from cyelec.api.workers import SAFE_ID_RE, env_flag, security_headers
from cyelec.saas.auth import AuthPrincipal, Role, verify_access_token

_MAX_RATE_BUCKETS = 4096

_PUBLIC_V1_PATHS = frozenset(
    {
        "/v1/auth/register",
        "/v1/auth/login",
        "/v1/auth/google",
        "/v1/billing/plans",
        "/v1/billing/webhook",
    }
)


def _is_privileged_tenant_mutation(method: str, path: str) -> bool:
    """Return True for owner-only tenant key, erasure, team, and billing mutations."""
    return (
        (method == "POST" and path == "/v1/tenants/me/api-key")
        or (method == "DELETE" and path == "/v1/tenants/me")
        or (path.startswith("/v1/team/members") and method in ("POST", "PATCH", "DELETE"))
        or (method == "POST" and path in ("/v1/billing/checkout", "/v1/billing/portal"))
    )


def _is_rate_limited_route(method: str, path: str) -> bool:
    return method == "POST" and (
        path.startswith(("/v1/solve/", "/v1/admin/", "/v1/auth/"))
        or path in ("/v1/dxf/layers", "/v1/tenants/me/api-key")
        or "/revisions" in path
    )


def _error_response(
    status_code: int,
    detail: str,
    req_id: str,
    *,
    is_https: bool,
    extra: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=security_headers(req_id, is_https=is_https, extra=extra),
    )


async def _resolve_principal(
    ctx: AppContext, request: Request
) -> tuple[AuthPrincipal | None, str | None, str]:
    """Authenticate caller via Bearer JWT or per-tenant X-API-Key."""
    principal: AuthPrincipal | None = None
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        jwt_principal = verify_access_token(auth_header[7:].strip())
        if jwt_principal is not None and jwt_principal.user_id:
            live_user = await anyio.to_thread.run_sync(
                ctx.store.get_user,
                jwt_principal.tenant_id,
                jwt_principal.user_id,
            )
            if live_user is not None:
                principal = jwt_principal.model_copy(
                    update={"role": live_user.role, "email": live_user.email}
                )
                request.state.auth_tenant_id = principal.tenant_id
                request.state.principal = principal

    supplied = request.headers.get("X-API-Key", "").strip()
    auth_tenant: str | None = principal.tenant_id if principal is not None else None
    if principal is None and supplied:
        resolved_tid = await ctx.resolve_api_key_nonblocking(supplied)
        if resolved_tid is not None:
            auth_tenant = resolved_tid
            principal = AuthPrincipal(
                tenant_id=resolved_tid,
                role=Role.OWNER,
                auth_method="api_key",
            )
            request.state.auth_tenant_id = resolved_tid
            request.state.principal = principal
    return principal, auth_tenant, supplied


def _check_access_control(
    request: Request,
    path: str,
    principal: AuthPrincipal | None,
    supplied_api_key: str,
    req_id: str,
    *,
    is_https: bool,
) -> JSONResponse | None:
    """Enforce admin keys, API/JWT auth, RBAC roles, and staging demo mutation lock."""
    admin_key = os.environ.get("CYELEC_ADMIN_KEY", "").strip()
    supplied_admin = request.headers.get("X-Admin-Key", "").strip()
    admin_ok = bool(admin_key) and hmac.compare_digest(supplied_admin, admin_key)

    if path.startswith("/v1/admin/"):
        if not admin_key:
            return _error_response(
                503,
                "Admin provisioning disabled (CYELEC_ADMIN_KEY not set).",
                req_id,
                is_https=is_https,
            )
        if not admin_ok:
            return _error_response(
                401, "Invalid or missing X-Admin-Key.", req_id, is_https=is_https
            )
        return None

    if not path.startswith("/v1/") or path in _PUBLIC_V1_PATHS:
        return None

    expected_key = os.environ.get("CYELEC_API_KEY", "").strip()
    auth_required = env_flag("CYELEC_AUTH_REQUIRED")
    global_ok = bool(expected_key) and hmac.compare_digest(supplied_api_key, expected_key)
    if global_ok and principal is None:
        hdr_tid = request.headers.get("X-Tenant-ID", "anonymous").strip() or "anonymous"
        principal = AuthPrincipal(tenant_id=hdr_tid, role=Role.OWNER, auth_method="global_key")
        request.state.principal = principal

    if (expected_key or auth_required) and not (global_ok or principal is not None):
        return _error_response(
            401, "Invalid or missing X-API-Key or Bearer token.", req_id, is_https=is_https
        )

    if principal is not None and principal.auth_method == "jwt":
        if (
            principal.role == Role.VIEWER
            and path != "/v1/auth/change-password"
            and request.method in ("POST", "PUT", "PATCH", "DELETE")
        ):
            return _error_response(
                403,
                "Insufficient role: 'viewer' accounts are read-only.",
                req_id,
                is_https=is_https,
            )
        if principal.role != Role.OWNER and _is_privileged_tenant_mutation(request.method, path):
            return _error_response(
                403,
                "Insufficient role: 'owner' privileges required.",
                req_id,
                is_https=is_https,
            )

    demo_lock = env_flag("CYELEC_DEMO_LOCK_TENANT_MUTATIONS")
    if (
        demo_lock
        and not auth_required
        and _is_privileged_tenant_mutation(request.method, path)
        and not (principal is not None or global_ok or admin_ok)
    ):
        return _error_response(
            403,
            (
                "Tenant key issuance, team/billing mutations, and erasure require an "
                "authenticated X-API-Key, Bearer JWT, or X-Admin-Key in demo mode."
            ),
            req_id,
            is_https=is_https,
        )
    return None


def _check_rate_limit(
    ctx: AppContext,
    request: Request,
    path: str,
    auth_tenant: str | None,
    req_id: str,
    *,
    is_https: bool,
) -> JSONResponse | None:
    """Apply bounded sliding-window per-minute rate limiting on compute & auth POST routes."""
    rpm_raw = os.environ.get("CYELEC_RATE_LIMIT_RPM", "0").strip()
    rpm_limit = int(rpm_raw) if rpm_raw.isdigit() else 0
    if rpm_limit <= 0 or not _is_rate_limited_route(request.method, path):
        return None

    xff_parts = [
        part.strip()
        for part in request.headers.get("X-Forwarded-For", "").split(",")
        if part.strip()
    ]
    # Cloud Run / Google Front End appends the real client IP on the right
    trusted_ip = (
        xff_parts[-1] if xff_parts else (request.client.host if request.client else "unknown")
    )
    bucket_key = auth_tenant or trusted_ip
    now_mono = time.monotonic()
    buckets = ctx.rate_buckets
    if len(buckets) >= _MAX_RATE_BUCKETS and bucket_key not in buckets:
        stale = [k for k, dq in buckets.items() if not dq or (now_mono - dq[-1]) > 60]
        for k in stale:
            del buckets[k]
        if len(buckets) >= _MAX_RATE_BUCKETS:
            oldest_key = min(buckets, key=lambda k: buckets[k][-1])
            del buckets[oldest_key]
    q = buckets[bucket_key]
    while q and (now_mono - q[0]) > 60.0:
        q.popleft()
    if len(q) >= rpm_limit:
        return _error_response(
            429,
            f"Rate limit exceeded ({rpm_limit} requests/min). Retry shortly.",
            req_id,
            is_https=is_https,
            extra={"Retry-After": "60"},
        )
    q.append(now_mono)
    return None


async def _check_solve_quota(
    ctx: AppContext,
    request: Request,
    path: str,
    auth_tenant: str | None,
    req_id: str,
    *,
    is_https: bool,
) -> tuple[bool, str | None, dict[str, str], JSONResponse | None]:
    """Check per-tenant monthly solve quota before dispatch; returns headers & optional 429."""
    is_solve_route = request.method == "POST" and (
        path.startswith("/v1/solve/") or "/revisions" in path
    )
    if not is_solve_route:
        return False, None, {}, None

    raw_hdr_tid = request.headers.get("X-Tenant-ID", "anonymous").strip() or "anonymous"
    cand_tid = auth_tenant or raw_hdr_tid
    if not (SAFE_ID_RE.match(cand_tid) and ".." not in cand_tid):
        return True, None, {}, None

    q_status = await anyio.to_thread.run_sync(ctx.store.get_quota_status, cand_tid)
    quota_hdrs = {
        "X-Plan-Tier": q_status.plan_id,
        "X-Quota-Limit": str(q_status.solves_limit),
        "X-Quota-Used": str(q_status.solves_used),
        "X-Quota-Remaining": str(q_status.solves_remaining),
    }
    if env_flag("CYELEC_ENFORCE_QUOTAS", default=True) and q_status.exceeded:
        err = _error_response(
            429,
            (
                f"Monthly solve quota exceeded ({q_status.solves_used}/"
                f"{q_status.solves_limit} on plan '{q_status.plan_id}' for "
                f"{q_status.month_key}). Upgrade plan or contact support."
            ),
            req_id,
            is_https=is_https,
            extra=quota_hdrs,
        )
        return True, cand_tid, quota_hdrs, err
    return True, cand_tid, quota_hdrs, None


def register_security_middleware(api: FastAPI, ctx: AppContext) -> None:
    """Attach security, RBAC, rate-limit, monthly quota, and timing headers middleware."""

    @api.middleware("http")
    async def security_and_observability_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        raw_req_id = request.headers.get("X-Request-ID", "").strip()
        req_id = (
            raw_req_id
            if (SAFE_ID_RE.fullmatch(raw_req_id) and len(raw_req_id) <= 64)
            else str(uuid.uuid4())
        )
        t0 = time.perf_counter()
        raw_proto = (
            request.headers.get("x-forwarded-proto", request.url.scheme)
            .split(",")[0]
            .strip()
            .lower()
        )
        is_https = raw_proto == "https"
        path = request.url.path

        principal, auth_tenant, supplied_key = await _resolve_principal(ctx, request)
        denied = _check_access_control(
            request, path, principal, supplied_key, req_id, is_https=is_https
        )
        if denied is not None:
            return denied

        limited = _check_rate_limit(ctx, request, path, auth_tenant, req_id, is_https=is_https)
        if limited is not None:
            return limited

        is_solve_route, quota_tid, quota_hdrs, quota_err = await _check_solve_quota(
            ctx, request, path, auth_tenant, req_id, is_https=is_https
        )
        if quota_err is not None:
            return quota_err

        response = await call_next(request)

        if is_solve_route and quota_tid is not None and 200 <= response.status_code < 300:
            new_used = await anyio.to_thread.run_sync(
                ctx.store.increment_monthly_solve_count, quota_tid
            )
            limit_val = int(quota_hdrs.get("X-Quota-Limit", "500"))
            quota_hdrs["X-Quota-Used"] = str(new_used)
            quota_hdrs["X-Quota-Remaining"] = str(max(0, limit_val - new_used))

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        is_html = "text/html" in response.headers.get("content-type", "").lower()
        sec_hdrs = security_headers(
            req_id,
            is_html=is_html,
            is_https=is_https,
            extra={"X-Process-Time-Ms": f"{elapsed_ms:.1f}", **quota_hdrs},
        )
        for hk, hv in sec_hdrs.items():
            response.headers[hk] = hv
        return response
