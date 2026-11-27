"""Auditable BS 7671 calculation summary & ETEK Human Review Flags Register (.md and .html)."""

from __future__ import annotations

import html
from pathlib import Path

from cyelec.assets import get_logo_svg_data_uri
from cyelec.models.electrical import ElectricalDesignResult, ReviewSeverity
from cyelec.render.i18n import format_branding_line, t


def _severity_order(sev: ReviewSeverity) -> int:
    return {ReviewSeverity.BLOCKER: 0, ReviewSeverity.WARNING: 1, ReviewSeverity.INFO: 2}[sev]


def render_calc_report_md(result: ElectricalDesignResult) -> str:
    """Render deterministic auditable BS 7671 & EAC calculation report in Markdown."""
    eac = result.eac_compliance
    prov = result.provenance
    lang = result.report_language
    branding_line = format_branding_line(result.branding)
    ordered_flags = sorted(
        result.review_flags,
        key=lambda f: (_severity_order(f.severity), f.code, f.circuit_id or ""),
    )

    profile_detail = (
        f"`{eac.eac_profile.value}` "
        f"(Bathroom socket exclusion: `{eac.bathroom_socket_exclusion_m:.1f} m`)"
    )
    supply_detail = (
        f"`{eac.supply_phases}-Phase` "
        f"(`Mandatory 3P: {'Yes' if eac.mandatory_three_phase else 'No'}`)"
    )
    blocker_flag_txt = "YES — ACTION REQUIRED" if result.has_blockers else "No"

    lines: list[str] = [
        f"# {t('report_title', lang)} — {result.building.project_name}",
        "",
        *(
            [f"**Consulting Practice:** {branding_line}", ""]
            if branding_line
            else []
        ),
        f"> **STATUTORY NOTICE:** {t('statutory_notice', lang)}",
        "",
        "## 1. Provenance & Design Basis",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Pipeline Version | `{prov.pipeline_version}` |",
        f"| Timestamp (UTC) | `{prov.timestamp_utc}` |",
        f"| Input SHA-256 | `{prov.input_sha256}` |",
        f"| Building Type | `{result.building.building_type.value}` |",
        f"| Total Floor Area | `{result.building.total_floor_area_m2:.2f} m²` |",
        f"| EAC Profile | {profile_detail} |",
        f"| Earthing System | `{result.earthing_system.value}` "
        f"(`Ze/Ra = {result.ze_or_ra_ohm:.2f} Ω`) |",
        f"| PV Capacity | `{result.building.pv_capacity_kva:.2f} kVA` |",
        f"| Overrides Applied | `{'Yes' if prov.overrides_applied else 'No'}` |",
        f"| Blocker Flags Present | `{blocker_flag_txt}` |",
        "",
        "**Standards Versions:**",
        *[f"- `{k}`: `{v}`" for k, v in sorted(prov.standards_versions.items())],
        "",
        "## 2. Cyprus EAC Supply Determination",
        "",
        "| Parameter | Value |",
        "| --- | --- |",
        f"| Supply Phases | {supply_detail} |",
        f"| Recommended EAC Supply Tier | **{eac.recommended_supply_tier}** "
        f"(`{eac.supply_tier_utilization_pct:.1f}%` utilized) |",
        f"| Total Connected Demand | `{eac.total_connected_kva:.2f} kVA` |",
        f"| Total Diversified Demand | `{eac.total_diversified_kva:.2f} kVA` |",
        f"| Peak Phase Demand | `{eac.max_phase_demand_a:.2f} A` |",
        f"| CT Metering Required (`>100 A/phase`) | "
        f"`{'YES' if eac.ct_metering_required else 'No'}` |",
        f"| Substation Warning (`>=100 kVA`) | `{'YES' if eac.substation_warning else 'No'}` |",
        "| Substation Consultation Blocker (`>=150 kVA`) | "
        f"`{'YES' if eac.substation_consultation_required else 'No'}` |",
        "",
    ]

    if eac.three_phase_reasons:
        lines.append("**3-Phase Determination Reasons:**")
        lines.extend(f"- {reason}" for reason in eac.three_phase_reasons)
        lines.append("")

    if result.pv_storage_design is not None:
        pvs = result.pv_storage_design
        lines.extend(
            [
                "### 2b. DC-Side PV Array & Battery Storage Sizing (BS 7671 Sec. 712 / EAC 2026)",
                "",
                "| Parameter | Engineered Specification |",
                "| --- | --- |",
                f"| Hybrid Inverter | `{pvs.inverter_ac_kva:g} kVA` ({pvs.inverter_phases}P, "
                f"Vdc_max `{pvs.inverter_max_dc_v} V`, "
                f"MPPT `{pvs.mppt_min_v}-{pvs.mppt_max_v} V`) |",
                f"| PV Array & Strings | `{pvs.module_count} × {pvs.module_wp} Wp = "
                f"{pvs.array_dc_kwp:g} kWp` (`{pvs.strings_count} × {pvs.modules_per_string}s`, "
                f"DC/AC `{pvs.dc_ac_ratio:.2f}`) |",
                f"| Cyprus Voc(0°C) / Vmp(70°C) | `Voc(0°C) = {pvs.string_voc_cold_0c_v:.1f} V` "
                f"({'PASS' if pvs.voc_cold_pass else 'FAIL'}) · "
                f"`Vmp(70°C) = {pvs.string_vmp_hot_70c_v:.1f} V` "
                f"({'PASS' if pvs.mppt_window_pass else 'CHECK'}) |",
                f"| Solar DC Cable & ΔV | {pvs.dc_cable_spec} (`{pvs.dc_run_length_m:.1f} m`, "
                f"ΔV_DC `{pvs.dc_voltage_drop_pct:.2f}%`) |",
                f"| DC Protection | {pvs.dc_isolator_spec} · {pvs.dc_spd_spec} |",
            ]
        )
        if pvs.battery_enabled:
            lines.extend(
                [
                    f"| Battery Storage (BESS) | `{pvs.battery_nominal_kwh:g} kWh` "
                    f"(`{pvs.battery_usable_kwh:g} kWh` usable, {pvs.battery_chemistry}, "
                    f"`{pvs.battery_bus_voltage_v} V` DC, max `{pvs.battery_max_power_kw:g} kW` / "
                    f"`{pvs.battery_max_current_a:g} A`) |",
                    f"| BESS DC Protection & Cable | {pvs.battery_dc_breaker_spec} · "
                    f"{pvs.battery_dc_cable_spec} |",
                ]
            )
            if pvs.eps_backup_enabled:
                lines.append(f"| EPS Critical-Load Backup | {pvs.eps_changeover_spec} |")
        lines.append("")

    if result.phase_balance is not None:
        pb = result.phase_balance
        lines.extend(
            [
                "## 3. Exact MILP 3-Phase Load Balance",
                "",
                "| Phase | Design Current (A) | Diversified Power (kW) |",
                "| --- | ---: | ---: |",
                f"| L1 | {pb.phase_currents_a['L1']:.2f} | {pb.phase_power_kw['L1']:.3f} |",
                f"| L2 | {pb.phase_currents_a['L2']:.2f} | {pb.phase_power_kw['L2']:.3f} |",
                f"| L3 | {pb.phase_currents_a['L3']:.2f} | {pb.phase_power_kw['L3']:.3f} |",
                "",
                f"- **Max / Min Phase Current:** `{pb.max_phase_current_a:.2f} A` / "
                f"`{pb.min_phase_current_a:.2f} A` (Spread: `{pb.imbalance_a:.2f} A`)",
                f"- **Imbalance:** `{pb.imbalance_pct:.2f}%` "
                f"({'PASS <= 10.0%' if pb.within_target else 'WARNING > 10.0%'})",
                f"- **Solver Status:** `{pb.solver_status}`",
                "",
            ]
        )

    if result.sub_mains:
        lines.extend(
            [
                "## 3b. Multi-Floor Sub-Distribution Boards & Sub-Mains (BS 7671 Cumulative ΔV)",
                "",
                "| Board | Parent | Floor | Div. kVA | Ib (A) | Upstream Device | "
                "Cable/CPC (mm²) | L (m) | Sub ΔV (%) | Cumul. Light ΔV (%) | "
                "Cumul. Power ΔV (%) | Status |",
                "| --- | --- | :---: | ---: | ---: | --- | :---: | ---: | ---: | "
                "---: | ---: | :---: |",
            ]
        )
        for sm in result.sub_mains:
            st = "PASS" if sm.overall_pass else "FAIL"
            lines.append(
                f"| `{sm.board_id}` | `{sm.parent_board_id}` | {sm.floor} | "
                f"{sm.diversified_kva:.2f} | {sm.design_current_ib_a:.2f} | "
                f"{sm.device_label} | `{sm.cable_mm2:g}/{sm.cpc_mm2:g}` | {sm.length_m:.1f} | "
                f"{sm.voltage_drop_pct:.2f}% | {sm.cumulative_lighting_vd_pct:.2f}% | "
                f"{sm.cumulative_power_vd_pct:.2f}% | **{st}** |"
            )
        lines.append("")

    circuit_hdr = (
        "| ID | Description | Ph | Topo | Ib (A) | Device (Cat. Ref) | Cable/CPC (mm²) | "
        "L_mat / L_elec (m) | Ca·Cg·Ci | Iz / Req (A) | ΔV (%) | EF Check | "
        "Ik1_min / Imag (A) | CPC Min (mm²) | Status |"
    )
    circuit_sep = (
        "| --- | --- | :---: | --- | ---: | --- | :---: | ---: | ---: | "
        "---: | ---: | :---: | ---: | ---: | :---: |"
    )
    lines.extend(
        [
            "## 4. BS 7671 Final Circuit Calculation Summary (Distributed Multi-Node)",
            "",
            circuit_hdr,
            circuit_sep,
        ]
    )
    for c in result.circuits:
        s = c.spec
        ef_str = (
            f"TT {c.tt_touch_voltage_v:.1f}V"
            if c.earth_fault_method == "TT_RCD" and c.tt_touch_voltage_v is not None
            else (
                "TT NO RCD"
                if c.earth_fault_method == "TT_RCD"
                else f"Zs {c.zs_ohm:.2f}/{c.zs_max_ohm:.2f}Ω"
            )
        )
        status = "PASS" if c.overall_pass else "**FAIL**"
        l_elec = (
            c.electrical_path_length_m if c.electrical_path_length_m is not None else s.length_m
        )
        dev_str = (
            f"{c.device_label} (`{c.vendor_catalog_ref}`)"
            if c.vendor_catalog_ref
            else c.device_label
        )
        ik_str = (
            f"{c.ik1_min_a:.0f} / {c.magnetic_trip_max_a:.0f}"
            if (c.ik1_min_a is not None and c.magnetic_trip_max_a is not None)
            else "-"
        )
        lines.append(
            f"| {s.circuit_id} | {s.description} | {c.phase.value} | {s.topology.value} | "
            f"{s.design_current_ib_a:.2f} | {dev_str} | "
            f"{c.cable_mm2:g}/{c.cpc_mm2:g} | {s.length_m:.1f} / {l_elec:.1f} | "
            f"{c.effective_derating:.3f} | {c.iz_derated_a:.1f}/{c.required_iz_a:.1f} | "
            f"{c.voltage_drop_pct:.2f}% | {ef_str} | {ik_str} | {c.cpc_min_mm2:.2f} | {status} |"
        )

    if result.selectivity_analysis is not None and result.selectivity_analysis.boards:
        sel = result.selectivity_analysis
        lines.extend(
            [
                "",
                "## 4b. IEC 60909 Short-Circuit Profile & Time-Current Selectivity (E-203)",
                "",
                "| Board | Upstream Device | Downstream Max | Ik3_max / Ik1_min (kA) | "
                "Icu (kA) | Is Limit (kA) | Vendor Ref | Selectivity |",
                "| --- | --- | --- | ---: | ---: | ---: | --- | :---: |",
            ]
        )
        for b in sel.boards:
            lines.append(
                f"| `{b.board_id}` | {b.upstream_device_label} | "
                f"{b.downstream_max_device_label} | "
                f"{b.ik3_max_ka:.2f} / {b.ik1_min_ka:.2f} | "
                f"{b.device_icu_ka:g} ({'PASS' if b.breaking_capacity_pass else '**FAIL**'}) | "
                f"{b.discrimination_limit_is_ka:.1f} | {b.vendor_coordination_ref or '-'} | "
                f"**{b.selectivity_type.upper()}** |"
            )

    if result.din_rail_boards:
        lines.extend(
            [
                "",
                "## 4d. Main & Secondary Distribution Board Designs (IEC 61439 / E-204)",
                "",
                "| Board | Role | Parent | Standard | Dims (mm) | IP | Busbar | "
                "Ways Used / Cap | Thermal P_loss / P_max (W) |",
                "| --- | --- | --- | --- | --- | :---: | ---: | :---: | :---: |",
            ]
        )
        for bd in result.din_rail_boards:
            lines.append(
                f"| `{bd.board_id}` ({bd.floor}) | {bd.board_role} | "
                f"{bd.parent_board_id or 'EAC Origin'} | {bd.standard_ref} | "
                f"{bd.enclosure_dims_mm} | {bd.ip_rating} | {bd.busbar_rating_a} A | "
                f"{bd.total_modules_used}/{bd.total_modules_capacity}M ({bd.spare_pct:.0f}% sp) | "
                f"{bd.power_dissipation_w:.1f}/{bd.max_enclosure_dissipation_w:.0f} "
                f"({'PASS' if bd.thermal_pass else '**FAIL**'}) |"
            )

    if result.lumen_checks:
        lines.extend(
            [
                "",
                "## 4c. Point-by-Point 2D Photometric Illuminance & Uniformity (BS EN 12464-1)",
                "",
                "| Room | Area (m²) | Fixtures | Target (lx) | E_avg (lx) | "
                "E_min / E_max (lx) | U0 / Target | Grid Pts | Status |",
                "| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | :---: |",
            ]
        )
        for lc in result.lumen_checks:
            min_max = (
                f"{lc.min_lux:.0f} / {lc.max_lux:.0f}"
                if (lc.min_lux is not None and lc.max_lux is not None)
                else "-"
            )
            u0_str = (
                f"{lc.uniformity_u0:.2f} / {lc.u0_target:.2f}"
                if lc.uniformity_u0 is not None
                else "-"
            )
            p_st = "PASS" if (lc.compliant and lc.u0_compliant) else "CHECK"
            lines.append(
                f"| {lc.room_label} | {lc.area_m2:.1f} | "
                f"{lc.luminaire_count} × {lc.watts_per_fixture:g}W | {lc.target_lux} | "
                f"**{lc.achieved_lux:.0f}** | {min_max} | {u0_str} | "
                f"{lc.grid_points_evaluated} | {p_st} |"
            )

    lines.extend(
        [
            "",
            "## 5. ETEK Human Review Flags Register",
            "",
            "| Severity | Code | Category | Circuit | Regulation | Details |",
            "| :---: | --- | --- | :---: | --- | --- |",
        ]
    )
    for f in ordered_flags:
        lines.append(
            f"| **{f.severity.value.upper()}** | `{f.code}` | {f.category} | "
            f"{f.circuit_id or '-'} | {f.regulation_ref or '-'} | {f.message} |"
        )

    if result.boq is not None and result.boq.items:
        boq = result.boq
        lines.extend(
            [
                "",
                f"## 5b. {t('boq_title', lang)}",
                "",
                "| Item | Category | Description | Unit | Qty | Rate (EUR) | Total (EUR) |",
                "| --- | --- | --- | :---: | ---: | ---: | ---: |",
            ]
        )
        for it in boq.items:
            desc = (
                it.description_el
                if lang.value == "el"
                else (
                    f"{it.description_en} / {it.description_el}"
                    if lang.value == "bilingual"
                    else it.description_en
                )
            )
            lines.append(
                f"| `{it.item_no}` | {it.category} | {desc} | {it.unit} | "
                f"{it.quantity:.2f} | {it.unit_rate_eur:.2f} | {it.total_eur:.2f} |"
            )
        lines.extend(
            [
                "",
                f"- **Subtotal (excl. VAT):** `€{boq.subtotal_eur:,.2f}`",
                f"- **Cyprus VAT ({int(round(boq.vat_rate * 100))}%):** `€{boq.vat_eur:,.2f}`",
                f"- **Grand Total (incl. VAT):** **`€{boq.grand_total_eur:,.2f}`**",
            ]
        )

    eng_col = (result.branding.engineer_name or "&nbsp;") if result.branding else "&nbsp;"
    etek_col = (result.branding.etek_reg_no or "&nbsp;") if result.branding else "&nbsp;"

    lines.extend(
        [
            "",
            f"## 6. {t('etek_signoff', lang)}",
            "",
            "- [ ] Site earthing arrangement (`TT` electrode $R_A$ / `TN` $Z_e$) verified on site.",
            "- [ ] BS 7671 Section 701 wet zones and EAC bathroom socket exclusion verified.",
            "- [ ] Conduit grouping ($C_g$) and Cyprus ambient derating ($C_a$) "
            "verified against routing.",
            "- [ ] All `BLOCKER` and `WARNING` flags resolved or formally accepted.",
            "",
            "| Engineer Name | ETEK Registration No. | Signature | Date |",
            "| --- | --- | --- | --- |",
            f"| {eng_col} | {etek_col} | &nbsp; | &nbsp; |",
            "",
        ]
    )
    return "\n".join(lines)


def render_calc_report_html(result: ElectricalDesignResult) -> str:
    """Render print-ready A4-landscape HTML BS 7671 calculation & ETEK review register."""
    eac = result.eac_compliance
    prov = result.provenance
    ordered_flags = sorted(
        result.review_flags,
        key=lambda f: (_severity_order(f.severity), f.code, f.circuit_id or ""),
    )

    def esc(val: object) -> str:
        return html.escape(str(val))

    circuit_rows_html: list[str] = []
    for c in result.circuits:
        s = c.spec
        ef_str = (
            f"TT: {c.tt_touch_voltage_v:.1f} V"
            if c.earth_fault_method == "TT_RCD" and c.tt_touch_voltage_v is not None
            else (
                "TT: NO RCD"
                if c.earth_fault_method == "TT_RCD"
                else f"Zs {c.zs_ohm:.2f}/{c.zs_max_ohm:.2f} Ω"
            )
        )
        badge_cls = "badge-pass" if c.overall_pass else "badge-fail"
        status_txt = "PASS" if c.overall_pass else "FAIL"
        upsize_txt = ", ".join(c.upsize_reasons) if c.upsize_reasons else "-"
        l_elec = (
            c.electrical_path_length_m if c.electrical_path_length_m is not None else s.length_m
        )
        dev_html = (
            f"{esc(c.device_label)}<br><small><code>{esc(c.vendor_catalog_ref)}</code></small>"
            if c.vendor_catalog_ref
            else esc(c.device_label)
        )
        ik_txt = (
            f"{c.ik1_min_a:.0f}/{c.magnetic_trip_max_a:.0f}A"
            if (c.ik1_min_a is not None and c.magnetic_trip_max_a is not None)
            else "-"
        )
        circuit_rows_html.append(
            "<tr>"
            f"<td><strong>{esc(s.circuit_id)}</strong></td>"
            f"<td>{esc(s.description)}</td>"
            f"<td class='c'>{esc(c.phase.value)}</td>"
            f"<td>{esc(s.topology.value)}</td>"
            f"<td class='r'>{s.design_current_ib_a:.2f}</td>"
            f"<td>{dev_html}</td>"
            f"<td class='c'>{c.cable_mm2:g} / {c.cpc_mm2:g}</td>"
            f"<td class='r'>{s.length_m:.1f} / {l_elec:.1f}</td>"
            f"<td class='r'>{c.ca:.2f} / {c.cg:.2f} / {c.ci:.2f}</td>"
            f"<td class='r'>{c.iz_derated_a:.1f} / {c.required_iz_a:.1f}</td>"
            f"<td class='r'>{c.voltage_drop_pct:.2f}%</td>"
            f"<td class='c'>{esc(ef_str)}<br><small>{esc(ik_txt)}</small></td>"
            f"<td class='r'>{c.cpc_min_mm2:.2f}</td>"
            f"<td>{esc(upsize_txt)}</td>"
            f"<td class='c'><span class='badge {badge_cls}'>{status_txt}</span></td>"
            "</tr>"
        )

    flag_rows_html: list[str] = []
    for f in ordered_flags:
        sev_cls = {
            ReviewSeverity.BLOCKER: "badge-fail",
            ReviewSeverity.WARNING: "badge-warn",
            ReviewSeverity.INFO: "badge-info",
        }[f.severity]
        sev_badge = f"<span class='badge {sev_cls}'>{esc(f.severity.value.upper())}</span>"
        flag_rows_html.append(
            "<tr>"
            f"<td class='c'>{sev_badge}</td>"
            f"<td><code>{esc(f.code)}</code></td>"
            f"<td>{esc(f.category)}</td>"
            f"<td class='c'>{esc(f.circuit_id or '-')}</td>"
            f"<td>{esc(f.regulation_ref or '-')}</td>"
            f"<td>{esc(f.message)}</td>"
            "</tr>"
        )

    balance_section = ""
    if result.phase_balance is not None:
        pb = result.phase_balance
        bal_cls = "badge-pass" if pb.within_target else "badge-warn"
        bal_lbl = "PASS (<=10%)" if pb.within_target else "WARNING (>10%)"
        l1_i, l1_kw = pb.phase_currents_a["L1"], pb.phase_power_kw["L1"]
        l2_i, l2_kw = pb.phase_currents_a["L2"], pb.phase_power_kw["L2"]
        l3_i, l3_kw = pb.phase_currents_a["L3"], pb.phase_power_kw["L3"]
        balance_section = f"""
        <section>
          <h2>3. Exact MILP 3-Phase Load Balance</h2>
          <table class="compact">
            <thead>
              <tr>
                <th>Phase</th><th>Design Current (A)</th><th>Diversified Power (kW)</th>
                <th>Spread (A)</th><th>Imbalance (%)</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>L1</strong></td>
                <td class="r">{l1_i:.2f}</td>
                <td class="r">{l1_kw:.3f}</td>
                <td rowspan="3" class="c">{pb.imbalance_a:.2f} A</td>
                <td rowspan="3" class="c"><strong>{pb.imbalance_pct:.2f}%</strong></td>
                <td rowspan="3" class="c"><span class="badge {bal_cls}">{bal_lbl}</span></td>
              </tr>
              <tr>
                <td><strong>L2</strong></td>
                <td class="r">{l2_i:.2f}</td>
                <td class="r">{l2_kw:.3f}</td>
              </tr>
              <tr>
                <td><strong>L3</strong></td>
                <td class="r">{l3_i:.2f}</td>
                <td class="r">{l3_kw:.3f}</td>
              </tr>
            </tbody>
          </table>
        </section>
        """

    submains_section = ""
    if result.sub_mains:
        sm_rows = []
        for sm in result.sub_mains:
            s_cls = "badge-pass" if sm.overall_pass else "badge-fail"
            s_lbl = "PASS" if sm.overall_pass else "FAIL"
            sm_rows.append(
                "<tr>"
                f"<td><strong>{esc(sm.board_id)}</strong></td>"
                f"<td>{esc(sm.parent_board_id)}</td>"
                f'<td class="c">{esc(sm.floor)}</td>'
                f'<td class="r">{sm.diversified_kva:.2f}</td>'
                f'<td class="r">{sm.design_current_ib_a:.2f}</td>'
                f"<td>{esc(sm.device_label)}</td>"
                f'<td class="c">{sm.cable_mm2:g}/{sm.cpc_mm2:g}</td>'
                f'<td class="r">{sm.length_m:.1f}</td>'
                f'<td class="r">{sm.voltage_drop_pct:.2f}%</td>'
                f'<td class="r">{sm.cumulative_lighting_vd_pct:.2f}%</td>'
                f'<td class="r">{sm.cumulative_power_vd_pct:.2f}%</td>'
                f'<td class="c"><span class="badge {s_cls}">{s_lbl}</span></td>'
                "</tr>"
            )
        submains_section = f"""
        <section>
          <h2>3b. Multi-Floor Sub-Distribution Boards &amp; Sub-Mains (BS 7671 Cumulative ΔV)</h2>
          <table>
            <thead>
              <tr>
                <th>Board</th><th>Parent</th><th class="c">Floor</th>
                <th class="r">Div. kVA</th><th class="r">Ib (A)</th>
                <th>Upstream Device</th><th class="c">Cable/CPC (mm²)</th>
                <th class="r">L (m)</th><th class="r">Sub ΔV (%)</th>
                <th class="r">Cumul. Light ΔV</th><th class="r">Cumul. Power ΔV</th>
                <th class="c">Status</th>
              </tr>
            </thead>
            <tbody>{''.join(sm_rows)}</tbody>
          </table>
        </section>
        """

    lang = result.report_language
    branding_line = format_branding_line(result.branding)
    eng_name = (result.branding.engineer_name or "") if result.branding else ""
    etek_no = (result.branding.etek_reg_no or "") if result.branding else ""
    branding_html = (
        f'<div style="background:#edf2f7;border-left:4px solid #1b365d;padding:5px 10px;'
        f'margin-bottom:8px;font-weight:600;color:#1b365d;">{esc(branding_line)}</div>'
        if branding_line
        else ""
    )

    boq_section = ""
    if result.boq is not None and result.boq.items:
        boq = result.boq
        b_rows: list[str] = []
        for it in boq.items:
            desc = (
                it.description_el
                if lang.value == "el"
                else (
                    f"{it.description_en} / {it.description_el}"
                    if lang.value == "bilingual"
                    else it.description_en
                )
            )
            b_rows.append(
                "<tr>"
                f"<td><code>{esc(it.item_no)}</code></td>"
                f"<td>{esc(it.category)}</td>"
                f"<td>{esc(desc)}</td>"
                f'<td class="c">{esc(it.unit)}</td>'
                f'<td class="r">{it.quantity:.2f}</td>'
                f'<td class="r">€{it.unit_rate_eur:,.2f}</td>'
                f'<td class="r">€{it.total_eur:,.2f}</td>'
                "</tr>"
            )
        vat_pct = int(round(boq.vat_rate * 100))
        boq_section = f"""
  <section>
    <h2>5b. {esc(t('boq_title', lang))}</h2>
    <table>
      <thead>
        <tr>
          <th>Item</th><th>Category</th><th>Description</th><th class="c">Unit</th>
          <th class="r">Qty</th><th class="r">Unit Rate</th><th class="r">Total (EUR)</th>
        </tr>
      </thead>
      <tbody>
        {''.join(b_rows)}
        <tr>
          <td colspan="6" class="r"><strong>Subtotal (excl. VAT)</strong></td>
          <td class="r"><strong>€{boq.subtotal_eur:,.2f}</strong></td>
        </tr>
        <tr>
          <td colspan="6" class="r"><strong>Cyprus VAT ({vat_pct}%)</strong></td>
          <td class="r"><strong>€{boq.vat_eur:,.2f}</strong></td>
        </tr>
        <tr>
          <td colspan="6" class="r"><strong>GRAND TOTAL (EUR incl. VAT)</strong></td>
          <td class="r"><strong>€{boq.grand_total_eur:,.2f}</strong></td>
        </tr>
      </tbody>
    </table>
  </section>
"""

    pv_storage_html = ""
    if result.pv_storage_design is not None:
        pvs = result.pv_storage_design
        bess_tr = ""
        if pvs.battery_enabled:
            bess_tr = (
                "<tr><td><strong>Battery Storage (BESS)</strong></td>"
                f"<td>{pvs.battery_nominal_kwh:g} kWh ({pvs.battery_usable_kwh:g} kWh usable, "
                f"{esc(pvs.battery_chemistry)}, {pvs.battery_bus_voltage_v}V DC, "
                f"max {pvs.battery_max_power_kw:g} kW / {pvs.battery_max_current_a:g} A)</td></tr>"
                "<tr><td><strong>BESS Protection &amp; Cabling</strong></td>"
                f"<td>{esc(pvs.battery_dc_breaker_spec)} · "
                f"{esc(pvs.battery_dc_cable_spec)}</td></tr>"
            )
            if pvs.eps_backup_enabled:
                bess_tr += (
                    "<tr><td><strong>EPS Critical-Load Backup</strong></td>"
                    f"<td>{esc(pvs.eps_changeover_spec)}</td></tr>"
                )
        voc_st = "PASS" if pvs.voc_cold_pass else "FAIL"
        mppt_st = "PASS" if pvs.mppt_window_pass else "CHECK"
        pv_storage_html = f"""
  <section>
    <h2>2b. DC-Side PV Array &amp; Battery Storage (BS 7671 Sec. 712 / EAC 2026 PVS-S-1.2)</h2>
    <table>
      <thead>
        <tr><th style="width:32%">Parameter</th><th>Engineered Specification</th></tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Hybrid Inverter</strong></td>
          <td>{pvs.inverter_ac_kva:g} kVA ({pvs.inverter_phases}P,
            Vdc_max={pvs.inverter_max_dc_v}V, MPPT {pvs.mppt_min_v}–{pvs.mppt_max_v}V)</td>
        </tr>
        <tr>
          <td><strong>PV Array &amp; Strings</strong></td>
          <td>{pvs.module_count} × {pvs.module_wp} Wp =
            <strong>{pvs.array_dc_kwp:g} kWp</strong>
            ({pvs.strings_count} × {pvs.modules_per_string}s,
            DC/AC {pvs.dc_ac_ratio:.2f})</td>
        </tr>
        <tr>
          <td><strong>Cyprus Voc(0°C) / Vmp(70°C)</strong></td>
          <td>Voc(0°C) = {pvs.string_voc_cold_0c_v:.1f} V ({voc_st}) ·
            Vmp(70°C) = {pvs.string_vmp_hot_70c_v:.1f} V ({mppt_st})</td>
        </tr>
        <tr>
          <td><strong>Solar DC Cabling &amp; ΔV</strong></td>
          <td>{esc(pvs.dc_cable_spec)} ({pvs.dc_run_length_m:.1f} m,
            ΔV_DC = {pvs.dc_voltage_drop_pct:.2f}%)</td>
        </tr>
        <tr>
          <td><strong>DC Protection &amp; Grid Control</strong></td>
          <td>{esc(pvs.dc_isolator_spec)} · {esc(pvs.dc_spd_spec)} ·
            {esc(pvs.eac_2026_drg_ref)}</td>
        </tr>
        {bess_tr}
      </tbody>
    </table>
  </section>
"""

    standards_list = " | ".join(
        f"{esc(k)}: <code>{esc(v)}</code>"
        for k, v in sorted(prov.standards_versions.items())
    )
    earthing_txt = f"{esc(result.earthing_system.value)} (Ze/Ra={result.ze_or_ra_ohm:.2f} Ω)"
    demand_txt = f"{eac.total_diversified_kva:.2f} / {eac.total_connected_kva:.2f} kVA"
    default_logo_uri = get_logo_svg_data_uri()
    header_logo_uri = (
        result.branding.logo_data_uri
        if (result.branding is not None and result.branding.logo_data_uri)
        else default_logo_uri
    )

    return f"""<!DOCTYPE html>
<html lang="{esc(lang.value if lang.value != 'bilingual' else 'en')}">
<head>
  <meta charset="utf-8" />
  <link rel="icon" type="image/svg+xml" href="{default_logo_uri}" />
  <title>{esc(t('report_title', lang))} — {esc(result.building.project_name)}</title>
  <style>
    @page {{ size: A4 landscape; margin: 12mm; }}
    body {{
      font-family: "Noto Sans", "DejaVu Sans", Arial, sans-serif;
      font-size: 9.5pt;
      color: #1a202c;
      margin: 0;
      padding: 16px;
      background: #ffffff;
    }}
    header {{
      border-bottom: 3px solid #1b365d;
      padding-bottom: 8px;
      margin-bottom: 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .hdr-brand {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .hdr-logo {{
      width: 30px;
      height: 30px;
      border-radius: 5px;
      object-fit: contain;
    }}
    h1 {{ font-size: 14pt; color: #1b365d; margin: 0; }}
    h2 {{
      font-size: 11pt;
      color: #1b365d;
      margin: 14px 0 6px 0;
      border-bottom: 1px solid #cbd5e0;
      padding-bottom: 2px;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      background: #f7fafc;
      border: 1px solid #e2e8f0;
      padding: 8px 10px;
      border-radius: 4px;
      font-size: 8.5pt;
    }}
    .notice {{
      background: #ebf8ff;
      border-left: 4px solid #2b6cb0;
      padding: 6px 10px;
      margin: 8px 0 12px 0;
      font-size: 8.5pt;
    }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 8.2pt; }}
    table.compact {{ width: 65%; }}
    th, td {{ border: 1px solid #cbd5e0; padding: 4px 6px; vertical-align: middle; }}
    th {{ background: #1b365d; color: #ffffff; font-weight: 600; text-align: left; }}
    tbody tr:nth-child(even) {{ background: #f8fafc; }}
    .r {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .c {{ text-align: center; }}
    .badge {{
      display: inline-block;
      padding: 1px 6px;
      border-radius: 3px;
      font-weight: 700;
      font-size: 7.5pt;
    }}
    .badge-pass {{ background: #d4edda; color: #155724; }}
    .badge-warn {{ background: #fff3cd; color: #856404; }}
    .badge-fail {{ background: #f8d7da; color: #721c24; }}
    .badge-info {{ background: #d9e2ec; color: #1b365d; }}
    .signoff {{
      margin-top: 16px;
      border: 1px solid #1b365d;
      padding: 10px;
      page-break-inside: avoid;
    }}
    .signoff-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-top: 18px;
    }}
    .sig-line {{ border-top: 1px solid #4a5568; padding-top: 4px; font-size: 8pt; color: #4a5568; }}
    code {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; }}
    footer {{
      margin-top: 12px;
      font-size: 7.5pt;
      color: #718096;
      border-top: 1px solid #e2e8f0;
      padding-top: 4px;
    }}
  </style>
</head>
<body>
  <header>
    <div class="hdr-brand">
      <img src="{esc(header_logo_uri)}" alt="CyElec Logo" class="hdr-logo" />
      <h1>{esc(t('report_title', lang))} — {esc(result.building.project_name)}</h1>
    </div>
    <div>
      <code>cyelec-pipeline v{esc(prov.pipeline_version)}</code> | {esc(prov.timestamp_utc)}
    </div>
  </header>

  {branding_html}

  <div class="notice">
    <strong>Statutory ETEK Review Notice:</strong> {esc(t('statutory_notice', lang))}
  </div>

  <div class="meta-grid">
    <div><strong>Building Type:</strong> {esc(result.building.building_type.value)}</div>
    <div><strong>Floor Area:</strong> {result.building.total_floor_area_m2:.2f} m²</div>
    <div><strong>EAC Profile:</strong> {esc(eac.eac_profile.value)}</div>
    <div><strong>Bathroom Exclusion:</strong> {eac.bathroom_socket_exclusion_m:.1f} m</div>
    <div><strong>Earthing:</strong> {earthing_txt}</div>
    <div>
      <strong>Recommended Supply:</strong>
      {esc(eac.recommended_supply_tier)} ({eac.supply_tier_utilization_pct:.1f}%)
    </div>
    <div><strong>Diversified / Connected:</strong> {demand_txt}</div>
    <div><strong>Input SHA-256:</strong> <code>{esc(prov.input_sha256[:16])}</code></div>
  </div>

  {balance_section}
  {submains_section}
  {pv_storage_html}

  <section>
    <h2>4. BS 7671 Final Circuit Calculations (Distributed Multi-Node)</h2>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>Description</th><th class="c">Ph</th><th>Topology</th>
          <th class="r">Ib (A)</th><th>Device / Cat. Ref</th><th class="c">Cable/CPC (mm²)</th>
          <th class="r">L_mat/L_el (m)</th><th class="r">Ca / Cg / Ci</th>
          <th class="r">Iz / Req (A)</th><th class="r">ΔV (%)</th>
          <th class="c">Earth Fault / Ik1</th><th class="r">CPC Min</th>
          <th>Upsize</th><th class="c">Status</th>
        </tr>
      </thead>
      <tbody>
        {''.join(circuit_rows_html)}
      </tbody>
    </table>
  </section>

  {''.join([
      '<section><h2>4b. IEC 60909 Short-Circuit Profile &amp; Selectivity (E-203)</h2>'
      '<table><thead><tr><th>Board</th><th>Upstream</th><th>Downstream Max</th>'
      '<th class="r">Ik3 / Ik1 (kA)</th><th class="r">Icu (kA)</th>'
      '<th class="r">Is Limit (kA)</th><th>Vendor Ref</th><th class="c">Selectivity</th>'
      '</tr></thead><tbody>'
      + ''.join(
          f'<tr><td><strong>{esc(b.board_id)}</strong></td>'
          f'<td>{esc(b.upstream_device_label)}</td>'
          f'<td>{esc(b.downstream_max_device_label)}</td>'
          f'<td class="r">{b.ik3_max_ka:.2f} / {b.ik1_min_ka:.2f}</td>'
          f'<td class="r">{b.device_icu_ka:g}</td>'
          f'<td class="r">{b.discrimination_limit_is_ka:.1f}</td>'
          f'<td><small>{esc(b.vendor_coordination_ref or "-")}</small></td>'
          f'<td class="c"><span class="badge '
          f'{"badge-pass" if b.selectivity_type != "check_required" else "badge-warn"}">'
          f'{esc(b.selectivity_type.upper())}</span></td></tr>'
          for b in result.selectivity_analysis.boards
      )
      + '</tbody></table></section>'
  ]) if (result.selectivity_analysis and result.selectivity_analysis.boards) else ''}

  {''.join([
      '<section><h2>4d. Main &amp; Secondary Distribution Board Designs (IEC 61439 / E-204)</h2>'
      '<table><thead><tr><th>Board</th><th>Role</th><th>Parent</th><th>Standard</th>'
      '<th>Dimensions</th><th class="c">IP</th><th class="r">Busbar</th>'
      '<th class="c">Modules (Spare)</th><th class="c">Thermal (W)</th></tr></thead><tbody>'
      + ''.join(
          f'<tr><td><strong>{esc(bd.board_id)}</strong> <small>({esc(bd.floor)})</small></td>'
          f'<td>{esc(bd.board_role)}</td>'
          f'<td>{esc(bd.parent_board_id or "EAC Origin")}</td>'
          f'<td><small>{esc(bd.standard_ref)}</small></td>'
          f'<td><small>{esc(bd.enclosure_dims_mm)}</small></td>'
          f'<td class="c">{esc(bd.ip_rating)}</td>'
          f'<td class="r">{bd.busbar_rating_a} A</td>'
          f'<td class="c">{bd.total_modules_used}/{bd.total_modules_capacity}M '
          f'({bd.spare_pct:.0f}%)</td>'
          f'<td class="c"><span class="badge '
          f'{"badge-pass" if bd.thermal_pass else "badge-fail"}">'
          f'{bd.power_dissipation_w:.1f}/{bd.max_enclosure_dissipation_w:.0f}W</span></td></tr>'
          for bd in result.din_rail_boards
      )
      + '</tbody></table></section>'
  ]) if result.din_rail_boards else ''}

  {''.join([
      '<section><h2>4c. Point-by-Point 2D Photometric Illuminance &amp; Uniformity '
      '(BS EN 12464-1)</h2>'
      '<table><thead><tr><th>Room</th><th class="r">Area (m²)</th><th class="c">Fixtures</th>'
      '<th class="r">Target (lx)</th><th class="r">E_avg (lx)</th>'
      '<th class="r">E_min / E_max</th><th class="r">U0 / Target</th>'
      '<th class="r">Grid Pts</th><th class="c">Status</th></tr></thead><tbody>'
      + ''.join(
          f'<tr><td>{esc(lc.room_label)}</td>'
          f'<td class="r">{lc.area_m2:.1f}</td>'
          f'<td class="c">{lc.luminaire_count} × {lc.watts_per_fixture:g}W</td>'
          f'<td class="r">{lc.target_lux}</td>'
          f'<td class="r"><strong>{lc.achieved_lux:.0f}</strong></td>'
          f'<td class="r">'
          f'{f"{lc.min_lux:.0f} / {lc.max_lux:.0f}" if lc.min_lux is not None else "-"}</td>'
          f'<td class="r">'
          f'{f"{lc.uniformity_u0:.2f}/{lc.u0_target:.2f}" if lc.uniformity_u0 is not None else "-"}'
          '</td>'
          f'<td class="r">{lc.grid_points_evaluated}</td>'
          f'<td class="c"><span class="badge '
          f'{"badge-pass" if (lc.compliant and lc.u0_compliant) else "badge-warn"}">'
          f'{"PASS" if (lc.compliant and lc.u0_compliant) else "CHECK"}</span></td></tr>'
          for lc in result.lumen_checks
      )
      + '</tbody></table></section>'
  ]) if result.lumen_checks else ''}

  <section>
    <h2>5. ETEK Human Review Flags Register</h2>
    <table>
      <thead>
        <tr>
          <th class="c">Severity</th><th>Code</th><th>Category</th><th class="c">Circuit</th>
          <th>Regulation</th><th>Details</th>
        </tr>
      </thead>
      <tbody>
        {''.join(flag_rows_html)}
      </tbody>
    </table>
  </section>

  {boq_section}

  <section class="signoff">
    <strong>6. {esc(t('etek_signoff', lang))}</strong>
    <div class="signoff-grid">
      <div class="sig-line">{esc(eng_name or 'ETEK Electrical Engineer Name')}</div>
      <div class="sig-line">{esc(etek_no or 'ETEK Registration Number')}</div>
      <div class="sig-line">Signature &amp; Official Stamp</div>
      <div class="sig-line">Date</div>
    </div>
  </section>

  <footer>
    Standards Provenance: {standards_list}
  </footer>
</body>
</html>
"""


def export_calc_reports(
    result: ElectricalDesignResult,
    out_md: Path,
    out_html: Path,
) -> tuple[Path, Path]:
    """Write both Markdown and print-ready A4-landscape HTML calculation reports."""
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_calc_report_md(result), encoding="utf-8")
    out_html.write_text(render_calc_report_html(result), encoding="utf-8")
    return out_md, out_html
