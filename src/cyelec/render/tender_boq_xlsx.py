"""Full 10-Section Cyprus Electrical Tender BOQ Excel Workbook (Priced + Blank Contractor Bid)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from cyelec.models.electrical import BoqItem, ElectricalDesignResult

_NAVY_FILL = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
_SEC_FILL = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
_SUB_FILL = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
_INPUT_FILL = PatternFill(start_color="ECFDF5", end_color="ECFDF5", fill_type="solid")
_TOTAL_FILL = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")

_TITLE_FONT = Font(name="Calibri", size=13, bold=True, color="1B365D")
_HEADER_FONT = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
_SEC_FONT = Font(name="Calibri", size=10, bold=True, color="1B365D")
_BOLD_FONT = Font(name="Calibri", size=10, bold=True)
_BODY_FONT = Font(name="Calibri", size=10)
_THIN = Side(border_style="thin", color="CBD5E1")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("01", "Switchgear & Distribution Boards", "Πίνακες Διανομής & Υλικό Προστασίας"),
    ("02", "Cables & Containment", "Καλώδια, Σωληνώσεις & Εσχάρες"),
    ("03", "Lighting & Switching", "Φωτισμός & Διακόπτες"),
    ("04", "Power Outlets & Fixed Accessories", "Πρίζες Ισχύος & Σταθερές Συσκευές"),
    ("05", "Mechanical, HVAC, Shutters & Plant", "Η/Μ Εγκαταστάσεις, Κλιματισμός, Ρολά & Αντλίες"),
    ("06", "ELV, Security, Intercom & Life Safety", "Ασθενή Ρεύματα, Ασφάλεια & Πυρανίχνευση"),
    ("07", "Earthing, Bonding & Lightning Protection", "Γείωση, Ισοδυναμική & Αντικεραυνική"),
    ("08", "External Site Works & Trenching", "Εξωτερικά Έργα & Υπόγειες Εκσκαφές"),
    ("09", "Smart Home / KNX Automation", "Έξυπνο Σπίτι / Αυτοματισμός KNX"),
    ("10", "Testing, Commissioning & As-Builts (HMY 58)", "Δοκιμές, Πιστοποίηση ΗΜΥ 58 & As-Built"),
)


def _map_item_section(item: BoqItem) -> str:
    """Assign each BoqItem deterministically to one of the 10 Cyprus MEP Tender Sections."""
    cat = item.category.lower()
    desc = item.description_en.lower()
    if "switchgear" in cat or "board" in cat:
        return "01"
    if "cable" in cat or "containment" in cat or "conduit" in cat:
        return "02"
    if "lighting" in cat or any(
        k in desc
        for k in (
            "luminaire",
            "switch",
            "mirror light",
            "dimmer",
            "pendant",
            "emergency",
            "exhaust fan",
        )
    ):
        if "water heater" in desc or "cooker" in desc or "shutter" in desc:
            return "05" if "shutter" in desc else "04"
        return "03"
    if (
        "mechanical" in cat
        or "plant" in cat
        or any(
            k in desc
            for k in (
                "split a/c",
                "shutter",
                "heat pump",
                "vrv",
                "pump",
                "gate",
                "pool",
                "ev charger",
                "pv ac",
            )
        )
    ):
        return "05"
    if "elv" in cat or "security" in cat or "fire alarm" in desc or "smoke" in desc:
        return "06"
    if "earthing" in cat or "lightning" in desc or "bonding" in desc:
        return "07"
    if "site" in cat or "trench" in desc or "bollard" in desc:
        return "08"
    if "knx" in cat:
        return "09"
    if "testing" in cat or "commissioning" in cat:
        return "10"
    return "04"


def _autosize(ws: Worksheet) -> None:
    widths = {1: 12, 2: 52, 3: 48, 4: 9, 5: 12, 6: 16, 7: 18}
    for col_idx, w in widths.items():
        if col_idx <= ws.max_column:
            ws.column_dimensions[get_column_letter(col_idx)].width = w


def _populate_boq_sheet(
    ws: Worksheet,
    result: ElectricalDesignResult,
    grouped: dict[str, list[BoqItem]],
    *,
    blank_rates: bool,
) -> dict[str, int]:
    """Populate a 10-section formula-driven BOQ worksheet and return section subtotal row map."""
    ws.views.sheetView[0].showGridLines = True
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4

    mode_lbl = (
        "CONTRACTOR TENDER SCHEDULE (UNPRICED — ENTER RATES IN GREEN CELLS)"
        if blank_rates
        else "ENGINEER'S COST ESTIMATE (PRICED — CYPRUS 2026 INDICATIVE EUR)"
    )
    ws["A1"] = f"CYPRUS 10-SECTION ELECTRICAL TENDER BOQ — {result.building.project_name}"
    ws["A1"].font = _TITLE_FONT
    ws["A2"] = (
        f"{mode_lbl} | Supply: {result.eac_compliance.recommended_supply_tier} | "
        f"Area: {result.building.total_floor_area_m2:.1f} m²"
    )
    ws["A2"].font = _BOLD_FONT

    headers = [
        "Item Ref",
        "Description (English)",
        "Περιγραφή (Ελληνικά)",
        "Unit",
        "Quantity",
        "Unit Rate (€)",
        "Amount (€)",
    ]
    hdr_row = 4
    for c_idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=hdr_row, column=c_idx, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _NAVY_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _BORDER

    cur_row = hdr_row + 1
    sec_subtotal_rows: dict[str, int] = {}

    for sec_no, sec_en, sec_el in _SECTIONS:
        sec_items = grouped.get(sec_no, [])
        # Section Banner
        ws.cell(row=cur_row, column=1, value=f"SEC {sec_no}").font = _SEC_FONT
        ws.cell(row=cur_row, column=2, value=f"SECTION {sec_no}: {sec_en.upper()}").font = _SEC_FONT
        ws.cell(row=cur_row, column=3, value=sec_el).font = _SEC_FONT
        for col in range(1, 8):
            c = ws.cell(row=cur_row, column=col)
            c.fill = _SEC_FILL
            c.border = _BORDER
        cur_row += 1

        start_data = cur_row
        if not sec_items:
            ws.cell(row=cur_row, column=1, value=f"{sec_no}.00").font = _BODY_FONT
            ws.cell(
                row=cur_row,
                column=2,
                value="Not included in current Engineering Brief scope (Optional)",
            ).font = _BODY_FONT
            ws.cell(
                row=cur_row, column=3, value="Δεν περιλαμβάνεται στο παρόν αντικείμενο"
            ).font = _BODY_FONT
            ws.cell(row=cur_row, column=4, value="item").font = _BODY_FONT
            ws.cell(row=cur_row, column=5, value=0).font = _BODY_FONT
            rate_c = ws.cell(row=cur_row, column=6, value=None if blank_rates else 0.0)
            rate_c.number_format = "#,##0.00"
            if blank_rates:
                rate_c.fill = _INPUT_FILL
            amt_c = ws.cell(
                row=cur_row,
                column=7,
                value=f'=IF(F{cur_row}="",0,E{cur_row}*F{cur_row})'
                if blank_rates
                else f"=E{cur_row}*F{cur_row}",
            )
            amt_c.number_format = "#,##0.00"
            for col in range(1, 8):
                ws.cell(row=cur_row, column=col).border = _BORDER
            cur_row += 1
        else:
            for idx, it in enumerate(sec_items, start=1):
                ws.cell(row=cur_row, column=1, value=f"{sec_no}.{idx:02d}").font = _BODY_FONT
                ws.cell(row=cur_row, column=2, value=it.description_en).font = _BODY_FONT
                ws.cell(row=cur_row, column=3, value=it.description_el).font = _BODY_FONT
                ws.cell(row=cur_row, column=4, value=it.unit).font = _BODY_FONT
                qty_c = ws.cell(row=cur_row, column=5, value=it.quantity)
                qty_c.font = _BODY_FONT
                qty_c.number_format = "#,##0.00"
                rate_c = ws.cell(
                    row=cur_row,
                    column=6,
                    value=None if blank_rates else it.unit_rate_eur,
                )
                rate_c.font = _BODY_FONT
                rate_c.number_format = "#,##0.00"
                if blank_rates:
                    rate_c.fill = _INPUT_FILL
                amt_c = ws.cell(
                    row=cur_row,
                    column=7,
                    value=(
                        f'=IF(F{cur_row}="",0,ROUND(E{cur_row}*F{cur_row},2))'
                        if blank_rates
                        else f"=ROUND(E{cur_row}*F{cur_row},2)"
                    ),
                )
                amt_c.font = _BODY_FONT
                amt_c.number_format = "#,##0.00"
                for col in range(1, 8):
                    ws.cell(row=cur_row, column=col).border = _BORDER
                cur_row += 1

        end_data = cur_row - 1
        # Section Subtotal Row (live SUM formula)
        ws.cell(
            row=cur_row, column=2, value=f"Subtotal Section {sec_no} — {sec_en}"
        ).font = _BOLD_FONT
        sub_c = ws.cell(row=cur_row, column=7, value=f"=SUM(G{start_data}:G{end_data})")
        sub_c.font = _BOLD_FONT
        sub_c.number_format = "#,##0.00"
        for col in range(1, 8):
            c = ws.cell(row=cur_row, column=col)
            c.fill = _SUB_FILL
            c.border = _BORDER
        sec_subtotal_rows[sec_no] = cur_row
        cur_row += 1

    # Grand Totals block (live SUM of section subtotals)
    vat_rate = result.boq.vat_rate if result.boq else 0.19
    cur_row += 1
    sub_terms = "+".join(f"G{r}" for r in sec_subtotal_rows.values())
    ws.cell(row=cur_row, column=2, value="TOTAL ELECTRICAL WORKS (EXCL. VAT)").font = _BOLD_FONT
    c_sub = ws.cell(row=cur_row, column=7, value=f"={sub_terms}")
    c_sub.font = _BOLD_FONT
    c_sub.number_format = "#,##0.00"
    for col in range(1, 8):
        ws.cell(row=cur_row, column=col).border = _BORDER
    sub_row = cur_row

    cur_row += 1
    ws.cell(row=cur_row, column=2, value=f"CYPRUS VAT ({vat_rate * 100:.0f}%)").font = _BOLD_FONT
    c_vat = ws.cell(row=cur_row, column=7, value=f"=ROUND(G{sub_row}*{vat_rate:.4f},2)")
    c_vat.font = _BOLD_FONT
    c_vat.number_format = "#,##0.00"
    for col in range(1, 8):
        ws.cell(row=cur_row, column=col).border = _BORDER
    vat_row = cur_row

    cur_row += 1
    ws.cell(row=cur_row, column=2, value="GRAND TOTAL (INCL. CYPRUS VAT)").font = _SEC_FONT
    c_gt = ws.cell(row=cur_row, column=7, value=f"=G{sub_row}+G{vat_row}")
    c_gt.font = _SEC_FONT
    c_gt.number_format = "€#,##0.00"
    for col in range(1, 8):
        gt_cell = ws.cell(row=cur_row, column=col)
        gt_cell.fill = _TOTAL_FILL
        gt_cell.border = _BORDER

    _autosize(ws)
    return sec_subtotal_rows


def export_tender_boq_10section_xlsx(
    result: ElectricalDesignResult,
    out_path: Path,
) -> Path:
    """Export 3-sheet formula-driven 10-Section Cyprus Tender BOQ (Summary + Priced + Blank)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    ws_sum = wb.active
    assert ws_sum is not None
    ws_sum.title = "Tender Summary"
    ws_est = wb.create_sheet("Engineer Estimate (Priced)")
    ws_bid = wb.create_sheet("Contractor Tender (Blank)")

    items = list(result.boq.items) if result.boq else []
    grouped: dict[str, list[BoqItem]] = {s[0]: [] for s in _SECTIONS}
    for it in items:
        grouped[_map_item_section(it)].append(it)

    est_rows = _populate_boq_sheet(ws_est, result, grouped, blank_rates=False)
    bid_rows = _populate_boq_sheet(ws_bid, result, grouped, blank_rates=True)

    # Populate Sheet 1: Executive 10-Section Tender Summary with cross-sheet formulas
    ws_sum.views.sheetView[0].showGridLines = True
    ws_sum.page_setup.orientation = ws_sum.ORIENTATION_LANDSCAPE
    ws_sum.page_setup.paperSize = ws_sum.PAPERSIZE_A4

    ws_sum["A1"] = f"10-SECTION ELECTRICAL TENDER SUMMARY — {result.building.project_name}"
    ws_sum["A1"].font = _TITLE_FONT
    ws_sum["A2"] = (
        f"EAC Tier: {result.eac_compliance.recommended_supply_tier} | "
        f"Area: {result.building.total_floor_area_m2:.1f} m² | "
        f"Circuits: {len(result.circuits)} | Currency: EUR"
    )
    ws_sum["A2"].font = _BOLD_FONT

    sum_headers = [
        "Section",
        "Trade Package (English)",
        "Ενότητα Εργασιών (Ελληνικά)",
        "Engineer Estimate (€)",
        "Contractor Tender Bid (€)",
    ]
    for col, txt in enumerate(sum_headers, start=1):
        c = ws_sum.cell(row=4, column=col, value=txt)
        c.font = _HEADER_FONT
        c.fill = _NAVY_FILL
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = _BORDER

    row_i = 5
    for sec_no, sec_en, sec_el in _SECTIONS:
        ws_sum.cell(row=row_i, column=1, value=f"SEC {sec_no}").font = _BOLD_FONT
        ws_sum.cell(row=row_i, column=2, value=sec_en).font = _BODY_FONT
        ws_sum.cell(row=row_i, column=3, value=sec_el).font = _BODY_FONT
        c_est = ws_sum.cell(
            row=row_i, column=4, value=f"='Engineer Estimate (Priced)'!G{est_rows[sec_no]}"
        )
        c_est.number_format = "#,##0.00"
        c_bid = ws_sum.cell(
            row=row_i, column=5, value=f"='Contractor Tender (Blank)'!G{bid_rows[sec_no]}"
        )
        c_bid.number_format = "#,##0.00"
        c_bid.fill = _INPUT_FILL
        for col in range(1, 6):
            ws_sum.cell(row=row_i, column=col).border = _BORDER
        row_i += 1

    vat_rate = result.boq.vat_rate if result.boq else 0.19
    sub_r = row_i
    ws_sum.cell(row=sub_r, column=2, value="SUBTOTAL (EXCL. VAT)").font = _BOLD_FONT
    for col, let in ((4, "D"), (5, "E")):
        c = ws_sum.cell(row=sub_r, column=col, value=f"=SUM({let}5:{let}{sub_r - 1})")
        c.font = _BOLD_FONT
        c.number_format = "#,##0.00"
    for col in range(1, 6):
        ws_sum.cell(row=sub_r, column=col).border = _BORDER

    vat_r = sub_r + 1
    ws_sum.cell(row=vat_r, column=2, value=f"CYPRUS VAT ({vat_rate * 100:.0f}%)").font = _BOLD_FONT
    for col, let in ((4, "D"), (5, "E")):
        c = ws_sum.cell(row=vat_r, column=col, value=f"=ROUND({let}{sub_r}*{vat_rate:.4f},2)")
        c.font = _BOLD_FONT
        c.number_format = "#,##0.00"
    for col in range(1, 6):
        ws_sum.cell(row=vat_r, column=col).border = _BORDER

    gt_r = vat_r + 1
    ws_sum.cell(row=gt_r, column=2, value="GRAND TOTAL (INCL. VAT)").font = _SEC_FONT
    for col, let in ((4, "D"), (5, "E")):
        c = ws_sum.cell(row=gt_r, column=col, value=f"={let}{sub_r}+{let}{vat_r}")
        c.font = _SEC_FONT
        c.number_format = "€#,##0.00"
    for col in range(1, 6):
        cell = ws_sum.cell(row=gt_r, column=col)
        cell.fill = _TOTAL_FILL
        cell.border = _BORDER

    for col_idx, w in ((1, 12), (2, 44), (3, 44), (4, 24), (5, 26)):
        ws_sum.column_dimensions[get_column_letter(col_idx)].width = w

    wb.save(str(out_path))
    return out_path
