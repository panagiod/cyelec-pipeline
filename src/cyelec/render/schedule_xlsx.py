"""Formula-backed Excel (.xlsx) and flat CSV load schedule exporters."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from cyelec.models.electrical import ElectricalDesignResult, ReviewSeverity

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


def export_load_schedule_xlsx(result: ElectricalDesignResult, out_path: Path) -> Path:
    """Export two-sheet formula-backed Excel workbook ('Load Schedule' + 'EAC & Review Flags')."""
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

    # Review Flags table
    flags_start = len(eac_rows) + 5
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
    wb.save(str(out_path))
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
                    s.rcd_idn_ma if (s.rcd_protected and s.rcd_idn_ma is not None) else "None",
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
    return out_path
