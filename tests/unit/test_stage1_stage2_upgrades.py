"""Comprehensive regression tests for Stage 1 (Pilot) and Stage 2 (Full Cyprus MEP) upgrades."""

from __future__ import annotations

import io
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest
from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.ingest import dwg_bridge
from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    Point2D,
    Room,
    RoomType,
    SubBoardSpec,
    WallSegment,
)
from cyelec.models.electrical import (
    BreakerCurve,
    CircuitCategory,
    CircuitSpec,
    CircuitTopology,
    EarthingSystem,
    ReviewSeverity,
)
from cyelec.pipeline import run_phase2_pipeline
from cyelec.render.layout_dxf import export_electrical_layout_dxf
from cyelec.solver.balancer import balance_three_phase_loads
from cyelec.solver.bs7671 import size_conduit_or_tray, solve_circuit
from cyelec.solver.submain import solve_submain
from cyelec.standards_loader import clear_standards_cache, load_standards
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[Point2D]:
    return [
        Point2D(x_mm=x0, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y1),
        Point2D(x_mm=x0, y_mm=y1),
    ]


# ----------------------------------------------------------------------
# Stage 1: Caching, Fast Balancer, Original DXF Underlay, API & UI Parity
# ----------------------------------------------------------------------
def test_load_standards_is_cached_and_sub_millisecond() -> None:
    clear_standards_cache()
    s1 = load_standards()
    t0 = time.perf_counter()
    s2 = load_standards()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert s1 is s2
    assert elapsed_ms < 5.0


def test_fast_three_phase_balancer_22_circuits_under_300ms() -> None:
    specs: list[CircuitSpec] = []
    loads_a = [
        4.2, 3.8, 13.5, 12.0, 14.2, 8.9, 9.1, 7.6, 6.4, 15.8,
        11.3, 18.0, 13.0, 9.5, 5.2, 6.8, 10.4, 8.2, 16.5, 7.1,
    ]
    for idx, ib in enumerate(loads_a, start=1):
        specs.append(
            CircuitSpec(
                circuit_id=f"C{idx}",
                description=f"1P Circuit {idx}",
                category=CircuitCategory.APPLIANCE,
                topology=CircuitTopology.RADIAL,
                room_ids=["R1"],
                connected_load_w=round(ib * 230.0, 1),
                diversity_factor=1.0,
                diversified_load_w=round(ib * 230.0, 1),
                power_factor=1.0,
                design_current_ib_a=ib,
                nominal_voltage_v=230.0,
                is_three_phase=False,
                length_m=18.0,
            )
        )
    for idx in (21, 22):
        specs.append(
            CircuitSpec(
                circuit_id=f"C{idx}",
                description=f"3P Feeder {idx}",
                category=CircuitCategory.HVAC_3P,
                topology=CircuitTopology.RADIAL,
                room_ids=["R1"],
                connected_load_w=20000.0,
                diversity_factor=1.0,
                diversified_load_w=20000.0,
                power_factor=0.9,
                design_current_ib_a=32.1,
                nominal_voltage_v=400.0,
                is_three_phase=True,
                length_m=22.0,
            )
        )

    t0 = time.perf_counter()
    res = balance_three_phase_loads(specs, imbalance_target_pct=10.0)
    elapsed_s = time.perf_counter() - t0
    assert elapsed_s < 0.35
    assert res.within_target is True
    assert res.imbalance_pct <= 2.5


def test_layout_dxf_preserves_original_architect_underlay_and_meter_units(tmp_path: Path) -> None:
    """Exported electrical_layout.dxf must preserve custom architect layers & meter coordinates."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6  # Meters
    msp = doc.modelspace()

    # 8 m x 5 m architectural drawing in METERS with a custom furniture layer
    msp.add_lwpolyline([(0, 0), (5, 0), (5, 5), (0, 5)], close=True, dxfattribs={"layer": "A-ROOM"})
    msp.add_lwpolyline([(5, 0), (8, 0), (8, 5), (5, 5)], close=True, dxfattribs={"layer": "A-ROOM"})
    msp.add_text("LIVING ROOM", dxfattribs={"layer": "A-TEXT", "insert": (2.5, 2.5)})
    msp.add_text("KITCHEN", dxfattribs={"layer": "A-TEXT", "insert": (6.5, 2.5)})
    # Custom architect furniture polyline that MUST survive on electrical_layout.dxf
    msp.add_lwpolyline(
        [(1.0, 1.0), (2.2, 1.0), (2.2, 1.8), (1.0, 1.8)],
        close=True,
        dxfattribs={"layer": "A-FURN-CUSTOM"},
    )
    src_dxf = tmp_path / "architect_meters.dxf"
    doc.saveas(src_dxf)

    p2 = run_phase2_pipeline(src_dxf)
    assert p2.design.building.source_unit_scale_to_mm == pytest.approx(1000.0)

    out_dxf = tmp_path / "electrical_layout.dxf"
    export_electrical_layout_dxf(p2, out_dxf)

    out_doc = ezdxf.readfile(str(out_dxf))
    out_msp = out_doc.modelspace()
    custom_furn = [e for e in out_msp if e.dxf.layer == "A-FURN-CUSTOM"]
    assert len(custom_furn) == 1

    e_circles = [
        e
        for e in out_msp
        if e.dxf.layer in ("E-LITE", "E-SWIT") and e.dxftype() == "CIRCLE"
    ]
    assert len(e_circles) >= 2
    # Coordinates & symbol radii must be scaled back into meters (~0..8 m), NOT raw mm (0..8000)
    for c in e_circles:
        assert -2.0 <= float(c.dxf.center.x) <= 12.0
        assert -2.0 <= float(c.dxf.center.y) <= 10.0
        assert 0.01 <= float(c.dxf.radius) <= 0.5


def test_prod_ui_hides_docs_and_multifloor_bundle_returns_zip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CYELEC_DISABLE_OPENAPI", "1")
    app = create_app(db_path=tmp_path / "stage1.db")
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/docs"' not in home.text
    assert "open_office" in home.text
    assert "lift_shaft" in home.text

    guide = client.get("/guide")
    assert guide.status_code == 200
    assert 'href="/docs"' not in guide.text

    gf_dxf = tmp_path / "GF.dxf"
    f1_dxf = tmp_path / "1F.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=gf_dxf)
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=f1_dxf)

    resp = client.post(
        "/v1/solve/dxf/multifloor/bundle",
        files=[
            ("files", ("GF.dxf", gf_dxf.read_bytes(), "application/dxf")),
            ("files", ("1F.dxf", f1_dxf.read_bytes(), "application/dxf")),
        ],
        data={"floors_json": '["GF", "1F"]', "submain_lengths_json": '{"GF": 6.0, "1F": 18.0}'},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "load_schedule.xlsx" in names
        assert "single_line_diagram.svg" in names
        assert "electrical_layout.dxf" in names
        assert "manifest.json" in names


# ----------------------------------------------------------------------
# Stage 2: Heavy XLPE/MCCB, Type D, Commercial MEP, Adiabatic, DWG Bridge
# ----------------------------------------------------------------------
def test_heavy_commercial_submain_200a_xlpe_mccb_and_attenuated_ipf() -> None:
    std = load_standards()
    sb = SubBoardSpec(
        board_id="DB-CHILLER",
        floor="ROOF",
        parent_board_id="MDB",
        location_mm=Point2D(x_mm=10_000.0, y_mm=10_000.0),
        submain_length_m=28.0,
        is_three_phase=True,
        ambient_temp_c=40,
        installation_method="C",
    )
    # 125 kVA diversified 3P load -> ~180 A/ph -> must size 200 A MCCB + XLPE >= 70 mm²
    heavy_specs = [
        CircuitSpec(
            circuit_id=f"H{i}",
            board_id="DB-CHILLER",
            description=f"Chiller Unit #{i}",
            category=CircuitCategory.HVAC_3P,
            topology=CircuitTopology.RADIAL,
            room_ids=["R1"],
            connected_load_w=37_500.0,
            diversity_factor=1.0,
            diversified_load_w=37_500.0,
            power_factor=0.90,
            design_current_ib_a=60.14,
            nominal_voltage_v=400.0,
            is_three_phase=True,
            length_m=15.0,
        )
        for i in range(1, 4)
    ]

    sm = solve_submain(
        sb,
        heavy_specs,
        std,
        earthing_system=EarthingSystem.TN_S,
        ze_or_ra_ohm=0.06,
        origin_ipf_ka=15.0,
    )
    assert sm.overall_pass is True
    assert sm.breaker_in_a >= 200
    assert "MCCB" in sm.device_label
    assert sm.cable_mm2 >= 50.0
    assert sm.downstream_ipf_ka is not None
    assert 0.5 < sm.downstream_ipf_ka < 15.0


def test_commercial_industrial_building_zero_residential_hallucinations() -> None:
    """Commercial/industrial buildings must emit commercial MEP circuits without domestic ones."""
    rooms = [
        Room(
            id="R1",
            raw_label="Open Office Floor",
            normalized_type=RoomType.OPEN_OFFICE,
            polygon_mm=_rect(0, 0, 10_000, 8_000),
            area_m2=80.0,
        ),
        Room(
            id="R2",
            raw_label="Server Room",
            normalized_type=RoomType.SERVER_ROOM,
            polygon_mm=_rect(10_000, 0, 14_000, 4_000),
            area_m2=16.0,
        ),
        Room(
            id="R3",
            raw_label="Staff Kitchenette",
            normalized_type=RoomType.KITCHEN,
            polygon_mm=_rect(10_000, 4_000, 14_000, 8_000),
            area_m2=16.0,
        ),
        Room(
            id="R4",
            raw_label="Plant Room",
            normalized_type=RoomType.PLANT_ROOM,
            polygon_mm=_rect(14_000, 0, 20_000, 4_000),
            area_m2=24.0,
        ),
        Room(
            id="R5",
            raw_label="Passenger Lift Shaft",
            normalized_type=RoomType.LIFT_SHAFT,
            polygon_mm=_rect(14_000, 4_000, 17_000, 8_000),
            area_m2=12.0,
        ),
        Room(
            id="R6",
            raw_label="Warehouse Bay",
            normalized_type=RoomType.WAREHOUSE,
            polygon_mm=_rect(20_000, 0, 30_000, 8_000),
            area_m2=80.0,
        ),
    ]
    building = BuildingModel(
        project_name="Cyprus Mixed Commercial & Logistics Hub",
        building_type=BuildingType.WORKSHOP,
        total_floor_area_m2=228.0,
        rooms=rooms,
        walls=[
            WallSegment(
                id="W1",
                start=Point2D(x_mm=0, y_mm=0),
                end=Point2D(x_mm=30_000, y_mm=0),
                is_exterior=True,
            )
        ],
        db_anchor=DistributionBoardAnchor(id="MDB1", location_mm=Point2D(x_mm=15_000, y_mm=2_000)),
    )

    p2 = run_phase2_pipeline(building)
    circuits = p2.design.circuits
    cats = {c.spec.category for c in circuits}
    descs = [c.spec.description for c in circuits]

    # Zero residential hallucinations
    assert all("Night / Sleeping Zone" not in d for d in descs)
    assert CircuitCategory.COOKER not in cats
    assert CircuitCategory.OVEN not in cats
    assert CircuitCategory.WATER_HEATER not in cats

    # Commercial mandatory & heavy 3P circuits present
    assert CircuitCategory.EMERGENCY_LIGHTING in cats
    assert CircuitCategory.FIRE_ALARM in cats
    assert CircuitCategory.UPS_SERVER in cats
    assert CircuitCategory.LIFT in cats
    assert CircuitCategory.HVAC_3P in cats
    assert CircuitCategory.SOCKET_RADIAL in cats

    # Type D curve used on lift & chiller feeders and all BS 7671 checks pass
    lift_c = next(c for c in circuits if c.spec.category == CircuitCategory.LIFT)
    assert lift_c.breaker_curve == BreakerCurve.D
    assert lift_c.spec.is_three_phase is True
    assert all(c.overall_pass and c.live_adiabatic_pass for c in circuits)
    assert all(bool(c.recommended_containment) for c in circuits)

    # BOQ includes BS 5266 emergency luminaires and BS 5839 fire alarm spur
    assert p2.design.boq is not None
    boq_descs = " ".join(it.description_en for it in p2.design.boq.items)
    assert "BS 5266" in boq_descs
    assert "BS 5839" in boq_descs


def test_conduit_fill_sizing_and_live_adiabatic_check() -> None:
    assert size_conduit_or_tray(1.5, is_three_phase=False, grouping_count=1) == "20mm PVC"
    assert size_conduit_or_tray(2.5, is_three_phase=False, grouping_count=4) in (
        "25mm PVC",
        "32mm PVC",
    )
    assert size_conduit_or_tray(95.0, is_three_phase=True) == "Galv. Cable Tray"

    std = load_standards()
    spec = CircuitSpec(
        circuit_id="C-LIFT",
        description="Heavy 3P Motor Feeder",
        category=CircuitCategory.LIFT,
        topology=CircuitTopology.RADIAL,
        room_ids=["R1"],
        connected_load_w=45_000.0,
        diversity_factor=1.0,
        diversified_load_w=45_000.0,
        power_factor=0.90,
        design_current_ib_a=72.17,
        nominal_voltage_v=400.0,
        is_three_phase=True,
        length_m=30.0,
        insulation_type="xlpe_90c",
        preferred_curve=BreakerCurve.MCCB,
        min_breaker_in_a=80,
        min_cable_mm2=25.0,
    )
    solved = solve_circuit(spec, std, earthing_system=EarthingSystem.TN_S, ze_or_ra_ohm=0.20)
    assert solved.overall_pass is True
    assert solved.live_adiabatic_pass is True
    assert solved.breaker_in_a >= 80
    assert "MCCB" in solved.device_label


def test_wet_room_missing_fixture_geometry_emits_section_701_warning() -> None:
    building = BuildingModel(
        project_name="No-Fixture Bath Check",
        building_type=BuildingType.APARTMENT_1BED,
        total_floor_area_m2=40.0,
        rooms=[
            Room(
                id="R1",
                raw_label="Living Room",
                normalized_type=RoomType.LIVING_ROOM,
                polygon_mm=_rect(0, 0, 6_000, 5_000),
                area_m2=30.0,
            ),
            Room(
                id="R2",
                raw_label="Main Bathroom",
                normalized_type=RoomType.BATHROOM,
                polygon_mm=_rect(6_000, 0, 8_000, 5_000),
                area_m2=10.0,
                is_wet_room=True,
                fixtures=[],
            ),
        ],
        db_anchor=DistributionBoardAnchor(id="DB1", location_mm=Point2D(x_mm=1_000, y_mm=1_000)),
    )
    p2 = run_phase2_pipeline(building)
    assert any(
        f.code == "WET_ROOM_NO_FIXTURE_GEOMETRY" and f.severity == ReviewSeverity.WARNING
        for f in p2.design.review_flags
    )


def test_dxf_ingest_auto_detects_commercial_office_building_type(tmp_path: Path) -> None:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (8000, 0), (8000, 6000), (0, 6000)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    msp.add_lwpolyline(
        [(8000, 0), (12000, 0), (12000, 6000), (8000, 6000)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    msp.add_text("OPEN OFFICE", dxfattribs={"layer": "A-TEXT", "insert": (4000, 3000)})
    msp.add_text("SERVER ROOM", dxfattribs={"layer": "A-TEXT", "insert": (10000, 3000)})
    dxf_path = tmp_path / "office_auto.dxf"
    doc.saveas(dxf_path)

    model, _ = ingest_dxf(dxf_path)
    assert model.building_type == BuildingType.OFFICE
    assert {r.normalized_type for r in model.rooms} == {RoomType.OPEN_OFFICE, RoomType.SERVER_ROOM}


def test_dwg_bridge_missing_cli_and_isolated_subprocess_conversion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_dwg = tmp_path / "client_plan.dwg"
    fake_dwg.write_bytes(b"AC1032-MOCK-DWG")

    # 1. Missing CLI produces actionable error without crashing
    monkeypatch.setattr(dwg_bridge.shutil, "which", lambda _tool: None)
    assert dwg_bridge.is_dwg_converter_available() is False
    with pytest.raises(ValueError, match="ODAFileConverter"):
        dwg_bridge.convert_dwg_to_dxf(fake_dwg, tmp_path / "out")

    # 2. Mocked dwg2dxf subprocess converts DWG -> real DXF with shell=False
    real_dxf = tmp_path / "template.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=real_dxf)
    seen_shell: list[bool] = []

    def _fake_which(tool: str) -> str | None:
        return "/usr/bin/dwg2dxf" if tool == "dwg2dxf" else None

    def _fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        seen_shell.append(bool(kwargs.get("shell", False)))
        out_idx = cmd.index("-o") + 1
        Path(cmd[out_idx]).write_bytes(real_dxf.read_bytes())
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(dwg_bridge.shutil, "which", _fake_which)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    p2 = run_phase2_pipeline(fake_dwg)
    assert seen_shell == [False]
    assert any(f.code == "DWG_CONVERTED_VIA_SUBPROCESS" for f in p2.design.review_flags)
    assert len(p2.design.circuits) >= 10
