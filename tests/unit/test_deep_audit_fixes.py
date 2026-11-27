"""Deep UI & engineering results audit regression tests (Bugs #1–#20)."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from cyelec.api.app import create_app
from cyelec.models.electrical import (
    CircuitCategory,
    DesignOverrides,
    ResidentialSystemsBrief,
)
from cyelec.models.spatial import DeviceType, Phase2DesignResult
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.details_dxf import render_details_notes_svg
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.render.riser_dxf import render_riser_and_db_svg
from cyelec.render.site_plan_dxf import render_site_plan_svg
from cyelec.render.sld import render_sld_dxf, render_sld_svg
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def _solve_benchmark(
    tmp_path: Path,
    tmpl: BenchmarkTemplate,
    *,
    brief: ResidentialSystemsBrief | None = None,
    pv_kva: float | None = None,
) -> tuple[Path, Phase2DesignResult]:
    dxf = tmp_path / f"{tmpl.value}.dxf"
    generate_benchmark_dxf(tmpl, out_dxf=dxf)
    overrides = (
        DesignOverrides(pv_capacity_kva=pv_kva, residential_brief=brief)
        if (brief is not None or pv_kva is not None)
        else None
    )
    res = run_phase2_pipeline(dxf, overrides=overrides)
    return dxf, res


# ---------------------------------------------------------------------------
# Wave 1 — Core Engineering & Geometry Fixes (Bugs #1-#6, #13)
# ---------------------------------------------------------------------------


def test_bug01_villa_2storey_no_duplicate_floor_shell_area(tmp_path: Path) -> None:
    """Bug #1: VILLA_2STOREY DXF ingest must not create duplicate 108 m² outer shell room."""
    _, res = _solve_benchmark(tmp_path, BenchmarkTemplate.VILLA_2STOREY)
    total_area = sum(r.area_m2 for r in res.design.building.rooms)
    assert total_area == pytest.approx(228.0, abs=0.5)
    assert len(res.design.building.rooms) == 12
    assert all(r.area_m2 < 60.0 for r in res.design.building.rooms)


def test_bug02_commercial_office_gets_full_elv_and_fire_devices(tmp_path: Path) -> None:
    """Bug #2: COMMERCIAL_OFFICE must populate E-103 ELV + BS 5839 fire detection."""
    _, res = _solve_benchmark(tmp_path, BenchmarkTemplate.COMMERCIAL_OFFICE)
    dtypes = {d.device_type for d in res.spatial.devices}
    assert DeviceType.TELECOM_CABINET in dtypes
    assert DeviceType.DATA_RJ45 in dtypes
    assert DeviceType.WIFI_AP in dtypes
    assert DeviceType.SMOKE_DETECTOR in dtypes
    assert DeviceType.CCTV_CAMERA in dtypes
    assert DeviceType.ALARM_PANEL in dtypes

    elv_devs = [
        d
        for d in res.spatial.devices
        if d.device_type
        in {
            DeviceType.TELECOM_CABINET,
            DeviceType.DATA_RJ45,
            DeviceType.WIFI_AP,
            DeviceType.SMOKE_DETECTOR,
            DeviceType.HEAT_DETECTOR,
            DeviceType.CCTV_CAMERA,
            DeviceType.ALARM_PANEL,
        }
    ]
    assert len(elv_devs) >= 12
    assert all(d.circuit_id is not None for d in elv_devs)


@pytest.mark.parametrize(
    "tmpl",
    [
        BenchmarkTemplate.APARTMENT_3BED,
        BenchmarkTemplate.VILLA_PV_EV,
        BenchmarkTemplate.VILLA_2STOREY,
        BenchmarkTemplate.COMMERCIAL_OFFICE,
    ],
)
def test_bug03_zero_stacked_wall_or_ceiling_devices(
    tmp_path: Path, tmpl: BenchmarkTemplate
) -> None:
    """Bug #3: Post-placement de-collision prevents stacked wall/ceiling devices (<100 mm)."""
    brief = (
        ResidentialSystemsBrief(
            include_pool=True,
            include_knx=True,
            ev_charger_kw=11.0,
            building_height_m=12.0,
        )
        if tmpl == BenchmarkTemplate.VILLA_2STOREY
        else None
    )
    _, res = _solve_benchmark(tmp_path, tmpl, brief=brief)
    room_floor = {r.id: r.floor for r in res.design.building.rooms}
    devs = res.spatial.devices
    stacked: list[str] = []
    for i in range(len(devs)):
        di = devs[i]
        fi = room_floor.get(di.room_id, "")
        for j in range(i + 1, len(devs)):
            dj = devs[j]
            if room_floor.get(dj.room_id, "") != fi:
                continue
            dist = math.hypot(
                di.location_mm.x_mm - dj.location_mm.x_mm,
                di.location_mm.y_mm - dj.location_mm.y_mm,
            )
            if dist < 100.0:
                stacked.append(f"{di.device_type.value}+{dj.device_type.value}@{dist:.0f}mm")
    assert stacked == []


@pytest.mark.parametrize(
    "tmpl",
    [
        BenchmarkTemplate.APARTMENT_3BED,
        BenchmarkTemplate.VILLA_PV_EV,
        BenchmarkTemplate.VILLA_2STOREY,
        BenchmarkTemplate.COMMERCIAL_OFFICE,
    ],
)
def test_bug04_all_rooms_meet_cibse_target_lux(
    tmp_path: Path, tmpl: BenchmarkTemplate
) -> None:
    """Bug #4: Luminaire sizing & lumen_checks share one formula so 100% of rooms pass lux."""
    _, res = _solve_benchmark(tmp_path, tmpl)
    assert len(res.design.lumen_checks) > 0
    non_compliant = [
        f"{lc.room_label}({lc.achieved_lux:.0f}/{lc.target_lux}lx)"
        for lc in res.design.lumen_checks
        if not lc.compliant
    ]
    assert non_compliant == []


def test_bug05_ring_final_cable_lengths_realistic(tmp_path: Path) -> None:
    """Bug #5: Ring final routed lengths must not double-count full drop height per socket."""
    _, res = _solve_benchmark(tmp_path, BenchmarkTemplate.APARTMENT_3BED)
    rings = [c for c in res.design.circuits if c.spec.category == CircuitCategory.SOCKET_RING]
    assert len(rings) >= 1
    for rc in rings:
        assert rc.spec.length_m < 100.0, f"{rc.spec.circuit_id} length inflated: {rc.spec.length_m}"


def test_bug06_lps_required_consistent_type12_spd_everywhere(tmp_path: Path) -> None:
    """Bug #6: When IEC 62305-2 requires LPS, SLD, E-301, and BOQ all specify Type 1+2 SPD."""
    brief = ResidentialSystemsBrief(building_height_m=14.0, include_pool=True)
    _, res = _solve_benchmark(
        tmp_path, BenchmarkTemplate.VILLA_2STOREY, brief=brief, pv_kva=6.0
    )
    assert res.design.lightning_risk is not None
    assert res.design.lightning_risk.lps_required is True

    # BOQ switchgear item
    assert res.design.boq is not None
    spd_items = [it for it in res.design.boq.items if "SPD" in it.description_en]
    assert any("Type 1+2" in it.description_en for it in spd_items)

    # SLD SVG & DXF
    sld_svg = render_sld_svg(res.design)
    assert "SPD TYPE 1+2" in sld_svg
    sld_doc = render_sld_dxf(res.design)
    dxf_texts = [str(e.dxf.text) for e in sld_doc.modelspace().query("TEXT")]
    assert any("SPD TYPE 1+2" in t for t in dxf_texts)

    # E-301 Details SVG
    e301_svg = render_details_notes_svg(res.design)
    assert "TYPE 1+2 SPD" in e301_svg


def test_bug13_no_raw_none_in_eac_forms_or_excel(tmp_path: Path) -> None:
    """Bug #13: 0 kVA PV / 0 kW EV render 'Not installed', never raw 'None'."""
    dxf, res = _solve_benchmark(tmp_path, BenchmarkTemplate.APARTMENT_3BED)
    out_dir = tmp_path / "pack"
    export_phase3_deliverables(res, out_dir, source_dxf_path=dxf)

    eac_html = (out_dir / "eac_application_form.html").read_text(encoding="utf-8")
    assert ">None<" not in eac_html
    assert "Not installed (0 kVA)" in eac_html
    assert "Not installed (0 kW)" in eac_html

    wb = load_workbook(out_dir / "load_schedule.xlsx", data_only=False)
    ws = wb["EAC Application"]
    vals = [str(cell.value) for row in ws.iter_rows() for cell in row if cell.value is not None]
    assert "None" not in vals
    assert any("Not installed (0 kVA)" in v for v in vals)
    assert any("Not installed (0 kW)" in v for v in vals)


# ---------------------------------------------------------------------------
# Wave 2 — Drawing Sheet Visual & Layout Fixes (Bugs #7-#12)
# ---------------------------------------------------------------------------


def test_bug07_e301_scoped_css_white_headers_no_column_overflow(tmp_path: Path) -> None:
    """Bug #7: E-301 uses scoped .cy-e301 CSS, white header classes, and clean 2-col bounds."""
    _, res = _solve_benchmark(tmp_path, BenchmarkTemplate.VILLA_2STOREY)
    svg = render_details_notes_svg(res.design)
    assert 'class="cy-e301"' in svg
    assert ".cy-e301 .wtitle" in svg and "fill: #ffffff;" in svg
    assert ".cy-e301 .th" in svg
    assert not re.search(r"^\s*text\s*\{", svg, flags=re.MULTILINE)

    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    for el in root.iter(f"{ns}text"):
        txt = "".join(el.itertext()).strip()
        if not txt:
            continue
        x = float(el.attrib.get("x", "0"))
        y = float(el.attrib.get("y", "0"))
        anchor = el.attrib.get("text-anchor", "start")
        cls = el.attrib.get("class", "")
        fs = 15.0 if cls == "wtitle" else (11.5 if cls in ("sec", "d1-h", "d2-h", "d3-h") else 9.6)
        w = len(txt) * fs * 0.52
        x2 = x + w / 2 if anchor == "middle" else (x if anchor == "end" else x + w)
        assert x2 <= 1312.0, f"E-301 text extends past right border: {txt!r} (x2={x2:.1f})"
        if x < 640 and 72 < y < 790:
            assert x2 <= 650.0, f"E-301 left col overflows into Detail pane: {txt!r} (x2={x2:.1f})"


def test_bug08_sld_contiguous_subboards_no_overlap(tmp_path: Path) -> None:
    """Bug #8: Multi-board SLD groups circuits contiguously so sub-board headers never collide."""
    brief = ResidentialSystemsBrief(include_pool=True, include_knx=True, ev_charger_kw=11.0)
    _, res = _solve_benchmark(
        tmp_path, BenchmarkTemplate.VILLA_2STOREY, brief=brief, pv_kva=6.0
    )
    svg = render_sld_svg(res.design)
    assert 'class="cy-e201"' in svg
    assert not re.search(r"^\s*text\s*\{", svg, flags=re.MULTILINE)

    # Check 2D bounding boxes of ALL sub-board header + direct feeder boxes have zero overlap
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    sub_boxes: list[tuple[float, float, float, float]] = []
    for rect in root.iter(f"{ns}rect"):
        y = float(rect.attrib.get("y", "0"))
        h = float(rect.attrib.get("height", "0"))
        if rect.attrib.get("rx") == "4" and y > 280 and h in (24.0, 44.0):
            x = float(rect.attrib["x"])
            w = float(rect.attrib["width"])
            sub_boxes.append((x, y, x + w, y + h))
    assert len(sub_boxes) >= 2
    for i in range(len(sub_boxes)):
        ax0, ay0, ax1, ay1 = sub_boxes[i]
        for j in range(i + 1, len(sub_boxes)):
            bx0, by0, bx1, by1 = sub_boxes[j]
            overlap = not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)
            assert not overlap, f"Overlapping SLD sub-boards: {sub_boxes[i]} vs {sub_boxes[j]}"

    # Check DXF sub-board labels also do not overlap
    sld_doc = render_sld_dxf(res.design)
    hdr_texts = [
        (float(t.dxf.insert.x), str(t.dxf.text))
        for t in sld_doc.modelspace().query("TEXT")
        if "MDB->DB-" in str(t.dxf.text)
    ]
    assert len(hdr_texts) >= 2
    hdr_texts.sort()
    assert hdr_texts[1][0] - hdr_texts[0][0] > 40.0


def test_bug09_no_interfloor_arcs_on_multi_storey_svg(tmp_path: Path) -> None:
    """Bug #9: E-101/E-102/E-103 SVGs must not draw loop or switch arcs across the floor gap."""
    brief = ResidentialSystemsBrief(include_pool=True, include_knx=True, ev_charger_kw=11.0)
    _, res = _solve_benchmark(tmp_path, BenchmarkTemplate.VILLA_2STOREY, brief=brief)
    for disc in ("lighting", "power", "elv"):
        svg = render_floorplan_svg(res, discipline=disc)
        assert 'class="cy-plan"' in svg
        assert not re.search(r"^\s*text\s*\{", svg, flags=re.MULTILINE)
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        for path in root.iter(f"{ns}path"):
            d = path.attrib.get("d", "")
            m = re.match(
                r"M\s+([0-9.\-]+),([0-9.\-]+)\s+Q\s+[0-9.\-]+,[0-9.\-]+\s+([0-9.\-]+),", d
            )
            if m:
                x1, x2 = float(m.group(1)), float(m.group(3))
                # GF sits on the left half (<520px) and 1F on the right half (>600px)
                crosses_gap = (x1 < 520 and x2 > 600) or (x2 < 520 and x1 > 600)
                assert not crosses_gap, f"Inter-floor arc detected on {disc}: {d}"


def test_bug11_riser_and_site_scoped_css_and_clean_meter_label(tmp_path: Path) -> None:
    """Bug #11: E-202 fixes truncated meter label & scoped CSS; E-104 wraps LPS & scopes CSS."""
    brief = ResidentialSystemsBrief(building_height_m=14.0, include_pool=True)
    _, res = _solve_benchmark(
        tmp_path, BenchmarkTemplate.VILLA_2STOREY, brief=brief, pv_kva=6.0
    )
    riser_svg = render_riser_and_db_svg(res)
    assert 'class="cy-e202"' in riser_svg
    assert not re.search(r"^\s*text\s*\{", riser_svg, flags=re.MULTILINE)
    assert "(3x100A (6" not in riser_svg
    assert "(3x80A (5" not in riser_svg
    assert "EAC METER (3x" in riser_svg

    site_svg = render_site_plan_svg(res)
    assert 'class="cy-e104"' in site_svg
    assert not re.search(r"^\s*text\s*\{", site_svg, flags=re.MULTILINE)
    root = ET.fromstring(site_svg)
    ns = "{http://www.w3.org/2000/svg}"
    vb_w = float(root.attrib["viewBox"].split()[2])
    for el in root.iter(f"{ns}text"):
        txt = "".join(el.itertext()).strip()
        if not txt or el.attrib.get("text-anchor") == "middle":
            continue
        x = float(el.attrib.get("x", "0"))
        assert x + len(txt) * 5.2 <= vb_w + 5.0, f"E-104 overflow text: {txt!r}"


def test_bug12_dxf_text_seeded_against_underlay_labels(tmp_path: Path) -> None:
    """Bug #12: E-101/E-102 DXF exports avoid colliding E-* labels with architectural labels."""
    dxf, res = _solve_benchmark(tmp_path, BenchmarkTemplate.APARTMENT_3BED)
    out_dir = tmp_path / "pack"
    outputs = export_phase3_deliverables(res, out_dir, source_dxf_path=dxf)
    for key in ("e101_lighting_dxf", "e102_power_dxf"):
        doc = ezdxf.readfile(str(outputs[key]))
        msp = doc.modelspace()
        e_boxes: list[tuple[float, float, float, float, str]] = []
        a_boxes: list[tuple[float, float, float, float, str]] = []
        for t in msp.query("TEXT"):
            lyr = str(t.dxf.layer)
            txt = str(t.dxf.text).strip()
            if not txt:
                continue
            x, y = float(t.dxf.insert.x), float(t.dxf.insert.y)
            h = max(float(t.dxf.height), 40.0)
            w = max(len(txt) * h * 0.58, h)
            box = (x, y, x + w, y + h, txt)
            if lyr.startswith("E-"):
                e_boxes.append(box)
            elif lyr.startswith("A-"):
                a_boxes.append(box)
        collisions = 0
        for ax1, ay1, ax2, ay2, _ in e_boxes:
            for bx1, by1, bx2, by2, _ in a_boxes:
                if not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1):
                    collisions += 1
        assert collisions == 0, f"{key} had {collisions} E-* vs A-* TEXT collisions"


# ---------------------------------------------------------------------------
# Wave 3 — Workbench UI / UX Fixes (Bugs #14-#20)
# ---------------------------------------------------------------------------


def test_bugs14_to_20_workbench_ui_markup_and_invariants() -> None:
    """Bugs #14-#20: Workbench UI includes responsive CSS, zoom, Clear File, Stage 5 & BOQ."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # Preserved existing test invariants
    assert "Relocate DB1 on Floorplan" in html
    assert "briefBox" in html
    assert "btnSheetElv" in html
    assert "anchorTargetSel" in html

    # Bug #14: .row2 grid & responsive breakpoints
    assert ".row2 {" in html
    assert "@media (max-width: 820px)" in html
    assert "@media (max-width: 480px)" in html

    # Bug #15: Clear uploaded file & override reset
    assert 'id="btnClearFiles"' in html
    assert "function clearUploadedFiles(" in html
    assert "function clearDesignOverrides(" in html

    # Bug #16: Sticky sidebar actions & inline status
    assert "sidebar-actions" in html
    assert 'id="btnRunSolve"' in html
    assert 'id="btnDownloadZip"' in html
    assert "function setStatus(" in html

    # Bug #17: SVG zoom / fit / fullscreen / Save SVG toolbars on all 5 sheets
    assert "function zoomSvg(" in html
    assert 'id="fsModal"' in html
    for box_id in ("floorplanSvg", "siteSvg", "sldSvg", "riserSvg", "detailsSvg"):
        assert f"zoomSvg('{box_id}'" in html
        assert f"openFullscreenSvg('{box_id}'" in html

    # Bug #18: Pending circuit edits bar & dynamic flags badge
    assert 'id="pendingEditsBar"' in html
    assert 'id="tabBtnFlags"' in html

    # Bug #19: Stage 5 engineering summary & 10-section grouped BOQ
    assert 'id="stage5EngSummary"' in html
    assert "function renderStage5EngineeringSummary(" in html
    assert "boq-sec-row" in html
    assert "SEC 01 — Switchgear" in html
    assert "luminaire_count" in html
    assert "is_tender_blank_rates" in html

    # Bug #20: Auth Enter-key, gate banner, safe team reset delegation
    assert 'id="authGateBanner"' in html
    assert "data-reset-uid=" in html
    assert "t.boq_rate_overrides.vat_rate" in html


def test_e2e_bug22_carry_forward_preserves_all_stage4_stage5_overrides() -> None:
    """E2E operational catch: carry_forward_overrides must preserve brief, 4 anchors & metadata."""
    from cyelec.models.building import Point2D
    from cyelec.models.electrical import BoqRateOverrides, EacProjectMetadata
    from cyelec.saas.revisions import carry_forward_overrides

    rev1 = DesignOverrides(
        db_location_override_mm=Point2D(x_mm=4200.0, y_mm=3800.0),
        meter_location_override_mm=Point2D(x_mm=100.0, y_mm=-900.0),
        earth_pit_location_override_mm=Point2D(x_mm=500.0, y_mm=-900.0),
        telecom_location_override_mm=Point2D(x_mm=3100.0, y_mm=2200.0),
        prospective_fault_current_ka=8.5,
        boq_rate_overrides=BoqRateOverrides(vat_rate=0.05),
        eac_metadata=EacProjectMetadata(district="Limassol", plot_no="418"),
        apply_corridor_grouping=True,
    )
    rev2 = DesignOverrides(
        pv_capacity_kva=6.0,
        tender_boq_blank_rates=True,
        residential_brief=ResidentialSystemsBrief(
            hvac_mode="heat_pump_ufh",
            heat_pump_kw=6.0,
            include_pool=True,
            include_knx=True,
            ev_charger_kw=11.0,
            building_height_m=12.0,
        ),
    )
    merged = carry_forward_overrides(rev1, rev2)
    assert merged is not None
    assert merged.db_location_override_mm == Point2D(x_mm=4200.0, y_mm=3800.0)
    assert merged.meter_location_override_mm == Point2D(x_mm=100.0, y_mm=-900.0)
    assert merged.earth_pit_location_override_mm == Point2D(x_mm=500.0, y_mm=-900.0)
    assert merged.telecom_location_override_mm == Point2D(x_mm=3100.0, y_mm=2200.0)
    assert merged.prospective_fault_current_ka == 8.5
    assert merged.boq_rate_overrides is not None
    assert merged.boq_rate_overrides.vat_rate == 0.05
    assert merged.eac_metadata is not None
    assert merged.eac_metadata.plot_no == "418"
    assert merged.apply_corridor_grouping is True
    assert merged.pv_capacity_kva == 6.0
    assert merged.tender_boq_blank_rates is True
    assert merged.residential_brief is not None
    assert merged.residential_brief.include_pool is True
    assert merged.residential_brief.include_knx is True
    assert merged.residential_brief.ev_charger_kw == 11.0

