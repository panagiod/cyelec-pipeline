"""Bounded upload handling, sync CAD/BIM pipeline workers, preview rendering & HTTP sanitizers."""

from __future__ import annotations

import gc
import importlib.util
import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import anyio
from fastapi import HTTPException, Request, UploadFile
from pydantic import ValidationError

from cyelec.api.schemas import SolvePreviewEnvelope
from cyelec.ingest import dwg_bridge
from cyelec.ingest.dxf_ingest import ingest_multi_floor_dxf, inspect_dxf_layers
from cyelec.models.building import (
    BenchmarkTemplate,
    BuildingType,
    DxfLayerInfo,
    DxfLayerMapping,
)
from cyelec.models.electrical import DesignOverrides
from cyelec.models.spatial import Phase2DesignResult
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.board_designs import render_board_designs_svg
from cyelec.render.details_dxf import render_details_notes_svg
from cyelec.render.eac_pack import build_submission_zip
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.render.pdf_export import is_weasyprint_available
from cyelec.render.riser_dxf import render_riser_and_db_svg
from cyelec.render.selectivity_svg import render_selectivity_curves_svg
from cyelec.render.site_plan_dxf import render_site_plan_svg
from cyelec.render.sld import render_sld_svg
from cyelec.synth.benchmarks import generate_benchmark_dxf

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB hard cap per DXF/DWG/IFC upload
MAX_MULTIFLOOR_FILES = 12
MAX_MULTIFLOOR_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB aggregate cap across floor uploads
_CHUNK_BYTES = 256 * 1024

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:]{1,253}$")
_SAFE_FLOOR_RE = re.compile(r"[^A-Z0-9_-]")
_TMP_PATH_RE = re.compile(r"(?:/tmp/|/var/folders/|C:\\\\?Users\\\\?)[^\s'\"]+")

_HTML_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"

SUPPORTED_CAD_SUFFIXES = (".dxf", ".dwg", ".ifc", ".pdf")


def env_flag(name: str, *, default: bool = False) -> bool:
    """Parse boolean environment flags dynamically (supports runtime monkeypatching in tests)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    clean = raw.strip().lower()
    if default:
        return clean not in ("0", "false", "no")
    return clean in ("1", "true", "yes")


def is_auth_required_env() -> bool:
    """Return True when global API key or strict tenant auth is active."""
    return env_flag("CYELEC_AUTH_REQUIRED") or bool(os.environ.get("CYELEC_API_KEY", "").strip())


def is_ifc_available() -> bool:
    """Return True when the optional LGPLv3 ifcopenshell BIM dependency is installed."""
    return importlib.util.find_spec("ifcopenshell") is not None


def _is_cad_client_error(exc: Exception) -> bool:
    """Return True if an exception represents an invalid uploaded CAD/BIM file error."""
    return isinstance(exc, (ValueError, ImportError, OSError)) or type(exc).__name__ == "DXFError"


def sanitize_client_error(exc: Exception) -> str:
    """Strip temporary filesystem paths before returning validation errors to HTTP callers."""
    if isinstance(exc, OSError):
        return "Failed to read or parse uploaded CAD file."
    msg = _TMP_PATH_RE.sub("<uploaded_file>", str(exc))
    return msg[:500]


async def run_cad_sync[T](fn: Callable[..., T], *args: object) -> T:
    """Run a blocking CAD/BIM worker in a thread and map malformed-input errors to HTTP 422."""
    try:
        return await anyio.to_thread.run_sync(fn, *args)
    except Exception as exc:
        if not _is_cad_client_error(exc):
            raise
        raise HTTPException(status_code=422, detail=sanitize_client_error(exc)) from exc


def validate_safe_id(value: str, field_name: str) -> str:
    """Strictly validate tenant/project identifiers to prevent path or document traversal."""
    clean = value.strip()
    if not clean or ".." in clean or not SAFE_ID_RE.match(clean):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid {field_name}: must be 1-64 characters of [A-Za-z0-9._-] without '..'."
            ),
        )
    return clean


def safe_base_url(request: Request) -> str:
    """Build a strictly validated origin URL for OG metadata without host-header injection."""
    raw_proto = (
        request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip().lower()
    )
    proto = raw_proto if raw_proto in ("http", "https") else "https"
    raw_host = (
        request.headers.get("x-forwarded-host", request.headers.get("host", ""))
        .split(",")[0]
        .strip()
    )
    if not raw_host or not _SAFE_HOST_RE.match(raw_host):
        return ""
    return f"{proto}://{raw_host}"


def slugify_tenant(name: str) -> str:
    """Generate a URL-safe tenant slug with a random 6-hex suffix."""
    raw = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:32]
    base = raw or "firm"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def safe_redirect_url(candidate: str, fallback: str) -> str:
    """Validate checkout/portal redirect URLs to allow only https:// or http://localhost."""
    clean = candidate.strip()
    if not clean:
        return fallback
    if clean.startswith(("https://", "http://localhost")):
        return clean
    raise HTTPException(
        status_code=422, detail="Redirect URL must use https:// (or http://localhost)."
    )


def security_headers(
    req_id: str,
    *,
    is_html: bool = False,
    is_https: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assemble strict security and observability HTTP headers."""
    headers: dict[str, str] = {
        "X-Request-ID": req_id,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": _PERMISSIONS_POLICY,
        "Content-Security-Policy": _HTML_CSP if is_html else _API_CSP,
    }
    if is_https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if extra:
        headers.update(extra)
    return headers


def parse_overrides_json(raw: str | None) -> DesignOverrides | None:
    """Parse optional JSON-encoded DesignOverrides form field."""
    if raw is None or not raw.strip():
        return None
    try:
        return DesignOverrides.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid DesignOverrides JSON: {exc}") from exc


def parse_layer_mapping_json(raw: str | None) -> DxfLayerMapping | None:
    """Parse optional JSON-encoded DxfLayerMapping form field."""
    if raw is None or not raw.strip():
        return None
    try:
        return DxfLayerMapping.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid DxfLayerMapping JSON: {exc}") from exc


async def read_upload_bounded(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
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


async def parse_multifloor_uploads(
    files: list[UploadFile],
    floors_json: str | None,
    submain_lengths_json: str | None,
    *,
    empty_detail: str = "At least one .dxf floor file is required.",
) -> tuple[list[tuple[str, bytes, str]], dict[str, float] | None, int]:
    """Validate and stream-read multi-floor DXF/DWG uploads with labels and aggregate RAM cap."""
    if not files:
        raise HTTPException(status_code=400, detail=empty_detail)
    if len(files) > MAX_MULTIFLOOR_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Multi-floor upload supports at most {MAX_MULTIFLOOR_FILES} floor files.",
        )

    default_labels = ["GF"] + [f"{i}F" for i in range(1, len(files))]
    labels: list[str] = default_labels[: len(files)]
    if floors_json and floors_json.strip():
        try:
            parsed_labels = json.loads(floors_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid floors_json: {exc}") from exc
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

    floor_blobs: list[tuple[str, bytes, str]] = []
    total_bytes = 0
    for label, uf in zip(labels, files, strict=True):
        fname = uf.filename or f"{label}.dxf"
        sfx = Path(fname).suffix.lower()
        if sfx not in (".dxf", ".dwg"):
            raise HTTPException(
                status_code=400,
                detail=f"Multi-floor combiner supports .dxf or .dwg uploads (got '{fname}').",
            )
        blob = await read_upload_bounded(uf)
        total_bytes += len(blob)
        if total_bytes > MAX_MULTIFLOOR_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Combined multi-floor upload exceeds maximum allowed size of "
                    f"{MAX_MULTIFLOOR_TOTAL_BYTES // (1024 * 1024)} MB."
                ),
            )
        floor_blobs.append((label, blob, sfx))
    return floor_blobs, submain_lengths, total_bytes


def render_preview_envelope_sync(tenant_id: str, p2: Phase2DesignResult) -> SolvePreviewEnvelope:
    """Render all 11 vector preview SVGs (E-100..E-301) for a solved Phase-2 design."""
    return SolvePreviewEnvelope(
        tenant_id=tenant_id,
        result=p2,
        floorplan_svg=render_floorplan_svg(p2, discipline="combined"),
        sld_svg=render_sld_svg(p2.design),
        lighting_svg=render_floorplan_svg(p2, discipline="lighting"),
        lighting_heatmap_svg=render_floorplan_svg(
            p2, discipline="lighting", show_lux_heatmap=True
        ),
        power_svg=render_floorplan_svg(p2, discipline="power"),
        elv_svg=render_floorplan_svg(p2, discipline="elv"),
        site_svg=render_site_plan_svg(p2),
        riser_svg=render_riser_and_db_svg(p2),
        selectivity_svg=render_selectivity_curves_svg(p2.design),
        boards_svg=render_board_designs_svg(p2),
        details_svg=render_details_notes_svg(p2),
    )


async def render_preview_envelope_async(
    tenant_id: str, p2: Phase2DesignResult
) -> SolvePreviewEnvelope:
    """Offload preview SVG generation to a worker thread to keep the event loop responsive."""
    return await anyio.to_thread.run_sync(render_preview_envelope_sync, tenant_id, p2)


def _resolve_bundle_concurrency_limit() -> int:
    raw = os.environ.get("CYELEC_MAX_CONCURRENT_BUNDLES", "2").strip()
    try:
        return max(1, min(8, int(raw)))
    except ValueError:
        return 2


# Bound concurrent heavy Phase 3 + WeasyPrint PDF + ZIP exports to match 2 vCPU / 2 GiB
# Cloud Run containers without OOM or GIL/Cairo thrashing under parallel downloads.
_BUNDLE_SEMAPHORE = threading.BoundedSemaphore(_resolve_bundle_concurrency_limit())


def inspect_dxf_layers_sync(data: bytes, suffix: str = ".dxf") -> list[DxfLayerInfo]:
    """Inspect architectural layers inside raw DXF or DWG bytes."""
    with tempfile.TemporaryDirectory(prefix="cyelec_layers_") as tmp_dir:
        tmp = Path(tmp_dir)
        in_path = tmp / f"plan{suffix}"
        in_path.write_bytes(data)
        if suffix == ".dwg":
            in_path, _ = dwg_bridge.convert_dwg_to_dxf(in_path, tmp / "dwg_conv")
        return inspect_dxf_layers(in_path)


def solve_dxf_bytes_sync(
    data: bytes,
    suffix: str,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> Phase2DesignResult:
    """Run Phase 0-2 deterministic pipeline on an uploaded CAD/BIM/PDF byte buffer."""
    with tempfile.TemporaryDirectory(prefix="cyelec_req_") as tmp_dir:
        in_path = Path(tmp_dir) / f"plan{suffix}"
        in_path.write_bytes(data)
        return run_phase2_pipeline(
            in_path,
            overrides=overrides,
            building_type=building_type,
            layer_mapping=layer_mapping,
        )


def solve_benchmark_bundle_sync(
    template: BenchmarkTemplate,
    overrides: DesignOverrides | None,
) -> tuple[Phase2DesignResult, bytes]:
    """Generate a benchmark DXF, solve Phase 0-3, and return the ZIP submission pack."""
    with _BUNDLE_SEMAPHORE:
        try:
            with tempfile.TemporaryDirectory(prefix="cyelec_bench_") as tmp_dir:
                tmp = Path(tmp_dir)
                building = generate_benchmark_dxf(template, out_dxf=tmp / f"{template.value}.dxf")
                p2 = run_phase2_pipeline(building, overrides=overrides)
                outputs = export_phase3_deliverables(
                    p2, tmp / "pack", export_pdf=is_weasyprint_available()
                )
                zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
                return p2, zip_path.read_bytes()
        finally:
            gc.collect()


def solve_dxf_bundle_sync(
    data: bytes,
    suffix: str,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> tuple[Phase2DesignResult, bytes]:
    """Solve an uploaded CAD/BIM/PDF file through Phase 3 and build the EAC ZIP bundle."""
    with _BUNDLE_SEMAPHORE:
        try:
            with tempfile.TemporaryDirectory(prefix="cyelec_bundle_") as tmp_dir:
                tmp = Path(tmp_dir)
                in_path = tmp / f"plan{suffix}"
                in_path.write_bytes(data)
                extra_flags = []
                source_dxf: Path | None = None
                if suffix == ".dwg":
                    conv_dxf, dwg_flag = dwg_bridge.convert_dwg_to_dxf(in_path, tmp / "dwg_conv")
                    extra_flags.append(dwg_flag)
                    solve_path = conv_dxf
                    source_dxf = conv_dxf
                else:
                    solve_path = in_path
                    source_dxf = in_path if suffix == ".dxf" else None
                p2 = run_phase2_pipeline(
                    solve_path,
                    overrides=overrides,
                    building_type=building_type,
                    layer_mapping=layer_mapping,
                    extra_flags=extra_flags,
                )
                outputs = export_phase3_deliverables(
                    p2,
                    tmp / "pack",
                    export_pdf=is_weasyprint_available(),
                    source_dxf_path=source_dxf,
                )
                zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
                return p2, zip_path.read_bytes()
        finally:
            gc.collect()


def _run_multifloor_in_dir(
    tmp: Path,
    floor_blobs: list[tuple[str, bytes, str]],
    submain_lengths_m: dict[str, float] | None,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> Phase2DesignResult:
    floor_files: list[tuple[str, Path]] = []
    dwg_flags = []
    for idx, (label, data, suffix) in enumerate(floor_blobs):
        safe_label = _SAFE_FLOOR_RE.sub("", label.upper())[:16] or f"F{idx}"
        # Never interpolate user-supplied labels into filesystem paths
        raw_p = tmp / f"floor_{idx:02d}{suffix}"
        raw_p.write_bytes(data)
        if suffix == ".dwg":
            conv_p, dwg_flag = dwg_bridge.convert_dwg_to_dxf(raw_p, tmp / f"dwg_{idx:02d}")
            dwg_flags.append(dwg_flag)
            floor_files.append((safe_label, conv_p))
        else:
            floor_files.append((safe_label, raw_p))
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
    return run_phase2_pipeline(model, overrides=overrides, extra_flags=[*dwg_flags, *mf_flags])


def solve_multifloor_dxf_sync(
    floor_blobs: list[tuple[str, bytes, str]],
    submain_lengths_m: dict[str, float] | None,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> Phase2DesignResult:
    """Combine multiple floor DXF/DWG files and return a solved Phase-2 multi-floor design."""
    with tempfile.TemporaryDirectory(prefix="cyelec_mf_") as tmp_dir:
        return _run_multifloor_in_dir(
            Path(tmp_dir),
            floor_blobs,
            submain_lengths_m,
            overrides,
            building_type,
            layer_mapping,
        )


def solve_multifloor_bundle_sync(
    floor_blobs: list[tuple[str, bytes, str]],
    submain_lengths_m: dict[str, float] | None,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> tuple[Phase2DesignResult, bytes]:
    """Combine multiple floor DXF/DWG files and build the full Phase-3 EAC ZIP bundle."""
    # Keep floor DXF files alive inside the SAME temp dir so layout_dxf preserves real underlays!
    with _BUNDLE_SEMAPHORE:
        try:
            with tempfile.TemporaryDirectory(prefix="cyelec_mfb_") as tmp_dir:
                tmp = Path(tmp_dir)
                p2 = _run_multifloor_in_dir(
                    tmp,
                    floor_blobs,
                    submain_lengths_m,
                    overrides,
                    building_type,
                    layer_mapping,
                )
                outputs = export_phase3_deliverables(
                    p2, tmp / "pack", export_pdf=is_weasyprint_available()
                )
                zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
                return p2, zip_path.read_bytes()
        finally:
            gc.collect()
