"""Parametric synthetic 3-bedroom Cyprus apartment R2018 DXF generator and ground-truth model."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

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

STANDARD_LAYERS: dict[str, int] = {
    "A-WALL": 7,   # White / Black
    "A-ROOM": 8,   # Grey closed room polygons
    "A-DOOR": 3,   # Green doors & swing arcs
    "A-WIND": 4,   # Cyan windows
    "A-FIXT": 5,   # Blue plumbing fixtures (bathtub, shower tray, etc.)
    "A-TEXT": 2,   # Yellow bilingual Greek + English labels
    "A-DB": 1,     # Red main distribution board anchor (DB1)
}


def _rect_poly(x0: float, y0: float, x1: float, y1: float) -> list[Point2D]:
    return [
        Point2D(x_mm=x0, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y1),
        Point2D(x_mm=x0, y_mm=y1),
    ]


def _rect_area_m2(x0: float, y0: float, x1: float, y1: float) -> float:
    return round(abs((x1 - x0) * (y1 - y0)) / 1_000_000.0, 4)


def build_cyprus_3bed_model(
    *,
    project_name: str = "Cyprus 3-Bedroom Apartment (120 m² Synthetic Benchmark)",
    pv_capacity_kva: float = 0.0,
) -> BuildingModel:
    """Build exact 120.0 m² (12,000 × 10,000 mm) ground-truth 3-bedroom Cyprus apartment model."""
    # Fixtures for BS 7671 Section 701 wet-zone testing
    bathtub = PlumbingFixture(
        id="FIXT-BATH-1",
        room_id="R4",
        fixture_type="bathtub",
        polygon_mm=_rect_poly(11_250.0, 6_000.0, 12_000.0, 7_700.0),  # 1700 x 750 mm
    )
    bath_basin = PlumbingFixture(
        id="FIXT-BASIN-1",
        room_id="R4",
        fixture_type="basin",
        polygon_mm=_rect_poly(9_100.0, 7_300.0, 9_700.0, 7_900.0),
    )
    bath_wc = PlumbingFixture(
        id="FIXT-WC-1",
        room_id="R4",
        fixture_type="wc",
        polygon_mm=_rect_poly(10_100.0, 7_300.0, 10_550.0, 7_950.0),
    )

    shower_tray = PlumbingFixture(
        id="FIXT-SHOWER-1",
        room_id="R8",
        fixture_type="shower_tray",
        polygon_mm=_rect_poly(4_600.0, 0.0, 5_500.0, 900.0),  # 900 x 900 mm
    )
    ensuite_basin = PlumbingFixture(
        id="FIXT-BASIN-2",
        room_id="R8",
        fixture_type="basin",
        polygon_mm=_rect_poly(4_100.0, 1_800.0, 4_600.0, 2_350.0),
    )

    guest_wc = PlumbingFixture(
        id="FIXT-WC-2",
        room_id="R9",
        fixture_type="wc",
        polygon_mm=_rect_poly(4_100.0, 2_600.0, 4_550.0, 3_200.0),
    )
    kitchen_sink = PlumbingFixture(
        id="FIXT-SINK-1",
        room_id="R2",
        fixture_type="sink",
        polygon_mm=_rect_poly(6_800.0, 9_400.0, 7_800.0, 9_950.0),
    )

    # Exact non-overlapping 12,000 mm x 10,000 mm layout = 120.00 m²
    rooms = [
        Room(
            id="R1",
            raw_label="ΣΑΛΟΝΙ / Living Room",
            normalized_type=RoomType.LIVING_ROOM,
            polygon_mm=_rect_poly(0.0, 5_500.0, 5_500.0, 10_000.0),
            area_m2=_rect_area_m2(0.0, 5_500.0, 5_500.0, 10_000.0),  # 24.75 m²
        ),
        Room(
            id="R2",
            raw_label="ΚΟΥΖΙΝΑ / Kitchen",
            normalized_type=RoomType.KITCHEN,
            polygon_mm=_rect_poly(5_500.0, 5_500.0, 9_000.0, 10_000.0),
            area_m2=_rect_area_m2(5_500.0, 5_500.0, 9_000.0, 10_000.0),  # 15.75 m²
            fixtures=[kitchen_sink],
        ),
        Room(
            id="R3",
            raw_label="ΠΛΥΣΤΑΡΙΟ / Utility",
            normalized_type=RoomType.UTILITY,
            polygon_mm=_rect_poly(9_000.0, 8_000.0, 12_000.0, 10_000.0),
            area_m2=_rect_area_m2(9_000.0, 8_000.0, 12_000.0, 10_000.0),  # 6.00 m²
        ),
        Room(
            id="R4",
            raw_label="ΜΠΑΝΙΟ / Bathroom",
            normalized_type=RoomType.BATHROOM,
            polygon_mm=_rect_poly(9_000.0, 5_500.0, 12_000.0, 8_000.0),
            area_m2=_rect_area_m2(9_000.0, 5_500.0, 12_000.0, 8_000.0),  # 7.50 m²
            is_wet_room=True,
            fixtures=[bathtub, bath_basin, bath_wc],
        ),
        Room(
            id="R5",
            raw_label="ΒΕΡΑΝΤΑ / Veranda",
            normalized_type=RoomType.VERANDA,
            polygon_mm=_rect_poly(0.0, 4_000.0, 4_000.0, 5_500.0),
            area_m2=_rect_area_m2(0.0, 4_000.0, 4_000.0, 5_500.0),  # 6.00 m²
            is_outdoor=True,
            ambient_temp_c=45,
        ),
        Room(
            id="R6",
            raw_label="ΔΙΑΔΡΟΜΟΣ / Corridor",
            normalized_type=RoomType.CORRIDOR,
            polygon_mm=_rect_poly(4_000.0, 4_000.0, 12_000.0, 5_500.0),
            area_m2=_rect_area_m2(4_000.0, 4_000.0, 12_000.0, 5_500.0),  # 12.00 m²
        ),
        Room(
            id="R7",
            raw_label="ΚΥΡΙΟ ΥΠΝΟΔΩΜΑΤΙΟ / Master Bedroom",
            normalized_type=RoomType.MASTER_BEDROOM,
            polygon_mm=_rect_poly(0.0, 0.0, 4_000.0, 4_000.0),
            area_m2=_rect_area_m2(0.0, 0.0, 4_000.0, 4_000.0),  # 16.00 m²
        ),
        Room(
            id="R8",
            raw_label="ΝΤΟΥΣ / En-Suite",
            normalized_type=RoomType.ENSUITE,
            polygon_mm=_rect_poly(4_000.0, 0.0, 5_500.0, 2_500.0),
            area_m2=_rect_area_m2(4_000.0, 0.0, 5_500.0, 2_500.0),  # 3.75 m²
            is_wet_room=True,
            fixtures=[shower_tray, ensuite_basin],
        ),
        Room(
            id="R9",
            raw_label="ΤΟΥΑΛΕΤΑ / Guest WC",
            normalized_type=RoomType.WC,
            polygon_mm=_rect_poly(4_000.0, 2_500.0, 5_500.0, 4_000.0),
            area_m2=_rect_area_m2(4_000.0, 2_500.0, 5_500.0, 4_000.0),  # 2.25 m²
            is_wet_room=True,
            fixtures=[guest_wc],
        ),
        Room(
            id="R10",
            raw_label="ΥΠΝΟΔΩΜΑΤΙΟ 2 / Bedroom 2",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=_rect_poly(5_500.0, 0.0, 8_750.0, 4_000.0),
            area_m2=_rect_area_m2(5_500.0, 0.0, 8_750.0, 4_000.0),  # 13.00 m²
        ),
        Room(
            id="R11",
            raw_label="ΥΠΝΟΔΩΜΑΤΙΟ 3 / Bedroom 3",
            normalized_type=RoomType.BEDROOM,
            polygon_mm=_rect_poly(8_750.0, 0.0, 12_000.0, 4_000.0),
            area_m2=_rect_area_m2(8_750.0, 0.0, 12_000.0, 4_000.0),  # 13.00 m²
        ),
    ]

    walls = [
        # Exterior perimeter (250 mm insulated masonry)
        WallSegment(
            id="W-EXT-S",
            start=Point2D(x_mm=0.0, y_mm=0.0),
            end=Point2D(x_mm=12_000.0, y_mm=0.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="W-EXT-E",
            start=Point2D(x_mm=12_000.0, y_mm=0.0),
            end=Point2D(x_mm=12_000.0, y_mm=10_000.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="W-EXT-N",
            start=Point2D(x_mm=12_000.0, y_mm=10_000.0),
            end=Point2D(x_mm=0.0, y_mm=10_000.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        WallSegment(
            id="W-EXT-W",
            start=Point2D(x_mm=0.0, y_mm=10_000.0),
            end=Point2D(x_mm=0.0, y_mm=0.0),
            thickness_mm=250.0,
            is_exterior=True,
        ),
        # Interior corridor & partition walls (150 mm masonry)
        WallSegment(
            id="W-INT-H1",
            start=Point2D(x_mm=0.0, y_mm=5_500.0),
            end=Point2D(x_mm=12_000.0, y_mm=5_500.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-H2",
            start=Point2D(x_mm=0.0, y_mm=4_000.0),
            end=Point2D(x_mm=12_000.0, y_mm=4_000.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-V1",
            start=Point2D(x_mm=5_500.0, y_mm=5_500.0),
            end=Point2D(x_mm=5_500.0, y_mm=10_000.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-V2",
            start=Point2D(x_mm=9_000.0, y_mm=5_500.0),
            end=Point2D(x_mm=9_000.0, y_mm=10_000.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-H3",
            start=Point2D(x_mm=9_000.0, y_mm=8_000.0),
            end=Point2D(x_mm=12_000.0, y_mm=8_000.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-V3",
            start=Point2D(x_mm=4_000.0, y_mm=0.0),
            end=Point2D(x_mm=4_000.0, y_mm=5_500.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-V4",
            start=Point2D(x_mm=5_500.0, y_mm=0.0),
            end=Point2D(x_mm=5_500.0, y_mm=4_000.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-H4",
            start=Point2D(x_mm=4_000.0, y_mm=2_500.0),
            end=Point2D(x_mm=5_500.0, y_mm=2_500.0),
            thickness_mm=150.0,
        ),
        WallSegment(
            id="W-INT-V5",
            start=Point2D(x_mm=8_750.0, y_mm=0.0),
            end=Point2D(x_mm=8_750.0, y_mm=4_000.0),
            thickness_mm=150.0,
        ),
    ]

    doors = [
        # Main entrance into corridor
        DoorOpening(
            id="D-ENT",
            room_id="R6",
            hinge=Point2D(x_mm=12_000.0, y_mm=4_250.0),
            width_mm=1_000.0,
            angle_deg=90.0,
            swing_dir="ccw",
        ),
        # Living room from corridor
        DoorOpening(
            id="D-R1",
            room_id="R1",
            hinge=Point2D(x_mm=4_200.0, y_mm=5_500.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="ccw",
        ),
        # Veranda from living room
        DoorOpening(
            id="D-R5",
            room_id="R5",
            hinge=Point2D(x_mm=1_500.0, y_mm=5_500.0),
            width_mm=1_200.0,
            angle_deg=0.0,
            swing_dir="cw",
        ),
        # Kitchen from corridor
        DoorOpening(
            id="D-R2",
            room_id="R2",
            hinge=Point2D(x_mm=6_200.0, y_mm=5_500.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="ccw",
        ),
        # Utility from kitchen
        DoorOpening(
            id="D-R3",
            room_id="R3",
            hinge=Point2D(x_mm=9_000.0, y_mm=8_300.0),
            width_mm=800.0,
            angle_deg=90.0,
            swing_dir="cw",
        ),
        # Main bathroom from corridor
        DoorOpening(
            id="D-R4",
            room_id="R4",
            hinge=Point2D(x_mm=9_400.0, y_mm=5_500.0),
            width_mm=800.0,
            angle_deg=0.0,
            swing_dir="ccw",
        ),
        # Master bedroom from corridor (angled/end entrance at x=4000, y=4000)
        DoorOpening(
            id="D-R7",
            room_id="R7",
            hinge=Point2D(x_mm=3_100.0, y_mm=4_000.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="cw",
        ),
        # En-suite from master bedroom
        DoorOpening(
            id="D-R8",
            room_id="R8",
            hinge=Point2D(x_mm=4_000.0, y_mm=1_000.0),
            width_mm=750.0,
            angle_deg=90.0,
            swing_dir="cw",
        ),
        # Guest WC from corridor
        DoorOpening(
            id="D-R9",
            room_id="R9",
            hinge=Point2D(x_mm=4_300.0, y_mm=4_000.0),
            width_mm=750.0,
            angle_deg=0.0,
            swing_dir="cw",
        ),
        # Bedroom 2 from corridor
        DoorOpening(
            id="D-R10",
            room_id="R10",
            hinge=Point2D(x_mm=6_000.0, y_mm=4_000.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="cw",
        ),
        # Bedroom 3 from corridor
        DoorOpening(
            id="D-R11",
            room_id="R11",
            hinge=Point2D(x_mm=9_200.0, y_mm=4_000.0),
            width_mm=900.0,
            angle_deg=0.0,
            swing_dir="cw",
        ),
    ]

    windows = [
        WindowOpening(
            id="WIN-R1-N",
            room_id="R1",
            start=Point2D(x_mm=1_200.0, y_mm=10_000.0),
            end=Point2D(x_mm=4_200.0, y_mm=10_000.0),
        ),
        WindowOpening(
            id="WIN-R2-N",
            room_id="R2",
            start=Point2D(x_mm=6_500.0, y_mm=10_000.0),
            end=Point2D(x_mm=8_200.0, y_mm=10_000.0),
            sill_height_mm=1_100.0,
        ),
        WindowOpening(
            id="WIN-R3-E",
            room_id="R3",
            start=Point2D(x_mm=12_000.0, y_mm=8_600.0),
            end=Point2D(x_mm=12_000.0, y_mm=9_400.0),
            sill_height_mm=1_200.0,
        ),
        WindowOpening(
            id="WIN-R4-E",
            room_id="R4",
            start=Point2D(x_mm=12_000.0, y_mm=6_500.0),
            end=Point2D(x_mm=12_000.0, y_mm=7_200.0),
            sill_height_mm=1_500.0,
        ),
        WindowOpening(
            id="WIN-R7-S",
            room_id="R7",
            start=Point2D(x_mm=1_000.0, y_mm=0.0),
            end=Point2D(x_mm=3_000.0, y_mm=0.0),
        ),
        WindowOpening(
            id="WIN-R10-S",
            room_id="R10",
            start=Point2D(x_mm=6_400.0, y_mm=0.0),
            end=Point2D(x_mm=8_000.0, y_mm=0.0),
        ),
        WindowOpening(
            id="WIN-R11-S",
            room_id="R11",
            start=Point2D(x_mm=9_600.0, y_mm=0.0),
            end=Point2D(x_mm=11_200.0, y_mm=0.0),
        ),
    ]

    db_anchor = DistributionBoardAnchor(
        id="DB1",
        location_mm=Point2D(x_mm=11_200.0, y_mm=5_500.0),
        wall_id="W-INT-H1",
    )

    total_area = round(sum(r.area_m2 for r in rooms), 4)

    return BuildingModel(
        project_name=project_name,
        building_type=BuildingType.APARTMENT_3BED_PLUS,
        units="mm",
        total_floor_area_m2=total_area,
        rooms=rooms,
        walls=walls,
        doors=doors,
        windows=windows,
        db_anchor=db_anchor,
        pv_capacity_kva=pv_capacity_kva,
    )


def render_building_to_dxf(building: BuildingModel) -> Drawing:
    """Create an R2018 DXF document (millimetres, $INSUNITS=4) from a BuildingModel."""
    doc = new_dxf("R2018", setup=True)
    doc.header["$INSUNITS"] = 4  # Millimeters
    doc.header["$MEASUREMENT"] = 1  # Metric

    for layer_name, color_idx in STANDARD_LAYERS.items():
        if layer_name not in doc.layers:
            doc.layers.add(layer_name, color=color_idx)

    msp = doc.modelspace()

    # 1. Closed room polygons + bilingual room labels
    for room in building.rooms:
        pts = [(p.x_mm, p.y_mm) for p in room.polygon_mm]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-ROOM"})

        centroid = room.centroid_mm()
        label = f"{room.raw_label}\\P({room.area_m2:.2f} m²)"
        mtext = msp.add_mtext(
            label,
            dxfattribs={"layer": "A-TEXT", "char_height": 180.0},
        )
        mtext.set_location((centroid.x_mm, centroid.y_mm), attachment_point=5)

        # Plumbing fixtures inside room
        for fixt in room.fixtures:
            fpts = [(p.x_mm, p.y_mm) for p in fixt.polygon_mm]
            msp.add_lwpolyline(fpts, close=True, dxfattribs={"layer": "A-FIXT"})
            fx = sum(p.x_mm for p in fixt.polygon_mm) / len(fixt.polygon_mm)
            fy = sum(p.y_mm for p in fixt.polygon_mm) / len(fixt.polygon_mm)
            ftext = msp.add_text(
                fixt.fixture_type.upper(),
                dxfattribs={"layer": "A-FIXT", "height": 90.0},
            )
            ftext.set_placement((fx, fy))

    # 2. Architectural walls
    for wall in building.walls:
        msp.add_line(
            (wall.start.x_mm, wall.start.y_mm),
            (wall.end.x_mm, wall.end.y_mm),
            dxfattribs={"layer": "A-WALL", "lineweight": 35 if wall.is_exterior else 25},
        )

    # 3. Doors (leaf line + 90-degree swing arc)
    for door in building.doors:
        hx, hy = door.hinge.x_mm, door.hinge.y_mm
        open_angle = door.angle_deg + (90.0 if door.swing_dir == "ccw" else -90.0)
        rad = math.radians(open_angle)
        leaf_end = (hx + door.width_mm * math.cos(rad), hy + door.width_mm * math.sin(rad))
        msp.add_line((hx, hy), leaf_end, dxfattribs={"layer": "A-DOOR"})

        if door.swing_dir == "ccw":
            start_ang = door.angle_deg % 360.0
            end_ang = (door.angle_deg + 90.0) % 360.0
        else:
            start_ang = (door.angle_deg - 90.0) % 360.0
            end_ang = door.angle_deg % 360.0

        msp.add_arc(
            center=(hx, hy),
            radius=door.width_mm,
            start_angle=start_ang,
            end_angle=end_ang,
            dxfattribs={"layer": "A-DOOR"},
        )

    # 4. Windows (double parallel line marker)
    for win in building.windows:
        msp.add_line(
            (win.start.x_mm, win.start.y_mm),
            (win.end.x_mm, win.end.y_mm),
            dxfattribs={"layer": "A-WIND", "lineweight": 30},
        )

    # 5. Distribution Board anchor (DB1)
    dbx = building.db_anchor.location_mm.x_mm
    dby = building.db_anchor.location_mm.y_mm
    half_w, half_h = 250.0, 120.0
    db_rect = [
        (dbx - half_w, dby - half_h),
        (dbx + half_w, dby - half_h),
        (dbx + half_w, dby + half_h),
        (dbx - half_w, dby + half_h),
    ]
    msp.add_lwpolyline(db_rect, close=True, dxfattribs={"layer": "A-DB"})
    msp.add_line(
        (dbx - half_w, dby - half_h),
        (dbx + half_w, dby + half_h),
        dxfattribs={"layer": "A-DB"},
    )
    db_text = msp.add_text(
        building.db_anchor.id,
        dxfattribs={"layer": "A-DB", "height": 160.0},
    )
    db_text.set_placement((dbx - 180.0, dby - 320.0))

    return doc


def generate_cyprus_3bed_dxf(
    out_dxf: Path | None = None,
    *,
    project_name: str = "Cyprus 3-Bedroom Apartment (120 m² Synthetic Benchmark)",
    pv_capacity_kva: float = 0.0,
) -> BuildingModel:
    """Generate synthetic 120 m² Cyprus 3-bed apartment DXF and return its ground-truth model."""
    model = build_cyprus_3bed_model(project_name=project_name, pv_capacity_kva=pv_capacity_kva)
    doc = render_building_to_dxf(model)

    if out_dxf is not None:
        out_dxf.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(str(out_dxf))
        sha256 = hashlib.sha256(out_dxf.read_bytes()).hexdigest()
        model = model.model_copy(
            update={
                "source_file": str(out_dxf),
                "source_sha256": sha256,
            }
        )

    return model
