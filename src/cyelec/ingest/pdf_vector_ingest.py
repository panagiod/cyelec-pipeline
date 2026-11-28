"""Direct CAD-exported vector architectural PDF ingestion (MIT-licensed pdfplumber).

Extracts real floor-plan wall geometry, door/window openings, architectural scale (1:50 /
1:100 / 1:200), and Greek/English room text coordinates directly from vector PDF plans
exported from AutoCAD/Revit/ArchiCAD — with ZERO manual redrawing and strict fail-fast
rejection (`ValueError`) on scanned bitmap/raster PDFs.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from pathlib import Path
from typing import Any, Literal

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from cyelec.ingest.messy_cad_recovery import (
    bridge_nearby_wall_endpoints,
    extract_parallel_double_wall_lines,
    score_room_detection_confidence,
    strip_mtext_formatting,
)
from cyelec.ingest.room_classifier import classify_room_label, clean_room_label
from cyelec.models.building import (
    BuildingModel,
    BuildingType,
    DistributionBoardAnchor,
    Point2D,
    Room,
    RoomType,
    WallSegment,
)
from cyelec.models.electrical import ReviewFlag, ReviewSeverity
from cyelec.standards_loader import StandardsBundle, load_standards, normalize_label

_SCALE_RE = re.compile(r"\b1\s*:\s*(20|25|50|75|100|125|150|200|250|500)\b")
_CID_RE = re.compile(r"\(cid:\d+\)")
_NUMERIC_OR_ELEV_RE = re.compile(r"^[±+\-.,0-9/\s%()°m²]+$", re.IGNORECASE)

_PLAN_TITLE_TOKENS = ("κατοψη", "κάτοψη", "floor plan", "ground floor", "first floor", "layout")
_ELEV_NOISE_TOKENS = (
    "προσοψη",
    "πρόσοψη",
    "οψη",
    "όψη",
    "τομη",
    "τομή",
    "κλιμακα",
    "κλίμακα",
    "scale",
    "παραρτημα",
    "παράρτημα",
    "κατοψη",
    "κάτοψη",
    "ασκηση",
    "άσκηση",
    "σχημα",
    "σχήμα",
    "φυλλο",
    "φύλλο",
    "εξετασεις",
    "εξετάσεις",
    "μοναδες",
    "μονάδες",
    "αρχιτεκτονικο",
    "αρχιτεκτονικό",
    "σχεδιο",
    "σχέδιο",
    "σημειωσεις",
    "σημειώσεις",
)
_ROOM_HINT_STEMS = (
    "κουζιν",
    "υπνοδωμ",
    "καθιστ",
    "τραπεζ",
    "μπανι",
    "λουτρ",
    "βεραντ",
    "διαδρομ",
    "αποθηκ",
    "πλυστ",
    "χωλ",
    "εισοδ",
    "σαλον",
    "γραφε",
    "wc",
    "bedroom",
    "kitchen",
    "living",
    "dining",
    "bath",
    "corridor",
    "hall",
    "veranda",
    "balcony",
    "office",
    "utility",
    "storage",
    "toilet",
    "ensuite",
)


def _clean_pdf_word(raw: str) -> str:
    no_cid = _CID_RE.sub("", raw)
    return strip_mtext_formatting(no_cid).strip(" ,;:")


def _is_room_hint_word(word: str) -> bool:
    norm = normalize_label(word)
    if len(norm) < 2:
        return False
    return any(stem in norm for stem in _ROOM_HINT_STEMS)


def _is_noise_word(word: str) -> bool:
    if not word or len(word) < 2:
        return True
    if _NUMERIC_OR_ELEV_RE.match(word):
        return True
    norm = normalize_label(word)
    if not norm or len(norm) < 2:
        return True
    return any(tok in norm for tok in _ELEV_NOISE_TOKENS)


def _extract_page_segments_pt(
    page: Any,
) -> list[tuple[tuple[float, float], tuple[float, float], float]]:
    """Extract all vector segments ((x0, y0), (x1, y1), linewidth_pt) from a pdfplumber page."""
    w_pg = float(page.width)
    h_pg = float(page.height)
    segs: list[tuple[tuple[float, float], tuple[float, float], float]] = []

    def _add(x0: float, y0: float, x1: float, y1: float, lw: float) -> None:
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len < 1.5:
            return
        # Skip outer sheet border frames spanning >86% of full page edge
        if seg_len > 0.86 * max(w_pg, h_pg):
            return
        segs.append(((x0, y0), (x1, y1), lw))

    for ln in page.lines or []:
        lw = float(ln.get("linewidth") or 0.0)
        _add(float(ln["x0"]), float(ln["top"]), float(ln["x1"]), float(ln["bottom"]), lw)

    for rc in page.rects or []:
        if not rc.get("stroke", True) and not rc.get("fill", False):
            continue
        lw = float(rc.get("linewidth") or 0.0)
        x0, y0 = float(rc["x0"]), float(rc["top"])
        x1, y1 = float(rc["x1"]), float(rc["bottom"])
        if (x1 - x0) > 0.86 * w_pg or (y1 - y0) > 0.86 * h_pg:
            continue
        _add(x0, y0, x1, y0, lw)
        _add(x1, y0, x1, y1, lw)
        _add(x1, y1, x0, y1, lw)
        _add(x0, y1, x0, y0, lw)

    for cv in page.curves or []:
        pts = cv.get("pts") or []
        if len(pts) < 2 or len(pts) > 64:
            continue
        lw = float(cv.get("linewidth") or 0.0)
        for a, b in itertools.pairwise(pts):
            _add(float(a[0]), float(a[1]), float(b[0]), float(b[1]), lw)

    return segs


def _select_best_floorplan_page(pdf: Any) -> tuple[int, Any, list[dict[str, Any]], int]:
    """Score every PDF page and pick the architectural floor-plan sheet."""
    best_idx = -1
    best_score = -10**9
    best_words: list[dict[str, Any]] = []
    max_vec_segs = 0

    for idx, pg in enumerate(pdf.pages):
        n_vecs = len(pg.lines or []) + 4 * len(pg.rects or []) + len(pg.curves or [])
        max_vec_segs = max(max_vec_segs, n_vecs)
        if n_vecs < 25:
            continue

        words = pg.extract_words(keep_blank_chars=False, use_text_flow=False) or []
        cleaned = [_clean_pdf_word(str(w.get("text", ""))) for w in words]
        norm_all = " ".join(normalize_label(t) for t in cleaned if t)

        room_hits = sum(1 for t in cleaned if _is_room_hint_word(t))
        uniq_room_stems = len(
            {stem for t in cleaned for stem in _ROOM_HINT_STEMS if stem in normalize_label(t)}
        )
        has_plan_title = any(tok in norm_all for tok in _PLAN_TITLE_TOKENS)
        thick_cnt = sum(
            1 for ln in (pg.lines or []) if float(ln.get("linewidth") or 0.0) >= 0.35
        )

        score = (
            uniq_room_stems * 35
            + room_hits * 12
            + (25 if has_plan_title else 0)
            + (20 if thick_cnt >= 40 else 0)
            + min(30, n_vecs // 20)
        )
        # Penalize text-heavy exam instruction / specification pages
        if len(words) > 150 and uniq_room_stems < 3:
            score -= 60
        if score > best_score:
            best_score = score
            best_idx = idx
            best_words = words

    if best_idx < 0 or max_vec_segs < 25:
        raise ValueError(
            "Uploaded PDF appears to be a scanned raster image or contains insufficient "
            "CAD vector linework (<25 vector segments). Export a vector PDF directly from "
            "AutoCAD/Revit/ArchiCAD or upload a .dxf / .dwg file."
        )
    return best_idx, pdf.pages[best_idx], best_words, max_vec_segs


def _detect_pdf_scale(
    words: list[dict[str, Any]],
) -> tuple[int, bool]:
    """Extract architectural drawing scale `1:N` (prioritizing scale nearest ΚΑΤΟΨΗ/PLAN)."""
    # Find floor-plan anchor word positions if present
    plan_pts: list[tuple[float, float]] = []
    scale_cands: list[tuple[int, float, float]] = []

    for w in words:
        txt = _clean_pdf_word(str(w.get("text", "")))
        cx = (float(w["x0"]) + float(w["x1"])) * 0.5
        cy = (float(w["top"]) + float(w["bottom"])) * 0.5
        norm = normalize_label(txt)
        if any(tok in norm for tok in ("κατοψη", "floor", "plan", "κλιμακα", "scale")):
            plan_pts.append((cx, cy))
        m = _SCALE_RE.search(txt)
        if m:
            scale_cands.append((int(m.group(1)), cx, cy))

    if not scale_cands:
        # Also search joined line text in case "1:" and "100" split across words
        joined = " ".join(_clean_pdf_word(str(w.get("text", ""))) for w in words)
        m2 = _SCALE_RE.search(joined)
        if m2:
            return int(m2.group(1)), True
        return 100, False

    if plan_pts:
        # Prefer scale ratio closest to ΚΑΤΟΨΗ / κλίμακα / SCALE text
        best_n = min(
            scale_cands,
            key=lambda sc: min(math.hypot(sc[1] - px, sc[2] - py) for px, py in plan_pts),
        )[0]
        return best_n, True

    # Prefer standard floorplan scales 1:100 or 1:50 over 1:20 detail scales
    for pref in (100, 50, 75, 200):
        if any(sc[0] == pref for sc in scale_cands):
            return pref, True
    return scale_cands[0][0], True


def _merge_room_words(
    words: list[dict[str, Any]],
    page_h_pt: float,
    mm_per_pt: float,
) -> list[tuple[str, Point2D, bool]]:
    """Merge multi-word horizontal & vertically stacked architectural room labels."""
    items: list[dict[str, Any]] = []
    for w in words:
        txt = _clean_pdf_word(str(w.get("text", "")))
        if _is_noise_word(txt):
            continue
        x0, x1 = float(w["x0"]), float(w["x1"])
        top, bot = float(w["top"]), float(w["bottom"])
        items.append(
            {
                "text": txt,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bot": bot,
                "cx": (x0 + x1) * 0.5,
                "cy": (top + bot) * 0.5,
            }
        )

    # Sort top-to-bottom, left-to-right and merge same-line adjacent words first
    items.sort(key=lambda d: (round(d["cy"] / 4.0), d["x0"]))
    merged_h: list[dict[str, Any]] = []
    for it in items:
        if merged_h:
            prev = merged_h[-1]
            same_line = abs(it["cy"] - prev["cy"]) <= 6.5
            close_x = -2.0 <= (it["x0"] - prev["x1"]) <= 18.0
            if same_line and close_x:
                prev["text"] = f"{prev['text']} {it['text']}"
                prev["x1"] = max(prev["x1"], it["x1"])
                prev["cx"] = (prev["x0"] + prev["x1"]) * 0.5
                prev["bot"] = max(prev["bot"], it["bot"])
                continue
        merged_h.append(dict(it))

    # Merge vertically stacked 2-line modifier + noun pairs (e.g. 'καλυμμένη' over 'βεράντα')
    used = [False] * len(merged_h)
    final_labels: list[tuple[str, Point2D, bool]] = []
    for i, a in enumerate(merged_h):
        if used[i]:
            continue
        used[i] = True
        txt = a["text"]
        cx_pt, cy_pt = a["cx"], a["cy"]
        for j in range(i + 1, len(merged_h)):
            if used[j]:
                continue
            b = merged_h[j]
            dy = b["top"] - a["bot"]
            x_overlap = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            close_cx = abs(a["cx"] - b["cx"]) <= 28.0
            if (
                0.0 <= dy <= 14.0
                and (x_overlap > 0.0 or close_cx)
                and _is_room_hint_word(b["text"])
                and not _is_room_hint_word(a["text"])
            ):
                txt = f"{a['text']} {b['text']}"
                cx_pt = (a["cx"] + b["cx"]) * 0.5
                cy_pt = (a["cy"] + b["cy"]) * 0.5
                used[j] = True
                break

        pt_mm = Point2D(
            x_mm=round(cx_pt * mm_per_pt, 1),
            y_mm=round((page_h_pt - cy_pt) * mm_per_pt, 1),
        )
        final_labels.append((txt, pt_mm, _is_room_hint_word(txt)))

    return final_labels


def _extract_cavities_from_lines(
    lines: list[LineString],
    *,
    buffer_mm: float,
    subtract_thin_mm: float = 70.0,
) -> list[Polygon]:
    """Morphologically seal double walls / doorway gaps and recover interior room cavities."""
    if not lines:
        return []
    wall_band = unary_union([ln.buffer(buffer_mm, cap_style=2, join_style=2) for ln in lines])
    wall_thin = (
        unary_union([ln.buffer(subtract_thin_mm, cap_style=2, join_style=2) for ln in lines])
        if subtract_thin_mm > 0.0
        else None
    )
    geoms = (
        list(wall_band.geoms)
        if isinstance(wall_band, MultiPolygon)
        else ([wall_band] if isinstance(wall_band, Polygon) else [])
    )
    cavities: list[Polygon] = []
    for g in geoms:
        for interior in g.interiors:
            hole = Polygon(interior)
            if hole.area < 150_000.0:
                continue
            exp = hole.buffer(max(20.0, buffer_mm - 20.0), quad_segs=2, join_style=2)
            if wall_thin is not None:
                exp = exp.difference(wall_thin)
                if isinstance(exp, MultiPolygon):
                    exp = max(exp.geoms, key=lambda p: p.area)
            if isinstance(exp, Polygon) and exp.is_valid:
                simp = exp.simplify(35.0, preserve_topology=True)
                if isinstance(simp, Polygon) and simp.is_valid:
                    area_m2 = simp.area / 1_000_000.0
                    perim_m = max(simp.length / 1000.0, 0.1)
                    compact = (4.0 * math.pi * area_m2) / (perim_m * perim_m)
                    if 1.2 <= area_m2 <= 250.0 and compact >= 0.14:
                        cavities.append(simp)
    return cavities


def ingest_vector_pdf(
    pdf_path: Path,
    *,
    standards: StandardsBundle | None = None,
    standards_dir: Path | None = None,
    project_name: str | None = None,
    building_type: BuildingType | None = None,
    pv_capacity_kva: float = 0.0,
) -> tuple[BuildingModel, list[ReviewFlag]]:
    """Ingest a CAD-exported vector architectural PDF directly into a BuildingModel."""
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    import pdfplumber

    std = standards or load_standards(standards_dir)
    sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    flags: list[ReviewFlag] = []

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            page_idx, page, words, _ = _select_best_floorplan_page(pdf)
            page_h_pt = float(page.height)
            raw_segs_pt = _extract_page_segments_pt(page)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"Uploaded PDF '{pdf_path.name}' is corrupted or unreadable: {exc}"
        ) from exc

    if len(raw_segs_pt) < 20:
        raise ValueError(
            "Uploaded PDF does not contain sufficient vector architectural geometry "
            "to reconstruct rooms. Upload a CAD-exported vector PDF or .dxf / .dwg file."
        )

    scale_n, scale_detected = _detect_pdf_scale(words)
    mm_per_pt = (25.4 / 72.0) * float(scale_n)

    if not scale_detected:
        flags.append(
            ReviewFlag(
                code="PDF_SCALE_ASSUMED",
                severity=ReviewSeverity.WARNING,
                category="geometry",
                message=(
                    f"No explicit scale annotation (e.g. 1:50 / 1:100) found on PDF page "
                    f"{page_idx + 1}; assumed 1:{scale_n}. Verify room dimensions before filing."
                ),
                requires_etek_signoff=True,
            )
        )

    # Convert all segments to architectural mm (Y flipped so North is up)
    all_segs_mm: list[tuple[Point2D, Point2D, float]] = []
    for (x0, y0), (x1, y1), lw in raw_segs_pt:
        p0 = Point2D(x_mm=round(x0 * mm_per_pt, 1), y_mm=round((page_h_pt - y0) * mm_per_pt, 1))
        p1 = Point2D(x_mm=round(x1 * mm_per_pt, 1), y_mm=round((page_h_pt - y1) * mm_per_pt, 1))
        all_segs_mm.append((p0, p1, lw))

    # Extract merged Greek/English room text labels
    labels = _merge_room_words(words, page_h_pt, mm_per_pt)
    room_hint_pts = [Point(pt.x_mm, pt.y_mm) for _txt, pt, is_hint in labels if is_hint]

    # Identify structural walls via CAD pen-weight separation (>= 0.35 pt) or double-wall pairing
    thick_segs = [(p0, p1) for p0, p1, lw in all_segs_mm if lw >= 0.35]
    if len(thick_segs) >= 30:
        struct_segs = thick_segs
        wall_method = "CAD pen-weight (>=0.35 pt)"
    else:
        plain_segs = [(p0, p1) for p0, p1, _lw in all_segs_mm]
        paired = extract_parallel_double_wall_lines(plain_segs)
        struct_segs = paired if len(paired) >= 16 else plain_segs
        wall_method = "parallel double-wall cavity pairing"

    # Isolate the floor-plan spatial island (separating ΚΑΤΟΨΗ from elevations/sections on page!)
    struct_lines = [
        LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)])
        for p0, p1 in struct_segs
        if math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm) >= 60.0
    ]
    if not struct_lines:
        raise ValueError("No structural wall line segments recovered from PDF vector geometry.")

    island_union = unary_union([ln.buffer(750.0, cap_style=2) for ln in struct_lines])
    islands = (
        list(island_union.geoms)
        if isinstance(island_union, MultiPolygon)
        else ([island_union] if isinstance(island_union, Polygon) else [])
    )

    if len(islands) > 1:
        def _island_score(isl: Polygon) -> float:
            hits = sum(1 for pt in room_hint_pts if isl.buffer(400.0).contains(pt))
            return float(hits * 1000.0 + (float(isl.area) / 1_000_000.0))

        plan_island = max(islands, key=_island_score)
        keep_zone = Polygon(plan_island.exterior).buffer(650.0)
        struct_segs = [
            (p0, p1)
            for p0, p1 in struct_segs
            if keep_zone.intersects(LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)]))
        ]
        struct_lines = [
            LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)])
            for p0, p1 in struct_segs
            if math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm) >= 60.0
        ]
        # Keep only labels inside the floorplan island
        labels = [
            (txt, pt, hint)
            for txt, pt, hint in labels
            if keep_zone.contains(Point(pt.x_mm, pt.y_mm))
        ]

    # Include thin structural window/door sill lines bridging between wall endpoints
    wall_union_thin = unary_union([ln.buffer(160.0) for ln in struct_lines])
    bridging_thin: list[LineString] = []
    for p0, p1, lw in all_segs_mm:
        if lw >= 0.35:
            continue
        seg_len = math.hypot(p1.x_mm - p0.x_mm, p1.y_mm - p0.y_mm)
        if not (550.0 <= seg_len <= 2800.0):
            continue
        pt_a = Point(p0.x_mm, p0.y_mm)
        pt_b = Point(p1.x_mm, p1.y_mm)
        if wall_union_thin.contains(pt_a) and wall_union_thin.contains(pt_b):
            bridging_thin.append(LineString([(p0.x_mm, p0.y_mm), (p1.x_mm, p1.y_mm)]))

    # Also close micro drafting gaps (10–150 mm) cleanly
    gap_bridges = bridge_nearby_wall_endpoints(struct_segs, min_gap_mm=10.0, max_gap_mm=150.0)
    gap_lines = [LineString([(a.x_mm, a.y_mm), (b.x_mm, b.y_mm)]) for a, b in gap_bridges]

    combined_lines = struct_lines + bridging_thin + gap_lines

    # Extract rooms: test tight wall-cavity closing (160mm) vs doorway-gap closing (500mm)
    tight_polys = _extract_cavities_from_lines(
        combined_lines, buffer_mm=160.0, subtract_thin_mm=65.0
    )
    bridged_polys = _extract_cavities_from_lines(
        combined_lines, buffer_mm=480.0, subtract_thin_mm=75.0
    )

    def _eval_polys(polys: list[Polygon]) -> tuple[int, float]:
        if not polys:
            return (0, 0.0)
        matched_hints = sum(
            1 for pt in room_hint_pts if any(p.buffer(150.0).contains(pt) for p in polys)
        )
        return (matched_hints * 10 + len(polys), sum(p.area for p in polys))

    raw_polys = (
        bridged_polys
        if _eval_polys(bridged_polys) >= _eval_polys(tight_polys)
        else tight_polys
    )
    if not raw_polys:
        raw_polys = tight_polys or bridged_polys

    if not raw_polys:
        raise ValueError(
            f"Vector PDF page {page_idx + 1} parsed {len(struct_segs)} wall segments "
            f"(scale 1:{scale_n}), but no enclosed architectural rooms (>=1.2 m²) could be "
            "polygonized. Verify the PDF contains closed floor-plan wall geometry."
        )

    # Normalize coordinates so building bottom-left sits at (0, 0) for tidy downstream drafting
    all_x = [c[0] for p in raw_polys for c in p.exterior.coords]
    all_y = [c[1] for p in raw_polys for c in p.exterior.coords]
    ox, oy = min(all_x), min(all_y)

    def _shift_poly(poly: Polygon) -> Polygon:
        return Polygon([(x - ox, y - oy) for x, y in list(poly.exterior.coords)[:-1]])

    shifted_polys = [_shift_poly(p) for p in raw_polys]
    shifted_labels = [
        (txt, Point2D(x_mm=round(pt.x_mm - ox, 1), y_mm=round(pt.y_mm - oy, 1)), hint)
        for txt, pt, hint in labels
    ]

    # Match labels to room polygons (join multiple open-plan zone labels with " / ")
    poly_labels: dict[int, list[str]] = {i: [] for i in range(len(shifted_polys))}
    for txt, pt, is_hint in shifted_labels:
        if not is_hint:
            continue
        sp = Point(pt.x_mm, pt.y_mm)
        containing = [i for i, p in enumerate(shifted_polys) if p.contains(sp)]
        if containing:
            idx_best = min(containing, key=lambda i: shifted_polys[i].area)
            if txt not in poly_labels[idx_best]:
                poly_labels[idx_best].append(txt)
        else:
            dists = [(float(p.distance(sp)), i) for i, p in enumerate(shifted_polys)]
            min_d, idx_near = min(dists)
            if min_d <= 850.0 and txt not in poly_labels[idx_near]:
                poly_labels[idx_near].append(txt)

    # Build canonical Room objects with honest per-room detection_confidence
    rooms: list[Room] = []
    low_conf_rooms: list[str] = []
    for idx, poly in enumerate(shifted_polys, start=1):
        coords = list(poly.exterior.coords)[:-1]
        if len(coords) < 3:
            continue
        pts_mm = [Point2D(x_mm=round(x, 1), y_mm=round(y, 1)) for x, y in coords]
        area_m2 = poly.area / 1_000_000.0
        matched = poly_labels.get(idx - 1, [])
        if matched:
            raw_lbl = clean_room_label(" / ".join(matched))
            norm_type = classify_room_label(raw_lbl, std.room_templates).normalized_type
            has_text = True
        else:
            # Infer unlabelled compact utility/wet/corridor cavity conservatively
            bx0, by0, bx1, by1 = poly.bounds
            aspect = max(bx1 - bx0, by1 - by0) / max(min(bx1 - bx0, by1 - by0), 1.0)
            if area_m2 <= 4.5 and aspect >= 1.9:
                raw_lbl = f"Corridor (Inferred {idx})"
                norm_type = RoomType.CORRIDOR
            elif 2.5 <= area_m2 <= 8.0:
                raw_lbl = f"Bathroom / Wet Room (Inferred {idx})"
                norm_type = RoomType.BATHROOM
            elif area_m2 >= 9.0:
                raw_lbl = f"Room {idx} (Unlabelled)"
                norm_type = RoomType.BEDROOM
            else:
                raw_lbl = f"Storage / Utility (Inferred {idx})"
                norm_type = RoomType.STORAGE
            has_text = False

        conf: Literal["high", "medium", "low"] = score_room_detection_confidence(
            poly,
            has_matched_text_label=has_text,
            from_explicit_room_layer=False,
            from_pdf_vector=True,
        )
        if conf == "low":
            low_conf_rooms.append(raw_lbl)

        rt = std.room_templates.rooms[norm_type]
        rooms.append(
            Room(
                id=f"R{idx}",
                raw_label=raw_lbl,
                normalized_type=norm_type,
                polygon_mm=pts_mm,
                area_m2=round(area_m2, 2),
                ceiling_height_mm=2900.0,
                is_wet_room=rt.is_wet_room,
                is_outdoor=rt.is_outdoor,
                ambient_temp_c=(
                    std.eac_rules.ambient_defaults_c.roof_outdoor_c if rt.is_outdoor else None
                ),
                floor="GF",
                detection_confidence=conf,
            )
        )

    if not rooms:
        raise ValueError("Vector PDF ingestion recovered zero valid rooms after polygon cleanup.")

    # Derive crisp exterior & interior WallSegments from unified room boundaries
    outer_ring = unary_union(shifted_polys).boundary
    walls: list[WallSegment] = []
    seen_edges: set[tuple[int, int, int, int]] = set()
    for r in rooms:
        rpts = r.polygon_mm
        for i in range(len(rpts)):
            a, b = rpts[i], rpts[(i + 1) % len(rpts)]
            if math.hypot(b.x_mm - a.x_mm, b.y_mm - a.y_mm) < 120.0:
                continue
            k1 = (round(a.x_mm / 50), round(a.y_mm / 50), round(b.x_mm / 50), round(b.y_mm / 50))
            k2 = (k1[2], k1[3], k1[0], k1[1])
            if k1 in seen_edges or k2 in seen_edges:
                continue
            seen_edges.add(k1)
            mid = Point((a.x_mm + b.x_mm) * 0.5, (a.y_mm + b.y_mm) * 0.5)
            is_ext = float(outer_ring.distance(mid)) < 90.0
            walls.append(
                WallSegment(id=f"W{len(walls) + 1}", start=a, end=b, is_exterior=is_ext)
            )

    # Place DB1 in corridor / central circulation room (or largest room perimeter)
    corridor = next((r for r in rooms if r.normalized_type == RoomType.CORRIDOR), None)
    db_room = corridor or max(rooms, key=lambda r: r.area_m2)
    db_pt = db_room.centroid_mm()

    # Infer building type
    if building_type is not None:
        inferred_bt = building_type
    else:
        tot_area = sum(r.area_m2 for r in rooms)
        inferred_bt = (
            BuildingType.INDEPENDENT_HOUSE if tot_area >= 90.0 else BuildingType.APARTMENT_2BED
        )

    high_or_med = sum(1 for r in rooms if r.detection_confidence in ("high", "medium"))
    flags.append(
        ReviewFlag(
            code="PDF_VECTOR_INGESTION_SUMMARY",
            severity=ReviewSeverity.WARNING if low_conf_rooms else ReviewSeverity.INFO,
            category="geometry",
            message=(
                f"Direct vector PDF ingestion (page {page_idx + 1}, scale 1:{scale_n}, "
                f"{wall_method}): recovered {len(rooms)} room(s) "
                f"({sum(r.area_m2 for r in rooms):.1f} m² total; "
                f"{high_or_med}/{len(rooms)} matched to CAD text labels). "
                + (
                    f"Low-confidence unlabelled cavities requiring ETEK check: "
                    f"{', '.join(low_conf_rooms[:4])}."
                    if low_conf_rooms
                    else "Verify recovered room boundaries & door openings on E-101/E-102."
                )
            ),
            requires_etek_signoff=True,
        )
    )

    proj = project_name or pdf_path.stem.replace("_", " ").title()
    model = BuildingModel(
        project_name=proj,
        building_type=inferred_bt,
        source_file=str(pdf_path),
        source_sha256=sha256,
        total_floor_area_m2=round(sum(r.area_m2 for r in rooms), 2),
        rooms=rooms,
        walls=walls,
        doors=[],
        windows=[],
        db_anchor=DistributionBoardAnchor(id="DB1", location_mm=db_pt),
        pv_capacity_kva=pv_capacity_kva,
    )
    return model, flags
