"""Phase 2 spatial geometry, BS 7671 Section 701 wet-zone, placement, and conduit routing models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from cyelec.models.building import Point2D
from cyelec.models.electrical import ElectricalDesignResult, ReviewFlag


class DeviceType(StrEnum):
    """Placed electrical accessory or equipment symbol type."""

    LUMINAIRE = "luminaire"
    PENDANT_LIGHT = "pendant_light"
    WALL_SCONCE = "wall_sconce"
    LINEAR_COVE = "linear_cove"
    OUTDOOR_PIR_LIGHT = "outdoor_pir_light"
    MIRROR_LIGHT = "mirror_light"
    EXHAUST_FAN = "exhaust_fan"
    SWITCH = "switch"
    DIMMER_SWITCH = "dimmer_switch"
    FAN_ISOLATOR_3P = "fan_isolator_3p"
    SOCKET_TWIN = "socket_twin"
    SPLIT_AC = "split_ac"
    SHUTTER_SWITCH = "shutter_switch"
    HEAT_PUMP_OUTDOOR = "heat_pump_outdoor"
    HEAT_PUMP_HYDROBOX = "heat_pump_hydrobox"
    UFH_MANIFOLD = "ufh_manifold"
    ROOM_THERMOSTAT = "room_thermostat"
    VRV_OUTDOOR = "vrv_outdoor"
    VRV_INDOOR_ISOLATOR = "vrv_indoor_isolator"
    WATER_BOOSTER_PUMP = "water_booster_pump"
    GATE_MOTOR = "gate_motor"
    EV_WALLBOX = "ev_wallbox"
    POOL_SUB_DB = "pool_sub_db"
    KITCHEN_FCU = "kitchen_fcu"
    COOKER_CONTROL_UNIT = "cooker_control_unit"
    COOKER_OUTLET = "cooker_outlet"
    OVEN_OUTLET = "oven_outlet"
    APPLIANCE_OUTLET = "appliance_outlet"
    WATER_HEATER_DP_SWITCH = "water_heater_dp_switch"
    WATER_HEATER_ISOLATOR = "water_heater_isolator"
    EAC_METER_CABINET = "eac_meter_cabinet"
    EARTH_PIT = "earth_pit"
    # ELV / Weak-Current / Life Safety / KNX / Site
    TELECOM_CABINET = "telecom_cabinet"
    DATA_RJ45 = "data_rj45"
    TV_OUTLET = "tv_outlet"
    WIFI_AP = "wifi_ap"
    INTERCOM_OUTDOOR = "intercom_outdoor"
    INTERCOM_INDOOR = "intercom_indoor"
    ALARM_PANEL = "alarm_panel"
    ALARM_PIR = "alarm_pir"
    ALARM_KEYPAD = "alarm_keypad"
    ALARM_SIREN = "alarm_siren"
    CCTV_CAMERA = "cctv_camera"
    SMOKE_DETECTOR = "smoke_detector"
    HEAT_DETECTOR = "heat_detector"
    KNX_KEYPAD = "knx_keypad"
    GARDEN_BOLLARD = "garden_bollard"


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
    switch_group: str | None = None
    switch_type: str | None = None
    controlled_device_ids: list[str] = Field(default_factory=list)
    luminaire_wattage_w: float | None = None
    luminaire_fixture_type: str | None = None
    lumens: int | None = None
    target_lux: int | None = None


class SwitchLeg(BaseModel):
    """Logical switch-to-luminaire/fan control wiring link rendered on E-101 Lighting Plan."""

    model_config = ConfigDict(extra="forbid")

    switch_id: str
    room_id: str
    group_label: str
    switch_type: str = "1G1W"
    is_two_way: bool = False
    target_device_ids: list[str] = Field(default_factory=list)
    circuit_id: str | None = None


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
    switch_legs: list[SwitchLeg] = Field(default_factory=list)
    routed_circuits: list[RoutedCircuit] = Field(default_factory=list)
    total_conduit_m: float = Field(ge=0.0)
    flags: list[ReviewFlag] = Field(default_factory=list)


class Phase2DesignResult(BaseModel):
    """End-to-end Phase 2 deliverable combining spatial layout with BS 7671/MILP/EAC design."""

    model_config = ConfigDict(extra="forbid")

    design: ElectricalDesignResult
    spatial: SpatialLayoutResult
