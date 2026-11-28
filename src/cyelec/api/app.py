"""Stateless Multi-Tenant EU B2B SaaS FastAPI composition root & ETEK Review Workbench."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from cyelec import __version__
from cyelec.api.context import AppContext
from cyelec.api.middleware import register_security_middleware
from cyelec.api.routes import (
    build_auth_team_router,
    build_billing_router,
    build_solve_router,
    build_ui_assets_router,
    build_workspace_router,
)
from cyelec.api.schemas import (
    AddMemberRequest,
    AdminResetPasswordRequest,
    AdminUpdatePlanRequest,
    ApiKeyIssuedResponse,
    AuthMeResponse,
    AuthTokenResponse,
    ChangePasswordRequest,
    CheckoutRequest,
    CheckoutSessionResponse,
    CreateProjectRequest,
    CreateRevisionRequest,
    DxfLayersResponse,
    GoogleAuthRequest,
    HealthResponse,
    LoginRequest,
    MemberDeletedResponse,
    PasswordUpdatedResponse,
    PortalSessionResponse,
    ProjectDeletedResponse,
    RegisterRequest,
    ResetPasswordRequest,
    SolveBuildingRequest,
    SolvePreviewEnvelope,
    SolveResponseEnvelope,
    TenantDeletedResponse,
    TenantUpdateRequest,
    UpdateMemberRoleRequest,
)
from cyelec.api.workers import env_flag, is_ifc_available, sanitize_client_error
from cyelec.saas.auth import verify_google_id_token
from cyelec.saas.firestore_store import (
    FirestoreWorkspaceStore,
    build_workspace_store_from_env,
)
from cyelec.saas.store import WorkspaceStore, WorkspaceStoreProtocol

# Backwards-compatible aliases preserved for direct imports & test monkeypatching
_sanitize_client_error = sanitize_client_error

__all__ = [
    "AddMemberRequest",
    "AdminResetPasswordRequest",
    "AdminUpdatePlanRequest",
    "ApiKeyIssuedResponse",
    "AuthMeResponse",
    "AuthTokenResponse",
    "ChangePasswordRequest",
    "CheckoutRequest",
    "CheckoutSessionResponse",
    "CreateProjectRequest",
    "CreateRevisionRequest",
    "DxfLayersResponse",
    "GoogleAuthRequest",
    "HealthResponse",
    "LoginRequest",
    "MemberDeletedResponse",
    "PasswordUpdatedResponse",
    "PortalSessionResponse",
    "ProjectDeletedResponse",
    "RegisterRequest",
    "ResetPasswordRequest",
    "SolveBuildingRequest",
    "SolvePreviewEnvelope",
    "SolveResponseEnvelope",
    "TenantDeletedResponse",
    "TenantUpdateRequest",
    "UpdateMemberRoleRequest",
    "_sanitize_client_error",
    "app",
    "create_app",
    "is_ifc_available",
    "verify_google_id_token",
]


def _resolve_store(
    db_path: Path | None,
    store: WorkspaceStoreProtocol | None,
) -> tuple[WorkspaceStoreProtocol, str]:
    if store is not None:
        backend = (
            "firestore"
            if isinstance(store, FirestoreWorkspaceStore)
            else ("sqlite" if isinstance(store, WorkspaceStore) else type(store).__name__)
        )
        return store, backend
    if db_path is not None:
        return WorkspaceStore(db_path=db_path), "sqlite"
    return build_workspace_store_from_env()


def create_app(
    db_path: Path | None = None,
    *,
    store: WorkspaceStoreProtocol | None = None,
) -> FastAPI:
    """Create the Multi-Tenant EU B2B SaaS FastAPI application with strict tenant isolation."""
    active_store, store_backend = _resolve_store(db_path, store)
    disable_openapi = env_flag("CYELEC_DISABLE_OPENAPI")

    api = FastAPI(
        title="cyelec-pipeline SaaS API",
        version=__version__,
        description=(
            "Deterministic BS 7671 / IEC 60364 / Cyprus EAC electrical engineering "
            "Multi-Tenant EU B2B SaaS service with strict per-tenant isolation, "
            "revision diffing, Pass-2 override carry-forward, and usage metering."
        ),
        docs_url=None if disable_openapi else "/docs",
        redoc_url=None if disable_openapi else "/redoc",
        openapi_url=None if disable_openapi else "/openapi.json",
    )
    api.state.store = active_store
    api.state.store_backend = store_backend

    ctx = AppContext(
        store=active_store,
        store_backend=store_backend,
        disable_openapi=disable_openapi,
    )
    register_security_middleware(api, ctx)
    api.include_router(build_ui_assets_router(ctx))
    api.include_router(build_solve_router(ctx))
    api.include_router(build_workspace_router(ctx))
    api.include_router(build_auth_team_router(ctx))
    api.include_router(build_billing_router(ctx))
    return api


app = create_app()
