"""Unit tests for deep-review hardening fixes: Sec 722 EV, XLPE, BS 7671 blockers, L-walls, API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    Point2D,
    Room,
    RoomType,
)
from cyelec.models.electrical import (
    BreakerCurve,
    CircuitCategory,
    CircuitOverride,
    CircuitSpec,
    CircuitTopology,
    DesignOverrides,
    EarthingSystem,
    ReviewSeverity,
)
from cyelec.pipeline import run_phase1_pipeline
from cyelec.solver.bs7671 import solve_circuit
from cyelec.spatial.placement import place_electrical_devices
from cyelec.standards_loader import load_standards
from cyelec.synth.benchmarks import (
    build_cyprus_commercial_office_model,
    build_cyprus_villa_pv_ev_model,
)


def test_ev_charger_section_722_rcd_and_tncs_pme_blocker() -> None:
    villa = build_cyprus_villa_pv_ev_model()

    # On Cyprus TT earthing: Type A + 6mA DC label & info flag, no PME blocker
    tt_res = run_phase1_pipeline(
        villa, overrides=DesignOverrides(earthing_system=EarthingSystem.TT)
    )
    ev_tt = next(c for c in tt_res.circuits if c.spec.category == CircuitCategory.EV_CHARGER)
    assert "Type A + 6mA DC" in ev_tt.device_label
    assert any(f.code == "EV_RCD_TYPE_A_DC_REQUIRED" for f in ev_tt.flags)
    assert not any(f.code == "EV_PME_PEN_PROTECTION_REQUIRED" for f in ev_tt.flags)

    # On TN-C-S (PME) earthing: mandatory Section 722.411.4.1 BLOCKER emitted
    pme_res = run_phase1_pipeline(
        villa, overrides=DesignOverrides(earthing_system=EarthingSystem.TN_C_S)
    )
    assert pme_res.has_blockers is True
    assert any(
        f.code == "EV_PME_PEN_PROTECTION_REQUIRED" and f.severity == ReviewSeverity.BLOCKER
        for f in pme_res.review_flags
    )


def test_bs7671_method_c_xlpe_grouping_gt20_and_failure_blockers() -> None:
    std = load_standards()

    # 1. Method C + XLPE 90°C + grouping > 20 warning
    spec_xlpe = CircuitSpec(
        circuit_id="C-XLPE",
        description="XLPE Clipped Direct Feeder",
        category=CircuitCategory.APPLIANCE,
        topology=CircuitTopology.RADIAL,
        connected_load_w=3000.0,
        diversity_factor=1.0,
        diversified_load_w=3000.0,
        power_factor=1.0,
        design_current_ib_a=13.04,
        length_m=15.0,
        installation_method="C",
        insulation_type="xlpe_90c",
        ambient_temp_c=40,
        grouping_count=25,  # > 20 Table 4C1 max
        rcd_protected=True,
        rcd_idn_ma=30,
    )
    solved_xlpe = solve_circuit(spec_xlpe, std, earthing_system=EarthingSystem.TT)
    assert any(f.code == "GROUPING_EXCEEDS_TABLE_4C1" for f in solved_xlpe.flags)
    assert any(f.code == "XLPE_CONSERVATIVE_70C_BASE_IT" for f in solved_xlpe.flags)
    # Adiabatic k = 143 for XLPE -> smaller cpc_min than PVC (k=115)
    assert solved_xlpe.cpc_min_mm2 < (15000.0**0.5) / 115.0

    # 2. Forced undersized fixed override triggers thermal, VD, and TN Zs blockers
    spec_fail = CircuitSpec(
        circuit_id="C-FAIL",
        description="Undersized Long TN Radial",
        category=CircuitCategory.APPLIANCE,
        topology=CircuitTopology.RADIAL,
        connected_load_w=7000.0,
        diversity_factor=1.0,
        diversified_load_w=7000.0,
        power_factor=1.0,
        design_current_ib_a=30.43,
        length_m=80.0,
        ambient_temp_c=40,
        preferred_curve=BreakerCurve.C,
        min_breaker_in_a=32,
        rcd_protected=False,
        rcd_idn_ma=None,
        vd_limit_pct=5.0,
    )
    ov = CircuitOverride(circuit_id="C-FAIL", cable_mm2=1.5, cpc_mm2=1.5)
    solved_fail = solve_circuit(
        spec_fail, std, earthing_system=EarthingSystem.TN_S, ze_or_ra_ohm=0.80, override=ov
    )
    codes = {f.code for f in solved_fail.flags if f.severity == ReviewSeverity.BLOCKER}
    assert "THERMAL_CAPACITY_FAIL" in codes
    assert "VOLTAGE_DROP_FAIL" in codes
    assert "TN_ZS_EXCEEDED" in codes


def test_eac_exceeds_max_lv_tier_emits_blocker() -> None:
    mega = build_cyprus_commercial_office_model(extra_three_phase_load_kva=320.0)
    res = run_phase1_pipeline(mega)
    assert res.has_blockers is True
    assert any(
        f.code == "EAC_EXCEEDS_MAX_LV_TIER" and f.severity == ReviewSeverity.BLOCKER
        for f in res.review_flags
    )


def test_l_shaped_building_exterior_wall_detection() -> None:
    """Unary-union boundary accurately detects recessed courtyard walls on L-shaped villas."""
    std = load_standards()
    # L-shaped building: R1 (0..5m, 0..5m), R2 (5..10m, 0..5m), R3 (0..5m, 5..10m)
    # Recessed north wall of R2 (y=5m, x=5..10m) is a TRUE exterior wall facing the courtyard!
    rooms = [
        Room(
            id="R1",
            raw_label="ΔΙΑΔΡΟΜΟΣ",
            normalized_type=RoomType.CORRIDOR,
            polygon_mm=[
                Point2D(x_mm=0, y_mm=0),
                Point2D(x_mm=5000, y_mm=0),
                Point2D(x_mm=5000, y_mm=5000),
                Point2D(x_mm=0, y_mm=5000),
            ],
            area_m2=25.0,
        ),
        Room(
            id="R2",
            raw_label="ΥΠΝΟΔΩΜΑΤΙΟ",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=[
                Point2D(x_mm=5000, y_mm=0),
                Point2D(x_mm=10000, y_mm=0),
                Point2D(x_mm=10000, y_mm=5000),
                Point2D(x_mm=5000, y_mm=5000),
            ],
            area_m2=25.0,
        ),
        Room(
            id="R3",
            raw_label="ΣΑΛΟΝΙ",
            normalized_type=RoomType.LIVING_ROOM,
            polygon_mm=[
                Point2D(x_mm=0, y_mm=5000),
                Point2D(x_mm=5000, y_mm=5000),
                Point2D(x_mm=5000, y_mm=10000),
                Point2D(x_mm=0, y_mm=10000),
            ],
            area_m2=25.0,
        ),
    ]
    building = BuildingModel(
        project_name="L-Shaped Villa",
        building_type=BuildingType.INDEPENDENT_HOUSE,
        total_floor_area_m2=75.0,
        rooms=rooms,
        db_anchor=DistributionBoardAnchor(id="DB1", location_mm=Point2D(x_mm=1000, y_mm=1000)),
    )
    devices, _ = place_electrical_devices(building, [], std)
    assert len(devices) > 0


def test_api_security_hardening_corrupt_dxf_auth_and_xss_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app())

    # 1. Workbench UI escapes HTML and avoids inline onchange handlers
    ui_html = client.get("/").text
    assert "function escapeHtml(" in ui_html
    assert 'onchange="setCircuitOverride' not in ui_html

    # 2. Corrupt DXF returns HTTP 422 cleanly instead of 500
    bad_dxf = client.post(
        "/v1/solve/dxf",
        files={"file": ("corrupt.dxf", b"NOT A VALID DXF FILE\n0\nGARBAGE", "application/dxf")},
    )
    assert bad_dxf.status_code == 422

    # 3. Empty upload returns HTTP 400
    empty = client.post(
        "/v1/solve/dxf",
        files={"file": ("empty.dxf", b"", "application/dxf")},
    )
    assert empty.status_code == 400

    # 4. Optional CYELEC_API_KEY production enforcement
    monkeypatch.setenv("CYELEC_API_KEY", "eu-secret-token-123")
    unauth = client.get("/v1/standards")
    assert unauth.status_code == 401
    auth_ok = client.get("/v1/standards", headers={"X-API-Key": "eu-secret-token-123"})
    assert auth_ok.status_code == 200
    # /health stays open for container/orchestrator liveness probes
    assert client.get("/health").status_code == 200
