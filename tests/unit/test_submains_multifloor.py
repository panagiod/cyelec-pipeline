"""Tests for Pillar 2: Multi-Floor Buildings & Sub-Distribution Boards (MDB -> DB-GF/DB-1F)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    Point2D,
    Room,
    RoomType,
    SubBoardSpec,
)
from cyelec.models.electrical import (
    CircuitCategory,
    DesignOverrides,
    EarthingSystem,
    ReviewSeverity,
)
from cyelec.pipeline import (
    export_phase3_deliverables,
    run_phase1_pipeline,
    run_phase2_pipeline,
)
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def test_cyprus_2storey_villa_submains_and_deliverables(tmp_path: Path) -> None:
    dxf_path = tmp_path / "villa_2storey.dxf"
    building = generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY, out_dxf=dxf_path)

    assert building.db_anchor.id == "MDB"
    assert [sb.board_id for sb in building.sub_boards] == ["DB-GF", "DB-1F"]

    p2 = run_phase2_pipeline(building)
    res = p2.design

    # Both sub-mains solved and pass
    assert len(res.sub_mains) == 2
    sm_by_id = {sm.board_id: sm for sm in res.sub_mains}
    assert set(sm_by_id.keys()) == {"DB-GF", "DB-1F"}

    for sm in res.sub_mains:
        assert sm.parent_board_id == "MDB"
        assert sm.rcd_selective_type_s is True
        assert sm.rcd_idn_ma == 100
        assert sm.thermal_pass is True
        assert sm.earth_fault_pass is True
        assert sm.cumulative_vd_pass is True
        assert sm.cumulative_lighting_vd_pct <= 3.0
        assert sm.cumulative_power_vd_pct <= 5.0
        assert sm.overall_pass is True
        assert len(sm.circuit_ids) >= 4

    # TT Type S selectivity info flag present
    assert any(f.code == "SUBMAIN_TT_SELECTIVE_RCD" for f in res.review_flags)

    # Circuits split across DB-GF and DB-1F
    gf_circuits = [c for c in res.circuits if c.spec.board_id == "DB-GF"]
    f1_circuits = [c for c in res.circuits if c.spec.board_id == "DB-1F"]
    assert len(gf_circuits) >= 5
    assert len(f1_circuits) >= 5

    # Export full Phase 3 pack and verify Sub-Mains appear in Excel, Reports, and SLD SVG
    out_dir = tmp_path / "pack"
    outputs = export_phase3_deliverables(p2, out_dir)

    wb = load_workbook(outputs["xlsx"], data_only=False)
    ws2 = wb["EAC & Review Flags"]
    ws2_text = " ".join(
        str(ws2.cell(row=r, column=c).value or "")
        for r in range(1, ws2.max_row + 1)
        for c in range(1, ws2.max_column + 1)
    )
    assert "DB-GF" in ws2_text
    assert "DB-1F" in ws2_text
    assert "Type S Selective RCD" in ws2_text

    md_text = outputs["md"].read_text(encoding="utf-8")
    assert "3b. Multi-Floor Sub-Distribution Boards" in md_text
    assert "`DB-GF`" in md_text and "`DB-1F`" in md_text

    html_text = outputs["html"].read_text(encoding="utf-8")
    assert "3b. Multi-Floor Sub-Distribution Boards" in html_text
    assert "DB-1F" in html_text

    sld_svg = outputs["sld_svg"].read_text(encoding="utf-8")
    assert "MDB -&gt; DB-GF" in sld_svg
    assert "MDB -&gt; DB-1F" in sld_svg
    assert "100mA Type S" in sld_svg


def test_submain_cumulative_vd_tightens_final_circuit_and_tns_zs_propagation() -> None:
    """Verify sub-main ΔV tightens final lighting ΔV budget and propagates R1+R2 on TN-S."""
    poly = [
        Point2D(x_mm=0.0, y_mm=0.0),
        Point2D(x_mm=6000.0, y_mm=0.0),
        Point2D(x_mm=6000.0, y_mm=5000.0),
        Point2D(x_mm=0.0, y_mm=5000.0),
    ]
    building = BuildingModel(
        project_name="Long Sub-Main Test",
        building_type=BuildingType.INDEPENDENT_HOUSE,
        total_floor_area_m2=60.0,
        rooms=[
            Room(
                id="R1",
                raw_label="Living Room",
                normalized_type=RoomType.LIVING_ROOM,
                floor="1F",
                polygon_mm=poly,
                area_m2=30.0,
            ),
            Room(
                id="R2",
                raw_label="Master Bedroom",
                normalized_type=RoomType.MASTER_BEDROOM,
                floor="1F",
                polygon_mm=poly,
                area_m2=30.0,
            ),
        ],
        db_anchor=DistributionBoardAnchor(id="MDB", location_mm=Point2D(x_mm=0.0, y_mm=0.0)),
        sub_boards=[
            SubBoardSpec(
                board_id="DB-1F",
                floor="1F",
                parent_board_id="MDB",
                location_mm=Point2D(x_mm=500.0, y_mm=500.0),
                submain_length_m=30.0,
                is_three_phase=True,
            )
        ],
    )

    # Run on TN-S to verify upstream R1+R2 propagates into downstream final circuit Zs
    res_tns = run_phase1_pipeline(
        building,
        overrides=DesignOverrides(earthing_system=EarthingSystem.TN_S, ze_or_ra_ohm=0.35),
    )
    assert len(res_tns.sub_mains) == 1
    sm = res_tns.sub_mains[0]
    assert sm.rcd_selective_type_s is False
    assert sm.cumulative_vd_pass is True
    assert sm.cumulative_lighting_vd_pct <= 3.0

    # Lighting final circuits must have vd_limit_pct < 3.0 (reduced by sub-main ΔV)
    light_ckts = [c for c in res_tns.circuits if c.spec.category == CircuitCategory.LIGHTING]
    assert light_ckts
    for lc in light_ckts:
        assert lc.spec.vd_limit_pct < 3.0
        # TN-S downstream Zs includes Ze (0.35) + sub-main (R1+R2) + final circuit (R1+R2)
        expected_zs = round(0.35 + sm.r1_plus_r2_ohm + lc.r1_plus_r2_ohm, 3)
        assert abs(lc.zs_ohm - expected_zs) <= 0.01

    # High TT earth electrode resistance (600 Ω) triggers BLOCKER on 100 mA Type S RCD (60V > 50V)
    res_bad_tt = run_phase1_pipeline(
        building,
        overrides=DesignOverrides(earthing_system=EarthingSystem.TT, ze_or_ra_ohm=600.0),
    )
    sm_bad = res_bad_tt.sub_mains[0]
    assert sm_bad.earth_fault_pass is False
    assert any(
        f.code == "SUBMAIN_TT_TOUCH_VOLTAGE_EXCEEDED" and f.severity == ReviewSeverity.BLOCKER
        for f in res_bad_tt.review_flags
    )
