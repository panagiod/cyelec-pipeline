"""Distributed multi-node radial tree & Kirchhoff ring final electrical network solver.

Replaces naive lumped-length circuit approximations by walking the actual routed
NetworkX conduit topology from each distribution board to every placed load device:
- Radials: constructs the shortest-path tree from the DB, accumulates exact branch
  currents I_e down every segment, and finds the true worst-case terminal node for
  both voltage drop (distributed ampere-metre moment) and earth fault loop (Zs).
- Ring Finals: orders socket outlets sequentially around the closed ring loop and
  solves Kirchhoff parallel-leg current division to compute exact node-by-node
  voltage drop and worst-case parallel loop impedance length d_i*(L - d_i)/L.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import networkx as nx

from cyelec.models.building import BuildingModel, Point2D
from cyelec.models.electrical import CircuitSpec, CircuitTopology
from cyelec.models.spatial import DeviceType, PlacedDevice, RoutedCircuit

_NON_CONSUMING_CONTROL_DEVICES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.SWITCH,
        DeviceType.DIMMER_SWITCH,
        DeviceType.FAN_ISOLATOR_3P,
        DeviceType.COOKER_CONTROL_UNIT,
        DeviceType.WATER_HEATER_DP_SWITCH,
        DeviceType.SHUTTER_SWITCH,
        DeviceType.ROOM_THERMOSTAT,
        DeviceType.KNX_KEYPAD,
    }
)


@dataclass(frozen=True)
class DistributedCircuitMetrics:
    """Exact node-by-node electrical length & distributed voltage-drop factor."""

    circuit_id: str
    total_material_length_m: float
    electrical_path_length_m: float
    distributed_vd_factor: float
    worst_case_node_id: str | None


def _pt_key(pt: Point2D) -> tuple[int, int]:
    return (round(pt.x_mm), round(pt.y_mm))


def _dist_m(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1]) / 1000.0


def _nearest_graph_node(
    graph: nx.Graph,
    pt: Point2D,
) -> tuple[int, int]:
    target = _pt_key(pt)
    if target in graph:
        return target
    best: tuple[int, int] = min(
        (tuple(n) for n in graph.nodes),
        key=lambda n: _dist_m((int(n[0]), int(n[1])), target),
    )
    return (int(best[0]), int(best[1]))


def compute_radial_distributed_metrics(
    spec: CircuitSpec,
    db_loc: Point2D,
    route: RoutedCircuit,
    circuit_devices: list[PlacedDevice],
    ceiling_height_m: float = 2.9,
) -> DistributedCircuitMetrics:
    """Solve distributed branch currents on a radial circuit tree rooted at db_loc."""
    total_m = max(4.0, route.total_routed_length_m)
    load_devs = [d for d in circuit_devices if d.device_type not in _NON_CONSUMING_CONTROL_DEVICES]
    if not load_devs:
        load_devs = list(circuit_devices)

    # Single-load or unsegmented radials carry 100% of Ib along their full length
    if len(load_devs) <= 1 or not route.segments:
        only_id = load_devs[0].device_id if load_devs else None
        return DistributedCircuitMetrics(
            circuit_id=spec.circuit_id,
            total_material_length_m=total_m,
            electrical_path_length_m=total_m,
            distributed_vd_factor=1.0,
            worst_case_node_id=only_id,
        )

    # Build undirected weighted graph from actual routed conduit segments
    g: nx.Graph = nx.Graph()
    for seg in route.segments:
        u = _pt_key(seg.start_mm)
        v = _pt_key(seg.end_mm)
        if u == v:
            continue
        w = max(0.01, _dist_m(u, v))
        if g.has_edge(u, v):
            if w < g[u][v]["weight"]:
                g[u][v]["weight"] = w
        else:
            g.add_edge(u, v, weight=w)

    if g.number_of_nodes() < 2:
        return DistributedCircuitMetrics(
            circuit_id=spec.circuit_id,
            total_material_length_m=total_m,
            electrical_path_length_m=total_m,
            distributed_vd_factor=1.0,
            worst_case_node_id=load_devs[-1].device_id,
        )

    root = _nearest_graph_node(g, db_loc)
    lengths_map, paths_map = nx.single_source_dijkstra(g, root, weight="weight")

    # Assign proportional load shares (exact luminaire watts if known, else uniform)
    raw_weights = [
        float(d.luminaire_wattage_w)
        if (d.luminaire_wattage_w is not None and d.luminaire_wattage_w > 0.0)
        else 1.0
        for d in load_devs
    ]
    w_sum = sum(raw_weights) or 1.0
    dev_currents = [spec.design_current_ib_a * (w / w_sum) for w in raw_weights]

    # Accumulate downstream branch current on every directed tree edge (parent -> child)
    edge_current: dict[tuple[tuple[int, int], tuple[int, int]], float] = {}
    dev_paths: list[tuple[PlacedDevice, float, list[tuple[int, int]], float]] = []
    db_riser_m = max(0.5, ceiling_height_m - 1.6)

    for dev, i_dev in zip(load_devs, dev_currents, strict=True):
        node = _nearest_graph_node(g, dev.location_mm)
        if node not in paths_map:
            # Disconnected fallback: straight-line distance to DB
            straight_m = max(1.0, _dist_m(root, _pt_key(dev.location_mm)))
            dev_paths.append((dev, i_dev, [root], straight_m))
            continue
        path = list(paths_map[node])
        horiz_m = float(lengths_map[node])
        for a, b in itertools.pairwise(path):
            edge_current[(a, b)] = edge_current.get((a, b), 0.0) + i_dev
        dev_paths.append((dev, i_dev, path, horiz_m))

    # Evaluate exact ampere-metre moment M_k and electrical path length L_elec_k per load node
    max_moment = 0.0
    max_elec_len = 0.0
    worst_vd_dev: str | None = None
    worst_zs_dev: str | None = None

    for dev, i_dev, path, horiz_m in dev_paths:
        is_ceiling = dev.device_type in (
            DeviceType.LUMINAIRE,
            DeviceType.PENDANT_LIGHT,
            DeviceType.SMOKE_DETECTOR,
            DeviceType.HEAT_DETECTOR,
            DeviceType.WIFI_AP,
        )
        drop_m = (
            0.25 if is_ceiling else max(0.3, ceiling_height_m - dev.mounting_height_mm / 1000.0)
        )
        elec_len = min(total_m, max(1.5, db_riser_m + horiz_m + drop_m))

        moment = spec.design_current_ib_a * db_riser_m + i_dev * drop_m
        if len(path) >= 2:
            for a, b in itertools.pairwise(path):
                seg_len = float(g[a][b]["weight"])
                moment += edge_current.get((a, b), i_dev) * seg_len
        else:
            moment += i_dev * horiz_m

        if moment > max_moment:
            max_moment = moment
            worst_vd_dev = dev.device_id
        if elec_len > max_elec_len:
            max_elec_len = elec_len
            worst_zs_dev = dev.device_id

    elec_path_m = round(min(total_m, max(2.0, max_elec_len)), 2)
    denom = max(spec.design_current_ib_a * elec_path_m, 1e-6)
    vd_factor = round(min(1.0, max(0.25, max_moment / denom)), 4)

    return DistributedCircuitMetrics(
        circuit_id=spec.circuit_id,
        total_material_length_m=total_m,
        electrical_path_length_m=elec_path_m,
        distributed_vd_factor=vd_factor,
        worst_case_node_id=worst_vd_dev or worst_zs_dev,
    )


def compute_ring_kirchhoff_metrics(
    spec: CircuitSpec,
    db_loc: Point2D,
    route: RoutedCircuit,
    circuit_devices: list[PlacedDevice],
) -> DistributedCircuitMetrics:
    """Solve exact Kirchhoff parallel-leg current division around a closed ring final.

    For loads I_j placed at cumulative arc positions d_j along a ring of total length L:
      - Leg A current leaving DB: I_A = sum_j I_j * (L - d_j) / L
      - Node i ampere-metre drop: M_i = I_A * d_i - sum_{j < i} I_j * (d_i - d_j)
      - Node i parallel loop impedance length (for R1+R2 & Zs): L_zs_i = d_i * (L - d_i) / L
    Check limits:
      - Single point load at exact midpoint (d = L/2): L_zs = L/4, M = Ib*L/4 -> factor = 1.00.
      - Uniformly distributed loads around ring: L_zs = L/4, M = Ib*L/8 -> factor = 0.50.
    """
    total_l = max(4.0, route.total_routed_length_m)
    load_devs = [d for d in circuit_devices if d.device_type not in _NON_CONSUMING_CONTROL_DEVICES]
    if not load_devs:
        return DistributedCircuitMetrics(
            circuit_id=spec.circuit_id,
            total_material_length_m=total_l,
            electrical_path_length_m=round(total_l / 4.0, 2),
            distributed_vd_factor=1.0,
            worst_case_node_id=None,
        )

    # Order sockets sequentially around the ring via greedy nearest-neighbor from DB
    remaining = list(load_devs)
    ordered: list[PlacedDevice] = []
    cur_pt = _pt_key(db_loc)
    cum_raw: list[float] = []
    acc = 0.0
    while remaining:
        nxt = min(remaining, key=lambda d: _dist_m(cur_pt, _pt_key(d.location_mm)))
        acc += max(0.4, _dist_m(cur_pt, _pt_key(nxt.location_mm)))
        ordered.append(nxt)
        cum_raw.append(acc)
        cur_pt = _pt_key(nxt.location_mm)
        remaining.remove(nxt)
    return_leg = max(0.4, _dist_m(cur_pt, _pt_key(db_loc)))
    raw_loop = acc + return_leg

    # Map cumulative arc positions proportionally onto exact routed total_l (incl. vertical stubs)
    scale = total_l / max(raw_loop, 1e-6)
    d_pos = [min(total_l * 0.98, max(total_l * 0.02, c * scale)) for c in cum_raw]

    n = len(ordered)
    i_each = spec.design_current_ib_a / float(n)
    i_leg_a = sum(i_each * ((total_l - d) / total_l) for d in d_pos)

    max_moment = 0.0
    max_zs_eff = 0.0
    worst_node_id = ordered[0].device_id

    for idx, (dev, di) in enumerate(zip(ordered, d_pos, strict=True)):
        moment_i = i_leg_a * di - sum(i_each * (di - d_pos[j]) for j in range(idx))
        zs_eff_i = (di * (total_l - di)) / total_l
        if moment_i > max_moment:
            max_moment = moment_i
            worst_node_id = dev.device_id
        if zs_eff_i > max_zs_eff:
            max_zs_eff = zs_eff_i

    # Ensure conservative floor of at least L/4 when ring has >=2 outlets near midpoint
    elec_zs_len = round(max(1.0, min(total_l / 4.0, max_zs_eff)), 2)
    denom = max(spec.design_current_ib_a * elec_zs_len, 1e-6)
    # Cap within physically valid [0.50, 1.00] Kirchhoff range
    vd_factor = round(min(1.0, max(0.50, max_moment / denom)), 4)

    return DistributedCircuitMetrics(
        circuit_id=spec.circuit_id,
        total_material_length_m=total_l,
        electrical_path_length_m=elec_zs_len,
        distributed_vd_factor=vd_factor,
        worst_case_node_id=worst_node_id,
    )


def enrich_specs_with_distributed_network(
    building: BuildingModel,
    specs: list[CircuitSpec],
    devices: list[PlacedDevice],
    routed_circuits: list[RoutedCircuit],
) -> list[CircuitSpec]:
    """Compute exact distributed node-by-node metrics and inject onto CircuitSpec objects."""
    route_by_cid = {rc.circuit_id: rc for rc in routed_circuits}
    devs_by_cid: dict[str, list[PlacedDevice]] = {}
    for dev in devices:
        if dev.circuit_id:
            devs_by_cid.setdefault(dev.circuit_id, []).append(dev)

    board_loc: dict[str, Point2D] = {building.db_anchor.id: building.db_anchor.location_mm}
    for sb in building.sub_boards:
        board_loc[sb.board_id] = sb.location_mm

    rooms_by_id = {r.id: r for r in building.rooms}
    updated: list[CircuitSpec] = []

    for spec in specs:
        rc = route_by_cid.get(spec.circuit_id)
        if rc is None:
            updated.append(spec)
            continue
        db_pt = board_loc.get(spec.board_id, building.db_anchor.location_mm)
        c_devs = devs_by_cid.get(spec.circuit_id, [])
        ceil_m = (
            rooms_by_id[spec.room_ids[0]].ceiling_height_mm / 1000.0
            if (spec.room_ids and spec.room_ids[0] in rooms_by_id)
            else 2.9
        )
        if spec.topology == CircuitTopology.RING_FINAL:
            m = compute_ring_kirchhoff_metrics(spec, db_pt, rc, c_devs)
        else:
            m = compute_radial_distributed_metrics(spec, db_pt, rc, c_devs, ceil_m)
        updated.append(
            spec.model_copy(
                update={
                    "length_m": m.total_material_length_m,
                    "electrical_path_length_m": m.electrical_path_length_m,
                    "distributed_vd_factor": m.distributed_vd_factor,
                    "worst_case_node_id": m.worst_case_node_id,
                }
            )
        )
    return updated
