"""Unit tests for deterministic BS 7671 cable sizing, ring finals, ΔV, TT RCD, TN Zs, and CPC."""

from __future__ import annotations

import pytest

from cyelec.models.electrical import (
    BreakerCurve,
    CircuitCategory,
    CircuitOverride,
    CircuitSpec,
    CircuitTopology,
    EarthingSystem,
    ReviewSeverity,
)
from cyelec.solver.bs7671 import solve_circuit
from cyelec.standards_loader import load_standards


def test_cyprus_40c_ungrouped_ring_final_passes_on_2_5mm2() -> None:
    """At 40°C Method B ungrouped (Ca=0.87, Cg=1.0), 2.5 mm² Iz = 24 * 0.87 = 20.88 A >= 20 A."""
    standards = load_standards()
    spec = CircuitSpec(
        circuit_id="C3",
        description="Socket Ring Final - General",
        category=CircuitCategory.SOCKET_RING,
        topology=CircuitTopology.RING_FINAL,
        connected_load_w=4600.0,
        diversity_factor=0.85,
        diversified_load_w=3910.0,
        power_factor=0.95,
        design_current_ib_a=17.89,
        length_m=48.0,
        installation_method="B",
        ambient_temp_c=40,
        grouping_count=1,
        preferred_curve=BreakerCurve.B,
        min_breaker_in_a=32,
        min_cable_mm2=2.5,
        rcd_protected=True,
        rcd_idn_ma=30,
        vd_limit_pct=5.0,
    )
    solved = solve_circuit(spec, standards, earthing_system=EarthingSystem.TT, ze_or_ra_ohm=80.0)

    assert solved.breaker_in_a == 32
    assert solved.cable_mm2 == pytest.approx(2.5)
    assert solved.ca == pytest.approx(0.87)
    assert solved.cg == pytest.approx(1.0)
    assert solved.iz_derated_a == pytest.approx(20.88, abs=0.01)
    assert solved.required_iz_a == pytest.approx(20.0)
    assert solved.thermal_pass is True
    # Ring midpoint L/4 voltage drop: 18 * 17.89 * (48 / 4) / 1000 = 3.864 V
    assert solved.voltage_drop_v == pytest.approx(3.864, abs=0.01)
    assert solved.voltage_drop_pass is True
    assert solved.overall_pass is True
    assert solved.upsize_reasons == []


def test_cyprus_40c_grouped_ring_final_upsizes_to_4mm2() -> None:
    """At 40°C Method B grouped (N=2, Cg=0.80), 2.5 mm² Iz=16.70 A < 20 A -> upsizes to 4.0 mm²."""
    standards = load_standards()
    spec = CircuitSpec(
        circuit_id="C3",
        description="Socket Ring Final - Kitchen (Grouped)",
        category=CircuitCategory.SOCKET_RING,
        topology=CircuitTopology.RING_FINAL,
        connected_load_w=6000.0,
        diversity_factor=0.85,
        diversified_load_w=5100.0,
        power_factor=0.95,
        design_current_ib_a=23.34,
        length_m=44.0,
        installation_method="B",
        ambient_temp_c=40,
        grouping_count=2,
        preferred_curve=BreakerCurve.B,
        min_breaker_in_a=32,
        min_cable_mm2=2.5,
        rcd_protected=True,
        rcd_idn_ma=30,
        vd_limit_pct=5.0,
    )
    solved = solve_circuit(spec, standards, earthing_system=EarthingSystem.TT, ze_or_ra_ohm=80.0)

    assert solved.cable_mm2 == pytest.approx(4.0)
    assert solved.iz_derated_a == pytest.approx(32.0 * 0.87 * 0.80, abs=0.02)
    assert solved.thermal_pass is True
    assert solved.overall_pass is True
    assert "thermal" in solved.upsize_reasons
    assert any(f.code == "RING_LEG_UPSIZED" for f in solved.flags)


def test_lighting_3pct_voltage_drop_forces_upsize() -> None:
    """Long 1.5 mm² lighting circuit exceeding 3.0% ΔV deterministically upsizes to 2.5 mm²."""
    standards = load_standards()
    # Ib = 5.0 A, L = 55 m -> on 1.5 mm²: 29 * 5 * 55 / 1000 = 7.975 V = 3.47% (> 3.0%)
    # On 2.5 mm²: 18 * 5 * 55 / 1000 = 4.95 V = 2.15% (<= 3.0%)
    spec = CircuitSpec(
        circuit_id="C1",
        description="Lighting - Long Corridor & Garden",
        category=CircuitCategory.LIGHTING,
        topology=CircuitTopology.RADIAL,
        connected_load_w=1650.0,
        diversity_factor=0.66,
        diversified_load_w=1089.0,
        power_factor=0.95,
        design_current_ib_a=5.0,
        length_m=55.0,
        installation_method="B",
        ambient_temp_c=40,
        grouping_count=1,
        preferred_curve=BreakerCurve.B,
        min_breaker_in_a=6,
        min_cable_mm2=1.5,
        rcd_protected=True,
        rcd_idn_ma=30,
        vd_limit_pct=3.0,
    )
    solved = solve_circuit(spec, standards, earthing_system=EarthingSystem.TT)

    assert solved.cable_mm2 == pytest.approx(2.5)
    assert solved.voltage_drop_pct == pytest.approx(2.152, abs=0.01)
    assert solved.voltage_drop_pass is True
    assert "voltage_drop" in solved.upsize_reasons
    assert any(f.code == "VD_UPSIZED" for f in solved.flags)


def test_tt_missing_rcd_emits_blocker() -> None:
    """TT earthing without RCD protection must fail earth fault check with BLOCKER flag."""
    standards = load_standards()
    spec = CircuitSpec(
        circuit_id="C9",
        description="Unprotected Radial on TT",
        category=CircuitCategory.APPLIANCE,
        topology=CircuitTopology.RADIAL,
        connected_load_w=2200.0,
        diversity_factor=1.0,
        diversified_load_w=2200.0,
        power_factor=0.95,
        design_current_ib_a=10.07,
        length_m=15.0,
        ambient_temp_c=40,
        rcd_protected=False,
        rcd_idn_ma=None,
        vd_limit_pct=5.0,
    )
    solved = solve_circuit(spec, standards, earthing_system=EarthingSystem.TT, ze_or_ra_ohm=80.0)

    assert solved.earth_fault_method == "TT_RCD"
    assert solved.earth_fault_pass is False
    assert solved.overall_pass is False
    blockers = [f for f in solved.flags if f.code == "TT_MISSING_RCD"]
    assert len(blockers) == 1
    assert blockers[0].severity == ReviewSeverity.BLOCKER


def test_tt_electrode_stability_warning_and_touch_voltage_blocker() -> None:
    standards = load_standards()
    spec = CircuitSpec(
        circuit_id="C10",
        description="Water Heater",
        category=CircuitCategory.WATER_HEATER,
        topology=CircuitTopology.RADIAL,
        connected_load_w=3000.0,
        diversity_factor=1.0,
        diversified_load_w=3000.0,
        power_factor=1.0,
        design_current_ib_a=13.04,
        length_m=15.0,
        ambient_temp_c=40,
        rcd_protected=True,
        rcd_idn_ma=30,
        vd_limit_pct=5.0,
    )
    # Ra = 250 Ω > 200 Ω warning, but touch voltage ~ 7.5 V <= 50 V -> PASS with WARNING
    warn_res = solve_circuit(spec, standards, earthing_system=EarthingSystem.TT, ze_or_ra_ohm=250.0)
    assert warn_res.earth_fault_pass is True
    assert any(
        f.code == "TT_ELECTRODE_UNSTABLE" and f.severity == ReviewSeverity.WARNING
        for f in warn_res.flags
    )

    # Ra = 1800 Ω * 0.03 A = 54 V > 50 V -> BLOCKER
    fail_res = solve_circuit(
        spec, standards, earthing_system=EarthingSystem.TT, ze_or_ra_ohm=1800.0
    )
    assert fail_res.earth_fault_pass is False
    assert any(
        f.code == "TT_TOUCH_VOLTAGE_EXCEEDED" and f.severity == ReviewSeverity.BLOCKER
        for f in fail_res.flags
    )


def test_tn_s_zs_verification_and_upsizing() -> None:
    """TN-S checks Zs <= Table 41.3 Zs_max and upsizes cable when Zs exceeds limit."""
    standards = load_standards()
    # Type C 16A has Zs_max = 1.37 Ω. With Ze = 0.80 Ω, allowed R1+R2 = 0.57 Ω.
    # For 2.5/2.5 mm² at 35 m: R1+R2 = (7.41+7.41)*35/1000 * 1.20 = 0.622 Ω -> Zs = 1.422 Ω > 1.37 Ω
    # Upsized to 4.0/4.0 mm²: R1+R2 = (4.61+4.61)*35/1000 * 1.20 = 0.387 Ω -> Zs = 1.187 Ω <= 1.37 Ω
    spec = CircuitSpec(
        circuit_id="C5",
        description="Split A/C - Remote Room",
        category=CircuitCategory.SPLIT_AC,
        topology=CircuitTopology.RADIAL,
        connected_load_w=2000.0,
        diversity_factor=0.85,
        diversified_load_w=1700.0,
        power_factor=0.90,
        design_current_ib_a=8.21,
        length_m=35.0,
        ambient_temp_c=40,
        preferred_curve=BreakerCurve.C,
        min_breaker_in_a=16,
        min_cable_mm2=2.5,
        rcd_protected=True,
        rcd_idn_ma=30,
        vd_limit_pct=5.0,
    )
    solved = solve_circuit(spec, standards, earthing_system=EarthingSystem.TN_S, ze_or_ra_ohm=0.80)

    assert solved.earth_fault_method == "TN_ZS"
    assert solved.cable_mm2 == pytest.approx(4.0)
    assert solved.zs_max_ohm == pytest.approx(1.37)
    assert solved.zs_ohm <= solved.zs_max_ohm
    assert solved.earth_fault_pass is True
    assert "zs_impedance" in solved.upsize_reasons


def test_adiabatic_cpc_check_flags_undersized_override() -> None:
    """Reg 543.1.3: 63A Type C (I²t=78,000, k=115 -> Smin=2.43 mm²) fails with 1.5 mm² CPC."""
    standards = load_standards()
    spec = CircuitSpec(
        circuit_id="C14",
        description="Submain Radial",
        category=CircuitCategory.OTHER,
        topology=CircuitTopology.RADIAL,
        connected_load_w=12000.0,
        diversity_factor=1.0,
        diversified_load_w=12000.0,
        power_factor=0.95,
        design_current_ib_a=54.92,
        length_m=10.0,
        ambient_temp_c=40,
        preferred_curve=BreakerCurve.C,
        min_breaker_in_a=63,
        min_cable_mm2=16.0,
        rcd_protected=True,
        rcd_idn_ma=30,
        vd_limit_pct=5.0,
    )
    override = CircuitOverride(circuit_id="C14", cable_mm2=16.0, cpc_mm2=1.5)
    solved = solve_circuit(
        spec,
        standards,
        earthing_system=EarthingSystem.TT,
        override=override,
    )

    assert solved.cpc_min_mm2 == pytest.approx(2.429, abs=0.01)
    assert solved.cpc_adiabatic_pass is False
    assert solved.overall_pass is False
    assert any(
        f.code == "CPC_ADIABATIC_FAIL" and f.severity == ReviewSeverity.BLOCKER
        for f in solved.flags
    )
