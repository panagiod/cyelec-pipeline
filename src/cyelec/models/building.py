"""Building and spatial geometry domain models (millimetre coordinate system)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BuildingType(StrEnum):
    """Cyprus EAC building classifications driving 1P vs 3P supply rules."""

    STUDIO = "studio"
    APARTMENT_1BED = "apartment_1bed"
    APARTMENT_2BED = "apartment_2bed"
    APARTMENT_3BED_PLUS = "apartment_3bed_plus"
    INDEPENDENT_HOUSE = "independent_house"
    OFFICE = "office"
    SHOP = "shop"
    WORKSHOP = "workshop"
    INDUSTRIAL = "industrial"
    COMMON_AREA_SMALL = "common_area_small"


class RoomType(StrEnum):
    """Canonical normalized room types mapped from English/Greek labels."""

    LIVING_ROOM = "living_room"
    KITCHEN = "kitchen"
    KITCHEN_LIVING = "kitchen_living"
    MASTER_BEDROOM = "master_bedroom"
    BEDROOM = "bedroom"
    BATHROOM = "bathroom"
    ENSUITE = "ensuite"
    WC = "wc"
    CORRIDOR = "corridor"
    UTILITY = "utility"
    VERANDA = "veranda"
    STORAGE = "storage"
    # Commercial & Multi-Storey MEP Room Types
    OPEN_OFFICE = "open_office"
    MEETING_ROOM = "meeting_room"
    SERVER_ROOM = "server_room"
    RETAIL_FLOOR = "retail_floor"
    WAREHOUSE = "warehouse"
    PLANT_ROOM = "plant_room"
    STAIRWELL = "stairwell"
    LIFT_SHAFT = "lift_shaft"


class BenchmarkTemplate(StrEnum):
    """Named Cyprus architectural benchmark templates."""

    APARTMENT_3BED = "apartment_3bed"
    VILLA_PV_EV = "villa_pv_ev"
    VILLA_2STOREY = "villa_2storey"
    COMMERCIAL_OFFICE = "commercial_office"


class Point2D(BaseModel):
    """2D Cartesian coordinate in millimetres."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x_mm: float
    y_mm: float


class WallSegment(BaseModel):
    """Architectural wall centreline segment."""

    model_config = ConfigDict(extra="forbid")

    id: str
    start: Point2D
    end: Point2D
    thickness_mm: float = Field(default=200.0, gt=0.0)
    is_exterior: bool = False


class DoorOpening(BaseModel):
    """Architectural door with hinge point and 90-degree swing geometry."""

    model_config = ConfigDict(extra="forbid")

    id: str
    room_id: str
    hinge: Point2D
    width_mm: float = Field(gt=0.0)
    angle_deg: float
    swing_dir: Literal["cw", "ccw"] = "ccw"


class WindowOpening(BaseModel):
    """Architectural window opening along an exterior wall."""

    model_config = ConfigDict(extra="forbid")

    id: str
    room_id: str
    start: Point2D
    end: Point2D
    sill_height_mm: float = Field(default=900.0, ge=0.0)


class PlumbingFixture(BaseModel):
    """Plumbing fixture polygon (bathtub / shower tray drives BS 7671 Section 701 wet zones)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    room_id: str
    fixture_type: Literal["bathtub", "shower_tray", "basin", "wc", "sink"]
    polygon_mm: list[Point2D] = Field(min_length=3)


class FurnitureItem(BaseModel):
    """Detected architectural furniture/joinery block for furniture-aware electrical placement."""

    model_config = ConfigDict(extra="forbid")

    id: str
    room_id: str
    furniture_type: Literal[
        "bed",
        "tv",
        "sofa",
        "wardrobe",
        "dining_table",
        "kitchen_counter",
        "stove",
        "fridge",
    ]
    polygon_mm: list[Point2D] = Field(min_length=3)
    centroid_mm: Point2D


class Room(BaseModel):
    """Normalized architectural room with closed boundary polygon in millimetres."""

    model_config = ConfigDict(extra="forbid")

    id: str
    raw_label: str
    normalized_type: RoomType
    floor: str = Field(default="GF")
    polygon_mm: list[Point2D] = Field(min_length=3)
    area_m2: float = Field(gt=0.0)
    ceiling_height_mm: float = Field(default=2900.0, gt=0.0)
    is_wet_room: bool = False
    is_outdoor: bool = False
    ambient_temp_c: int | None = None
    fixtures: list[PlumbingFixture] = Field(default_factory=list)
    furniture: list[FurnitureItem] = Field(default_factory=list)

    def centroid_mm(self) -> Point2D:
        """Compute simple vertex centroid of the room polygon."""
        n = len(self.polygon_mm)
        return Point2D(
            x_mm=sum(p.x_mm for p in self.polygon_mm) / n,
            y_mm=sum(p.y_mm for p in self.polygon_mm) / n,
        )


class DistributionBoardAnchor(BaseModel):
    """Location of the main distribution board (DB1 / MDB) on the floorplan."""

    model_config = ConfigDict(extra="forbid")

    id: str = "DB1"
    location_mm: Point2D
    wall_id: str | None = None


class SubBoardSpec(BaseModel):
    """Sub-distribution board specification for multi-floor / multi-board buildings."""

    model_config = ConfigDict(extra="forbid")

    board_id: str
    floor: str
    parent_board_id: str = "MDB"
    location_mm: Point2D
    submain_length_m: float = Field(gt=0.0)
    is_three_phase: bool = True
    installation_method: Literal["B", "C"] = "B"
    ambient_temp_c: int = Field(default=40, ge=10, le=70)


class FloorUnderlayRef(BaseModel):
    """Per-floor source DXF underlay metadata for multi-floor CAD overlay preservation."""

    model_config = ConfigDict(extra="forbid")

    floor: str
    source_file: str
    dx_mm: float = 0.0
    unit_scale_to_mm: float = Field(default=1.0, gt=0.0)


class BuildingModel(BaseModel):
    """Canonical architectural input contract consumed by electrical solvers."""

    model_config = ConfigDict(extra="forbid")

    project_name: str
    building_type: BuildingType
    units: Literal["mm"] = "mm"
    total_floor_area_m2: float = Field(gt=0.0)
    rooms: list[Room] = Field(min_length=1)
    walls: list[WallSegment] = Field(default_factory=list)
    doors: list[DoorOpening] = Field(default_factory=list)
    windows: list[WindowOpening] = Field(default_factory=list)
    db_anchor: DistributionBoardAnchor
    sub_boards: list[SubBoardSpec] = Field(default_factory=list)
    pv_capacity_kva: float = Field(default=0.0, ge=0.0)
    ev_charger_kw: float = Field(default=0.0, ge=0.0)
    extra_three_phase_load_kva: float = Field(default=0.0, ge=0.0)
    source_file: str | None = None
    source_sha256: str | None = None
    source_unit_scale_to_mm: float = Field(default=1.0, gt=0.0)
    floor_underlays: list[FloorUnderlayRef] = Field(default_factory=list)


class DxfLayerMapping(BaseModel):
    """Explicit architect layer-name mapping overriding heuristic DXF layer discovery."""

    model_config = ConfigDict(extra="forbid")

    rooms: list[str] = Field(default_factory=list)
    walls: list[str] = Field(default_factory=list)
    doors: list[str] = Field(default_factory=list)
    windows: list[str] = Field(default_factory=list)
    fixtures: list[str] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)
    db: list[str] = Field(default_factory=list)


class DxfLayerInfo(BaseModel):
    """Summary of one DXF layer discovered during pre-ingest layer inspection."""

    model_config = ConfigDict(extra="forbid")

    layer_name: str
    entity_count: int = Field(ge=0)
    entity_types: dict[str, int] = Field(default_factory=dict)
    inferred_role: Literal[
        "rooms", "walls", "doors", "windows", "fixtures", "text", "db", "unknown"
    ] = "unknown"
