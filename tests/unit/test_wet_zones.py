"""Unit tests for BS 7671 Section 701 wet-zone polygons (2.5m 18th A2 vs 3.0m 17th A3 + walls)."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    PlumbingFixture,
    Point2D,
    Room,
    RoomType,
)
from cyelec.models.electrical import EacProfile
from cyelec.spatial.wet_zones import (
    classify_point_wet_zone,
    compute_building_wet_zones,
    is_point_in_socket_exclusion,
)
from cyelec.standards_loader import load_standards


def _large_bath_plus_adjacent_bedroom() -> BuildingModel:
    # Large 4.5 m x 3.0 m bathroom with bathtub along east wall (x=3750..4500)
    # Adjacent bedroom on east side of partition wall (x=4500..8000)
    bath = Room(
        id="R-BATH",
        raw_label="ΜΠΑΝΙΟ",
        normalized_type=RoomType.BATHROOM,
        polygon_mm=[
            Point2D(x_mm=0, y_mm=0),
            Point2D(x_mm=4500, y_mm=0),
            Point2D(x_mm=4500, y_mm=3000),
            Point2D(x_mm=0, y_mm=3000),
        ],
        area_m2=13.5,
        is_wet_room=True,
        fixtures=[
            PlumbingFixture(
                id="BATH-1",
                room_id="R-BATH",
                fixture_type="bathtub",
                polygon_mm=[
                    Point2D(x_mm=3750, y_mm=650),
                    Point2D(x_mm=4500, y_mm=650),
                    Point2D(x_mm=4500, y_mm=2350),
                    Point2D(x_mm=3750, y_mm=2350),
                ],
            )
        ],
    )
    bedroom = Room(
        id="R-BED",
        raw_label="ΥΠΝΟΔΩΜΑΤΙΟ",
        normalized_type=RoomType.BEDROOM,
        polygon_mm=[
            Point2D(x_mm=4500, y_mm=0),
            Point2D(x_mm=8000, y_mm=0),
            Point2D(x_mm=8000, y_mm=3000),
            Point2D(x_mm=4500, y_mm=3000),
        ],
        area_m2=10.5,
    )
    return BuildingModel(
        project_name="Section 701 Test",
        building_type=BuildingType.APARTMENT_1BED,
        total_floor_area_m2=24.0,
        rooms=[bath, bedroom],
        db_anchor=DistributionBoardAnchor(id="DB1", location_mm=Point2D(x_mm=5000, y_mm=2900)),
    )


def test_section_701_dual_eac_profiles_2_5m_vs_3_0m_and_wall_clipping() -> None:
    std = load_standards()
    building = _large_bath_plus_adjacent_bedroom()

    # 1. 18th Ed A2 (2.5 m socket exclusion from bath edge at x=3750 -> reaches x=1250)
    wz_18 = compute_building_wet_zones(building, std, eac_profile=EacProfile.EAC_18TH_A2)
    assert len(wz_18) == 1
    bath_wz_18 = wz_18[0]
    assert bath_wz_18.exclusion_radius_m == pytest.approx(2.5)

    # Inside bath -> Zone 0
    assert classify_point_wet_zone(Point2D(x_mm=4100, y_mm=1500), bath_wz_18) == "zone_0"
    # 300 mm from bath edge -> Zone 2 (<= 600 mm)
    assert classify_point_wet_zone(Point2D(x_mm=3450, y_mm=1500), bath_wz_18) == "zone_2"
    # 2.25 m from bath edge (x=1500) -> inside 2.5 m exclusion
    assert is_point_in_socket_exclusion(Point2D(x_mm=1500, y_mm=1500), bath_wz_18) is True
    # 2.75 m from bath edge (x=1000) -> OUTSIDE 2.5 m exclusion under 18th A2!
    assert is_point_in_socket_exclusion(Point2D(x_mm=1000, y_mm=1500), bath_wz_18) is False

    # 2. 17th Ed A3 (3.0 m socket exclusion from bath edge at x=3750 -> reaches x=750)
    wz_17 = compute_building_wet_zones(building, std, eac_profile=EacProfile.EAC_17TH_A3)
    bath_wz_17 = wz_17[0]
    assert bath_wz_17.exclusion_radius_m == pytest.approx(3.0)

    # Same point at x=1000 (2.75 m from bath) IS excluded under 17th A3!
    assert is_point_in_socket_exclusion(Point2D(x_mm=1000, y_mm=1500), bath_wz_17) is True
    # Point at x=500 (3.25 m from bath) is outside 3.0 m exclusion
    assert is_point_in_socket_exclusion(Point2D(x_mm=500, y_mm=1500), bath_wz_17) is False

    # 3. Wall clipping: exclusion polygon MUST NOT bleed into adjacent bedroom (x > 4500)
    excl_poly = Polygon([(p.x_mm, p.y_mm) for p in bath_wz_17.socket_exclusion_polygons_mm[0]])
    assert excl_poly.bounds[2] <= 4500.5
    assert is_point_in_socket_exclusion(Point2D(x_mm=4600, y_mm=1500), bath_wz_17) is False
