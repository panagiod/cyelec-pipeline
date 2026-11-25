"""Tests for Pillar 4: Interactive Visual Floorplan Overrides, Layer Wizard & Workbench UI."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.models.building import Point2D, RoomType
from cyelec.models.electrical import DesignOverrides, ReportLanguage, TenantBranding
from cyelec.pipeline import run_phase2_pipeline
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def test_room_type_override_and_db_relocation_reroutes_conduits() -> None:
    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    baseline = run_phase2_pipeline(building)

    # Reclassify veranda to bedroom (which adds a dedicated Cyprus Split A/C circuit)
    veranda = next(r for r in building.rooms if r.normalized_type == RoomType.VERANDA)
    base_ac_count = sum(1 for c in baseline.design.circuits if c.spec.category.value == "split_ac")

    # Relocate DB1 to the far right corner of the 120 m² apartment
    new_db_loc = Point2D(x_mm=11_500.0, y_mm=9_500.0)
    overrides = DesignOverrides(
        room_type_overrides={veranda.id: RoomType.BEDROOM},
        db_location_override_mm=new_db_loc,
    )
    edited = run_phase2_pipeline(building, overrides=overrides)

    # 1. Room reclassified & Split A/C count increased by 1
    updated_room = next(r for r in edited.design.building.rooms if r.id == veranda.id)
    assert updated_room.normalized_type == RoomType.BEDROOM
    assert updated_room.is_outdoor is False
    new_ac_count = sum(1 for c in edited.design.circuits if c.spec.category.value == "split_ac")
    assert new_ac_count == base_ac_count + 1

    # 2. DB1 relocated & NetworkX routed conduit length changed
    assert edited.design.building.db_anchor.location_mm == new_db_loc
    assert edited.spatial.total_conduit_m != baseline.spatial.total_conduit_m

    # 3. Audit flags emitted
    flag_codes = {f.code for f in edited.design.review_flags}
    assert "ROOM_TYPE_OVERRIDDEN" in flag_codes
    assert "DB_LOCATION_OVERRIDDEN" in flag_codes

    # 4. Floorplan SVG exposes interactive data-* attributes & updated room type
    svg = render_floorplan_svg(edited)
    assert 'data-minx="' in svg
    assert 'data-maxy="' in svg
    assert 'data-scale="' in svg
    assert 'data-pad-left="' in svg
    assert 'data-pad-top="' in svg
    assert f'data-room-id="{veranda.id}" data-room-type="bedroom"' in svg
    assert 'data-db="true"' in svg


def test_2storey_floorplan_svg_renders_sub_board_markers() -> None:
    building = generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY)
    p2 = run_phase2_pipeline(building)
    svg = render_floorplan_svg(p2)
    assert 'data-sub-board="DB-GF"' in svg
    assert 'data-sub-board="DB-1F"' in svg


def test_workbench_ui_and_api_visual_override_roundtrip() -> None:
    client = TestClient(create_app())

    # 1. Workbench UI contains all Pillar 4 visual & BOQ widgets, zero external CDNs
    ui_resp = client.get("/")
    assert ui_resp.status_code == 200
    html_text = ui_resp.text
    assert "Relocate DB1 on Floorplan" in html_text
    assert "DXF Layer Mapping Wizard" in html_text
    assert "Bill of Quantities (EUR)" in html_text
    assert "villa_2storey" in html_text
    assert "escapeHtml" in html_text
    assert "https://cdn" not in html_text

    # 2. API preview accepts room_type_overrides, db_location_override_mm, branding & language
    ov = DesignOverrides(
        room_type_overrides={"R9": RoomType.BEDROOM},
        db_location_override_mm=Point2D(x_mm=10_000.0, y_mm=8_000.0),
        report_language=ReportLanguage.BILINGUAL,
        branding=TenantBranding(firm_name="Nicosia MEP Ltd", etek_reg_no="ETEK-999"),
    )
    resp = client.post(
        "/v1/solve/benchmark/apartment_3bed",
        json=ov.model_dump(),
        headers={"X-Tenant-ID": "cy-visual-test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"]["design"]["boq"]["grand_total_eur"] > 1000.0
    assert "ΜΟΝΟΓΡΑΜΜΙΚΟ ΔΙΑΓΡΑΜΜΑ" in body["sld_svg"]
    assert "Nicosia MEP Ltd" in body["sld_svg"]
    assert 'data-db="true"' in body["floorplan_svg"]
