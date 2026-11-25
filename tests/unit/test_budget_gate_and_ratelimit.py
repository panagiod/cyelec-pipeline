"""Tests for Layer-1 sliding-window rate limiting and Layer-2 €10 GCP budget hard gate."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.saas.budget_gate import create_budget_gate_app


def _b64_pubsub(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return {"message": {"data": encoded, "messageId": "msg-001"}}


def test_budget_gate_noop_below_threshold_and_pauses_at_90_pct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paused_calls: list[tuple[str, str, str]] = []

    def _fake_pause(project: str, region: str, svc: str) -> dict[str, Any]:
        paused_calls.append((project, region, svc))
        return {"name": svc, "ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY"}

    monkeypatch.setenv("CYELEC_GCP_PROJECT", "cyelec-saas-261125")
    monkeypatch.setenv("CYELEC_GCP_REGION", "europe-west1")
    monkeypatch.setenv("CYELEC_GATE_TARGET_SERVICES", "cyelec-staging,cyelec-prod")
    monkeypatch.setenv("CYELEC_BUDGET_GATE_RATIO", "0.90")

    client = TestClient(create_budget_gate_app(pause_fn=_fake_pause))
    assert client.get("/health").status_code == 200

    # 1. Spend at 50% (€5 / €10) -> NOOP, services remain live
    r_low = client.post(
        "/pubsub/budget",
        json=_b64_pubsub(
            {
                "budgetDisplayName": "CyElec Hard Gate (10 EUR)",
                "costAmount": 5.0,
                "budgetAmount": 10.0,
                "currencyCode": "EUR",
                "alertThresholdExceeded": 0.5,
            }
        ),
    )
    assert r_low.status_code == 200, r_low.text
    assert r_low.json()["action"] == "noop_within_budget"
    assert r_low.json()["paused_services"] == []
    assert paused_calls == []

    # 2. Spend hits 90% (€9.05 / €10) -> HARD GATE pauses BOTH staging & prod ingress!
    r_hit = client.post(
        "/pubsub/budget",
        json=_b64_pubsub(
            {
                "budgetDisplayName": "CyElec Hard Gate (10 EUR)",
                "costAmount": 9.05,
                "budgetAmount": 10.0,
                "currencyCode": "EUR",
                "alertThresholdExceeded": 0.9,
            }
        ),
    )
    assert r_hit.status_code == 200, r_hit.text
    body = r_hit.json()
    assert body["action"] == "paused_cloud_run_ingress"
    assert body["paused_services"] == ["cyelec-staging", "cyelec-prod"]
    assert paused_calls == [
        ("cyelec-saas-261125", "europe-west1", "cyelec-staging"),
        ("cyelec-saas-261125", "europe-west1", "cyelec-prod"),
    ]

    # 3. Malformed payload -> 400
    bad = client.post("/pubsub/budget", json={"message": {"data": "not-valid-base64!!!"}})
    assert bad.status_code == 400


def test_rate_limit_middleware_returns_429_and_never_throttles_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYELEC_RATE_LIMIT_RPM", "2")
    client = TestClient(create_app(db_path=tmp_path / "rl.db"))

    # First 2 solves succeed; 3rd within 60s returns HTTP 429 with Retry-After
    for _ in range(2):
        ok = client.post("/v1/solve/benchmark/apartment_3bed")
        assert ok.status_code == 200, ok.text

    throttled = client.post("/v1/solve/benchmark/apartment_3bed")
    assert throttled.status_code == 429
    assert throttled.headers.get("Retry-After") == "60"

    # /health is never rate-limited
    assert client.get("/health").status_code == 200
