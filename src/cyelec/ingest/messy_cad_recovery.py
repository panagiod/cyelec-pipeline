"""Messy real-world Cyprus CAD recovery helpers (MTEXT stripping, double-wall & door recovery).

Handles unlayered or inconsistently structured AutoCAD DXF/DWG architectural drawings:
1. Strips AutoCAD MTEXT escape codes (`{\\fArial|b1;...}`, `\\P`, `\\A1;`, `\\H1.2x;`, etc.)
   and normalizes PDF/CAD Greek homoglyphs (`\\u00b5` micro sign -> `μ`, Latin lookalikes).
2. Identifies structural double-line masonry wall cavities (`80–320 mm` parallel pairs)
   even when walls are dumped on Layer `0` or arbitrary architect layer names.
3. Detects unblocked loose door swings (quarter-circle `ARC` paired with radial `LINE` leaf).
4. Scores per-room `detection_confidence` (`high` / `medium` / `low`) based on polygon
   compactness, text-match quality, and recovery path.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from shapely.geometry import LineString, Polygon

from cyelec.models.building import DoorOpening, Point2D

# AutoCAD MTEXT formatting sequences: {\fFont|b1|i0;text}, \P, \A1;, \C7;, \H0.8x;, \W0.8;, etc.
_MTEXT_GROUP_RE = re.compile(r"\\[fFHWCQATp][^;]*;")
_MTEXT_NEWLINE_RE = re.compile(r"\\[PpNn]")
_MTEXT_STACK_RE = re.compile(r"\\S([^;^#/]+)[/^#]([^;]*);")
_MTEXT_SIMPLE_RE = re.compile(r"\\[LlOoKk~]")
_GREEK_CHAR_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")

# Common Latin lookalikes emitted inside Greek words by CAD SHX / PDF Type3 font encoders
_LATIN_IN_GREEK_MAP = str.maketrans(
    {
        "A": "Α",
        "B": "Β",
        "E": "Ε",
        "H": "Η",
        "I": "Ι",
        "K": "Κ",
        "M": "Μ",
        "N": "Ν",
        "O": "Ο",
        "P": "Ρ",
        "T": "Τ",
        "X": "Χ",
        "Y": "Υ",
        "Z": "Ζ",
        "a": "α",
        "e": "ε",
        "i": "ι",
        "k": "κ",
        "o": "ο",
        "p": "ρ",
        "v": "ν",
        "x": "χ",
    }
)


def strip_mtext_formatting(raw: str) -> str:
    """Strip AutoCAD MTEXT rich-text control sequences and normalize CAD Greek homoglyphs."""
    if not raw:
        return ""
    s = raw.replace("\u00b5", "μ").replace("\u03bc", "μ")
    s = _MTEXT_STACK_RE.sub(r"\1/\2", s)
    s = _MTEXT_NEWLINE_RE.sub(" ", s)
    s = _MTEXT_GROUP_RE.sub("", s)
    s = _MTEXT_SIMPLE_RE.sub("", s)
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    # Normalize per-token Latin homoglyphs ONLY inside tokens that already contain Greek letters
    tokens: list[str] = []
    for tok in s.split():
        if _GREEK_CHAR_RE.search(tok):
            tokens.append(tok.translate(_LATIN_IN_GREEK_MAP))
        else:
            tokens.append(tok)
    return " ".join(tokens).strip()


def extract_parallel_double_wall_lines(
    segments: list[tuple[Point2D, Point2D]],
    *,
    min_length_mm: float = 350.0,
    min_cavity_mm: float = 75.0,
    max_cavity_mm: float = 340.0,
    angle_tol_deg: float = 5.0,
) -> list[tuple[Point2D, Point2D]]:
    """Return subset of line segments that form architectural double-line masonry wall pairs.

    Real Cyprus architect CAD drawings (especially Layer-0 exports) draw 100mm internal
    partitions and 200-300mm external masonry walls as pairs of parallel lines separated by
    `75-340 mm` with substantial longitudinal overlap. Furniture, hatching, and dimension lines
    do not form long overlapping pairs at masonry wall thickness.
    """
    # Filter to candidate structural-length segments
    cands: list[tuple[int, Point2D, Point2D, float, float, float, float]] = []
    for idx, (p0, p1) in enumerate(segments):
        dx = p1.x_mm - p0.x_mm
        dy = p1.y_mm - p0.y_mm
        length = math.hypot(dx, dy)
        if length < min_length_mm:
            continue
        ang = math.degrees(math.atan2(dy, dx)) % 180.0
        ux, uy = dx / length, dy / length
        cands.append((idx, p0, p1, length, ang, ux, uy))

    if len(cands) < 4:
        return []

    # Bucket by quantized orientation (5° buckets) for O(N) average-case wall-pair matching
    buckets: dict[int, list[tuple[int, Point2D, Point2D, float, float, float, float]]] = {}
    for item in cands:
        b = int(round(item[4] / angle_tol_deg)) % int(round(180.0 / angle_tol_deg))
        buckets.setdefault(b, []).append(item)

    matched_indices: set[int] = set()
    num_buckets = int(round(180.0 / angle_tol_deg))

    for b_id, group in buckets.items():
        # Include adjacent angular bucket to avoid boundary misses
        neighbors = list(group)
        nxt_b = (b_id + 1) % num_buckets
        if nxt_b in buckets and nxt_b != b_id:
            neighbors.extend(buckets[nxt_b])

        for i in range(len(group)):
            idx_a, a0, _a1, len_a, ang_a, ux, uy = group[i]
            nx, ny = -uy, ux
            # Projection along wall axis u and perpendicular axis n
            proj_a0 = a0.x_mm * ux + a0.y_mm * uy
            proj_a1 = proj_a0 + len_a
            perp_a = a0.x_mm * nx + a0.y_mm * ny

            for j in range(len(neighbors)):
                idx_b, b0, b1, len_b, ang_b, _bx, _by = neighbors[j]
                if idx_b == idx_a:
                    continue
                diff_ang = abs(ang_a - ang_b)
                if min(diff_ang, 180.0 - diff_ang) > angle_tol_deg:
                    continue
                perp_b = b0.x_mm * nx + b0.y_mm * ny
                sep = abs(perp_b - perp_a)
                if not (min_cavity_mm <= sep <= max_cavity_mm):
                    continue
                pb0 = b0.x_mm * ux + b0.y_mm * uy
                pb1 = b1.x_mm * ux + b1.y_mm * uy
                lo_b, hi_b = (pb0, pb1) if pb0 <= pb1 else (pb1, pb0)
                overlap = min(proj_a1, hi_b) - max(proj_a0, lo_b)
                min_req_ov = min(len_a, len_b) * 0.35
                if overlap >= max(250.0, min_req_ov):
                    matched_indices.add(idx_a)
                    matched_indices.add(idx_b)

    return [segments[i] for i in sorted(matched_indices)]


def detect_loose_door_swings(
    arcs: list[tuple[Point2D, float, float, float]],
    lines: list[tuple[Point2D, Point2D]],
    *,
    min_width_mm: float = 550.0,
    max_width_mm: float = 1250.0,
) -> list[DoorOpening]:
    """Detect loose unblocked architectural doors from quarter-circle ARC + radial LINE pairs."""
    doors: list[DoorOpening] = []
    seen_hinges: list[tuple[float, float]] = []

    for center, radius, start_ang, end_ang in arcs:
        if not (min_width_mm <= radius <= max_width_mm):
            continue
        sweep = (end_ang - start_ang) % 360.0
        if not (72.0 <= sweep <= 108.0):
            continue
        # Deduplicate double-line door swing arcs sharing the same hinge
        if any(
            math.hypot(center.x_mm - hx, center.y_mm - hy) < 120.0 for hx, hy in seen_hinges
        ):
            continue

        # Check if a matching radial leaf line originates near the arc center with ~radius length
        has_leaf = False
        leaf_ang = start_ang
        for p0, p1 in lines:
            d0 = math.hypot(p0.x_mm - center.x_mm, p0.y_mm - center.y_mm)
            d1 = math.hypot(p1.x_mm - center.x_mm, p1.y_mm - center.y_mm)
            seg_len = math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm)
            if abs(seg_len - radius) > radius * 0.22:
                continue
            if d0 <= 120.0:
                has_leaf = True
                leaf_ang = math.degrees(math.atan2(p1.y_mm - p0.y_mm, p1.x_mm - p0.x_mm)) % 360.0
                break
            if d1 <= 120.0:
                has_leaf = True
                leaf_ang = math.degrees(math.atan2(p0.y_mm - p1.y_mm, p0.x_mm - p1.x_mm)) % 360.0
                break

        if not has_leaf:
            continue

        # Determine closed-wall angle & swing direction from leaf vs arc endpoints
        d_start = abs(((leaf_ang - start_ang + 180.0) % 360.0) - 180.0)
        d_end = abs(((leaf_ang - end_ang + 180.0) % 360.0) - 180.0)
        if d_start <= d_end:
            closed_ang = end_ang % 360.0
            swing_dir: Literal["ccw", "cw"] = "cw"
        else:
            closed_ang = start_ang % 360.0
            swing_dir = "ccw"

        seen_hinges.append((center.x_mm, center.y_mm))
        doors.append(
            DoorOpening(
                id=f"D-LOOSE-{len(doors) + 1}",
                room_id="UNASSIGNED",
                hinge=center,
                width_mm=round(radius, 1),
                angle_deg=round(closed_ang, 1),
                swing_dir=swing_dir,
            )
        )
    return doors


def score_room_detection_confidence(
    poly: Polygon,
    *,
    has_matched_text_label: bool,
    from_explicit_room_layer: bool,
    from_pdf_vector: bool = False,
) -> Literal["high", "medium", "low"]:
    """Score geometric + semantic confidence of a recovered room polygon."""
    area_m2 = poly.area / 1_000_000.0
    perim_m = max(poly.length / 1000.0, 0.1)
    # Isoperimetric quotient (circle=1, square=0.785, skinny sliver < 0.25)
    compactness = (4.0 * math.pi * area_m2) / (perim_m * perim_m)

    if from_explicit_room_layer and has_matched_text_label and compactness >= 0.30:
        return "high"
    if has_matched_text_label and 1.2 <= area_m2 <= 180.0 and compactness >= 0.28:
        return "high" if not from_pdf_vector else "medium"
    if has_matched_text_label and area_m2 >= 1.0:
        return "medium"
    if compactness >= 0.35 and 2.0 <= area_m2 <= 80.0:
        return "medium"
    return "low"


def bridge_nearby_wall_endpoints(
    wall_lines: list[tuple[Point2D, Point2D]],
    *,
    min_gap_mm: float = 10.0,
    max_gap_mm: float = 120.0,
) -> list[tuple[Point2D, Point2D]]:
    """Add tiny bridge segments closing collinear or near-corner CAD drafting gaps (10–120 mm)."""
    endpoints: list[tuple[float, float, int]] = []
    for idx, (p0, p1) in enumerate(wall_lines):
        endpoints.append((p0.x_mm, p0.y_mm, idx))
        endpoints.append((p1.x_mm, p1.y_mm, idx))

    bridges: list[tuple[Point2D, Point2D]] = []
    if len(endpoints) > 2500:
        return bridges

    # Grid spatial hash with cell size = max_gap_mm for O(N) gap bridging
    cell = max(max_gap_mm, 20.0)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x, y, _seg_i) in enumerate(endpoints):
        gx, gy = int(math.floor(x / cell)), int(math.floor(y / cell))
        grid.setdefault((gx, gy), []).append(i)

    seen_pairs: set[tuple[int, int]] = set()
    for i, (x0, y0, seg_i) in enumerate(endpoints):
        gx, gy = int(math.floor(x0 / cell)), int(math.floor(y0 / cell))
        best_j = -1
        best_d = max_gap_mm + 1.0
        for dx_c in (-1, 0, 1):
            for dy_c in (-1, 0, 1):
                for j in grid.get((gx + dx_c, gy + dy_c), ()):
                    if j <= i:
                        continue
                    x1, y1, seg_j = endpoints[j]
                    if seg_j == seg_i:
                        continue
                    d = math.hypot(x1 - x0, y1 - y0)
                    if min_gap_mm <= d < best_d:
                        best_d = d
                        best_j = j
        if best_j >= 0 and (i, best_j) not in seen_pairs:
            seen_pairs.add((i, best_j))
            x1, y1, _ = endpoints[best_j]
            bridges.append((Point2D(x_mm=x0, y_mm=y0), Point2D(x_mm=x1, y_mm=y1)))
    return bridges


def count_wall_intersections_metric(lines: list[LineString]) -> int:
    """Helper count of structural wall line segments (utility for diagnostics)."""
    return len(lines)
