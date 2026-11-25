"""End-to-end Phase 3 integration tests: EAC submission pack, SHA-256 manifest, and SaaS API."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ezdxf.filemanagement import readfile as read_dxf
from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.models.electrical import DesignOverrides, EacProfile, EarthingSystem
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.eac_pack import build_submission_zip, compute_file_sha256
from cyelec.synth.sample_plan import generate_cyprus_3bed_dxf


def test_phase3_eac_submission_pack_manifest_and_zip(tmp_path: Path) -> None:
    input_dxf = tmp_path / "cyprus_3bed.dxf"
    generate_cyprus_3bed_dxf(out_dxf=input_dxf)

    p2 = run_phase2_pipeline(input_dxf)
    out_dir = tmp_path / "eac_pack"
    outputs = export_phase3_deliverables(p2, out_dir)
    zip_path = build_submission_zip(outputs, out_dir / "eac_submission_pack.zip")

    # Verify manifest.json SHA-256 checksums match every artifact on disk
    manifest = json.loads(outputs["manifest_json"].read_text(encoding="utf-8"))
    assert manifest["has_blockers"] is False
    assert manifest["etek_signoff_required"] is True
    for key, meta in manifest["files"].items():
        fpath = outputs[key]
        assert fpath.is_file()
        assert meta["sha256"] == compute_file_sha256(fpath)

    # Verify EAC cover HTML embeds inline SLD SVG & statutory ETEK sign-off
    pack_html = outputs["eac_pack_html"].read_text(encoding="utf-8")
    assert "<svg " in pack_html
    assert "CYPRUS EAC PLAN-APPROVAL SUBMISSION PACK" in pack_html
    assert "ETEK Electrical Engineer" in pack_html

    # Verify layout DXF includes E-TITLE block and passes audit
    layout_doc = read_dxf(str(outputs["layout_dxf"]))
    assert len(layout_doc.audit().errors) == 0
    assert "E-TITLE" in {e.dxf.layer for e in layout_doc.modelspace()}

    # Verify ZIP archive contains all core submission deliverables
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    for expected in (
        "design_result.json",
        "spatial_layout.json",
        "electrical_layout.dxf",
        "single_line_diagram.svg",
        "single_line_diagram.dxf",
        "single_line_diagram.qet",
        "load_schedule.xlsx",
        "load_schedule.csv",
        "calc_report.md",
        "calc_report.html",
        "eac_submission_pack.html",
        "manifest.json",
    ):
        assert expected in names


def test_stateless_multitenant_saas_api_endpoints(tmp_path: Path) -> None:
    client = TestClient(create_app())

    # 1. Health & standards discovery
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    stds = client.get("/v1/standards")
    assert stds.status_code == 200
    assert stds.json()["earthing_default"] == "TT"
    assert stds.json()["eac_profiles"]["cyprus_eac_18th_a2"]["bathroom_socket_exclusion_m"] == 2.5

    # 2. Stateless JSON BuildingModel solve with Pass-2 overrides & X-Tenant-ID
    input_dxf = tmp_path / "api_input.dxf"
    building = generate_cyprus_3bed_dxf(out_dxf=input_dxf)
    overrides = DesignOverrides(
        eac_profile=EacProfile.EAC_17TH_A3,
        earthing_system=EarthingSystem.TN_S,
        ze_or_ra_ohm=0.50,
    )
    resp_b = client.post(
        "/v1/solve/building",
        headers={"X-Tenant-ID": "cy-tenant-001"},
        json={
            "building": building.model_dump(mode="json"),
            "overrides": overrides.model_dump(mode="json"),
        },
    )
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["tenant_id"] == "cy-tenant-001"
    assert body_b["result"]["design"]["earthing_system"] == "TN-S"
    assert body_b["result"]["design"]["eac_compliance"]["bathroom_socket_exclusion_m"] == 3.0

    # 3. Multipart DXF upload -> JSON result
    dxf_bytes = input_dxf.read_bytes()
    resp_dxf = client.post(
        "/v1/solve/dxf",
        headers={"X-Tenant-ID": "cy-tenant-002"},
        files={"file": ("cyprus_3bed.dxf", dxf_bytes, "application/dxf")},
    )
    assert resp_dxf.status_code == 200
    body_dxf = resp_dxf.json()
    assert body_dxf["tenant_id"] == "cy-tenant-002"
    assert body_dxf["result"]["design"]["provenance"]["input_sha256"] == building.source_sha256
    assert len(body_dxf["result"]["design"]["circuits"]) == 13
    assert body_dxf["result"]["design"]["has_blockers"] is False

    # 4. Multipart DXF upload -> downloadable EAC submission pack ZIP bundle
    resp_zip = client.post(
        "/v1/solve/dxf/bundle",
        headers={"X-Tenant-ID": "cy-tenant-003"},
        files={"file": ("cyprus_3bed.dxf", dxf_bytes, "application/dxf")},
    )
    assert resp_zip.status_code == 200
    assert resp_zip.headers["content-type"] == "application/zip"
    assert resp_zip.headers["x-tenant-id"] == "cy-tenant-003"
    with zipfile.ZipFile(io.BytesIO(resp_zip.content), "r") as zf:
        znames = set(zf.namelist())
    assert "single_line_diagram.svg" in znames
    assert "electrical_layout.dxf" in znames
    assert "load_schedule.xlsx" in znames
    assert "manifest.json" in znames

    # 5. Reject unsupported file extensions
    resp_bad = client.post(
        "/v1/solve/dxf",
        files={"file": ("bad.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert resp_bad.status_code == 400
