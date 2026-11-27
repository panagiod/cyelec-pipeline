"""Deterministic NetworkX conduit router computing true cable lengths and trunk grouping."""

from __future__ import annotations

import heapq
import math
from collections import Counter

import networkx as nx

from cyelec.models.building import BuildingModel, Point2D, Room
from cyelec.models.electrical import (
    CircuitCategory,
    CircuitSpec,
    CircuitTopology,
    ReviewFlag,
    ReviewSeverity,
)
from cyelec.models.spatial import ConduitSegment, DeviceType, PlacedDevice, RoutedCircuit

_SNAP_MM = 10.0  # Snap tolerance when merging identical graph vertices
_SNAP_INT = int(_SNAP_MM)


def _node_key(pt: Point2D) -> tuple[int, int]:
    return (round(pt.x_mm / _SNAP_MM) * _SNAP_INT, round(pt.y_mm / _SNAP_MM) * _SNAP_INT)


def _node_key_xy(x_mm: float, y_mm: float) -> tuple[int, int]:
    return (round(x_mm / _SNAP_MM) * _SNAP_INT, round(y_mm / _SNAP_MM) * _SNAP_INT)


def _dist_m(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1]) / 1000.0


def _edge_id(u: tuple[int, int], v: tuple[int, int]) -> str:
    a, b = (u, v) if u <= v else (v, u)
    return f"{a[0]},{a[1]}->{b[0]},{b[1]}"


def _add_weighted_edge(
    graph: nx.Graph,
    u: tuple[int, int],
    v: tuple[int, int],
    *,
    penalty: float = 1.0,
    kind: str = "wall",
) -> None:
    if u == v:
        return
    dist = _dist_m(u, v)
    weight = dist * penalty
    if graph.has_edge(u, v):
        if weight < float(graph[u][v]["weight"]):
            graph[u][v]["weight"] = weight
            graph[u][v]["length_m"] = dist
            graph[u][v]["kind"] = kind
    else:
        graph.add_edge(u, v, weight=weight, length_m=dist, kind=kind)


def _project_point_onto_segment(
    pt: Point2D,
    a: tuple[int, int],
    b: tuple[int, int],
) -> tuple[tuple[int, int], float]:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-6:
        return a, math.hypot(pt.x_mm - ax, pt.y_mm - ay)
    t = max(0.0, min(1.0, ((pt.x_mm - ax) * dx + (pt.y_mm - ay) * dy) / seg_len_sq))
    px = ax + t * dx
    py = ay + t * dy
    proj = _node_key_xy(px, py)
    return proj, math.hypot(pt.x_mm - px, pt.y_mm - py)


def _attach_node_to_graph(
    graph: nx.Graph,
    pt: Point2D,
    *,
    kind: str = "drop",
) -> tuple[int, int]:
    """Attach a 2D point to the nearest existing graph edge (splitting it cleanly)."""
    node = _node_key(pt)
    if node in graph:
        return node

    x = pt.x_mm
    y = pt.y_mm
    best_edge: tuple[tuple[int, int], tuple[int, int]] | None = None
    best_px = 0.0
    best_py = 0.0
    best_d_sq = float("inf")

    for u, v in graph.edges():
        ax, ay = u
        bx, by = v
        # Exact AABB lower-bound rejection skips distant edges without float division
        dx_box = (ax - x) if x < ax else ((x - bx) if x > bx else 0.0)
        if ax > bx:
            dx_box = (bx - x) if x < bx else ((x - ax) if x > ax else 0.0)
        dy_box = (ay - y) if y < ay else ((y - by) if y > by else 0.0)
        if ay > by:
            dy_box = (by - y) if y < by else ((y - ay) if y > ay else 0.0)
        if dx_box * dx_box + dy_box * dy_box > best_d_sq:
            continue

        dx = bx - ax
        dy = by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-6:
            px, py = float(ax), float(ay)
        else:
            t = ((x - ax) * dx + (y - ay) * dy) / seg_len_sq
            if t <= 0.0:
                px, py = float(ax), float(ay)
            elif t >= 1.0:
                px, py = float(bx), float(by)
            else:
                px = ax + t * dx
                py = ay + t * dy
        ddx = x - px
        ddy = y - py
        d_sq = ddx * ddx + ddy * ddy
        if d_sq < best_d_sq:
            best_d_sq = d_sq
            best_edge = (u, v)
            best_px = px
            best_py = py

    graph.add_node(node)
    if best_edge is not None:
        u, v = best_edge
        best_proj = _node_key_xy(best_px, best_py)
        old_data = dict(graph[u][v])
        old_kind = str(old_data.get("kind", "wall"))
        old_len = float(old_data.get("length_m", 1.0))
        old_pen = float(old_data.get("weight", old_len)) / max(old_len, 1e-6)
        if best_proj != u and best_proj != v:
            graph.remove_edge(u, v)
            _add_weighted_edge(graph, u, best_proj, penalty=old_pen, kind=old_kind)
            _add_weighted_edge(graph, best_proj, v, penalty=old_pen, kind=old_kind)
        if best_proj != node:
            _add_weighted_edge(graph, node, best_proj, penalty=1.0, kind=kind)
    return node


def build_conduit_graph(
    building: BuildingModel,
    devices: list[PlacedDevice],
) -> tuple[nx.Graph, tuple[int, int], dict[str, tuple[int, int]]]:
    """Build deterministic NetworkX wall + corridor + doorway conduit routing graph."""
    graph = nx.Graph()

    # 1. Room perimeter walls (preferred masonry conduit paths, penalty = 1.0)
    for room in building.rooms:
        pts = room.polygon_mm
        n = len(pts)
        room_nodes = [_node_key(p) for p in pts]
        mids = [
            _node_key_xy(
                (pts[i].x_mm + pts[(i + 1) % n].x_mm) / 2.0,
                (pts[i].y_mm + pts[(i + 1) % n].y_mm) / 2.0,
            )
            for i in range(n)
        ]
        for i in range(n):
            u = room_nodes[i]
            v = room_nodes[(i + 1) % n]
            # Add midpoint node along long edges for cleaner orthogonal transitions
            mid = mids[i]
            _add_weighted_edge(graph, u, mid, penalty=1.0, kind="wall")
            _add_weighted_edge(graph, mid, v, penalty=1.0, kind="wall")

        # Ceiling centroid connected to wall midpoints (slab conduit drops, slight penalty 1.05)
        centroid = _node_key(room.centroid_mm())
        for mid in mids:
            _add_weighted_edge(graph, centroid, mid, penalty=1.05, kind="ceiling")

    # 2. Explicit architectural walls
    for wall in building.walls:
        _add_weighted_edge(
            graph, _node_key(wall.start), _node_key(wall.end), penalty=1.0, kind="wall"
        )

    # 3. Attach DB1 and every LV powered indoor electrical device cleanly onto the routing graph
    _NON_ROUTED_TYPES = frozenset(
        {
            DeviceType.EAC_METER_CABINET,
            DeviceType.EARTH_PIT,
            DeviceType.TELECOM_CABINET,
            DeviceType.DATA_RJ45,
            DeviceType.TV_OUTLET,
            DeviceType.WIFI_AP,
            DeviceType.INTERCOM_OUTDOOR,
            DeviceType.INTERCOM_INDOOR,
            DeviceType.ALARM_PANEL,
            DeviceType.ALARM_PIR,
            DeviceType.ALARM_KEYPAD,
            DeviceType.ALARM_SIREN,
            DeviceType.CCTV_CAMERA,
            DeviceType.KNX_KEYPAD,
            DeviceType.GARDEN_BOLLARD,
            DeviceType.HEAT_PUMP_OUTDOOR,
            DeviceType.VRV_OUTDOOR,
            DeviceType.GATE_MOTOR,
            DeviceType.EV_WALLBOX,
            DeviceType.POOL_SUB_DB,
            DeviceType.OUTDOOR_PIR_LIGHT,
        }
    )
    db_node = _attach_node_to_graph(graph, building.db_anchor.location_mm, kind="db")
    device_nodes: dict[str, tuple[int, int]] = {}
    for dev in devices:
        if dev.device_type in _NON_ROUTED_TYPES:
            continue
        device_nodes[dev.device_id] = _attach_node_to_graph(graph, dev.location_mm, kind="device")

    # 4. Ensure global connectivity via closest-pair MST (keeps intra-floor detached rooms local
    # before bridging across wide empty gaps between side-by-side floor clusters)
    components = [list(c) for c in nx.connected_components(graph)]
    while len(components) > 1:
        best_d_sq = float("inf")
        best_i, best_j = 0, 1
        best_pair: tuple[tuple[int, int], tuple[int, int]] | None = None
        for i in range(len(components)):
            comp_i = components[i]
            for j in range(i + 1, len(components)):
                comp_j = components[j]
                for u in comp_i:
                    ux, uy = u
                    for v in comp_j:
                        dx = ux - v[0]
                        dy = uy - v[1]
                        d_sq = dx * dx + dy * dy
                        if d_sq < best_d_sq:
                            best_d_sq = d_sq
                            best_i, best_j = i, j
                            best_pair = (u, v)
        if best_pair is None:
            break
        best_dist = math.sqrt(best_d_sq) / 1000.0
        bridge_pen = 5.0 if best_dist > 1.5 else 1.25
        _add_weighted_edge(graph, best_pair[0], best_pair[1], penalty=bridge_pen, kind="bridge")
        components[best_i].extend(components[best_j])
        components.pop(best_j)

    return graph, db_node, device_nodes


def assign_devices_to_circuits(
    circuits: list[CircuitSpec],
    devices: list[PlacedDevice],
) -> tuple[list[PlacedDevice], dict[str, list[PlacedDevice]]]:
    """Deterministically bind each placed device to its supplying CircuitSpec or ELV bus."""
    updated_devices: list[PlacedDevice] = []
    by_circuit: dict[str, list[PlacedDevice]] = {c.circuit_id: [] for c in circuits}

    lighting_by_room: dict[str, str] = {}
    socket_by_room: dict[str, str] = {}
    ac_by_room: dict[str, str] = {}
    shutter_by_room: dict[str, str] = {}
    app_by_category_room: dict[tuple[str, str], str] = {}
    app_by_category: dict[str, str] = {}
    first_by_category: dict[CircuitCategory, str] = {}
    garden_light_cid: str | None = None

    for c in circuits:
        first_by_category.setdefault(c.category, c.circuit_id)
        if c.category == CircuitCategory.LIGHTING and "garden" in c.description.lower():
            garden_light_cid = c.circuit_id
        for rid in c.room_ids:
            if c.category == CircuitCategory.LIGHTING and "garden" not in c.description.lower():
                lighting_by_room[rid] = c.circuit_id
            elif c.category in (CircuitCategory.SOCKET_RING, CircuitCategory.SOCKET_RADIAL):
                socket_by_room[rid] = c.circuit_id
            elif c.category == CircuitCategory.SPLIT_AC:
                ac_by_room[rid] = c.circuit_id
            elif c.category == CircuitCategory.SHUTTERS:
                shutter_by_room[rid] = c.circuit_id
            elif c.category in (
                CircuitCategory.COOKER,
                CircuitCategory.OVEN,
                CircuitCategory.APPLIANCE,
                CircuitCategory.WATER_HEATER,
            ):
                app_by_category_room[(c.category.value, rid)] = c.circuit_id
        if c.category in (
            CircuitCategory.COOKER,
            CircuitCategory.OVEN,
            CircuitCategory.APPLIANCE,
            CircuitCategory.WATER_HEATER,
        ):
            app_by_category.setdefault(c.category.value, c.circuit_id)

    # Distinguish dedicated appliance / commercial plant / UPS / lift outlets
    appliance_circuits = [
        c
        for c in circuits
        if c.category
        in (
            CircuitCategory.APPLIANCE,
            CircuitCategory.UPS_SERVER,
            CircuitCategory.HVAC_3P,
            CircuitCategory.LIFT,
            CircuitCategory.SOCKET_RADIAL,
        )
    ]

    for dev in devices:
        cid: str | None = None
        if dev.device_type in (
            DeviceType.LUMINAIRE,
            DeviceType.PENDANT_LIGHT,
            DeviceType.WALL_SCONCE,
            DeviceType.LINEAR_COVE,
            DeviceType.MIRROR_LIGHT,
            DeviceType.EXHAUST_FAN,
            DeviceType.SWITCH,
            DeviceType.DIMMER_SWITCH,
            DeviceType.FAN_ISOLATOR_3P,
        ):
            cid = lighting_by_room.get(dev.room_id) or first_by_category.get(
                CircuitCategory.LIGHTING
            )
        elif dev.device_type in (DeviceType.OUTDOOR_PIR_LIGHT, DeviceType.GARDEN_BOLLARD):
            cid = (
                garden_light_cid
                or lighting_by_room.get(dev.room_id)
                or first_by_category.get(CircuitCategory.LIGHTING)
                or "SITE-LIGHT"
            )
        elif dev.device_type in (DeviceType.SOCKET_TWIN, DeviceType.KITCHEN_FCU):
            cid = socket_by_room.get(dev.room_id) or first_by_category.get(
                CircuitCategory.SOCKET_RING
            )
        elif dev.device_type in (DeviceType.SPLIT_AC, DeviceType.VRV_INDOOR_ISOLATOR):
            cid = ac_by_room.get(dev.room_id) or first_by_category.get(CircuitCategory.SPLIT_AC)
        elif dev.device_type == DeviceType.SHUTTER_SWITCH:
            cid = shutter_by_room.get(dev.room_id) or first_by_category.get(
                CircuitCategory.SHUTTERS
            )
        elif dev.device_type in (
            DeviceType.HEAT_PUMP_OUTDOOR,
            DeviceType.HEAT_PUMP_HYDROBOX,
            DeviceType.UFH_MANIFOLD,
        ):
            cid = first_by_category.get(CircuitCategory.HEAT_PUMP)
        elif dev.device_type == DeviceType.VRV_OUTDOOR:
            cid = first_by_category.get(CircuitCategory.HVAC_3P) or first_by_category.get(
                CircuitCategory.SPLIT_AC
            )
        elif dev.device_type == DeviceType.ROOM_THERMOSTAT:
            cid = (
                first_by_category.get(CircuitCategory.HEAT_PUMP)
                or first_by_category.get(CircuitCategory.SPLIT_AC)
                or "HVAC-CTRL"
            )
        elif dev.device_type == DeviceType.WATER_BOOSTER_PUMP:
            cid = first_by_category.get(CircuitCategory.WATER_PUMP)
        elif dev.device_type == DeviceType.GATE_MOTOR:
            cid = first_by_category.get(CircuitCategory.GATE_GARAGE)
        elif dev.device_type == DeviceType.EV_WALLBOX:
            cid = first_by_category.get(CircuitCategory.EV_CHARGER)
        elif dev.device_type == DeviceType.POOL_SUB_DB:
            cid = first_by_category.get(CircuitCategory.POOL)
        elif dev.device_type in (DeviceType.SMOKE_DETECTOR, DeviceType.HEAT_DETECTOR):
            cid = (
                first_by_category.get(CircuitCategory.SMOKE_DETECTION)
                or first_by_category.get(CircuitCategory.FIRE_ALARM)
                or "ELV-SMOKE"
            )
        elif dev.device_type == DeviceType.TELECOM_CABINET:
            cid = first_by_category.get(CircuitCategory.ELV_POWER) or "ELV-RACK"
        elif dev.device_type == DeviceType.DATA_RJ45:
            cid = "ELV-DATA"
        elif dev.device_type == DeviceType.TV_OUTLET:
            cid = "ELV-TV"
        elif dev.device_type == DeviceType.WIFI_AP:
            cid = "ELV-WIFI"
        elif dev.device_type in (DeviceType.INTERCOM_OUTDOOR, DeviceType.INTERCOM_INDOOR):
            cid = "ELV-INTERCOM"
        elif dev.device_type in (
            DeviceType.ALARM_PANEL,
            DeviceType.ALARM_PIR,
            DeviceType.ALARM_KEYPAD,
            DeviceType.ALARM_SIREN,
        ):
            cid = "ELV-ALARM"
        elif dev.device_type == DeviceType.CCTV_CAMERA:
            cid = "ELV-CCTV"
        elif dev.device_type == DeviceType.KNX_KEYPAD:
            cid = first_by_category.get(CircuitCategory.KNX_BUS) or "KNX-BUS"
        elif dev.device_type in (DeviceType.COOKER_OUTLET, DeviceType.COOKER_CONTROL_UNIT):
            cid = app_by_category_room.get(("cooker", dev.room_id)) or app_by_category.get("cooker")
        elif dev.device_type == DeviceType.OVEN_OUTLET:
            cid = app_by_category_room.get(("oven", dev.room_id)) or app_by_category.get("oven")
        elif dev.device_type in (
            DeviceType.WATER_HEATER_ISOLATOR,
            DeviceType.WATER_HEATER_DP_SWITCH,
        ):
            cid = app_by_category_room.get(("water_heater", dev.room_id)) or app_by_category.get(
                "water_heater"
            )
        elif dev.device_type == DeviceType.EAC_METER_CABINET:
            cid = "EAC-INCOMER"
        elif dev.device_type == DeviceType.EARTH_PIT:
            cid = "EARTHING"
        elif dev.device_type == DeviceType.APPLIANCE_OUTLET:
            # Match by appliance key/label or room
            for ac in appliance_circuits:
                key_match = bool(
                    dev.appliance_key
                    and dev.appliance_key.replace("_", " ") in ac.description.lower()
                )
                word_match = any(w in ac.description.lower() for w in dev.label.lower().split()[:1])
                if dev.room_id in ac.room_ids and (key_match or word_match):
                    cid = ac.circuit_id
                    break
            if cid is None:
                cid = app_by_category_room.get(("appliance", dev.room_id)) or app_by_category.get(
                    "appliance"
                )
            if cid is None:
                cid = next(
                    (ac.circuit_id for ac in appliance_circuits if dev.room_id in ac.room_ids),
                    None,
                )

        bound = dev.model_copy(update={"circuit_id": cid})
        updated_devices.append(bound)
        if cid is not None and cid in by_circuit:
            by_circuit[cid].append(bound)

    return updated_devices, by_circuit


def _nearest_target_dijkstra(
    graph: nx.Graph,
    start: tuple[int, int],
    target_set: set[tuple[int, int]],
) -> tuple[int, int]:
    """Find the exact nearest node in target_set via early-stopping Dijkstra."""
    adj = graph._adj  # Direct adjacency dict avoids NetworkX decorator/backend overhead
    dist: dict[tuple[int, int], float] = {start: 0.0}
    heap: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    best_dist: float | None = None
    best_candidates: list[tuple[float, tuple[int, int]]] = []

    while heap:
        d, u = heapq.heappop(heap)
        if best_dist is not None and d > best_dist + 1e-12:
            break
        if d > dist.get(u, float("inf")):
            continue
        if u in target_set:
            if best_dist is None or d < best_dist:
                best_dist = d
            best_candidates.append((d, u))
            continue
        for v, edata in adj[u].items():
            nd = d + float(edata["weight"])
            if best_dist is not None and nd > best_dist + 1e-12:
                continue
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))

    if best_candidates:
        return min(best_candidates)[1]
    return min(target_set)


def _order_waypoints_greedy(
    graph: nx.Graph,
    start: tuple[int, int],
    targets: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Deterministic nearest-neighbor ordering of target device nodes along graph shortest paths."""
    remaining = set(targets)
    ordered: list[tuple[int, int]] = []
    curr = start
    while remaining:
        if len(remaining) == 1:
            nxt = next(iter(remaining))
        else:
            nxt = _nearest_target_dijkstra(graph, curr, remaining)
        ordered.append(nxt)
        remaining.remove(nxt)
        curr = nxt
    return ordered


def route_circuits(
    building: BuildingModel,
    circuits: list[CircuitSpec],
    devices: list[PlacedDevice],
    *,
    db_mounting_height_mm: float = 1500.0,
) -> tuple[list[PlacedDevice], list[RoutedCircuit], list[ReviewFlag]]:
    """Route all circuits on a NetworkX conduit graph and measure true lengths & trunk sharing."""
    bound_devices, by_circuit = assign_devices_to_circuits(circuits, devices)
    graph, db_node, device_nodes = build_conduit_graph(building, bound_devices)
    sub_board_nodes: dict[str, tuple[int, int]] = {
        sb.board_id: _attach_node_to_graph(graph, sb.location_mm, kind="sub_board")
        for sb in building.sub_boards
    }
    rooms_by_id: dict[str, Room] = {r.id: r for r in building.rooms}

    circuit_edge_sets: dict[str, set[str]] = {}
    edge_circuit_counts: Counter[str] = Counter()
    raw_routes: list[tuple[str, list[str], list[ConduitSegment], float, float]] = []

    board_floor_map: dict[str, str] = {sb.board_id: sb.floor for sb in building.sub_boards}
    if building.sub_boards and building.db_anchor.id not in board_floor_map:
        board_floor_map[building.db_anchor.id] = building.sub_boards[0].floor

    for spec in circuits:
        origin_node = sub_board_nodes.get(spec.board_id, db_node)
        c_devs = by_circuit.get(spec.circuit_id, [])
        board_floor = board_floor_map.get(spec.board_id)
        if board_floor is not None:
            same_floor_devs = [
                d
                for d in c_devs
                if d.room_id not in rooms_by_id or rooms_by_id[d.room_id].floor == board_floor
            ]
            off_floor_count = len(c_devs) - len(same_floor_devs)
        else:
            same_floor_devs = c_devs
            off_floor_count = 0

        target_nodes = [
            device_nodes[d.device_id] for d in same_floor_devs if d.device_id in device_nodes
        ]

        if not target_nodes:
            # Fallback to target room centroid on the same floor if no explicit devices bound
            fallback_rooms = [
                rooms_by_id[rid]
                for rid in spec.room_ids
                if rid in rooms_by_id
                and (board_floor is None or rooms_by_id[rid].floor == board_floor)
            ]
            target_nodes = [
                _attach_node_to_graph(graph, r.centroid_mm(), kind="fallback")
                for r in fallback_rooms
            ]

        ordered_targets = _order_waypoints_greedy(graph, origin_node, target_nodes)
        waypoint_seq = [origin_node, *ordered_targets]
        if spec.topology == CircuitTopology.RING_FINAL and ordered_targets:
            waypoint_seq.append(origin_node)

        segments: list[ConduitSegment] = []
        horiz_m = 0.0
        used_edges: set[str] = set()

        for src, dst in zip(waypoint_seq, waypoint_seq[1:], strict=False):
            if src == dst:
                continue
            path_nodes = nx.shortest_path(graph, src, dst, weight="weight")
            for u, v in zip(path_nodes, path_nodes[1:], strict=False):
                seg_len = _dist_m(u, v)
                if seg_len <= 1e-6:
                    continue
                ekey = _edge_id(u, v)
                used_edges.add(ekey)
                horiz_m += seg_len
                segments.append(
                    ConduitSegment(
                        start_mm=Point2D(x_mm=float(u[0]), y_mm=float(u[1])),
                        end_mm=Point2D(x_mm=float(v[0]), y_mm=float(v[1])),
                        length_m=round(seg_len, 3),
                        edge_key=ekey,
                    )
                )

        # Vertical riser from DB1 to slab ceiling + drops down to each wall-mounted device
        ceiling_mm = (
            rooms_by_id[spec.room_ids[0]].ceiling_height_mm
            if spec.room_ids and spec.room_ids[0] in rooms_by_id
            else 2900.0
        )
        if spec.topology == CircuitTopology.RING_FINAL:
            # Cyprus masonry ring final: 2 legs drop from DB to floor screed + rise/return stub
            # at each socket box from floor screed (typically +450 mm -> 0.90 m per twin socket).
            vertical_m = 2.0 * max(0.8, db_mounting_height_mm / 1000.0)
            if off_floor_count > 0:
                vertical_m += 3.5 + 1.5 * min(off_floor_count, 4)
            for dev in same_floor_devs:
                stub_h = min(max(0.3, dev.mounting_height_mm / 1000.0), 1.2)
                vertical_m += 2.0 * stub_h
        else:
            riser_count = 1
            vertical_m = riser_count * max(0.5, (ceiling_mm - db_mounting_height_mm) / 1000.0)
            if off_floor_count > 0:
                vertical_m += 3.5 + 1.5 * min(off_floor_count, 4)
            for dev in same_floor_devs:
                if dev.device_type in (
                    DeviceType.LUMINAIRE,
                    DeviceType.PENDANT_LIGHT,
                    DeviceType.SMOKE_DETECTOR,
                    DeviceType.HEAT_DETECTOR,
                    DeviceType.WIFI_AP,
                ):
                    vertical_m += 0.25  # Ceiling rose tail
                else:
                    drop_m = max(0.3, (ceiling_mm - dev.mounting_height_mm) / 1000.0)
                    vertical_m += drop_m
            # Cyprus rooftop solar water heater immersion tank sits on roof slab (~13-16 m run)
            if spec.category == CircuitCategory.WATER_HEATER:
                floors_above = max(1, len({r.floor for r in building.rooms}))
                vertical_m += round(floors_above * (ceiling_mm / 1000.0) + 8.5, 2)

        circuit_edge_sets[spec.circuit_id] = used_edges
        for ekey in used_edges:
            edge_circuit_counts[ekey] += 1

        raw_routes.append(
            (
                spec.circuit_id,
                [d.device_id for d in c_devs],
                segments,
                round(horiz_m, 2),
                round(vertical_m, 2),
            )
        )

    routed: list[RoutedCircuit] = []
    flags: list[ReviewFlag] = []
    high_trunk_edges = [e for e, cnt in edge_circuit_counts.items() if cnt >= 4]

    for cid, dev_ids, segs, horiz_m, vert_m in raw_routes:
        used = circuit_edge_sets.get(cid, set())
        max_par = max((edge_circuit_counts[e] for e in used), default=1)
        total_m = round(max(4.0, horiz_m + vert_m), 2)
        routed.append(
            RoutedCircuit(
                circuit_id=cid,
                device_ids=dev_ids,
                segments=segs,
                horizontal_length_m=horiz_m,
                vertical_drops_m=vert_m,
                total_routed_length_m=total_m,
                max_parallel_circuits_on_trunk=max_par,
            )
        )

    if high_trunk_edges:
        peak_shared = max(edge_circuit_counts[e] for e in high_trunk_edges)
        flags.append(
            ReviewFlag(
                code="CONDUIT_TRUNK_GROUPING_ADVISORY",
                severity=ReviewSeverity.INFO,
                category="geometry",
                regulation_ref="BS 7671 Table 4C1 (Grouping Factor Cg)",
                message=(
                    f"Up to {peak_shared} circuits share {len(high_trunk_edges)} main corridor/DB "
                    "conduit trunk edge(s). Ensure conduits are spaced >= 2x diameter in masonry/"
                    "slab or apply explicit Cg overrides via DesignOverrides."
                ),
            )
        )

    return bound_devices, routed, flags
