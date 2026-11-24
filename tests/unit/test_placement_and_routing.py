"""Unit tests for valid-wall device placement and NetworkX conduit routing."""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon

from cyelec.models.electrical import CircuitTopology, EacProfile
from cyelec.models.spatial import DeviceType
from cyelec.solver.grouper import generate_circuits
from cyelec.spatial.placement import place_electrical_devices
from cyelec.spatial.router import route_circuits
from cyelec.spatial.wet_zones import compute_building_wet_zones, is_point_in_socket_exclusion
from cyelec.standards_loader import load_standards
from cyelec.synth.sample_plan import build_cyprus_3bed_model


def test_valid_wall_placement_respects_wet_zones_windows_and_ip_ratings() -> None:
    std = load_standards()
    building = build_cyprus_3bed_model()
    wet_zones = compute_building_wet_zones(building, std, eac_profile=EacProfile.EAC_18TH_A2)
    wz_by_room = {w.room_id: w for w in wet_zones}

    devices, _flags = place_electrical_devices(building, wet_zones, std)

    sockets = [d for d in devices if d.device_type == DeviceType.SOCKET_TWIN]
    luminaires = [d for d in devices if d.device_type == DeviceType.LUMINAIRE]
    switches = [d for d in devices if d.device_type == DeviceType.SWITCH]
    acs = [d for d in devices if d.device_type == DeviceType.SPLIT_AC]

    assert len(sockets) >= 15
    assert len(luminaires) >= len(building.rooms)
    assert len(switches) == len(building.rooms)
    assert len(acs) == 4  # Living + 3 bedrooms

    # No socket may ever fall inside a Section 701 socket exclusion zone or window span
    window_lines = [
        LineString([(w.start.x_mm, w.start.y_mm), (w.end.x_mm, w.end.y_mm)]).buffer(100.0)
        for w in building.windows
    ]
    for sock in sockets:
        assert is_point_in_socket_exclusion(sock.location_mm, wz_by_room.get(sock.room_id)) is False
        pt = Point(sock.location_mm.x_mm, sock.location_mm.y_mm)
        assert all(not wbuf.contains(pt) for wbuf in window_lines)

    # Wet-room switches placed outside wet room polygons; wet-room luminaires rated IP44
    rooms_by_id = {r.id: r for r in building.rooms}
    for sw in switches:
        room = rooms_by_id[sw.room_id]
        if room.is_wet_room:
            rpoly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
            assert not rpoly.contains(Point(sw.location_mm.x_mm, sw.location_mm.y_mm))

    veranda_lums = [d for d in luminaires if rooms_by_id[d.room_id].is_outdoor]
    assert veranda_lums and all(d.ip_rating == "IP65" for d in veranda_lums)

    bath_lums = [d for d in luminaires if rooms_by_id[d.room_id].is_wet_room]
    assert bath_lums and all(d.ip_rating == "IP44" for d in bath_lums)


def test_networkx_conduit_router_radials_rings_and_trunk_grouping() -> None:
    std = load_standards()
    building = build_cyprus_3bed_model()
    wet_zones = compute_building_wet_zones(building, std)
    devices, _ = place_electrical_devices(building, wet_zones, std)
    specs = generate_circuits(building, std)

    bound_devices, routed, flags = route_circuits(building, specs, devices)

    assert len(routed) == len(specs)
    assert all(d.circuit_id is not None for d in bound_devices)

    db = building.db_anchor.location_mm
    specs_by_id = {s.circuit_id: s for s in specs}

    for rc in routed:
        assert len(rc.segments) >= 1
        assert rc.horizontal_length_m > 0.0
        assert rc.vertical_drops_m > 0.0
        assert rc.total_routed_length_m >= rc.horizontal_length_m + rc.vertical_drops_m - 0.1

        # First segment starts near DB1
        first_pt = rc.segments[0].start_mm
        assert math.hypot(first_pt.x_mm - db.x_mm, first_pt.y_mm - db.y_mm) < 20.0

        # Ring final loops must return back to DB1
        if specs_by_id[rc.circuit_id].topology == CircuitTopology.RING_FINAL:
            last_pt = rc.segments[-1].end_mm
            assert math.hypot(last_pt.x_mm - db.x_mm, last_pt.y_mm - db.y_mm) < 20.0

    assert any(f.code == "CONDUIT_TRUNK_GROUPING_ADVISORY" for f in flags)
