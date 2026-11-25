"""Stateless Multi-Tenant EU B2B SaaS FastAPI service with ETEK Review Workbench & hardening."""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import anyio
from ezdxf.lldxf.const import DXFError
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from cyelec import __version__
from cyelec.api.ui import render_etek_workbench_html
from cyelec.ingest.dxf_ingest import ingest_multi_floor_dxf, inspect_dxf_layers
from cyelec.models.building import BuildingModel, BuildingType, DxfLayerInfo, DxfLayerMapping
from cyelec.models.electrical import (
    BoqRateOverrides,
    DesignOverrides,
    ReportLanguage,
    TenantBranding,
)
from cyelec.models.spatial import Phase2DesignResult
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.eac_pack import build_submission_zip
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.render.qet_bridge import is_qelectrotech_available
from cyelec.render.sld import render_sld_svg
from cyelec.saas.revisions import RevisionDiff, carry_forward_overrides, compute_revision_diff
from cyelec.saas.store import (
    ProjectSummary,
    RevisionRecord,
    RevisionSummary,
    TenantExportBundle,
    TenantProfile,
    UsageSummary,
    WorkspaceStore,
    WorkspaceStoreProtocol,
)
from cyelec.standards_loader import load_standards
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB hard cap per DXF/IFC upload
_CHUNK_BYTES = 256 * 1024


class HealthResponse(BaseModel):
    """Health & runtime capability status."""

    model_config = ConfigDict(extra="forbid")

    status: str
    pipeline_version: str
    standards_versions: dict[str, str]
    qelectrotech_available: bool


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
    """Preview response returning full design JSON plus inline Floorplan & SLD vector SVGs."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    result: Phase2DesignResult
    floorplan_svg: str
    sld_svg: str


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


def _parse_overrides_json(raw: str | None) -> DesignOverrides | None:
    if raw is None or not raw.strip():
        return None
    try:
        return DesignOverrides.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid DesignOverrides JSON: {exc}"
        ) from exc


def _parse_layer_mapping_json(raw: str | None) -> DxfLayerMapping | None:
    if raw is None or not raw.strip():
        return None
    try:
        return DxfLayerMapping.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid DxfLayerMapping JSON: {exc}"
        ) from exc


async def _read_upload_bounded(file: UploadFile, max_bytes: int = _MAX_UPLOAD_BYTES) -> bytes:
    """Stream-read an uploaded file with a hard RAM cap, returning HTTP 413 if exceeded."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds maximum allowed size ({max_bytes // (1024 * 1024)} MB).",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    return data


def _inspect_dxf_layers_sync(data: bytes) -> list[DxfLayerInfo]:
    with tempfile.TemporaryDirectory(prefix="cyelec_layers_") as tmp_dir:
        in_path = Path(tmp_dir) / "plan.dxf"
        in_path.write_bytes(data)
        return inspect_dxf_layers(in_path)


def _solve_dxf_bytes_sync(
    data: bytes,
    suffix: str,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> Phase2DesignResult:
    with tempfile.TemporaryDirectory(prefix="cyelec_req_") as tmp_dir:
        in_path = Path(tmp_dir) / f"plan{suffix}"
        in_path.write_bytes(data)
        return run_phase2_pipeline(
            in_path,
            overrides=overrides,
            building_type=building_type,
            layer_mapping=layer_mapping,
        )


def _solve_dxf_bundle_sync(
    data: bytes,
    suffix: str,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> tuple[Phase2DesignResult, bytes]:
    with tempfile.TemporaryDirectory(prefix="cyelec_bundle_") as tmp_dir:
        tmp = Path(tmp_dir)
        in_path = tmp / f"plan{suffix}"
        in_path.write_bytes(data)
        p2 = run_phase2_pipeline(
            in_path,
            overrides=overrides,
            building_type=building_type,
            layer_mapping=layer_mapping,
        )
        outputs = export_phase3_deliverables(p2, tmp / "pack")
        zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
        return p2, zip_path.read_bytes()


def _solve_multifloor_dxf_sync(
    floor_blobs: list[tuple[str, bytes]],
    submain_lengths_m: dict[str, float] | None,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> Phase2DesignResult:
    with tempfile.TemporaryDirectory(prefix="cyelec_mf_") as tmp_dir:
        tmp = Path(tmp_dir)
        floor_files: list[tuple[str, Path]] = []
        for idx, (label, data) in enumerate(floor_blobs):
            p = tmp / f"floor_{idx}_{label}.dxf"
            p.write_bytes(data)
            floor_files.append((label, p))
        model, mf_flags = ingest_multi_floor_dxf(
            floor_files,
            submain_lengths_m=submain_lengths_m,
            building_type=building_type,
            pv_capacity_kva=(
                overrides.pv_capacity_kva
                if (overrides is not None and overrides.pv_capacity_kva is not None)
                else 0.0
            ),
            layer_mapping=layer_mapping,
        )
        return run_phase2_pipeline(model, overrides=overrides, extra_flags=mf_flags)


def create_app(
    db_path: Path | None = None,
    *,
    store: WorkspaceStoreProtocol | None = None,
) -> FastAPI:
    """Create the Multi-Tenant EU B2B SaaS FastAPI application with strict tenant isolation."""
    active_store: WorkspaceStoreProtocol = (
        store if store is not None else WorkspaceStore(db_path=db_path)
    )
    store = active_store
    api = FastAPI(
        title="cyelec-pipeline SaaS API",
        version=__version__,
        description=(
            "Deterministic BS 7671 / IEC 60364 / Cyprus EAC electrical engineering "
            "Multi-Tenant EU B2B SaaS service with strict per-tenant isolation, "
            "revision diffing, Pass-2 override carry-forward, and usage metering."
        ),
    )
    api.state.store = store

    def _effective_tenant(request: Request, header_tenant: str) -> str:
        auth_tid = getattr(request.state, "auth_tenant_id", None)
        if isinstance(auth_tid, str) and auth_tid:
            return auth_tid
        return header_tenant or "anonymous"

    def _merge_tenant_defaults(
        tenant_id: str,
        overrides: DesignOverrides | None,
    ) -> DesignOverrides | None:
        """Apply saved tenant branding, language, and BOQ rate defaults unless overridden."""
        profile = store.get_or_create_tenant(tenant_id)
        if (
            profile.branding is None
            and profile.report_language == ReportLanguage.EN
            and profile.boq_rate_overrides is None
        ):
            return overrides
        if overrides is None:
            return DesignOverrides(
                report_language=profile.report_language,
                branding=profile.branding,
                boq_rate_overrides=profile.boq_rate_overrides,
            )
        updates: dict[str, object] = {}
        if overrides.branding is None and profile.branding is not None:
            updates["branding"] = profile.branding
        if (
            overrides.report_language == ReportLanguage.EN
            and profile.report_language != ReportLanguage.EN
        ):
            updates["report_language"] = profile.report_language
        if overrides.boq_rate_overrides is None and profile.boq_rate_overrides is not None:
            updates["boq_rate_overrides"] = profile.boq_rate_overrides
        return overrides.model_copy(update=updates) if updates else overrides

    @api.middleware("http")
    async def security_and_observability_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        t0 = time.perf_counter()

        supplied = request.headers.get("X-API-Key", "").strip()
        auth_tenant: str | None = None
        if supplied:
            auth_tenant = store.resolve_tenant_by_api_key(supplied)
            if auth_tenant is not None:
                request.state.auth_tenant_id = auth_tenant

        if request.url.path.startswith("/v1/admin/"):
            admin_key = os.environ.get("CYELEC_ADMIN_KEY", "").strip()
            supplied_admin = request.headers.get("X-Admin-Key", "").strip()
            if not admin_key:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Admin provisioning disabled (CYELEC_ADMIN_KEY not set)."},
                    headers={"X-Request-ID": req_id, "X-Content-Type-Options": "nosniff"},
                )
            if not hmac.compare_digest(supplied_admin, admin_key):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing X-Admin-Key."},
                    headers={"X-Request-ID": req_id, "X-Content-Type-Options": "nosniff"},
                )
        elif request.url.path.startswith("/v1/"):
            expected_key = os.environ.get("CYELEC_API_KEY", "").strip()
            auth_required = os.environ.get("CYELEC_AUTH_REQUIRED", "").strip() in ("1", "true")
            global_ok = bool(expected_key) and hmac.compare_digest(supplied, expected_key)

            if (expected_key or auth_required) and not (global_ok or auth_tenant is not None):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing X-API-Key."},
                    headers={"X-Request-ID": req_id, "X-Content-Type-Options": "nosniff"},
                )

        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @api.get("/", response_class=HTMLResponse)
    def workbench_ui() -> HTMLResponse:
        return HTMLResponse(content=render_etek_workbench_html())

    @api.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        std = load_standards()
        return HealthResponse(
            status="ok",
            pipeline_version=__version__,
            standards_versions=std.versions_map(),
            qelectrotech_available=is_qelectrotech_available(),
        )

    @api.get("/v1/standards")
    def get_standards() -> dict[str, object]:
        std = load_standards()
        return {
            "pipeline_version": __version__,
            "versions": std.versions_map(),
            "eac_profiles": {
                k.value: v.model_dump() for k, v in std.eac_rules.profiles.items()
            },
            "earthing_default": std.eac_rules.earthing.default_system.value,
            "ambient_defaults_c": std.eac_rules.ambient_defaults_c.model_dump(),
        }

    @api.post("/v1/admin/tenants/{tenant_id}/api-key", response_model=ApiKeyIssuedResponse)
    def admin_provision_tenant_api_key(tenant_id: str) -> ApiKeyIssuedResponse:
        """Provision or rotate a tenant API key using CYELEC_ADMIN_KEY (X-Admin-Key)."""
        raw_key = store.issue_tenant_api_key(tenant_id)
        return ApiKeyIssuedResponse(tenant_id=tenant_id, api_key=raw_key)

    @api.post("/v1/solve/building", response_model=SolveResponseEnvelope)
    def solve_building(
        request: Request,
        payload: SolveBuildingRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolveResponseEnvelope:
        tid = _effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        eff_overrides = _merge_tenant_defaults(tid, payload.overrides)
        p2 = run_phase2_pipeline(payload.building, overrides=eff_overrides)
        store.record_usage(
            tid,
            "solve_building",
            (time.perf_counter() - t0) * 1000.0,
            len(payload.model_dump_json().encode("utf-8")),
        )
        return SolveResponseEnvelope(tenant_id=tid, result=p2)

    @api.post("/v1/solve/benchmark/{template}", response_model=SolvePreviewEnvelope)
    def solve_benchmark_preview(
        request: Request,
        template: BenchmarkTemplate,
        overrides: Annotated[DesignOverrides | None, Body()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolvePreviewEnvelope:
        tid = _effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        eff_overrides = _merge_tenant_defaults(tid, overrides)
        building = generate_benchmark_dxf(template)
        p2 = run_phase2_pipeline(building, overrides=eff_overrides)
        store.record_usage(
            tid,
            "solve_benchmark",
            (time.perf_counter() - t0) * 1000.0,
            len(p2.model_dump_json().encode("utf-8")),
        )
        return SolvePreviewEnvelope(
            tenant_id=tid,
            result=p2,
            floorplan_svg=render_floorplan_svg(p2),
            sld_svg=render_sld_svg(p2.design),
        )

    @api.post("/v1/solve/benchmark/{template}/bundle")
    def solve_benchmark_bundle(
        request: Request,
        template: BenchmarkTemplate,
        overrides: Annotated[DesignOverrides | None, Body()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> Response:
        tid = _effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        eff_overrides = _merge_tenant_defaults(tid, overrides)
        with tempfile.TemporaryDirectory(prefix="cyelec_bench_") as tmp_dir:
            tmp = Path(tmp_dir)
            building = generate_benchmark_dxf(template, out_dxf=tmp / f"{template.value}.dxf")
            p2 = run_phase2_pipeline(building, overrides=eff_overrides)
            outputs = export_phase3_deliverables(p2, tmp / "pack")
            zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
            zip_bytes = zip_path.read_bytes()
        store.record_usage(
            tid,
            "solve_benchmark_bundle",
            (time.perf_counter() - t0) * 1000.0,
            len(zip_bytes),
        )
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="eac_pack_{template.value}.zip"',
                "X-Tenant-ID": tid,
                "X-Input-SHA256": p2.design.provenance.input_sha256,
            },
        )

    @api.post("/v1/dxf/layers", response_model=DxfLayersResponse)
    async def inspect_dxf_layers_upload(
        request: Request,
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf floorplan")],
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> DxfLayersResponse:
        """Inspect layers, entity counts, and inferred roles of an uploaded DXF."""
        tid = _effective_tenant(request, x_tenant_id)
        filename = file.filename or "upload.dxf"
        if Path(filename).suffix.lower() != ".dxf":
            raise HTTPException(status_code=400, detail="Only .dxf files support layer inspection.")

        t0 = time.perf_counter()
        data = await _read_upload_bounded(file)
        try:
            layers = await anyio.to_thread.run_sync(_inspect_dxf_layers_sync, data)
        except (ValueError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store.record_usage(
            tid, "dxf_layer_inspect", (time.perf_counter() - t0) * 1000.0, len(data)
        )
        return DxfLayersResponse(tenant_id=tid, filename=filename, layers=layers)

    @api.post("/v1/solve/dxf", response_model=SolveResponseEnvelope)
    async def solve_dxf_upload(
        request: Request,
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf floorplan")],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolveResponseEnvelope:
        tid = _effective_tenant(request, x_tenant_id)
        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".dxf", ".ifc"):
            raise HTTPException(status_code=400, detail="Only .dxf or .ifc uploads are supported.")

        t0 = time.perf_counter()
        data = await _read_upload_bounded(file)
        overrides = _merge_tenant_defaults(tid, _parse_overrides_json(overrides_json))
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)
        try:
            p2 = await anyio.to_thread.run_sync(
                _solve_dxf_bytes_sync, data, suffix, overrides, building_type, layer_mapping
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store.record_usage(tid, "solve_dxf", (time.perf_counter() - t0) * 1000.0, len(data))
        return SolveResponseEnvelope(tenant_id=tid, result=p2)

    @api.post("/v1/solve/dxf/preview", response_model=SolvePreviewEnvelope)
    async def solve_dxf_preview(
        request: Request,
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf floorplan")],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolvePreviewEnvelope:
        envelope = await solve_dxf_upload(
            request=request,
            file=file,
            overrides_json=overrides_json,
            building_type=building_type,
            layer_mapping_json=layer_mapping_json,
            x_tenant_id=x_tenant_id,
        )
        fp_svg = await anyio.to_thread.run_sync(render_floorplan_svg, envelope.result)
        sld_svg = await anyio.to_thread.run_sync(render_sld_svg, envelope.result.design)
        return SolvePreviewEnvelope(
            tenant_id=envelope.tenant_id,
            result=envelope.result,
            floorplan_svg=fp_svg,
            sld_svg=sld_svg,
        )

    @api.post("/v1/solve/dxf/bundle")
    async def solve_dxf_bundle(
        request: Request,
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf floorplan")],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> Response:
        """Run full Phase 0-3 pipeline and return a ZIP archive of the EAC submission pack."""
        tid = _effective_tenant(request, x_tenant_id)
        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".dxf", ".ifc"):
            raise HTTPException(status_code=400, detail="Only .dxf or .ifc uploads are supported.")

        t0 = time.perf_counter()
        data = await _read_upload_bounded(file)
        overrides = _merge_tenant_defaults(tid, _parse_overrides_json(overrides_json))
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)
        try:
            p2, zip_bytes = await anyio.to_thread.run_sync(
                _solve_dxf_bundle_sync, data, suffix, overrides, building_type, layer_mapping
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store.record_usage(
            tid, "solve_dxf_bundle", (time.perf_counter() - t0) * 1000.0, len(zip_bytes)
        )
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="eac_submission_pack.zip"',
                "X-Tenant-ID": tid,
                "X-Input-SHA256": p2.design.provenance.input_sha256,
            },
        )

    @api.post("/v1/solve/dxf/multifloor", response_model=SolvePreviewEnvelope)
    async def solve_dxf_multifloor(
        request: Request,
        files: Annotated[
            list[UploadFile],
            File(description="Two or more architectural .dxf floorplans (e.g., GF.dxf, 1F.dxf)"),
        ],
        floors_json: Annotated[str | None, Form()] = None,
        submain_lengths_json: Annotated[str | None, Form()] = None,
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolvePreviewEnvelope:
        """Combine multiple uploaded floor DXF files into MDB -> DB-<FLOOR> preview solve."""
        tid = _effective_tenant(request, x_tenant_id)
        if not files:
            raise HTTPException(status_code=400, detail="At least one .dxf floor file is required.")

        default_labels = ["GF"] + [f"{i}F" for i in range(1, len(files))]
        labels: list[str] = default_labels[: len(files)]
        if floors_json and floors_json.strip():
            try:
                parsed_labels = json.loads(floors_json)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=422, detail=f"Invalid floors_json: {exc}"
                ) from exc
            if not isinstance(parsed_labels, list) or len(parsed_labels) != len(files):
                raise HTTPException(
                    status_code=422,
                    detail="floors_json must be a JSON array matching uploaded files count.",
                )
            labels = [str(lbl).strip().upper() or f"F{i}" for i, lbl in enumerate(parsed_labels)]

        submain_lengths: dict[str, float] | None = None
        if submain_lengths_json and submain_lengths_json.strip():
            try:
                raw_sm = json.loads(submain_lengths_json)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=422, detail=f"Invalid submain_lengths_json: {exc}"
                ) from exc
            if not isinstance(raw_sm, dict):
                raise HTTPException(
                    status_code=422, detail="submain_lengths_json must be a JSON object."
                )
            submain_lengths = {str(k).strip().upper(): float(v) for k, v in raw_sm.items()}

        t0 = time.perf_counter()
        floor_blobs: list[tuple[str, bytes]] = []
        total_bytes = 0
        for label, uf in zip(labels, files, strict=True):
            fname = uf.filename or f"{label}.dxf"
            if Path(fname).suffix.lower() != ".dxf":
                raise HTTPException(
                    status_code=400,
                    detail=f"Multi-floor combiner only supports .dxf uploads (got '{fname}').",
                )
            blob = await _read_upload_bounded(uf)
            total_bytes += len(blob)
            floor_blobs.append((label, blob))

        overrides = _merge_tenant_defaults(tid, _parse_overrides_json(overrides_json))
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)
        try:
            p2 = await anyio.to_thread.run_sync(
                _solve_multifloor_dxf_sync,
                floor_blobs,
                submain_lengths,
                overrides,
                building_type,
                layer_mapping,
            )
        except (ValueError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        fp_svg = await anyio.to_thread.run_sync(render_floorplan_svg, p2)
        sld_svg = await anyio.to_thread.run_sync(render_sld_svg, p2.design)
        store.record_usage(
            tid, "solve_dxf_multifloor", (time.perf_counter() - t0) * 1000.0, total_bytes
        )
        return SolvePreviewEnvelope(
            tenant_id=tid,
            result=p2,
            floorplan_svg=fp_svg,
            sld_svg=sld_svg,
        )

    # ==================================================================
    # Multi-Tenant B2B SaaS Workspace, Branding, Revisions, Diff & Usage
    # ==================================================================
    @api.get("/v1/tenants/me", response_model=TenantProfile)
    def get_my_tenant(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantProfile:
        tid = _effective_tenant(request, x_tenant_id)
        return store.get_or_create_tenant(tid)

    @api.put("/v1/tenants/me", response_model=TenantProfile)
    def update_my_tenant(
        request: Request,
        payload: TenantUpdateRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantProfile:
        tid = _effective_tenant(request, x_tenant_id)
        return store.update_tenant_profile(
            tid,
            display_name=payload.display_name,
            report_language=payload.report_language,
            branding=payload.branding,
            boq_rate_overrides=payload.boq_rate_overrides,
        )

    @api.post("/v1/tenants/me/api-key", response_model=ApiKeyIssuedResponse)
    def issue_my_api_key(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ApiKeyIssuedResponse:
        tid = _effective_tenant(request, x_tenant_id)
        raw_key = store.issue_tenant_api_key(tid)
        return ApiKeyIssuedResponse(tenant_id=tid, api_key=raw_key)

    @api.get("/v1/tenants/me/export", response_model=TenantExportBundle)
    def export_my_tenant_data(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantExportBundle:
        """EU GDPR Article 20 portable data export (excludes API key secrets)."""
        tid = _effective_tenant(request, x_tenant_id)
        return store.export_tenant_data(tid)

    @api.delete("/v1/tenants/me", response_model=TenantDeletedResponse)
    def delete_my_tenant(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> TenantDeletedResponse:
        """EU GDPR Article 17 right to erasure: cascade-delete tenant and all owned records."""
        tid = _effective_tenant(request, x_tenant_id)
        deleted = store.delete_tenant_cascade(tid)
        return TenantDeletedResponse(deleted=deleted, tenant_id=tid)

    @api.post("/v1/projects", response_model=ProjectSummary, status_code=201)
    def create_project(
        request: Request,
        payload: CreateProjectRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ProjectSummary:
        tid = _effective_tenant(request, x_tenant_id)
        return store.create_project(tid, payload.name, payload.description)

    @api.get("/v1/projects", response_model=list[ProjectSummary])
    def list_projects(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> list[ProjectSummary]:
        tid = _effective_tenant(request, x_tenant_id)
        return store.list_projects(tid)

    @api.get("/v1/projects/{project_id}", response_model=ProjectSummary)
    def get_project(
        request: Request,
        project_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ProjectSummary:
        tid = _effective_tenant(request, x_tenant_id)
        proj = store.get_project(tid, project_id)
        if proj is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return proj

    @api.delete("/v1/projects/{project_id}", response_model=ProjectDeletedResponse)
    def delete_project(
        request: Request,
        project_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> ProjectDeletedResponse:
        """Delete a tenant-owned project and all its revisions (404 if not owned by tenant)."""
        tid = _effective_tenant(request, x_tenant_id)
        if not store.delete_project(tid, project_id):
            raise HTTPException(status_code=404, detail="Project not found.")
        return ProjectDeletedResponse(deleted=True, project_id=project_id)

    @api.post(
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
        tid = _effective_tenant(request, x_tenant_id)
        proj = store.get_project(tid, project_id)
        if proj is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        if payload.building is None and payload.benchmark_template is None:
            raise HTTPException(
                status_code=422,
                detail="Provide either 'building' or 'benchmark_template'.",
            )

        t0 = time.perf_counter()
        eff_overrides = payload.overrides
        if payload.carry_forward_overrides:
            prev = store.get_latest_revision(tid, project_id)
            if prev is not None:
                eff_overrides = carry_forward_overrides(prev.overrides, eff_overrides)
        eff_overrides = _merge_tenant_defaults(tid, eff_overrides)

        building = (
            payload.building
            if payload.building is not None
            else generate_benchmark_dxf(payload.benchmark_template)  # type: ignore[arg-type]
        )
        p2 = run_phase2_pipeline(building, overrides=eff_overrides)
        rec = store.save_revision(tid, project_id, p2, overrides=eff_overrides, notes=payload.notes)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        store.record_usage(
            tid,
            "revision_solve",
            elapsed_ms,
            len(p2.model_dump_json().encode("utf-8")),
            project_id=project_id,
        )
        return rec

    @api.post(
        "/v1/projects/{project_id}/revisions/dxf",
        response_model=RevisionRecord,
        status_code=201,
    )
    async def create_project_revision_dxf(
        request: Request,
        project_id: str,
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf or .ifc")],
        overrides_json: Annotated[str | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        carry_forward: Annotated[bool, Form()] = True,
        notes: Annotated[str, Form()] = "",
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> RevisionRecord:
        """Upload a DXF/IFC revision, carry forward Pass-2 engineer overrides, and persist."""
        tid = _effective_tenant(request, x_tenant_id)
        if store.get_project(tid, project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".dxf", ".ifc"):
            raise HTTPException(status_code=400, detail="Only .dxf or .ifc uploads are supported.")

        t0 = time.perf_counter()
        data = await _read_upload_bounded(file)
        overrides = _parse_overrides_json(overrides_json)
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)

        if carry_forward:
            prev = store.get_latest_revision(tid, project_id)
            if prev is not None:
                overrides = carry_forward_overrides(prev.overrides, overrides)
        overrides = _merge_tenant_defaults(tid, overrides)

        try:
            p2 = await anyio.to_thread.run_sync(
                _solve_dxf_bytes_sync, data, suffix, overrides, building_type, layer_mapping
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        rec = store.save_revision(tid, project_id, p2, overrides=overrides, notes=notes)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        store.record_usage(
            tid,
            "dxf_revision_solve",
            elapsed_ms,
            len(data),
            project_id=project_id,
        )
        return rec

    @api.get("/v1/projects/{project_id}/revisions", response_model=list[RevisionSummary])
    def list_project_revisions(
        request: Request,
        project_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> list[RevisionSummary]:
        tid = _effective_tenant(request, x_tenant_id)
        if store.get_project(tid, project_id) is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return store.list_revisions(tid, project_id)

    @api.get(
        "/v1/projects/{project_id}/revisions/{revision_number}",
        response_model=RevisionRecord,
    )
    def get_project_revision(
        request: Request,
        project_id: str,
        revision_number: int,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> RevisionRecord:
        tid = _effective_tenant(request, x_tenant_id)
        rec = store.get_revision(tid, project_id, revision_number)
        if rec is None:
            raise HTTPException(status_code=404, detail="Revision not found.")
        return rec

    @api.get(
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
        tid = _effective_tenant(request, x_tenant_id)
        ra = store.get_revision(tid, project_id, rev_a)
        rb = store.get_revision(tid, project_id, rev_b)
        if ra is None or rb is None:
            raise HTTPException(status_code=404, detail="One or both revisions not found.")
        return compute_revision_diff(rev_a, ra.result, rev_b, rb.result)

    @api.get("/v1/tenant/usage", response_model=UsageSummary)
    def get_tenant_usage(
        request: Request,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> UsageSummary:
        tid = _effective_tenant(request, x_tenant_id)
        return store.get_usage_summary(tid)

    return api


app = create_app()
