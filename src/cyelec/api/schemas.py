"""Pydantic request and response schemas for the CyElec Multi-Tenant SaaS FastAPI service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from cyelec.models.building import BenchmarkTemplate, BuildingModel, DxfLayerInfo
from cyelec.models.electrical import (
    BoqRateOverrides,
    DesignOverrides,
    ReportLanguage,
    TenantBranding,
)
from cyelec.models.spatial import Phase2DesignResult
from cyelec.saas.auth import AuthPrincipal, Role
from cyelec.saas.billing import QuotaStatus
from cyelec.saas.store import TenantProfile, UserRecord


class HealthResponse(BaseModel):
    """Health & runtime capability status."""

    model_config = ConfigDict(extra="forbid")

    status: str
    pipeline_version: str
    standards_versions: dict[str, str]
    qelectrotech_available: bool
    ifc_available: bool = False
    dwg_available: bool = False
    pdf_export_available: bool = False
    stripe_configured: bool = False
    google_sso_configured: bool = False
    auth_required: bool = False
    invite_code_required: bool = False
    store_backend: str = "sqlite"


class SolveBuildingRequest(BaseModel):
    """Stateless JSON request for solving a canonical BuildingModel with optional overrides."""

    model_config = ConfigDict(extra="forbid")

    building: BuildingModel
    overrides: DesignOverrides | None = None


class SolveResponseEnvelope(BaseModel):
    """Multi-tenant response envelope preserving tenant audit tag alongside design output."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    result: Phase2DesignResult


class SolvePreviewEnvelope(BaseModel):
    """Preview response returning full design JSON plus E-100..E-301 vector preview SVGs."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    result: Phase2DesignResult
    floorplan_svg: str
    sld_svg: str
    lighting_svg: str = ""
    lighting_heatmap_svg: str = ""
    power_svg: str = ""
    elv_svg: str = ""
    site_svg: str = ""
    riser_svg: str = ""
    selectivity_svg: str = ""
    boards_svg: str = ""
    details_svg: str = ""


class DxfLayersResponse(BaseModel):
    """Discovered DXF layers and inferred architectural roles for the Layer Mapping Wizard."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    filename: str
    layers: list[DxfLayerInfo]


class TenantUpdateRequest(BaseModel):
    """Update tenant consulting practice branding, language, and custom BOQ rate card."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    report_language: ReportLanguage | None = None
    branding: TenantBranding | None = None
    boq_rate_overrides: BoqRateOverrides | None = None


class ApiKeyIssuedResponse(BaseModel):
    """One-time plaintext response when issuing or rotating a tenant API key."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    api_key: str


class CreateProjectRequest(BaseModel):
    """Create a new tenant-scoped electrical engineering project."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""


class CreateRevisionRequest(BaseModel):
    """Create a new solved revision from a benchmark template or canonical BuildingModel."""

    model_config = ConfigDict(extra="forbid")

    benchmark_template: BenchmarkTemplate | None = None
    building: BuildingModel | None = None
    overrides: DesignOverrides | None = None
    carry_forward_overrides: bool = True
    notes: str = ""


class ProjectDeletedResponse(BaseModel):
    """Confirmation envelope after deleting a tenant-owned project and its revisions."""

    model_config = ConfigDict(extra="forbid")

    deleted: bool
    project_id: str


class TenantDeletedResponse(BaseModel):
    """Confirmation envelope after GDPR Article 17 cascade tenant erasure."""

    model_config = ConfigDict(extra="forbid")

    deleted: bool
    tenant_id: str


class RegisterRequest(BaseModel):
    """Self-serve firm registration creating a tenant workspace and initial Owner seat."""

    model_config = ConfigDict(extra="forbid")

    firm_name: str
    email: str
    password: str
    display_name: str = ""
    tenant_id: str | None = None
    invite_code: str = ""


class ChangePasswordRequest(BaseModel):
    """Authenticated user self-service password change request."""

    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    """Firm Owner password reset request for a team member seat."""

    model_config = ConfigDict(extra="forbid")

    new_password: str


class AdminResetPasswordRequest(BaseModel):
    """Platform Admin password reset request for recovering a locked-out pilot account."""

    model_config = ConfigDict(extra="forbid")

    email: str
    new_password: str


class PasswordUpdatedResponse(BaseModel):
    """Confirmation envelope returned after a password change or reset."""

    model_config = ConfigDict(extra="forbid")

    updated: bool
    email: str


class LoginRequest(BaseModel):
    """Email + password login request returning a signed HS256 JWT."""

    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class GoogleAuthRequest(BaseModel):
    """Google OIDC ID-token sign-in / firm registration request."""

    model_config = ConfigDict(extra="forbid")

    id_token: str
    firm_name: str = ""


class AuthTokenResponse(BaseModel):
    """Issued HS256 JWT session envelope with user, tenant, and quota metadata."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    user: UserRecord
    tenant: TenantProfile
    quota: QuotaStatus


class AuthMeResponse(BaseModel):
    """Resolved caller identity, RBAC role, tenant profile, and monthly quota."""

    model_config = ConfigDict(extra="forbid")

    principal: AuthPrincipal
    tenant: TenantProfile
    quota: QuotaStatus


class AddMemberRequest(BaseModel):
    """Owner request to add an engineer, viewer, or co-owner seat to the firm."""

    model_config = ConfigDict(extra="forbid")

    email: str
    display_name: str
    password: str
    role: Role = Role.ENGINEER


class UpdateMemberRoleRequest(BaseModel):
    """Owner request to change a team member's RBAC role."""

    model_config = ConfigDict(extra="forbid")

    role: Role


class MemberDeletedResponse(BaseModel):
    """Confirmation envelope after removing a firm member seat."""

    model_config = ConfigDict(extra="forbid")

    deleted: bool
    user_id: str


class CheckoutRequest(BaseModel):
    """Owner request to initiate a Stripe Hosted Checkout upgrade session."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    success_url: str = ""
    cancel_url: str = ""


class CheckoutSessionResponse(BaseModel):
    """Stripe Hosted Checkout redirect envelope."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    checkout_url: str


class PortalSessionResponse(BaseModel):
    """Stripe Billing Customer Portal redirect envelope."""

    model_config = ConfigDict(extra="forbid")

    portal_url: str


class AdminUpdatePlanRequest(BaseModel):
    """Admin manual B2B plan / quota assignment request."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    subscription_status: str = "active"
    monthly_quota_override: int | None = None
    clear_quota_override: bool = False
