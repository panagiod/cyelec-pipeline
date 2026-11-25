"""Additional Cyprus benchmark architectural fixtures (Detached Villa PV+EV, Commercial Office)."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path

from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    DoorOpening,
    PlumbingFixture,
    Point2D,
    Room,
    RoomType,
    WallSegment,
    WindowOpening,
)
from cyelec.synth.sample_plan import (
    build_cyprus_3bed_model,
    generate_cyprus_3bed_dxf,
    render_building_to_dxf,
)


class BenchmarkTemplate(StrEnum):
    """Named Cyprus architectural benchmark templates."""

    APARTMENT_3BED = "apartment_3bed"
    VILLA_PV_EV = "villa_pv_ev"
    COMMERCIAL_OFFICE = "commercial_office"


def _rect_poly(x0: float, y0: float, x1: float, y1: float) -> list[Point2D]:
    return [
        Point2D(x_mm=x0, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y1),
        Point2D(x_mm=x0, y_mm=y1),
    ]


def _rect_area_m2(x0: float, y0: float, x1: float, y1: float) -> float:
    return round(abs((x1 - x0) * (y1 - y0)) / 1_000_000.0, 4)


def build_cyprus_villa_pv_ev_model(
    *,
    project_name: str = "Cyprus 4-Bedroom Detached Villa (216 m², 7.5 kVA PV + 7.4 kW EV)",
    pv_capacity_kva: float = 7.5,
    ev_charger_kw: float = 7.4,
) -> BuildingModel:
    """Build 216 m² detached villa with >100 m² general sockets, 7.5 kVA PV, and 7.4 kW EV."""
    # 18,000 mm x 12,000 mm = 216.00 m² exact non-overlapping layout
    master_bath_tub = PlumbingFixture(
        id="V-BATH-1",
        room_id="R6",
        fixture_type="bathtub",
        polygon_mm=_rect_poly(17_200.0, 6_800.0, 18_000.0, 8_600.0),
    )
    family_shower = PlumbingFixture(
        id="V-SHOWER-1",
        room_id="R8",
        fixture_type="shower_tray",
        polygon_mm=_rect_poly(17_000.0, 0.0, 18_000.0, 1_000.0),
    )

    rooms = [
        # Top band: y = 6,500..12,000 (h = 5,500 mm)
        Room(
            id="R1",
            raw_label="ΣΑΛΟΝΙ / Living Room",
            normalized_type=RoomType.LIVING_ROOM,
            polygon_mm=_rect_poly(0.0, 6_500.0, 8_000.0, 12_000.0),
            area_m2=_rect_area_m2(0.0, 6_500.0, 8_000.0, 12_000.0),  # 44.00 m²
        ),
        Room(
            id="R2",
            raw_label="ΚΟΥΖΙΝΑ / Kitchen",
            normalized_type=RoomType.KITCHEN,
            polygon_mm=_rect_poly(8_000.0, 6_500.0, 13_500.0, 12_000.0),
            area_m2=_rect_area_m2(8_000.0, 6_500.0, 13_500.0, 12_000.0),  # 30.25 m²
        ),
        Room(
            id="R3",
            raw_label="ΠΛΥΣΤΑΡΙΟ / Utility",
            normalized_type=RoomType.UTILITY,
            polygon_mm=_rect_poly(13_500.0, 9_500.0, 18_000.0, 12_000.0),
            area_m2=_rect_area_m2(13_500.0, 9_500.0, 18_000.0, 12_000.0),  # 11.25 m²
        ),
        Room(
            id="R6",
            raw_label="ΜΠΑΝΙΟ / Master Bathroom",
            normalized_type=RoomType.BATHROOM,
            polygon_mm=_rect_poly(13_500.0, 6_500.0, 18_000.0, 9_500.0),
            area_m2=_rect_area_m2(13_500.0, 6_500.0, 18_000.0, 9_500.0),  # 13.50 m² (4.5x3.0 m)
            is_wet_room=True,
            fixtures=[master_bath_tub],
        ),
        # Middle band: y = 4,500..6,500 (h = 2,000 mm)
        Room(
            id="R4",
            raw_label="ΒΕΡΑΝΤΑ / Pool Veranda",
            normalized_type=RoomType.VERANDA,
            polygon_mm=_rect_poly(0.0, 4_500.0, 6_000.0, 6_500.0),
            area_m2=_rect_area_m2(0.0, 4_500.0, 6_000.0, 6_500.0),  # 12.00 m²
            is_outdoor=True,
            ambient_temp_c=45,
        ),
        Room(
            id="R5",
            raw_label="ΔΙΑΔΡΟΜΟΣ / Corridor",
            normalized_type=RoomType.CORRIDOR,
            polygon_mm=_rect_poly(6_000.0, 4_500.0, 18_000.0, 6_500.0),
            area_m2=_rect_area_m2(6_000.0, 4_500.0, 18_000.0, 6_500.0),  # 24.00 m²
        ),
        # Bottom band: y = 0..4,500 (h = 4,500 mm)
        Room(
            id="R7",
            raw_label="ΚΥΡΙΟ ΥΠΝΟΔΩΜΑΤΙΟ / Master Bedroom",
            normalized_type=RoomType.MASTER_BEDROOM,
            polygon_mm=_rect_poly(0.0, 0.0, 5_000.0, 4_500.0),
            area_m2=_rect_area_m2(0.0, 0.0, 5_000.0, 4_500.0),  # 22.50 m²
        ),
        Room(
            id="R9",
            raw_label="ΥΠΝΟΔΩΜΑΤΙΟ 2 / Bedroom 2",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=_rect_poly(5_000.0, 0.0, 8_800.0, 4_500.0),
            area_m2=_rect_area_m2(5_000.0, 0.0, 8_800.0, 4_500.0),  # 17.10 m²
        ),
        Room(
            id="R10",
            raw_label="ΥΠΝΟΔΩΜΑΤΙΟ 3 / Bedroom 3",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=_rect_poly(8_800.0, 0.0, 12_600.0, 4_500.0),
            area_m2=_rect_area_m2(8_800.0, 0.0, 12_600.0, 4_500.0),  # 17.10 m²
        ),
        Room(
            id="R11",
            raw_label="ΥΠΝΟΔΩΜΑΤΙΟ 4 / Bedroom 4",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=_rect_poly(12_600.0, 0.0, 16_000.0, 4_500.0),
            area_m2=_rect_area_m2(12_600.0, 0.0, 16_000.0, 4_500.0),  # 15.30 m²
        ),
        Room(
            id="R8",
            raw_label="ΝΤΟΥΣ / En-Suite",
            normalized_type=RoomType.ENSUITE,
            polygon_mm=_rect_poly(16_000.0, 0.0, 18_000.0, 4_500.0),
            area_m2=_rect_area_m2(16_000.0, 0.0, 18_000.0, 4_500.0),  # 9.00 m²
            is_wet_room=True,
            fixtures=[family_shower],
        ),
    ]

    walls = [
        WallSegment(
            id="VW-S",
            start=Point2D(x_mm=0.0, y_mm=0.0),
            end=Point2D(x_mm=18_000.0, y_mm=0.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="VW-E",
            start=Point2D(x_mm=18_000.0, y_mm=0.0),
            end=Point2D(x_mm=18_000.0, y_mm=12_000.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="VW-N",
            start=Point2D(x_mm=18_000.0, y_mm=12_000.0),
            end=Point2D(x_mm=0.0, y_mm=12_000.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="VW-W",
            start=Point2D(x_mm=0.0, y_mm=12_000.0),
            end=Point2D(x_mm=0.0, y_mm=0.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="VW-H1",
            start=Point2D(x_mm=0.0, y_mm=6_500.0),
            end=Point2D(x_mm=18_000.0, y_mm=6_500.0),
        ),
        WallSegment(
            id="VW-H2",
            start=Point2D(x_mm=0.0, y_mm=4_500.0),
            end=Point2D(x_mm=18_000.0, y_mm=4_500.0),
        ),
    ]

    doors = [
        DoorOpening(
            id="VD-1",
            room_id="R1",
            hinge=Point2D(x_mm=6_200.0, y_mm=6_500.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="ccw",
        ),
        DoorOpening(
            id="VD-2",
            room_id="R2",
            hinge=Point2D(x_mm=9_000.0, y_mm=6_500.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="ccw",
        ),
        DoorOpening(
            id="VD-6",
            room_id="R6",
            hinge=Point2D(x_mm=14_000.0, y_mm=6_500.0),
            width_mm=800.0,
            angle_deg=0.0,
            swing_dir="ccw",
        ),
        DoorOpening(
            id="VD-7",
            room_id="R7",
            hinge=Point2D(x_mm=3_800.0, y_mm=4_500.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="cw",
        ),
    ]

    windows = [
        WindowOpening(
            id="VWIN-1",
            room_id="R1",
            start=Point2D(x_mm=1_500.0, y_mm=12_000.0),
            end=Point2D(x_mm=6_000.0, y_mm=12_000.0),
        ),
        WindowOpening(
            id="VWIN-2",
            room_id="R7",
            start=Point2D(x_mm=1_200.0, y_mm=0.0),
            end=Point2D(x_mm=3_600.0, y_mm=0.0),
        ),
    ]

    return BuildingModel(
        project_name=project_name,
        building_type=BuildingType.INDEPENDENT_HOUSE,
        units="mm",
        total_floor_area_m2=round(sum(r.area_m2 for r in rooms), 4),
        rooms=rooms,
        walls=walls,
        doors=doors,
        windows=windows,
        db_anchor=DistributionBoardAnchor(
            id="DB1",
            location_mm=Point2D(x_mm=13_000.0, y_mm=6_500.0),
        ),
        pv_capacity_kva=pv_capacity_kva,
        ev_charger_kw=ev_charger_kw,
    )


def build_cyprus_commercial_office_model(
    *,
    project_name: str = "Cyprus Commercial Office Floor (450 m², CT Metering & Substation Check)",
    extra_three_phase_load_kva: float = 95.0,
    pv_capacity_kva: float = 15.0,
) -> BuildingModel:
    """Build 450 m² commercial office triggering CT metering (>100 A/ph) & substation checks."""
    # 30,000 mm x 15,000 mm = 450.00 m²
    rooms = [
        Room(
            id="R1",
            raw_label="ΣΑΛΟΝΙ / Open Office West",
            normalized_type=RoomType.LIVING_ROOM,
            polygon_mm=_rect_poly(0.0, 5_000.0, 15_000.0, 15_000.0),
            area_m2=150.0,
        ),
        Room(
            id="R2",
            raw_label="ΥΠΝΟΔΩΜΑΤΙΟ / Open Office East",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=_rect_poly(15_000.0, 5_000.0, 26_000.0, 15_000.0),
            area_m2=110.0,
        ),
        Room(
            id="R3",
            raw_label="ΚΟΥΖΙΝΑ / Staff Kitchenette",
            normalized_type=RoomType.KITCHEN,
            polygon_mm=_rect_poly(26_000.0, 10_000.0, 30_000.0, 15_000.0),
            area_m2=20.0,
        ),
        Room(
            id="R4",
            raw_label="ΤΟΥΑΛΕΤΑ / Office WC",
            normalized_type=RoomType.WC,
            polygon_mm=_rect_poly(26_000.0, 5_000.0, 30_000.0, 10_000.0),
            area_m2=20.0,
            is_wet_room=True,
        ),
        Room(
            id="R5",
            raw_label="ΔΙΑΔΡΟΜΟΣ / Main Lobby & Corridor",
            normalized_type=RoomType.CORRIDOR,
            polygon_mm=_rect_poly(0.0, 0.0, 30_000.0, 5_000.0),
            area_m2=150.0,
        ),
    ]
    walls = [
        WallSegment(
            id="CW-S",
            start=Point2D(x_mm=0.0, y_mm=0.0),
            end=Point2D(x_mm=30_000.0, y_mm=0.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="CW-E",
            start=Point2D(x_mm=30_000.0, y_mm=0.0),
            end=Point2D(x_mm=30_000.0, y_mm=15_000.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="CW-N",
            start=Point2D(x_mm=30_000.0, y_mm=15_000.0),
            end=Point2D(x_mm=0.0, y_mm=15_000.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="CW-W",
            start=Point2D(x_mm=0.0, y_mm=15_000.0),
            end=Point2D(x_mm=0.0, y_mm=0.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
    ]
    return BuildingModel(
        project_name=project_name,
        building_type=BuildingType.OFFICE,
        units="mm",
        total_floor_area_m2=450.0,
        rooms=rooms,
        walls=walls,
        db_anchor=DistributionBoardAnchor(
            id="MDB1",
            location_mm=Point2D(x_mm=2_000.0, y_mm=5_000.0),
        ),
        pv_capacity_kva=pv_capacity_kva,
        extra_three_phase_load_kva=extra_three_phase_load_kva,
    )


def generate_benchmark_dxf(
    template: BenchmarkTemplate,
    out_dxf: Path | None = None,
) -> BuildingModel:
    """Generate any named Cyprus benchmark DXF fixture and return its ground-truth model."""
    if template == BenchmarkTemplate.APARTMENT_3BED:
        if out_dxf is not None:
            return generate_cyprus_3bed_dxf(out_dxf=out_dxf)
        return build_cyprus_3bed_model()

    model = (
        build_cyprus_villa_pv_ev_model()
        if template == BenchmarkTemplate.VILLA_PV_EV
        else build_cyprus_commercial_office_model()
    )
    if out_dxf is not None:
        out_dxf.parent.mkdir(parents=True, exist_ok=True)
        doc = render_building_to_dxf(model)
        doc.saveas(str(out_dxf))
        sha256 = hashlib.sha256(out_dxf.read_bytes()).hexdigest()
        model = model.model_copy(update={"source_file": str(out_dxf), "source_sha256": sha256})
    return model
