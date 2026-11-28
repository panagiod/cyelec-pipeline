"""Workbench UI, User Guide, packaged brand assets, /health & /v1/standards routes."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from cyelec import __version__
from cyelec.api.context import AppContext
from cyelec.api.docs_ui import render_user_guide_html
from cyelec.api.schemas import HealthResponse
from cyelec.api.ui import render_etek_workbench_html
from cyelec.api.ui_i18n import render_user_guide_el_html
from cyelec.api.workers import is_auth_required_env, is_ifc_available, safe_base_url
from cyelec.assets import get_asset_bytes, get_asset_media_type
from cyelec.ingest.dwg_bridge import is_dwg_converter_available
from cyelec.render.pdf_export import is_weasyprint_available
from cyelec.render.qet_bridge import is_qelectrotech_available
from cyelec.saas.billing import is_stripe_configured
from cyelec.standards_loader import load_standards


def _asset_response(filename: str) -> Response:
    return Response(
        content=get_asset_bytes(filename),
        media_type=get_asset_media_type(filename),
        headers={"Cache-Control": "public, max-age=86400"},
    )


def build_ui_assets_router(ctx: AppContext) -> APIRouter:
    """Register Workbench HTML, User Guide HTML, static brand assets, /health & /v1/standards."""
    router = APIRouter()

    def _render_workbench(request: Request, ui_lang: Literal["en", "el"]) -> HTMLResponse:
        invite_req = bool(os.environ.get("CYELEC_PILOT_INVITE_CODE", "").strip())
        return HTMLResponse(
            content=render_etek_workbench_html(
                base_url=safe_base_url(request),
                show_api_docs=not ctx.disable_openapi,
                ifc_available=is_ifc_available(),
                dwg_available=is_dwg_converter_available(),
                auth_required=is_auth_required_env(),
                invite_code_required=invite_req,
                ui_lang=ui_lang,
            )
        )

    @router.get("/", response_class=HTMLResponse)
    def workbench_ui(request: Request) -> HTMLResponse:
        raw_lang = request.query_params.get("lang", "en").strip().lower()
        ui_lang: Literal["en", "el"] = "el" if raw_lang == "el" else "en"
        return _render_workbench(request, ui_lang)

    @router.get("/el", response_class=HTMLResponse)
    def workbench_ui_el(request: Request) -> HTMLResponse:
        return _render_workbench(request, "el")

    @router.get("/guide", response_class=HTMLResponse)
    def user_guide_ui(request: Request) -> HTMLResponse:
        raw_lang = request.query_params.get("lang", "en").strip().lower()
        renderer = render_user_guide_el_html if raw_lang == "el" else render_user_guide_html
        return HTMLResponse(
            content=renderer(
                base_url=safe_base_url(request),
                show_api_docs=not ctx.disable_openapi,
            )
        )

    @router.get("/el/guide", response_class=HTMLResponse)
    def user_guide_ui_el(request: Request) -> HTMLResponse:
        return HTMLResponse(
            content=render_user_guide_el_html(
                base_url=safe_base_url(request),
                show_api_docs=not ctx.disable_openapi,
            )
        )

    @router.get("/favicon.ico", include_in_schema=False)
    def favicon_ico() -> Response:
        return _asset_response("favicon.ico")

    @router.get("/favicon.svg", include_in_schema=False)
    def favicon_svg() -> Response:
        return _asset_response("cyelec_logo.svg")

    @router.get("/logo.png", include_in_schema=False)
    def brand_logo_png() -> Response:
        return _asset_response("cyelec_logo.png")

    @router.get("/og-thumbnail.jpg", include_in_schema=False)
    def og_thumbnail_jpg() -> Response:
        return _asset_response("og_thumbnail.jpg")

    @router.get("/health", response_model=HealthResponse)
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
            stripe_configured=is_stripe_configured(),
            google_sso_configured=bool(os.environ.get("CYELEC_GOOGLE_CLIENT_ID", "").strip()),
            auth_required=is_auth_required_env(),
            invite_code_required=bool(os.environ.get("CYELEC_PILOT_INVITE_CODE", "").strip()),
            store_backend=ctx.store_backend,
        )

    @router.get("/v1/standards")
    def get_standards() -> dict[str, object]:
        std = load_standards()
        return {
            "pipeline_version": __version__,
            "versions": std.versions_map(),
            "eac_profiles": {k.value: v.model_dump() for k, v in std.eac_rules.profiles.items()},
            "earthing_default": std.eac_rules.earthing.default_system.value,
            "ambient_defaults_c": std.eac_rules.ambient_defaults_c.model_dump(),
        }

    return router
