"""Deterministic vector SVG renderer for architectural floorplan + Phase 2 electrical overlay."""

from __future__ import annotations

import html
import math
from typing import Literal

from cyelec.models.building import Point2D
from cyelec.models.electrical import CircuitCategory
from cyelec.models.spatial import DeviceType, Phase2DesignResult

SvgDiscipline = Literal["combined", "lighting", "power"]

_LIGHTING_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.LUMINAIRE,
        DeviceType.MIRROR_LIGHT,
        DeviceType.EXHAUST_FAN,
        DeviceType.SWITCH,
        DeviceType.FAN_ISOLATOR_3P,
    }
)
_POWER_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.SOCKET_TWIN,
        DeviceType.SPLIT_AC,
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


def _esc(val: object) -> str:
    return html.escape(str(val), quote=True)


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

    # Expand bounds slightly if EAC Meter Cabinet / Earth Pit sit outside building envelope
    if discipline in ("combined", "power"):
        for dev in spatial.devices:
            if dev.device_type in (DeviceType.EAC_METER_CABINET, DeviceType.EARTH_PIT):
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

    def tx(x_mm: float) -> float:
        return pad_left + (x_mm - minx) * scale

    def ty(y_mm: float) -> float:
        return pad_top + (maxy - y_mm) * scale

    def poly_points(pts: list[Point2D]) -> str:
        return " ".join(f"{tx(p.x_mm):.1f},{ty(p.y_mm):.1f}" for p in pts)

    sheet_title = (
        "E-101 LIGHTING &amp; SWITCHING LAYOUT"
        if discipline == "lighting"
        else (
            "E-102 POWER, SOCKETS &amp; ACCESSORIES LAYOUT"
            if discipline == "power"
            else "E-100 COMBINED ELECTRICAL LAYOUT (COORDINATION)"
        )
    )
    if discipline == "lighting":
        active_devices = [d for d in spatial.devices if d.device_type in _LIGHTING_TYPES]
    elif discipline == "power":
        active_devices = [d for d in spatial.devices if d.device_type in _POWER_TYPES]
    else:
        active_devices = list(spatial.devices)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w} {svg_h}" '
            f'width="{svg_w}" height="{svg_h}" '
            f'data-minx="{minx:.2f}" data-maxy="{maxy:.2f}" '
            f'data-scale="{scale:.6f}" data-pad-left="{pad_left:.2f}" '
            f'data-pad-top="{pad_top:.2f}">'
        ),
        "  <style>",
        '    text { font-family: "DejaVu Sans", "Noto Sans", Arial, sans-serif; fill: #1a202c; }',
        "    .fp-title { font-size: 13.5px; font-weight: 700; fill: #1b365d; }",
        "    .fp-sub { font-size: 9.8px; fill: #4a5568; }",
        "    .room-poly { cursor: pointer; transition: fill-opacity 0.12s; }",
        "    .room-poly:hover { stroke: #1b365d; stroke-width: 2; }",
        "    .room-lbl { font-size: 9px; font-weight: 600; fill: #2d3748; pointer-events: none; }",
        "    .room-area { font-size: 8px; fill: #718096; pointer-events: none; }",
        "    .dev-tag { font-size: 7.0px; font-weight: 700; fill: #1a202c; }",
        "    .leg-hdr { font-size: 9.8px; font-weight: 700; fill: #1b365d; }",
        "    .leg-txt { font-size: 8.4px; fill: #2d3748; }",
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

    # 3. Plumbing fixtures
    for room in building.rooms:
        for fixt in room.fixtures:
            parts.append(
                f'  <polygon points="{poly_points(fixt.polygon_mm)}" '
                'fill="#bee3f8" fill-opacity="0.6" stroke="#2b6cb0" '
                'stroke-width="1" style="pointer-events:none;" />'
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
            'stroke="#00b5d8" stroke-width="4.2" style="pointer-events:none;" />'
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
            'stroke="#38a169" stroke-width="1.6" style="pointer-events:none;" />'
        )

    # 5. Color-Coded Discipline-Filtered NetworkX Routed Conduit Paths
    cat_map = {c.spec.circuit_id: c.spec.category for c in result.design.circuits}
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

    # 7. Distribution Board Anchor (DB1 / MDB) & Sub-Boards
    dbx = tx(building.db_anchor.location_mm.x_mm)
    dby = ty(building.db_anchor.location_mm.y_mm)
    parts.extend(
        [
            (
                f'  <rect data-db="true" x="{dbx - 14:.1f}" y="{dby - 8:.1f}" '
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

    # 8. Placed Electrical Devices
    for dev in active_devices:
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
            if discipline == "lighting" and dev.switch_group:
                parts.append(
                    f'  <text x="{dx + 8:.1f}" y="{dy + 2.5:.1f}" class="dev-tag">'
                    f"{_esc(cid)}({dev.switch_group})</text>"
                )
        elif dev.device_type == DeviceType.MIRROR_LIGHT:
            parts.append(
                f'  <rect x="{dx - 6:.1f}" y="{dy - 3.5:.1f}" width="12" height="7" '
                'fill="#fef08a" stroke="#a16207" stroke-width="1.4" rx="2" />'
            )
            parts.append(
                f'  <text x="{dx + 8:.1f}" y="{dy + 2.5:.1f}" class="dev-tag">ML(b)</text>'
            )
        elif dev.device_type == DeviceType.EXHAUST_FAN:
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="6" '
                'fill="#e0f2fe" stroke="#0369a1" stroke-width="1.5" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#0369a1;font-size:6.2px;font-weight:700;">EF</text>'
            )
        elif dev.device_type == DeviceType.SWITCH:
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.2" '
                'fill="#c6f6d5" stroke="#15803d" stroke-width="1.4" />'
            )
            if discipline == "lighting":
                st = dev.switch_type or "1G1W"
                grp = dev.switch_group or "a"
                parts.append(
                    f'  <text x="{dx + 7:.1f}" y="{dy + 2.5:.1f}" class="dev-tag">'
                    f"{_esc(st)}({_esc(grp)})</text>"
                )
        elif dev.device_type == DeviceType.FAN_ISOLATOR_3P:
            parts.append(
                f'  <rect x="{dx - 5:.1f}" y="{dy - 5:.1f}" width="10" height="10" '
                'fill="#dcfce7" stroke="#166534" stroke-width="1.3" />'
            )
            parts.append(
                f'  <text x="{dx + 7:.1f}" y="{dy + 2.5:.1f}" class="dev-tag">3P-FAN</text>'
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
            parts.append(
                f'  <text x="{dx + 11:.1f}" y="{dy + 3:.1f}" class="dev-tag">{_esc(cid)}</text>'
            )
        elif dev.device_type == DeviceType.COOKER_CONTROL_UNIT:
            parts.append(
                f'  <rect x="{dx - 8:.1f}" y="{dy - 5:.1f}" width="16" height="10" '
                'fill="#fee2e2" stroke="#b91c1c" stroke-width="1.5" rx="1.5" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#991b1b;font-size:5.8px;font-weight:700;">CCU</text>'
            )
        elif dev.device_type == DeviceType.WATER_HEATER_DP_SWITCH:
            parts.append(
                f'  <rect x="{dx - 7:.1f}" y="{dy - 5:.1f}" width="14" height="10" '
                'fill="#ffedd5" stroke="#c2410c" stroke-width="1.5" rx="1.5" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#9a3412;font-size:5.8px;font-weight:700;">DP</text>'
            )
        elif dev.device_type == DeviceType.EAC_METER_CABINET:
            parts.append(
                f'  <rect x="{dx - 13:.1f}" y="{dy - 7:.1f}" width="26" height="14" '
                'fill="#dbeafe" stroke="#1e40af" stroke-width="1.6" rx="2" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#1e3a8a;font-size:6.4px;font-weight:700;">EAC-M</text>'
            )
        elif dev.device_type == DeviceType.EARTH_PIT:
            parts.append(
                f'  <circle cx="{dx:.1f}" cy="{dy:.1f}" r="6.5" '
                'fill="#dcfce7" stroke="#15803d" stroke-width="1.6" />'
            )
            parts.append(
                f'  <text x="{dx:.1f}" y="{dy + 2.5:.1f}" text-anchor="middle" '
                'style="fill:#14532d;font-size:6.2px;font-weight:700;">TT</text>'
            )
        else:
            parts.append(
                f'  <rect x="{dx - 5.5:.1f}" y="{dy - 5.5:.1f}" width="11" height="11" '
                'fill="#faf5ff" stroke="#6b46c1" stroke-width="1.5" />'
            )
            parts.append(
                f'  <text x="{dx + 8:.1f}" y="{dy + 3:.1f}" class="dev-tag">{_esc(cid)}</text>'
            )

    # 9. Legend + Standard Mounting Heights Sidebar
    lx = pad_left + draw_w + 18.0
    ly = pad_top
    if discipline == "lighting":
        legend_items = [
            ("#c53030", "DB / MDB Distribution Board"),
            ("#fefcbf", "Ceiling Luminaire (IP20/44/65)"),
            ("#fef08a", "IP44 Vanity Mirror Light (+2.0m)"),
            ("#e0f2fe", "Bathroom Exhaust Fan (+2.4m)"),
            ("#c6f6d5", "Switch 1G/2G/2W (+1.2m)"),
            ("#dcfce7", "3P Fan Isolator (+2.2m)"),
            ("#16a34a", "Dashed Switch Leg (a / b)"),
            ("#b7791f", "Lighting Conduit (Ø20 PVC)"),
        ]
    elif discipline == "power":
        legend_items = [
            ("#c53030", "DB / MDB Distribution Board"),
            ("#e6fffa", "Twin 13A Socket (+0.45/1.15m)"),
            ("#ebf8ff", "20A DP Split A/C (+2.2m)"),
            ("#fee2e2", "45A Cooker Control (+1.15m)"),
            ("#ffedd5", "20A DP Water Heater (+1.2m)"),
            ("#faf5ff", "Dedicated Appliance (+0.45m)"),
            ("#dbeafe", "EAC Meter Cabinet (Verify Site)"),
            ("#dcfce7", "TT Earth Rod Pit (Ø16×1.2m)"),
            ("#0284c7", "Power Ring/Radial Conduit"),
            ("#7c3aed", "Dedicated Heavy Conduit"),
            ("#fbb6ce", f"EAC Socket Excl ({eac.bathroom_socket_exclusion_m:.1f} m)"),
        ]
    else:
        legend_items = [
            ("#c53030", "DB1 Distribution Board"),
            ("#fefcbf", "Ceiling Luminaire / Mirror"),
            ("#c6f6d5", "Switch (1G/2G/2W) + 3P Fan"),
            ("#16a34a", "Dashed Switch Legs (a / b)"),
            ("#e6fffa", "Twin 13A Socket (+0.45/1.15m)"),
            ("#ebf8ff", "Split A/C Isolator (+2.2m)"),
            ("#fee2e2", "45A CCU / 20A DP / Appl"),
            ("#dbeafe", "EAC Meter Box / TT Earth Pit"),
            ("#0284c7", "Power / Lighting Conduits"),
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
    mh_lines = [
        "Sockets: +450 mm (Kitchen +1150)",
        "Switches: +1200 mm (Bed 2W +750)",
        "A/C & 3P Fan Isol: +2200 mm",
        "CCU: +1150 | WH DP: +1200 mm",
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
