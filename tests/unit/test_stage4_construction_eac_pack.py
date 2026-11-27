"""Stage 4 Complete Cyprus EAC Approval & Site Construction LV Pack tests (W1–W5)."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from cyelec.api.app import create_app
from cyelec.models.building import BenchmarkTemplate
from cyelec.models.electrical import DesignOverrides, EacProjectMetadata, TenantBranding
from cyelec.models.spatial import DeviceType
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.eac_forms import render_eac_application_form_html
from cyelec.render.pdf_export import is_weasyprint_available
from cyelec.render.sld import render_sld_dxf, render_sld_svg
from cyelec.synth.benchmarks import generate_benchmark_dxf


def _count_electrical_text_overlaps(doc: ezdxf.document.Drawing) -> int:
    """Count overlapping bounding boxes among E-* TEXT entities in Model Space."""
    msp = doc.modelspace()
    boxes: list[tuple[float, float, float, float]] = []
    for txt in msp.query("TEXT"):
        if not txt.dxf.layer.startswith("E-"):
            continue
        x, y = float(txt.dxf.insert.x), float(txt.dxf.insert.y)
        h = float(txt.dxf.height or 100.0)
        text_str = str(txt.dxf.text or "")
        w = max(len(text_str) * h * 0.62, h)
        boxes.append((x, y, x + w, y + h))
    overlaps = 0
    for i in range(len(boxes)):
        ax0, ay0, ax1, ay1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            bx0, by0, bx1, by1 = boxes[j]
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                overlaps += 1
    return overlaps


# ---------------------------------------------------------------------------
# W1: Lighting, Switching, 2-Way Control Legs, Mounting Heights & Cyprus Fixed Accessories
# ---------------------------------------------------------------------------


def test_w1_switching_legs_mounting_heights_and_cyprus_fixed_accessories(
    tmp_path: Path,
) -> None:
    apt_dxf = tmp_path / "apt3bed.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=apt_dxf)
    p2 = run_phase2_pipeline(apt_dxf)

    assert p2.design.has_blockers is False
    assert all(c.overall_pass for c in p2.design.circuits)

    devs = p2.spatial.devices
    by_type: dict[DeviceType, list[object]] = {}
    for d in devs:
        by_type.setdefault(d.device_type, []).append(d)

    # 1. Switch-to-luminaire control legs & 2-way switching (Corridor & Master Bedroom)
    switches = [d for d in devs if d.device_type == DeviceType.SWITCH]
    two_way = [s for s in switches if s.switch_type == "1G2W"]
    two_gang = [s for s in switches if s.switch_type == "2G1W"]
    bedside = [s for s in switches if abs(s.mounting_height_mm - 750.0) < 10.0]

    assert len(two_way) >= 4  # Master bedroom + corridor pairs
    assert len(two_gang) >= 2  # Bathrooms / large living room
    assert len(bedside) >= 1  # Master bedroom bedside +750mm 2-way switch

    assert len(p2.spatial.switch_legs) >= 10
    assert any(leg.is_two_way for leg in p2.spatial.switch_legs)
    dev_ids = {d.device_id for d in devs}
    for leg in p2.spatial.switch_legs:
        assert leg.switch_id in dev_ids
        assert leg.target_device_ids
        for tid in leg.target_device_ids:
            assert tid in dev_ids

    # 2. Luminaire lux & wattage metadata populated
    lums = [d for d in devs if d.device_type == DeviceType.LUMINAIRE]
    assert len(lums) >= len(p2.design.building.rooms)
    assert all((d.luminaire_wattage_w or 0.0) > 0.0 for d in lums)
    assert all((d.target_lux or 0) >= 100 for d in lums)

    # 3. Cyprus wet-room & kitchen/water-heater fixed accessories
    mirrors = [d for d in devs if d.device_type == DeviceType.MIRROR_LIGHT]
    fans = [d for d in devs if d.device_type == DeviceType.EXHAUST_FAN]
    fan_isols = [d for d in devs if d.device_type == DeviceType.FAN_ISOLATOR_3P]
    ccus = [d for d in devs if d.device_type == DeviceType.COOKER_CONTROL_UNIT]
    wh_dps = [d for d in devs if d.device_type == DeviceType.WATER_HEATER_DP_SWITCH]
    meters = [d for d in devs if d.device_type == DeviceType.EAC_METER_CABINET]
    pits = [d for d in devs if d.device_type == DeviceType.EARTH_PIT]

    assert len(mirrors) == 3  # Bathroom + En-suite + Guest WC
    assert all(m.ip_rating == "IP44" and m.mounting_height_mm == 2000.0 for m in mirrors)
    assert len(fans) == 3
    assert all(f.ip_rating == "IP44" and f.mounting_height_mm == 2400.0 for f in fans)
    assert len(fan_isols) == 3
    assert all(fi.mounting_height_mm == 2200.0 for fi in fan_isols)
    assert len(ccus) == 1 and ccus[0].mounting_height_mm == 1150.0
    assert len(wh_dps) == 1 and wh_dps[0].mounting_height_mm == 1200.0
    assert len(meters) == 1 and meters[0].mounting_height_mm == 1500.0
    assert len(pits) == 1 and pits[0].mounting_height_mm == 0.0

    # 4. Site-verification advisory flag raised for EAC Meter Cabinet & Earth Pit
    assert any(
        f.code == "EAC_METER_AND_EARTH_PIT_SITE_VERIFY" for f in p2.design.review_flags
    )


# ---------------------------------------------------------------------------
# W2: Separate Discipline DXFs (E-101, E-102, E-301), Exact Scale, Zero Overlaps & PDFs
# ---------------------------------------------------------------------------


def test_w2_separate_discipline_dxfs_details_sheet_and_pdfs(tmp_path: Path) -> None:
    apt_dxf = tmp_path / "apt_w2.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=apt_dxf)
    p2 = run_phase2_pipeline(
        apt_dxf,
        overrides=DesignOverrides(
            branding=TenantBranding(
                firm_name="Kypros MEP Ltd",
                engineer_name="A. Christophides",
                etek_reg_no="ETEK-E-5501",
            ),
            eac_metadata=EacProjectMetadata(
                district="Limassol",
                municipality="Germasogeia",
                sheet_plan="LIV/52",
                plot_no="418",
                building_permit_no="BP-2026-0091",
                owner_name="N. Papadopoulos",
                eac_tariff="01 Domestic",
                contractor_name="A. Ioannou Electrical Ltd",
                contractor_hmy_reg="HMY-A-1420",
            ),
        ),
    )
    outputs = export_phase3_deliverables(p2, tmp_path / "stage4_pack")

    # All required Stage 4 + backward-compatible files generated
    for key in (
        "layout_dxf",
        "e101_lighting_dxf",
        "e101_lighting_svg",
        "e102_power_dxf",
        "e102_power_svg",
        "e201_sld_dxf",
        "e201_sld_svg",
        "sld_dxf",
        "sld_svg",
        "e301_details_dxf",
        "e301_details_svg",
    ):
        assert outputs[key].is_file(), f"Missing output {key}"

    # 1. E-101 Lighting Layout DXF
    e101_doc = ezdxf.readfile(str(outputs["e101_lighting_dxf"]))
    assert len(e101_doc.audit().errors) == 0
    e101_layers = {e.dxf.layer for e in e101_doc.modelspace()}
    assert "E-LITE" in e101_layers
    assert "E-SWIT" in e101_layers
    assert "E-SWIT-LEG" in e101_layers
    assert "E-COND-LITE" in e101_layers
    # Strictly lighting-only: NO power sockets, A/C, or heavy appliances
    assert "E-SOCK" not in e101_layers
    assert "E-AC" not in e101_layers
    assert "E-APPL" not in e101_layers
    assert _count_electrical_text_overlaps(e101_doc) == 0
    e101_tab = next(
        lyt.name for lyt in e101_doc.layouts if lyt.name.startswith("E101-LITE-A3-1-50")
    )
    vp101 = list(e101_doc.layouts.get(e101_tab).query("VIEWPORT"))[-1]
    assert vp101.dxf.view_height / vp101.dxf.height == pytest.approx(50.0, rel=1e-4)

    # 2. E-102 Power Layout DXF
    e102_doc = ezdxf.readfile(str(outputs["e102_power_dxf"]))
    assert len(e102_doc.audit().errors) == 0
    e102_layers = {e.dxf.layer for e in e102_doc.modelspace()}
    assert "E-SOCK" in e102_layers
    assert "E-AC" in e102_layers
    assert "E-APPL" in e102_layers
    assert "E-EARTHING" in e102_layers
    assert "E-COND-PWR" in e102_layers
    # Strictly power-only: NO ceiling luminaires or switch-leg arcs
    assert "E-LITE" not in e102_layers
    assert "E-SWIT-LEG" not in e102_layers
    assert _count_electrical_text_overlaps(e102_doc) == 0
    e102_tab = next(
        lyt.name for lyt in e102_doc.layouts if lyt.name.startswith("E102-PWR-A3-1-50")
    )
    vp102 = list(e102_doc.layouts.get(e102_tab).query("VIEWPORT"))[-1]
    assert vp102.dxf.view_height / vp102.dxf.height == pytest.approx(50.0, rel=1e-4)

    # 3. E-301 Construction Details & General Notes DXF + SVG
    e301_doc = ezdxf.readfile(str(outputs["e301_details_dxf"]))
    assert len(e301_doc.audit().errors) == 0
    assert "EAC-DETAILS-A3" in e301_doc.layouts
    e301_texts = " ".join(str(t.dxf.text) for t in e301_doc.modelspace().query("TEXT"))
    assert "GENERAL ELECTRICAL NOTES" in e301_texts
    assert "DETAIL 1: TT EARTH INSPECTION PIT" in e301_texts
    assert "DETAIL 2: CYPRUS EAC METER CABINET" in e301_texts
    assert "DETAIL 3: BS 7671 SEC. 701 WET ZONES" in e301_texts

    e301_svg = outputs["e301_details_svg"].read_text(encoding="utf-8")
    assert "ΓΕΝΙΚΕΣ ΣΗΜΕΙΩΣΕΙΣ" in e301_svg
    assert "ΚΙΒΩΤΙΟ ΜΕΤΡΗΤΗ ΑΗΚ" in e301_svg
    assert "Limassol" in e301_svg
    assert "418" in e301_svg

    # 4. Printable landscape vector PDFs when WeasyPrint is installed
    if is_weasyprint_available():
        for pdf_key in (
            "e101_lighting_pdf",
            "e102_power_pdf",
            "e201_sld_pdf",
            "e301_details_pdf",
        ):
            assert pdf_key in outputs and outputs[pdf_key].is_file()
            assert outputs[pdf_key].read_bytes()[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# W3: Full Cyprus EAC SLD E-201 (Tails, TT 100mA Type-S, SPD, Earthing/Bonding, PV Branch)
# ---------------------------------------------------------------------------


def test_w3_eac_sld_incoming_earthing_bonding_rcd_toggle_and_pv_branch(
    tmp_path: Path,
) -> None:
    apt_dxf = tmp_path / "apt_sld.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=apt_dxf)

    # Default TT includes upstream 100mA Type-S RCD
    p2_default = run_phase2_pipeline(apt_dxf)
    svg_def = render_sld_svg(p2_default.design)
    dxf_def = render_sld_dxf(p2_default.design)
    assert len(dxf_def.audit().errors) == 0
    dxf_def_txt = " ".join(str(t.dxf.text) for t in dxf_def.modelspace().query("TEXT"))

    for token in (
        "MAIN RCD 100mA TYPE-S",
        "SPD TYPE 2 (40kA)",
        "16mm² Cu Main Earth Conductor",
        "10mm² Main Bonding",
        "SPARE WAYS (MIN 20%)",
    ):
        assert token in svg_def, f"Missing '{token}' in SLD SVG"
    assert "MAIN RCD 100mA TYPE-S" in dxf_def_txt
    assert "SPD TYPE 2 (40kA)" in dxf_def_txt
    assert "SPARE WAYS" in dxf_def_txt

    # Override: TT insulated tail entry omits 100mA Type-S
    p2_no_rcd = run_phase2_pipeline(
        apt_dxf,
        overrides=DesignOverrides(include_tt_main_rcd_s=False),
    )
    svg_no_rcd = render_sld_svg(p2_no_rcd.design)
    assert "MAIN RCD 100mA TYPE-S" not in svg_no_rcd
    assert "TT INSULATED TAIL ENTRY" in svg_no_rcd

    # PV + EV Villa includes official March 2026 EAC PVS-S-1.2 Bidirectional Meter & R1/R2 branch
    villa_bldg = generate_benchmark_dxf(BenchmarkTemplate.VILLA_PV_EV)
    p2_villa = run_phase2_pipeline(villa_bldg)
    svg_villa = render_sld_svg(p2_villa.design)
    dxf_villa = render_sld_dxf(p2_villa.design)
    assert len(dxf_villa.audit().errors) == 0
    dxf_villa_txt = " ".join(str(t.dxf.text) for t in dxf_villa.modelspace().query("TEXT"))

    for token in (
        "HYBRID PV INVERTER",
        "EAC BIDIRECTIONAL METER",
        "EAC R1 CURTAILMENT + R2 ZERO-EXPORT RX",
        "LOCKABLE AC ISOLATOR + BIDIRECTIONAL RCBO",
    ):
        assert token in svg_villa
    assert "HYBRID PV" in dxf_villa_txt
    assert "EAC R1/R2 RX + AC ISOLATOR + BIDIR RCBO" in dxf_villa_txt


# ---------------------------------------------------------------------------
# W4: 6-Sheet Excel (Luminaire & Switching), Updated Cyprus BOQ & Cadastral Ε-ΔΔ-516
# ---------------------------------------------------------------------------


def test_w4_six_sheet_excel_cyprus_boq_parity_and_cadastral_eac_form(
    tmp_path: Path,
) -> None:
    villa_bldg = generate_benchmark_dxf(BenchmarkTemplate.VILLA_PV_EV)
    meta = EacProjectMetadata(
        district="Nicosia",
        municipality="Strovolos",
        sheet_plan="XXI/46",
        plot_no="912",
        building_permit_no="BP-2026-441",
        owner_name="M. Konstantinou",
        eac_tariff="01 Domestic",
        contractor_name="Cyprus Power Contractors Ltd",
        contractor_hmy_reg="HMY-A-0881",
    )
    p2 = run_phase2_pipeline(villa_bldg, overrides=DesignOverrides(eac_metadata=meta))
    outputs = export_phase3_deliverables(p2, tmp_path / "w4_pack")

    # 1. 6-sheet Excel workbook with Luminaire & Switching schedule
    wb = load_workbook(str(outputs["xlsx"]), data_only=False)
    assert wb.sheetnames == [
        "Load Schedule",
        "EAC & Review Flags",
        "Bill of Quantities",
        "EAC Application",
        "HMY 58 Test Schedule",
        "Luminaire & Switching",
    ]
    ws6 = wb["Luminaire & Switching"]
    assert ws6.max_row >= len(p2.design.building.rooms) + 4
    ws6_text = " ".join(
        str(ws6.cell(row=r, column=c).value or "")
        for r in range(1, ws6.max_row + 1)
        for c in range(1, ws6.max_column + 1)
    )
    assert "IP44" in ws6_text
    assert "1G2W" in ws6_text or "2G1W" in ws6_text

    # 2. Cyprus BOQ includes all Stage 4 accessories & PV AC kit
    boq = p2.design.boq
    assert boq is not None
    descs = " | ".join(it.description_en for it in boq.items)
    for expected_substr in (
        "100 mA Type S",
        "IP44 Bathroom Vanity Mirror Light",
        "Exhaust Fan",
        "45 A Double-Pole Cooker Control Unit",
        "20 A Double-Pole Water Heater Switch",
        "10 mm²",
        "PV AC Lockable Rotary Isolator",
    ):
        assert expected_substr in descs, f"Missing BOQ item '{expected_substr}'"

    # 3. Cadastral & Contractor metadata auto-populated in Ε-ΔΔ-516 HTML
    form_html = render_eac_application_form_html(p2)
    for val in (
        "Nicosia",
        "Strovolos",
        "XXI/46",
        "912",
        "BP-2026-441",
        "M. Konstantinou",
        "Cyprus Power Contractors Ltd",
        "HMY-A-0881",
    ):
        assert val in form_html


# ---------------------------------------------------------------------------
# W5: API Preview SVGs (E-100/E-101/E-102/E-201/E-301), Bundle ZIP & Web UI Controls
# ---------------------------------------------------------------------------


def test_w5_api_stage4_preview_bundle_and_ui_controls(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "w5.db")
    client = TestClient(app)

    # 1. Web UI exposes Stage 4 controls & Drawing Set switcher
    ui_html = client.get("/").text
    for token in (
        "ttMainRcdChk",
        "eacMetaBox",
        "btnSheetCombined",
        "btnSheetLighting",
        "btnSheetPower",
        "tabDetails",
        "selectDrawingSheet",
    ):
        assert token in ui_html

    # 2. Preview endpoint returns all 5 vector SVGs
    prev = client.post("/v1/solve/benchmark/apartment_3bed", json={})
    assert prev.status_code == 200
    payload = prev.json()
    for svg_field in ("floorplan_svg", "lighting_svg", "power_svg", "sld_svg", "details_svg"):
        assert "<svg" in payload[svg_field]
        root = ET.fromstring(payload[svg_field])
        assert root.tag.endswith("svg")
    assert "LEGEND (LIGHTING)" in payload["lighting_svg"]
    assert "LEGEND (POWER)" in payload["power_svg"]
    assert "DETAIL 1" in payload["details_svg"]

    # 3. Bundle ZIP contains full Stage 4 drawing pack + populated cadastral Ε-ΔΔ-516
    ov = {
        "include_tt_main_rcd_s": True,
        "eac_metadata": {
            "district": "Larnaca",
            "municipality": "Aradippou",
            "sheet_plan": "XL/22",
            "plot_no": "105",
            "contractor_name": "Kition Electric Ltd",
            "contractor_hmy_reg": "HMY-A-2044",
        },
    }
    zip_resp = client.post("/v1/solve/benchmark/apartment_3bed/bundle", json=ov)
    assert zip_resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(zip_resp.content))
    names = set(zf.namelist())
    for fname in (
        "electrical_layout.dxf",
        "E101_lighting_layout.dxf",
        "E101_lighting_layout.svg",
        "E102_power_layout.dxf",
        "E102_power_layout.svg",
        "E201_single_line_diagram.dxf",
        "E201_single_line_diagram.svg",
        "E301_details_notes.dxf",
        "E301_details_notes.svg",
        "single_line_diagram.dxf",
        "single_line_diagram.svg",
        "load_schedule.xlsx",
        "eac_application_form.html",
    ):
        assert fname in names, f"Missing '{fname}' in bundle ZIP"

    form_in_zip = zf.read("eac_application_form.html").decode("utf-8")
    assert "Larnaca" in form_in_zip
    assert "HMY-A-2044" in form_in_zip
    json_name = next(n for n in names if n.endswith(".json") and "spatial" not in n)
    _ = json.loads(zf.read(json_name).decode("utf-8"))


# ---------------------------------------------------------------------------
# W6: Real-World DWG Hardening (Single-Floor Underlay, Drafter Typos & Window-Layer Door Arcs)
# ---------------------------------------------------------------------------


def test_w6_real_world_dwg_underlay_preservation_aliases_and_fallback_door_arcs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from cyelec.ingest.room_classifier import classify_room_label
    from cyelec.models.building import RoomType
    from cyelec.models.electrical import ReviewFlag, ReviewSeverity
    from cyelec.standards_loader import load_standards

    # 1. Real-world drafter room labels, common typos & fractional imperial MTEXT stripping
    rt = load_standards().room_templates
    cases = [
        ("DRAWING ROOM\n12'-6\" x 15'-0\"", RoomType.LIVING_ROOM),
        ("DINNING\n13'-0\"X10'-0\"", RoomType.LIVING_ROOM),
        ("TV LOUNGE \n20'-9\"X13'-0\"", RoomType.LIVING_ROOM),
        ("LAUNDARY\n5'-7\"X5'-0\"", RoomType.UTILITY),
        ("DRESS\n5'4 1/2\"X6'-0\"", RoomType.STORAGE),
        ("PASSAGE \n5'-0\" WIDE", RoomType.CORRIDOR),
        ("CAR PORCH\n18'-0\"x12'-0\"", RoomType.VERANDA),
    ]
    for raw_lbl, expected_rt in cases:
        res = classify_room_label(raw_lbl, rt)
        assert res.normalized_type == expected_rt, (
            f"Expected {expected_rt} for {raw_lbl!r}, got {res.normalized_type}"
        )
        assert res.flag is None, f"Unexpected ambiguity flag on {raw_lbl!r}"

    # 2. Architectural R2000 DXF (matching LibreDWG dwg2dxf output) with open doorway
    #    sealed ONLY by a 90-deg ARC on Window layer + custom architect layer!
    src_dxf = tmp_path / "real_dwg_converted.dxf"
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 4  # mm
    msp = doc.modelspace()
    # Outer perimeter 8000 x 5000 mm
    for p0, p1 in (
        ((0, 0), (8000, 0)),
        ((8000, 0), (8000, 5000)),
        ((8000, 5000), (0, 5000)),
        ((0, 5000), (0, 0)),
        # Partition wall at x=4500 with a 1000 mm doorway opening from y=1000..2000
        ((4500, 0), (4500, 1000)),
        ((4500, 2000), (4500, 5000)),
    ):
        msp.add_line(p0, p1, dxfattribs={"layer": "Walls"})
    # 90-degree door swing arc drawn on Window layer (r=1000 mm seals doorway!)
    msp.add_arc(
        center=(4500, 1000),
        radius=1000.0,
        start_angle=0.0,
        end_angle=90.0,
        dxfattribs={"layer": "Window"},
    )
    msp.add_mtext(
        "DRAWING ROOM\\P14'-0\"x16'-0\"",
        dxfattribs={"layer": "Text", "insert": (2200, 2500)},
    )
    msp.add_mtext(
        "LAUNDARY\\P10'-0\"x12'-0\"",
        dxfattribs={"layer": "Text", "insert": (6200, 2500)},
    )
    # Custom architect layer that MUST survive in E-100, E-101, and E-102 DXFs!
    msp.add_lwpolyline(
        [(500, 500), (1500, 500), (1500, 1200), (500, 1200)],
        close=True,
        dxfattribs={"layer": "A-CUSTOM-DWG-STAIRS"},
    )
    doc.saveas(str(src_dxf))

    def fake_dwg_convert(
        dwg_path: Path, out_dir: Path, *, timeout_s: float = 60.0
    ) -> tuple[Path, ReviewFlag]:
        _ = timeout_s
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{dwg_path.stem}.dxf"
        out.write_bytes(src_dxf.read_bytes())
        return (
            out,
            ReviewFlag(
                code="DWG_CONVERTED_VIA_SUBPROCESS",
                severity=ReviewSeverity.INFO,
                category="geometry",
                message="Mock single-floor DWG converted.",
            ),
        )

    monkeypatch.setattr("cyelec.ingest.dwg_bridge.convert_dwg_to_dxf", fake_dwg_convert)
    monkeypatch.setattr("cyelec.pipeline.convert_dwg_to_dxf", fake_dwg_convert)

    app = create_app(db_path=tmp_path / "w6.db")
    client = TestClient(app)
    resp = client.post(
        "/v1/solve/dxf/bundle",
        files={"file": ("cyprus_villa.dwg", b"AC1032FAKE_DWG", "application/acad")},
    )
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    ds = json.loads(zf.read("design_result.json").decode("utf-8"))
    # Fallback Window-layer 90-deg door arc cleanly separated 2 distinct rooms!
    room_types = {r["normalized_type"] for r in ds["building"]["rooms"]}
    assert room_types == {"living_room", "utility"}

    # Original architect .dwg layer MUST be preserved in pristine AC1032 R2018 DXFs!
    for dxf_name in (
        "electrical_layout.dxf",
        "E101_lighting_layout.dxf",
        "E102_power_layout.dxf",
    ):
        p = tmp_path / dxf_name
        p.write_bytes(zf.read(dxf_name))
        out_doc = ezdxf.readfile(str(p))
        assert out_doc.dxfversion == "AC1032"
        assert len(out_doc.audit().errors) == 0
        layers_in_msp = {e.dxf.layer for e in out_doc.modelspace()}
        assert "A-CUSTOM-DWG-STAIRS" in layers_in_msp, (
            f"Architect DWG underlay lost in {dxf_name}!"
        )

