"""Integration tests for Pillar 5: Multi-Tenant SaaS Workspace, Auth, Revision Diff & Metering."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def test_strict_cross_tenant_isolation_and_branding_inheritance(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "workspace.db")
    client = TestClient(app)

    headers_a = {"X-Tenant-ID": "firm-nicosia-a"}
    headers_b = {"X-Tenant-ID": "firm-limassol-b"}

    # 1. Tenant A saves consulting firm branding & bilingual default
    upd = client.put(
        "/v1/tenants/me",
        headers=headers_a,
        json={
            "display_name": "Nicosia MEP Associates",
            "report_language": "bilingual",
            "branding": {
                "firm_name": "Nicosia MEP Associates",
                "engineer_name": "M. Georgiou",
                "etek_reg_no": "ETEK-E-2026",
            },
        },
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["branding"]["etek_reg_no"] == "ETEK-E-2026"
    assert upd.json()["report_language"] == "bilingual"

    # 2. Tenant A creates a project and solves Rev 1 (no explicit branding passed -> inherited!)
    proj_resp = client.post(
        "/v1/projects",
        headers=headers_a,
        json={"name": "Strovolos 3-Bed Block A", "description": "EAC Stage 1"},
    )
    assert proj_resp.status_code == 201, proj_resp.text
    project_id = proj_resp.json()["project_id"]

    rev1_resp = client.post(
        f"/v1/projects/{project_id}/revisions",
        headers=headers_a,
        json={
            "benchmark_template": "apartment_3bed",
            "overrides": {
                "circuit_overrides": {"C3": {"circuit_id": "C3", "grouping_count": 3}},
            },
            "notes": "Initial architectural issue",
        },
    )
    assert rev1_resp.status_code == 201, rev1_resp.text
    rev1 = rev1_resp.json()
    assert rev1["summary"]["revision_number"] == 1
    assert rev1["result"]["design"]["branding"]["firm_name"] == "Nicosia MEP Associates"
    assert rev1["result"]["design"]["report_language"] == "bilingual"

    # 3. Tenant B MUST NOT see or access Tenant A's project or revisions (strict 404 isolation)
    assert client.get("/v1/projects", headers=headers_b).json() == []
    assert client.get(f"/v1/projects/{project_id}", headers=headers_b).status_code == 404
    assert (
        client.get(f"/v1/projects/{project_id}/revisions", headers=headers_b).status_code == 404
    )
    assert (
        client.get(f"/v1/projects/{project_id}/revisions/1", headers=headers_b).status_code == 404
    )
    assert (
        client.post(
            f"/v1/projects/{project_id}/revisions",
            headers=headers_b,
            json={"benchmark_template": "apartment_3bed"},
        ).status_code
        == 404
    )


def test_revision_carry_forward_diff_dxf_upload_and_usage_metering(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "diff_ws.db")
    client = TestClient(app)
    headers = {"X-Tenant-ID": "cy-diff-firm"}

    pid = client.post(
        "/v1/projects",
        headers=headers,
        json={"name": "Engomi Apartment Revision Track"},
    ).json()["project_id"]

    # Rev 1: DXF upload with C3 grouping_count=3 override
    dxf_path = tmp_path / "apt_plan.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=dxf_path)
    r1 = client.post(
        f"/v1/projects/{pid}/revisions/dxf",
        headers=headers,
        files={"file": ("apt_rev1.dxf", dxf_path.read_bytes(), "application/dxf")},
        data={
            "overrides_json": (
                '{"circuit_overrides": {"C3": {"circuit_id": "C3", "grouping_count": 3}}}'
            ),
            "notes": "Rev 1 initial DXF",
        },
    )
    assert r1.status_code == 201, r1.text
    veranda_id = next(
        r["id"]
        for r in r1.json()["result"]["design"]["building"]["rooms"]
        if r["normalized_type"] == "veranda"
    )

    # Rev 2: Re-upload DXF with room_type_override veranda->bedroom + 6 kVA PV & carry_forward=True
    r2 = client.post(
        f"/v1/projects/{pid}/revisions/dxf",
        headers=headers,
        files={"file": ("apt_rev2.dxf", dxf_path.read_bytes(), "application/dxf")},
        data={
            "overrides_json": (
                f'{{"room_type_overrides": {{"{veranda_id}": "bedroom"}}, "pv_capacity_kva": 6.0}}'
            ),
            "carry_forward": "true",
            "notes": "Architect enclosed veranda into bedroom + added 6 kVA PV",
        },
    )
    assert r2.status_code == 201, r2.text
    rev2 = r2.json()
    assert rev2["summary"]["revision_number"] == 2
    # Pass-2 carry-forward preserved C3 grouping_count=3 from Rev 1!
    assert rev2["overrides"]["circuit_overrides"]["C3"]["grouping_count"] == 3
    assert rev2["overrides"]["room_type_overrides"][veranda_id] == "bedroom"
    assert rev2["overrides"]["pv_capacity_kva"] == 6.0

    # Revision Diff Rev 1 -> Rev 2
    diff_resp = client.get(f"/v1/projects/{pid}/diff/1/2", headers=headers)
    assert diff_resp.status_code == 200, diff_resp.text
    diff = diff_resp.json()
    assert diff["rev_a"] == 1
    assert diff["rev_b"] == 2
    assert any(
        d["room_id"] == veranda_id and d["change"] == "reclassified" and d["new_type"] == "bedroom"
        for d in diff["room_deltas"]
    )
    assert len(diff["circuit_deltas"]) >= 1
    assert diff["diversified_kva_delta"] > 0.0
    assert diff["boq_eur_delta"] > 0.0

    # Per-tenant usage metering recorded both DXF solves
    usage_resp = client.get("/v1/tenant/usage", headers=headers)
    assert usage_resp.status_code == 200
    usage = usage_resp.json()
    assert usage["tenant_id"] == "cy-diff-firm"
    assert usage["total_events"] == 2
    assert usage["counts_by_event_type"]["dxf_revision_solve"] == 2
    assert usage["total_duration_ms"] > 0.0


def test_per_tenant_api_key_auth_prevents_tenant_spoofing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(db_path=tmp_path / "auth_ws.db")
    client = TestClient(app)

    # Issue a per-tenant API key for 'firm-alpha' before enabling strict auth mode
    key_resp = client.post("/v1/tenants/me/api-key", headers={"X-Tenant-ID": "firm-alpha"})
    assert key_resp.status_code == 200
    api_key = key_resp.json()["api_key"]
    assert api_key.startswith("cyelec_live_")

    # Enable strict production auth mode
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "1")

    # Missing or wrong API key -> 401
    assert client.get("/v1/tenants/me").status_code == 401
    assert client.get("/v1/tenants/me", headers={"X-API-Key": "wrong-key"}).status_code == 401

    # Valid API key succeeds AND prevents spoofing X-Tenant-ID to another tenant!
    me_resp = client.get(
        "/v1/tenants/me",
        headers={"X-API-Key": api_key, "X-Tenant-ID": "spoofed-victim-tenant"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["tenant_id"] == "firm-alpha"
    assert me_resp.json()["has_api_key"] is True
