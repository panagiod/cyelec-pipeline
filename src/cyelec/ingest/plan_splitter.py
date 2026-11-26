"""Automatic spatial clustering & bilingual title splitting for multi-plan single-file DXFs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from cyelec.ingest.room_classifier import clean_room_label
from cyelec.models.building import (
    DistributionBoardAnchor,
    Point2D,
    Room,
    RoomType,
    SubBoardSpec,
)
from cyelec.models.electrical import ReviewFlag, ReviewSeverity
from cyelec.standards_loader import normalize_label

# Polygons closer than 1.8 m belong to the same continuous floor plan (bridges double walls,
# shafts, and attached porches while separating side-by-side / stacked plans in Model Space).
CLUSTER_GAP_MM = 1800.0
STRAY_FRAGMENT_MAX_AREA_M2 = 12.0
STRAY_FRAGMENT_MERGE_DIST_MM = 4000.0
MAX_TITLE_DIST_MM = 7000.0
TITLE_AXIS_PAD_MM = 3000.0

# Untitled auto-split thresholds (prevent false splits on detached 1-room garages/sheds)
UNTITLED_MIN_ROOMS = 3
UNTITLED_MIN_AREA_M2 = 25.0
UNTITLED_MIN_AREA_RATIO = 0.25

_HABITABLE_INDOOR_TYPES: frozenset[RoomType] = frozenset(
    {
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN,
        RoomType.KITCHEN_LIVING,
        RoomType.MASTER_BEDROOM,
        RoomType.BEDROOM,
        RoomType.OPEN_OFFICE,
        RoomType.MEETING_ROOM,
        RoomType.RETAIL_FLOOR,
    }
)

_CORE_FLOOR_TYPES: frozenset[RoomType] = _HABITABLE_INDOOR_TYPES | frozenset(
    {
        RoomType.BATHROOM,
        RoomType.ENSUITE,
        RoomType.WC,
        RoomType.CORRIDOR,
        RoomType.UTILITY,
    }
)

_DB_ROOM_PREF_ORDER: tuple[RoomType, ...] = (
    RoomType.PLANT_ROOM,
    RoomType.CORRIDOR,
    RoomType.UTILITY,
    RoomType.STORAGE,
    RoomType.STAIRWELL,
    RoomType.OPEN_OFFICE,
    RoomType.LIVING_ROOM,
    RoomType.KITCHEN_LIVING,
)

_SCALE_SUFFIX_RE = re.compile(
    r"(?:\b(?:scale|κλ\.?|κλιμακα)\s*1\s*[:/]\s*\d+\b|\b1\s*[:/]\s*(?:20|25|50|75|100|200|250|500)\b)",
    flags=re.IGNORECASE,
)

# NOTE: normalize_label() uses Unicode casefold(), which maps Greek final sigma 'ς' -> 'σ'.
# All Greek patterns below therefore use 'σ' consistently.
_NON_FLOOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\broof(?:\s+floor)?\s+plan\b"),
    re.compile(r"^roof(?:\s+(?:level|terrace|layout|framing|deck|plan))?$"),
    re.compile(r"\bterrace\s+plan\b"),
    re.compile(
        r"\b(?:front|rear|back|left|right|north|south|east|west|side|main)?\s*elevations?\b"
    ),
    re.compile(
        r"\b(?:cross|longitudinal|typical|building|wall)?\s*sections?(?:\s+[a-z0-9\-]+)?\b"
    ),
    re.compile(
        r"\b(?:site|plot|location|key|block|master|foundation|footing|framing|"
        r"structural|drainage|plumbing|hvac|reflected\s+ceiling)\s+plan\b"
    ),
    re.compile(r"^(?:typical\s+)?details?$"),
    # Greek (normalized via NFD + casefold -> no accents, final sigma -> σ)
    re.compile(r"\b(?:κατοψη\s+)?δωμα(?:τοσ)?\b"),
    re.compile(r"\b(?:κατοψη\s+)?στεγησ?\b"),
    re.compile(r"^ταρατσα(?:\s+κατοψη)?$"),
    re.compile(
        r"\b(?:προσ|πισω|πλαινη|βορια|νοτια|ανατολικη|δυτικη|κυρια)?\s*οψ(?:η|εισ)\b"
    ),
    re.compile(r"\bτομ(?:η|εσ)(?:\s+[α-ωa-z0-9\-]+)?\b"),
    re.compile(r"\bτοπογραφικο\b"),
    re.compile(r"\bδιαγραμμα\s+καλυψησ\b"),
    re.compile(r"\bθεμελιωση\b"),
    re.compile(r"\bξυλοτυποσ\b"),
)

_FLOOR_PATTERNS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (
        re.compile(
            r"\b(?:sub[\s\-]?basement|basement\s*(?:2|b2)|2nd\s+basement|"
            r"β['’]?\s*υπογειο|2ο\s*υπογειο)\b"
        ),
        "B2",
        -2.0,
    ),
    (
        re.compile(
            r"\b(?:basement(?:\s*(?:1|b1|plan|floor|level))?|lower\s+ground(?:\s+floor)?|"
            r"cellar|υπογειο|κατοψη\s+υπογειου)\b"
        ),
        "B1",
        -1.0,
    ),
    (
        re.compile(
            r"\b(?:ground\s*(?:floor|level|plan)(?:\s+plan)?|"
            r"main\s*(?:floor|level)(?:\s+plan)?|"
            r"level\s*00?(?:\s+plan)?|"
            r"downstairs|lower\s+(?:floor|level)(?:\s+plan)?|"
            r"ισογειο|κατοψη\s+ισογειου)\b"
        ),
        "GF",
        0.0,
    ),
    (
        re.compile(
            r"\b(?:mezzanine(?:\s*(?:floor|level|plan))?|"
            r"μεσοπατωμα|κατοψη\s+μεσοπατωματοσ)\b"
        ),
        "MZ",
        0.5,
    ),
    (
        re.compile(
            r"\b(?:first\s*(?:floor|level)(?:\s+plan)?|1st\s*(?:floor|level)(?:\s+plan)?|"
            r"upper\s*(?:floor|level)(?:\s+plan)?|upstairs|"
            r"α['’]?\s*οροφ(?:οσ|ου)|1οσ\s*οροφ(?:οσ|ου)|πρωτ(?:οσ|ου)\s*οροφ(?:οσ|ου))\b"
        ),
        "1F",
        1.0,
    ),
    (
        re.compile(
            r"\b(?:second\s*(?:floor|level)(?:\s+plan)?|2nd\s*(?:floor|level)(?:\s+plan)?|"
            r"β['’]?\s*οροφ(?:οσ|ου)|2οσ\s*οροφ(?:οσ|ου)|δευτερ(?:οσ|ου)\s*οροφ(?:οσ|ου))\b"
        ),
        "2F",
        2.0,
    ),
    (
        re.compile(
            r"\b(?:third\s*(?:floor|level)(?:\s+plan)?|3rd\s*(?:floor|level)(?:\s+plan)?|"
            r"γ['’]?\s*οροφ(?:οσ|ου)|3οσ\s*οροφ(?:οσ|ου)|τριτ(?:οσ|ου)\s*οροφ(?:οσ|ου))\b"
        ),
        "3F",
        3.0,
    ),
    (
        re.compile(
            r"\b(?:fourth\s*(?:floor|level)(?:\s+plan)?|4th\s*(?:floor|level)(?:\s+plan)?|"
            r"δ['’]?\s*οροφ(?:οσ|ου)|4οσ\s*οροφ(?:οσ|ου))\b"
        ),
        "4F",
        4.0,
    ),
    (
        re.compile(
            r"\b(?:penthouse(?:\s+plan)?|attic(?:\s*(?:floor|plan))?|"
            r"loft(?:\s*(?:floor|plan))?|ρετιρε|σοφιτα)\b"
        ),
        "PH",
        9.0,
    ),
)

_GENERIC_LEVEL_RE = re.compile(r"\b(?:level|floor|storey|story)\s*([1-9])(?:\s+plan)?\b")


@dataclass(frozen=True)
class PlanTitleMatch:
    """Classified CAD sheet or floor-plan title."""

    raw_text: str
    kind: Literal["floor", "non_floor"]
    floor_code: str | None
    sort_rank: float


@dataclass
class _SpatialCluster:
    """Connected component of architectural rooms inside a single Model Space."""

    room_indices: list[int]
    bounds: tuple[float, float, float, float]  # (minx, miny, maxx, maxy)
    area_m2: float
    union_geom: Polygon | object
    title: PlanTitleMatch | None = None


@dataclass
class ClusterSplitResult:
    """Output of automatic single-file multi-plan cluster detection & splitting."""

    rooms: list[Room]
    room_poly_pairs: list[tuple[str, Polygon]]
    sub_boards: list[SubBoardSpec]
    db_anchor: DistributionBoardAnchor | None
    excluded_room_ids: set[str]
    room_id_remap: dict[str, str]
    did_split_floors: bool
    did_exclude_non_floor: bool
    flags: list[ReviewFlag]


def classify_plan_title(raw_text: str) -> PlanTitleMatch | None:
    """Classify a short EN/EL CAD title label as a floor plan or non-habitable sheet."""
    if raw_text.count("\n") > 3 or raw_text.count("\\P") > 3:
        return None
    cleaned = clean_room_label(raw_text)
    if not cleaned or len(cleaned) > 64:
        return None
    stripped = _SCALE_SUFFIX_RE.sub(" ", cleaned)
    norm = normalize_label(stripped).strip(" -–—:/|•·()")
    if not norm or len(norm) > 56:
        return None

    for pat in _NON_FLOOR_PATTERNS:
        if pat.search(norm):
            return PlanTitleMatch(
                raw_text=cleaned,
                kind="non_floor",
                floor_code=None,
                sort_rank=99.0,
            )

    for pat, code, rank in _FLOOR_PATTERNS:
        if pat.search(norm):
            return PlanTitleMatch(
                raw_text=cleaned,
                kind="floor",
                floor_code=code,
                sort_rank=rank,
            )

    m = _GENERIC_LEVEL_RE.search(norm)
    if m:
        lvl = int(m.group(1))
        return PlanTitleMatch(
            raw_text=cleaned,
            kind="floor",
            floor_code=f"L{lvl}",
            sort_rank=float(lvl),
        )

    return None


def _build_clusters(
    rooms: list[Room],
    polys: list[Polygon],
    gap_mm: float = CLUSTER_GAP_MM,
) -> list[_SpatialCluster]:
    """Group room polygons into connected components separated by > gap_mm."""
    n = len(polys)
    adj: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if polys[i].distance(polys[j]) <= gap_mm:
                adj[i].add(j)
                adj[j].add(i)

    seen: set[int] = set()
    clusters: list[_SpatialCluster] = []
    for i in range(n):
        if i in seen:
            continue
        stack = [i]
        comp: list[int] = []
        while stack:
            u = stack.pop()
            if u in seen:
                continue
            seen.add(u)
            comp.append(u)
            stack.extend(adj[u] - seen)
        comp.sort()
        geom = unary_union([polys[idx] for idx in comp])
        area_m2 = sum(rooms[idx].area_m2 for idx in comp)
        clusters.append(
            _SpatialCluster(
                room_indices=comp,
                bounds=geom.bounds,
                area_m2=area_m2,
                union_geom=geom,
            )
        )
    return clusters


def _title_relation_to_cluster(
    pt: Point,
    bounds: tuple[float, float, float, float],
) -> tuple[str, float] | None:
    """Return (side, weighted_cost_mm) if a title point plausibly labels a cluster bbox."""
    minx, miny, maxx, maxy = bounds
    tx, ty = float(pt.x), float(pt.y)
    cx = (minx + maxx) * 0.5
    cy = (miny + maxy) * 0.5

    if minx <= tx <= maxx and miny <= ty <= maxy:
        return ("inside", 0.1 * math.hypot(tx - cx, ty - cy))

    # Below or above cluster (most common in AutoCAD side-by-side & stacked plans)
    if (minx - TITLE_AXIS_PAD_MM) <= tx <= (maxx + TITLE_AXIS_PAD_MM):
        if ty < miny and (miny - ty) <= MAX_TITLE_DIST_MM:
            dist = miny - ty
            return ("below", dist + 0.15 * abs(tx - cx))
        if ty > maxy and (ty - maxy) <= MAX_TITLE_DIST_MM:
            dist = ty - maxy
            return ("above", dist + 0.15 * abs(tx - cx))

    # Left or right of cluster
    if (miny - TITLE_AXIS_PAD_MM) <= ty <= (maxy + TITLE_AXIS_PAD_MM):
        if tx < minx and (minx - tx) <= MAX_TITLE_DIST_MM:
            dist = minx - tx
            return ("left", dist + 0.15 * abs(ty - cy))
        if tx > maxx and (tx - maxx) <= MAX_TITLE_DIST_MM:
            dist = tx - maxx
            return ("right", dist + 0.15 * abs(ty - cy))

    return None


def _match_titles_to_clusters(
    clusters: list[_SpatialCluster],
    title_candidates: list[tuple[PlanTitleMatch, Point]],
) -> dict[int, PlanTitleMatch]:
    """Optimal one-to-one matching of candidate titles to spatial clusters."""
    if not clusters or not title_candidates:
        return {}

    # Build top candidate list per cluster
    cand_options: list[list[tuple[int, str, float]]] = []
    for c in clusters:
        opts: list[tuple[int, str, float]] = []
        for t_idx, (_tm, pt) in enumerate(title_candidates):
            rel = _title_relation_to_cluster(pt, c.bounds)
            if rel is not None:
                opts.append((t_idx, rel[0], rel[1]))
        opts.sort(key=lambda item: item[2])
        cand_options.append(opts[:4])

    n_c = len(clusters)
    if n_c > 8:
        # Deterministic greedy fallback for unusually high sheet counts
        used_t: set[int] = set()
        assigned: dict[int, PlanTitleMatch] = {}
        all_pairs = [
            (cost, c_idx, t_idx)
            for c_idx, opts in enumerate(cand_options)
            for t_idx, _side, cost in opts
        ]
        all_pairs.sort()
        for _cost, c_idx, t_idx in all_pairs:
            if c_idx in assigned or t_idx in used_t:
                continue
            assigned[c_idx] = title_candidates[t_idx][0]
            used_t.add(t_idx)
        return assigned

    # Exact optimal one-to-one search maximizing (matched_count, side_consistency, -total_cost)
    best_score: tuple[int, int, float] = (-1, -1, -1e18)
    best_assign: dict[int, int] = {}

    def _dfs(
        c_idx: int,
        used_titles: set[int],
        cur_assign: dict[int, int],
        cur_sides: list[str],
        cur_cost: float,
    ) -> None:
        nonlocal best_score, best_assign
        # Upper-bound pruning on matched count
        max_possible = len(cur_assign) + (n_c - c_idx)
        if max_possible < best_score[0]:
            return
        if c_idx == n_c:
            matched = len(cur_assign)
            if cur_sides:
                side_counts: dict[str, int] = {}
                for s in cur_sides:
                    side_counts[s] = side_counts.get(s, 0) + 1
                consistency = max(side_counts.values())
            else:
                consistency = 0
            score = (matched, consistency, -cur_cost)
            if score > best_score:
                best_score = score
                best_assign = dict(cur_assign)
            return

        # Try matching cluster c_idx to each valid unused title
        for t_idx, side, cost in cand_options[c_idx]:
            if t_idx in used_titles:
                continue
            used_titles.add(t_idx)
            cur_assign[c_idx] = t_idx
            cur_sides.append(side)
            _dfs(c_idx + 1, used_titles, cur_assign, cur_sides, cur_cost + cost)
            cur_sides.pop()
            del cur_assign[c_idx]
            used_titles.remove(t_idx)

        # Also allow leaving cluster c_idx unmatched
        _dfs(c_idx + 1, used_titles, cur_assign, cur_sides, cur_cost)

    _dfs(0, set(), {}, [], 0.0)
    return {c_idx: title_candidates[t_idx][0] for c_idx, t_idx in best_assign.items()}


def _is_unnamed_or_ancillary(room: Room) -> bool:
    if room.raw_label.lower().startswith("unnamed room"):
        return True
    return room.normalized_type in (RoomType.STORAGE, RoomType.VERANDA)


def _should_exclude_non_floor_cluster(cluster: _SpatialCluster, rooms: list[Room]) -> bool:
    """Confirm a cluster titled as Roof/Elevation/Section is genuinely non-habitable."""
    if cluster.title is None or cluster.title.kind != "non_floor":
        return False
    c_rooms = [rooms[i] for i in cluster.room_indices]
    has_labeled_habitable = any(
        (not r.raw_label.lower().startswith("unnamed room"))
        and r.normalized_type in _HABITABLE_INDOOR_TYPES
        for r in c_rooms
    )
    if not has_labeled_habitable:
        return True
    ancillary_count = sum(1 for r in c_rooms if _is_unnamed_or_ancillary(r))
    return (ancillary_count / max(len(c_rooms), 1)) >= 0.75


def _select_cluster_db_location(
    cluster_rooms: list[Room],
    cluster_polys: list[Polygon],
    db_points: list[tuple[str, Point2D]],
) -> Point2D:
    """Pick an explicit architect DB point inside the floor cluster or a dry indoor centroid."""
    if db_points and cluster_polys:
        union_geom = unary_union(cluster_polys)
        for _lbl, pt in db_points:
            if union_geom.distance(Point(pt.x_mm, pt.y_mm)) <= 1000.0:
                return Point2D(x_mm=round(pt.x_mm, 2), y_mm=round(pt.y_mm, 2))

    dry_indoor = [r for r in cluster_rooms if not r.is_wet_room and not r.is_outdoor]
    for rtype in _DB_ROOM_PREF_ORDER:
        host = next((r for r in dry_indoor if r.normalized_type == rtype), None)
        if host is not None:
            return host.centroid_mm()
    fallback = dry_indoor[0] if dry_indoor else cluster_rooms[0]
    return fallback.centroid_mm()


def _merge_tiny_stray_clusters(
    clusters: list[_SpatialCluster],
    rooms: list[Room],
    polys: list[Polygon],
    title_candidates: list[tuple[PlanTitleMatch, Point]],
) -> list[_SpatialCluster]:
    """Absorb tiny untitled stray fragments (<12 m², 1 room) into nearest major cluster."""
    if len(clusters) <= 1:
        return clusters

    prelim_titles = _match_titles_to_clusters(clusters, title_candidates)
    major_indices = [
        i
        for i, c in enumerate(clusters)
        if c.area_m2 >= STRAY_FRAGMENT_MAX_AREA_M2
        or len(c.room_indices) >= 2
        or i in prelim_titles
    ]
    if not major_indices or len(major_indices) == len(clusters):
        return clusters

    merged_rooms: dict[int, list[int]] = {i: list(clusters[i].room_indices) for i in major_indices}
    unmerged: list[int] = []
    for idx, c in enumerate(clusters):
        if idx in merged_rooms:
            continue
        best_major = min(
            major_indices,
            key=lambda m_idx: float(c.union_geom.distance(clusters[m_idx].union_geom)),  # type: ignore[attr-defined]
        )
        dist = float(c.union_geom.distance(clusters[best_major].union_geom))  # type: ignore[attr-defined]
        if dist <= STRAY_FRAGMENT_MERGE_DIST_MM:
            merged_rooms[best_major].extend(c.room_indices)
        else:
            unmerged.append(idx)

    rebuilt: list[_SpatialCluster] = []
    for m_idx in major_indices:
        idxs = sorted(merged_rooms[m_idx])
        geom = unary_union([polys[i] for i in idxs])
        rebuilt.append(
            _SpatialCluster(
                room_indices=idxs,
                bounds=geom.bounds,
                area_m2=sum(rooms[i].area_m2 for i in idxs),
                union_geom=geom,
            )
        )
    for u_idx in unmerged:
        rebuilt.append(clusters[u_idx])
    return rebuilt


def split_side_by_side_plans(
    *,
    rooms: list[Room],
    room_poly_pairs: list[tuple[str, Polygon]],
    text_labels: list[tuple[str, Point, str]],
    db_points: list[tuple[str, Point2D]],
) -> ClusterSplitResult:
    """Detect side-by-side / stacked multi-floor plans in a single DXF and exclude non-floors."""
    if len(rooms) < 2:
        return ClusterSplitResult(
            rooms=rooms,
            room_poly_pairs=room_poly_pairs,
            sub_boards=[],
            db_anchor=None,
            excluded_room_ids=set(),
            room_id_remap={r.id: r.id for r in rooms},
            did_split_floors=False,
            did_exclude_non_floor=False,
            flags=[],
        )

    polys = [poly for _rid, poly in room_poly_pairs]
    raw_clusters = _build_clusters(rooms, polys)
    if len(raw_clusters) <= 1:
        return ClusterSplitResult(
            rooms=rooms,
            room_poly_pairs=room_poly_pairs,
            sub_boards=[],
            db_anchor=None,
            excluded_room_ids=set(),
            room_id_remap={r.id: r.id for r in rooms},
            did_split_floors=False,
            did_exclude_non_floor=False,
            flags=[],
        )

    # Extract candidate sheet/floor titles (ignore text sitting inside classified habitable rooms)
    title_candidates: list[tuple[PlanTitleMatch, Point]] = []
    for raw_txt, pt, _layer in text_labels:
        tm = classify_plan_title(raw_txt)
        if tm is None:
            continue
        inside_habitable = False
        for r_obj, r_poly in zip(rooms, polys, strict=True):
            if (r_poly.contains(pt) or r_poly.touches(pt)) and (
                not _is_unnamed_or_ancillary(r_obj)
            ):
                inside_habitable = True
                break
        if not inside_habitable:
            title_candidates.append((tm, pt))

    clusters = _merge_tiny_stray_clusters(raw_clusters, rooms, polys, title_candidates)
    if len(clusters) <= 1:
        return ClusterSplitResult(
            rooms=rooms,
            room_poly_pairs=room_poly_pairs,
            sub_boards=[],
            db_anchor=None,
            excluded_room_ids=set(),
            room_id_remap={r.id: r.id for r in rooms},
            did_split_floors=False,
            did_exclude_non_floor=False,
            flags=[],
        )

    matched_titles = _match_titles_to_clusters(clusters, title_candidates)
    for c_idx, tm in matched_titles.items():
        clusters[c_idx].title = tm

    flags: list[ReviewFlag] = []
    excluded_clusters: list[_SpatialCluster] = []
    kept_clusters: list[_SpatialCluster] = []

    for c in clusters:
        if _should_exclude_non_floor_cluster(c, rooms):
            excluded_clusters.append(c)
        else:
            kept_clusters.append(c)

    # Never drop every single cluster in a file
    if not kept_clusters:
        kept_clusters = clusters
        excluded_clusters = []

    excluded_room_ids: set[str] = set()
    for ec in excluded_clusters:
        title_str = ec.title.raw_text if ec.title is not None else "Non-Floor Sheet"
        for r_idx in ec.room_indices:
            excluded_room_ids.add(rooms[r_idx].id)
        flags.append(
            ReviewFlag(
                code="DXF_NON_FLOOR_PLAN_CLUSTER_EXCLUDED",
                severity=ReviewSeverity.INFO,
                category="geometry",
                message=(
                    f"Excluded non-habitable CAD sheet cluster '{title_str}' "
                    f"({len(ec.room_indices)} enclosure(s), {ec.area_m2:.1f} m²) "
                    "from electrical load and layout design."
                ),
            )
        )

    did_exclude = len(excluded_clusters) > 0

    # Decide whether remaining clusters represent genuinely separate floors
    explicit_floor_clusters = [
        c for c in kept_clusters if c.title is not None and c.title.kind == "floor"
    ]
    should_split = False
    if len(kept_clusters) >= 2:
        if len(explicit_floor_clusters) >= 2:
            should_split = True
        else:
            major_untitled = [
                c
                for c in kept_clusters
                if len(c.room_indices) >= UNTITLED_MIN_ROOMS
                and c.area_m2 >= UNTITLED_MIN_AREA_M2
                and any(
                    rooms[i].normalized_type in _CORE_FLOOR_TYPES for i in c.room_indices
                )
            ]
            if len(major_untitled) >= 2:
                areas = [c.area_m2 for c in major_untitled]
                if min(areas) / max(max(areas), 1e-6) >= UNTITLED_MIN_AREA_RATIO:
                    should_split = True

    if not should_split:
        # Either single floor remained (possibly after excluding Roof Plan), or detached shed
        if not did_exclude:
            return ClusterSplitResult(
                rooms=rooms,
                room_poly_pairs=room_poly_pairs,
                sub_boards=[],
                db_anchor=None,
                excluded_room_ids=set(),
                room_id_remap={r.id: r.id for r in rooms},
                did_split_floors=False,
                did_exclude_non_floor=False,
                flags=[],
            )
        kept_indices = sorted(i for c in kept_clusters for i in c.room_indices)
        kept_rooms = [rooms[i] for i in kept_indices]
        kept_pairs = [room_poly_pairs[i] for i in kept_indices]
        return ClusterSplitResult(
            rooms=kept_rooms,
            room_poly_pairs=kept_pairs,
            sub_boards=[],
            db_anchor=None,
            excluded_room_ids=excluded_room_ids,
            room_id_remap={r.id: r.id for r in kept_rooms},
            did_split_floors=False,
            did_exclude_non_floor=True,
            flags=flags,
        )

    # Absorb any minor untitled stray cluster into nearest titled/major floor before finalizing
    major_kept = [
        c
        for c in kept_clusters
        if (c.title is not None and c.title.kind == "floor")
        or (len(c.room_indices) >= UNTITLED_MIN_ROOMS and c.area_m2 >= UNTITLED_MIN_AREA_M2)
    ]
    if len(major_kept) >= 2 and len(major_kept) < len(kept_clusters):
        minor_kept = [c for c in kept_clusters if c not in major_kept]
        for mc in minor_kept:
            nearest = min(
                major_kept,
                key=lambda target: float(mc.union_geom.distance(target.union_geom)),  # type: ignore[attr-defined]
            )
            nearest.room_indices.extend(mc.room_indices)
            nearest.room_indices.sort()
            nearest.union_geom = unary_union([polys[i] for i in nearest.room_indices])
            nearest.bounds = nearest.union_geom.bounds  # type: ignore[attr-defined]
            nearest.area_m2 = sum(rooms[i].area_m2 for i in nearest.room_indices)
        kept_clusters = major_kept

    # Assign canonical floor codes and logical architectural sort ranks
    fallback_codes = ["GF", "1F", "2F", "3F", "4F", "5F", "6F"]
    used_codes: set[str] = {
        c.title.floor_code
        for c in kept_clusters
        if c.title is not None and c.title.floor_code is not None
    }

    # Deterministic reading order for untitled clusters (left-to-right, else bottom-to-top)
    xs_span = max(c.bounds[2] for c in kept_clusters) - min(c.bounds[0] for c in kept_clusters)
    ys_span = max(c.bounds[3] for c in kept_clusters) - min(c.bounds[1] for c in kept_clusters)
    stacked_vertically = ys_span > xs_span * 1.15

    unlabeled = [c for c in kept_clusters if c.title is None or c.title.floor_code is None]
    unlabeled.sort(
        key=lambda c: (
            (c.bounds[1] + c.bounds[3]) * 0.5
            if stacked_vertically
            else (c.bounds[0] + c.bounds[2]) * 0.5
        )
    )
    for uc in unlabeled:
        code = next((fc for fc in fallback_codes if fc not in used_codes), f"F{len(used_codes)}")
        used_codes.add(code)
        rank = float(fallback_codes.index(code)) if code in fallback_codes else 10.0
        uc.title = PlanTitleMatch(
            raw_text=code,
            kind="floor",
            floor_code=code,
            sort_rank=rank,
        )

    # Deduplicate floor codes if two clusters somehow matched the same title
    seen_codes: dict[str, int] = {}
    ordered_entries: list[tuple[float, float, float, str, _SpatialCluster]] = []
    for c in kept_clusters:
        assert c.title is not None and c.title.floor_code is not None
        base_code = c.title.floor_code
        cnt = seen_codes.get(base_code, 0) + 1
        seen_codes[base_code] = cnt
        code = base_code if cnt == 1 else f"{base_code}_{cnt}"
        cx = (c.bounds[0] + c.bounds[2]) * 0.5
        cy = (c.bounds[1] + c.bounds[3]) * 0.5
        ordered_entries.append((c.title.sort_rank, cx, -cy, code, c))

    ordered_entries.sort(key=lambda item: (item[0], item[1], item[2]))

    new_rooms: list[Room] = []
    new_pairs: list[tuple[str, Polygon]] = []
    sub_boards: list[SubBoardSpec] = []
    room_id_remap: dict[str, str] = {}
    floor_summaries: list[str] = []

    for f_idx, (_rank, _cx, _neg_cy, floor_code, cluster) in enumerate(ordered_entries):
        # Deterministic room ordering within each floor (top-to-bottom, left-to-right)
        sorted_r_idxs = sorted(
            cluster.room_indices,
            key=lambda idx: (
                -round(float(polys[idx].centroid.y), 1),
                round(float(polys[idx].centroid.x), 1),
            ),
        )
        floor_rooms: list[Room] = []
        floor_polys: list[Polygon] = []
        for local_idx, r_idx in enumerate(sorted_r_idxs, start=1):
            old_room = rooms[r_idx]
            poly = polys[r_idx]
            new_rid = f"{floor_code}-R{local_idx}"
            room_id_remap[old_room.id] = new_rid
            suffix = f" ({floor_code})"
            new_label = (
                old_room.raw_label
                if old_room.raw_label.endswith(suffix)
                else f"{old_room.raw_label}{suffix}"
            )
            updated_room = old_room.model_copy(
                update={
                    "id": new_rid,
                    "floor": floor_code,
                    "raw_label": new_label,
                }
            )
            floor_rooms.append(updated_room)
            floor_polys.append(poly)
            new_rooms.append(updated_room)
            new_pairs.append((new_rid, poly))

        db_loc = _select_cluster_db_location(floor_rooms, floor_polys, db_points)
        sub_boards.append(
            SubBoardSpec(
                board_id=f"DB-{floor_code}",
                floor=floor_code,
                parent_board_id="MDB",
                location_mm=db_loc,
                submain_length_m=6.0 + 10.0 * f_idx,
                is_three_phase=True,
            )
        )
        floor_summaries.append(
            f"{floor_code} ({len(floor_rooms)} rooms, {cluster.area_m2:.1f} m²)"
        )

    # Primary floor for MDB placement: prefer GF, else first floor in logical order
    primary_sb = next((sb for sb in sub_boards if sb.floor == "GF"), sub_boards[0])
    primary_cluster = next(
        c for _r, _cx, _cy, code, c in ordered_entries if code == primary_sb.floor
    )
    minx, _miny, maxx, _maxy = primary_cluster.bounds
    mdb_x = primary_sb.location_mm.x_mm - 500.0
    if mdb_x < minx + 150.0:
        mdb_x = min(maxx - 150.0, primary_sb.location_mm.x_mm + 500.0)
    mdb_anchor = DistributionBoardAnchor(
        id="MDB",
        location_mm=Point2D(x_mm=round(mdb_x, 2), y_mm=round(primary_sb.location_mm.y_mm, 2)),
    )

    flags.append(
        ReviewFlag(
            code="DXF_SIDE_BY_SIDE_FLOORS_AUTO_SPLIT",
            severity=ReviewSeverity.INFO,
            category="geometry",
            message=(
                f"Automatically detected {len(sub_boards)} separate floor plan clusters inside "
                f"single CAD file ({', '.join(floor_summaries)}) and configured unified "
                f"MDB -> {', '.join(sb.board_id for sb in sub_boards)} distribution hierarchy."
            ),
        )
    )

    return ClusterSplitResult(
        rooms=new_rooms,
        room_poly_pairs=new_pairs,
        sub_boards=sub_boards,
        db_anchor=mdb_anchor,
        excluded_room_ids=excluded_room_ids,
        room_id_remap=room_id_remap,
        did_split_floors=True,
        did_exclude_non_floor=did_exclude,
        flags=flags,
    )
