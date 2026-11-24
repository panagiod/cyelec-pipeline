"""Unit tests for deterministic BS 7671 / Cyprus EAC circuit grouper."""

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
from cyelec.models.electrical import BreakerCurve, CircuitCategory, CircuitTopology
from cyelec.solver.grouper import generate_circuits
from cyelec.standards_loader import load_standards
from cyelec.synth.sample_plan import build_cyprus_3bed_model


def test_cyprus_3bed_circuit_grouping_structure() -> None:
    standards = load_standards()
    building = build_cyprus_3bed_model()
    specs = generate_circuits(building, standards)

    lighting = [c for c in specs if c.category == CircuitCategory.LIGHTING]
    rings = [c for c in specs if c.topology == CircuitTopology.RING_FINAL]
    acs = [c for c in specs if c.category == CircuitCategory.SPLIT_AC]
    categories = {c.category for c in specs}

    # Reg 314.1: lighting split across >= 2 circuits
    assert len(lighting) >= 2
    assert all(c.vd_limit_pct == pytest.approx(3.0) for c in lighting)

    # Kitchen/Utility + General 32 A rings
    assert len(rings) == 2
    assert all(c.min_breaker_in_a == 32 for c in rings)

    # 4 habitable rooms (Living + 3 Bedrooms) -> 4 Curve C split A/C radials at 0.85 diversity
    assert len(acs) == 4
    assert all(c.preferred_curve == BreakerCurve.C for c in acs)
    assert all(c.diversity_factor == pytest.approx(0.85) for c in acs)

    # Dedicated Cyprus appliances present
    assert CircuitCategory.COOKER in categories
    assert CircuitCategory.OVEN in categories
    assert CircuitCategory.APPLIANCE in categories
    assert CircuitCategory.WATER_HEATER in categories

    # Cyprus 40°C default indoor ambient applied
    assert all(c.ambient_temp_c >= 40 for c in specs)


def test_general_socket_rings_split_above_100m2() -> None:
    """BS 7671 Appendix 15: general socket rings must not exceed 100 m² floor area per ring."""
    standards = load_standards()
    rooms = [
        Room(
            id=f"R{i}",
            raw_label=f"ΥΠΝΟΔΩΜΑΤΙΟ {i}",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=[
                Point2D(x_mm=0, y_mm=0),
                Point2D(x_mm=7000, y_mm=0),
                Point2D(x_mm=7000, y_mm=6000),
                Point2D(x_mm=0, y_mm=6000),
            ],
            area_m2=42.0,
        )
        for i in range(1, 5)  # 4 x 42 m² = 168 m² > 100 m²
    ]
    building = BuildingModel(
        project_name="Large Villa",
        building_type=BuildingType.INDEPENDENT_HOUSE,
        total_floor_area_m2=168.0,
        rooms=rooms,
        db_anchor=DistributionBoardAnchor(id="DB1", location_mm=Point2D(x_mm=0, y_mm=0)),
    )
    specs = generate_circuits(building, standards)
    gen_rings = [c for c in specs if c.category == CircuitCategory.SOCKET_RING]
    assert len(gen_rings) == 2
    for ring in gen_rings:
        ring_area = sum(r.area_m2 for r in rooms if r.id in ring.room_ids)
        assert ring_area <= 100.0 + 1e-6
