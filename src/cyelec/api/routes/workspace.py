"""Multi-tenant workspace, GDPR export/erasure, projects, revisions, diff & usage routes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile

from cyelec.api.context import AppContext
from cyelec.api.schemas import (
    ApiKeyIssuedResponse,
    CreateProjectRequest,
    CreateRevisionRequest,
    ProjectDeletedResponse,
    TenantDeletedResponse,
    TenantUpdateRequest,
)
from cyelec.api.workers import (
    SUPPORTED_CAD_SUFFIXES,
    parse_layer_mapping_json,
    parse_overrides_json,
    read_upload_bounded,
    run_cad_sync,
    solve_dxf_bytes_sync,
    validate_safe_id,
)
from cyelec.models.building import BuildingType
from cyelec.pipeline import run_phase2_pipeline
from cyelec.saas.revisions import RevisionDiff, carry_forward_overrides, compute_revision_diff
from cyelec.saas.store import (
    ProjectSummary,
    RevisionRecord,
    RevisionSummary,
    TenantExportBundle,
    TenantProfile,
    UsageSummary,
)
from cyelec.synth.benchmarks import generate_benchmark_dxf


def build_workspace_router(ctx: AppContext) -> APIRouter:
    """Register tenant profile, GDPR export/erasure, projects, revisions, diff & usage routes."""
    router = APIRouter()

    @router.get("/v1/tenants/me", response_model=TenantProfile)
    def get_my_tenant(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantProfile:
        tid = ctx.effective_tenant(request, x_tenant_id)
        return ctx.store.get_or_create_tenant(tid)

    @router.put("/v1/tenants/me", response_model=TenantProfile)
    def update_my_tenant(
        request: Request,
        payload: TenantUpdateRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantProfile:
        tid = ctx.effective_tenant(request, x_tenant_id)
        return ctx.store.update_tenant_profile(
            tid,
            display_name=payload.display_name,
            report_language=payload.report_language,
            branding=payload.branding,
            boq_rate_overrides=payload.boq_rate_overrides,
        )

    @router.post("/v1/tenants/me/api-key", response_model=ApiKeyIssuedResponse)
    def issue_my_api_key(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ApiKeyIssuedResponse:
        tid = ctx.effective_tenant(request, x_tenant_id)
        raw_key = ctx.store.issue_tenant_api_key(tid)
        ctx.evict_auth_cache(tid)
        return ApiKeyIssuedResponse(tenant_id=tid, api_key=raw_key)

    @router.get("/v1/tenants/me/export", response_model=TenantExportBundle)
    def export_my_tenant_data(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantExportBundle:
        """EU GDPR Article 20 portable data export (excludes API key secrets)."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        return ctx.store.export_tenant_data(tid)

    @router.delete("/v1/tenants/me", response_model=TenantDeletedResponse)
    def delete_my_tenant(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantDeletedResponse:
        """EU GDPR Article 17 right to erasure: cascade-delete tenant and all owned records."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        deleted = ctx.store.delete_tenant_cascade(tid)
        ctx.evict_auth_cache(tid)
        return TenantDeletedResponse(deleted=deleted, tenant_id=tid)

    @router.post("/v1/projects", response_model=ProjectSummary, status_code=201)
    def create_project(
        request: Request,
        payload: CreateProjectRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ProjectSummary:
        tid = ctx.effective_tenant(request, x_tenant_id)
        return ctx.store.create_project(tid, payload.name, payload.description)

    @router.get("/v1/projects", response_model=list[ProjectSummary])
    def list_projects(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> list[ProjectSummary]:
        tid = ctx.effective_tenant(request, x_tenant_id)
        return ctx.store.list_projects(tid)

    @router.get("/v1/projects/{project_id}", response_model=ProjectSummary)
    def get_project(
        request: Request,
        project_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ProjectSummary:
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_pid = validate_safe_id(project_id, "project_id")
        proj = ctx.store.get_project(tid, safe_pid)
        if proj is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return proj

    @router.delete("/v1/projects/{project_id}", response_model=ProjectDeletedResponse)
    def delete_project(
        request: Request,
        project_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ProjectDeletedResponse:
        """Delete a tenant-owned project and all its revisions (404 if not owned by tenant)."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_pid = validate_safe_id(project_id, "project_id")
        if not ctx.store.delete_project(tid, safe_pid):
            raise HTTPException(status_code=404, detail="Project not found.")
        return ProjectDeletedResponse(deleted=True, project_id=safe_pid)

    @router.post(
        "/v1/projects/{project_id}/revisions",
        response_model=RevisionRecord,
        status_code=201,
    )
    def create_project_revision(
        request: Request,
        project_id: str,
        payload: CreateRevisionRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> RevisionRecord:
        """Solve & persist a new revision with optional Pass-2 override carry-forward."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_pid = validate_safe_id(project_id, "project_id")
        if ctx.store.get_project(tid, safe_pid) is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        if payload.building is not None:
            building = payload.building
        elif payload.benchmark_template is not None:
            building = generate_benchmark_dxf(payload.benchmark_template)
        else:
            raise HTTPException(
                status_code=422,
                detail="Provide either 'building' or 'benchmark_template'.",
            )

        t0 = time.perf_counter()
        eff_overrides = payload.overrides
        if payload.carry_forward_overrides:
            prev = ctx.store.get_latest_revision(tid, safe_pid)
            if prev is not None:
                eff_overrides = carry_forward_overrides(prev.overrides, eff_overrides)
        eff_overrides = ctx.merge_tenant_defaults(tid, eff_overrides)

        p2 = run_phase2_pipeline(building, overrides=eff_overrides)
        rec = ctx.store.save_revision(
            tid, safe_pid, p2, overrides=eff_overrides, notes=payload.notes
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        ctx.store.record_usage(
            tid,
            "revision_solve",
            elapsed_ms,
            len(p2.model_dump_json().encode("utf-8")),
            project_id=safe_pid,
        )
        return rec

    @router.post(
        "/v1/projects/{project_id}/revisions/dxf",
        response_model=RevisionRecord,
        status_code=201,
    )
    async def create_project_revision_dxf(
        request: Request,
        project_id: str,
        file: Annotated[
            UploadFile, File(description="Architectural .dxf, .dwg, .ifc, or vector .pdf plan")
        ],
        overrides_json: Annotated[str | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        carry_forward: Annotated[bool, Form()] = True,
        notes: Annotated[str, Form()] = "",
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> RevisionRecord:
        """Upload a DXF/DWG/IFC/vector-PDF revision, carry forward overrides, and persist."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_pid = validate_safe_id(project_id, "project_id")
        if ctx.store.get_project(tid, safe_pid) is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_CAD_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="Only .dxf, .dwg, .ifc, or vector .pdf uploads are supported.",
            )

        t0 = time.perf_counter()
        data = await read_upload_bounded(file)
        overrides = parse_overrides_json(overrides_json)
        layer_mapping = parse_layer_mapping_json(layer_mapping_json)

        if carry_forward:
            prev = ctx.store.get_latest_revision(tid, safe_pid)
            if prev is not None:
                overrides = carry_forward_overrides(prev.overrides, overrides)
        overrides = ctx.merge_tenant_defaults(tid, overrides)

        p2 = await run_cad_sync(
            solve_dxf_bytes_sync, data, suffix, overrides, building_type, layer_mapping
        )
        rec = ctx.store.save_revision(tid, safe_pid, p2, overrides=overrides, notes=notes)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        ctx.store.record_usage(
            tid,
            "dxf_revision_solve",
            elapsed_ms,
            len(data),
            project_id=safe_pid,
        )
        return rec

    @router.get("/v1/projects/{project_id}/revisions", response_model=list[RevisionSummary])
    def list_project_revisions(
        request: Request,
        project_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> list[RevisionSummary]:
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_pid = validate_safe_id(project_id, "project_id")
        if ctx.store.get_project(tid, safe_pid) is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return ctx.store.list_revisions(tid, safe_pid)

    @router.get(
        "/v1/projects/{project_id}/revisions/{revision_number}",
        response_model=RevisionRecord,
    )
    def get_project_revision(
        request: Request,
        project_id: str,
        revision_number: int,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> RevisionRecord:
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_pid = validate_safe_id(project_id, "project_id")
        rec = ctx.store.get_revision(tid, safe_pid, revision_number)
        if rec is None:
            raise HTTPException(status_code=404, detail="Revision not found.")
        return rec

    @router.get(
        "/v1/projects/{project_id}/diff/{rev_a}/{rev_b}",
        response_model=RevisionDiff,
    )
    def diff_project_revisions(
        request: Request,
        project_id: str,
        rev_a: int,
        rev_b: int,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> RevisionDiff:
        """Compute deterministic engineering diff (rooms, circuits, BOQ, flags) across revisions."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        safe_pid = validate_safe_id(project_id, "project_id")
        ra = ctx.store.get_revision(tid, safe_pid, rev_a)
        rb = ctx.store.get_revision(tid, safe_pid, rev_b)
        if ra is None or rb is None:
            raise HTTPException(status_code=404, detail="One or both revisions not found.")
        return compute_revision_diff(rev_a, ra.result, rev_b, rb.result)

    @router.get("/v1/tenant/usage", response_model=UsageSummary)
    def get_tenant_usage(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> UsageSummary:
        tid = ctx.effective_tenant(request, x_tenant_id)
        return ctx.store.get_usage_summary(tid)

    return router
