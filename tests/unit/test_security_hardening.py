"""Security regression tests for OWASP, multi-tenant, CSV, SSRF, and path-traversal hardening."""

from __future__ import annotations

import base64
import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cyelec.api.app import _sanitize_client_error, create_app
from cyelec.api.docs_ui import render_user_guide_html
from cyelec.api.ui import render_etek_workbench_html
from cyelec.pipeline import run_phase2_pipeline
from cyelec.render.schedule_xlsx import (
    _safe_csv_cell,
    export_boq_csv,
    export_load_schedule_csv,
)
from cyelec.saas.budget_gate import _validate_gcp_identifier, create_budget_gate_app
from cyelec.saas.firestore_store import FirestoreWorkspaceStore
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def _make_dxf_bytes(tmp_path: Path) -> bytes:
    dxf_path = tmp_path / "sample.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=dxf_path)
    return dxf_path.read_bytes()


def test_security_headers_on_html_api_and_error_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "1")
    client = TestClient(create_app(db_path=tmp_path / "sec.db"))

    # HTML workbench response over HTTPS proxy header
    r_html = client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert r_html.status_code == 200
    assert r_html.headers["x-frame-options"] == "DENY"
    assert r_html.headers["x-content-type-options"] == "nosniff"
    assert r_html.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" in r_html.headers
    assert "frame-ancestors 'none'" in r_html.headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline'" in r_html.headers["content-security-policy"]

    # JSON API health response uses strict default-src 'none' CSP
    r_api = client.get("/health", headers={"X-Forwarded-Proto": "https"})
    assert r_api.status_code == 200
    assert r_api.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"

    # Early-rejected 401 response also includes security headers & replaces bad X-Request-ID
    r_401 = client.get(
        "/v1/standards",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Request-ID": "bad\r\nX-Injected: 1",
        },
    )
    assert r_401.status_code == 401
    assert r_401.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" in r_401.headers
    assert "\r" not in r_401.headers["x-request-id"]
    assert "bad" not in r_401.headers["x-request-id"]


def test_reflected_xss_host_header_blocked_and_escaped(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "xss.db"))
    evil_host = 'evil.com"><script>alert(1)</script>'

    for route in ("/", "/guide"):
        resp = client.get(
            route,
            headers={
                "X-Forwarded-Host": evil_host,
                "X-Forwarded-Proto": "javascript",
            },
        )
        assert resp.status_code == 200
        assert "<script>alert(1)</script>" not in resp.text
        assert "evil.com" not in resp.text
        assert "javascript://" not in resp.text

    # Direct renderer escaping defense-in-depth
    wb = render_etek_workbench_html(base_url='https://x.test/"><script>alert(9)</script>')
    guide = render_user_guide_html(base_url='https://x.test/"><script>alert(9)</script>')
    assert "<script>alert(9)</script>" not in wb
    assert "<script>alert(9)</script>" not in guide
    assert "&quot;&gt;&lt;script&gt;" in wb
    assert "&quot;&gt;&lt;script&gt;" in guide


def test_openapi_disabled_in_prod_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYELEC_DISABLE_OPENAPI", "1")
    client = TestClient(create_app(db_path=tmp_path / "prod_openapi.db"))
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_demo_lock_blocks_unauthenticated_tenant_mutations_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CYELEC_AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("CYELEC_DEMO_LOCK_TENANT_MUTATIONS", "1")
    monkeypatch.setenv("CYELEC_ADMIN_KEY", "adm_secret_123456")
    client = TestClient(create_app(db_path=tmp_path / "demo.db"))

    # Public demo solve still works without API key
    r_solve = client.post("/v1/solve/benchmark/apartment_3bed")
    assert r_solve.status_code == 200

    # Unauthenticated key issuance and tenant erasure are blocked with 403
    r_key = client.post("/v1/tenants/me/api-key", headers={"X-Tenant-ID": "victim"})
    assert r_key.status_code == 403
    r_del = client.delete("/v1/tenants/me", headers={"X-Tenant-ID": "victim"})
    assert r_del.status_code == 403

    # Admin-provisioned tenant with valid X-API-Key CAN rotate its own key and delete itself
    r_prov = client.post(
        "/v1/admin/tenants/demo-firm/api-key",
        headers={"X-Admin-Key": "adm_secret_123456"},
    )
    assert r_prov.status_code == 200
    api_key = r_prov.json()["api_key"]

    r_rot = client.post("/v1/tenants/me/api-key", headers={"X-API-Key": api_key})
    assert r_rot.status_code == 200
    rotated_key = r_rot.json()["api_key"]

    r_erase = client.delete("/v1/tenants/me", headers={"X-API-Key": rotated_key})
    assert r_erase.status_code == 200
    assert r_erase.json()["deleted"] is True


def test_multifloor_path_traversal_sanitized_and_file_count_capped(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "mf.db"))
    dxf_bytes = _make_dxf_bytes(tmp_path)

    # Path traversal payload in floors_json is safely sanitized and cannot escape tmp dir
    r = client.post(
        "/v1/solve/dxf/multifloor",
        files=[
            ("files", ("gf.dxf", dxf_bytes, "application/dxf")),
            ("files", ("1f.dxf", dxf_bytes, "application/dxf")),
        ],
        data={"floors_json": json.dumps(["../../escaped_evil", "1F"])},
    )
    assert r.status_code == 200
    assert not (tmp_path / "escaped_evil.dxf").exists()

    # >12 floor files rejected with HTTP 400
    too_many = [("files", (f"f{i}.dxf", dxf_bytes, "application/dxf")) for i in range(13)]
    r_cap = client.post("/v1/solve/dxf/multifloor", files=too_many)
    assert r_cap.status_code == 400
    assert "at most 12" in r_cap.json()["detail"]


def test_invalid_tenant_and_project_ids_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYELEC_ADMIN_KEY", "adm_secret_123456")
    client = TestClient(create_app(db_path=tmp_path / "ids.db"))

    # Path traversal / invalid characters in X-Tenant-ID
    r_bad_tid = client.get("/v1/tenants/me", headers={"X-Tenant-ID": "../../prod"})
    assert r_bad_tid.status_code == 400

    # Invalid project_id rejected before reaching store
    r_bad_pid = client.get("/v1/projects/bad..proj")
    assert r_bad_pid.status_code == 400

    # Invalid admin tenant_id rejected
    r_bad_admin = client.post(
        "/v1/admin/tenants/bad..tenant/api-key",
        headers={"X-Admin-Key": "adm_secret_123456"},
    )
    assert r_bad_admin.status_code == 400

    # Firestore store _safe_id defense-in-depth
    for bad in ("", "../etc", "a/b", "a\\b", "a\x00b", "x" * 129):
        with pytest.raises(ValueError):
            FirestoreWorkspaceStore._safe_id(bad, "tenant_id")
    assert FirestoreWorkspaceStore._safe_id("cyprus_firm-01", "tenant_id") == "cyprus_firm-01"


def test_rate_limiter_uses_rightmost_xff_and_covers_dxf_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYELEC_RATE_LIMIT_RPM", "2")
    client = TestClient(create_app(db_path=tmp_path / "rl.db"))
    dxf_bytes = _make_dxf_bytes(tmp_path)

    # Spoofing leftmost X-Forwarded-For IP cannot bypass limit for same rightmost proxy IP
    for spoof_ip in ("1.1.1.1", "2.2.2.2"):
        r = client.post(
            "/v1/dxf/layers",
            files={"file": ("plan.dxf", dxf_bytes, "application/dxf")},
            headers={"X-Forwarded-For": f"{spoof_ip}, 203.0.113.42"},
        )
        assert r.status_code == 200

    r_blocked = client.post(
        "/v1/dxf/layers",
        files={"file": ("plan.dxf", dxf_bytes, "application/dxf")},
        headers={"X-Forwarded-For": "3.3.3.3, 203.0.113.42"},
    )
    assert r_blocked.status_code == 429
    assert r_blocked.headers["retry-after"] == "60"
    assert r_blocked.headers["x-frame-options"] == "DENY"


def test_csv_formula_injection_neutralized(tmp_path: Path) -> None:
    assert _safe_csv_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert _safe_csv_cell("+SUM(1,2)") == "'+SUM(1,2)"
    assert _safe_csv_cell("-1+1") == "'-1+1"
    assert _safe_csv_cell("@SUM(A1:A2)") == "'@SUM(A1:A2)"
    assert _safe_csv_cell(-12.5) == -12.5
    assert _safe_csv_cell("-12.5") == "-12.5"
    assert _safe_csv_cell("Living Room") == "Living Room"

    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    building.rooms[0].raw_label = '=HYPERLINK("https://evil.example","click")'
    p2 = run_phase2_pipeline(building)
    p2.design.circuits[0].spec.description = '=HYPERLINK("https://evil.example","click")'

    sched_csv = export_load_schedule_csv(p2.design, tmp_path / "sched.csv")
    boq_csv = export_boq_csv(p2.design, tmp_path / "boq.csv")
    with sched_csv.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    flattened = [cell for row in rows for cell in row]
    assert not any(c.startswith("=HYPERLINK") for c in flattened)
    assert any(c.startswith("'=HYPERLINK") for c in flattened)
    assert boq_csv.exists()


def test_budget_gate_validation_and_subscription_check(monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("../evil", "proj/evil", "UPPER", "bad host"):
        with pytest.raises(ValueError, match="Invalid GCP project_id"):
            _validate_gcp_identifier(bad, "project_id")
    assert _validate_gcp_identifier("cyelec-saas-261125", "project_id") == "cyelec-saas-261125"

    expected_sub = "projects/cyelec-saas-261125/subscriptions/cyelec-budget-gate-push"
    monkeypatch.setenv("CYELEC_GCP_PROJECT", "cyelec-saas-261125")
    monkeypatch.setenv("CYELEC_EXPECTED_SUBSCRIPTION", expected_sub)
    client = TestClient(create_budget_gate_app())

    # OpenAPI disabled on budget gate
    assert client.get("/openapi.json").status_code == 404

    payload_b64 = base64.b64encode(
        json.dumps({"costAmount": 1.0, "budgetAmount": 10.0}).encode("utf-8")
    ).decode("ascii")

    # Wrong subscription rejected with 403
    r_bad_sub = client.post(
        "/pubsub/budget",
        json={
            "subscription": "projects/other/subscriptions/spoof",
            "message": {"data": payload_b64},
        },
    )
    assert r_bad_sub.status_code == 403

    # Expected subscription succeeds (below threshold -> no_op)
    r_ok = client.post(
        "/pubsub/budget",
        json={
            "subscription": expected_sub,
            "message": {"data": payload_b64},
        },
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["action"] == "noop_within_budget"


def test_sanitize_client_error_redacts_tmp_paths() -> None:
    msg = _sanitize_client_error(
        ValueError("Failed reading /tmp/cyelec_abc123/floor_00.dxf: bad header")
    )
    assert "/tmp/" not in msg
    assert "<uploaded_file>" in msg
