"""Unit & end-to-end tests for automatic side-by-side / stacked multi-plan CAD splitting."""

from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.filemanagement import readfile as read_dxf

from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.ingest.plan_splitter import classify_plan_title
from cyelec.models.spatial import DeviceType
from cyelec.pipeline import run_phase2_pipeline
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.render.layout_dxf import export_electrical_layout_dxf
from cyelec.render.sld import render_sld_svg


def _add_rect_room(
    msp: object,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    label: str | None = None,
    *,
    room_layer: str = "A-ROOM",
    wall_layer: str = "A-WALL",
    text_layer: str = "A-TEXT",
) -> None:
    msp.add_lwpolyline(  # type: ignore[attr-defined]
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": room_layer},
    )
    msp.add_lwpolyline(  # type: ignore[attr-defined]
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": wall_layer},
    )
    if label:
        msp.add_mtext(label, dxfattribs={"layer": text_layer, "char_height": 180.0}).set_location(  # type: ignore[attr-defined]
            ((x0 + x1) * 0.5, (y0 + y1) * 0.5)
        )


def _add_floor_cluster(
    msp: object,
    ox: float,
    oy: float,
    *,
    rooms: list[tuple[float, float, float, float, str | None]],
    title: str | None = None,
    title_pos: tuple[float, float] | None = None,
    title_layer: str = "0",
) -> None:
    for rx0, ry0, rx1, ry1, lbl in rooms:
        _add_rect_room(msp, ox + rx0, oy + ry0, ox + rx1, oy + ry1, lbl)
    if title is not None and title_pos is not None:
        msp.add_mtext(title, dxfattribs={"layer": title_layer, "char_height": 300.0}).set_location(  # type: ignore[attr-defined]
            (ox + title_pos[0], oy + title_pos[1])
        )


def test_classify_plan_title_bilingual_en_el() -> None:
    """Bilingual EN/EL floor plan & non-floor sheet titles classify deterministically."""
    # Floors (English)
    gf = classify_plan_title("GROUND FLOOR PLAN")
    assert gf is not None and gf.kind == "floor" and gf.floor_code == "GF"

    f1 = classify_plan_title("FIRST FLOOR PLAN\\PSCALE 1:50")
    assert f1 is not None and f1.kind == "floor" and f1.floor_code == "1F"

    f2 = classify_plan_title("2nd Floor Level")
    assert f2 is not None and f2.kind == "floor" and f2.floor_code == "2F"

    b1 = classify_plan_title("BASEMENT PLAN")
    assert b1 is not None and b1.kind == "floor" and b1.floor_code == "B1"

    up = classify_plan_title("Upstairs")
    down = classify_plan_title("Downstairs")
    assert up is not None and up.floor_code == "1F"
    assert down is not None and down.floor_code == "GF"

    # Floors (Greek with tonos & final sigma)
    el_gf = classify_plan_title("ΚΑΤΟΨΗ ΙΣΟΓΕΙΟΥ")
    assert el_gf is not None and el_gf.kind == "floor" and el_gf.floor_code == "GF"

    el_1f = classify_plan_title("Α' ΟΡΟΦΟΣ")
    assert el_1f is not None and el_1f.kind == "floor" and el_1f.floor_code == "1F"

    el_2f = classify_plan_title("Β' Όροφος - Κλίμακα 1:100")
    assert el_2f is not None and el_2f.kind == "floor" and el_2f.floor_code == "2F"

    el_b1 = classify_plan_title("ΥΠΟΓΕΙΟ")
    assert el_b1 is not None and el_b1.kind == "floor" and el_b1.floor_code == "B1"

    el_mz = classify_plan_title("ΜΕΣΟΠΑΤΩΜΑ")
    assert el_mz is not None and el_mz.kind == "floor" and el_mz.floor_code == "MZ"

    # Non-floor sheets (EN + EL)
    for title in (
        "ROOF PLAN",
        "ROOF",
        "FRONT ELEVATION",
        "SECTION A-A",
        "SITE PLAN",
        "ΔΩΜΑ",
        "ΚΑΤΟΨΗ ΔΩΜΑΤΟΣ",
        "ΚΑΤΟΨΗ ΣΤΕΓΗΣ",
        "ΚΥΡΙΑ ΟΨΗ",
        "ΤΟΜΗ Α-Α",
        "ΤΟΠΟΓΡΑΦΙΚΟ",
    ):
        res = classify_plan_title(title)
        assert res is not None and res.kind == "non_floor", f"Expected non_floor for {title!r}"

    # Must ignore room names & long schedule notes
    assert classify_plan_title("WATERPROOF STORAGE") is None
    assert classify_plan_title("MASTER BEDROOM") is None
    assert classify_plan_title("1 Kitchen\n2 Living\n3 Bath\n4 Roof fan\n5 Garage") is None


def test_side_by_side_gf_1f_roof_auto_split_end_to_end(tmp_path: Path) -> None:
    """Single-file GF + 1F + Roof Plan splits to MDB->DB-GF/DB-1F and excludes Roof Plan."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()

    # Cluster 1: Ground Floor (x = 0..8000 mm)
    _add_floor_cluster(
        msp,
        0.0,
        0.0,
        rooms=[
            (0, 0, 4500, 4000, "KITCHEN / LIVING"),
            (4500, 0, 8000, 4000, "BEDROOM"),
            (0, 4000, 4500, 6000, "CORRIDOR"),
            (4500, 4000, 8000, 6000, "BATHROOM"),
        ],
        title="GROUND FLOOR PLAN",
        title_pos=(4000.0, -1200.0),
    )

    # Cluster 2: First Floor (x = 18000..26000 mm — 10 m gap!)
    _add_floor_cluster(
        msp,
        18000.0,
        0.0,
        rooms=[
            (0, 0, 4500, 4000, "MASTER BEDROOM"),
            (4500, 0, 8000, 4000, "BEDROOM 2"),
            (0, 4000, 4500, 6000, "HALLWAY"),
            (4500, 4000, 8000, 6000, "BATHROOM 2"),
        ],
        title="FIRST FLOOR PLAN",
        title_pos=(4000.0, -1200.0),
    )

    # Cluster 3: Roof Plan (x = 36000..44000 mm — unnamed parapet/stair enclosures)
    _add_floor_cluster(
        msp,
        36000.0,
        0.0,
        rooms=[
            (0, 0, 4500, 6000, None),
            (4500, 0, 8000, 6000, None),
        ],
        title="ROOF PLAN",
        title_pos=(4000.0, -1200.0),
    )

    dxf_path = tmp_path / "side_by_side_house.dxf"
    doc.saveas(str(dxf_path))

    model, flags = ingest_dxf(dxf_path)
    flag_codes = {f.code for f in flags}

    # 1. Roof cluster excluded cleanly (zero roof rooms, zero leaked ROOM_LABEL_MISSING flags)
    assert "DXF_NON_FLOOR_PLAN_CLUSTER_EXCLUDED" in flag_codes
    assert "DXF_SIDE_BY_SIDE_FLOORS_AUTO_SPLIT" in flag_codes
    assert "ROOM_LABEL_MISSING" not in flag_codes
    assert len(model.rooms) == 8
    assert all(p.x_mm < 30000.0 for r in model.rooms for p in r.polygon_mm)

    # 2. Multi-board MDB -> DB-GF / DB-1F hierarchy
    assert model.db_anchor.id == "MDB"
    assert [sb.board_id for sb in model.sub_boards] == ["DB-GF", "DB-1F"]
    assert [sb.floor for sb in model.sub_boards] == ["GF", "1F"]
    assert model.rooms[0].floor == "GF"
    assert model.rooms[-1].floor == "1F"

    # 3. Full Phase 2 electrical + spatial solve
    result = run_phase2_pipeline(model, extra_flags=flags)
    assert len(result.design.sub_mains) == 2
    assert all(sm.overall_pass for sm in result.design.sub_mains)
    assert all(c.overall_pass for c in result.design.circuits)

    # 4. No final circuit jumps across the 10 m empty Model Space gap between GF and 1F!
    for rc in result.spatial.routed_circuits:
        for seg in rc.segments:
            dx_m = abs(seg.end_mm.x_mm - seg.start_mm.x_mm) / 1000.0
            assert dx_m < 9.0, f"Circuit {rc.circuit_id} jumped {dx_m:.1f} m across floor gap!"

    # 5. EAC Meter Cabinet & Earth Pit sit cleanly adjacent to GF envelope (not in empty gap)
    mtr = next(d for d in result.spatial.devices if d.device_type == DeviceType.EAC_METER_CABINET)
    assert -1500.0 <= mtr.location_mm.x_mm <= 9500.0

    # 6. Per-floor Paper-Space DXF tabs in logical order (GF then 1F) + 0 audit errors
    for disc, prefix in (("lighting", "E101-LITE"), ("power", "E102-PWR")):
        out_dxf = export_electrical_layout_dxf(
            result, tmp_path / f"{disc}.dxf", discipline=disc  # type: ignore[arg-type]
        )
        out_doc = read_dxf(str(out_dxf))
        assert len(out_doc.audit().errors) == 0
        tab_names = [lay.name for lay in out_doc.layouts if lay.name != "Model"]
        gf_tabs = [t for t in tab_names if f"{prefix}-GF-" in t]
        f1_tabs = [t for t in tab_names if f"{prefix}-1F-" in t]
        assert len(gf_tabs) == 1 and len(f1_tabs) == 1
        assert tab_names.index(gf_tabs[0]) < tab_names.index(f1_tabs[0])

    # 7. SVG & SLD render cleanly with MDB -> DB-GF / DB-1F
    sld_svg = render_sld_svg(result.design)
    fp_svg = render_floorplan_svg(result, discipline="lighting")
    assert "DB-GF" in sld_svg and "DB-1F" in sld_svg
    assert "DB-GF" in fp_svg and "DB-1F" in fp_svg


def test_greek_bilingual_side_by_side_and_doma_excluded(tmp_path: Path) -> None:
    """Greek ΙΣΟΓΕΙΟ + Α' ΟΡΟΦΟΣ + ΔΩΜΑ single-file DXF splits cleanly to GF/1F."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    _add_floor_cluster(
        msp,
        0.0,
        0.0,
        rooms=[
            (0, 0, 5000, 4000, "ΣΑΛΟΝΙ"),
            (5000, 0, 9000, 4000, "ΚΟΥΖΙΝΑ"),
            (0, 4000, 9000, 6000, "ΔΙΑΔΡΟΜΟΣ"),
        ],
        title="ΙΣΟΓΕΙΟ",
        title_pos=(4500.0, -1000.0),
    )
    _add_floor_cluster(
        msp,
        15000.0,
        0.0,
        rooms=[
            (0, 0, 4500, 4000, "ΥΠΝΟΔΩΜΑΤΙΟ 1"),
            (4500, 0, 9000, 4000, "ΥΠΝΟΔΩΜΑΤΙΟ 2"),
            (0, 4000, 9000, 6000, "ΜΠΑΝΙΟ"),
        ],
        title="Α' ΟΡΟΦΟΣ",
        title_pos=(4500.0, -1000.0),
    )
    _add_floor_cluster(
        msp,
        30000.0,
        0.0,
        rooms=[(0, 0, 9000, 6000, None)],
        title="ΔΩΜΑ",
        title_pos=(4500.0, -1000.0),
    )

    p = tmp_path / "cyprus_bilingual.dxf"
    doc.saveas(str(p))

    model, flags = ingest_dxf(p)
    assert [sb.floor for sb in model.sub_boards] == ["GF", "1F"]
    assert len(model.rooms) == 6
    assert any(f.code == "DXF_NON_FLOOR_PLAN_CLUSTER_EXCLUDED" for f in flags)
    assert any(f.code == "DXF_SIDE_BY_SIDE_FLOORS_AUTO_SPLIT" for f in flags)


def test_vertically_stacked_upstairs_downstairs_bipartite_matching(tmp_path: Path) -> None:
    """Vertically stacked plans with titles above each cluster match 1-to-1 accurately."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # Top cluster (y = 0..6000), title 'Upstairs' sits 1300 mm ABOVE it (y = 7300)
    _add_floor_cluster(
        msp,
        0.0,
        0.0,
        rooms=[
            (0, 0, 4500, 3500, "LIVING ROOM"),
            (4500, 0, 9000, 3500, "KITCHEN"),
            (0, 3500, 9000, 6000, "BEDROOM 1"),
        ],
        title="Upstairs",
        title_pos=(4500.0, 7300.0),
    )
    # Bottom cluster (y = -8500..-2500), gap = 2500 mm, title 'Downstairs' sits 1200 mm ABOVE it
    # (y = -1300, closer to bottom of top cluster = 1300 vs 1200, testing optimal bipartite match!)
    _add_floor_cluster(
        msp,
        0.0,
        -8500.0,
        rooms=[
            (0, 0, 4500, 3500, "UTILITY ROOM"),
            (4500, 0, 9000, 3500, "BEDROOM 2"),
            (0, 3500, 9000, 6000, "BATHROOM"),
        ],
        title="Downstairs",
        title_pos=(4500.0, 7200.0),  # y = -1300
    )

    p = tmp_path / "stacked_floors.dxf"
    doc.saveas(str(p))

    model, _flags = ingest_dxf(p)
    assert [sb.floor for sb in model.sub_boards] == ["GF", "1F"]
    gf_labels = {r.raw_label for r in model.rooms if r.floor == "GF"}
    f1_labels = {r.raw_label for r in model.rooms if r.floor == "1F"}
    assert any("UTILITY" in lbl.upper() for lbl in gf_labels)
    assert any("KITCHEN" in lbl.upper() for lbl in f1_labels)


def test_single_floor_plus_roof_excludes_roof_without_fake_subboards(tmp_path: Path) -> None:
    """Bungalow + Roof Plan side-by-side drops Roof Plan and keeps single-board DB1."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    _add_floor_cluster(
        msp,
        0.0,
        0.0,
        rooms=[
            (0, 0, 5000, 4000, "LIVING ROOM"),
            (5000, 0, 9000, 4000, "KITCHEN"),
            (0, 4000, 4500, 7000, "BEDROOM"),
            (4500, 4000, 9000, 7000, "BATHROOM"),
        ],
        title="GROUND FLOOR PLAN",
        title_pos=(4500.0, -1000.0),
    )
    _add_floor_cluster(
        msp,
        15000.0,
        0.0,
        rooms=[(0, 0, 9000, 7000, None)],
        title="ROOF PLAN",
        title_pos=(4500.0, -1000.0),
    )

    p = tmp_path / "bungalow_and_roof.dxf"
    doc.saveas(str(p))

    model, flags = ingest_dxf(p)
    assert len(model.rooms) == 4
    assert model.sub_boards == []
    assert model.db_anchor.id == "DB1"
    assert any(f.code == "DXF_NON_FLOOR_PLAN_CLUSTER_EXCLUDED" for f in flags)
    assert not any(f.code == "DXF_SIDE_BY_SIDE_FLOORS_AUTO_SPLIT" for f in flags)


def test_detached_single_room_garage_does_not_false_split(tmp_path: Path) -> None:
    """Single-floor villa with detached 1-room garage (untitled) must NOT false-split."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    _add_floor_cluster(
        msp,
        0.0,
        0.0,
        rooms=[
            (0, 0, 5000, 5000, "LIVING ROOM"),
            (5000, 0, 10000, 5000, "KITCHEN"),
            (0, 5000, 5000, 9000, "MASTER BEDROOM"),
            (5000, 5000, 10000, 9000, "BATHROOM"),
        ],
    )
    # Detached 1-room garage 3 m away, 24 m2
    _add_rect_room(msp, 13000.0, 0.0, 19000.0, 4000.0, "GARAGE")

    p = tmp_path / "villa_detached_garage.dxf"
    doc.saveas(str(p))

    model, flags = ingest_dxf(p)
    assert len(model.rooms) == 5
    assert model.sub_boards == []
    assert not any(f.code == "DXF_SIDE_BY_SIDE_FLOORS_AUTO_SPLIT" for f in flags)


def test_untitled_side_by_side_two_full_floors_auto_splits(tmp_path: Path) -> None:
    """Two full untitled floor plans laid out side-by-side auto-split left-to-right GF/1F."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    _add_floor_cluster(
        msp,
        0.0,
        0.0,
        rooms=[
            (0, 0, 5000, 4000, "LIVING ROOM"),
            (5000, 0, 9000, 4000, "KITCHEN"),
            (0, 4000, 9000, 6000, "CORRIDOR"),
        ],
    )
    _add_floor_cluster(
        msp,
        15000.0,
        0.0,
        rooms=[
            (0, 0, 4500, 4000, "MASTER BEDROOM"),
            (4500, 0, 9000, 4000, "BEDROOM 2"),
            (0, 4000, 9000, 6000, "BATHROOM"),
        ],
    )

    p = tmp_path / "untitled_two_floors.dxf"
    doc.saveas(str(p))

    model, flags = ingest_dxf(p)
    assert [sb.board_id for sb in model.sub_boards] == ["DB-GF", "DB-1F"]
    assert any(f.code == "DXF_SIDE_BY_SIDE_FLOORS_AUTO_SPLIT" for f in flags)
