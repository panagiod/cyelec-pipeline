"""Phase 2 spatial geometry, BS 7671 Section 701 wet-zone, placement, and conduit routing models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from cyelec.models.building import Point2D
from cyelec.models.electrical import ElectricalDesignResult, ReviewFlag


class DeviceType(StrEnum):
    """Placed electrical accessory or equipment symbol type."""

    LUMINAIRE = "luminaire"
    SWITCH = "switch"
    SOCKET_TWIN = "socket_twin"
    SPLIT_AC = "split_ac"
    COOKER_OUTLET = "cooker_outlet"
    OVEN_OUTLET = "oven_outlet"
    APPLIANCE_OUTLET = "appliance_outlet"
    WATER_HEATER_ISOLATOR = "water_heater_isolator"


class RoomWetZones(BaseModel):
    """BS 7671 Section 701 wet-zone polygons clipped to the enclosing room boundary (mm)."""

    model_config = ConfigDict(extra="forbid")

    room_id: str
    fixture_ids: list[str] = Field(default_factory=list)
    exclusion_radius_m: float = Field(gt=0.0)
    zone_0_polygons_mm: list[list[Point2D]] = Field(default_factory=list)
    zone_1_polygons_mm: list[list[Point2D]] = Field(default_factory=list)
    zone_2_polygons_mm: list[list[Point2D]] = Field(default_factory=list)
    socket_exclusion_polygons_mm: list[list[Point2D]] = Field(default_factory=list)


class PlacedDevice(BaseModel):
    """Deterministically placed electrical device on a valid wall or ceiling coordinate."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    room_id: str
    device_type: DeviceType
    label: str
    location_mm: Point2D
    mounting_height_mm: float = Field(ge=0.0)
    wall_angle_deg: float = 0.0
    ip_rating: str = "IP20"
    circuit_id: str | None = None
    appliance_key: str | None = None


class ConduitSegment(BaseModel):
    """Single straight routed conduit segment between two 2D waypoints (mm)."""

    model_config = ConfigDict(extra="forbid")

    start_mm: Point2D
    end_mm: Point2D
    length_m: float = Field(ge=0.0)
    edge_key: str


class RoutedCircuit(BaseModel):
    """NetworkX-routed conduit geometry and true length for a single final circuit."""

    model_config = ConfigDict(extra="forbid")

    circuit_id: str
    device_ids: list[str] = Field(default_factory=list)
    segments: list[ConduitSegment] = Field(default_factory=list)
    horizontal_length_m: float = Field(ge=0.0)
    vertical_drops_m: float = Field(ge=0.0)
    total_routed_length_m: float = Field(gt=0.0)
    max_parallel_circuits_on_trunk: int = Field(default=1, ge=1)


class SpatialLayoutResult(BaseModel):
    """Complete Phase 2 spatial analysis: wet zones, valid-wall placements, and conduit routes."""

    model_config = ConfigDict(extra="forbid")

    wet_zones: list[RoomWetZones] = Field(default_factory=list)
    devices: list[PlacedDevice] = Field(default_factory=list)
    routed_circuits: list[RoutedCircuit] = Field(default_factory=list)
    total_conduit_m: float = Field(ge=0.0)
    flags: list[ReviewFlag] = Field(default_factory=list)


class Phase2DesignResult(BaseModel):
    """End-to-end Phase 2 deliverable combining spatial layout with BS 7671/MILP/EAC design."""

    model_config = ConfigDict(extra="forbid")

    design: ElectricalDesignResult
    spatial: SpatialLayoutResult
