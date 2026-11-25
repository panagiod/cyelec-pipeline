"""Stateless Multi-Tenant EU B2B SaaS FastAPI service with ETEK Review Workbench & hardening."""

from __future__ import annotations

import hmac
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
from cyelec.models.building import BuildingModel, BuildingType
from cyelec.models.electrical import DesignOverrides
from cyelec.models.spatial import Phase2DesignResult
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.eac_pack import build_submission_zip
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.render.qet_bridge import is_qelectrotech_available
from cyelec.render.sld import render_sld_svg
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


def _parse_overrides_json(raw: str | None) -> DesignOverrides | None:
    if raw is None or not raw.strip():
        return None
    try:
        return DesignOverrides.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid DesignOverrides JSON: {exc}"
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


def _solve_dxf_bytes_sync(
    data: bytes,
    suffix: str,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
) -> Phase2DesignResult:
    with tempfile.TemporaryDirectory(prefix="cyelec_req_") as tmp_dir:
        in_path = Path(tmp_dir) / f"plan{suffix}"
        in_path.write_bytes(data)
        return run_phase2_pipeline(
            in_path,
            overrides=overrides,
            building_type=building_type,
        )


def _solve_dxf_bundle_sync(
    data: bytes,
    suffix: str,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
) -> tuple[Phase2DesignResult, bytes]:
    with tempfile.TemporaryDirectory(prefix="cyelec_bundle_") as tmp_dir:
        tmp = Path(tmp_dir)
        in_path = tmp / f"plan{suffix}"
        in_path.write_bytes(data)
        p2 = run_phase2_pipeline(
            in_path,
            overrides=overrides,
            building_type=building_type,
        )
        outputs = export_phase3_deliverables(p2, tmp / "pack")
        zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
        return p2, zip_path.read_bytes()


def create_app() -> FastAPI:
    """Create a strictly stateless Multi-Tenant EU B2B SaaS FastAPI application."""
    api = FastAPI(
        title="cyelec-pipeline SaaS API",
        version=__version__,
        description=(
            "Stateless deterministic BS 7671 / IEC 60364 / Cyprus EAC electrical engineering "
            "service. All runs are isolated in ephemeral storage with zero cross-tenant state."
        ),
    )

    @api.middleware("http")
    async def security_and_observability_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        t0 = time.perf_counter()

        # Optional API-key enforcement when CYELEC_API_KEY is configured in production
        expected_key = os.environ.get("CYELEC_API_KEY", "").strip()
        if expected_key and request.url.path.startswith("/v1/"):
            supplied = request.headers.get("X-API-Key", "")
            if not hmac.compare_digest(supplied, expected_key):
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

    @api.post("/v1/solve/building", response_model=SolveResponseEnvelope)
    def solve_building(
        payload: SolveBuildingRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolveResponseEnvelope:
        p2 = run_phase2_pipeline(payload.building, overrides=payload.overrides)
        return SolveResponseEnvelope(tenant_id=x_tenant_id, result=p2)

    @api.post("/v1/solve/benchmark/{template}", response_model=SolvePreviewEnvelope)
    def solve_benchmark_preview(
        template: BenchmarkTemplate,
        overrides: Annotated[DesignOverrides | None, Body()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolvePreviewEnvelope:
        building = generate_benchmark_dxf(template)
        p2 = run_phase2_pipeline(building, overrides=overrides)
        return SolvePreviewEnvelope(
            tenant_id=x_tenant_id,
            result=p2,
            floorplan_svg=render_floorplan_svg(p2),
            sld_svg=render_sld_svg(p2.design),
        )

    @api.post("/v1/solve/benchmark/{template}/bundle")
    def solve_benchmark_bundle(
        template: BenchmarkTemplate,
        overrides: Annotated[DesignOverrides | None, Body()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> Response:
        with tempfile.TemporaryDirectory(prefix="cyelec_bench_") as tmp_dir:
            tmp = Path(tmp_dir)
            building = generate_benchmark_dxf(template, out_dxf=tmp / f"{template.value}.dxf")
            p2 = run_phase2_pipeline(building, overrides=overrides)
            outputs = export_phase3_deliverables(p2, tmp / "pack")
            zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
            zip_bytes = zip_path.read_bytes()
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="eac_pack_{template.value}.zip"',
                "X-Tenant-ID": x_tenant_id,
                "X-Input-SHA256": p2.design.provenance.input_sha256,
            },
        )

    @api.post("/v1/solve/dxf", response_model=SolveResponseEnvelope)
    async def solve_dxf_upload(
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf floorplan")],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolveResponseEnvelope:
        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".dxf", ".ifc"):
            raise HTTPException(status_code=400, detail="Only .dxf or .ifc uploads are supported.")

        data = await _read_upload_bounded(file)
        overrides = _parse_overrides_json(overrides_json)
        try:
            p2 = await anyio.to_thread.run_sync(
                _solve_dxf_bytes_sync, data, suffix, overrides, building_type
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return SolveResponseEnvelope(tenant_id=x_tenant_id, result=p2)

    @api.post("/v1/solve/dxf/preview", response_model=SolvePreviewEnvelope)
    async def solve_dxf_preview(
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf floorplan")],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolvePreviewEnvelope:
        envelope = await solve_dxf_upload(
            file=file,
            overrides_json=overrides_json,
            building_type=building_type,
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
        file: Annotated[UploadFile, File(description="Architectural R12-R2018 .dxf floorplan")],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> Response:
        """Run full Phase 0-3 pipeline and return a ZIP archive of the EAC submission pack."""
        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".dxf", ".ifc"):
            raise HTTPException(status_code=400, detail="Only .dxf or .ifc uploads are supported.")

        data = await _read_upload_bounded(file)
        overrides = _parse_overrides_json(overrides_json)
        try:
            p2, zip_bytes = await anyio.to_thread.run_sync(
                _solve_dxf_bundle_sync, data, suffix, overrides, building_type
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="eac_submission_pack.zip"',
                "X-Tenant-ID": x_tenant_id,
                "X-Input-SHA256": p2.design.provenance.input_sha256,
            },
        )

    return api


app = create_app()
