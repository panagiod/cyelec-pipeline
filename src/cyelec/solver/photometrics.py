"""Point-by-point 2D working-plane photometric illuminance solver (DIALux-Lite).

Replaces single-number average Lumen Method approximations with a vectorized NumPy
point-by-point illuminance grid (`0.25 m x 0.25 m` working plane):
- Exact inverse-square + cosine-cubed direct illuminance from a flux-conserving
  polar intensity distribution I(theta) = ((n + 1) * Phi_m / (2 * pi)) * cos^n(theta).
- Sumpner interior cavity inter-reflection model (ceiling 0.75, walls 0.55, floor 0.20).
- Computes exact E_avg, E_min, E_max, and BS EN 12464-1 uniformity ratio U_0 = E_min / E_avg.
- Emits a compact SVG false-color Lux heatmap `<g class="lux-heatmap">` for E-101 overlays.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from shapely import contains_xy as shapely_contains_xy
from shapely.geometry import Polygon

from cyelec.models.building import BuildingModel, Room, RoomType
from cyelec.models.spatial import DeviceType, PlacedDevice

_LUM_DEVICE_TYPES: frozenset[DeviceType] = frozenset(
    {
        DeviceType.LUMINAIRE,
        DeviceType.PENDANT_LIGHT,
        DeviceType.WALL_SCONCE,
        DeviceType.LINEAR_COVE,
        DeviceType.OUTDOOR_PIR_LIGHT,
        DeviceType.MIRROR_LIGHT,
    }
)

_FLOOR_LEVEL_ROOMS: frozenset[RoomType] = frozenset(
    {
        RoomType.CORRIDOR,
        RoomType.STAIRWELL,
        RoomType.VERANDA,
        RoomType.STORAGE,
        RoomType.LIFT_SHAFT,
    }
)

_LED_EFFICACY_LM_PER_W = 105.0
_MAINTENANCE_FACTOR = 0.80


@dataclass(frozen=True)
class RoomPhotometricGridResult:
    """Computed 2D working-plane illuminance statistics & grid values for one room."""

    room_id: str
    avg_lux: float
    min_lux: float
    max_lux: float
    uniformity_u0: float
    u0_target: float
    u0_compliant: bool
    grid_points: int
    working_plane_m: float
    xs_mm: tuple[float, ...]
    ys_mm: tuple[float, ...]
    lux_values: tuple[float, ...]
    step_mm: float


def target_uniformity_u0(room_type: RoomType) -> float:
    """Return BS EN 12464-1 minimum illuminance uniformity ratio U_0 = E_min / E_avg."""
    if room_type in (RoomType.OPEN_OFFICE, RoomType.MEETING_ROOM):
        return 0.60
    if room_type in (
        RoomType.CORRIDOR,
        RoomType.VERANDA,
        RoomType.STORAGE,
        RoomType.STAIRWELL,
        RoomType.LIFT_SHAFT,
    ):
        return 0.30
    return 0.40


def direct_illuminance_point_lux(
    lumens_maintained: float,
    horizontal_dist_m: float,
    mounting_height_above_wp_m: float,
    beam_exponent_n: float = 1.0,
) -> float:
    """Closed-form direct horizontal illuminance E (lux) from one flux-conserving luminaire.

    Polar distribution: I(theta) = ((n + 1) * Phi_m / (2 * pi)) * cos^n(theta)
    Exact horizontal illuminance: E = I(theta) * cos(theta) / d^2
                                    = I_0 * h^(n+1) / (r^2 + h^2)^((n+3)/2)
    """
    h = max(0.35, mounting_height_above_wp_m)
    i0 = ((beam_exponent_n + 1.0) * lumens_maintained) / (2.0 * math.pi)
    d2 = horizontal_dist_m * horizontal_dist_m + h * h
    return float(i0 * (h ** (beam_exponent_n + 1.0)) / (d2 ** ((beam_exponent_n + 3.0) * 0.5)))


def _beam_exponent(dev: PlacedDevice) -> float:
    """Return polar distribution cosine exponent n (1.0=120° LED downlight, 0.5=wide bulkhead)."""
    if dev.device_type in (
        DeviceType.PENDANT_LIGHT,
        DeviceType.WALL_SCONCE,
        DeviceType.MIRROR_LIGHT,
        DeviceType.OUTDOOR_PIR_LIGHT,
    ):
        return 0.5
    if dev.luminaire_fixture_type and "bulkhead" in dev.luminaire_fixture_type.lower():
        return 0.5
    return 1.0


def evaluate_room_photometrics_2d(
    room: Room,
    room_luminaires: list[PlacedDevice],
    *,
    grid_step_mm: float = 250.0,
    maintenance_factor: float = _MAINTENANCE_FACTOR,
    include_indirect: bool = True,
) -> RoomPhotometricGridResult:
    """Compute point-by-point 2D working-plane illuminance grid & BS EN 12464-1 U_0."""
    poly = Polygon([(p.x_mm, p.y_mm) for p in room.polygon_mm])
    wp_m = 0.0 if room.normalized_type in _FLOOR_LEVEL_ROOMS else 0.80
    u0_tgt = target_uniformity_u0(room.normalized_type)

    if not room_luminaires or poly.is_empty or poly.area <= 1.0:
        return RoomPhotometricGridResult(
            room_id=room.id,
            avg_lux=0.0,
            min_lux=0.0,
            max_lux=0.0,
            uniformity_u0=0.0,
            u0_target=u0_tgt,
            u0_compliant=False,
            grid_points=0,
            working_plane_m=wp_m,
            xs_mm=(),
            ys_mm=(),
            lux_values=(),
            step_mm=grid_step_mm,
        )

    # BS EN 12464-1 standard 0.25m perimeter evaluation inset (fallback to full poly on micro-rooms)
    eval_poly = poly.buffer(-250.0) if room.area_m2 >= 4.0 else poly
    if eval_poly.is_empty or eval_poly.area < 0.25 * poly.area:
        eval_poly = poly

    minx, miny, maxx, maxy = eval_poly.bounds
    step = grid_step_mm
    # Keep large commercial halls fast (<2500 grid points per room) while retaining crisp resolution
    if room.area_m2 > 120.0:
        step = max(grid_step_mm, 500.0)

    gx = np.arange(minx + step * 0.5, maxx, step, dtype=np.float64)
    gy = np.arange(miny + step * 0.5, maxy, step, dtype=np.float64)
    if gx.size == 0:
        gx = np.array([(minx + maxx) * 0.5], dtype=np.float64)
    if gy.size == 0:
        gy = np.array([(miny + maxy) * 0.5], dtype=np.float64)

    xx, yy = np.meshgrid(gx, gy)
    mask = shapely_contains_xy(eval_poly, xx, yy)
    px = xx[mask]
    py = yy[mask]

    if px.size == 0:
        rp = poly.representative_point()
        px = np.array([float(rp.x)], dtype=np.float64)
        py = np.array([float(rp.y)], dtype=np.float64)

    e_grid = np.zeros_like(px, dtype=np.float64)
    total_maintained_lm = 0.0

    for lum in room_luminaires:
        lm_raw = float(
            lum.lumens or round(float(lum.luminaire_wattage_w or 15.0) * _LED_EFFICACY_LM_PER_W)
        )
        phi_m = lm_raw * maintenance_factor
        total_maintained_lm += phi_m

        z_m = max(1.8, (lum.mounting_height_mm or room.ceiling_height_mm) / 1000.0)
        h = max(0.5, z_m - wp_m)
        n_exp = _beam_exponent(lum)
        i0 = ((n_exp + 1.0) * phi_m) / (2.0 * math.pi)

        dx_m = (px - lum.location_mm.x_mm) / 1000.0
        dy_m = (py - lum.location_mm.y_mm) / 1000.0
        d2 = dx_m * dx_m + dy_m * dy_m + h * h
        e_grid += i0 * (h ** (n_exp + 1.0)) / np.power(d2, (n_exp + 3.0) * 0.5)

    # Sumpner cavity inter-reflection (indoor: 0.75 ceil / 0.55 wall / 0.20 floor)
    if include_indirect and total_maintained_lm > 0.0:
        area_m2 = max(0.5, room.area_m2)
        perim_m = max(2.0, poly.length / 1000.0)
        room_h_m = max(2.2, room.ceiling_height_mm / 1000.0)
        if room.is_outdoor:
            rho_bar = 0.08
            f_ref = 0.30
        else:
            rho_c, rho_w, rho_f = 0.75, 0.55, 0.20
            s_tot = 2.0 * area_m2 + perim_m * room_h_m
            rho_bar = (area_m2 * (rho_c + rho_f) + perim_m * room_h_m * rho_w) / max(s_tot, 1.0)
            f_ref = 0.65
        s_tot = 2.0 * area_m2 + perim_m * room_h_m
        e_indirect = (total_maintained_lm * rho_bar * f_ref) / max(s_tot * (1.0 - rho_bar), 0.5)
        e_grid += e_indirect

    avg_lux = round(float(np.mean(e_grid)), 1)
    min_lux = round(float(np.min(e_grid)), 1)
    max_lux = round(float(np.max(e_grid)), 1)
    u0 = round(min_lux / max(avg_lux, 1e-6), 2)
    u0_ok = u0 >= (u0_tgt - 1e-6)

    return RoomPhotometricGridResult(
        room_id=room.id,
        avg_lux=avg_lux,
        min_lux=min_lux,
        max_lux=max_lux,
        uniformity_u0=u0,
        u0_target=u0_tgt,
        u0_compliant=u0_ok,
        grid_points=int(px.size),
        working_plane_m=wp_m,
        xs_mm=tuple(float(v) for v in px),
        ys_mm=tuple(float(v) for v in py),
        lux_values=tuple(float(v) for v in e_grid),
        step_mm=step,
    )


def _lux_to_hex_color(lux: float) -> str:
    """Map illuminance (lx) onto a crisp engineering false-color palette (50..550+ lx)."""
    stops: tuple[tuple[float, tuple[int, int, int]], ...] = (
        (30.0, (59, 130, 246)),  # Deep Blue (#3b82f6)
        (110.0, (6, 182, 212)),  # Cyan (#06b6d4)
        (210.0, (34, 197, 94)),  # Emerald (#22c55e)
        (340.0, (234, 179, 8)),  # Amber (#eab308)
        (520.0, (239, 68, 68)),  # Red (#ef4444)
    )
    if lux <= stops[0][0]:
        r, g, b = stops[0][1]
        return f"#{r:02x}{g:02x}{b:02x}"
    if lux >= stops[-1][0]:
        r, g, b = stops[-1][1]
        return f"#{r:02x}{g:02x}{b:02x}"
    for (l0, c0), (l1, c1) in zip(stops, stops[1:], strict=False):
        if l0 <= lux <= l1:
            t = (lux - l0) / max(l1 - l0, 1e-6)
            r = int(round(c0[0] + (c1[0] - c0[0]) * t))
            g = int(round(c0[1] + (c1[1] - c0[1]) * t))
            b = int(round(c0[2] + (c1[2] - c0[2]) * t))
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#22c55e"


def render_lux_heatmap_svg_group(
    building: BuildingModel,
    devices: list[PlacedDevice],
    *,
    tx: Callable[[float], float],
    ty: Callable[[float], float],
    scale_px_per_mm: float,
) -> str:
    """Render a compact SVG `<g class="lux-heatmap">` overlay across all rooms."""
    devs_by_room: dict[str, list[PlacedDevice]] = {}
    for d in devices:
        if d.device_type in _LUM_DEVICE_TYPES:
            devs_by_room.setdefault(d.room_id, []).append(d)

    rects: list[str] = [
        '  <g class="lux-heatmap" fill-opacity="0.52" style="pointer-events:none;">'
    ]
    for room in building.rooms:
        lums = devs_by_room.get(room.id, [])
        if not lums:
            continue
        # Adaptive tile step keeps SVG heatmap lightweight (<15 KB residential, fast on 48-room)
        step_mm = 600.0 if (len(building.rooms) > 20 or room.area_m2 > 60.0) else 350.0
        res = evaluate_room_photometrics_2d(room, lums, grid_step_mm=step_mm)
        cell_px = max(2.0, res.step_mm * scale_px_per_mm)
        half = cell_px * 0.5
        for x_mm, y_mm, lv in zip(res.xs_mm, res.ys_mm, res.lux_values, strict=True):
            cx = tx(x_mm)
            cy = ty(y_mm)
            col = _lux_to_hex_color(lv)
            rects.append(
                f'    <rect x="{cx - half:.1f}" y="{cy - half:.1f}" '
                f'width="{cell_px + 0.5:.1f}" height="{cell_px + 0.5:.1f}" fill="{col}" />'
            )
    rects.append("  </g>")
    return "\n".join(rects)
