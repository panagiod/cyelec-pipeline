"""Regression: every drawing label is rendered in full (no truncation / ellipsis) in EN, EL and
bilingual sheets, long project names & branding wrap instead of being cut, and the masked /
hoisted label approach keeps floorplan text free of overlaps."""

from __future__ import annotations

import html
import re
from functools import cache

import pytest

from cyelec.models.building import BenchmarkTemplate
from cyelec.models.electrical import (
    DesignOverrides,
    EacProjectMetadata,
    ReportLanguage,
    ResidentialSystemsBrief,
    TenantBranding,
)
from cyelec.models.spatial import Phase2DesignResult
from cyelec.pipeline import run_phase2_pipeline
from cyelec.render.board_designs import render_board_designs_svg
from cyelec.render.details_dxf import render_details_notes_dxf, render_details_notes_svg
from cyelec.render.floorplan_svg import render_floorplan_svg
from cyelec.render.label_layout import count_svg_text_bbox_overlaps
from cyelec.render.layout_dxf import render_electrical_layout_dxf
from cyelec.render.riser_dxf import render_riser_and_db_dxf, render_riser_and_db_svg
from cyelec.render.selectivity_svg import render_selectivity_curves_svg
from cyelec.render.site_plan_dxf import render_site_plan_dxf, render_site_plan_svg
from cyelec.render.sld import render_sld_svg
from cyelec.render.text_fit import (
    dxf_char_budget,
    hoist_svg_text_to_top,
    svg_text_mask,
    text_width_px,
    wrap_text_chars,
    wrap_text_to_width,
)
from cyelec.synth.benchmarks import generate_benchmark_dxf

LONG_NAME = (
    "Κατοικία Παπαδόπουλου – Ανακαίνιση & Επέκταση Ορόφου, Λεμεσός (Germasogeia Hills Phase II)"
)
BRAND = TenantBranding(
    firm_name="Papadopoulos & Christodoulou Consulting Electrical Engineers Ltd",
    engineer_name="Dr. Konstantinos Papadopoulos-Christodoulou",
    etek_reg_no="ETEK-REG-0012345-ELEC-2026",
)
EAC = EacProjectMetadata(
    district="Λεμεσός / Limassol",
    municipality="Γερμασόγεια / Germasogeia",
    sheet_plan="54/12",
    plot_no="1234/5 & 1235/1",
    owner_name="Ανδρέας Παπαδόπουλος",
    contractor_name="Christodoulou Electrical Installations Ltd",
)

_TEXT_RE = re.compile(r"<text\b[^>]*>(.*?)</text>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\u00a0", " ")).strip()


def _svg_words(svg: str) -> str:
    """All <text> content joined with spaces (so wrapped lines re-join into the full label)."""
    parts = [html.unescape(_TAG_RE.sub("", m)) for m in _TEXT_RE.findall(svg)]
    return _norm(" ".join(parts))


def _contains_wrapped(haystack: str, needle: str) -> bool:
    """True when every word of ``needle`` appears in order inside ``haystack``."""
    pos = 0
    for word in _norm(needle).split(" "):
        idx = haystack.find(word, pos)
        if idx < 0:
            return False
        pos = idx + len(word)
    return True


@cache
def _villa(lang: ReportLanguage) -> Phase2DesignResult:
    v2 = generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY)
    v2 = v2.model_copy(update={"project_name": LONG_NAME})
    brief = ResidentialSystemsBrief(
        include_pool=True,
        include_knx=True,
        ev_charger_kw=11.0,
        battery_storage_kwh=10.0,
        include_eps_backup=True,
    )
    ov = DesignOverrides(
        pv_capacity_kva=10.0,
        residential_brief=brief,
        report_language=lang,
        branding=BRAND,
        eac_metadata=EAC,
    )
    return run_phase2_pipeline(v2, overrides=ov, project_name=LONG_NAME)


def _sheets(p2: Phase2DesignResult) -> dict[str, str]:
    return {
        "E100": render_floorplan_svg(p2, discipline="combined"),
        "E101": render_floorplan_svg(p2, discipline="lighting"),
        "E102": render_floorplan_svg(p2, discipline="power"),
        "E103": render_floorplan_svg(p2, discipline="elv"),
        "E104": render_site_plan_svg(p2),
        "E201": render_sld_svg(p2.design),
        "E202": render_riser_and_db_svg(p2),
        "E203": render_selectivity_curves_svg(p2.design),
        "E204": render_board_designs_svg(p2),
        "E301": render_details_notes_svg(p2),
    }


# ---------------------------------------------------------------------------- text_fit helpers


@pytest.mark.parametrize(
    "text",
    [
        LONG_NAME,
        "Supercalifragilisticexpialidocious-Unbreakable-Token-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "MDB 63A 4P Type C MCB/MCCB + 100mA Type S Selective RCD",
        "",
    ],
)
@pytest.mark.parametrize("max_w", [60.0, 140.0, 400.0])
def test_wrap_text_to_width_never_drops_characters(text: str, max_w: float) -> None:
    lines = wrap_text_to_width(text, max_w, 9.0)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")
    for ln in lines:
        # A single unbreakable glyph may exceed tiny budgets; everything else must fit.
        assert len(ln) == 1 or text_width_px(ln, 9.0) <= max_w + 0.5


def test_wrap_text_chars_and_dxf_budget() -> None:
    lines = wrap_text_chars(LONG_NAME, 24)
    assert _norm(" ".join(lines)) == _norm(LONG_NAME)
    assert all(len(ln) <= 24 for ln in lines)
    assert dxf_char_budget(10.0, 5.0) == 8  # floor of 8 characters
    assert dxf_char_budget(142.0, 2.1) >= 60


def test_hoist_moves_masks_then_text_last() -> None:
    mask = svg_text_mask("Kitchen", 10, 20, 8.0, anchor="middle")
    assert 'class="txt-mask' in mask
    svg = "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg">',
            '  <text x="1" y="2">Label</text>',
            f"  {mask}",
            '  <line x1="0" y1="0" x2="9" y2="9" />',
            "</svg>",
        ]
    )
    out = hoist_svg_text_to_top(svg).splitlines()
    assert out[-1] == "</svg>"
    assert "<text" in out[-2] and "txt-mask" in out[-3]
    assert out.index('  <line x1="0" y1="0" x2="9" y2="9" />') < len(out) - 3


# ---------------------------------------------------------------------------- full sheets


@pytest.mark.parametrize("lang", list(ReportLanguage), ids=lambda x: x.value)
def test_long_project_name_and_branding_render_in_full(lang: ReportLanguage) -> None:
    p2 = _villa(lang)
    sheets = _sheets(p2)
    for name, svg in sheets.items():
        assert "\u2026" not in svg, f"{name} contains an ellipsis (truncated label)"
        words = _svg_words(svg)
        if name in {"E101", "E102", "E103", "E104", "E202", "E203", "E301"}:
            assert _contains_wrapped(words, LONG_NAME), f"{name} project name truncated"
    e301 = _svg_words(sheets["E301"])
    for field in (BRAND.firm_name, BRAND.engineer_name, BRAND.etek_reg_no):
        assert field and _contains_wrapped(e301, field), f"E-301 title block cut: {field}"
    for disc in ("E100", "E101", "E102", "E103"):
        assert count_svg_text_bbox_overlaps(sheets[disc]) == 0, disc


def test_riser_lists_every_board_and_din_row_item_in_full() -> None:
    p2 = _villa(ReportLanguage.EN)
    svg = render_riser_and_db_svg(p2)
    words = _svg_words(svg).upper()
    assert "MORE)" not in words  # old "(+N more)" truncation suffix is gone
    layouts = p2.design.din_rail_boards
    assert len(layouts) >= 4, "villa benchmark must produce main + secondary boards"
    for lay in layouts:
        assert _contains_wrapped(words, lay.board_id.upper())
        for row in lay.rows:
            for item in row.items:
                assert _contains_wrapped(words, item.upper()), item


@pytest.mark.parametrize("lang", list(ReportLanguage), ids=lambda x: x.value)
def test_dxf_sheets_audit_clean_with_long_names(lang: ReportLanguage) -> None:
    p2 = _villa(lang)
    for doc in (
        render_riser_and_db_dxf(p2),
        render_site_plan_dxf(p2),
        render_details_notes_dxf(p2),
        render_electrical_layout_dxf(p2),
    ):
        assert len(doc.audit().errors) == 0
        texts = [
            str(e.dxf.text) for e in doc.modelspace() if e.dxftype() in {"TEXT", "MTEXT"}
        ]
        assert not any("\u2026" in t for t in texts)
