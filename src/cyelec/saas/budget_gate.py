"""Reversible Google Cloud €10/mo Budget Hard-Gate receiver (Pub/Sub Push -> Cloud Run Ingress).

Triggered by a GCP Billing Budget Pub/Sub notification. When reported spend reaches
`budgetAmount * CYELEC_BUDGET_GATE_RATIO` (default 0.90 = €9 on a €10 budget to absorb
GCP billing reporting lag), this service flips all target Cloud Run services' ingress to
`INGRESS_TRAFFIC_INTERNAL_ONLY`, immediately halting 100% of public traffic and compute
spend while preserving Firestore data, GCS blobs, and Secret Manager intact.

Re-enabling after review is a single `terraform apply` or
`gcloud run services update <svc> --region=europe-west1 --ingress=all`.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_GCP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class BudgetNotification(BaseModel):
    """Parsed GCP Cloud Billing Budget Pub/Sub JSON payload."""

    model_config = ConfigDict(extra="ignore")

    budgetDisplayName: str = ""
    costAmount: float = Field(ge=0.0)
    budgetAmount: float = Field(gt=0.0)
    currencyCode: str = "EUR"
    alertThresholdExceeded: float | None = None


class PubSubMessage(BaseModel):
    """GCP Pub/Sub push message wrapper."""

    model_config = ConfigDict(extra="ignore")

    data: str
    messageId: str | None = None


class PubSubPushEnvelope(BaseModel):
    """Top-level GCP Pub/Sub HTTP push request body."""

    model_config = ConfigDict(extra="ignore")

    message: PubSubMessage
    subscription: str | None = None


class BudgetGateResult(BaseModel):
    """Structured response logged by Cloud Run after evaluating a budget event."""

    model_config = ConfigDict(extra="forbid")

    action: str
    cost_amount: float
    budget_amount: float
    currency: str
    gate_threshold_amount: float
    paused_services: list[str] = Field(default_factory=list)


def _validate_gcp_identifier(value: str, label: str) -> str:
    clean = value.strip()
    if not _GCP_ID_RE.match(clean):
        raise ValueError(f"Invalid GCP {label}: {value!r}")
    return clean


def pause_cloud_run_service(
    project_id: str,
    region: str,
    service_name: str,
    *,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Flip a Cloud Run v2 service's ingress to INGRESS_TRAFFIC_INTERNAL_ONLY via ADC."""
    import google.auth
    import google.auth.transport.requests

    safe_project = _validate_gcp_identifier(project_id, "project_id")
    safe_region = _validate_gcp_identifier(region, "region")
    safe_service = _validate_gcp_identifier(service_name, "service_name")

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    token = str(creds.token)

    path = (
        f"/v2/projects/{safe_project}/locations/{safe_region}"
        f"/services/{safe_service}?updateMask=ingress"
    )
    body = json.dumps({"ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY"}).encode("utf-8")
    conn = http.client.HTTPSConnection("run.googleapis.com", timeout=timeout_s)
    try:
        conn.request(
            "PATCH",
            path,
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        if resp.status >= 400:
            raise RuntimeError(f"Cloud Run API PATCH failed ({resp.status}): {raw[:300]}")
        parsed: dict[str, Any] = json.loads(raw) if raw else {}
        return parsed
    finally:
        conn.close()


def evaluate_budget_notification(
    notif: BudgetNotification,
    *,
    project_id: str,
    region: str,
    target_services: list[str],
    gate_ratio: float = 0.90,
    pause_fn: Callable[[str, str, str], dict[str, Any]] = pause_cloud_run_service,
) -> BudgetGateResult:
    """Evaluate a GCP budget notification and pause target services if spend >= gate threshold."""
    threshold_amount = round(notif.budgetAmount * gate_ratio, 4)
    ratio_exceeded = notif.costAmount >= threshold_amount - 1e-9
    alert_exceeded = (
        notif.alertThresholdExceeded is not None
        and notif.alertThresholdExceeded >= gate_ratio - 1e-9
    )

    if not (ratio_exceeded or alert_exceeded):
        return BudgetGateResult(
            action="noop_within_budget",
            cost_amount=notif.costAmount,
            budget_amount=notif.budgetAmount,
            currency=notif.currencyCode,
            gate_threshold_amount=threshold_amount,
            paused_services=[],
        )

    paused: list[str] = []
    for svc in target_services:
        svc_clean = svc.strip()
        if not svc_clean:
            continue
        pause_fn(project_id, region, svc_clean)
        paused.append(svc_clean)

    return BudgetGateResult(
        action="paused_cloud_run_ingress",
        cost_amount=notif.costAmount,
        budget_amount=notif.budgetAmount,
        currency=notif.currencyCode,
        gate_threshold_amount=threshold_amount,
        paused_services=paused,
    )


def create_budget_gate_app(
    *,
    pause_fn: Callable[[str, str, str], dict[str, Any]] = pause_cloud_run_service,
) -> FastAPI:
    """Create the lightweight Cloud Run Pub/Sub push receiver for the €10/mo hard gate."""
    gate_api = FastAPI(
        title="cyelec-budget-gate",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @gate_api.get("/health")
    def gate_health() -> dict[str, str]:
        return {"status": "ok", "service": "cyelec-budget-gate"}

    @gate_api.post("/pubsub/budget", response_model=BudgetGateResult)
    def handle_budget_pubsub(envelope: PubSubPushEnvelope) -> BudgetGateResult:
        expected_sub = os.environ.get("CYELEC_EXPECTED_SUBSCRIPTION", "").strip()
        if expected_sub and envelope.subscription != expected_sub:
            raise HTTPException(status_code=403, detail="Unexpected Pub/Sub subscription.")

        try:
            decoded = base64.b64decode(envelope.message.data).decode("utf-8")
            notif = BudgetNotification.model_validate_json(decoded)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="Invalid budget Pub/Sub payload."
            ) from exc

        project_id = os.environ.get("CYELEC_GCP_PROJECT", "").strip()
        region = os.environ.get("CYELEC_GCP_REGION", "europe-west1").strip()
        raw_targets = os.environ.get("CYELEC_GATE_TARGET_SERVICES", "cyelec-staging,cyelec-prod")
        targets = [s.strip() for s in raw_targets.split(",") if s.strip()]
        gate_ratio = float(os.environ.get("CYELEC_BUDGET_GATE_RATIO", "0.90"))

        if not project_id:
            raise HTTPException(
                status_code=500, detail="CYELEC_GCP_PROJECT environment variable is not set."
            )

        return evaluate_budget_notification(
            notif,
            project_id=project_id,
            region=region,
            target_services=targets,
            gate_ratio=gate_ratio,
            pause_fn=pause_fn,
        )

    return gate_api


app = create_budget_gate_app()
