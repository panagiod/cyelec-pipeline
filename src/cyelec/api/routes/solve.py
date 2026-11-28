"""Deterministic engineering solve, CAD/BIM upload, preview & EAC ZIP bundle routes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)

from cyelec.api.context import AppContext
from cyelec.api.schemas import (
    DxfLayersResponse,
    SolveBuildingRequest,
    SolvePreviewEnvelope,
    SolveResponseEnvelope,
)
from cyelec.api.workers import (
    SUPPORTED_CAD_SUFFIXES,
    inspect_dxf_layers_sync,
    parse_layer_mapping_json,
    parse_multifloor_uploads,
    parse_overrides_json,
    read_upload_bounded,
    render_preview_envelope_async,
    render_preview_envelope_sync,
    run_cad_sync,
    solve_benchmark_bundle_sync,
    solve_dxf_bundle_sync,
    solve_dxf_bytes_sync,
    solve_multifloor_bundle_sync,
    solve_multifloor_dxf_sync,
)
from cyelec.models.building import BenchmarkTemplate, BuildingType
from cyelec.models.electrical import DesignOverrides
from cyelec.pipeline import run_phase2_pipeline
from cyelec.synth.benchmarks import generate_benchmark_dxf


def build_solve_router(ctx: AppContext) -> APIRouter:
    """Register all single-floor, benchmark, and multi-floor solve/preview/bundle routes."""
    router = APIRouter()

    @router.post("/v1/solve/building", response_model=SolveResponseEnvelope)
    def solve_building(
        request: Request,
        payload: SolveBuildingRequest,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolveResponseEnvelope:
        tid = ctx.effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        eff_overrides = ctx.merge_tenant_defaults(tid, payload.overrides)
        p2 = run_phase2_pipeline(payload.building, overrides=eff_overrides)
        ctx.store.record_usage(
            tid,
            "solve_building",
            (time.perf_counter() - t0) * 1000.0,
            len(payload.model_dump_json().encode("utf-8")),
        )
        return SolveResponseEnvelope(tenant_id=tid, result=p2)

    @router.post("/v1/solve/benchmark/{template}", response_model=SolvePreviewEnvelope)
    def solve_benchmark_preview(
        request: Request,
        template: BenchmarkTemplate,
        overrides: Annotated[DesignOverrides | None, Body()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolvePreviewEnvelope:
        tid = ctx.effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        eff_overrides = ctx.merge_tenant_defaults(tid, overrides)
        building = generate_benchmark_dxf(template)
        p2 = run_phase2_pipeline(building, overrides=eff_overrides)
        ctx.store.record_usage(
            tid,
            "solve_benchmark",
            (time.perf_counter() - t0) * 1000.0,
            len(p2.model_dump_json().encode("utf-8")),
        )
        return render_preview_envelope_sync(tid, p2)

    @router.post("/v1/solve/benchmark/{template}/bundle")
    def solve_benchmark_bundle(
        request: Request,
        template: BenchmarkTemplate,
        overrides: Annotated[DesignOverrides | None, Body()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> Response:
        tid = ctx.effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        eff_overrides = ctx.merge_tenant_defaults(tid, overrides)
        p2, zip_bytes = solve_benchmark_bundle_sync(template, eff_overrides)
        ctx.store.record_usage(
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

    @router.post("/v1/dxf/layers", response_model=DxfLayersResponse)
    async def inspect_dxf_layers_upload(
        request: Request,
        file: Annotated[UploadFile, File(description="Architectural .dxf or .dwg floorplan")],
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> DxfLayersResponse:
        """Inspect layers, entity counts, and inferred roles of an uploaded DXF or DWG."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".dxf", ".dwg"):
            raise HTTPException(
                status_code=400, detail="Only .dxf or .dwg files support layer inspection."
            )

        t0 = time.perf_counter()
        data = await read_upload_bounded(file)
        layers = await run_cad_sync(inspect_dxf_layers_sync, data, suffix)
        ctx.store.record_usage(
            tid, "dxf_layer_inspect", (time.perf_counter() - t0) * 1000.0, len(data)
        )
        return DxfLayersResponse(tenant_id=tid, filename=filename, layers=layers)

    @router.post("/v1/solve/dxf", response_model=SolveResponseEnvelope)
    async def solve_dxf_upload(
        request: Request,
        file: Annotated[
            UploadFile, File(description="Architectural .dxf, .dwg, .ifc, or vector .pdf plan")
        ],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> SolveResponseEnvelope:
        tid = ctx.effective_tenant(request, x_tenant_id)
        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_CAD_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="Only .dxf, .dwg, .ifc, or vector .pdf uploads are supported.",
            )

        t0 = time.perf_counter()
        data = await read_upload_bounded(file)
        overrides = ctx.merge_tenant_defaults(tid, parse_overrides_json(overrides_json))
        layer_mapping = parse_layer_mapping_json(layer_mapping_json)
        p2 = await run_cad_sync(
            solve_dxf_bytes_sync, data, suffix, overrides, building_type, layer_mapping
        )
        ctx.store.record_usage(tid, "solve_dxf", (time.perf_counter() - t0) * 1000.0, len(data))
        return SolveResponseEnvelope(tenant_id=tid, result=p2)

    @router.post("/v1/solve/dxf/preview", response_model=SolvePreviewEnvelope)
    async def solve_dxf_preview(
        request: Request,
        file: Annotated[
            UploadFile, File(description="Architectural .dxf, .dwg, .ifc, or vector .pdf plan")
        ],
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
        return await render_preview_envelope_async(envelope.tenant_id, envelope.result)

    @router.post("/v1/solve/dxf/bundle")
    async def solve_dxf_bundle(
        request: Request,
        file: Annotated[
            UploadFile, File(description="Architectural .dxf, .dwg, .ifc, or vector .pdf plan")
        ],
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> Response:
        """Run full Phase 0-3 pipeline and return a ZIP archive of the EAC submission pack."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_CAD_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="Only .dxf, .dwg, .ifc, or vector .pdf uploads are supported.",
            )

        t0 = time.perf_counter()
        data = await read_upload_bounded(file)
        overrides = ctx.merge_tenant_defaults(tid, parse_overrides_json(overrides_json))
        layer_mapping = parse_layer_mapping_json(layer_mapping_json)
        p2, zip_bytes = await run_cad_sync(
            solve_dxf_bundle_sync, data, suffix, overrides, building_type, layer_mapping
        )
        ctx.store.record_usage(
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

    @router.post("/v1/solve/dxf/multifloor", response_model=SolvePreviewEnvelope)
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
        tid = ctx.effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        floor_blobs, submain_lengths, total_bytes = await parse_multifloor_uploads(
            files,
            floors_json,
            submain_lengths_json,
            empty_detail="At least one .dxf floor file is required.",
        )
        overrides = ctx.merge_tenant_defaults(tid, parse_overrides_json(overrides_json))
        layer_mapping = parse_layer_mapping_json(layer_mapping_json)
        p2 = await run_cad_sync(
            solve_multifloor_dxf_sync,
            floor_blobs,
            submain_lengths,
            overrides,
            building_type,
            layer_mapping,
        )
        envelope = await render_preview_envelope_async(tid, p2)
        ctx.store.record_usage(
            tid, "solve_dxf_multifloor", (time.perf_counter() - t0) * 1000.0, total_bytes
        )
        return envelope

    @router.post("/v1/solve/dxf/multifloor/bundle")
    async def solve_dxf_multifloor_bundle(
        request: Request,
        files: Annotated[
            list[UploadFile],
            File(description="Two or more architectural .dxf or .dwg floorplans"),
        ],
        floors_json: Annotated[str | None, Form()] = None,
        submain_lengths_json: Annotated[str | None, Form()] = None,
        overrides_json: Annotated[str | None, Form()] = None,
        building_type: Annotated[BuildingType | None, Form()] = None,
        layer_mapping_json: Annotated[str | None, Form()] = None,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> Response:
        """Combine multiple floor DXF/DWG files into a full MDB -> DB-<FLOOR> EAC ZIP pack."""
        tid = ctx.effective_tenant(request, x_tenant_id)
        t0 = time.perf_counter()
        floor_blobs, submain_lengths, _ = await parse_multifloor_uploads(
            files,
            floors_json,
            submain_lengths_json,
            empty_detail="At least one .dxf or .dwg floor file is required.",
        )
        overrides = ctx.merge_tenant_defaults(tid, parse_overrides_json(overrides_json))
        layer_mapping = parse_layer_mapping_json(layer_mapping_json)
        p2, zip_bytes = await run_cad_sync(
            solve_multifloor_bundle_sync,
            floor_blobs,
            submain_lengths,
            overrides,
            building_type,
            layer_mapping,
        )
        ctx.store.record_usage(
            tid, "solve_dxf_multifloor_bundle", (time.perf_counter() - t0) * 1000.0, len(zip_bytes)
        )
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="eac_submission_pack_multifloor.zip"',
                "X-Tenant-ID": tid,
                "X-Input-SHA256": p2.design.provenance.input_sha256,
            },
        )

    return router
