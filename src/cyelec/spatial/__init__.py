"""Phase 2 spatial geometry: Section 701 wet zones, valid-wall placement, NetworkX routing."""

from cyelec.spatial.placement import compute_valid_wall_segments, place_electrical_devices
from cyelec.spatial.router import assign_devices_to_circuits, build_conduit_graph, route_circuits
from cyelec.spatial.wet_zones import (
    classify_point_wet_zone,
    compute_building_wet_zones,
    compute_room_wet_zones,
    is_point_in_socket_exclusion,
)

__all__ = [
    "assign_devices_to_circuits",
    "build_conduit_graph",
    "classify_point_wet_zone",
    "compute_building_wet_zones",
    "compute_room_wet_zones",
    "compute_valid_wall_segments",
    "is_point_in_socket_exclusion",
    "place_electrical_devices",
    "route_circuits",
]
