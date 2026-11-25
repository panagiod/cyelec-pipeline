"""Deterministic vector SVG renderer for architectural floorplan + Phase 2 electrical overlay."""

from __future__ import annotations

import html
import math

from cyelec.models.building import Point2D
from cyelec.models.spatial import DeviceType, Phase2DesignResult


def _esc(val: object) -> str:
    return html.escape(str(val), quote=True)


def render_floorplan_svg(result: Phase2DesignResult) -> str:
    """Render architectural floorplan, Section 701 wet zones, devices, and conduits as SVG."""
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

    span_x = max(1000.0, maxx - minx)
    span_y = max(1000.0, maxy - miny)

    # Map architectural mm -> SVG pixels (flipping Y axis so north is up)
    pad_left = 48.0
    pad_top = 56.0
    pad_right = 230.0  # Legend sidebar
    pad_bottom = 44.0
    draw_w = 880.0
    scale = draw_w / span_x
    draw_h = span_y * scale
    svg_w = int(math.ceil(pad_left + draw_w + pad_right))
    svg_h = int(math.ceil(pad_top + draw_h + pad_bottom))

    def tx(x_mm: float) -> float:
        return pad_left + (x_mm - minx) * scale

    def ty(y_mm: float) -> float:
        return pad_top + (maxy - y_mm) * scale

    def poly_points(pts: list[Point2D]) -> str:
        return " ".join(f"{tx(p.x_mm):.1f},{ty(p.y_mm):.1f}" for p in pts)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w} {svg_h}" '
            f'width="{svg_w}" height="{svg_h}">'
        ),
        "  <style>",
        '    text { font-family: "Noto Sans", "DejaVu Sans", Arial, sans-serif; fill: #1a202c; }',
        "    .fp-title { font-size: 14px; font-weight: 700; fill: #1b365d; }",
        "    .fp-sub { font-size: 10px; fill: #4a5568; }",
        "    .room-lbl { font-size: 9px; font-weight: 600; fill: #2d3748; }",
        "    .room-area { font-size: 8px; fill: #718096; }",
        "    .dev-tag { font-size: 7.2px; font-weight: 700; fill: #1a202c; }",
        "    .leg-hdr { font-size: 10px; font-weight: 700; fill: #1b365d; }",
        "    .leg-txt { font-size: 8.8px; fill: #2d3748; }",
        "  </style>",
        f'  <rect x="0" y="0" width="{svg_w}" height="{svg_h}" fill="#ffffff" />',
        (
            f'  <text x="{pad_left:.0f}" y="24" class="fp-title">'
            f"ELECTRICAL LAYOUT — {_esc(building.project_name)}</text>"
        ),
        (
            f'  <text x="{pad_left:.0f}" y="40" class="fp-sub">'
            f"Area: {building.total_floor_area_m2:.1f} m² | "
            f"Profile: {_esc(eac.eac_profile.value)} "
            f"(Bath excl: {eac.bathroom_socket_exclusion_m:.1f} m) | "
            f"Devices: {len(spatial.devices)} | "
            f"Conduit: {spatial.total_conduit_m:.1f} m</text>"
        ),
    ]

    # 1. Room fills
    for room in building.rooms:
        fill = "#edf2f7" if room.is_wet_room else ("#f0fff4" if room.is_outdoor else "#f8fafc")
        parts.append(
            f'  <polygon points="{poly_points(room.polygon_mm)}" '
            f'fill="{fill}" stroke="#cbd5e0" stroke-width="1" />'
        )

    # 2. BS 7671 Section 701 Wet Zones (Exclusion -> Zone 2 -> Zone 0/1)
    for wz in spatial.wet_zones:
        for ring in wz.socket_exclusion_polygons_mm:
            parts.append(
                f'  <polygon points="{poly_points(ring)}" '
                'fill="#fbb6ce" fill-opacity="0.28" stroke="#b83280" '
                'stroke-width="1.2" stroke-dasharray="5,3" />'
            )
        for ring in wz.zone_2_polygons_mm:
            parts.append(
                f'  <polygon points="{poly_points(ring)}" '
                'fill="#fbd38d" fill-opacity="0.45" stroke="#c05621" stroke-width="1" />'
            )
        for ring in wz.zone_0_polygons_mm:
            parts.append(
                f'  <polygon points="{poly_points(ring)}" '
                'fill="#feb2b2" fill-opacity="0.72" stroke="#c53030" stroke-width="1.4" />'
            )

    # 3. Plumbing fixtures
    for room in building.rooms:
        for fixt in room.fixtures:
            parts.append(
                f'  <polygon points="{poly_points(fixt.polygon_mm)}" '
                'fill="#bee3f8" fill-opacity="0.6" stroke="#2b6cb0" stroke-width="1" />'
            )

    # 4. Architectural walls, windows, and doors
    for wall in building.walls:
        sw = 3.2 if wall.is_exterior else 1.8
        parts.append(
            f'  <line x1="{tx(wall.start.x_mm):.1f}" y1="{ty(wall.start.y_mm):.1f}" '
            f'x2="{tx(wall.end.x_mm):.1f}" y2="{ty(wall.end.y_mm):.1f}" '
            f'stroke="#2d3748" stroke-width="{sw}" stroke-linecap="square" />'
        )
    for win in building.windows:
        parts.append(
            f'  <line x1="{tx(win.start.x_mm):.1f}" y1="{ty(win.start.y_mm):.1f}" '
            f'x2="{tx(win.end.x_mm):.1f}" y2="{ty(win.end.y_mm):.1f}" '
            'stroke="#00b5d8" stroke-width="4.2" />'
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
            'stroke="#38a169" stroke-width="1.6" />'
        )

    # 5. NetworkX Routed Conduit Paths
    for rc in spatial.routed_circuits:
        for seg in rc.segments:
            parts.append(
                f'  <line x1="{tx(seg.start_mm.x_mm):.1f}" y1="{ty(seg.start_mm.y_mm):.1f}" '
                f'x2="{tx(seg.end_mm.x_mm):.1f}" y2="{ty(seg.end_mm.y_mm):.1f}" '
                'stroke="#4a5568" stroke-width="1.15" stroke-opacity="0.55" />'
            )

    # 6. Room Bilingual Labels
    for room in building.rooms:
        c = room.centroid_mm()
        cx, cy = tx(c.x_mm), ty(c.y_mm)
        parts.append(
            f'  <text x="{cx:.1f}" y="{cy - 3:.1f}" text-anchor="middle" '
            f'class="room-lbl">{_esc(room.raw_label)}</text>'
        )
        parts.append(
            f'  <text x="{cx:.1f}" y="{cy + 9:.1f}" text-anchor="middle" '
            f'class="room-area">{room.area_m2:.1f} m²</text>'
        )

    # 7. Distribution Board Anchor (DB1)
    dbx = tx(building.db_anchor.location_mm.x_mm)
    dby = ty(building.db_anchor.location_mm.y_mm)
    parts.extend(
        [
            (
                f'  <rect x="{dbx - 14:.1f}" y="{dby - 8:.1f}" width="28" height="16" '
                'fill="#c53030" stroke="#742a2a" stroke-width="1.5" rx="2" />'
            ),
            (
                f'  <text x="{dbx:.1f}" y="{dby + 3:.1f}" text-anchor="middle" '
                f'style="fill:#ffffff;font-size:8px;font-weight:700;">'
                f"{_esc(building.db_anchor.id)}</text>"
            ),
        ]
    )

    # 8. Placed Electrical Devices
    for dev in spatial.devices:
        dx = tx(dev.location_mm.x_mm)
        dy = ty(dev.location_mm.y_mm)
        cid = dev.circuit_id or "?"
        if dev.device_type == DeviceType.LUMINAIRE:
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
        elif dev.device_type == DeviceType.SWITCH:
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4" '
                'fill="#c6f6d5" stroke="#2f855a" stroke-width="1.4" />'
            )
        elif dev.device_type == DeviceType.SOCKET_TWIN:
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.5" '
                'fill="#e6fffa" stroke="#008080" stroke-width="1.5" />'
            )
        elif dev.device_type == DeviceType.SPLIT_AC:
            parts.append(
                f'  <rect x="{dx - 9:.1f}" y="{dy - 5:.1f}" width="18" height="10" '
                'fill="#ebf8ff" stroke="#2b6cb0" stroke-width="1.5" rx="2" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#2b6cb0;font-size:6.5px;font-weight:700;">AC</text>'
            )
        else:
            parts.append(
                f'  <rect x="{dx - 5.5:.1f}" y="{dy - 5.5:.1f}" width="11" height="11" '
                'fill="#faf5ff" stroke="#6b46c1" stroke-width="1.5" />'
            )
        if dev.device_type in (
            DeviceType.SPLIT_AC,
            DeviceType.COOKER_OUTLET,
            DeviceType.OVEN_OUTLET,
            DeviceType.APPLIANCE_OUTLET,
            DeviceType.WATER_HEATER_ISOLATOR,
        ):
            parts.append(
                f'  <text x="{dx + 10:.1f}" y="{dy + 3:.1f}" class="dev-tag">{_esc(cid)}</text>'
            )

    # 9. Legend Panel (right sidebar)
    lx = pad_left + draw_w + 20.0
    ly = pad_top
    parts.extend(
        [
            (
                f'  <rect x="{lx:.0f}" y="{ly:.0f}" width="194" height="284" '
                'rx="4" fill="#f8fafc" stroke="#cbd5e0" stroke-width="1.2" />'
            ),
            f'  <text x="{lx + 12:.0f}" y="{ly + 20:.0f}" class="leg-hdr">LEGEND</text>',
        ]
    )
    legend_items = [
        ("#c53030", "DB1 Distribution Board"),
        ("#fefcbf", "Ceiling Luminaire (E-LITE)"),
        ("#c6f6d5", "Light Switch 1.2m (E-SWIT)"),
        ("#e6fffa", "Twin 13A Socket (E-SOCK)"),
        ("#ebf8ff", "Split A/C Isolator (E-AC)"),
        ("#faf5ff", "Dedicated Appliance (E-APPL)"),
        ("#4a5568", "NetworkX Routed Conduit"),
        ("#feb2b2", "Sec 701 Zone 0 / Zone 1"),
        ("#fbd38d", "Sec 701 Zone 2 (600 mm)"),
        ("#fbb6ce", f"EAC Socket Excl ({eac.bathroom_socket_exclusion_m:.1f} m)"),
        ("#00b5d8", "Window Opening"),
        ("#38a169", "Door Leaf / Swing"),
    ]
    for idx, (color, label) in enumerate(legend_items):
        iy = ly + 36 + idx * 20
        parts.append(
            f'  <rect x="{lx + 12:.0f}" y="{iy - 8:.0f}" width="12" height="10" '
            f'fill="{color}" stroke="#4a5568" stroke-width="0.8" />'
        )
        parts.append(f'  <text x="{lx + 30:.0f}" y="{iy:.0f}" class="leg-txt">{_esc(label)}</text>')

    parts.append("</svg>")
    return "\n".join(parts)
