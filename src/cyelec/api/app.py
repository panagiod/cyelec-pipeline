"""Stateless Multi-Tenant EU B2B SaaS FastAPI service with ETEK Review Workbench & hardening."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import tempfile
import time
import uuid
from collections import defaultdict, deque
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
from cyelec.ingest.dwg_bridge import is_dwg_converter_available
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
from cyelec.render.pdf_export import is_weasyprint_available
from cyelec.render.qet_bridge import is_qelectrotech_available
from cyelec.render.sld import render_sld_svg
from cyelec.saas.firestore_store import (
    FirestoreWorkspaceStore,
    build_workspace_store_from_env,
)
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
_MAX_MULTIFLOOR_FILES = 12
_MAX_MULTIFLOOR_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB aggregate cap across floor uploads
_MAX_RATE_BUCKETS = 4096
_CHUNK_BYTES = 256 * 1024

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
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
        outputs = export_phase3_deliverables(
            p2, tmp / "pack", export_pdf=is_weasyprint_available()
        )
        zip_path = build_submission_zip(outputs, tmp / "eac_submission_pack.zip")
        return p2, zip_path.read_bytes()


def _validate_safe_id(value: str, field_name: str) -> str:
    """Strictly validate tenant/project identifiers to prevent path or document traversal."""
    clean = value.strip()
    if not clean or ".." in clean or not _SAFE_ID_RE.match(clean):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid {field_name}: must be 1-64 characters of "
                "[A-Za-z0-9._-] without '..'."
            ),
        )
    return clean


def _safe_base_url(request: Request) -> str:
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


def _sanitize_client_error(exc: Exception) -> str:
    """Strip temporary filesystem paths before returning validation errors to HTTP callers."""
    if isinstance(exc, OSError):
        return "Failed to read or parse uploaded CAD file."
    msg = _TMP_PATH_RE.sub("<uploaded_file>", str(exc))
    return msg[:500]


def _security_headers(
    req_id: str,
    *,
    is_html: bool = False,
    is_https: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
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


_AUTH_CACHE_TTL_S = 60.0
_MAX_AUTH_CACHE = 512


def is_ifc_available() -> bool:
    """Return True when the optional LGPLv3 ifcopenshell BIM dependency is installed."""
    return importlib.util.find_spec("ifcopenshell") is not None


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
            safe_label = _SAFE_FLOOR_RE.sub("", label.upper())[:16] or f"F{idx}"
            # Never interpolate user-supplied labels into filesystem paths
            p = tmp / f"floor_{idx:02d}.dxf"
            p.write_bytes(data)
            floor_files.append((safe_label, p))
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


def _solve_multifloor_bundle_sync(
    floor_blobs: list[tuple[str, bytes]],
    submain_lengths_m: dict[str, float] | None,
    overrides: DesignOverrides | None,
    building_type: BuildingType | None,
    layer_mapping: DxfLayerMapping | None = None,
) -> tuple[Phase2DesignResult, bytes]:
    with tempfile.TemporaryDirectory(prefix="cyelec_mfb_") as tmp_dir:
        tmp = Path(tmp_dir)
        p2 = _solve_multifloor_dxf_sync(
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


def create_app(
    db_path: Path | None = None,
    *,
    store: WorkspaceStoreProtocol | None = None,
) -> FastAPI:
    """Create the Multi-Tenant EU B2B SaaS FastAPI application with strict tenant isolation."""
    active_store: WorkspaceStoreProtocol
    if store is not None:
        active_store = store
        store_backend = (
            "firestore"
            if isinstance(store, FirestoreWorkspaceStore)
            else ("sqlite" if isinstance(store, WorkspaceStore) else type(store).__name__)
        )
    elif db_path is not None:
        active_store = WorkspaceStore(db_path=db_path)
        store_backend = "sqlite"
    else:
        active_store, store_backend = build_workspace_store_from_env()
    store = active_store

    disable_openapi = os.environ.get("CYELEC_DISABLE_OPENAPI", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
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
    api.state.store = store
    api.state.store_backend = store_backend

    def _effective_tenant(request: Request, header_tenant: str) -> str:
        auth_tid = getattr(request.state, "auth_tenant_id", None)
        if isinstance(auth_tid, str) and auth_tid:
            return auth_tid
        return _validate_safe_id(header_tenant or "anonymous", "X-Tenant-ID")

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

    rate_buckets: dict[str, deque[float]] = defaultdict(deque)
    # Bounded 60s TTL cache mapping SHA-256(api_key) -> (tenant_id, expires_monotonic)
    auth_cache: dict[str, tuple[str, float]] = {}

    def _evict_auth_cache(tenant_id: str) -> None:
        stale_keys = [k for k, (tid, _) in auth_cache.items() if tid == tenant_id]
        for k in stale_keys:
            auth_cache.pop(k, None)

    async def _resolve_api_key_nonblocking(supplied_key: str) -> str | None:
        now_m = time.monotonic()
        key_sha = hashlib.sha256(supplied_key.encode("utf-8")).hexdigest()
        cached = auth_cache.get(key_sha)
        if cached is not None:
            tid, exp = cached
            if now_m < exp:
                return tid
            auth_cache.pop(key_sha, None)

        resolved = await anyio.to_thread.run_sync(store.resolve_tenant_by_api_key, supplied_key)
        if resolved is not None:
            if len(auth_cache) >= _MAX_AUTH_CACHE:
                expired = [k for k, (_, exp) in auth_cache.items() if now_m >= exp]
                for k in expired:
                    auth_cache.pop(k, None)
                if len(auth_cache) >= _MAX_AUTH_CACHE:
                    oldest_k = min(auth_cache, key=lambda k: auth_cache[k][1])
                    auth_cache.pop(oldest_k, None)
            auth_cache[key_sha] = (resolved, now_m + _AUTH_CACHE_TTL_S)
        return resolved

    @api.middleware("http")
    async def security_and_observability_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        raw_req_id = request.headers.get("X-Request-ID", "").strip()
        req_id = (
            raw_req_id
            if (_SAFE_ID_RE.fullmatch(raw_req_id) and len(raw_req_id) <= 64)
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

        supplied = request.headers.get("X-API-Key", "").strip()
        auth_tenant: str | None = None
        if supplied:
            auth_tenant = await _resolve_api_key_nonblocking(supplied)
            if auth_tenant is not None:
                request.state.auth_tenant_id = auth_tenant

        admin_key = os.environ.get("CYELEC_ADMIN_KEY", "").strip()
        supplied_admin = request.headers.get("X-Admin-Key", "").strip()
        admin_ok = bool(admin_key) and hmac.compare_digest(supplied_admin, admin_key)

        if path.startswith("/v1/admin/"):
            if not admin_key:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Admin provisioning disabled (CYELEC_ADMIN_KEY not set)."},
                    headers=_security_headers(req_id, is_https=is_https),
                )
            if not admin_ok:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing X-Admin-Key."},
                    headers=_security_headers(req_id, is_https=is_https),
                )
        elif path.startswith("/v1/"):
            expected_key = os.environ.get("CYELEC_API_KEY", "").strip()
            auth_required = os.environ.get("CYELEC_AUTH_REQUIRED", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            global_ok = bool(expected_key) and hmac.compare_digest(supplied, expected_key)

            if (expected_key or auth_required) and not (global_ok or auth_tenant is not None):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing X-API-Key."},
                    headers=_security_headers(req_id, is_https=is_https),
                )

            # In open public demo mode (staging), block unauthenticated key-issuing / erasure
            demo_lock = os.environ.get("CYELEC_DEMO_LOCK_TENANT_MUTATIONS", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if (
                demo_lock
                and not auth_required
                and (
                    (request.method == "POST" and path == "/v1/tenants/me/api-key")
                    or (request.method == "DELETE" and path == "/v1/tenants/me")
                )
                and not (auth_tenant is not None or global_ok or admin_ok)
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            "Tenant key issuance and erasure require an authenticated X-API-Key "
                            "or X-Admin-Key in public demo mode."
                        )
                    },
                    headers=_security_headers(req_id, is_https=is_https),
                )

        # Layer-1 guardrail: bounded sliding-window rate limit using trusted rightmost client IP
        rpm_raw = os.environ.get("CYELEC_RATE_LIMIT_RPM", "0").strip()
        rpm_limit = int(rpm_raw) if rpm_raw.isdigit() else 0
        rate_limited_route = (
            request.method == "POST"
            and (
                path.startswith("/v1/solve/")
                or path == "/v1/dxf/layers"
                or path.startswith("/v1/admin/")
                or path == "/v1/tenants/me/api-key"
                or "/revisions" in path
            )
        )
        if rpm_limit > 0 and rate_limited_route:
            xff_parts = [
                part.strip()
                for part in request.headers.get("X-Forwarded-For", "").split(",")
                if part.strip()
            ]
            # Cloud Run / Google Front End appends the real client IP on the right
            trusted_ip = xff_parts[-1] if xff_parts else (
                request.client.host if request.client else "unknown"
            )
            bucket_key = auth_tenant or trusted_ip
            now_mono = time.monotonic()
            if len(rate_buckets) >= _MAX_RATE_BUCKETS and bucket_key not in rate_buckets:
                stale = [k for k, dq in rate_buckets.items() if not dq or (now_mono - dq[-1]) > 60]
                for k in stale:
                    del rate_buckets[k]
                if len(rate_buckets) >= _MAX_RATE_BUCKETS:
                    oldest_key = min(rate_buckets, key=lambda k: rate_buckets[k][-1])
                    del rate_buckets[oldest_key]
            q = rate_buckets[bucket_key]
            while q and (now_mono - q[0]) > 60.0:
                q.popleft()
            if len(q) >= rpm_limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded ({rpm_limit} requests/min). Retry shortly."
                    },
                    headers=_security_headers(
                        req_id, is_https=is_https, extra={"Retry-After": "60"}
                    ),
                )
            q.append(now_mono)

        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        ctype = response.headers.get("content-type", "").lower()
        is_html = "text/html" in ctype
        sec_hdrs = _security_headers(
            req_id,
            is_html=is_html,
            is_https=is_https,
            extra={"X-Process-Time-Ms": f"{elapsed_ms:.1f}"},
        )
        for hk, hv in sec_hdrs.items():
            response.headers[hk] = hv
        return response

    @api.get("/", response_class=HTMLResponse)
    def workbench_ui(request: Request) -> HTMLResponse:
        return HTMLResponse(
            content=render_etek_workbench_html(
                base_url=_safe_base_url(request),
                show_api_docs=not disable_openapi,
                ifc_available=is_ifc_available(),
                dwg_available=is_dwg_converter_available(),
            )
        )

    @api.get("/guide", response_class=HTMLResponse)
    def user_guide_ui(request: Request) -> HTMLResponse:
        from cyelec.api.docs_ui import render_user_guide_html

        return HTMLResponse(
            content=render_user_guide_html(
                base_url=_safe_base_url(request),
                show_api_docs=not disable_openapi,
            )
        )

    @api.get("/favicon.ico", include_in_schema=False)
    def favicon_ico() -> Response:
        from cyelec.assets import get_asset_bytes, get_asset_media_type

        return Response(
            content=get_asset_bytes("favicon.ico"),
            media_type=get_asset_media_type("favicon.ico"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @api.get("/favicon.svg", include_in_schema=False)
    def favicon_svg() -> Response:
        from cyelec.assets import get_asset_bytes, get_asset_media_type

        return Response(
            content=get_asset_bytes("cyelec_logo.svg"),
            media_type=get_asset_media_type("cyelec_logo.svg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @api.get("/logo.png", include_in_schema=False)
    def brand_logo_png() -> Response:
        from cyelec.assets import get_asset_bytes, get_asset_media_type

        return Response(
            content=get_asset_bytes("cyelec_logo.png"),
            media_type=get_asset_media_type("cyelec_logo.png"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @api.get("/og-thumbnail.jpg", include_in_schema=False)
    def og_thumbnail_jpg() -> Response:
        from cyelec.assets import get_asset_bytes, get_asset_media_type

        return Response(
            content=get_asset_bytes("og_thumbnail.jpg"),
            media_type=get_asset_media_type("og_thumbnail.jpg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @api.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        std = load_standards()
        return HealthResponse(
            status="ok",
            pipeline_version=__version__,
            standards_versions=std.versions_map(),
            qelectrotech_available=is_qelectrotech_available(),
            ifc_available=is_ifc_available(),
            dwg_available=is_dwg_converter_available(),
            pdf_export_available=is_weasyprint_available(),
            store_backend=store_backend,
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
        safe_tid = _validate_safe_id(tenant_id, "tenant_id")
        raw_key = store.issue_tenant_api_key(safe_tid)
        _evict_auth_cache(safe_tid)
        return ApiKeyIssuedResponse(tenant_id=safe_tid, api_key=raw_key)

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
            outputs = export_phase3_deliverables(
                p2, tmp / "pack", export_pdf=is_weasyprint_available()
            )
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
            raise HTTPException(status_code=422, detail=_sanitize_client_error(exc)) from exc

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
        if suffix not in (".dxf", ".dwg", ".ifc"):
            raise HTTPException(
                status_code=400, detail="Only .dxf, .dwg, or .ifc uploads are supported."
            )

        t0 = time.perf_counter()
        data = await _read_upload_bounded(file)
        overrides = _merge_tenant_defaults(tid, _parse_overrides_json(overrides_json))
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)
        try:
            p2 = await anyio.to_thread.run_sync(
                _solve_dxf_bytes_sync, data, suffix, overrides, building_type, layer_mapping
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=_sanitize_client_error(exc)) from exc

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
        if suffix not in (".dxf", ".dwg", ".ifc"):
            raise HTTPException(
                status_code=400, detail="Only .dxf, .dwg, or .ifc uploads are supported."
            )

        t0 = time.perf_counter()
        data = await _read_upload_bounded(file)
        overrides = _merge_tenant_defaults(tid, _parse_overrides_json(overrides_json))
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)
        try:
            p2, zip_bytes = await anyio.to_thread.run_sync(
                _solve_dxf_bundle_sync, data, suffix, overrides, building_type, layer_mapping
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=_sanitize_client_error(exc)) from exc

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
        if len(files) > _MAX_MULTIFLOOR_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Multi-floor upload supports at most {_MAX_MULTIFLOOR_FILES} floor files.",
            )

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
            if total_bytes > _MAX_MULTIFLOOR_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Combined multi-floor upload exceeds maximum allowed size of "
                        f"{_MAX_MULTIFLOOR_TOTAL_BYTES // (1024 * 1024)} MB."
                    ),
                )
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
            raise HTTPException(status_code=422, detail=_sanitize_client_error(exc)) from exc

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

    @api.post("/v1/solve/dxf/multifloor/bundle")
    async def solve_dxf_multifloor_bundle(
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
    ) -> Response:
        """Combine multiple floor DXF files into a full MDB -> DB-<FLOOR> EAC ZIP pack."""
        tid = _effective_tenant(request, x_tenant_id)
        if not files:
            raise HTTPException(status_code=400, detail="At least one .dxf floor file is required.")
        if len(files) > _MAX_MULTIFLOOR_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Multi-floor upload supports at most {_MAX_MULTIFLOOR_FILES} floor files.",
            )

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
            if total_bytes > _MAX_MULTIFLOOR_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Combined multi-floor upload exceeds maximum allowed size of "
                        f"{_MAX_MULTIFLOOR_TOTAL_BYTES // (1024 * 1024)} MB."
                    ),
                )
            floor_blobs.append((label, blob))

        overrides = _merge_tenant_defaults(tid, _parse_overrides_json(overrides_json))
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)
        try:
            p2, zip_bytes = await anyio.to_thread.run_sync(
                _solve_multifloor_bundle_sync,
                floor_blobs,
                submain_lengths,
                overrides,
                building_type,
                layer_mapping,
            )
        except (ValueError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=_sanitize_client_error(exc)) from exc

        store.record_usage(
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
        _evict_auth_cache(tid)
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
        _evict_auth_cache(tid)
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
        safe_pid = _validate_safe_id(project_id, "project_id")
        proj = store.get_project(tid, safe_pid)
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
        safe_pid = _validate_safe_id(project_id, "project_id")
        if not store.delete_project(tid, safe_pid):
            raise HTTPException(status_code=404, detail="Project not found.")
        return ProjectDeletedResponse(deleted=True, project_id=safe_pid)

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
        safe_pid = _validate_safe_id(project_id, "project_id")
        proj = store.get_project(tid, safe_pid)
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
            prev = store.get_latest_revision(tid, safe_pid)
            if prev is not None:
                eff_overrides = carry_forward_overrides(prev.overrides, eff_overrides)
        eff_overrides = _merge_tenant_defaults(tid, eff_overrides)

        building = (
            payload.building
            if payload.building is not None
            else generate_benchmark_dxf(payload.benchmark_template)  # type: ignore[arg-type]
        )
        p2 = run_phase2_pipeline(building, overrides=eff_overrides)
        rec = store.save_revision(tid, safe_pid, p2, overrides=eff_overrides, notes=payload.notes)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        store.record_usage(
            tid,
            "revision_solve",
            elapsed_ms,
            len(p2.model_dump_json().encode("utf-8")),
            project_id=safe_pid,
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
        safe_pid = _validate_safe_id(project_id, "project_id")
        if store.get_project(tid, safe_pid) is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        filename = file.filename or "upload.dxf"
        suffix = Path(filename).suffix.lower()
        if suffix not in (".dxf", ".dwg", ".ifc"):
            raise HTTPException(
                status_code=400, detail="Only .dxf, .dwg, or .ifc uploads are supported."
            )

        t0 = time.perf_counter()
        data = await _read_upload_bounded(file)
        overrides = _parse_overrides_json(overrides_json)
        layer_mapping = _parse_layer_mapping_json(layer_mapping_json)

        if carry_forward:
            prev = store.get_latest_revision(tid, safe_pid)
            if prev is not None:
                overrides = carry_forward_overrides(prev.overrides, overrides)
        overrides = _merge_tenant_defaults(tid, overrides)

        try:
            p2 = await anyio.to_thread.run_sync(
                _solve_dxf_bytes_sync, data, suffix, overrides, building_type, layer_mapping
            )
        except (ValueError, ImportError, DXFError, OSError) as exc:
            raise HTTPException(status_code=422, detail=_sanitize_client_error(exc)) from exc

        rec = store.save_revision(tid, safe_pid, p2, overrides=overrides, notes=notes)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        store.record_usage(
            tid,
            "dxf_revision_solve",
            elapsed_ms,
            len(data),
            project_id=safe_pid,
        )
        return rec

    @api.get("/v1/projects/{project_id}/revisions", response_model=list[RevisionSummary])
    def list_project_revisions(
        request: Request,
        project_id: str,
        x_tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "anonymous",
    ) -> list[RevisionSummary]:
        tid = _effective_tenant(request, x_tenant_id)
        safe_pid = _validate_safe_id(project_id, "project_id")
        if store.get_project(tid, safe_pid) is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return store.list_revisions(tid, safe_pid)

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
        safe_pid = _validate_safe_id(project_id, "project_id")
        rec = store.get_revision(tid, safe_pid, revision_number)
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
        safe_pid = _validate_safe_id(project_id, "project_id")
        ra = store.get_revision(tid, safe_pid, rev_a)
        rb = store.get_revision(tid, safe_pid, rev_b)
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
