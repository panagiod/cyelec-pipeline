"""Tests for Pillar 1: Real-World Messy DXF Resilience Pack."""

from __future__ import annotations

import json
from pathlib import Path

from ezdxf.filemanagement import new as new_dxf
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cyelec.api.app import create_app
from cyelec.cli import app as cli_app
from cyelec.ingest.dxf_ingest import ingest_dxf, inspect_dxf_layers
from cyelec.ingest.room_classifier import classify_room_label
from cyelec.models.building import DxfLayerMapping, RoomType
from cyelec.pipeline import run_phase2_pipeline
from cyelec.standards_loader import load_standards


def _make_wall_only_and_blocks_dxf(path: Path) -> Path:
    """Create a realistic messy Cyprus architect DXF:
    - ZERO closed A-ROOM polylines (only walls on A-WALL with a doorway gap)
    - AutoCAD INSERT blocks on Layer '0' for DOOR_90, BATH_170, SHOWER_90, and DB1_PANEL
    - Greeklish & abbreviated Cyprus room labels (SALONI, KOUZ., YPN. 1, MPANIO)
    """
    doc = new_dxf("R2018")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()

    # Exterior walls: 10,000 x 8,000 mm rectangle
    walls = [
        ((0, 0), (10000, 0)),
        ((10000, 0), (10000, 8000)),
        ((10000, 8000), (0, 8000)),
        ((0, 8000), (0, 0)),
        # Horizontal partition at y=4000 with 900mm door gap at x=2000..2900
        ((0, 4000), (2000, 4000)),
        ((2900, 4000), (10000, 4000)),
        # Vertical partition at x=5000 (bottom half y=0..4000, top half y=4000..8000)
        ((5000, 0), (5000, 1500)),
        ((5000, 2400), (5000, 4000)),  # 900mm door gap at y=1500..2400
        ((5000, 4000), (5000, 8000)),
    ]
    for p1, p2 in walls:
        msp.add_line(p1, p2, dxfattribs={"layer": "A-WALL"})

    # Define reusable AutoCAD BLOCKS with primitives on Layer "0"
    door_blk = doc.blocks.new(name="DOOR_90")
    door_blk.add_arc(
        center=(0, 0), radius=900, start_angle=0, end_angle=90, dxfattribs={"layer": "0"}
    )
    door_blk.add_line((0, 0), (0, 900), dxfattribs={"layer": "0"})

    bath_blk = doc.blocks.new(name="BATH_170")
    bath_blk.add_lwpolyline(
        [(0, 0), (1700, 0), (1700, 750), (0, 750)],
        close=True,
        dxfattribs={"layer": "0"},
    )

    shower_blk = doc.blocks.new(name="SHOWER_90")
    shower_blk.add_lwpolyline(
        [(0, 0), (900, 0), (900, 900), (0, 900)],
        close=True,
        dxfattribs={"layer": "0"},
    )

    db_blk = doc.blocks.new(name="DB1_PANEL")
    db_blk.add_lwpolyline(
        [(0, 0), (400, 0), (400, 150), (0, 150)],
        close=True,
        dxfattribs={"layer": "0"},
    )

    # Insert blocks (door 1 closes gap at y=4000; door 2 closes gap at x=5000)
    msp.add_blockref("DOOR_90", insert=(2000, 4000), dxfattribs={"layer": "A-DOOR"})
    msp.add_blockref(
        "DOOR_90",
        insert=(5000, 1500),
        dxfattribs={"layer": "A-DOOR", "rotation": 90},
    )
    # Place bathtub & shower blocks on a generic layer ("0") so block-name hint triggers!
    msp.add_blockref("BATH_170", insert=(5200, 4200), dxfattribs={"layer": "0"})
    msp.add_blockref("SHOWER_90", insert=(8800, 4200), dxfattribs={"layer": "0"})
    msp.add_blockref("DB1_PANEL", insert=(400, 3800), dxfattribs={"layer": "0"})

    # Greeklish & abbreviated Cyprus labels
    labels = [
        ("SALONI", (2500, 2000)),
        ("KOUZ.", (7500, 2000)),
        ("YPN. 1", (2500, 6000)),
        ("MPANIO", (7500, 6000)),
    ]
    for text, pos in labels:
        t = msp.add_text(text, dxfattribs={"layer": "A-TEXT", "height": 200})
        t.dxf.insert = pos

    doc.saveas(path)
    return path


def _make_custom_layer_dxf(path: Path) -> Path:
    """Create a DXF with completely non-standard Cypriot architect layer names."""
    doc = new_dxf("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # Custom layer names that do NOT match default token heuristics
    msp.add_lwpolyline(
        [(0, 0), (5000, 0), (5000, 4000), (0, 4000)],
        close=True,
        dxfattribs={"layer": "CUSTOM_PERIM_POLYS"},
    )
    msp.add_lwpolyline(
        [(5000, 0), (8000, 0), (8000, 4000), (5000, 4000)],
        close=True,
        dxfattribs={"layer": "CUSTOM_PERIM_POLYS"},
    )
    for p1, p2 in [
        ((0, 0), (8000, 0)),
        ((8000, 0), (8000, 4000)),
        ((8000, 4000), (0, 4000)),
        ((0, 4000), (0, 0)),
        ((5000, 0), (5000, 4000)),
    ]:
        msp.add_line(p1, p2, dxfattribs={"layer": "STRUCT_PARTITIONS"})

    t1 = msp.add_text("KATHISTIKO", dxfattribs={"layer": "CY_NOTES", "height": 180})
    t1.dxf.insert = (2500, 2000)
    t2 = msp.add_text("WC/NTOYS", dxfattribs={"layer": "CY_NOTES", "height": 180})
    t2.dxf.insert = (6500, 2000)

    # Custom fixture polygon in bathroom
    msp.add_lwpolyline(
        [(7000, 200), (7900, 200), (7900, 1100), (7000, 1100)],
        close=True,
        dxfattribs={"layer": "HYDRAULIC_EQ"},
    )
    ft = msp.add_text("SHOWER", dxfattribs={"layer": "HYDRAULIC_EQ", "height": 120})
    ft.dxf.insert = (7450, 650)

    # Custom DB marker
    msp.add_lwpolyline(
        [(200, 200), (500, 200), (500, 350), (200, 350)],
        close=True,
        dxfattribs={"layer": "ELEC_BOARD_LOC"},
    )

    doc.saveas(path)
    return path


def test_cyprus_greeklish_and_abbreviated_room_labels() -> None:
    std = load_standards()
    cases = [
        ("SALONI", RoomType.LIVING_ROOM),
        ("Kathistiko", RoomType.LIVING_ROOM),
        ("KOUZ.", RoomType.KITCHEN),
        ("Koyzina", RoomType.KITCHEN),
        ("YPN. 1", RoomType.MASTER_BEDROOM),
        ("Ypnodomatio 2", RoomType.BEDROOM),
        ("MPANIO", RoomType.BATHROOM),
        ("WC/NTOYS", RoomType.ENSUITE),
        ("DIADROMOS", RoomType.CORRIDOR),
        ("PLYSTARIO", RoomType.UTILITY),
        ("VERANTA", RoomType.VERANDA),
        ("APOTH.", RoomType.STORAGE),
        ("TOUALETA", RoomType.WC),
        ("SALONOKOUZINA", RoomType.KITCHEN_LIVING),
    ]
    for raw, expected in cases:
        res = classify_room_label(raw, std.room_templates, room_id="R1")
        assert res.normalized_type == expected, (
            f"Failed for '{raw}': got {res.normalized_type}"
        )
        assert res.method in ("tier1_exact", "tier1_substring")


def test_wall_polygonization_and_insert_block_explosion(tmp_path: Path) -> None:
    dxf_path = _make_wall_only_and_blocks_dxf(tmp_path / "messy_walls_blocks.dxf")

    model, flags = ingest_dxf(dxf_path)
    flag_codes = {f.code for f in flags}
    assert "ROOMS_POLYGONIZED_FROM_WALLS" in flag_codes

    # All 4 rooms derived from walls + doorway arc closures
    assert len(model.rooms) == 4
    room_types = {r.normalized_type for r in model.rooms}
    assert room_types == {
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM,
        RoomType.BATHROOM,
    }

    # Exploded INSERT door blocks captured
    assert len(model.doors) >= 2

    # Exploded INSERT bathtub & shower blocks captured inside bathroom
    bath_room = next(r for r in model.rooms if r.normalized_type == RoomType.BATHROOM)
    fixture_types = {f.fixture_type for f in bath_room.fixtures}
    assert "bathtub" in fixture_types
    assert "shower_tray" in fixture_types

    # Exploded DB1_PANEL block located near (600, 3875)
    assert 300.0 <= model.db_anchor.location_mm.x_mm <= 900.0
    assert 3700.0 <= model.db_anchor.location_mm.y_mm <= 4050.0

    # End-to-end Phase 2 pipeline succeeds and creates Section 701 wet zones from exploded blocks!
    p2 = run_phase2_pipeline(dxf_path)
    assert len(p2.spatial.wet_zones) == 1
    assert p2.spatial.wet_zones[0].room_id == bath_room.id
    assert len(p2.design.circuits) >= 6


def test_custom_layer_mapping_and_inspection(tmp_path: Path) -> None:
    dxf_path = _make_custom_layer_dxf(tmp_path / "custom_layers.dxf")

    # Inspect layers discovers all custom layers
    layers = inspect_dxf_layers(dxf_path)
    layer_map = {item.layer_name: item for item in layers}
    assert "CUSTOM_PERIM_POLYS" in layer_map
    assert layer_map["CUSTOM_PERIM_POLYS"].inferred_role == "unknown"
    assert layer_map["CUSTOM_PERIM_POLYS"].entity_count == 2
    assert "STRUCT_PARTITIONS" in layer_map
    assert "HYDRAULIC_EQ" in layer_map

    # With explicit DxfLayerMapping, ingestion & full Phase 2 succeed cleanly
    mapping = DxfLayerMapping(
        rooms=["CUSTOM_PERIM_POLYS"],
        walls=["STRUCT_PARTITIONS"],
        text=["CY_NOTES"],
        fixtures=["HYDRAULIC_EQ"],
        db=["ELEC_BOARD_LOC"],
    )
    model, _ = ingest_dxf(dxf_path, layer_mapping=mapping)
    assert len(model.rooms) == 2
    assert {r.normalized_type for r in model.rooms} == {
        RoomType.LIVING_ROOM,
        RoomType.ENSUITE,
    }
    ensuite = next(r for r in model.rooms if r.normalized_type == RoomType.ENSUITE)
    assert len(ensuite.fixtures) == 1
    assert ensuite.fixtures[0].fixture_type == "shower_tray"
    assert 200.0 <= model.db_anchor.location_mm.x_mm <= 550.0

    p2 = run_phase2_pipeline(dxf_path, layer_mapping=mapping)
    assert len(p2.spatial.wet_zones) == 1


def test_cli_inspect_layers_and_api_dxf_layers(tmp_path: Path) -> None:
    dxf_path = _make_custom_layer_dxf(tmp_path / "custom_api.dxf")
    mapping = DxfLayerMapping(
        rooms=["CUSTOM_PERIM_POLYS"],
        walls=["STRUCT_PARTITIONS"],
        text=["CY_NOTES"],
        fixtures=["HYDRAULIC_EQ"],
        db=["ELEC_BOARD_LOC"],
    )
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(mapping.model_dump_json(), encoding="utf-8")

    # CLI inspect-dxf --layers & --layer-mapping
    runner = CliRunner()
    res_layers = runner.invoke(cli_app, ["inspect-dxf", str(dxf_path), "--layers"])
    assert res_layers.exit_code == 0, res_layers.output
    assert "CUSTOM_PERIM_POLYS" in res_layers.output

    res_mapped = runner.invoke(
        cli_app,
        ["inspect-dxf", str(dxf_path), "--layer-mapping", str(mapping_path)],
    )
    assert res_mapped.exit_code == 0, res_mapped.output
    assert "living_room" in res_mapped.output
    assert "ensuite" in res_mapped.output

    # FastAPI POST /v1/dxf/layers & POST /v1/solve/dxf with layer_mapping_json
    client = TestClient(create_app())
    dxf_bytes = dxf_path.read_bytes()

    resp = client.post(
        "/v1/dxf/layers",
        files={"file": ("custom_api.dxf", dxf_bytes, "application/dxf")},
        headers={"X-Tenant-ID": "cy-firm-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == "cy-firm-1"
    names = {layer["layer_name"] for layer in body["layers"]}
    assert {"CUSTOM_PERIM_POLYS", "STRUCT_PARTITIONS", "HYDRAULIC_EQ"}.issubset(names)

    solve_resp = client.post(
        "/v1/solve/dxf",
        files={"file": ("custom_api.dxf", dxf_bytes, "application/dxf")},
        data={"layer_mapping_json": json.dumps(mapping.model_dump())},
    )
    assert solve_resp.status_code == 200, solve_resp.text
    solved_rooms = solve_resp.json()["result"]["design"]["building"]["rooms"]
    assert len(solved_rooms) == 2
