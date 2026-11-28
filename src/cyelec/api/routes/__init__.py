"""Focused FastAPI APIRouter modules decomposed from the legacy app.py monolith."""

from __future__ import annotations

from cyelec.api.routes.auth_team import build_auth_team_router
from cyelec.api.routes.billing import build_billing_router
from cyelec.api.routes.solve import build_solve_router
from cyelec.api.routes.ui_assets import build_ui_assets_router
from cyelec.api.routes.workspace import build_workspace_router

__all__ = [
    "build_auth_team_router",
    "build_billing_router",
    "build_solve_router",
    "build_ui_assets_router",
    "build_workspace_router",
]
