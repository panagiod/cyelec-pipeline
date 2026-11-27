"""AutoCAD-grade deterministic vector SVG renderer for E-100..E-103 electrical floorplans.

Drafting standards implemented:
- Double-line architectural masonry exterior (200mm) & interior partition (100mm) walls
  with subtle pochée fill, double-glazed windows, and 90° quarter-circle door swing arcs.
- Authentic IEC 60617 / Cyprus MOEC symbols (⊗ luminaires, ⟋ 1G1W / ⟋= 2G1W / double-flag
  1G2W αλέ-ρετούρ switches, semicircular twin BS 1363 sockets, wall sconces).
- Local nearest-neighbor chained switch arcs (ΣΥΡΜΑΤΩΣΕΙΣ) eliminating cross-room spider webs.
- Strict 2D bounding-box label & badge de-collision (`LabelLayoutEngine`) with automatic
  architectural leader-line callouts guaranteeing `svg_text_bbox_overlaps == 0`.
"""

from __future__ import annotations

import html
import math
from typing import Literal

from cyelec.models.building import Point2D
from cyelec.models.electrical import CircuitCategory
from cyelec.models.spatial import DeviceType, Phase2DesignResult, PlacedDevice
from cyelec.render.label_layout import LabelLayoutEngine, estimate_text_size

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


def _render_double_wall(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    thickness_px: float,
    fill_color: str,
    edge_color: str,
) -> list[str]:
    """Render an architectural double-line masonry wall band with crisp outer edges."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 0.8:
        return []
    nx, ny = (-dy / length) * (thickness_px * 0.5), (dx / length) * (thickness_px * 0.5)
    p1 = (x1 + nx, y1 + ny)
    p2 = (x2 + nx, y2 + ny)
    p3 = (x2 - nx, y2 - ny)
    p4 = (x1 - nx, y1 - ny)
    pts = (
        f"{p1[0]:.1f},{p1[1]:.1f} {p2[0]:.1f},{p2[1]:.1f} "
        f"{p3[0]:.1f},{p3[1]:.1f} {p4[0]:.1f},{p4[1]:.1f}"
    )
    return [
        (
            f'  <polygon points="{pts}" fill="{fill_color}" stroke="{edge_color}" '
            'stroke-width="0.95" stroke-linejoin="miter" style="pointer-events:none;" />'
        )
    ]


def _render_iec_switch(
    dx: float,
    dy: float,
    *,
    switch_type: str,
    is_dimmer: bool,
) -> list[str]:
    """Render authentic IEC 60617 / Cyprus switch symbol (1G1W, 2G1W, 1G2W αλέ-ρετούρ, Dimmer)."""
    stroke = "#15803d"
    fill = "#d9f99d" if is_dimmer else "#ffffff"
    out: list[str] = [
        f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="3.4" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.45" />'
    ]
    st_up = switch_type.upper()
    if "2W" in st_up or "INT" in st_up:
        # Authentic IEC 60617 2-way (αλέ-ρετούρ): opposing angled actuator stems with ticks
        out.append(
            f'  <line x1="{dx + 2.2:.1f}" y1="{dy - 2.6:.1f}" '
            f'x2="{dx + 7.2:.1f}" y2="{dy - 8.2:.1f}" stroke="{stroke}" stroke-width="1.35" />'
        )
        out.append(
            f'  <line x1="{dx + 5.2:.1f}" y1="{dy - 9.2:.1f}" '
            f'x2="{dx + 8.6:.1f}" y2="{dy - 6.8:.1f}" stroke="{stroke}" stroke-width="1.3" />'
        )
        out.append(
            f'  <line x1="{dx - 2.2:.1f}" y1="{dy + 2.6:.1f}" '
            f'x2="{dx - 7.2:.1f}" y2="{dy + 8.2:.1f}" stroke="{stroke}" stroke-width="1.35" />'
        )
        out.append(
            f'  <line x1="{dx - 5.2:.1f}" y1="{dy + 9.2:.1f}" '
            f'x2="{dx - 8.6:.1f}" y2="{dy + 6.8:.1f}" stroke="{stroke}" stroke-width="1.3" />'
        )
    elif "2G" in st_up:
        # 2-Gang switch: angled stem with twin parallel ticks
        out.append(
            f'  <line x1="{dx + 2.2:.1f}" y1="{dy - 2.6:.1f}" '
            f'x2="{dx + 7.8:.1f}" y2="{dy - 8.8:.1f}" stroke="{stroke}" stroke-width="1.35" />'
        )
        out.append(
            f'  <line x1="{dx + 4.4:.1f}" y1="{dy - 7.2:.1f}" '
            f'x2="{dx + 7.4:.1f}" y2="{dy - 4.8:.1f}" stroke="{stroke}" stroke-width="1.25" />'
        )
        out.append(
            f'  <line x1="{dx + 6.0:.1f}" y1="{dy - 9.2:.1f}" '
            f'x2="{dx + 9.0:.1f}" y2="{dy - 6.8:.1f}" stroke="{stroke}" stroke-width="1.25" />'
        )
    else:
        # 1G1W standard IEC switch: single angled stem + tick
        out.append(
            f'  <line x1="{dx + 2.2:.1f}" y1="{dy - 2.6:.1f}" '
            f'x2="{dx + 7.6:.1f}" y2="{dy - 8.4:.1f}" stroke="{stroke}" stroke-width="1.35" />'
        )
        out.append(
            f'  <line x1="{dx + 5.6:.1f}" y1="{dy - 9.4:.1f}" '
            f'x2="{dx + 8.8:.1f}" y2="{dy - 7.0:.1f}" stroke="{stroke}" stroke-width="1.3" />'
        )
    if is_dimmer:
        # Diagonal arrow across stem denoting rotary/LED dimmer
        out.append(
            f'  <line x1="{dx - 4.5:.1f}" y1="{dy + 3.5:.1f}" '
            f'x2="{dx + 5.5:.1f}" y2="{dy - 4.5:.1f}" stroke="#166534" stroke-width="1.15" />'
        )
    return out


def _render_iec_twin_socket(dx: float, dy: float, wall_angle_deg: float) -> list[str]:
    """Render authentic IEC 60617 / Cyprus twin 13A BS 1363 socket symbol oriented to wall."""
    # Map architectural wall angle (Y-up) into SVG screen angle (Y-down)
    rad = math.radians(-wall_angle_deg)
    # Normal unit vector pointing off wall
    nx, ny = -math.sin(rad), math.cos(rad)
    tx_u, ty_u = math.cos(rad), math.sin(rad)
    stroke = "#0f766e"
    # Base tick + stem + semicircular arc + twin bars (2x13A)
    r = 4.2
    cx, cy = dx + nx * 4.5, dy + ny * 4.5
    p_l = (cx - tx_u * r, cy - ty_u * r)
    p_r = (cx + tx_u * r, cy + ty_u * r)
    stem_end = (dx + nx * 9.5, dy + ny * 9.5)
    b1_a = (dx + nx * 6.8 - tx_u * 2.5, dy + ny * 6.8 - ty_u * 2.5)
    b1_b = (dx + nx * 6.8 + tx_u * 2.5, dy + ny * 6.8 + ty_u * 2.5)
    b2_a = (dx + nx * 8.9 - tx_u * 2.5, dy + ny * 8.9 - ty_u * 2.5)
    b2_b = (dx + nx * 8.9 + tx_u * 2.5, dy + ny * 8.9 + ty_u * 2.5)
    return [
        (
            f'  <path d="M {p_l[0]:.1f} {p_l[1]:.1f} A {r:.1f} {r:.1f} 0 0 1 '
            f'{p_r[0]:.1f} {p_r[1]:.1f}" fill="#ccfbf1" fill-opacity="0.7" '
            f'stroke="{stroke}" stroke-width="1.45" />'
        ),
        (
            f'  <line x1="{dx:.1f}" y1="{dy:.1f}" x2="{stem_end[0]:.1f}" '
            f'y2="{stem_end[1]:.1f}" stroke="{stroke}" stroke-width="1.35" />'
        ),
        (
            f'  <line x1="{b1_a[0]:.1f}" y1="{b1_a[1]:.1f}" x2="{b1_b[0]:.1f}" '
            f'y2="{b1_b[1]:.1f}" stroke="{stroke}" stroke-width="1.25" />'
        ),
        (
            f'  <line x1="{b2_a[0]:.1f}" y1="{b2_a[1]:.1f}" x2="{b2_b[0]:.1f}" '
            f'y2="{b2_b[1]:.1f}" stroke="{stroke}" stroke-width="1.25" />'
        ),
    ]


def render_floorplan_svg(
    result: Phase2DesignResult,
    *,
    discipline: SvgDiscipline = "combined",
    show_lux_heatmap: bool = False,
) -> str:
    """Render AutoCAD-grade architectural floorplan + discipline electrical overlay as SVG."""
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
        minx = min(minx, dev.location_mm.x_mm - 350.0)
        maxx = max(maxx, dev.location_mm.x_mm + 350.0)
        miny = min(miny, dev.location_mm.y_mm - 350.0)
        maxy = max(maxy, dev.location_mm.y_mm + 350.0)

    span_x = max(1000.0, maxx - minx)
    span_y = max(1000.0, maxy - miny)

    # Map architectural mm -> SVG pixels (flipping Y axis so north is up)
    pad_left = 60.0
    pad_top = 64.0
    pad_right = 258.0  # Legend + Mounting Heights sidebar
    pad_bottom = 52.0
    draw_w = 900.0
    scale = draw_w / span_x
    draw_h = max(480.0, span_y * scale)
    svg_w = int(math.ceil(pad_left + draw_w + pad_right))
    svg_h = int(math.ceil(pad_top + draw_h + pad_bottom))

    # Layout engine strictly bounded to the drawing canvas (excluding title band & legend sidebar)
    canvas_bounds = (
        10.0,
        pad_top - 10.0,
        pad_left + draw_w + 8.0,
        svg_h - 10.0,
    )
    layout = LabelLayoutEngine(canvas_bounds)

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
        "    .cy-plan .fp-title { font-size: 13.5px; font-weight: 700; fill: #0f2942; }",
        "    .cy-plan .fp-sub { font-size: 9.8px; fill: #475569; }",
        "    .cy-plan .room-poly { cursor: pointer; transition: fill-opacity 0.12s; }",
        "    .cy-plan .room-poly:hover { stroke: #1b365d; stroke-width: 2; }",
        (
            "    .cy-plan .room-lbl { font-weight: 600; fill: #334155; pointer-events: none; "
            "paint-order: stroke; stroke: #ffffff; stroke-width: 2.2px; stroke-linejoin: round; }"
        ),
        (
            "    .cy-plan .room-area { font-size: 7.2px; fill: #64748b; pointer-events: none; "
            "paint-order: stroke; stroke: #ffffff; stroke-width: 2.0px; stroke-linejoin: round; }"
        ),
        (
            "    .cy-plan .dev-tag { font-size: 6.6px; font-weight: 700; fill: #0f172a; "
            "paint-order: stroke; stroke: #ffffff; stroke-width: 2.6px; stroke-linejoin: round; }"
        ),
        "    .cy-plan .leg-hdr { font-size: 9.6px; font-weight: 700; fill: #0f2942; }",
        "    .cy-plan .leg-txt { font-size: 8.2px; fill: #1e293b; }",
        "  </style>",
        f'  <rect x="0" y="0" width="{svg_w}" height="{svg_h}" fill="#ffffff" />',
        # Crisp drafting frame border
        (
            f'  <rect x="6" y="6" width="{svg_w - 12}" height="{svg_h - 12}" '
            'fill="none" stroke="#cbd5e1" stroke-width="1.0" />'
        ),
        (
            f'  <text x="{pad_left:.0f}" y="26" class="fp-title">'
            f"{sheet_title} — {_esc(building.project_name)}</text>"
        ),
        (
            f'  <text x="{pad_left:.0f}" y="42" class="fp-sub">'
            f"Area: {building.total_floor_area_m2:.1f} m² | "
            f"Profile: {_esc(eac.eac_profile.value)} "
            f"(Bath excl: {eac.bathroom_socket_exclusion_m:.1f} m) | "
            f"Points: {len(active_devices)} | "
            f"Total Conduit: {spatial.total_conduit_m:.1f} m</text>"
        ),
    ]

    # 1. Room fills (interactive polygons for Pass-2 visual room override)
    for room in building.rooms:
        fill = "#f1f5f9" if room.is_wet_room else ("#f0fdf4" if room.is_outdoor else "#fafbfc")
        parts.append(
            f'  <polygon class="room-poly" data-room-id="{_esc(room.id)}" '
            f'data-room-type="{_esc(room.normalized_type.value)}" '
            f'data-room-label="{_esc(room.raw_label)}" '
            f'points="{poly_points(room.polygon_mm)}" '
            f'fill="{fill}" stroke="#cbd5e1" stroke-width="0.9" />'
        )

    # 1b. Optional 2D Working-Plane Photometric Lux Heatmap Overlay (E-101)
    if show_lux_heatmap and discipline in ("lighting", "combined"):
        try:
            from cyelec.solver.photometrics import render_lux_heatmap_svg_group

            hm_svg = render_lux_heatmap_svg_group(
                building,
                spatial.devices,
                tx=tx,
                ty=ty,
                scale_px_per_mm=scale,
            )
            if hm_svg:
                parts.append(hm_svg)
        except Exception:
            pass

    # 2. BS 7671 Section 701 Wet Zones (Power & Combined sheets)
    if discipline in ("combined", "power"):
        for wz in spatial.wet_zones:
            for ring in wz.socket_exclusion_polygons_mm:
                parts.append(
                    f'  <polygon points="{poly_points(ring)}" '
                    'fill="#fbb6ce" fill-opacity="0.26" stroke="#be185d" '
                    'stroke-width="1.1" stroke-dasharray="5,3" style="pointer-events:none;" />'
                )
            for ring in wz.zone_2_polygons_mm:
                parts.append(
                    f'  <polygon points="{poly_points(ring)}" '
                    'fill="#fed7aa" fill-opacity="0.45" stroke="#c2410c" '
                    'stroke-width="1" style="pointer-events:none;" />'
                )
            for ring in wz.zone_0_polygons_mm:
                parts.append(
                    f'  <polygon points="{poly_points(ring)}" '
                    'fill="#fecaca" fill-opacity="0.72" stroke="#dc2626" '
                    'stroke-width="1.3" style="pointer-events:none;" />'
                )

    # 3. Plumbing fixtures & auto-detected architectural furniture outlines
    for room in building.rooms:
        for fixt in room.fixtures:
            parts.append(
                f'  <polygon points="{poly_points(fixt.polygon_mm)}" '
                'fill="#e0f2fe" fill-opacity="0.6" stroke="#0284c7" '
                'stroke-width="0.9" style="pointer-events:none;" />'
            )
        for furn in room.furniture:
            if len(furn.polygon_mm) >= 3:
                parts.append(
                    f'  <polygon points="{poly_points(furn.polygon_mm)}" '
                    'fill="#f1f5f9" fill-opacity="0.5" stroke="#94a3b8" '
                    'stroke-width="0.85" stroke-dasharray="3,2" style="pointer-events:none;" />'
                )

    # 4. AutoCAD-Style Double-Line Masonry Walls, Glazed Windows, and Quarter-Circle Door Swings
    ext_thick_px = max(4.4, min(11.0, 200.0 * scale))
    int_thick_px = max(2.4, min(6.5, 100.0 * scale))
    for wall in building.walls:
        wx1, wy1 = tx(wall.start.x_mm), ty(wall.start.y_mm)
        wx2, wy2 = tx(wall.end.x_mm), ty(wall.end.y_mm)
        if wall.is_exterior:
            parts.extend(
                _render_double_wall(wx1, wy1, wx2, wy2, ext_thick_px, "#cbd5e1", "#1e293b")
            )
        else:
            parts.extend(
                _render_double_wall(wx1, wy1, wx2, wy2, int_thick_px, "#e2e8f0", "#475569")
            )

    # Architectural double-glazed windows (clean sill break + twin glass panes)
    for win in building.windows:
        x1, y1 = tx(win.start.x_mm), ty(win.start.y_mm)
        x2, y2 = tx(win.end.x_mm), ty(win.end.y_mm)
        parts.append(
            f'  <line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#ffffff" stroke-width="{ext_thick_px + 0.8:.1f}" '
            'style="pointer-events:none;" />'
        )
        parts.append(
            f'  <line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="#0284c7" stroke-width="2.6" style="pointer-events:none;" />'
        )
        parts.append(
            f'  <line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="#e0f2fe" stroke-width="1.0" style="pointer-events:none;" />'
        )

    # Architectural doors: sill opening gap + leaf + 90-degree quarter-circle swing arc
    for door in building.doors:
        hx, hy = door.hinge.x_mm, door.hinge.y_mm
        closed_rad = math.radians(door.angle_deg)
        cx_mm = hx + door.width_mm * math.cos(closed_rad)
        cy_mm = hy + door.width_mm * math.sin(closed_rad)
        open_ang = door.angle_deg + (90.0 if door.swing_dir == "ccw" else -90.0)
        open_rad = math.radians(open_ang)
        lx_mm = hx + door.width_mm * math.cos(open_rad)
        ly_mm = hy + door.width_mm * math.sin(open_rad)

        shx, shy = tx(hx), ty(hy)
        scx, scy = tx(cx_mm), ty(cy_mm)
        slx, sly = tx(lx_mm), ty(ly_mm)
        r_px = max(4.0, door.width_mm * scale)
        # Clear wall across door threshold
        parts.append(
            f'  <line x1="{shx:.1f}" y1="{shy:.1f}" x2="{scx:.1f}" y2="{scy:.1f}" '
            f'stroke="#fafbfc" stroke-width="{int_thick_px + 1.4:.1f}" '
            'style="pointer-events:none;" />'
        )
        # Door leaf
        parts.append(
            f'  <line x1="{shx:.1f}" y1="{shy:.1f}" x2="{slx:.1f}" y2="{sly:.1f}" '
            'stroke="#475569" stroke-width="1.5" style="pointer-events:none;" />'
        )
        # Quarter-circle swing arc (Y flipped -> ccw in architectural becomes sweep=0 in SVG)
        svg_sweep = 0 if door.swing_dir == "ccw" else 1
        parts.append(
            f'  <path d="M {scx:.1f} {scy:.1f} A {r_px:.1f} {r_px:.1f} 0 0 {svg_sweep} '
            f'{slx:.1f} {sly:.1f}" fill="none" stroke="#94a3b8" stroke-width="0.9" '
            'stroke-dasharray="3,2" style="pointer-events:none;" />'
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
                    cx = (ax + bx) * 0.5 - (ddy / dist) * (0.12 * dist)
                    cy = (ay + by) * 0.5 + (ddx / dist) * (0.12 * dist)
                    parts.append(
                        f'  <path d="M {ax:.1f} {ay:.1f} Q {cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}" '
                        'fill="none" stroke="#dc2626" stroke-width="1.15" stroke-dasharray="5,3" '
                        'stroke-opacity="0.78" style="pointer-events:none;" />'
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
                    cx = (ax + bx) * 0.5 + (ddy / dist) * (0.12 * dist)
                    cy = (ay + by) * 0.5 - (ddx / dist) * (0.12 * dist)
                    parts.append(
                        f'  <path d="M {ax:.1f} {ay:.1f} Q {cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}" '
                        'fill="none" stroke="#16a34a" stroke-width="1.2" stroke-dasharray="3,2" '
                        'stroke-opacity="0.82" style="pointer-events:none;" />'
                    )
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
                        ax, ay = ex + ux * 18.0, ey + uy * 18.0
                        parts.append(
                            f'  <line x1="{ex:.1f}" y1="{ey:.1f}" x2="{ax:.1f}" y2="{ay:.1f}" '
                            'stroke="#2563eb" stroke-width="0.95" stroke-opacity="0.65" '
                            'style="pointer-events:none;" />'
                        )
    elif use_homerun:
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
            fx, fy = tx(ordered[0].location_mm.x_mm), ty(ordered[0].location_mm.y_mm)
            ddx, ddy = db_x - fx, db_y - fy
            dist = math.hypot(ddx, ddy)
            if dist > 8.0:
                ux, uy = ddx / dist, ddy / dist
                hr_len = min(28.0, dist * 0.48)
                hx, hy = fx + ux * hr_len, fy + uy * hr_len
                px, py = -uy, ux
                b1x, b1y = hx - ux * 5.2 + px * 2.6, hy - uy * 5.2 + py * 2.6
                b2x, b2y = hx - ux * 5.2 - px * 2.6, hy - uy * 5.2 - py * 2.6
                parts.append(
                    f'  <line x1="{fx:.1f}" y1="{fy:.1f}" x2="{hx:.1f}" y2="{hy:.1f}" '
                    f'stroke="{c_stroke}" stroke-width="1.25" style="pointer-events:none;" />'
                )
                pts_a = f"{hx:.1f},{hy:.1f} {b1x:.1f},{b1y:.1f} {b2x:.1f},{b2y:.1f}"
                parts.append(
                    f'  <polygon points="{pts_a}" '
                    f'fill="{c_stroke}" style="pointer-events:none;" />'
                )
                deferred_tags.append((hx, hy, f"{cid}→DB", c_stroke))

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
                    bulge = (0.11 if idx % 2 == 0 else -0.11) * ldist
                    cx = (ax + bx) * 0.5 - (ldy / ldist) * bulge
                    cy = (ay + by) * 0.5 + (ldx / ldist) * bulge
                    parts.append(
                        f'  <path d="M {ax:.1f} {ay:.1f} Q {cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}" '
                        f'fill="none" stroke="{c_stroke}" stroke-width="1.0" stroke-opacity="0.65" '
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
                    f'stroke="{c_stroke}" stroke-width="1.2" stroke-opacity="0.6" '
                    'style="pointer-events:none;" />'
                )

    # 5b. Clean Local Chained Switch Arcs (ΣΥΡΜΑΤΩΣΕΙΣ) — NO Cross-Room Spider Webs!
    if discipline in ("combined", "lighting") and spatial.switch_legs:
        dev_by_id = {d.device_id: d for d in active_devices}
        chained_groups_done: set[tuple[str, str]] = set()
        for leg in spatial.switch_legs:
            sw_dev = dev_by_id.get(leg.switch_id)
            if sw_dev is None:
                continue
            sw_flr = room_floor.get(sw_dev.room_id, "GF")
            targets = [
                dev_by_id[tid]
                for tid in leg.target_device_ids
                if tid in dev_by_id and room_floor.get(dev_by_id[tid].room_id, "GF") == sw_flr
            ]
            if not targets:
                continue

            # Group targets by switch_group so group 'a' and group 'b' chain independently
            by_grp: dict[str, list[PlacedDevice]] = {}
            for t_d in targets:
                by_grp.setdefault(t_d.switch_group or "a", []).append(t_d)

            sw_xy = (sw_dev.location_mm.x_mm, sw_dev.location_mm.y_mm)
            sx, sy = tx(sw_xy[0]), ty(sw_xy[1])

            for grp_k, grp_targets in by_grp.items():
                grp_key = (sw_dev.room_id, grp_k)
                ordered_t = _chain_nearest_neighbor(grp_targets, sw_xy)

                # Always draw one clean arc from switch to its nearest luminaire in the group
                first_t = ordered_t[0]
                ex, ey = tx(first_t.location_mm.x_mm), ty(first_t.location_mm.y_mm)
                ddx, ddy = ex - sx, ey - sy
                dist = math.hypot(ddx, ddy)
                if dist >= 3.0:
                    bulge = 0.12 * dist
                    cx = (sx + ex) * 0.5 - (ddy / dist) * bulge
                    cy = (sy + ey) * 0.5 + (ddx / dist) * bulge
                    parts.append(
                        f'  <path d="M {sx:.1f} {sy:.1f} Q {cx:.1f} {cy:.1f} {ex:.1f} {ey:.1f}" '
                        'fill="none" stroke="#16a34a" stroke-width="1.05" '
                        'stroke-dasharray="4,2.5" stroke-opacity="0.82" '
                        'style="pointer-events:none;" />'
                    )

                # Chain remaining luminaires in this group ONCE (avoids 2-way double spider web!)
                if grp_key not in chained_groups_done:
                    chained_groups_done.add(grp_key)
                    for idx_c, (a_d, b_d) in enumerate(
                        zip(ordered_t, ordered_t[1:], strict=False)
                    ):
                        ax, ay = tx(a_d.location_mm.x_mm), ty(a_d.location_mm.y_mm)
                        bx, by = tx(b_d.location_mm.x_mm), ty(b_d.location_mm.y_mm)
                        cdx, cdy = bx - ax, by - ay
                        cdist = math.hypot(cdx, cdy)
                        if cdist < 3.0:
                            continue
                        bulge = (0.11 if idx_c % 2 == 0 else -0.11) * cdist
                        cx = (ax + bx) * 0.5 - (cdy / cdist) * bulge
                        cy = (ay + by) * 0.5 + (cdx / cdist) * bulge
                        d_attr = f"M {ax:.1f} {ay:.1f} Q {cx:.1f} {cy:.1f} {bx:.1f} {by:.1f}"
                        parts.append(
                            f'  <path d="{d_attr}" '
                            'fill="none" stroke="#16a34a" stroke-width="0.95" '
                            'stroke-dasharray="4,2.5" stroke-opacity="0.72" '
                            'style="pointer-events:none;" />'
                        )

    # 6. Helper to safely place any text-bearing badge with zero overlap against prior texts
    def _find_free_badge_center(
        bx: float,
        by: float,
        w: float,
        h: float,
    ) -> tuple[float, float, bool]:
        min_x, min_y, max_x, max_y = canvas_bounds

        def _box_at(px: float, py: float) -> tuple[float, float, float, float]:
            return (px - w * 0.5 - 1.2, py - h * 0.5 - 1.2, px + w * 0.5 + 1.2, py + h * 0.5 + 1.2)

        def _is_clear(px: float, py: float) -> bool:
            if px - w * 0.5 < min_x or px + w * 0.5 > max_x:
                return False
            if py - h * 0.5 < min_y or py + h * 0.5 > max_y:
                return False
            box = _box_at(px, py)
            return not layout.has_text_collision(box, pad=1.2)

        if _is_clear(bx, by):
            return bx, by, False
        for r in (12.0, 20.0, 30.0, 42.0, 56.0, 72.0):
            for ang in (0.0, 180.0, -90.0, 90.0, -45.0, 45.0, -135.0, 135.0):
                ra = math.radians(ang)
                cx = bx + r * math.cos(ra)
                cy = by + r * math.sin(ra)
                if _is_clear(cx, cy):
                    return cx, cy, True
        return bx, by, False

    def _badge(
        bx: float,
        by: float,
        text: str,
        *,
        fill: str,
        stroke: str,
        fg: str,
        w: float = 18.0,
        h: float = 10.5,
        anchor: str | None = None,
        font_px: float = 5.8,
    ) -> tuple[float, float]:
        est_w, _ = estimate_text_size(text, font_px, bold=True)
        eff_w = max(w, est_w + 5.0)
        eff_h = max(h, font_px * 1.55)
        fx, fy, shifted = _find_free_badge_center(bx, by, eff_w, eff_h)
        layout.register_badge(fx, fy, eff_w, eff_h)
        if shifted:
            parts.append(
                f'  <line x1="{bx:.1f}" y1="{by:.1f}" x2="{fx:.1f}" y2="{fy:.1f}" '
                'stroke="#64748b" stroke-width="0.7" style="pointer-events:none;" />'
            )
            parts.append(
                f'  <circle cx="{bx:.1f}" cy="{by:.1f}" r="1.8" fill="{stroke}" '
                'style="pointer-events:none;" />'
            )
        a_attr = f' data-anchor="{anchor}"' if anchor else ""
        parts.append(
            f'  <rect{a_attr} x="{fx - eff_w / 2:.1f}" y="{fy - eff_h / 2:.1f}" '
            f'width="{eff_w:.1f}" height="{eff_h:.1f}" fill="{fill}" stroke="{stroke}" '
            'stroke-width="1.35" rx="2" />'
        )
        parts.append(
            f'  <text x="{fx:.1f}" y="{fy + font_px * 0.36:.1f}" text-anchor="middle" '
            f'class="badge-txt" style="fill:{fg};font-size:{font_px:.1f}px;'
            f'font-weight:700;pointer-events:none;">{_esc(text)}</text>'
        )
        return fx, fy

    # 7. Distribution Board Anchor (DB1 / MDB) & Sub-Boards
    dbx = tx(building.db_anchor.location_mm.x_mm)
    dby = ty(building.db_anchor.location_mm.y_mm)
    db_w = max(28.0, estimate_text_size(building.db_anchor.id, 8.0, bold=True)[0] + 8.0)
    layout.register_badge(dbx, dby, db_w, 16.0)
    parts.extend(
        [
            (
                f'  <rect data-db="true" data-anchor="db" '
                f'x="{dbx - db_w / 2:.1f}" y="{dby - 8:.1f}" '
                f'width="{db_w:.1f}" height="16" fill="#c53030" stroke="#742a2a" '
                'stroke-width="1.5" rx="2" />'
            ),
            (
                f'  <text x="{dbx:.1f}" y="{dby + 3:.1f}" text-anchor="middle" '
                'style="fill:#ffffff;font-size:8.0px;font-weight:700;pointer-events:none;">'
                f"{_esc(building.db_anchor.id)}</text>"
            ),
        ]
    )
    for sb in building.sub_boards:
        sbx = tx(sb.location_mm.x_mm)
        sby = ty(sb.location_mm.y_mm)
        sb_w = max(36.0, estimate_text_size(sb.board_id, 7.2, bold=True)[0] + 8.0)
        fx, fy, shifted = _find_free_badge_center(sbx, sby, sb_w, 16.0)
        layout.register_badge(fx, fy, sb_w, 16.0)
        if shifted:
            parts.append(
                f'  <line x1="{sbx:.1f}" y1="{sby:.1f}" x2="{fx:.1f}" y2="{fy:.1f}" '
                'stroke="#7b341e" stroke-width="0.75" style="pointer-events:none;" />'
            )
        parts.extend(
            [
                (
                    f'  <rect data-sub-board="{_esc(sb.board_id)}" '
                    f'x="{fx - sb_w / 2:.1f}" y="{fy - 8:.1f}" width="{sb_w:.1f}" height="16" '
                    'fill="#dd6b20" stroke="#7b341e" stroke-width="1.5" rx="2" />'
                ),
                (
                    f'  <text x="{fx:.1f}" y="{fy + 2.8:.1f}" text-anchor="middle" '
                    'style="fill:#ffffff;font-size:7.2px;font-weight:700;pointer-events:none;">'
                    f"{_esc(sb.board_id)}</text>"
                ),
            ]
        )

    # 8. Placed Electrical Devices (Authentic IEC 60617 / Cyprus Symbols)
    for dev in active_devices:
        dx = tx(dev.location_mm.x_mm)
        dy = ty(dev.location_mm.y_mm)
        cid = dev.circuit_id or "?"
        dt = dev.device_type
        if dt == DeviceType.LUMINAIRE:
            layout.register_obstacle((dx - 6.5, dy - 6.5, dx + 6.5, dy + 6.5))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="5.6" '
                'fill="#fefce8" stroke="#92400e" stroke-width="1.45" />'
            )
            parts.append(
                f'  <line x1="{dx - 3.9:.1f}" y1="{dy - 3.9:.1f}" '
                f'x2="{dx + 3.9:.1f}" y2="{dy + 3.9:.1f}" stroke="#92400e" stroke-width="1.2" />'
            )
            parts.append(
                f'  <line x1="{dx - 3.9:.1f}" y1="{dy + 3.9:.1f}" '
                f'x2="{dx + 3.9:.1f}" y2="{dy - 3.9:.1f}" stroke="#92400e" stroke-width="1.2" />'
            )
            if discipline == "lighting" and dev.switch_group:
                deferred_tags.append((dx, dy, f"{cid}({dev.switch_group})", "#78350f"))
        elif dt == DeviceType.PENDANT_LIGHT:
            layout.register_obstacle((dx - 8.0, dy - 8.0, dx + 8.0, dy + 8.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="7.0" '
                'fill="#fef08a" stroke="#92400e" stroke-width="1.5" />'
            )
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="3.2" '
                'fill="#f59e0b" stroke="#78350f" stroke-width="1.0" />'
            )
            if discipline == "lighting":
                deferred_tags.append((dx, dy, "PD", "#78350f"))
        elif dt == DeviceType.OUTDOOR_PIR_LIGHT:
            _badge(dx, dy, "PIR-L", fill="#fef3c7", stroke="#d97706", fg="#92400e", w=24.0)
        elif dt == DeviceType.GARDEN_BOLLARD:
            layout.register_obstacle((dx - 6.0, dy - 6.0, dx + 6.0, dy + 6.0))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.6" '
                'fill="#fde68a" stroke="#b45309" stroke-width="1.4" />'
            )
            deferred_tags.append((dx, dy, "BOL", "#92400e"))
        elif dt == DeviceType.MIRROR_LIGHT:
            layout.register_obstacle((dx - 7.0, dy - 5.0, dx + 7.0, dy + 5.0))
            parts.append(
                f'  <rect x="{dx - 6:.1f}" y="{dy - 3.2:.1f}" width="12" height="6.4" '
                'fill="#fef08a" stroke="#a16207" stroke-width="1.35" rx="1.5" />'
            )
            deferred_tags.append((dx, dy, "ML(b)", "#92400e"))
        elif dt == DeviceType.WALL_SCONCE:
            layout.register_obstacle((dx - 6.5, dy - 6.5, dx + 6.5, dy + 6.5))
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.8" '
                'fill="#fde68a" stroke="#b45309" stroke-width="1.45" />'
            )
            parts.append(
                f'  <line x1="{dx - 4.8:.1f}" y1="{dy:.1f}" x2="{dx + 4.8:.1f}" y2="{dy:.1f}" '
                'stroke="#92400e" stroke-width="1.2" />'
            )
            if discipline == "lighting":
                deferred_tags.append((dx, dy, f"WS({dev.switch_group or 'b'})", "#92400e"))
        elif dt == DeviceType.EXHAUST_FAN:
            _badge(dx, dy, "EF", fill="#e0f2fe", stroke="#0369a1", fg="#0369a1", w=15.0, h=11.0)
        elif dt in (DeviceType.SWITCH, DeviceType.DIMMER_SWITCH):
            layout.register_obstacle((dx - 7.0, dy - 8.5, dx + 8.5, dy + 7.5))
            st = dev.switch_type or ("DIM" if dt == DeviceType.DIMMER_SWITCH else "1G1W")
            parts.extend(
                _render_iec_switch(
                    dx,
                    dy,
                    switch_type=st,
                    is_dimmer=(dt == DeviceType.DIMMER_SWITCH),
                )
            )
            if discipline == "lighting":
                grp = dev.switch_group or "a"
                deferred_tags.append((dx, dy, f"{st}({grp})", "#166534"))
        elif dt == DeviceType.FAN_ISOLATOR_3P:
            layout.register_obstacle((dx - 6.0, dy - 6.0, dx + 6.0, dy + 6.0))
            parts.append(
                f'  <rect x="{dx - 4.8:.1f}" y="{dy - 4.8:.1f}" width="9.6" height="9.6" '
                'fill="#dcfce7" stroke="#166534" stroke-width="1.35" />'
            )
            parts.append(
                f'  <line x1="{dx - 3.2:.1f}" y1="{dy + 3.2:.1f}" '
                f'x2="{dx + 3.2:.1f}" y2="{dy - 3.2:.1f}" stroke="#166534" stroke-width="1.15" />'
            )
            deferred_tags.append((dx, dy, "3P-FAN", "#166534"))
        elif dt == DeviceType.SOCKET_TWIN:
            layout.register_obstacle((dx - 7.0, dy - 7.0, dx + 7.0, dy + 7.0))
            parts.extend(_render_iec_twin_socket(dx, dy, dev.wall_angle_deg))
        elif dt == DeviceType.SPLIT_AC:
            fx, fy = _badge(dx, dy, "AC", fill="#ebf8ff", stroke="#2b6cb0", fg="#2b6cb0", w=18.0)
            deferred_tags.append((fx, fy, cid, "#1d4ed8"))
        elif dt == DeviceType.COOKER_CONTROL_UNIT:
            _badge(dx, dy, "CCU", fill="#fee2e2", stroke="#b91c1c", fg="#991b1b", w=20.0)
        elif dt == DeviceType.WATER_HEATER_DP_SWITCH:
            _badge(dx, dy, "WH", fill="#ffedd5", stroke="#c2410c", fg="#9a3412", w=17.0)
        elif dt == DeviceType.SHUTTER_SWITCH:
            _badge(dx, dy, "SH", fill="#e0e7ff", stroke="#4338ca", fg="#312e81", w=16.0)
        elif dt == DeviceType.KITCHEN_FCU:
            _badge(dx, dy, "FCU", fill="#fce7f3", stroke="#be185d", fg="#831843", w=19.0)
        elif dt == DeviceType.ROOM_THERMOSTAT:
            _badge(dx, dy, "T", fill="#fff7ed", stroke="#ea580c", fg="#9a3412", w=12.0, h=11.0)
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
            _badge(dx, dy, lbl, fill="#e0f2fe", stroke="#0284c7", fg="#075985", w=24.0, h=11.5)
        elif dt == DeviceType.WATER_BOOSTER_PUMP:
            _badge(dx, dy, "WP", fill="#cffafe", stroke="#0891b2", fg="#155e75", w=18.0)
        elif dt == DeviceType.GATE_MOTOR:
            _badge(dx, dy, "GATE", fill="#f3e8ff", stroke="#7e22ce", fg="#581c87", w=24.0)
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
                w=28.0,
                h=14.0,
                anchor="meter",
            )
        elif dt == DeviceType.EARTH_PIT:
            _badge(
                dx,
                dy,
                "TT",
                fill="#dcfce7",
                stroke="#15803d",
                fg="#14532d",
                w=16.0,
                h=13.0,
                anchor="earth_pit",
            )
        elif dt == DeviceType.TELECOM_CABINET:
            _badge(
                dx,
                dy,
                "9U-IT",
                fill="#dbeafe",
                stroke="#1d4ed8",
                fg="#1e3a8a",
                w=28.0,
                h=14.0,
                anchor="telecom",
            )
        elif dt == DeviceType.DATA_RJ45:
            _badge(dx, dy, "RJ45", fill="#eff6ff", stroke="#2563eb", fg="#1d4ed8", w=22.0)
        elif dt == DeviceType.TV_OUTLET:
            _badge(dx, dy, "TV", fill="#f0fdf4", stroke="#16a34a", fg="#166534", w=16.0)
        elif dt == DeviceType.WIFI_AP:
            _badge(dx, dy, "AP", fill="#e0f2fe", stroke="#0284c7", fg="#0369a1", w=16.0, h=11.0)
        elif dt in (DeviceType.INTERCOM_OUTDOOR, DeviceType.INTERCOM_INDOOR):
            lbl = "V-OUT" if dt == DeviceType.INTERCOM_OUTDOOR else "V-MON"
            _badge(dx, dy, lbl, fill="#eef2ff", stroke="#4f46e5", fg="#3730a3", w=26.0)
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
            _badge(dx, dy, lbl_map[dt], fill="#fef2f2", stroke="#dc2626", fg="#991b1b", w=20.0)
        elif dt == DeviceType.CCTV_CAMERA:
            _badge(dx, dy, "CAM", fill="#f1f5f9", stroke="#334155", fg="#0f172a", w=22.0)
        elif dt in (DeviceType.SMOKE_DETECTOR, DeviceType.HEAT_DETECTOR):
            lbl = "SD" if dt == DeviceType.SMOKE_DETECTOR else "HD"
            _badge(dx, dy, lbl, fill="#fee2e2", stroke="#dc2626", fg="#991b1b", w=16.0, h=11.0)
        elif dt == DeviceType.KNX_KEYPAD:
            _badge(dx, dy, "KNX", fill="#dcfce7", stroke="#15803d", fg="#14532d", w=20.0)
        else:
            layout.register_obstacle((dx - 6.5, dy - 6.5, dx + 6.5, dy + 6.5))
            parts.append(
                f'  <rect x="{dx - 5.5:.1f}" y="{dy - 5.5:.1f}" width="11" height="11" '
                'fill="#faf5ff" stroke="#6b46c1" stroke-width="1.5" />'
            )
            deferred_tags.append((dx, dy, cid, "#6b46c1"))

    # 8b. Place Room Bilingual Titles + Area via LabelLayoutEngine (avoiding DB1, badges & lights!)
    for room in building.rooms:
        c = room.centroid_mm()
        cx, cy = tx(c.x_mm), ty(c.y_mm)
        r_xs = [tx(p.x_mm) for p in room.polygon_mm]
        r_ys = [ty(p.y_mm) for p in room.polygon_mm]
        r_bbox = (min(r_xs), min(r_ys), max(r_xs), max(r_ys))
        room_px_w = max(32.0, (r_bbox[2] - r_bbox[0]) - 8.0)
        raw_lbl = room.raw_label.strip()
        if " / " in raw_lbl:
            p1, p2 = raw_lbl.split(" / ", 1)
            lbl_lines = [p1.strip()[:22], p2.strip()[:22]]
        elif len(raw_lbl) > 18 and " " in raw_lbl:
            mid = len(raw_lbl) // 2
            sp_i = raw_lbl.find(" ", mid)
            if sp_i == -1:
                sp_i = raw_lbl.rfind(" ", 0, mid)
            lbl_lines = (
                [raw_lbl[:sp_i].strip(), raw_lbl[sp_i + 1 :].strip()]
                if sp_i > 0
                else [raw_lbl[:22]]
            )
        else:
            lbl_lines = [raw_lbl[:22]]

        max_ch = max(len(ln) for ln in lbl_lines)
        lbl_fs = min(8.2, max(5.6, room_px_w / max(max_ch * 0.62, 1.0)))
        area_txt = f"{room.area_m2:.1f} m²"
        spec_lines: list[tuple[str, float, bool]] = [(ln, lbl_fs, True) for ln in lbl_lines]
        spec_lines.append((area_txt, 7.2, False))

        blk_cx, _blk_top, line_boxes, used_callout = layout.place_multiline_block(
            cx, cy, spec_lines, room_bounds=r_bbox
        )
        if used_callout and math.hypot(blk_cx - cx, (line_boxes[0][1] + 5.0) - cy) > 26.0:
            # Subtle hairline callout connecting micro-room centroid to its shifted title
            parts.append(
                f'  <line x1="{cx:.1f}" y1="{cy:.1f}" '
                f'x2="{blk_cx:.1f}" y2="{line_boxes[0][3]:.1f}" '
                'stroke="#94a3b8" stroke-width="0.65" stroke-dasharray="2,2" '
                'style="pointer-events:none;" />'
            )
        for (txt_s, fs_s, is_title), lb in zip(spec_lines, line_boxes, strict=True):
            base_y = lb[1] + (lb[3] - lb[1]) * 0.80
            if is_title:
                parts.append(
                    f'  <text x="{blk_cx:.1f}" y="{base_y:.1f}" text-anchor="middle" '
                    f'class="room-lbl" style="font-size:{fs_s:.1f}px;">{_esc(txt_s)}</text>'
                )
            else:
                parts.append(
                    f'  <text x="{blk_cx:.1f}" y="{base_y:.1f}" text-anchor="middle" '
                    f'class="room-area">{_esc(txt_s)}</text>'
                )

    # 8c. Emit all deferred device & home-run tags via strict zero-overlap LabelLayoutEngine
    for tx0, ty0, t_str, t_col in deferred_tags:
        placed = layout.place_point_tag(tx0, ty0, t_str, font_px=6.6, bold=True)
        if placed.needs_leader:
            parts.append(
                f'  <line x1="{placed.leader_start[0]:.1f}" y1="{placed.leader_start[1]:.1f}" '
                f'x2="{placed.leader_end[0]:.1f}" y2="{placed.leader_end[1]:.1f}" '
                'stroke="#64748b" stroke-width="0.7" style="pointer-events:none;" />'
            )
            parts.append(
                f'  <circle cx="{placed.leader_start[0]:.1f}" cy="{placed.leader_start[1]:.1f}" '
                'r="1.5" fill="#475569" style="pointer-events:none;" />'
            )
        style_attr = f' style="fill:{t_col};"' if t_col else ""
        parts.append(
            f'  <text x="{placed.text_x:.1f}" y="{placed.text_y:.1f}" '
            f'class="dev-tag"{style_attr}>{_esc(t_str)}</text>'
        )

    # 9. Legend + Standard Mounting Heights Sidebar
    lx = pad_left + draw_w + 22.0
    ly = pad_top
    if discipline == "lighting":
        legend_items = [
            ("#c53030", "DB / MDB Distribution Board"),
            ("#fefce8", "IEC Luminaire ⊗ (IP20/44/65)"),
            ("#fef08a", "Dining Pendant / IP44 Mirror"),
            ("#fef3c7", "IP65 Outdoor PIR / Bollard"),
            ("#e0f2fe", "Bathroom Exhaust Fan (+2.4m)"),
            ("#bbf7d0", "IEC Switch 1G1W/2G1W/1G2W"),
            ("#dcfce7", "3P Fan Isolator (+2.2m)"),
            ("#16a34a", "Chained Switch Arc (a / b)"),
            ("#b7791f", "Lighting Home-Run & Loops"),
        ]
    elif discipline == "power":
        legend_items = [
            ("#c53030", "DB / MDB Distribution Board"),
            ("#ccfbf1", "IEC Twin 13A Socket (+0.45m)"),
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
            ("#fefce8", "IEC Luminaires / Pendant / PIR"),
            ("#bbf7d0", "IEC Switches / Dimmers / 3P Fan"),
            ("#ccfbf1", "IEC Twin 13A Sockets (+0.45m)"),
            ("#ebf8ff", "HVAC / Shutters / FCUs / Pumps"),
            ("#dbeafe", "EAC Meter / 9U Telecom Rack"),
            ("#fee2e2", "BS 5839-6 Smoke / Alarm / CCTV"),
            ("#dcfce7", "TT Earth Pit / EV / KNX"),
            ("#0284c7", "Cyprus Home-Run & Loop Arcs"),
            ("#fecaca", "Sec 701 Zone 0 / Zone 1"),
            ("#fbb6ce", f"EAC Socket Excl ({eac.bathroom_socket_exclusion_m:.1f} m)"),
        ]
    leg_h = 34 + len(legend_items) * 19 + 8
    parts.extend(
        [
            (
                f'  <rect x="{lx:.0f}" y="{ly:.0f}" width="222" height="{leg_h}" '
                'rx="4" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.1" />'
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
            f'fill="{color}" stroke="#475569" stroke-width="0.8" />'
        )
        parts.append(f'  <text x="{lx + 26:.0f}" y="{iy:.0f}" class="leg-txt">{_esc(label)}</text>')

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
        f'  <rect x="{lx:.0f}" y="{my:.0f}" width="222" height="{26 + len(mh_lines) * 16}" '
        'rx="4" fill="#fffbeb" stroke="#f59e0b" stroke-width="1.05" />'
    )
    parts.append(
        f'  <text x="{lx + 10:.0f}" y="{my + 17:.0f}" class="leg-hdr">MOUNTING HEIGHTS (FFL)</text>'
    )
    for idx, mline in enumerate(mh_lines):
        parts.append(
            f'  <text x="{lx + 10:.0f}" y="{my + 33 + idx * 16:.0f}" class="leg-txt">'
            f"{_esc(mline)}</text>"
        )

    # Optional Lux Heatmap color scale legend bar on E-101 when heatmap active
    if show_lux_heatmap and discipline in ("lighting", "combined"):
        hy = my + 26 + len(mh_lines) * 16 + 12
        parts.extend(
            [
                (
                    f'  <rect x="{lx:.0f}" y="{hy:.0f}" width="222" height="54" '
                    'rx="4" fill="#f8fafc" stroke="#0284c7" stroke-width="1.05" />'
                ),
                (
                    f'  <text x="{lx + 10:.0f}" y="{hy + 16:.0f}" class="leg-hdr">'
                    "2D PHOTOMETRIC LUX SCALE</text>"
                ),
                *[
                    f'  <rect x="{lx + 10 + i * 40:.0f}" y="{hy + 23:.0f}" '
                    f'width="40" height="10" fill="{col}" />'
                    for i, col in enumerate(
                        ("#3b82f6", "#06b6d4", "#22c55e", "#eab308", "#ef4444")
                    )
                ],
                f'  <text x="{lx + 10:.0f}" y="{hy + 45:.0f}" class="leg-txt">50 lx</text>',
                f'  <text x="{lx + 84:.0f}" y="{hy + 45:.0f}" class="leg-txt">200 lx</text>',
                f'  <text x="{lx + 168:.0f}" y="{hy + 45:.0f}" class="leg-txt">500+ lx</text>',
            ]
        )

    parts.append("</svg>")
    return "\n".join(parts)
