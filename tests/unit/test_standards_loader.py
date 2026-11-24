"""Unit tests for versioned BS 7671 & Cyprus EAC YAML standards loading and validation."""

from __future__ import annotations

import pytest

from cyelec.models.building import RoomType
from cyelec.models.electrical import BreakerCurve, EacProfile, EarthingSystem
from cyelec.standards_loader import load_standards, normalize_label


def test_load_standards_bundle_complete() -> None:
    bundle = load_standards()
    versions = bundle.versions_map()
    assert set(versions.keys()) == {
        "bs7671_cables",
        "bs7671_devices",
        "eac_cyprus_rules",
        "room_templates",
    }
    assert all(versions.values())


def test_bs7671_cable_constants_and_derating_factors() -> None:
    bundle = load_standards()
    cables = bundle.cables

    assert cables.constants.temp_resistance_multiplier_70c == pytest.approx(1.20)
    assert cables.constants.adiabatic_k_pvc_copper == pytest.approx(115.0)
    assert cables.constants.ring_final_min_iz_a == pytest.approx(20.0)

    # Cyprus ambient Ca values (BS 7671 Table 4B1)
    assert cables.get_ca(40, "pvc_70c") == pytest.approx(0.87)
    assert cables.get_ca(45, "pvc_70c") == pytest.approx(0.79)
    assert cables.get_ca(40, "xlpe_90c") == pytest.approx(0.91)
    assert cables.get_ca(45, "xlpe_90c") == pytest.approx(0.87)

    # Grouping & thermal insulation factors
    assert cables.get_cg(1) == (1.0, False)
    assert cables.get_cg(2) == (0.80, False)
    assert cables.get_cg(3) == (0.70, False)
    assert cables.get_cg(25) == (0.38, True)
    assert cables.get_ci(0) == pytest.approx(1.0)
    assert cables.get_ci(100) == pytest.approx(0.81)

    # Cable table coverage 1.5 - 35 mm²
    sizes = [c.size_mm2 for c in cables.sorted_cables()]
    assert sizes == [1.5, 2.5, 4.0, 6.0, 10.0, 16.0, 25.0, 35.0]
    c25 = cables.get_cable(2.5)
    assert c25.method_b_1p_a == pytest.approx(24.0)
    assert c25.method_c_1p_a == pytest.approx(27.0)
    assert c25.mv_per_a_per_m_1p == pytest.approx(18.0)


def test_bs7671_table_41_3_devices() -> None:
    bundle = load_standards()
    devs = bundle.devices

    assert devs.u0_v == pytest.approx(230.0)
    assert devs.cmin == pytest.approx(0.95)
    assert devs.standard_ratings_a == [6, 10, 16, 20, 25, 32, 40, 50, 63]
    assert devs.get_device(BreakerCurve.B, 32).zs_max_ohm == pytest.approx(1.37)
    assert devs.get_device(BreakerCurve.C, 16).zs_max_ohm == pytest.approx(1.37)
    assert devs.get_device(BreakerCurve.C, 32).zs_max_ohm == pytest.approx(0.68)


def test_eac_cyprus_dual_profiles_and_defaults() -> None:
    bundle = load_standards()
    eac = bundle.eac_rules

    assert eac.profiles[EacProfile.EAC_17TH_A3].bathroom_socket_exclusion_m == pytest.approx(3.0)
    assert eac.profiles[EacProfile.EAC_18TH_A2].bathroom_socket_exclusion_m == pytest.approx(2.5)
    assert eac.ambient_defaults_c.indoor_conduit_c == 40
    assert eac.ambient_defaults_c.roof_outdoor_c == 45
    assert eac.earthing.default_system == EarthingSystem.TT
    assert eac.earthing.tt_touch_voltage_limit_v == pytest.approx(50.0)
    assert eac.earthing.tt_electrode_stability_warning_ohm == pytest.approx(200.0)
    assert eac.voltage_drop_limits_pct.lighting == pytest.approx(3.0)
    assert eac.voltage_drop_limits_pct.power == pytest.approx(5.0)
    assert eac.supply_rules.pv_three_phase_threshold_kva == pytest.approx(5.0)
    assert eac.supply_rules.ct_metering_threshold_a_per_phase == pytest.approx(100.0)
    assert eac.supply_rules.substation_warning_kva == pytest.approx(100.0)
    assert eac.supply_rules.substation_blocker_kva == pytest.approx(150.0)


def test_bilingual_greek_english_room_alias_lookup() -> None:
    bundle = load_standards()
    lookup = bundle.room_templates.build_alias_lookup()

    # Accented Greek, uppercase Greek, and English labels all resolve deterministically
    assert lookup[normalize_label("Σαλόνι")] == RoomType.LIVING_ROOM
    assert lookup[normalize_label("ΣΑΛΟΝΙ")] == RoomType.LIVING_ROOM
    assert lookup[normalize_label("Κουζίνα")] == RoomType.KITCHEN
    assert lookup[normalize_label("Υπνοδωμάτιο")] == RoomType.BEDROOM
    assert lookup[normalize_label("Μπάνιο")] == RoomType.BATHROOM
    assert lookup[normalize_label("Διάδρομος")] == RoomType.CORRIDOR
    assert lookup[normalize_label("Πλυσταριό")] == RoomType.UTILITY
    assert lookup[normalize_label("Βεράντα")] == RoomType.VERANDA
    assert lookup[normalize_label("Living Room")] == RoomType.LIVING_ROOM
