"""Unit tests for Cyprus EAC compliance solver (1P/3P, PV > 5 kVA, CT metering, substations)."""

from __future__ import annotations

import pytest

from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    Point2D,
    Room,
    RoomType,
)
from cyelec.models.electrical import (
    CircuitCategory,
    CircuitSpec,
    CircuitTopology,
    EacProfile,
    EarthingSystem,
    PhaseBalanceResult,
    ReviewSeverity,
    SolvedCircuit,
)
from cyelec.solver.bs7671 import solve_circuit
from cyelec.solver.eac_compliance import evaluate_eac_compliance
from cyelec.standards_loader import load_standards


def _make_building(building_type: BuildingType, pv_kva: float = 0.0) -> BuildingModel:
    return BuildingModel(
        project_name="Test Building",
        building_type=building_type,
        total_floor_area_m2=50.0,
        rooms=[
            Room(
                id="R1",
                raw_label="ΣΑΛΟΝΙ",
                normalized_type=RoomType.LIVING_ROOM,
                polygon_mm=[
                    Point2D(x_mm=0, y_mm=0),
                    Point2D(x_mm=10000, y_mm=0),
                    Point2D(x_mm=10000, y_mm=5000),
                    Point2D(x_mm=0, y_mm=5000),
                ],
                area_m2=50.0,
            )
        ],
        db_anchor=DistributionBoardAnchor(id="DB1", location_mm=Point2D(x_mm=500, y_mm=500)),
        pv_capacity_kva=pv_kva,
    )


def _dummy_solved_circuit(cid: str, watts: float) -> SolvedCircuit:
    standards = load_standards()
    spec = CircuitSpec(
        circuit_id=cid,
        description=f"Load {cid}",
        category=CircuitCategory.APPLIANCE,
        topology=CircuitTopology.RADIAL,
        connected_load_w=watts,
        diversity_factor=1.0,
        diversified_load_w=watts,
        power_factor=1.0,
        design_current_ib_a=min(watts / 230.0, 60.0),
        length_m=10.0,
        ambient_temp_c=40,
        rcd_protected=True,
        rcd_idn_ma=30,
    )
    return solve_circuit(spec, standards, earthing_system=EarthingSystem.TT)


def test_single_phase_allowed_for_1bed_low_load_and_small_pv() -> None:
    standards = load_standards()
    building = _make_building(BuildingType.APARTMENT_1BED, pv_kva=4.5)
    circuits = [_dummy_solved_circuit("C1", 6000.0)]

    res = evaluate_eac_compliance(
        building, circuits, standards, eac_profile=EacProfile.EAC_17TH_A3
    )
    assert res.supply_phases == 1
    assert res.mandatory_three_phase is False
    assert res.bathroom_socket_exclusion_m == pytest.approx(3.0)
    assert res.ct_metering_required is False
    assert res.substation_warning is False


def test_pv_above_5kva_forces_three_phase() -> None:
    standards = load_standards()
    building = _make_building(BuildingType.APARTMENT_1BED, pv_kva=6.0)
    circuits = [_dummy_solved_circuit("C1", 6000.0)]

    res = evaluate_eac_compliance(
        building, circuits, standards, eac_profile=EacProfile.EAC_18TH_A2
    )
    assert res.supply_phases == 3
    assert res.mandatory_three_phase is True
    assert res.bathroom_socket_exclusion_m == pytest.approx(2.5)
    assert any(f.code == "EAC_PV_THREE_PHASE_MANDATORY" for f in res.flags)


def test_ct_metering_and_substation_warning_and_blocker_thresholds() -> None:
    standards = load_standards()
    building = _make_building(BuildingType.OFFICE)

    # 110 kVA diversified, 160 A peak phase -> CT metering + Substation warning (>=100 kVA)
    circuits_110 = [_dummy_solved_circuit(f"C{i}", 11000.0) for i in range(10)]
    pb_110 = PhaseBalanceResult(
        assignments={},
        phase_currents_a={"L1": 160.0, "L2": 159.0, "L3": 159.0},
        phase_power_kw={"L1": 36.8, "L2": 36.6, "L3": 36.6},
        max_phase_current_a=160.0,
        min_phase_current_a=159.0,
        mean_phase_current_a=159.33,
        imbalance_a=1.0,
        imbalance_pct=0.63,
        within_target=True,
        solver_status="optimal",
    )
    res_110 = evaluate_eac_compliance(building, circuits_110, standards, phase_balance=pb_110)
    assert res_110.ct_metering_required is True
    assert res_110.substation_warning is True
    assert res_110.substation_consultation_required is False
    assert any(
        f.code == "EAC_CT_METERING_REQUIRED" and f.severity == ReviewSeverity.WARNING
        for f in res_110.flags
    )
    assert any(
        f.code == "EAC_SUBSTATION_WARNING" and f.severity == ReviewSeverity.WARNING
        for f in res_110.flags
    )

    # 165 kVA diversified (>=150 kVA) -> Substation consultation BLOCKER
    circuits_165 = [_dummy_solved_circuit(f"C{i}", 11000.0) for i in range(15)]
    pb_165 = pb_110.model_copy(update={"max_phase_current_a": 240.0})
    res_165 = evaluate_eac_compliance(building, circuits_165, standards, phase_balance=pb_165)
    assert res_165.substation_consultation_required is True
    assert any(
        f.code == "EAC_SUBSTATION_CONSULTATION_BLOCKER" and f.severity == ReviewSeverity.BLOCKER
        for f in res_165.flags
    )
