"""Formula-backed Excel (.xlsx) and flat CSV load schedule exporters."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from cyelec.models.electrical import ElectricalDesignResult, ReviewSeverity
from cyelec.models.spatial import DeviceType, PlacedDevice, SpatialLayoutResult

_NAVY_FILL = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
_SUBHEADER_FILL = PatternFill(start_color="D9E2EC", end_color="D9E2EC", fill_type="solid")
_PASS_FILL = PatternFill(start_color="D4EDDA", end_color="D4EDDA", fill_type="solid")
_WARN_FILL = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
_FAIL_FILL = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")

_HEADER_FONT = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
_TITLE_FONT = Font(name="Calibri", size=13, bold=True, color="1B365D")
_BOLD_FONT = Font(name="Calibri", size=10, bold=True)
_BODY_FONT = Font(name="Calibri", size=10)

_THIN_SIDE = Side(border_style="thin", color="BFBFBF")
_CELL_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)


SCHEDULE_HEADERS: list[str] = [
    "Circuit ID",
    "Description",
    "Category",
    "Topology",
    "Phase",
    "Connected (W)",
    "Diversity",
    "Diversified (W)",
    "PF",
    "Ib (A)",
    "Device",
    "In (A)",
    "Curve",
    "RCD (mA)",
    "Method",
    "Length (m)",
    "Cable (mm²)",
    "CPC (mm²)",
    "Ca",
    "Cg",
    "Ci",
    "It (A)",
    "Iz (A)",
    "Req Iz (A)",
    "Thermal",
    "mV/A/m",
    "ΔV (V)",
    "ΔV (%)",
    "ΔV Limit (%)",
    "ΔV Check",
    "Zs (Ω)",
    "Zs Max (Ω)",
    "EF Check",
    "CPC Min (mm²)",
    "CPC Check",
    "Overall",
]


def _autosize_columns(ws: Worksheet, min_width: int = 9, max_width: int = 44) -> None:
    for col_idx in range(1, ws.max_column + 1):
        letter = get_column_letter(col_idx)
        best = min_width
        for row_idx in range(1, ws.max_row + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is None:
                continue
            text = str(val)
            if text.startswith("="):
                continue
            best = max(best, min(len(text) + 3, max_width))
        ws.column_dimensions[letter].width = best


def export_load_schedule_xlsx(
    result: ElectricalDesignResult,
    out_path: Path,
    *,
    spatial: SpatialLayoutResult | None = None,
) -> Path:
    """Export 6-sheet formula-backed Excel workbook (Load, EAC, BOQ, Form, HMY 58, Lighting)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # ==================================================================
    # Sheet 1: Load Schedule
    # ==================================================================
    ws = wb.active
    assert ws is not None
    ws.title = "Load Schedule"
    ws.views.sheetView[0].showGridLines = True
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4

    # Project Metadata Header (Rows 1-6; B4=Earthing, B5=Ze/Ra referenced by formulas)
    ws["A1"] = f"CYELEC-PIPELINE LOAD SCHEDULE — {result.building.project_name}"
    ws["A1"].font = _TITLE_FONT

    meta_left: list[tuple[str, str | int | float]] = [
        ("Building Type", result.building.building_type.value),
        ("EAC Profile", result.eac_compliance.eac_profile.value),
        ("Earthing System", result.earthing_system.value),
        ("Ze / Ra (Ω)", result.ze_or_ra_ohm),
        ("Supply Tier", result.eac_compliance.recommended_supply_tier),
    ]
    for offset, (label, val) in enumerate(meta_left, start=2):
        ws.cell(row=offset, column=1, value=label).font = _BOLD_FONT
        ws.cell(row=offset, column=2, value=val).font = _BODY_FONT

    meta_right: list[tuple[str, str | int | float]] = [
        ("Pipeline Version", result.provenance.pipeline_version),
        ("Floor Area (m²)", result.building.total_floor_area_m2),
        ("PV Capacity (kVA)", result.building.pv_capacity_kva),
        ("Input SHA-256", result.provenance.input_sha256[:16]),
        ("ETEK Sign-Off", "REQUIRED" if result.etek_signoff_required else "OPTIONAL"),
    ]
    for offset, (label, val) in enumerate(meta_right, start=2):
        ws.cell(row=offset, column=5, value=label).font = _BOLD_FONT
        ws.cell(row=offset, column=6, value=val).font = _BODY_FONT

    header_row = 8
    for col_idx, text in enumerate(SCHEDULE_HEADERS, start=1):
        hdr_cell = ws.cell(row=header_row, column=col_idx, value=text)
        hdr_cell.font = _HEADER_FONT
        hdr_cell.fill = _NAVY_FILL
        hdr_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        hdr_cell.border = _CELL_BORDER
    ws.row_dimensions[header_row].height = 28
    ws.freeze_panes = f"C{header_row + 1}"

    first_data_row = header_row + 1
    for idx, circ in enumerate(result.circuits):
        r = first_data_row + idx
        s = circ.spec

        row_values: list[str | int | float] = [
            s.circuit_id,
            s.description,
            s.category.value,
            s.topology.value,
            circ.phase.value,
            s.connected_load_w,
            s.diversity_factor,
            f"=F{r}*G{r}",
            s.power_factor,
            f'=IF(E{r}="3P",H{r}/(SQRT(3)*400*I{r}),H{r}/(230*I{r}))',
            circ.device_label,
            circ.breaker_in_a,
            circ.breaker_curve.value,
            s.rcd_idn_ma if (s.rcd_protected and s.rcd_idn_ma is not None) else "None",
            s.installation_method,
            s.length_m,
            circ.cable_mm2,
            circ.cpc_mm2,
            circ.ca,
            circ.cg,
            circ.ci,
            circ.it_tabulated_a,
            f"=V{r}*S{r}*T{r}*U{r}",
            circ.required_iz_a,
            f'=IF(W{r}>=X{r},"PASS","FAIL")',
            circ.mv_per_a_per_m,
            f'=IF(D{r}="ring_final",Z{r}*J{r}*(P{r}/4)/1000,Z{r}*J{r}*P{r}/1000)',
            f'=IF(E{r}="3P",AA{r}/400*100,AA{r}/230*100)',
            s.vd_limit_pct,
            f'=IF(AB{r}<=AC{r},"PASS","FAIL")',
            circ.zs_ohm,
            circ.zs_max_ohm,
            (
                f'=IF($B$4="TT",'
                f'IF(AND(ISNUMBER(N{r}),N{r}>0,(AE{r}*N{r}/1000)<=50),"PASS","FAIL"),'
                f'IF(AE{r}<=AF{r},"PASS","FAIL"))'
            ),
            circ.cpc_min_mm2,
            f'=IF(R{r}>=AH{r},"PASS","FAIL")',
            f'=IF(AND(Y{r}="PASS",AD{r}="PASS",AG{r}="PASS",AI{r}="PASS"),"PASS","FAIL")',
        ]

        for col_idx, value in enumerate(row_values, start=1):
            c = ws.cell(row=r, column=col_idx, value=value)
            c.font = _BODY_FONT
            c.border = _CELL_BORDER
            if col_idx in (1, 3, 4, 5, 12, 13, 14, 15, 25, 30, 33, 35, 36):
                c.alignment = Alignment(horizontal="center")

        # Number formatting
        for col_idx in (6, 8):
            ws.cell(row=r, column=col_idx).number_format = "#,##0.0"
        for col_idx in (7, 9, 10, 16, 17, 18, 19, 20, 21, 22, 23, 24, 26, 27, 28, 29, 31, 32, 34):
            ws.cell(row=r, column=col_idx).number_format = "0.00"

        # Status cell visual styling based on deterministic Python verification
        status_map = [
            (25, circ.thermal_pass),
            (30, circ.voltage_drop_pass),
            (33, circ.earth_fault_pass),
            (35, circ.cpc_adiabatic_pass),
            (36, circ.overall_pass),
        ]
        for col_idx, passed in status_map:
            status_cell = ws.cell(row=r, column=col_idx)
            status_cell.fill = _PASS_FILL if passed else _FAIL_FILL
            status_cell.font = _BOLD_FONT

    last_data_row = first_data_row + len(result.circuits) - 1

    # Totals row
    totals_row = last_data_row + 1
    ws.cell(row=totals_row, column=1, value="TOTAL").font = _BOLD_FONT
    ws.cell(row=totals_row, column=6, value=f"=SUM(F{first_data_row}:F{last_data_row})").font = (
        _BOLD_FONT
    )
    ws.cell(row=totals_row, column=6).number_format = "#,##0.0"
    ws.cell(row=totals_row, column=8, value=f"=SUM(H{first_data_row}:H{last_data_row})").font = (
        _BOLD_FONT
    )
    ws.cell(row=totals_row, column=8).number_format = "#,##0.0"
    if result.eac_compliance.supply_phases == 1:
        ws.cell(
            row=totals_row, column=10, value=f"=SUM(J{first_data_row}:J{last_data_row})"
        ).font = _BOLD_FONT
        ws.cell(row=totals_row, column=10).number_format = "0.00"
    else:
        ws.cell(row=totals_row, column=10, value="See 3P Balance").font = _BOLD_FONT
        ws.cell(row=totals_row, column=10).alignment = Alignment(horizontal="center")
    for col_idx in range(1, len(SCHEDULE_HEADERS) + 1):
        ws.cell(row=totals_row, column=col_idx).fill = _SUBHEADER_FILL
        ws.cell(row=totals_row, column=col_idx).border = _CELL_BORDER

    # 3-Phase Load Balance Formula Summary Block
    bal_hdr = totals_row + 2
    ws.cell(row=bal_hdr, column=1, value="3-Phase Load Balance Summary (Live Formulas)").font = (
        _BOLD_FONT
    )
    l1_row = bal_hdr + 1
    l2_row = bal_hdr + 2
    l3_row = bal_hdr + 3
    max_row = bal_hdr + 4
    min_row = bal_hdr + 5
    imb_row = bal_hdr + 6
    status_row = bal_hdr + 7

    phase_rows = [
        (
            l1_row,
            "Phase L1 Current (A)",
            (
                f'=SUMIF(E{first_data_row}:E{last_data_row},"L1",J{first_data_row}:J{last_data_row})'
                f'+SUMIF(E{first_data_row}:E{last_data_row},"3P",J{first_data_row}:J{last_data_row})'
            ),
        ),
        (
            l2_row,
            "Phase L2 Current (A)",
            (
                f'=SUMIF(E{first_data_row}:E{last_data_row},"L2",J{first_data_row}:J{last_data_row})'
                f'+SUMIF(E{first_data_row}:E{last_data_row},"3P",J{first_data_row}:J{last_data_row})'
            ),
        ),
        (
            l3_row,
            "Phase L3 Current (A)",
            (
                f'=SUMIF(E{first_data_row}:E{last_data_row},"L3",J{first_data_row}:J{last_data_row})'
                f'+SUMIF(E{first_data_row}:E{last_data_row},"3P",J{first_data_row}:J{last_data_row})'
            ),
        ),
        (max_row, "Max Phase Current (A)", f"=MAX(B{l1_row}:B{l3_row})"),
        (min_row, "Min Phase Current (A)", f"=MIN(B{l1_row}:B{l3_row})"),
        (
            imb_row,
            "Phase Imbalance (%)",
            f"=IF(B{max_row}=0,0,(B{max_row}-B{min_row})/B{max_row}*100)",
        ),
        (status_row, "Balance Status (<=10%)", f'=IF(B{imb_row}<=10,"PASS","WARNING")'),
    ]
    for r_num, label, formula in phase_rows:
        lbl_cell = ws.cell(row=r_num, column=1, value=label)
        lbl_cell.font = _BOLD_FONT
        lbl_cell.border = _CELL_BORDER
        val_cell = ws.cell(row=r_num, column=2, value=formula)
        val_cell.font = _BOLD_FONT if r_num >= imb_row else _BODY_FONT
        val_cell.border = _CELL_BORDER
        if r_num != status_row:
            val_cell.number_format = "0.00"
        elif result.phase_balance is not None:
            val_cell.fill = _PASS_FILL if result.phase_balance.within_target else _WARN_FILL

    _autosize_columns(ws)

    # ==================================================================
    # Sheet 2: EAC & Review Flags
    # ==================================================================
    ws2 = wb.create_sheet(title="EAC & Review Flags")
    ws2.views.sheetView[0].showGridLines = True
    ws2.page_setup.orientation = ws2.ORIENTATION_LANDSCAPE
    ws2.page_setup.paperSize = ws2.PAPERSIZE_A4

    ws2["A1"] = "CYPRUS EAC COMPLIANCE & ETEK ENGINEER REVIEW FLAGS REGISTER"
    ws2["A1"].font = _TITLE_FONT

    eac = result.eac_compliance
    eac_rows = [
        ("EAC Profile", eac.eac_profile.value),
        ("Supply Phases", f"{eac.supply_phases}-Phase"),
        ("Mandatory 3-Phase", "YES" if eac.mandatory_three_phase else "NO"),
        ("3-Phase Justification", "; ".join(eac.three_phase_reasons) or "N/A"),
        ("Total Connected Load (kVA)", eac.total_connected_kva),
        ("Total Diversified Load (kVA)", eac.total_diversified_kva),
        ("Peak Phase Demand (A)", eac.max_phase_demand_a),
        ("Recommended EAC Supply Tier", eac.recommended_supply_tier),
        ("CT Metering Required (>100 A/ph)", "YES" if eac.ct_metering_required else "NO"),
        ("Substation Warning (>=100 kVA)", "YES" if eac.substation_warning else "NO"),
        (
            "Substation Consultation Blocker (>=150 kVA)",
            "YES" if eac.substation_consultation_required else "NO",
        ),
        ("Bathroom Socket Exclusion (m)", eac.bathroom_socket_exclusion_m),
    ]
    for r_idx, (k, v) in enumerate(eac_rows, start=3):
        c1 = ws2.cell(row=r_idx, column=1, value=k)
        c1.font = _BOLD_FONT
        c1.fill = _SUBHEADER_FILL
        c1.border = _CELL_BORDER
        c2 = ws2.cell(row=r_idx, column=2, value=v)
        c2.font = _BODY_FONT
        c2.border = _CELL_BORDER

    # Optional Sub-Distribution Boards & Sub-Mains table (multi-floor buildings)
    cursor_row = len(eac_rows) + 5
    if result.sub_mains:
        ws2.cell(
            row=cursor_row,
            column=1,
            value="Sub-Distribution Boards & Sub-Main Feeders (BS 7671 Cumulative ΔV)",
        ).font = _BOLD_FONT
        sm_hdr_row = cursor_row + 1
        sm_headers = [
            "Board ID",
            "Parent",
            "Floor",
            "Div. kVA",
            "Ib (A)",
            "Upstream Device (Type S RCD)",
            "Cable / CPC (mm²)",
            "Length (m)",
            "Sub-Main ΔV (%)",
            "Cumul. Light ΔV (%)",
            "Cumul. Power ΔV (%)",
            "Status",
        ]
        for col_idx, text in enumerate(sm_headers, start=1):
            hdr_c = ws2.cell(row=sm_hdr_row, column=col_idx, value=text)
            hdr_c.font = _HEADER_FONT
            hdr_c.fill = _NAVY_FILL
            hdr_c.alignment = Alignment(horizontal="center")
            hdr_c.border = _CELL_BORDER
        for sm_idx, sm in enumerate(result.sub_mains, start=1):
            r = sm_hdr_row + sm_idx
            sm_vals: list[str | int | float] = [
                sm.board_id,
                sm.parent_board_id,
                sm.floor,
                sm.diversified_kva,
                sm.design_current_ib_a,
                sm.device_label,
                f"{sm.cable_mm2:g}/{sm.cpc_mm2:g}",
                sm.length_m,
                sm.voltage_drop_pct,
                sm.cumulative_lighting_vd_pct,
                sm.cumulative_power_vd_pct,
                "PASS" if sm.overall_pass else "FAIL",
            ]
            for col_idx, val in enumerate(sm_vals, start=1):
                sc = ws2.cell(row=r, column=col_idx, value=val)
                sc.font = _BODY_FONT
                sc.border = _CELL_BORDER
            st_cell = ws2.cell(row=r, column=12)
            st_cell.font = _BOLD_FONT
            st_cell.fill = _PASS_FILL if sm.overall_pass else _FAIL_FILL
        cursor_row = sm_hdr_row + len(result.sub_mains) + 2

    # Review Flags table
    flags_start = cursor_row
    flag_headers = [
        "Code",
        "Severity",
        "Category",
        "Circuit ID",
        "Regulation Reference",
        "Message",
        "ETEK Sign-Off",
    ]
    for col_idx, text in enumerate(flag_headers, start=1):
        c = ws2.cell(row=flags_start, column=col_idx, value=text)
        c.font = _HEADER_FONT
        c.fill = _NAVY_FILL
        c.alignment = Alignment(horizontal="center")
        c.border = _CELL_BORDER

    severity_rank = {ReviewSeverity.BLOCKER: 0, ReviewSeverity.WARNING: 1, ReviewSeverity.INFO: 2}
    ordered_flags = sorted(result.review_flags, key=lambda f: (severity_rank[f.severity], f.code))

    for idx, flag in enumerate(ordered_flags, start=1):
        r = flags_start + idx
        vals = [
            flag.code,
            flag.severity.value.upper(),
            flag.category,
            flag.circuit_id or "-",
            flag.regulation_ref or "-",
            flag.message,
            "YES" if flag.requires_etek_signoff else "NO",
        ]
        for col_idx, val in enumerate(vals, start=1):
            c = ws2.cell(row=r, column=col_idx, value=val)
            c.font = _BODY_FONT
            c.border = _CELL_BORDER
        sev_cell = ws2.cell(row=r, column=2)
        sev_cell.font = _BOLD_FONT
        if flag.severity == ReviewSeverity.BLOCKER:
            sev_cell.fill = _FAIL_FILL
        elif flag.severity == ReviewSeverity.WARNING:
            sev_cell.fill = _WARN_FILL
        else:
            sev_cell.fill = _SUBHEADER_FILL

    # Provenance block
    prov_start = flags_start + len(ordered_flags) + 3
    ws2.cell(row=prov_start, column=1, value="Run Provenance & Audit Metadata").font = _BOLD_FONT
    prov_rows = [
        ("Pipeline Version", result.provenance.pipeline_version),
        ("Input SHA-256", result.provenance.input_sha256),
        ("Timestamp (UTC)", result.provenance.timestamp_utc),
        ("Overrides Applied", "YES" if result.provenance.overrides_applied else "NO"),
        *[
            (f"Standards: {name}", ver)
            for name, ver in sorted(result.provenance.standards_versions.items())
        ],
    ]
    for offset, (k, v) in enumerate(prov_rows, start=1):
        ws2.cell(row=prov_start + offset, column=1, value=k).font = _BOLD_FONT
        ws2.cell(row=prov_start + offset, column=2, value=v).font = _BODY_FONT

    _autosize_columns(ws2, max_width=70)

    # ==================================================================
    # Sheet 3: Bill of Quantities (Priced EUR or Unpriced Tender Mode)
    # ==================================================================
    if result.boq is not None and result.boq.items:
        boq = result.boq
        is_tender = boq.is_tender_blank_rates
        ws3 = wb.create_sheet(title="Bill of Quantities")
        ws3.page_setup.orientation = ws3.ORIENTATION_LANDSCAPE
        ws3.page_setup.paperSize = ws3.PAPERSIZE_A4

        if is_tender:
            ws3["A1"] = (
                f"TENDER BILL OF QUANTITIES (UNPRICED CONTRACTOR BID) — "
                f"{result.building.project_name}"
            )
            ws3["A2"] = (
                f"Tender Mode: Contractor to enter Unit Rate (EUR) in Col G — "
                f"Formulas Auto-Calculate | Currency: {boq.currency}"
            )
        else:
            ws3["A1"] = f"BILL OF QUANTITIES (EUR) — {result.building.project_name}"
            ws3["A2"] = f"Rates Table: {boq.rates_version} | Currency: {boq.currency}"
        ws3["A1"].font = _TITLE_FONT
        ws3["A2"].font = _BODY_FONT

        boq_headers = [
            "Item No.",
            "Category",
            "Description (EN)",
            "Περιγραφή (EL)",
            "Unit",
            "Qty",
            "Unit Rate (EUR)",
            "Line Total (EUR)",
        ]
        hdr_row = 4
        for col_idx, text in enumerate(boq_headers, start=1):
            hc = ws3.cell(row=hdr_row, column=col_idx, value=text)
            hc.font = _HEADER_FONT
            hc.fill = _NAVY_FILL
            hc.alignment = Alignment(horizontal="center")
            hc.border = _CELL_BORDER

        first_item_row = hdr_row + 1
        for idx, item in enumerate(boq.items):
            r = first_item_row + idx
            row_vals: list[str | int | float | None] = [
                item.item_no,
                item.category,
                item.description_en,
                item.description_el,
                item.unit,
                item.quantity,
                None if is_tender else item.unit_rate_eur,
                f"=ROUND(F{r}*G{r},2)",
            ]
            for col_idx, cell_val in enumerate(row_vals, start=1):
                bc = ws3.cell(row=r, column=col_idx, value=cell_val)
                bc.font = _BODY_FONT
                bc.border = _CELL_BORDER
                if col_idx in (6, 7, 8):
                    bc.number_format = "#,##0.00"
                    bc.alignment = Alignment(horizontal="right")
                if is_tender and col_idx == 7:
                    bc.fill = _WARN_FILL

        last_item_row = first_item_row + len(boq.items) - 1
        sub_row = last_item_row + 2
        vat_row = sub_row + 1
        tot_row = vat_row + 1

        ws3.cell(row=sub_row, column=7, value="Subtotal (excl. VAT)").font = _BOLD_FONT
        sub_c = ws3.cell(row=sub_row, column=8, value=f"=SUM(H{first_item_row}:H{last_item_row})")
        sub_c.font = _BOLD_FONT
        sub_c.number_format = "#,##0.00"
        sub_c.border = _CELL_BORDER

        vat_pct_int = int(round(boq.vat_rate * 100))
        ws3.cell(row=vat_row, column=7, value=f"Cyprus VAT ({vat_pct_int}%)").font = _BOLD_FONT
        vat_c = ws3.cell(row=vat_row, column=8, value=f"=ROUND(H{sub_row}*{boq.vat_rate},2)")
        vat_c.font = _BOLD_FONT
        vat_c.number_format = "#,##0.00"
        vat_c.border = _CELL_BORDER

        ws3.cell(row=tot_row, column=7, value="GRAND TOTAL (EUR incl. VAT)").font = _BOLD_FONT
        tot_c = ws3.cell(row=tot_row, column=8, value=f"=H{sub_row}+H{vat_row}")
        tot_c.font = _BOLD_FONT
        tot_c.fill = _PASS_FILL
        tot_c.number_format = "#,##0.00"
        tot_c.border = _CELL_BORDER

        _autosize_columns(ws3, max_width=52)

    # ==================================================================
    # Sheet 4: Cyprus EAC Plan Approval & Supply Application Schedule
    # ==================================================================
    ws4 = wb.create_sheet(title="EAC Application")
    ws4.views.sheetView[0].showGridLines = True
    ws4.page_setup.orientation = ws4.ORIENTATION_PORTRAIT
    ws4.page_setup.paperSize = ws4.PAPERSIZE_A4

    ws4["A1"] = "CYPRUS EAC (ΑΗΚ) PLAN APPROVAL & ELECTRICAL SUPPLY APPLICATION SCHEDULE"
    ws4["A1"].font = _TITLE_FONT
    ws4["A2"] = (
        f"Project: {result.building.project_name} | "
        f"Input SHA-256: {result.provenance.input_sha256[:20]}"
    )
    ws4["A2"].font = _BODY_FONT

    app_headers = ["Section", "Parameter (EN)", "Παράμετρος (EL)", "Declared Value / Status"]
    for c_idx, title in enumerate(app_headers, start=1):
        cell = ws4.cell(row=4, column=c_idx, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _NAVY_FILL
        cell.border = _CELL_BORDER

    eac = result.eac_compliance
    br = result.branding
    pb = result.phase_balance
    firm_str = br.firm_name if br and br.firm_name else "[Firm Name]"
    eng_str = br.engineer_name if br and br.engineer_name else "[Engineer]"
    etek_str = br.etek_reg_no if br and br.etek_reg_no else "[ETEK-E-XXXX]"
    kva_str = f"{eac.total_connected_kva:.2f} / {eac.total_diversified_kva:.2f} kVA"
    peak_str = (
        f"{pb.max_phase_current_a:.2f} A ({pb.imbalance_pct:.2f}%)"
        if pb
        else f"{eac.max_phase_demand_a:.2f} A (1P)"
    )
    ct_str = "REQUIRED" if eac.ct_metering_required else "Direct"
    sub_str = (
        "MANDATORY"
        if eac.substation_consultation_required
        else ("WARNING" if eac.substation_warning else "N/A")
    )
    earth_str = f"{result.earthing_system.value} ({result.ze_or_ra_ohm:.2f} Ω)"
    pv_str = (
        f"{result.building.pv_capacity_kva:g} kVA"
        if result.building.pv_capacity_kva > 0
        else "None"
    )
    ev_str = (
        f"{result.building.ev_charger_kw:g} kW"
        if result.building.ev_charger_kw > 0
        else "None"
    )
    app_rows: list[tuple[str, str, str, str]] = [
        ("A. Premises", "Building Classification", "Κατηγορία Κτιρίου",
         result.building.building_type.value),
        ("A. Premises", "Total Floor Area (m²)", "Συνολικό Εμβαδόν",
         f"{result.building.total_floor_area_m2:.2f} m²"),
        ("A. Premises", "District / Sheet / Plan / Plot", "Επαρχία / Φύλλο / Σχέδιο / Τεμάχιο",
         "[Complete before EAC filing]"),
        ("A. Premises", "EAC Tariff Code", "Διατίμηση ΑΗΚ",
         "Code 10 (Commercial)"
         if result.building.building_type.value in ("office", "shop", "workshop")
         else "Code 01 (Domestic)"),
        ("B. Ε-ΔΔ-516", "Consulting Firm", "Μελετητικό Γραφείο ΕΤΕΚ", firm_str),
        ("B. Ε-ΔΔ-516", "Lead Engineer", "Μελετητής Ηλεκτρολόγος Μηχανικός", eng_str),
        ("B. Ε-ΔΔ-516", "ETEK Reg. No.", "Αρ. Μητρώου ΕΤΕΚ", etek_str),
        ("B. Ε-ΔΔ-514", "Licensed Installer (Class A/B/C)", "Αδειούχος Εγκαταστάτης",
         "[Contractor Name & HMY Licence No.]"),
        ("C. Supply", "Requested EAC Tier", "Αιτούμενη Παροχή ΑΗΚ", eac.recommended_supply_tier),
        ("C. Supply", "Connected / Diversified kVA", "Εγκατεστημένη / Ταυτόχρονη", kva_str),
        ("C. Supply", "Peak Phase Current", "Μέγιστο Ρεύμα Φάσης", peak_str),
        ("C. Supply", "CT Metering (>100 A/ph)", "Θάλαμος Μετρητών CT", ct_str),
        ("C. Supply", "Substation (>=150 kVA)", "Υποσταθμός ΑΗΚ", sub_str),
        ("C. Supply", "Earthing & Ze/Ra", "Σύστημα Γείωσης", earth_str),
        ("D. Systems", "PV Capacity (kVA)", "Φωτοβολταϊκά (Φ/Β)", pv_str),
        ("D. Systems", "EV Charger (Sec 722)", "Φορτιστής EV", ev_str),
        ("E. Sign-Off", "ETEK Statutory Stamp (Ε-ΔΔ-516)", "Σφραγίδα & Υπογραφή ΕΤΕΚ",
         "REQUIRED BEFORE EAC SUBMISSION"),
    ]
    for app_r_idx, app_row in enumerate(app_rows, start=5):
        for app_c_idx, app_val in enumerate(app_row, start=1):
            c = ws4.cell(row=app_r_idx, column=app_c_idx, value=app_val)
            c.font = _BOLD_FONT if app_c_idx == 2 else _BODY_FONT
            c.border = _CELL_BORDER
    _autosize_columns(ws4, max_width=46)

    # ==================================================================
    # Sheet 5: Cyprus HMY 58 (18-1) BS 7671 Initial Verification Test Schedule
    # ==================================================================
    ws5 = wb.create_sheet(title="HMY 58 Test Schedule")
    ws5.views.sheetView[0].showGridLines = True
    ws5.page_setup.orientation = ws5.ORIENTATION_LANDSCAPE
    ws5.page_setup.paperSize = ws5.PAPERSIZE_A4

    ws5["A1"] = "CYPRUS HMY 58 (18-1) — BS 7671 INITIAL VERIFICATION CIRCUIT TEST SCHEDULE"
    ws5["A1"].font = _TITLE_FONT
    ws5["A2"] = (
        f"Project: {result.building.project_name} | Earthing: {earth_str} | "
        "Complete blank site test columns during inspection (Ε-ΔΔ-514)"
    )
    ws5["A2"].font = _BODY_FONT

    hmy_headers = [
        "Board",
        "Circuit ID",
        "Phase",
        "Description",
        "Live/CPC (mm²)",
        "Device",
        "RCD (mA)",
        "Max Zs (Ω)",
        "R1+R2 (Ω)",
        "IR (MΩ >=1.0)",
        "Polarity",
        "Meas. Zs/Ra (Ω)",
        "RCD 1x (ms)",
        "RCD 5x (ms)",
        "Site Pass/Fail",
    ]
    for c_idx, title in enumerate(hmy_headers, start=1):
        cell = ws5.cell(row=4, column=c_idx, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _NAVY_FILL
        cell.border = _CELL_BORDER

    for t_idx, circ in enumerate(result.circuits, start=5):
        hmy_row: list[str | float | int | None] = [
            circ.spec.board_id,
            circ.spec.circuit_id,
            circ.phase.value,
            circ.spec.description,
            f"{circ.cable_mm2:g}/{circ.cpc_mm2:g}",
            circ.device_label,
            circ.spec.rcd_idn_ma if circ.spec.rcd_idn_ma else "-",
            round(circ.zs_max_ohm, 3),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
        for c_idx, cell_val in enumerate(hmy_row, start=1):
            tc = ws5.cell(row=t_idx, column=c_idx, value=cell_val)
            tc.font = _BOLD_FONT if c_idx == 2 else _BODY_FONT
            tc.border = _CELL_BORDER
    _autosize_columns(ws5, max_width=32)

    # ==================================================================
    # Sheet 6: Luminaire & Switching Schedule (E-101 Construction Takeoff)
    # ==================================================================
    ws6 = wb.create_sheet(title="Luminaire & Switching")
    ws6.views.sheetView[0].showGridLines = True
    ws6.page_setup.orientation = ws6.ORIENTATION_LANDSCAPE
    ws6.page_setup.paperSize = ws6.PAPERSIZE_A4

    ws6["A1"] = "E-101 LUMINAIRE, SWITCHING & BATHROOM ACCESSORY SCHEDULE (BS EN 12464 / CYPRUS)"
    ws6["A1"].font = _TITLE_FONT
    ws6["A2"] = (
        f"Project: {result.building.project_name} | "
        "Switch Codes: 1G1W=1-Gang 1-Way, 2G1W=2-Gang 1-Way, 1G2W=2-Way (Corridor/Stairs/Master)"
    )
    ws6["A2"].font = _BODY_FONT

    ls_headers = [
        "Room ID",
        "Space / Room",
        "Floor",
        "Area (m²)",
        "Target Lux",
        "Ceiling Points",
        "IP Rating",
        "Est. Lighting (W)",
        "Switch Type & Qty",
        "Groups",
        "Mirror Light / Exhaust Fan",
        "Mounting Heights (FFL)",
    ]
    for c_idx, title in enumerate(ls_headers, start=1):
        cell = ws6.cell(row=4, column=c_idx, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _NAVY_FILL
        cell.border = _CELL_BORDER

    devs_by_room: dict[str, list[PlacedDevice]] = {}
    if spatial is not None:
        for d in spatial.devices:
            devs_by_room.setdefault(d.room_id, []).append(d)

    for r_idx, room in enumerate(result.building.rooms, start=5):
        room_devs = devs_by_room.get(room.id, [])
        lums = [d for d in room_devs if d.device_type == DeviceType.LUMINAIRE]
        sws = [d for d in room_devs if d.device_type == DeviceType.SWITCH]
        mirrors = [d for d in room_devs if d.device_type == DeviceType.MIRROR_LIGHT]
        fans = [d for d in room_devs if d.device_type == DeviceType.EXHAUST_FAN]

        if lums:
            lum_cnt = len(lums)
            ip_val = lums[0].ip_rating
            lux_val = lums[0].target_lux or 150
            watts = round(
                sum(
                    (d.luminaire_wattage_w or 15.0)
                    for d in (lums + mirrors + fans)
                ),
                1,
            )
        else:
            lum_cnt = 1
            ip_val = "IP65" if room.is_outdoor else ("IP44" if room.is_wet_room else "IP20")
            lux_val = (
                200
                if room.is_wet_room
                else (300 if "kitchen" in room.normalized_type.value else 150)
            )
            watts = 15.0

        if sws:
            st_counts: dict[str, int] = {}
            groups: list[str] = []
            for sw_d in sws:
                st = sw_d.switch_type or "1G1W"
                st_counts[st] = st_counts.get(st, 0) + 1
                if sw_d.switch_group and sw_d.switch_group not in groups:
                    groups.append(sw_d.switch_group)
            sw_summary = ", ".join(f"{cnt}x {st}" for st, cnt in st_counts.items())
            grp_summary = ", ".join(groups) or "a"
        else:
            sw_summary = "1x 2G1W" if room.is_wet_room else "1x 1G1W"
            grp_summary = "a,b" if room.is_wet_room else "a"

        wet_acc = (
            f"{len(mirrors)}x IP44 Mirror + {len(fans)}x Fan/3P Isol"
            if (mirrors or fans)
            else ("IP44 Mirror + Fan/3P Isol" if room.is_wet_room else "-")
        )
        has_bedside = any(abs(s.mounting_height_mm - 750.0) < 10.0 for s in sws)
        mh_txt = (
            "SW +1.2m (outside), ML +2.0m, 3P +2.2m, Fan +2.4m"
            if room.is_wet_room
            else ("SW +1.2m & Bedside +0.75m (2W)" if has_bedside else "SW +1.2m, Ceiling")
        )

        ls_row: list[str | float | int] = [
            room.id,
            room.raw_label,
            room.floor,
            round(room.area_m2, 2),
            lux_val,
            lum_cnt,
            ip_val,
            watts,
            sw_summary,
            grp_summary,
            wet_acc,
            mh_txt,
        ]
        for c_idx, val in enumerate(ls_row, start=1):
            lc = ws6.cell(row=r_idx, column=c_idx, value=val)
            lc.font = _BOLD_FONT if c_idx == 1 else _BODY_FONT
            lc.border = _CELL_BORDER
    _autosize_columns(ws6, max_width=36)

    wb.save(str(out_path))
    return out_path


def _safe_csv_cell(val: object) -> object:
    """Neutralize OWASP CSV formula injection (=, +, -, @, tab, CR) on non-numeric strings."""
    if not isinstance(val, str) or not val:
        return val
    if val[0] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(val)
            return val
        except ValueError:
            return f"'{val}"
    return val


def _safe_csv_row(row: list[object]) -> list[object]:
    return [_safe_csv_cell(v) for v in row]


def export_boq_csv(result: ElectricalDesignResult, out_path: Path) -> Path:
    """Export flat evaluated Bill of Quantities CSV (priced or blank Tender BOQ mode)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "Item No.",
                "Category",
                "Description (EN)",
                "Description (EL)",
                "Unit",
                "Quantity",
                "Unit Rate (EUR)",
                "Total (EUR)",
            ]
        )
        if result.boq is not None:
            is_tender = result.boq.is_tender_blank_rates
            for item in result.boq.items:
                writer.writerow(
                    _safe_csv_row(
                        [
                            item.item_no,
                            item.category,
                            item.description_en,
                            item.description_el,
                            item.unit,
                            f"{item.quantity:.2f}",
                            "" if is_tender else f"{item.unit_rate_eur:.2f}",
                            "" if is_tender else f"{item.total_eur:.2f}",
                        ]
                    )
                )
            writer.writerow(
                [
                    "",
                    "",
                    "Subtotal (excl. VAT)",
                    "",
                    "",
                    "",
                    "",
                    "" if is_tender else f"{result.boq.subtotal_eur:.2f}",
                ]
            )
            writer.writerow(
                [
                    "",
                    "",
                    f"Cyprus VAT ({int(round(result.boq.vat_rate * 100))}%)",
                    "",
                    "",
                    "",
                    "",
                    "" if is_tender else f"{result.boq.vat_eur:.2f}",
                ]
            )
            writer.writerow(
                [
                    "",
                    "",
                    "GRAND TOTAL (EUR incl. VAT)",
                    "",
                    "",
                    "",
                    "",
                    "" if is_tender else f"{result.boq.grand_total_eur:.2f}",
                ]
            )
    return out_path


def export_load_schedule_csv(result: ElectricalDesignResult, out_path: Path) -> Path:
    """Export flat evaluated CSV load schedule matching the Excel column layout."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(SCHEDULE_HEADERS)
        for circ in result.circuits:
            s = circ.spec
            writer.writerow(
                _safe_csv_row(
                    [
                        s.circuit_id,
                        s.description,
                        s.category.value,
                        s.topology.value,
                        circ.phase.value,
                        f"{s.connected_load_w:.1f}",
                        f"{s.diversity_factor:.2f}",
                        f"{s.diversified_load_w:.1f}",
                        f"{s.power_factor:.2f}",
                        f"{s.design_current_ib_a:.2f}",
                        circ.device_label,
                        circ.breaker_in_a,
                        circ.breaker_curve.value,
                        (
                            s.rcd_idn_ma
                            if (s.rcd_protected and s.rcd_idn_ma is not None)
                            else "None"
                        ),
                        s.installation_method,
                        f"{s.length_m:.1f}",
                        f"{circ.cable_mm2:g}",
                        f"{circ.cpc_mm2:g}",
                        f"{circ.ca:.2f}",
                        f"{circ.cg:.2f}",
                        f"{circ.ci:.2f}",
                        f"{circ.it_tabulated_a:.2f}",
                        f"{circ.iz_derated_a:.2f}",
                        f"{circ.required_iz_a:.2f}",
                        "PASS" if circ.thermal_pass else "FAIL",
                        f"{circ.mv_per_a_per_m:.2f}",
                        f"{circ.voltage_drop_v:.3f}",
                        f"{circ.voltage_drop_pct:.3f}",
                        f"{s.vd_limit_pct:.1f}",
                        "PASS" if circ.voltage_drop_pass else "FAIL",
                        f"{circ.zs_ohm:.4f}",
                        f"{circ.zs_max_ohm:.2f}",
                        "PASS" if circ.earth_fault_pass else "FAIL",
                        f"{circ.cpc_min_mm2:.3f}",
                        "PASS" if circ.cpc_adiabatic_pass else "FAIL",
                        "PASS" if circ.overall_pass else "FAIL",
                    ]
                )
            )
    return out_path
