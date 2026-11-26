"""Deterministic vector SVG renderer for architectural floorplan + Phase 2 electrical overlay."""

from __future__ import annotations

import html
import math
from typing import Literal

from cyelec.models.building import Point2D
from cyelec.models.electrical import CircuitCategory
from cyelec.models.spatial import DeviceType, Phase2DesignResult, PlacedDevice

SvgDiscipline = Literal["combined", "lighting", "power", "elv"]

_LIGHTING_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.LUMINAIRE,
        DeviceType.PENDANT_LIGHT,
        DeviceType.WALL_SCONCE,
        DeviceType.LINEAR_COVE,
        DeviceType.OUTDOOR_PIR_LIGHT,
        DeviceType.GARDEN_BOLLARD,
        DeviceType.MIRROR_LIGHT,
        DeviceType.EXHAUST_FAN,
        DeviceType.SWITCH,
        DeviceType.DIMMER_SWITCH,
        DeviceType.FAN_ISOLATOR_3P,
    }
)
_POWER_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.SOCKET_TWIN,
        DeviceType.KITCHEN_FCU,
        DeviceType.SPLIT_AC,
        DeviceType.SHUTTER_SWITCH,
        DeviceType.HEAT_PUMP_OUTDOOR,
        DeviceType.HEAT_PUMP_HYDROBOX,
        DeviceType.UFH_MANIFOLD,
        DeviceType.ROOM_THERMOSTAT,
        DeviceType.VRV_OUTDOOR,
        DeviceType.VRV_INDOOR_ISOLATOR,
        DeviceType.WATER_BOOSTER_PUMP,
        DeviceType.GATE_MOTOR,
        DeviceType.EV_WALLBOX,
        DeviceType.POOL_SUB_DB,
        DeviceType.COOKER_CONTROL_UNIT,
        DeviceType.COOKER_OUTLET,
        DeviceType.OVEN_OUTLET,
        DeviceType.APPLIANCE_OUTLET,
        DeviceType.WATER_HEATER_DP_SWITCH,
        DeviceType.WATER_HEATER_ISOLATOR,
        DeviceType.EAC_METER_CABINET,
        DeviceType.EARTH_PIT,
    }
)
_ELV_TYPES: frozenset[DeviceType] = frozenset(
    {
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
        DeviceType.SMOKE_DETECTOR,
        DeviceType.HEAT_DETECTOR,
        DeviceType.KNX_KEYPAD,
    }
)


def _esc(val: object) -> str:
    return html.escape(str(val), quote=True)


def _svg_boxes_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float = 1.5,
) -> bool:
    return not (
        a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1]
    )


def _place_svg_tag(
    x: float,
    y: float,
    text: str,
    font_px: float,
    occ_boxes: list[tuple[float, float, float, float]],
    bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Find a collision-free SVG text placement around (x, y) and register its bounding box."""
    w = max(12.0, len(text) * font_px * 0.56)
    h = font_px * 1.25
    min_x, min_y, max_x, max_y = bounds
    cands: list[tuple[float, float]] = []
    for r in (9.0, 16.0, 25.0, 36.0):
        cands.extend(
            [
                (x + r, y - h * 0.5),  # E
                (x - r - w, y - h * 0.5),  # W
                (x - w * 0.5, y - r - h),  # N
                (x - w * 0.5, y + r),  # S
                (x + r * 0.75, y - r * 0.75 - h),  # NE
                (x + r * 0.75, y + r * 0.75),  # SE
                (x - r * 0.75 - w, y - r * 0.75 - h),  # NW
                (x - r * 0.75 - w, y + r * 0.75),  # SW
            ]
        )

    def _clamp(cx: float, cy: float) -> tuple[float, float]:
        return (
            max(min_x, min(cx, max_x - w)),
            max(min_y, min(cy, max_y - h)),
        )

    for raw_x, raw_y in cands:
        cx, cy = _clamp(raw_x, raw_y)
        box = (cx, cy, cx + w, cy + h)
        if not any(_svg_boxes_overlap(box, ob, pad=1.2) for ob in occ_boxes):
            occ_boxes.append(box)
            return cx, cy + h * 0.82

    # Fallback: pick candidate with minimum overlap count
    best_xy = _clamp(cands[0][0], cands[0][1])
    best_hits = 10**9
    for raw_x, raw_y in cands:
        cx, cy = _clamp(raw_x, raw_y)
        box = (cx, cy, cx + w, cy + h)
        hits = sum(1 for ob in occ_boxes if _svg_boxes_overlap(box, ob, pad=0.8))
        if hits < best_hits:
            best_hits = hits
            best_xy = (cx, cy)
            if hits == 0:
                break
    occ_boxes.append((best_xy[0], best_xy[1], best_xy[0] + w, best_xy[1] + h))
    return best_xy[0], best_xy[1] + h * 0.82


def _chain_nearest_neighbor(
    devs: list[PlacedDevice],
    start_xy_mm: tuple[float, float],
) -> list[PlacedDevice]:
    """Order devices by nearest-neighbor starting closest to start_xy_mm for tidy loop arcs."""
    if len(devs) <= 1:
        return list(devs)
    rem = list(devs)
    first_i = min(
        range(len(rem)),
        key=lambda i: math.hypot(
            rem[i].location_mm.x_mm - start_xy_mm[0],
            rem[i].location_mm.y_mm - start_xy_mm[1],
        ),
    )
    ordered = [rem.pop(first_i)]
    while rem:
        last = ordered[-1].location_mm
        nxt_i = min(
            range(len(rem)),
            key=lambda i: math.hypot(
                rem[i].location_mm.x_mm - last.x_mm,
                rem[i].location_mm.y_mm - last.y_mm,
            ),
        )
        ordered.append(rem.pop(nxt_i))
    return ordered


def render_floorplan_svg(
    result: Phase2DesignResult,
    *,
    discipline: SvgDiscipline = "combined",
) -> str:
    """Render architectural floorplan + discipline-filtered electrical overlay as vector SVG."""
    building = result.design.building
    spatial = result.spatial
    eac = result.design.eac_compliance

    all_pts = [p for r in building.rooms for p in r.polygon_mm]
    if not all_pts:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200"></svg>'

    minx = min(p.x_mm for p in all_pts)
    maxx = max(p.x_mm for p in all_pts)
    miny = min(p.y_mm for p in all_pts)
    maxy = max(p.y_mm for p in all_pts)

    if discipline == "lighting":
        active_devices = [d for d in spatial.devices if d.device_type in _LIGHTING_TYPES]
    elif discipline == "power":
        active_devices = [d for d in spatial.devices if d.device_type in _POWER_TYPES]
    elif discipline == "elv":
        active_devices = [d for d in spatial.devices if d.device_type in _ELV_TYPES]
    else:
        active_devices = [
            d for d in spatial.devices if d.device_type in (_LIGHTING_TYPES | _POWER_TYPES)
        ]

    # Expand bounds so exterior devices (Meter, Earth Pit, CCTV, Gate, EV, Pool) fit cleanly
    for dev in active_devices:
        minx = min(minx, dev.location_mm.x_mm - 300.0)
        maxx = max(maxx, dev.location_mm.x_mm + 300.0)
        miny = min(miny, dev.location_mm.y_mm - 300.0)
        maxy = max(maxy, dev.location_mm.y_mm + 300.0)

    span_x = max(1000.0, maxx - minx)
    span_y = max(1000.0, maxy - miny)

    # Map architectural mm -> SVG pixels (flipping Y axis so north is up)
    pad_left = 48.0
    pad_top = 56.0
    pad_right = 248.0  # Legend + Mounting Heights sidebar
    pad_bottom = 44.0
    draw_w = 880.0
    scale = draw_w / span_x
    draw_h = max(460.0, span_y * scale)
    svg_w = int(math.ceil(pad_left + draw_w + pad_right))
    svg_h = int(math.ceil(pad_top + draw_h + pad_bottom))
    tag_bounds = (pad_left - 26.0, pad_top - 6.0, pad_left + draw_w + 10.0, pad_top + draw_h + 26.0)

    def tx(x_mm: float) -> float:
        return pad_left + (x_mm - minx) * scale

    def ty(y_mm: float) -> float:
        return pad_top + (maxy - y_mm) * scale

    def poly_points(pts: list[Point2D]) -> str:
        return " ".join(f"{tx(p.x_mm):.1f},{ty(p.y_mm):.1f}" for p in pts)

    room_floor: dict[str, str] = {r.id: r.floor for r in building.rooms}
    board_mm_by_id: dict[str, tuple[float, float]] = {
        building.db_anchor.id: (
            building.db_anchor.location_mm.x_mm,
            building.db_anchor.location_mm.y_mm,
        )
    }
    board_id_by_floor: dict[str, str] = {}
    for sb in building.sub_boards:
        board_mm_by_id[sb.board_id] = (sb.location_mm.x_mm, sb.location_mm.y_mm)
        board_id_by_floor[sb.floor] = sb.board_id

    sheet_title = (
        "E-101 LIGHTING, PHOTOMETRICS &amp; SWITCHING LAYOUT"
        if discipline == "lighting"
        else (
            "E-102 POWER, SOCKETS &amp; MECHANICAL PLANT LAYOUT"
            if discipline == "power"
            else (
                "E-103 ELV, SECURITY, INTERCOM &amp; LIFE SAFETY LAYOUT"
                if discipline == "elv"
                else "E-100 COMBINED LV ELECTRICAL LAYOUT (COORDINATION)"
            )
        )
    )

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" class="cy-plan" '
            f'viewBox="0 0 {svg_w} {svg_h}" width="{svg_w}" height="{svg_h}" '
            f'data-minx="{minx:.2f}" data-maxy="{maxy:.2f}" '
            f'data-scale="{scale:.6f}" data-pad-left="{pad_left:.2f}" '
            f'data-pad-top="{pad_top:.2f}">'
        ),
        "  <style>",
        (
            '    .cy-plan text { font-family: "DejaVu Sans", "Noto Sans", Arial, sans-serif; '
            "fill: #1a202c; }"
        ),
        "    .cy-plan .fp-title { font-size: 13.5px; font-weight: 700; fill: #1b365d; }",
        "    .cy-plan .fp-sub { font-size: 9.8px; fill: #4a5568; }",
        "    .cy-plan .room-poly { cursor: pointer; transition: fill-opacity 0.12s; }",
        "    .cy-plan .room-poly:hover { stroke: #1b365d; stroke-width: 2; }",
        "    .cy-plan .room-lbl { font-weight: 600; fill: #2d3748; pointer-events: none; }",
        "    .cy-plan .room-area { font-size: 7.4px; fill: #718096; pointer-events: none; }",
        "    .cy-plan .dev-tag { font-size: 6.8px; font-weight: 700; fill: #1a202c; }",
        "    .cy-plan .leg-hdr { font-size: 9.8px; font-weight: 700; fill: #1b365d; }",
        "    .cy-plan .leg-txt { font-size: 8.4px; fill: #2d3748; }",
        "  </style>",
        f'  <rect x="0" y="0" width="{svg_w}" height="{svg_h}" fill="#ffffff" />',
        (
            f'  <text x="{pad_left:.0f}" y="24" class="fp-title">'
            f"{sheet_title} — {_esc(building.project_name)}</text>"
        ),
        (
            f'  <text x="{pad_left:.0f}" y="40" class="fp-sub">'
            f"Area: {building.total_floor_area_m2:.1f} m² | "
            f"Profile: {_esc(eac.eac_profile.value)} "
            f"(Bath excl: {eac.bathroom_socket_exclusion_m:.1f} m) | "
            f"Points: {len(active_devices)} | "
            f"Total Conduit: {spatial.total_conduit_m:.1f} m</text>"
        ),
    ]

    # 1. Room fills (interactive polygons for Pass-2 visual room override)
    for room in building.rooms:
        fill = "#edf2f7" if room.is_wet_room else ("#f0fff4" if room.is_outdoor else "#f8fafc")
        parts.append(
            f'  <polygon class="room-poly" data-room-id="{_esc(room.id)}" '
            f'data-room-type="{_esc(room.normalized_type.value)}" '
            f'data-room-label="{_esc(room.raw_label)}" '
            f'points="{poly_points(room.polygon_mm)}" '
            f'fill="{fill}" stroke="#cbd5e0" stroke-width="1" />'
        )

    # 2. BS 7671 Section 701 Wet Zones (Power & Combined sheets)
    if discipline in ("combined", "power"):
        for wz in spatial.wet_zones:
            for ring in wz.socket_exclusion_polygons_mm:
                parts.append(
                    f'  <polygon points="{poly_points(ring)}" '
                    'fill="#fbb6ce" fill-opacity="0.28" stroke="#b83280" '
                    'stroke-width="1.2" stroke-dasharray="5,3" style="pointer-events:none;" />'
                )
            for ring in wz.zone_2_polygons_mm:
                parts.append(
                    f'  <polygon points="{poly_points(ring)}" '
                    'fill="#fbd38d" fill-opacity="0.45" stroke="#c05621" '
                    'stroke-width="1" style="pointer-events:none;" />'
                )
            for ring in wz.zone_0_polygons_mm:
                parts.append(
                    f'  <polygon points="{poly_points(ring)}" '
                    'fill="#feb2b2" fill-opacity="0.72" stroke="#c53030" '
                    'stroke-width="1.4" style="pointer-events:none;" />'
                )

    # 3. Plumbing fixtures & auto-detected architectural furniture outlines
    for room in building.rooms:
        for fixt in room.fixtures:
            parts.append(
                f'  <polygon points="{poly_points(fixt.polygon_mm)}" '
                'fill="#bee3f8" fill-opacity="0.6" stroke="#2b6cb0" '
                'stroke-width="1" style="pointer-events:none;" />'
            )
        for furn in room.furniture:
            if len(furn.polygon_mm) >= 3:
                parts.append(
                    f'  <polygon points="{poly_points(furn.polygon_mm)}" '
                    'fill="#e2e8f0" fill-opacity="0.45" stroke="#94a3b8" '
                    'stroke-width="0.9" stroke-dasharray="3,2" style="pointer-events:none;" />'
                )

    # 4. Architectural walls, windows, and doors
    for wall in building.walls:
        sw = 3.2 if wall.is_exterior else 1.8
        parts.append(
            f'  <line x1="{tx(wall.start.x_mm):.1f}" y1="{ty(wall.start.y_mm):.1f}" '
            f'x2="{tx(wall.end.x_mm):.1f}" y2="{ty(wall.end.y_mm):.1f}" '
            f'stroke="#2d3748" stroke-width="{sw}" stroke-linecap="square" '
            'style="pointer-events:none;" />'
        )
    for win in building.windows:
        parts.append(
            f'  <line x1="{tx(win.start.x_mm):.1f}" y1="{ty(win.start.y_mm):.1f}" '
            f'x2="{tx(win.end.x_mm):.1f}" y2="{ty(win.end.y_mm):.1f}" '
            f'stroke="#00b5d8" stroke-width="4.2" style="pointer-events:none;" />'
        )
    for door in building.doors:
        hx, hy = door.hinge.x_mm, door.hinge.y_mm
        open_ang = door.angle_deg + (90.0 if door.swing_dir == "ccw" else -90.0)
        rad = math.radians(open_ang)
        lx = hx + door.width_mm * math.cos(rad)
        ly = hy + door.width_mm * math.sin(rad)
        parts.append(
            f'  <line x1="{tx(hx):.1f}" y1="{ty(hy):.1f}" '
            f'x2="{tx(lx):.1f}" y2="{ty(ly):.1f}" '
            f'stroke="#38a169" stroke-width="1.6" style="pointer-events:none;" />'
        )

    # 5. Conduit / Circuit Representation (strictly grouped PER FLOOR so zero arcs jump floor gaps)
    cat_map = {c.spec.circuit_id: c.spec.category for c in result.design.circuits}
    board_map = {c.spec.circuit_id: c.spec.board_id for c in result.design.circuits}
    board_pts: dict[str, tuple[float, float]] = {
        bid: (tx(bx_mm), ty(by_mm)) for bid, (bx_mm, by_mm) in board_mm_by_id.items()
    }
    use_homerun = result.design.residential_brief.conduit_drafting_style == "homerun_arcs"
    deferred_tags: list[tuple[float, float, str, str]] = []  # (x, y, text, color_or_empty)

    if discipline == "elv":
        # E-103: Per-floor interlinked BS 5839 Smoke/Heat loop arcs + KNX bus daisy-chain arcs
        smoke_by_flr: dict[str, list[PlacedDevice]] = {}
        for d in active_devices:
            if d.device_type in (DeviceType.SMOKE_DETECTOR, DeviceType.HEAT_DETECTOR):
                smoke_by_flr.setdefault(room_floor.get(d.room_id, "GF"), []).append(d)
        for flr, flr_smokes in smoke_by_flr.items():
            if len(flr_smokes) < 2:
                continue
            start_mm = board_mm_by_id.get(
                board_id_by_floor.get(flr, building.db_anchor.id),
                board_mm_by_id[building.db_anchor.id],
            )
            chain = _chain_nearest_neighbor(flr_smokes, start_mm)
            for a_d, b_d in zip(chain, chain[1:], strict=False):
                if (
                    math.hypot(
                        b_d.location_mm.x_mm - a_d.location_mm.x_mm,
                        b_d.location_mm.y_mm - a_d.location_mm.y_mm,
                    )
                    > 8500.0
                ):
                    continue
                ax, ay = tx(a_d.location_mm.x_mm), ty(a_d.location_mm.y_mm)
                bx, by = tx(b_d.location_mm.x_mm), ty(b_d.location_mm.y_mm)
                ddx, ddy = bx - ax, by - ay
                dist = math.hypot(ddx, ddy)
                if dist >= 3.0:
                    cx = (ax + bx) * 0.5 - (ddy / dist) * (0.14 * dist)
                    cy = (ay + by) * 0.5 + (ddx / dist) * (0.14 * dist)
                    parts.append(
                        f'  <path d="M {ax:.1f} {ay:.1f} Q {cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}" '
                        'fill="none" stroke="#dc2626" stroke-width="1.2" stroke-dasharray="5,3" '
                        'stroke-opacity="0.8" style="pointer-events:none;" />'
                    )
        knx_by_flr: dict[str, list[PlacedDevice]] = {}
        for d in active_devices:
            if d.device_type == DeviceType.KNX_KEYPAD:
                knx_by_flr.setdefault(room_floor.get(d.room_id, "GF"), []).append(d)
        for flr, flr_knx in knx_by_flr.items():
            if len(flr_knx) < 2:
                continue
            start_mm = board_mm_by_id.get(
                board_id_by_floor.get(flr, building.db_anchor.id),
                board_mm_by_id[building.db_anchor.id],
            )
            chain = _chain_nearest_neighbor(flr_knx, start_mm)
            for a_d, b_d in zip(chain, chain[1:], strict=False):
                if (
                    math.hypot(
                        b_d.location_mm.x_mm - a_d.location_mm.x_mm,
                        b_d.location_mm.y_mm - a_d.location_mm.y_mm,
                    )
                    > 8500.0
                ):
                    continue
                ax, ay = tx(a_d.location_mm.x_mm), ty(a_d.location_mm.y_mm)
                bx, by = tx(b_d.location_mm.x_mm), ty(b_d.location_mm.y_mm)
                ddx, ddy = bx - ax, by - ay
                dist = math.hypot(ddx, ddy)
                if dist >= 3.0:
                    cx = (ax + bx) * 0.5 + (ddy / dist) * (0.14 * dist)
                    cy = (ay + by) * 0.5 - (ddx / dist) * (0.14 * dist)
                    parts.append(
                        f'  <path d="M {ax:.1f} {ay:.1f} Q {cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}" '
                        'fill="none" stroke="#16a34a" stroke-width="1.25" stroke-dasharray="3,2" '
                        'stroke-opacity="0.85" style="pointer-events:none;" />'
                    )
        # Clean star home-run ticks from Data/WiFi endpoints toward same-floor Rack or floor riser
        rack = next(
            (d for d in active_devices if d.device_type == DeviceType.TELECOM_CABINET), None
        )
        if rack is not None:
            rack_flr = room_floor.get(rack.room_id, "GF")
            rx, ry = tx(rack.location_mm.x_mm), ty(rack.location_mm.y_mm)
            for ep in active_devices:
                if ep.device_type in (DeviceType.DATA_RJ45, DeviceType.WIFI_AP):
                    ep_flr = room_floor.get(ep.room_id, "GF")
                    if ep_flr == rack_flr:
                        tgt_x, tgt_y = rx, ry
                    else:
                        tgt_x, tgt_y = board_pts.get(
                            board_id_by_floor.get(ep_flr, building.db_anchor.id), (rx, ry)
                        )
                    ex, ey = tx(ep.location_mm.x_mm), ty(ep.location_mm.y_mm)
                    ddx, ddy = tgt_x - ex, tgt_y - ey
                    dist = math.hypot(ddx, ddy)
                    if dist > 20.0:
                        ux, uy = ddx / dist, ddy / dist
                        ax, ay = ex + ux * 20.0, ey + uy * 20.0
                        parts.append(
                            f'  <line x1="{ex:.1f}" y1="{ey:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" '
                            'stroke="#2563eb" stroke-width="1.0" stroke-opacity="0.65" '
                            'style="pointer-events:none;" />'
                        )
    elif use_homerun:
        # Cyprus Standard: per-floor nearest-neighbor loop arcs + DB home-run arrow
        by_cid_flr: dict[tuple[str, str, bool], list[PlacedDevice]] = {}
        for dev in active_devices:
            if not dev.circuit_id or dev.circuit_id not in cat_map:
                continue
            if dev.device_type in (
                DeviceType.SWITCH,
                DeviceType.DIMMER_SWITCH,
                DeviceType.FAN_ISOLATOR_3P,
                DeviceType.EAC_METER_CABINET,
                DeviceType.EARTH_PIT,
            ):
                continue
            flr = room_floor.get(dev.room_id, "GF")
            is_ext = dev.device_type in (
                DeviceType.GARDEN_BOLLARD,
                DeviceType.OUTDOOR_PIR_LIGHT,
                DeviceType.GATE_MOTOR,
                DeviceType.POOL_SUB_DB,
                DeviceType.HEAT_PUMP_OUTDOOR,
                DeviceType.VRV_OUTDOOR,
                DeviceType.EV_WALLBOX,
            )
            by_cid_flr.setdefault((dev.circuit_id, flr, is_ext), []).append(dev)

        for (cid, flr, is_ext), c_devs in by_cid_flr.items():
            cat = cat_map.get(cid)
            is_lite = cat in (CircuitCategory.LIGHTING, CircuitCategory.EMERGENCY_LIGHTING)
            if discipline == "lighting" and not is_lite:
                continue
            if discipline == "power" and is_lite:
                continue
            c_stroke = (
                "#b7791f"
                if is_lite
                else (
                    "#0284c7"
                    if cat in (CircuitCategory.SOCKET_RING, CircuitCategory.SOCKET_RADIAL)
                    else "#7c3aed"
                )
            )
            target_bid = board_id_by_floor.get(flr) or board_map.get(cid, building.db_anchor.id)
            db_mm = board_mm_by_id.get(target_bid, board_mm_by_id[building.db_anchor.id])
            db_x, db_y = board_pts.get(target_bid, board_pts[building.db_anchor.id])

            ordered = _chain_nearest_neighbor(c_devs, db_mm)
            # Home-run arrow from closest device pointing toward its floor DB
            fx, fy = tx(ordered[0].location_mm.x_mm), ty(ordered[0].location_mm.y_mm)
            ddx, ddy = db_x - fx, db_y - fy
            dist = math.hypot(ddx, ddy)
            if dist > 8.0:
                ux, uy = ddx / dist, ddy / dist
                hr_len = min(30.0, dist * 0.50)
                hx, hy = fx + ux * hr_len, fy + uy * hr_len
                px, py = -uy, ux
                b1x, b1y = hx - ux * 5.5 + px * 2.8, hy - uy * 5.5 + py * 2.8
                b2x, b2y = hx - ux * 5.5 - px * 2.8, hy - uy * 5.5 - py * 2.8
                parts.append(
                    f'  <line x1="{fx:.1f}" y1="{fy:.1f}" x2="{hx:.1f}" y2="{hy:.1f}" '
                    f'stroke="{c_stroke}" stroke-width="1.3" style="pointer-events:none;" />'
                )
                pts_a = f"{hx:.1f},{hy:.1f} {b1x:.1f},{b1y:.1f} {b2x:.1f},{b2y:.1f}"
                parts.append(
                    f'  <polygon points="{pts_a}" '
                    f'fill="{c_stroke}" style="pointer-events:none;" />'
                )
                deferred_tags.append((hx, hy, f"{cid}→DB", c_stroke))

            # Clean curved loop arcs between adjacent indoor devices (never jumping >6.5 m)
            if not is_ext:
                for idx, (a_d, b_d) in enumerate(zip(ordered, ordered[1:], strict=False)):
                    d_mm = math.hypot(
                        b_d.location_mm.x_mm - a_d.location_mm.x_mm,
                        b_d.location_mm.y_mm - a_d.location_mm.y_mm,
                    )
                    if d_mm < 100.0 or d_mm > 6500.0:
                        continue
                    ax, ay = tx(a_d.location_mm.x_mm), ty(a_d.location_mm.y_mm)
                    bx, by = tx(b_d.location_mm.x_mm), ty(b_d.location_mm.y_mm)
                    ldx, ldy = bx - ax, by - ay
                    ldist = math.hypot(ldx, ldy)
                    if ldist < 4.0:
                        continue
                    bulge = (0.14 if idx % 2 == 0 else -0.14) * ldist
                    cx = (ax + bx) * 0.5 - (ldy / ldist) * bulge
                    cy = (ay + by) * 0.5 + (ldx / ldist) * bulge
                    parts.append(
                        f'  <path d="M {ax:.1f} {ay:.1f} Q {cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}" '
                        f'fill="none" stroke="{c_stroke}" stroke-width="1.1" stroke-opacity="0.72" '
                        'style="pointer-events:none;" />'
                    )
    else:
        for rc in spatial.routed_circuits:
            cat = cat_map.get(rc.circuit_id)
            is_lite = cat in (CircuitCategory.LIGHTING, CircuitCategory.EMERGENCY_LIGHTING)
            if discipline == "lighting" and not is_lite:
                continue
            if discipline == "power" and is_lite:
                continue
            c_stroke = (
                "#b7791f"
                if is_lite
                else (
                    "#0284c7"
                    if cat in (CircuitCategory.SOCKET_RING, CircuitCategory.SOCKET_RADIAL)
                    else "#7c3aed"
                )
            )
            for seg in rc.segments:
                parts.append(
                    f'  <line x1="{tx(seg.start_mm.x_mm):.1f}" y1="{ty(seg.start_mm.y_mm):.1f}" '
                    f'x2="{tx(seg.end_mm.x_mm):.1f}" y2="{ty(seg.end_mm.y_mm):.1f}" '
                    f'stroke="{c_stroke}" stroke-width="1.25" stroke-opacity="0.6" '
                    'style="pointer-events:none;" />'
                )

    # 5b. Curved Dashed Switch-to-Luminaire/Fan Control Arcs (Lighting & Combined)
    if discipline in ("combined", "lighting") and spatial.switch_legs:
        dev_by_id = {d.device_id: d for d in active_devices}
        for leg in spatial.switch_legs:
            sw_dev = dev_by_id.get(leg.switch_id)
            if sw_dev is None:
                continue
            sx, sy = tx(sw_dev.location_mm.x_mm), ty(sw_dev.location_mm.y_mm)
            for idx, tid in enumerate(leg.target_device_ids):
                t_dev = dev_by_id.get(tid)
                if t_dev is None:
                    continue
                if room_floor.get(t_dev.room_id, "GF") != room_floor.get(sw_dev.room_id, "GF"):
                    continue
                ex, ey = tx(t_dev.location_mm.x_mm), ty(t_dev.location_mm.y_mm)
                ddx, ddy = ex - sx, ey - sy
                dist = math.hypot(ddx, ddy)
                if dist < 2.0:
                    continue
                bulge = (0.18 if idx % 2 == 0 else -0.18) * dist
                cx = (sx + ex) * 0.5 - (ddy / dist) * bulge
                cy = (sy + ey) * 0.5 + (ddx / dist) * bulge
                parts.append(
                    f'  <path d="M {sx:.1f} {sy:.1f} Q {cx:.1f} {cy:.1f} {ex:.1f} {ey:.1f}" '
                    'fill="none" stroke="#16a34a" stroke-width="1.15" '
                    'stroke-dasharray="4,3" stroke-opacity="0.85" style="pointer-events:none;" />'
                )

    # 6. Wrapped & Auto-Fitted Room Bilingual Labels + 2D De-Collision Occupancy Table
    occ_boxes: list[tuple[float, float, float, float]] = []
    for room in building.rooms:
        c = room.centroid_mm()
        cx, cy = tx(c.x_mm), ty(c.y_mm)
        r_xs = [tx(p.x_mm) for p in room.polygon_mm]
        room_px_w = max(34.0, (max(r_xs) - min(r_xs)) - 8.0)
        raw_lbl = room.raw_label.strip()
        if " / " in raw_lbl:
            p1, p2 = raw_lbl.split(" / ", 1)
            lbl_lines = [p1.strip()[:24], p2.strip()[:24]]
        elif len(raw_lbl) > 20 and " " in raw_lbl:
            mid = len(raw_lbl) // 2
            sp_i = raw_lbl.find(" ", mid)
            if sp_i == -1:
                sp_i = raw_lbl.rfind(" ", 0, mid)
            lbl_lines = (
                [raw_lbl[:sp_i].strip(), raw_lbl[sp_i + 1 :].strip()]
                if sp_i > 0
                else [raw_lbl[:24]]
            )
        else:
            lbl_lines = [raw_lbl[:24]]

        max_ch = max(len(ln) for ln in lbl_lines)
        lbl_fs = min(8.4, max(5.6, room_px_w / max(max_ch * 0.58, 1.0)))
        line_h = lbl_fs * 1.18
        total_h = len(lbl_lines) * line_h + 9.0
        top_y = cy - total_h * 0.5 + lbl_fs * 0.85
        for li, ln in enumerate(lbl_lines):
            parts.append(
                f'  <text x="{cx:.1f}" y="{top_y + li * line_h:.1f}" text-anchor="middle" '
                f'class="room-lbl" style="font-size:{lbl_fs:.1f}px;">{_esc(ln)}</text>'
            )
        area_y = top_y + len(lbl_lines) * line_h + 1.0
        parts.append(
            f'  <text x="{cx:.1f}" y="{area_y:.1f}" text-anchor="middle" '
            f'class="room-area">{room.area_m2:.1f} m²</text>'
        )
        est_w = max(max_ch * lbl_fs * 0.56, 32.0)
        occ_boxes.append(
            (cx - est_w * 0.5, cy - total_h * 0.5 - 1.0, cx + est_w * 0.5, area_y + 2.0)
        )

    # 7. Distribution Board Anchor (DB1 / MDB) & Sub-Boards
    dbx = tx(building.db_anchor.location_mm.x_mm)
    dby = ty(building.db_anchor.location_mm.y_mm)
    occ_boxes.append((dbx - 16.0, dby - 10.0, dbx + 16.0, dby + 10.0))
    parts.extend(
        [
            (
                f'  <rect data-db="true" data-anchor="db" x="{dbx - 14:.1f}" y="{dby - 8:.1f}" '
                'width="28" height="16" fill="#c53030" stroke="#742a2a" '
                'stroke-width="1.5" rx="2" />'
            ),
            (
                f'  <text x="{dbx:.1f}" y="{dby + 3:.1f}" text-anchor="middle" '
                'style="fill:#ffffff;font-size:8px;font-weight:700;pointer-events:none;">'
                f"{_esc(building.db_anchor.id)}</text>"
            ),
        ]
    )
    for sb in building.sub_boards:
        sbx = tx(sb.location_mm.x_mm)
        sby = ty(sb.location_mm.y_mm)
        if abs(sbx - dbx) < 35.0 and abs(sby - dby) < 17.0:
            sbx = dbx + 35.0
        occ_boxes.append((sbx - 20.0, sby - 10.0, sbx + 20.0, sby + 10.0))
        parts.extend(
            [
                (
                    f'  <rect data-sub-board="{_esc(sb.board_id)}" '
                    f'x="{sbx - 18:.1f}" y="{sby - 8:.1f}" width="36" height="16" '
                    'fill="#dd6b20" stroke="#7b341e" stroke-width="1.5" rx="2" />'
                ),
                (
                    f'  <text x="{sbx:.1f}" y="{sby + 3:.1f}" text-anchor="middle" '
                    'style="fill:#ffffff;font-size:7.2px;font-weight:700;pointer-events:none;">'
                    f"{_esc(sb.board_id)}</text>"
                ),
            ]
        )

    # 8. Placed Electrical Devices (Lighting, Power, Mechanical Plant, ELV, Security, Smoke, KNX)
    def _badge(
        bx: float,
        by: float,
        text: str,
        *,
        fill: str,
        stroke: str,
        fg: str,
        w: float = 18.0,
        h: float = 10.0,
        anchor: str | None = None,
    ) -> None:
        a_attr = f' data-anchor="{anchor}"' if anchor else ""
        occ_boxes.append((bx - w / 2 - 1.0, by - h / 2 - 1.0, bx + w / 2 + 1.0, by + h / 2 + 1.0))
        parts.append(
            f'  <rect{a_attr} x="{bx - w / 2:.1f}" y="{by - h / 2:.1f}" '
            f'width="{w:.1f}" height="{h:.1f}" fill="{fill}" stroke="{stroke}" '
            'stroke-width="1.4" rx="2" />'
        )
        parts.append(
            f'  <text x="{bx:.1f}" y="{by + 2.5:.1f}" text-anchor="middle" '
            f'style="fill:{fg};font-size:5.8px;font-weight:700;pointer-events:none;">'
            f"{_esc(text)}</text>"
        )

    for dev in active_devices:
        dx = tx(dev.location_mm.x_mm)
        dy = ty(dev.location_mm.y_mm)
        cid = dev.circuit_id or "?"
        dt = dev.device_type
        if dt == DeviceType.LUMINAIRE:
            occ_boxes.append((dx - 7.0, dy - 7.0, dx + 7.0, dy + 7.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="6" '
                'fill="#fefcbf" stroke="#b7791f" stroke-width="1.5" />'
            )
            parts.append(
                f'  <line x1="{dx - 4:.1f}" y1="{dy - 4:.1f}" '
                f'x2="{dx + 4:.1f}" y2="{dy + 4:.1f}" stroke="#b7791f" stroke-width="1.2" />'
            )
            parts.append(
                f'  <line x1="{dx - 4:.1f}" y1="{dy + 4:.1f}" '
                f'x2="{dx + 4:.1f}" y2="{dy - 4:.1f}" stroke="#b7791f" stroke-width="1.2" />'
            )
            if discipline == "lighting" and dev.switch_group:
                deferred_tags.append((dx, dy, f"{cid}({dev.switch_group})", ""))
        elif dt == DeviceType.PENDANT_LIGHT:
            occ_boxes.append((dx - 8.0, dy - 8.0, dx + 8.0, dy + 8.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="7.2" '
                'fill="#fef08a" stroke="#a16207" stroke-width="1.6" />'
            )
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="3.2" '
                'fill="#f59e0b" stroke="#78350f" stroke-width="1.0" />'
            )
            if discipline == "lighting":
                deferred_tags.append((dx, dy, "PD", ""))
        elif dt == DeviceType.OUTDOOR_PIR_LIGHT:
            _badge(dx, dy, "PIR-L", fill="#fef3c7", stroke="#d97706", fg="#92400e", w=22.0)
        elif dt == DeviceType.GARDEN_BOLLARD:
            occ_boxes.append((dx - 6.0, dy - 6.0, dx + 6.0, dy + 6.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.8" '
                'fill="#fde68a" stroke="#b45309" stroke-width="1.4" />'
            )
            deferred_tags.append((dx, dy, "BOL", ""))
        elif dt == DeviceType.MIRROR_LIGHT:
            occ_boxes.append((dx - 7.0, dy - 5.0, dx + 7.0, dy + 5.0))
            parts.append(
                f'  <rect x="{dx - 6:.1f}" y="{dy - 3.5:.1f}" width="12" height="7" '
                'fill="#fef08a" stroke="#a16207" stroke-width="1.4" rx="2" />'
            )
            deferred_tags.append((dx, dy, "ML(b)", ""))
        elif dt == DeviceType.EXHAUST_FAN:
            occ_boxes.append((dx - 7.0, dy - 7.0, dx + 7.0, dy + 7.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="6" '
                'fill="#e0f2fe" stroke="#0369a1" stroke-width="1.5" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#0369a1;font-size:6.2px;font-weight:700;">EF</text>'
            )
        elif dt in (DeviceType.SWITCH, DeviceType.DIMMER_SWITCH):
            occ_boxes.append((dx - 6.0, dy - 6.0, dx + 6.0, dy + 6.0))
            fill_c = "#bbf7d0" if dt == DeviceType.SWITCH else "#d9f99d"
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.4" '
                f'fill="{fill_c}" stroke="#15803d" stroke-width="1.4" />'
            )
            if dt == DeviceType.DIMMER_SWITCH:
                parts.append(
                    f'  <line x1="{dx - 4:.1f}" y1="{dy + 4:.1f}" '
                    f'x2="{dx + 4:.1f}" y2="{dy - 4:.1f}" stroke="#15803d" stroke-width="1.3" />'
                )
            if discipline == "lighting":
                st = dev.switch_type or ("DIM" if dt == DeviceType.DIMMER_SWITCH else "1G1W")
                grp = dev.switch_group or "a"
                deferred_tags.append((dx, dy, f"{st}({grp})", ""))
        elif dt == DeviceType.FAN_ISOLATOR_3P:
            occ_boxes.append((dx - 6.0, dy - 6.0, dx + 6.0, dy + 6.0))
            parts.append(
                f'  <rect x="{dx - 5:.1f}" y="{dy - 5:.1f}" width="10" height="10" '
                'fill="#dcfce7" stroke="#166534" stroke-width="1.3" />'
            )
            deferred_tags.append((dx, dy, "3P-FAN", ""))
        elif dt == DeviceType.SOCKET_TWIN:
            occ_boxes.append((dx - 5.5, dy - 5.5, dx + 5.5, dy + 5.5))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.5" '
                'fill="#e6fffa" stroke="#008080" stroke-width="1.5" />'
            )
        elif dt == DeviceType.SPLIT_AC:
            _badge(dx, dy, "AC", fill="#ebf8ff", stroke="#2b6cb0", fg="#2b6cb0", w=18.0)
            deferred_tags.append((dx, dy, cid, ""))
        elif dt == DeviceType.COOKER_CONTROL_UNIT:
            _badge(dx, dy, "CCU", fill="#fee2e2", stroke="#b91c1c", fg="#991b1b", w=18.0)
        elif dt == DeviceType.WATER_HEATER_DP_SWITCH:
            _badge(dx, dy, "WH", fill="#ffedd5", stroke="#c2410c", fg="#9a3412", w=16.0)
        elif dt == DeviceType.SHUTTER_SWITCH:
            _badge(dx, dy, "SH", fill="#e0e7ff", stroke="#4338ca", fg="#312e81", w=15.0)
        elif dt == DeviceType.KITCHEN_FCU:
            _badge(dx, dy, "FCU", fill="#fce7f3", stroke="#be185d", fg="#831843", w=18.0)
        elif dt == DeviceType.ROOM_THERMOSTAT:
            occ_boxes.append((dx - 6.0, dy - 6.0, dx + 6.0, dy + 6.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="5.2" '
                'fill="#fff7ed" stroke="#ea580c" stroke-width="1.4" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.2:.1f}" text-anchor="middle" '
                'style="fill:#9a3412;font-size:5.8px;font-weight:700;">T</text>'
            )
        elif dt in (DeviceType.HEAT_PUMP_OUTDOOR, DeviceType.VRV_OUTDOOR):
            lbl = "HP-3P" if dt == DeviceType.HEAT_PUMP_OUTDOOR else "VRV"
            _badge(dx, dy, lbl, fill="#dbeafe", stroke="#1d4ed8", fg="#1e3a8a", w=26.0, h=13.0)
        elif dt in (
            DeviceType.HEAT_PUMP_HYDROBOX,
            DeviceType.UFH_MANIFOLD,
            DeviceType.VRV_INDOOR_ISOLATOR,
        ):
            lbl = (
                "HYDRO"
                if dt == DeviceType.HEAT_PUMP_HYDROBOX
                else ("UFH" if dt == DeviceType.UFH_MANIFOLD else "FCU-AC")
            )
            _badge(dx, dy, lbl, fill="#e0f2fe", stroke="#0284c7", fg="#075985", w=24.0, h=11.0)
        elif dt == DeviceType.WATER_BOOSTER_PUMP:
            _badge(dx, dy, "WP", fill="#cffafe", stroke="#0891b2", fg="#155e75", w=18.0)
        elif dt == DeviceType.GATE_MOTOR:
            _badge(dx, dy, "GATE", fill="#f3e8ff", stroke="#7e22ce", fg="#581c87", w=22.0)
        elif dt == DeviceType.EV_WALLBOX:
            _badge(dx, dy, "EV", fill="#dcfce7", stroke="#15803d", fg="#14532d", w=22.0, h=12.0)
        elif dt == DeviceType.POOL_SUB_DB:
            _badge(dx, dy, "POOL", fill="#bae6fd", stroke="#0369a1", fg="#0c4a6e", w=24.0, h=12.0)
        elif dt == DeviceType.EAC_METER_CABINET:
            _badge(
                dx,
                dy,
                "EAC-M",
                fill="#dbeafe",
                stroke="#1e40af",
                fg="#1e3a8a",
                w=26.0,
                h=14.0,
                anchor="meter",
            )
        elif dt == DeviceType.EARTH_PIT:
            occ_boxes.append((dx - 8.0, dy - 8.0, dx + 8.0, dy + 8.0))
            parts.append(
                f'  <circle data-anchor="earth_pit" cx="{dx:.1f}" cy="{dy:.1f}" r="6.5" '
                'fill="#dcfce7" stroke="#15803d" stroke-width="1.6" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#14532d;font-size:6.2px;font-weight:700;pointer-events:none;">TT</text>'
            )
        elif dt == DeviceType.TELECOM_CABINET:
            _badge(
                dx,
                dy,
                "9U-IT",
                fill="#dbeafe",
                stroke="#1d4ed8",
                fg="#1e3a8a",
                w=26.0,
                h=14.0,
                anchor="telecom",
            )
        elif dt == DeviceType.DATA_RJ45:
            _badge(dx, dy, "RJ45", fill="#eff6ff", stroke="#2563eb", fg="#1d4ed8", w=20.0)
        elif dt == DeviceType.TV_OUTLET:
            _badge(dx, dy, "TV", fill="#f0fdf4", stroke="#16a34a", fg="#166534", w=16.0)
        elif dt == DeviceType.WIFI_AP:
            occ_boxes.append((dx - 7.0, dy - 7.0, dx + 7.0, dy + 7.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="6.0" '
                'fill="#e0f2fe" stroke="#0284c7" stroke-width="1.5" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.2:.1f}" text-anchor="middle" '
                'style="fill:#0369a1;font-size:5.6px;font-weight:700;">AP</text>'
            )
        elif dt in (DeviceType.INTERCOM_OUTDOOR, DeviceType.INTERCOM_INDOOR):
            lbl = "V-OUT" if dt == DeviceType.INTERCOM_OUTDOOR else "V-MON"
            _badge(dx, dy, lbl, fill="#eef2ff", stroke="#4f46e5", fg="#3730a3", w=24.0)
        elif dt in (
            DeviceType.ALARM_PANEL,
            DeviceType.ALARM_KEYPAD,
            DeviceType.ALARM_PIR,
            DeviceType.ALARM_SIREN,
        ):
            lbl_map = {
                DeviceType.ALARM_PANEL: "ALM",
                DeviceType.ALARM_KEYPAD: "KP",
                DeviceType.ALARM_PIR: "PIR",
                DeviceType.ALARM_SIREN: "SIR",
            }
            _badge(dx, dy, lbl_map[dt], fill="#fef2f2", stroke="#dc2626", fg="#991b1b", w=18.0)
        elif dt == DeviceType.CCTV_CAMERA:
            _badge(dx, dy, "CAM", fill="#f1f5f9", stroke="#334155", fg="#0f172a", w=20.0)
        elif dt in (DeviceType.SMOKE_DETECTOR, DeviceType.HEAT_DETECTOR):
            lbl = "SD" if dt == DeviceType.SMOKE_DETECTOR else "HD"
            occ_boxes.append((dx - 7.0, dy - 7.0, dx + 7.0, dy + 7.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="5.6" '
                'fill="#fee2e2" stroke="#dc2626" stroke-width="1.5" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.2:.1f}" text-anchor="middle" '
                f'style="fill:#991b1b;font-size:5.8px;font-weight:700;">{lbl}</text>'
            )
        elif dt == DeviceType.KNX_KEYPAD:
            _badge(dx, dy, "KNX", fill="#dcfce7", stroke="#15803d", fg="#14532d", w=19.0)
        else:
            occ_boxes.append((dx - 6.5, dy - 6.5, dx + 6.5, dy + 6.5))
            parts.append(
                f'  <rect x="{dx - 5.5:.1f}" y="{dy - 5.5:.1f}" width="11" height="11" '
                'fill="#faf5ff" stroke="#6b46c1" stroke-width="1.5" />'
            )
            deferred_tags.append((dx, dy, cid, ""))

    # 8b. Emit all deferred floating tags through deterministic 2D bounding-box de-collision
    for tx0, ty0, t_str, t_col in deferred_tags:
        px_t, py_t = _place_svg_tag(tx0, ty0, t_str, 6.8, occ_boxes, tag_bounds)
        style_attr = f' style="fill:{t_col};"' if t_col else ""
        parts.append(
            f'  <text x="{px_t:.1f}" y="{py_t:.1f}" class="dev-tag"{style_attr}>'
            f"{_esc(t_str)}</text>"
        )

    # 9. Legend + Standard Mounting Heights Sidebar
    lx = pad_left + draw_w + 18.0
    ly = pad_top
    if discipline == "lighting":
        legend_items = [
            ("#c53030", "DB / MDB Distribution Board"),
            ("#fefcbf", "Ceiling Luminaire (IP20/44/65)"),
            ("#fef08a", "Dining Pendant / IP44 Mirror"),
            ("#fef3c7", "IP65 Outdoor PIR / Bollard"),
            ("#e0f2fe", "Bathroom Exhaust Fan (+2.4m)"),
            ("#bbf7d0", "Switch 1G/2G/2W/Int/Dimmer"),
            ("#dcfce7", "3P Fan Isolator (+2.2m)"),
            ("#16a34a", "Dashed Switch Leg (a / b)"),
            ("#b7791f", "Lighting Home-Run & Loops"),
        ]
    elif discipline == "power":
        legend_items = [
            ("#c53030", "DB / MDB Distribution Board"),
            ("#e6fffa", "Twin 13A Socket (+0.45/1.15m)"),
            ("#ebf8ff", "HVAC (Split A/C / HP / VRV)"),
            ("#e0e7ff", "Electric Shutter Switch (+1.2m)"),
            ("#fce7f3", "Kitchen FCU / 45A CCU / WH DP"),
            ("#cffafe", "Water Pumps / Gate / EV / Pool"),
            ("#dbeafe", "EAC Meter Cabinet (Relocatable)"),
            ("#dcfce7", "TT Earth Pit (Relocatable)"),
            ("#0284c7", "Power Home-Run & Loops"),
            ("#7c3aed", "Dedicated Heavy Circuits"),
            ("#fbb6ce", f"EAC Socket Excl ({eac.bathroom_socket_exclusion_m:.1f} m)"),
        ]
    elif discipline == "elv":
        legend_items = [
            ("#dbeafe", "9U Telecom Rack (Cyta Fiber)"),
            ("#eff6ff", "2xCat6 RJ45 Data (+0.45m)"),
            ("#f0fdf4", "TV / SAT Coax Outlet (+0.45m)"),
            ("#e0f2fe", "Ceiling PoE Wi-Fi 6 AP"),
            ("#eef2ff", "Video Intercom Out / Monitor"),
            ("#fef2f2", "EN 50131 Alarm / Keypad / PIR"),
            ("#f1f5f9", "IP67 PoE CCTV Camera (+2.8m)"),
            ("#fee2e2", "BS 5839-6 Smoke / Heat Loop"),
            ("#dcfce7", "KNX Smart Bus Keypad (Opt.)"),
        ]
    else:
        legend_items = [
            ("#c53030", "DB1 / MDB Distribution Board"),
            ("#fefcbf", "Luminaires / Pendant / PIR"),
            ("#bbf7d0", "Switches / Dimmers / 3P Fan"),
            ("#e6fffa", "Twin 13A Sockets (+0.45/1.15m)"),
            ("#ebf8ff", "HVAC / Shutters / FCUs / Pumps"),
            ("#dbeafe", "EAC Meter / 9U Telecom Rack"),
            ("#fee2e2", "BS 5839-6 Smoke / Alarm / CCTV"),
            ("#dcfce7", "TT Earth Pit / EV / KNX"),
            ("#0284c7", "Cyprus Home-Run & Loop Arcs"),
            ("#feb2b2", "Sec 701 Zone 0 / Zone 1"),
            ("#fbb6ce", f"EAC Socket Excl ({eac.bathroom_socket_exclusion_m:.1f} m)"),
        ]
    leg_h = 34 + len(legend_items) * 19 + 8
    parts.extend(
        [
            (
                f'  <rect x="{lx:.0f}" y="{ly:.0f}" width="214" height="{leg_h}" '
                'rx="4" fill="#f8fafc" stroke="#cbd5e0" stroke-width="1.2" />'
            ),
            (
                f'  <text x="{lx + 10:.0f}" y="{ly + 19:.0f}" class="leg-hdr">'
                f"LEGEND ({discipline.upper()})</text>"
            ),
        ]
    )
    for idx, (color, label) in enumerate(legend_items):
        iy = ly + 36 + idx * 19
        parts.append(
            f'  <rect x="{lx + 10:.0f}" y="{iy - 8:.0f}" width="11" height="9" '
            f'fill="{color}" stroke="#4a5568" stroke-width="0.8" />'
        )
        parts.append(f'  <text x="{lx + 26:.0f}" y="{iy:.0f}" class="leg-txt">{_esc(label)}</text>')

    # Compact Standard Mounting Heights box below Legend
    my = ly + leg_h + 12
    if discipline == "elv":
        mh_lines = [
            "Data RJ45 / TV: +450 mm (TV +1200)",
            "Intercom / Keypad / KNX: +1450 mm",
            "Alarm PIR: +2400 | CCTV: +2800 mm",
            "Smoke/Heat & Wi-Fi AP: Ceiling",
            "9U Telecom Rack: +1600 mm (FFL)",
        ]
    else:
        mh_lines = [
            "Sockets: +450 mm (Kitchen +1150)",
            "Switches/Shutters: +1200 (Bed +750)",
            "A/C & 3P Fan Isol: +2200 mm",
            "CCU: +1150 | WH DP / T-Stat: +1200",
            "DB Top: +1600 | Mirror: +2000",
        ]
    parts.append(
        f'  <rect x="{lx:.0f}" y="{my:.0f}" width="214" height="{26 + len(mh_lines) * 16}" '
        'rx="4" fill="#fffbeb" stroke="#f59e0b" stroke-width="1.1" />'
    )
    parts.append(
        f'  <text x="{lx + 10:.0f}" y="{my + 17:.0f}" class="leg-hdr">MOUNTING HEIGHTS (FFL)</text>'
    )
    for idx, mline in enumerate(mh_lines):
        parts.append(
            f'  <text x="{lx + 10:.0f}" y="{my + 33 + idx * 16:.0f}" class="leg-txt">'
            f"{_esc(mline)}</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)
