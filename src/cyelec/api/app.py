"""Stateless Multi-Tenant EU B2B SaaS FastAPI service for cyelec-pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel, ConfigDict, ValidationError

from cyelec import __version__
from cyelec.models.building import BuildingModel, BuildingType
from cyelec.models.electrical import DesignOverrides
from cyelec.models.spatial import Phase2DesignResult
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.eac_pack import build_submission_zip
from cyelec.render.qet_bridge import is_qelectrotech_available
from cyelec.standards_loader import load_standards

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB safety cap per DXF/IFC upload


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


def _parse_overrides_json(raw: str | None) -> DesignOverrides | None:
    if raw is None or not raw.strip():
        return None
    try:
        return DesignOverrides.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid DesignOverrides JSON: {exc}") from exc


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

        data = await file.read()
        if not data or len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Upload empty or exceeds 25 MB limit.")

        overrides = _parse_overrides_json(overrides_json)
        with tempfile.TemporaryDirectory(prefix="cyelec_req_") as tmp_dir:
            in_path = Path(tmp_dir) / f"plan{suffix}"
            in_path.write_bytes(data)
            try:
                p2 = run_phase2_pipeline(
                    in_path,
                    overrides=overrides,
                    building_type=building_type,
                )
            except (ValueError, ImportError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        return SolveResponseEnvelope(tenant_id=x_tenant_id, result=p2)

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

        data = await file.read()
        if not data or len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Upload empty or exceeds 25 MB limit.")

        overrides = _parse_overrides_json(overrides_json)
        with tempfile.TemporaryDirectory(prefix="cyelec_bundle_") as tmp_dir:
            tmp = Path(tmp_dir)
            in_path = tmp / f"plan{suffix}"
            in_path.write_bytes(data)
            out_dir = tmp / "pack"
            try:
                p2 = run_phase2_pipeline(
                    in_path,
                    overrides=overrides,
                    building_type=building_type,
                )
                outputs = export_phase3_deliverables(p2, out_dir)
                zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
                zip_bytes = zip_path.read_bytes()
            except (ValueError, ImportError) as exc:
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
