"""Comprehensive Stage 5 verification: Turnkey Cyprus Residential Tender & Construction Pack."""

from __future__ import annotations

import io
import json
import math
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from cyelec.api.app import create_app
from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.models.building import Point2D
from cyelec.models.electrical import (
    CircuitCategory,
    DesignOverrides,
    ResidentialSystemsBrief,
)
from cyelec.models.spatial import DeviceType
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def _count_dxf_text_overlaps(doc: ezdxf.document.Drawing) -> int:
    msp = doc.modelspace()
    boxes: list[tuple[float, float, float, float]] = []
    for txt in msp.query("TEXT"):
        if not str(txt.dxf.layer).startswith("E-"):
            continue
        x, y = float(txt.dxf.insert.x), float(txt.dxf.insert.y)
        h = float(txt.dxf.height)
        w = len(str(txt.dxf.text)) * h * 0.65
        boxes.append((x, y, x + w, y + h))
    overlaps = 0
    for i in range(len(boxes)):
        ax1, ay1, ax2, ay2 = boxes[i]
        for j in range(i + 1, len(boxes)):
            bx1, by1, bx2, by2 = boxes[j]
            if not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1):
                overlaps += 1
    return overlaps


def _add_rect_block(doc: ezdxf.document.Drawing, name: str, w: float, h: float) -> None:
    blk = doc.blocks.new(name=name)
    blk.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)


def test_furniture_block_auto_detection_and_placement(tmp_path: Path) -> None:
    """DXF furniture blocks drive pendant, TV/Data, bedside switches, and wardrobe exclusion."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4  # mm
    for lyr in ("A-ROOM", "A-WALL", "A-DOOR", "A-WIN", "A-TEXT", "A-FURN", "A-DB"):
        doc.layers.add(lyr)
    msp = doc.modelspace()

    # Two rooms: Living/Dining (0..6000 x 0..5000) & Master Bedroom (6000..10500 x 0..5000)
    rooms_spec = [
        (0, 0, 6000, 5000, "KITCHEN / LIVING"),
        (6000, 0, 10500, 5000, "MASTER BEDROOM"),
    ]
    for x0, y0, x1, y1, lbl in rooms_spec:
        msp.add_lwpolyline(
            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            close=True,
            dxfattribs={"layer": "A-ROOM"},
        )
        msp.add_text(lbl, dxfattribs={"layer": "A-TEXT", "height": 180}).set_placement(
            ((x0 + x1) / 2, (y0 + y1) / 2)
        )
    # Perimeter + partition walls
    for p1, p2 in (
        ((0, 0), (10500, 0)),
        ((10500, 0), (10500, 5000)),
        ((10500, 5000), (0, 5000)),
        ((0, 5000), (0, 0)),
        ((6000, 0), (6000, 5000)),
    ):
        msp.add_line(p1, p2, dxfattribs={"layer": "A-WALL"})
    # Interior door between rooms & window on south wall
    msp.add_arc(
        (6000, 2200), radius=900, start_angle=0, end_angle=90, dxfattribs={"layer": "A-DOOR"}
    )
    msp.add_line((1500, 0), (3500, 0), dxfattribs={"layer": "A-WIN"})
    msp.add_text("DB1", dxfattribs={"layer": "A-DB", "height": 150}).set_placement((400, 4600))

    # Furniture blocks: DINING_TABLE, TV, STOVE, FRIDGE in Living/Kitchen; BED & WARDROBE in Bedroom
    _add_rect_block(doc, "DINING_TABLE_6P", 1800, 950)
    _add_rect_block(doc, "TV_UNIT", 1400, 450)
    _add_rect_block(doc, "STOVE_COOKER", 650, 650)
    _add_rect_block(doc, "FRIDGE_UNIT", 700, 700)
    _add_rect_block(doc, "DOUBLE_BED", 2000, 1800)
    _add_rect_block(doc, "WARDROBE_CLOSET", 2400, 600)

    msp.add_blockref("DINING_TABLE_6P", (1800, 2600), dxfattribs={"layer": "A-FURN"})
    msp.add_blockref("TV_UNIT", (2200, 4450), dxfattribs={"layer": "A-FURN"})
    msp.add_blockref("STOVE_COOKER", (150, 200), dxfattribs={"layer": "A-FURN"})
    msp.add_blockref("FRIDGE_UNIT", (150, 1200), dxfattribs={"layer": "A-FURN"})
    msp.add_blockref("DOUBLE_BED", (7600, 2900), dxfattribs={"layer": "A-FURN"})
    msp.add_blockref("WARDROBE_CLOSET", (6300, 100), dxfattribs={"layer": "A-FURN"})

    dxf_path = tmp_path / "furnished_house.dxf"
    doc.saveas(str(dxf_path))

    building, flags = ingest_dxf(dxf_path)
    all_furn_types = {f.furniture_type for r in building.rooms for f in r.furniture}
    assert {"dining_table", "tv", "stove", "fridge", "bed", "wardrobe"} <= all_furn_types

    p2 = run_phase2_pipeline(building, extra_flags=flags)
    devs = p2.spatial.devices

    # 1. Dining pendant centered over DINING_TABLE_6P (x=2700, y=3075)
    pendants = [d for d in devs if d.device_type == DeviceType.PENDANT_LIGHT]
    assert len(pendants) >= 1
    assert math.hypot(pendants[0].location_mm.x_mm - 2700, pendants[0].location_mm.y_mm - 3075) < 50

    # 2. Zero sockets placed inside the wardrobe footprint (x=6300..8700, y=100..700)
    bedroom_sockets = [
        d for d in devs if d.device_type == DeviceType.SOCKET_TWIN and d.location_mm.x_mm > 6000
    ]
    assert len(bedroom_sockets) >= 2
    for sock in bedroom_sockets:
        in_wardrobe = 6300 <= sock.location_mm.x_mm <= 8700 and 0 <= sock.location_mm.y_mm <= 750
        assert not in_wardrobe, f"Socket {sock.device_id} landed behind wardrobe!"

    # 3. TV / Data outlets snapped close to TV_UNIT wall (top wall y=5000, x~2900)
    tv_outlets = [
        d for d in devs if d.device_type == DeviceType.TV_OUTLET and d.location_mm.x_mm < 6000
    ]
    assert len(tv_outlets) >= 1
    assert tv_outlets[0].location_mm.y_mm > 4000

    # 4. Bedside 2-way switch placed in Master Bedroom
    bed_2w = [
        d
        for d in devs
        if d.device_type == DeviceType.SWITCH
        and d.switch_type == "1G2W"
        and d.location_mm.x_mm > 6000
    ]
    assert len(bed_2w) >= 1


def test_hvac_modes_heat_pump_ufh_and_vrv() -> None:
    """HVAC selector cleanly switches between Heat Pump + UFH and VRV without Split A/Cs."""
    villa = generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY)

    # Mode A: Air-to-Water Heat Pump + UFH
    hp_ov = DesignOverrides(
        residential_brief=ResidentialSystemsBrief(hvac_mode="heat_pump_ufh", heat_pump_kw=6.5)
    )
    hp_res = run_phase2_pipeline(villa, overrides=hp_ov)
    hp_cats = {c.spec.category for c in hp_res.design.circuits}
    hp_dev_types = {d.device_type for d in hp_res.spatial.devices}
    assert CircuitCategory.HEAT_PUMP in hp_cats
    assert CircuitCategory.SPLIT_AC not in hp_cats
    assert DeviceType.SPLIT_AC not in hp_dev_types
    assert {
        DeviceType.HEAT_PUMP_OUTDOOR,
        DeviceType.HEAT_PUMP_HYDROBOX,
        DeviceType.UFH_MANIFOLD,
        DeviceType.ROOM_THERMOSTAT,
    } <= hp_dev_types
    assert all(c.overall_pass for c in hp_res.design.circuits)

    # Mode B: VRV / VRF Central + Indoor FCUs
    vrv_ov = DesignOverrides(
        residential_brief=ResidentialSystemsBrief(hvac_mode="vrv", vrv_outdoor_kw=7.5)
    )
    vrv_res = run_phase2_pipeline(villa, overrides=vrv_ov)
    vrv_cats = {c.spec.category for c in vrv_res.design.circuits}
    vrv_descs = [c.spec.description for c in vrv_res.design.circuits]
    vrv_dev_types = {d.device_type for d in vrv_res.spatial.devices}
    assert CircuitCategory.HVAC_3P in vrv_cats
    assert not any("Split A/C" in d for d in vrv_descs)
    assert any("VRV Concealed Indoor Fan-Coil" in d for d in vrv_descs)
    assert DeviceType.SPLIT_AC not in vrv_dev_types
    assert {DeviceType.VRV_OUTDOOR, DeviceType.VRV_INDOOR_ISOLATOR} <= vrv_dev_types
    assert all(c.overall_pass for c in vrv_res.design.circuits)


def test_full_turnkey_stage5_pack_all_6_systems_and_zero_overlaps(tmp_path: Path) -> None:
    """Turnkey Stage 5 Brief produces audited E-101..E-301 DXF/SVG, LPS, DIN-Rail & 0 overlaps."""
    villa_dxf = tmp_path / "villa_2storey.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY, out_dxf=villa_dxf)

    brief = ResidentialSystemsBrief(
        hvac_mode="heat_pump_ufh",
        heat_pump_kw=6.0,
        include_electric_shutters=True,
        include_kitchen_fcus=True,
        include_water_pumps=True,
        include_gate_motor=True,
        include_elv=True,
        include_smoke_detectors=True,
        include_pool=True,
        include_site_works=True,
        include_knx=True,
        ev_charger_kw=11.0,
        building_height_m=14.0,
        conduit_drafting_style="homerun_arcs",
    )
    ov = DesignOverrides(pv_capacity_kva=6.0, residential_brief=brief)

    p2 = run_phase2_pipeline(villa_dxf, overrides=ov)
    outputs = export_phase3_deliverables(p2, tmp_path / "pack", source_dxf_path=villa_dxf)

    # 1. Every placed device has a non-null circuit_id binding
    assert all(d.circuit_id is not None for d in p2.spatial.devices)
    dev_types = {d.device_type for d in p2.spatial.devices}
    for required_dt in (
        DeviceType.LUMINAIRE,
        DeviceType.DIMMER_SWITCH,
        DeviceType.OUTDOOR_PIR_LIGHT,
        DeviceType.SHUTTER_SWITCH,
        DeviceType.KITCHEN_FCU,
        DeviceType.HEAT_PUMP_OUTDOOR,
        DeviceType.UFH_MANIFOLD,
        DeviceType.WATER_BOOSTER_PUMP,
        DeviceType.GATE_MOTOR,
        DeviceType.EV_WALLBOX,
        DeviceType.POOL_SUB_DB,
        DeviceType.TELECOM_CABINET,
        DeviceType.DATA_RJ45,
        DeviceType.TV_OUTLET,
        DeviceType.WIFI_AP,
        DeviceType.INTERCOM_OUTDOOR,
        DeviceType.INTERCOM_INDOOR,
        DeviceType.ALARM_PANEL,
        DeviceType.ALARM_KEYPAD,
        DeviceType.ALARM_PIR,
        DeviceType.ALARM_SIREN,
        DeviceType.CCTV_CAMERA,
        DeviceType.SMOKE_DETECTOR,
        DeviceType.HEAT_DETECTOR,
        DeviceType.KNX_KEYPAD,
        DeviceType.GARDEN_BOLLARD,
    ):
        assert required_dt in dev_types, f"Missing Stage 5 device {required_dt}"

    # 2. CIBSE / BS EN 12464-1 Lumen Method checks populated
    assert len(p2.design.lumen_checks) >= len(p2.design.building.rooms) - 1
    assert all(c.achieved_lux > 0 for c in p2.design.lumen_checks)

    # 3. IEC 62305-2 Lightning Risk Assessment triggers LPS Class IV at 14m height
    lps = p2.design.lightning_risk
    assert lps is not None
    assert lps.collection_area_ad_m2 > 0
    assert lps.lps_required is True
    assert "Class IV" in lps.recommended_lps_class

    # 4. Physical 18mm DIN-rail DB layout for MDB + sub-boards (>=20% spare ways, selectivity pass)
    assert len(p2.design.din_rail_boards) >= 1
    for brd in p2.design.din_rail_boards:
        assert brd.spare_pct >= 20.0
        assert brd.selectivity_pass is True
        assert brd.min_device_icu_ka >= brd.incoming_ipf_ka

    # 5. All DXFs pass ezdxf audit with ZERO errors & ZERO overlapping electrical text labels
    for dxf_key in (
        "layout_dxf",
        "e101_lighting_dxf",
        "e102_power_dxf",
        "e103_elv_dxf",
        "e104_site_dxf",
        "e201_sld_dxf",
        "e202_riser_dxf",
        "e301_details_dxf",
    ):
        doc = ezdxf.readfile(str(outputs[dxf_key]))
        assert len(doc.audit().errors) == 0, f"Audit errors in {dxf_key}"
        if dxf_key in ("layout_dxf", "e101_lighting_dxf", "e102_power_dxf", "e103_elv_dxf"):
            assert _count_dxf_text_overlaps(doc) == 0, f"Label overlaps in {dxf_key}"

    # 6. Cyprus Home-Run style keeps E-ROUTE-CONDUIT OFF by default in E-102 DXF
    e102_doc = ezdxf.readfile(str(outputs["e102_power_dxf"]))
    assert "E-ROUTE-CONDUIT" in e102_doc.layers
    assert not e102_doc.layers.get("E-ROUTE-CONDUIT").is_on()

    # 7. Paper-Space tabs present on E-103, E-104, E-202
    assert any(
        n.startswith("E103-") for n in ezdxf.readfile(str(outputs["e103_elv_dxf"])).layouts.names()
    )
    assert "E104-SITE-A3" in ezdxf.readfile(str(outputs["e104_site_dxf"])).layouts
    assert "E202-RISER-DB-A1" in ezdxf.readfile(str(outputs["e202_riser_dxf"])).layouts

    # 8. Strict-XML SVGs for E-103, E-104, E-202
    for svg_key in ("e103_elv_svg", "e104_site_svg", "e202_riser_svg"):
        root = ET.fromstring(outputs[svg_key].read_text(encoding="utf-8"))
        assert root.tag.endswith("svg")


def test_10section_tender_boq_xlsx_and_6sheet_load_schedule_preserved(tmp_path: Path) -> None:
    """10-Section Tender BOQ has 3 live-formula tabs while load_schedule.xlsx stays 6 tabs."""
    apt_dxf = tmp_path / "apt.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=apt_dxf)
    p2 = run_phase2_pipeline(apt_dxf)
    outputs = export_phase3_deliverables(p2, tmp_path / "pack", source_dxf_path=apt_dxf)

    # 1. Existing 6-sheet load_schedule.xlsx preserved + Lumen Method table added to Sheet 6
    ls_wb = load_workbook(outputs["xlsx"], data_only=False)
    assert ls_wb.sheetnames == [
        "Load Schedule",
        "EAC & Review Flags",
        "Bill of Quantities",
        "EAC Application",
        "HMY 58 Test Schedule",
        "Luminaire & Switching",
    ]
    ws6_text = " ".join(
        str(cell.value)
        for row in ls_wb["Luminaire & Switching"].iter_rows()
        for cell in row
        if cell.value
    )
    assert "LUMEN METHOD" in ws6_text.upper()

    # 2. Dedicated 10-Section Tender BOQ workbook
    t_wb = load_workbook(outputs["tender_boq_xlsx"], data_only=False)
    assert t_wb.sheetnames == [
        "Tender Summary",
        "Engineer Estimate (Priced)",
        "Contractor Tender (Blank)",
    ]
    ws_sum = t_wb["Tender Summary"]
    sum_vals = [str(ws_sum.cell(row=r, column=1).value or "") for r in range(1, ws_sum.max_row + 1)]
    for sec in ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10"):
        assert f"SEC {sec}" in sum_vals, f"Missing BOQ section SEC {sec} in Tender Summary"

    # Live Excel SUM formulas on all 3 sheets
    for sname in t_wb.sheetnames:
        ws = t_wb[sname]
        formulas = [
            str(c.value)
            for row in ws.iter_rows()
            for c in row
            if isinstance(c.value, str) and c.value.startswith("=")
        ]
        assert any("SUM(" in f for f in formulas), f"Missing SUM formulas in {sname}"

    # Contractor Tender (Blank) has blank rate cells (column F) with IF/multiply amount formulas
    ws_blank = t_wb["Contractor Tender (Blank)"]
    rate_blanks = 0
    if_formulas = 0
    for r in range(5, ws_blank.max_row + 1):
        g_val = ws_blank.cell(row=r, column=7).value
        if isinstance(g_val, str) and g_val.startswith("=IF("):
            if_formulas += 1
            if ws_blank.cell(row=r, column=6).value is None:
                rate_blanks += 1
    assert if_formulas >= 10
    assert rate_blanks == if_formulas


def test_api_preview_stage5_svgs_anchor_relocation_and_workbench_ui() -> None:
    """API returns E-103/E-104/E-202 SVGs, honors 4-anchor overrides, and ships Stage 5 ZIP pack."""
    client = TestClient(create_app())

    # 1. Workbench UI includes 60-Second Brief, E-103/E-104/E-202 tabs & anchor selector
    ui_html = client.get("/").text
    for token in ("briefBox", "btnSheetElv", "tabSite", "tabRiser", "anchorTargetSel"):
        assert token in ui_html

    # 2. Preview solve with Brief + Meter/Earth/Telecom relocation overrides
    payload = DesignOverrides(
        residential_brief=ResidentialSystemsBrief(
            hvac_mode="heat_pump_ufh",
            include_pool=True,
            include_knx=True,
            ev_charger_kw=11.0,
        ),
        meter_location_override_mm=Point2D(x_mm=1200.0, y_mm=-300.0),
        earth_pit_location_override_mm=Point2D(x_mm=1800.0, y_mm=-500.0),
        telecom_location_override_mm=Point2D(x_mm=2400.0, y_mm=2400.0),
    ).model_dump(mode="json")

    resp = client.post("/v1/solve/benchmark/villa_2storey", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    for svg_field in ("elv_svg", "site_svg", "riser_svg"):
        assert svg_field in body and len(body[svg_field]) > 200
        assert ET.fromstring(body[svg_field]).tag.endswith("svg")

    flags = body["result"]["design"]["review_flags"]
    anchor_flags = [f for f in flags if f["code"] == "ANCHOR_LOCATION_OVERRIDDEN"]
    assert len(anchor_flags) == 3

    devs = body["result"]["spatial"]["devices"]
    meter = next(d for d in devs if d["device_type"] == "eac_meter_cabinet")
    earth = next(d for d in devs if d["device_type"] == "earth_pit")
    rack = next(d for d in devs if d["device_type"] == "telecom_cabinet")
    assert meter["location_mm"] == {"x_mm": 1200.0, "y_mm": -300.0}
    assert earth["location_mm"] == {"x_mm": 1800.0, "y_mm": -500.0}
    assert rack["location_mm"] == {"x_mm": 2400.0, "y_mm": 2400.0}

    # 3. Downloadable ZIP pack contains all Stage 5 deliverables & manifest hashes
    zip_resp = client.post("/v1/solve/benchmark/apartment_3bed/bundle")
    assert zip_resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        names = set(zf.namelist())
        for expected in (
            "E103_elv_layout.dxf",
            "E103_elv_layout.svg",
            "E104_site_earthing_plan.dxf",
            "E104_site_earthing_plan.svg",
            "E202_riser_and_db_layout.dxf",
            "E202_riser_and_db_layout.svg",
            "tender_boq_10section.xlsx",
            "manifest.json",
        ):
            assert expected in names, f"Missing {expected} from ZIP pack"
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        for mkey in ("e103_elv_dxf", "e104_site_dxf", "e202_riser_dxf", "tender_boq_xlsx"):
            assert mkey in manifest["files"]
