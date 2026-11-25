"""Tests for Track B: Multi-DXF floor combiner, SLD DXF parity & BS 7671:2018+A2 checks."""

from __future__ import annotations

import json
from pathlib import Path

from ezdxf.filemanagement import readfile as read_dxf
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cyelec.api.app import create_app
from cyelec.cli import app as cli_app
from cyelec.ingest.dxf_ingest import ingest_multi_floor_dxf
from cyelec.models.electrical import (
    DesignOverrides,
    EacProfile,
    ReviewSeverity,
    TenantBranding,
)
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def test_multifloor_dxf_combiner_python_cli_and_api(tmp_path: Path) -> None:
    gf_dxf = tmp_path / "ground_floor.dxf"
    f1_dxf = tmp_path / "first_floor.dxf"
    gf_model = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=gf_dxf)
    f1_model = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=f1_dxf)

    # 1. Direct Python multi-DXF ingestion -> MDB + DB-GF + DB-1F
    model, flags = ingest_multi_floor_dxf(
        [("GF", gf_dxf), ("1F", f1_dxf)],
        submain_lengths_m={"GF": 6.0, "1F": 18.0},
        project_name="Limassol 2-Floor Combined Villa",
    )
    assert model.db_anchor.id == "MDB"
    assert [sb.board_id for sb in model.sub_boards] == ["DB-GF", "DB-1F"]
    assert any(f.code == "MULTIFLOOR_DXF_COMBINED" for f in flags)
    assert len(model.rooms) == len(gf_model.rooms) + len(f1_model.rooms) == 22

    p2 = run_phase2_pipeline(model)
    assert len(p2.design.sub_mains) == 2
    assert all(sm.overall_pass for sm in p2.design.sub_mains)

    # 2. CLI `cyelec solve-multifloor`
    runner = CliRunner()
    cli_out = tmp_path / "mf_cli"
    res = runner.invoke(
        cli_app,
        [
            "solve-multifloor",
            "--floor",
            f"GF={gf_dxf}",
            "--floor",
            f"1F={f1_dxf}",
            "--out-dir",
            str(cli_out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "DB-GF" in res.output and "DB-1F" in res.output
    assert (cli_out / "single_line_diagram.dxf").is_file()

    # 3. FastAPI `POST /v1/solve/dxf/multifloor`
    client = TestClient(create_app(db_path=tmp_path / "mf.db"))
    resp = client.post(
        "/v1/solve/dxf/multifloor",
        files=[
            ("files", ("gf.dxf", gf_dxf.read_bytes(), "application/dxf")),
            ("files", ("1f.dxf", f1_dxf.read_bytes(), "application/dxf")),
        ],
        data={"floors_json": json.dumps(["GF", "1F"])},
        headers={"X-Tenant-ID": "cy-mf-tenant"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["result"]["design"]["sub_mains"]) == 2
    assert 'data-sub-board="DB-GF"' in body["floorplan_svg"]
    assert 'data-sub-board="DB-1F"' in body["floorplan_svg"]


def test_sld_dxf_subboards_and_branding_parity(tmp_path: Path) -> None:
    building = generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY)
    p2 = run_phase2_pipeline(
        building,
        overrides=DesignOverrides(
            branding=TenantBranding(
                firm_name="Larnaca Power MEP",
                engineer_name="A. Christou",
                etek_reg_no="ETEK-E-7710",
            )
        ),
    )
    outputs = export_phase3_deliverables(p2, tmp_path / "sld_parity")
    dxf_doc = read_dxf(str(outputs["sld_dxf"]))
    texts = [
        str(e.dxf.text)
        for e in dxf_doc.modelspace()
        if e.dxftype() == "TEXT" and e.dxf.layer == "E-SLD-TEXT"
    ]
    joined = "\n".join(texts)
    assert "MDB->DB-GF" in joined
    assert "MDB->DB-1F" in joined
    assert "100mA TYPE S" in joined
    assert "Larnaca Power MEP" in joined
    assert "ETEK-E-7710" in joined


def test_bs7671_a2_pv_backfeed_ipf_and_afdd_checks() -> None:
    # 1. Villa PV+EV under 18th+A2: PV bidirectional/busbar check + AFDD advisory
    villa = generate_benchmark_dxf(BenchmarkTemplate.VILLA_PV_EV)
    res_a2 = run_phase2_pipeline(
        villa,
        overrides=DesignOverrides(
            eac_profile=EacProfile.EAC_18TH_A2,
            prospective_fault_current_ka=8.5,
        ),
    )
    codes_a2 = {f.code: f for f in res_a2.design.review_flags}
    assert "BS7671_PV_BIDIRECTIONAL_AND_BUSBAR_CHECK" in codes_a2
    assert "BS7671_AFDD_ADVISORY_421_1_7" in codes_a2
    assert "BS7671_IPF_10KA_SWITCHGEAR_REQUIRED" in codes_a2
    assert codes_a2["BS7671_IPF_10KA_SWITCHGEAR_REQUIRED"].severity == ReviewSeverity.WARNING

    # 2. High prospective fault current (>10 kA) raises BLOCKER
    res_high_ipf = run_phase2_pipeline(
        villa,
        overrides=DesignOverrides(prospective_fault_current_ka=14.0),
    )
    assert any(
        f.code == "BS7671_IPF_EXCEEDS_10KA" and f.severity == ReviewSeverity.BLOCKER
        for f in res_high_ipf.design.review_flags
    )

    # 3. 17th+A3 profile does NOT emit 18th+A2 AFDD advisory
    res_17 = run_phase2_pipeline(
        villa,
        overrides=DesignOverrides(eac_profile=EacProfile.EAC_17TH_A3),
    )
    assert not any(f.code == "BS7671_AFDD_ADVISORY_421_1_7" for f in res_17.design.review_flags)
