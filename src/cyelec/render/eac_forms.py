"""Official Cyprus EAC (ΑΗΚ) & HMY 58 (ΗΜΥ 58) Bilingual Plan Approval & Test Pack Generator."""

from __future__ import annotations

from html import escape as esc
from pathlib import Path

from cyelec.models.building import BuildingType
from cyelec.models.electrical import (
    CircuitCategory,
    EacProjectMetadata,
    ElectricalDesignResult,
    ReviewSeverity,
)
from cyelec.models.spatial import Phase2DesignResult
from cyelec.render.templates import load_indented_css, render_template

EAC_DISTRICT_EMAILS: dict[str, str] = {
    "Nicosia / Λευκωσία": "amc-lefkosia@eac.com.cy",
    "Limassol / Λεμεσός": "amc-lemesos@eac.com.cy",
    "Larnaca-Famagusta / Λάρνακα-Αμμόχωστος": "amc-larnaca@eac.com.cy",
    "Paphos / Πάφος": "amc-pafos@eac.com.cy",
}


def _suggested_eac_tariff(btype: BuildingType) -> str:
    if btype in (BuildingType.OFFICE, BuildingType.SHOP, BuildingType.WORKSHOP):
        return "Code 10 — Commercial / Εμπορική Διατίμηση (verify with EAC)"
    return "Code 01 — Domestic / Οικιακή Διατίμηση"


def build_eac_application_rows(result: Phase2DesignResult) -> list[tuple[str, str, str, str]]:
    """Build deterministic Cyprus EAC (ΑΗΚ) Plan Approval & Ε-ΔΔ-516 Schedule rows."""
    d = result.design
    b = d.building
    eac = d.eac_compliance
    pb = d.phase_balance
    br = d.branding

    has_emg = any(c.spec.category == CircuitCategory.EMERGENCY_LIGHTING for c in d.circuits)
    has_fa = any(c.spec.category == CircuitCategory.FIRE_ALARM for c in d.circuits)
    has_ups = any(c.spec.category == CircuitCategory.UPS_SERVER for c in d.circuits)
    has_lift = any(c.spec.category == CircuitCategory.LIFT for c in d.circuits)
    has_ev = b.ev_charger_kw > 0.0
    blockers = sum(1 for f in d.review_flags if f.severity == ReviewSeverity.BLOCKER)
    warnings = sum(1 for f in d.review_flags if f.severity == ReviewSeverity.WARNING)
    all_pass = all(c.overall_pass and c.live_adiabatic_pass for c in d.circuits)

    return [
        ("A. Premises / Υποστατικό", "Project / Premises Title", "Τίτλος Έργου", b.project_name),
        (
            "A. Premises / Υποστατικό",
            "Building Classification",
            "Κατηγορία Κτιρίου",
            b.building_type.value.replace("_", " ").title(),
        ),
        (
            "A. Premises / Υποστατικό",
            "Total Internal Floor Area",
            "Συνολικό Εμβαδόν",
            f"{b.total_floor_area_m2:.2f} m² ({len(b.rooms)} spaces)",
        ),
        (
            "A. Premises / Υποστατικό",
            "District / Municipality / Village",
            "Επαρχία / Δήμος / Κοινότητα",
            (
                " / ".join(
                    p
                    for p in (
                        d.eac_metadata.district.strip(),
                        d.eac_metadata.municipality.strip(),
                    )
                    if p
                )
                if d.eac_metadata
                and (d.eac_metadata.district.strip() or d.eac_metadata.municipality.strip())
                else "[Nicosia / Limassol / Larnaca / Paphos — Complete before filing]"
            ),
        ),
        (
            "A. Premises / Υποστατικό",
            "Cadastral Sheet / Plan / Plot No.",
            "Φύλλο / Σχέδιο / Αρ. Τεμαχίου",
            (
                f"Sheet/Plan: {d.eac_metadata.sheet_plan.strip() or '___'} | "
                f"Plot: {d.eac_metadata.plot_no.strip() or '___'}"
                + (
                    f" | Permit: {d.eac_metadata.building_permit_no.strip()}"
                    if d.eac_metadata.building_permit_no.strip()
                    else ""
                )
                if d.eac_metadata
                and (
                    d.eac_metadata.sheet_plan.strip()
                    or d.eac_metadata.plot_no.strip()
                    or d.eac_metadata.building_permit_no.strip()
                )
                else "[Sheet ___ / Plan ___ / Plot ___ — Attach Title Deed & Site Plan]"
            ),
        ),
        (
            "A. Premises / Υποστατικό",
            "Property Owner / Applicant",
            "Ιδιοκτήτης / Αιτητής",
            (
                d.eac_metadata.owner_name.strip()
                if d.eac_metadata and d.eac_metadata.owner_name.strip()
                else "[Owner Full Name & ID / Company Reg. No.]"
            ),
        ),
        (
            "A. Premises / Υποστατικό",
            "EAC Premises / Account No.",
            "Αρ. Υποστατικού / Λογαριασμού ΑΗΚ",
            (
                d.eac_metadata.eac_premises_no.strip()
                if d.eac_metadata and d.eac_metadata.eac_premises_no.strip()
                else "[Complete from EAC Application / Existing Bill]"
            ),
        ),
        (
            "A. Premises / Υποστατικό",
            "EAC Tariff Code",
            "Διατίμηση ΑΗΚ",
            (
                d.eac_metadata.eac_tariff.strip()
                if d.eac_metadata and d.eac_metadata.eac_tariff.strip()
                else _suggested_eac_tariff(b.building_type)
            ),
        ),
        (
            "B. ETEK Designer & Contractor / Ε-ΔΔ-516",
            "Consulting Practice",
            "Μελετητικό Γραφείο ΕΤΕΚ",
            (br.firm_name if br and br.firm_name else "[ETEK Licensed Practice]"),
        ),
        (
            "B. ETEK Designer & Contractor / Ε-ΔΔ-516",
            "Lead Electrical Engineer",
            "Υπεύθυνος Μελετητής ΕΤΕΚ",
            (br.engineer_name if br and br.engineer_name else "[Name & Signature]"),
        ),
        (
            "B. ETEK Designer & Contractor / Ε-ΔΔ-516",
            "ETEK Registration No.",
            "Αρ. Μητρώου ΕΤΕΚ",
            (br.etek_reg_no if br and br.etek_reg_no else "[ETEK-E-XXXX]"),
        ),
        (
            "B. ETEK Designer & Contractor / Ε-ΔΔ-516",
            "Licensed Installer (Class A/B/C)",
            "Αδειούχος Εγκαταστάτης (Ε-ΔΔ-514)",
            (
                " — ".join(
                    p
                    for p in (
                        d.eac_metadata.contractor_name.strip(),
                        d.eac_metadata.contractor_hmy_reg.strip(),
                    )
                    if p
                )
                if d.eac_metadata
                and (
                    d.eac_metadata.contractor_name.strip()
                    or d.eac_metadata.contractor_hmy_reg.strip()
                )
                else "[Contractor Name & HMY Licence No. — Complete at inspection]"
            ),
        ),
        (
            "B. ETEK Designer & Contractor / Ε-ΔΔ-516",
            "Applicable BS 7671 / EAC Profile",
            "Εφαρμοστέοι Κανονισμοί ΑΗΚ",
            eac.eac_profile.value,
        ),
        (
            "B. ETEK Designer & Contractor / Ε-ΔΔ-516",
            "Deterministic Input SHA-256",
            "Ψηφιακό Αποτύπωμα Μελέτης",
            d.provenance.input_sha256[:24],
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Requested EAC Supply Tier",
            "Αιτούμενη Παροχή ΑΗΚ",
            (
                f"{eac.recommended_supply_tier} "
                f"(Utilization: {eac.supply_tier_utilization_pct:.1f}%"
                + (
                    f" — Next tier: {eac.next_supply_tier}"
                    if eac.next_supply_tier and eac.supply_tier_utilization_pct >= 85.0
                    else ""
                )
                + ")"
            ),
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Supply Phases & Main Breaker",
            "Φάσεις & Κύριος Διακόπτης",
            f"{eac.supply_phases}-Phase / {eac.recommended_main_breaker_a} A",
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Total Connected / Diversified Demand",
            "Εγκατεστημένη / Ταυτόχρονη Ισχύς",
            f"{eac.total_connected_kva:.2f} kVA / {eac.total_diversified_kva:.2f} kVA",
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Peak Phase Current & 3P Balance",
            "Μέγιστο Ρεύμα Φάσης & Ισορροπία",
            (
                f"{pb.max_phase_current_a:.2f} A "
                f"(L1={pb.phase_currents_a['L1']:.1f}A, "
                f"L2={pb.phase_currents_a['L2']:.1f}A, "
                f"L3={pb.phase_currents_a['L3']:.1f}A | "
                f"Imbalance {pb.imbalance_pct:.2f}%)"
                if pb is not None
                else f"{eac.max_phase_demand_a:.2f} A (Single-Phase)"
            ),
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "CT Metering Cabinet (>100 A/phase)",
            "Θάλαμος Μετρητών CT",
            "REQUIRED (EAC CT Chamber)" if eac.ct_metering_required else "Not Required (Direct)",
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Dedicated EAC Substation (>=150 kVA)",
            "Υποσταθμός ΑΗΚ",
            (
                "MANDATORY EAC CONSULTATION"
                if eac.substation_consultation_required
                else ("WARNING (>=100 kVA)" if eac.substation_warning else "Not Required")
            ),
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Earthing System & Design Ze / Ra",
            "Σύστημα Γείωσης & Αντίσταση",
            f"{d.earthing_system.value} ({d.ze_or_ra_ohm:.2f} Ω)",
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Prospective Fault Current (Ipf)",
            "Αναμενόμενο Ρεύμα Βραχυκύκλωσης",
            f"{d.prospective_fault_current_ka:.1f} kA at Origin",
        ),
        (
            "D. Special Systems & Renewables / Ειδικές Εγκαταστάσεις",
            "Photovoltaic (PV) Net-Metering / Billing",
            "Φωτοβολταϊκό Σύστημα (Φ/Β)",
            (
                f"{b.pv_capacity_kva:g} kVA (Attach EAC RES Sheet)"
                if b.pv_capacity_kva > 0
                else "Not installed (0 kVA)"
            ),
        ),
        (
            "D. Special Systems & Renewables / Ειδικές Εγκαταστάσεις",
            "EV Charging Point (BS 7671 Sec 722)",
            "Σημείο Φόρτισης EV",
            (
                f"{b.ev_charger_kw:g} kW (Type A+6mA DC / Type B)"
                if has_ev
                else "Not installed (0 kW)"
            ),
        ),
        (
            "D. Special Systems & Renewables / Ειδικές Εγκαταστάσεις",
            "Emergency Lighting / Fire Alarm / UPS / Lift",
            "Φωτισμός Ασφαλείας / Πυρανίχνευση / UPS / Ανελκυστήρας",
            (
                f"BS 5266 Emg: {'YES' if has_emg else 'No'} | "
                f"BS 5839 Fire: {'YES' if has_fa else 'No'} | "
                f"UPS: {'YES' if has_ups else 'No'} | "
                f"Lift: {'YES' if has_lift else 'No'}"
            ),
        ),
        (
            "E. Verification & Sign-Off / Έλεγχος & Υπογραφή",
            "Final Circuits / Sub-Boards",
            "Τελικά Κυκλώματα / Υποπίνακες",
            f"{len(d.circuits)} circuits | {len(d.sub_mains)} sub-main(s)",
        ),
        (
            "E. Verification & Sign-Off / Έλεγχος & Υπογραφή",
            "BS 7671 Safety & Adiabatic Checks",
            "Έλεγχος BS 7671 & Αδιαβατικού",
            "ALL CIRCUITS PASS" if all_pass else "REQUIRES REMEDIATION (SEE FLAGS)",
        ),
        (
            "E. Verification & Sign-Off / Έλεγχος & Υπογραφή",
            "ETEK Review Flags Summary",
            "Σύνοψη Σημειώσεων Ελέγχου ΕΤΕΚ",
            f"{blockers} Blocker(s) | {warnings} Warning(s) | ETEK Sign-Off Required",
        ),
    ]


def _hmy_wiring_code(category: CircuitCategory, cable_mm2: float, is_outdoor_or_site: bool) -> str:
    """Return official Cyprus HMY 58.18-1 Table A Wiring Type Code (A–O)."""
    if is_outdoor_or_site or category in (CircuitCategory.GATE_GARAGE, CircuitCategory.EV_CHARGER):
        return "G" if cable_mm2 >= 4.0 else "F"
    if cable_mm2 >= 16.0:
        return "G"
    return "C"  # Official HMY 58.18-1 Code C: PVC insulated cables in non-metallic conduit


def _count_circuit_points(result: Phase2DesignResult) -> dict[str, int]:
    """Count placed electrical points supplied per final circuit for HMY 58.18-1 Table A."""
    counts: dict[str, int] = {}
    for dev in result.spatial.devices:
        cid = dev.circuit_id
        if cid:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def _is_commercial_type(btype: BuildingType) -> bool:
    return btype in (
        BuildingType.OFFICE,
        BuildingType.SHOP,
        BuildingType.WORKSHOP,
        BuildingType.INDUSTRIAL,
    )


def _render_pv_storage_html(d: ElectricalDesignResult) -> str:
    """Render official 2026 EAC Annex VII PV DC Array & Battery Storage HTML section."""
    pvs = d.pv_storage_design
    if pvs is None:
        return ""
    voc_badge = "PASS" if pvs.voc_cold_pass else "FAIL"
    mppt_badge = "PASS" if pvs.mppt_window_pass else "CHECK"
    dcvd_badge = "PASS" if pvs.dc_vd_pass else "CHECK"
    if pvs.battery_enabled:
        bess_rows = (
            "<tr><td><strong>Battery Storage (BESS) / Αποθήκευση</strong></td>"
            f"<td>{pvs.battery_nominal_kwh:g} kWh nominal / "
            f"{pvs.battery_usable_kwh:g} kWh usable "
            f"({esc(pvs.battery_chemistry)}, {pvs.battery_bus_voltage_v}V DC bus)</td></tr>"
            "<tr><td><strong>BESS Charge/Discharge &amp; DC Protection</strong></td>"
            f"<td>Max {pvs.battery_max_power_kw:g} kW ({pvs.battery_max_current_a:g} A DC) · "
            f"{esc(pvs.battery_dc_breaker_spec)} · {esc(pvs.battery_dc_cable_spec)}</td></tr>"
        )
        if pvs.eps_backup_enabled:
            bess_rows += (
                "<tr><td><strong>EPS Critical-Load Backup (Annex VI)</strong></td>"
                f"<td>{esc(pvs.eps_changeover_spec)}</td></tr>"
            )
    else:
        bess_rows = (
            "<tr><td><strong>Battery Storage (BESS) / Αποθήκευση</strong></td>"
            "<td>Hybrid Inverter Storage-Ready (0 kWh installed — "
            "upgradeable under EAC PVS-S-1.2)</td></tr>"
        )
    return (
        "<h2>G. Official 2026 EAC Annex VII — PV DC Array &amp; "
        "Battery Storage (DRG. PVS-S-1.2)</h2>"
        "<table><thead><tr>"
        "<th style='width:38%'>PV / Storage Parameter (BS 7671 Sec. 712 / EAC 2026)</th>"
        "<th>Engineered Value / Υπολογισμένη Τιμή</th>"
        "</tr></thead><tbody>"
        "<tr><td><strong>Hybrid Inverter AC Rating &amp; Phases</strong></td>"
        f"<td>{pvs.inverter_ac_kva:g} kVA ({pvs.inverter_phases}-Phase, "
        "EN 50549-1 NS Protection, "
        f"Vdc_max={pvs.inverter_max_dc_v}V, MPPT {pvs.mppt_min_v}–{pvs.mppt_max_v}V)</td></tr>"
        "<tr><td><strong>PV Array &amp; String Configuration</strong></td>"
        f"<td>{pvs.module_count} × {pvs.module_wp} Wp = "
        f"<strong>{pvs.array_dc_kwp:g} kWp</strong> "
        f"(DC/AC {pvs.dc_ac_ratio:.2f}) — {pvs.strings_count} string(s) × "
        f"{pvs.modules_per_string} modules/string</td></tr>"
        "<tr><td><strong>Cyprus Cold Voc (0°C) &amp; Hot Vmp (70°C)</strong></td>"
        f"<td>Voc(STC)={pvs.string_voc_stc_v:.1f}V · Voc(0°C)={pvs.string_voc_cold_0c_v:.1f}V "
        f"≤ {pvs.inverter_max_dc_v}V [<strong>{voc_badge}</strong>] · "
        f"Vmp(70°C)={pvs.string_vmp_hot_70c_v:.1f}V [<strong>{mppt_badge}</strong>]</td></tr>"
        "<tr><td><strong>Solar DC Cabling &amp; Voltage Drop</strong></td>"
        f"<td>{esc(pvs.dc_cable_spec)} (est. {pvs.dc_run_length_m:.1f} m) · "
        f"ΔV_DC = {pvs.dc_voltage_drop_pct:.2f}% ≤ 1.50% "
        f"[<strong>{dcvd_badge}</strong>]</td></tr>"
        "<tr><td><strong>DC Load-Break Isolator &amp; DC SPD</strong></td>"
        f"<td>{esc(pvs.dc_isolator_spec)} · {esc(pvs.dc_spd_spec)}</td></tr>"
        f"{bess_rows}"
        "<tr><td><strong>EAC 2026 Grid Curtailment &amp; Zero-Export</strong></td>"
        f"<td>{esc(pvs.eac_2026_drg_ref)} · Bidirectional Smart Meter + "
        "Sealable Contactor Box + CT Sensor</td></tr>"
        "</tbody></table>"
    )


def _render_commissioning_boxes(
    meta: EacProjectMetadata | None,
) -> tuple[str, str, str]:
    """Build HMY 58.18-1 bonding check boxes and site commissioning summary row."""
    mb_val = meta.measured_main_bonding_ohm if meta is not None else None
    sb_val = meta.measured_supp_bonding_ohm if meta is not None else None
    ra_val = meta.measured_ra_ohm if meta is not None else None
    ze_val = meta.measured_ze_ohm if meta is not None else None
    ir_val = meta.measured_ir_mohm if meta is not None else None
    rcd1_val = meta.measured_rcd_1x_ms if meta is not None else None
    rcd5_val = meta.measured_rcd_5x_ms if meta is not None else None
    insp_date_val = meta.inspection_date.strip() if meta is not None else ""

    mb_pass = mb_val is not None and mb_val <= 0.5
    sb_pass = sb_val is not None and sb_val <= 1.0
    ra_pass = ra_val is not None and ra_val <= 200.0
    ir_pass = ir_val is not None and ir_val >= 1.0
    rcd1_pass = rcd1_val is not None and rcd1_val <= 200.0
    rcd5_pass = rcd5_val is not None and rcd5_val <= 40.0

    mb_box = (
        f"&#9745; <strong>{mb_val:.2f} Ω ({'PASS' if mb_pass else 'FAIL >0.5Ω'})</strong>"
        if mb_val is not None
        else "&#9744; Αγωγών κύριας ισοδυναμικής γεφύρωσης (≤ 0.5 Ω)"
    )
    sb_box = (
        f"&#9745; <strong>{sb_val:.2f} Ω ({'PASS' if sb_pass else 'FAIL >1.0Ω'})</strong>"
        if sb_val is not None
        else "&#9744; Αγωγών συμπληρωματικής ισοδυναμικής γεφύρωσης"
    )

    comm_items: list[str] = []
    if ze_val is not None:
        comm_items.append(f"Ze={ze_val:.2f} Ω")
    if ra_val is not None:
        comm_items.append(f"Ra={ra_val:.1f} Ω ({'PASS' if ra_pass else 'CHECK >200Ω'})")
    if mb_val is not None:
        comm_items.append(
            f"Main Bonding={mb_val:.2f} Ω ({'PASS ≤0.5Ω' if mb_pass else 'FAIL >0.5Ω'})"
        )
    if ir_val is not None:
        comm_items.append(f"IR={ir_val:.1f} MΩ ({'PASS ≥1MΩ' if ir_pass else 'FAIL <1MΩ'})")
    if rcd1_val is not None:
        comm_items.append(
            f"RCD 1×IΔn={rcd1_val:.0f} ms ({'PASS ≤200ms' if rcd1_pass else 'FAIL'})"
        )
    if rcd5_val is not None:
        comm_items.append(
            f"RCD 5×IΔn={rcd5_val:.0f} ms ({'PASS ≤40ms' if rcd5_pass else 'FAIL'})"
        )
    comm_summary_row = ""
    if comm_items or insp_date_val:
        dt_part = (
            f"<strong>Inspection Date:</strong> {esc(insp_date_val)} &nbsp;|&nbsp; "
            if insp_date_val
            else ""
        )
        comm_txt = " · ".join(comm_items) if comm_items else "Entered on site"
        comm_summary_row = (
            "<tr>"
            "<td><strong>Μετρήσεις Ελέγχου / Site Commissioning</strong></td>"
            f"<td colspan='3'>{dt_part}{esc(comm_txt)}</td>"
            "</tr>"
        )
    return mb_box, sb_box, comm_summary_row


def _render_grouped_application_tables(rows: list[tuple[str, str, str, str]]) -> str:
    """Group EAC application schedule rows into bilingual HTML tables per section."""
    grouped_html: list[str] = []
    cur_section = ""
    buf: list[str] = []
    for sec, en_lbl, el_lbl, val in rows:
        if sec != cur_section:
            if buf:
                grouped_html.append(
                    f"<h2>{esc(cur_section)}</h2>"
                    "<table><thead><tr>"
                    "<th style='width:34%'>Parameter (EN)</th>"
                    "<th style='width:30%'>Παράμετρος (EL)</th>"
                    "<th>Declared Value / Δηλωθείσα Τιμή</th>"
                    f"</tr></thead><tbody>{''.join(buf)}</tbody></table>"
                )
                buf = []
            cur_section = sec
        buf.append(
            f"<tr><td><strong>{esc(en_lbl)}</strong></td>"
            f"<td>{esc(el_lbl)}</td>"
            f"<td>{esc(val)}</td></tr>"
        )
    if buf:
        grouped_html.append(
            f"<h2>{esc(cur_section)}</h2>"
            "<table><thead><tr>"
            "<th style='width:34%'>Parameter (EN)</th>"
            "<th style='width:30%'>Παράμετρος (EL)</th>"
            "<th>Declared Value / Δηλωθείσα Τιμή</th>"
            f"</tr></thead><tbody>{''.join(buf)}</tbody></table>"
        )
    return "".join(grouped_html)


def _render_hmy_table_a_and_b_rows(
    result: Phase2DesignResult,
) -> tuple[str, str, str, str]:
    """Build sub-main section, HMY Table A circuit rows, Table B inspection rows & checklist."""
    d = result.design
    eac = d.eac_compliance
    points_by_cid = _count_circuit_points(result)

    submain_section = ""
    if d.sub_mains:
        sm_rows = "".join(
            f"<tr><td><strong>{esc(sm.board_id)}</strong></td>"
            f"<td>{esc(sm.parent_board_id)}</td>"
            f"<td>{esc(sm.floor)}</td>"
            f"<td>{esc(sm.device_label)}</td>"
            f"<td>{sm.cable_mm2:g}/{sm.cpc_mm2:g} mm² ({sm.length_m:.1f} m)</td>"
            f"<td>{sm.cumulative_lighting_vd_pct:.2f}% / {sm.cumulative_power_vd_pct:.2f}%</td>"
            f"<td>"
            f"{f'{sm.downstream_ipf_ka:.2f} kA' if sm.downstream_ipf_ka is not None else '-'}"
            "</td>"
            "</tr>"
            for sm in d.sub_mains
        )
        submain_section = (
            "<h2>F. Sub-Distribution Boards &amp; Sub-Mains / Υποπίνακες</h2>"
            "<table><thead><tr>"
            "<th>Board</th><th>Parent</th><th>Floor</th><th>Upstream Protection</th>"
            "<th>Sub-Main Cable</th><th>Cumul. L / P ΔV</th><th>Downstream Ipf</th>"
            f"</tr></thead><tbody>{sm_rows}</tbody></table>"
        )

    hmy_rows_parts: list[str] = []
    for c in d.circuits:
        is_site_cct = any(
            tok in c.spec.description.lower()
            for tok in ("external", "garden", "gate", "ev charger", "pool")
        )
        wcode = _hmy_wiring_code(c.spec.category, c.cable_mm2, is_site_cct)
        pts_n = points_by_cid.get(c.spec.circuit_id, max(1, len(c.spec.room_ids)))
        is_ring = c.spec.topology.value == "ring_final"
        ring_cell = "&#9744;" if is_ring else "&mdash;"
        rcd_cell = f"{c.spec.rcd_idn_ma}mA" if c.spec.rcd_idn_ma else "&mdash;"
        hmy_rows_parts.append(
            "<tr>"
            f"<td>{esc(c.spec.board_id)}</td>"
            f"<td><strong>{esc(c.spec.circuit_id)}</strong>/{esc(c.phase.value)}</td>"
            f"<td>{esc(c.spec.description)}</td>"
            f"<td style='text-align:center'><strong>{wcode}</strong></td>"
            f"<td style='text-align:center'>{pts_n}</td>"
            f"<td>{c.cable_mm2:g}/{c.cpc_mm2:g}</td>"
            f"<td>{esc(c.device_label)}</td>"
            f"<td style='text-align:center'>{rcd_cell}</td>"
            f"<td>{c.zs_max_ohm:.2f}</td>"
            f"<td></td>"
            f"<td style='text-align:center'>{ring_cell}</td>"
            f"<td></td><td></td><td></td>"
            f"<td>{c.voltage_drop_pct:.2f}%</td>"
            f"<td></td>"
            "</tr>"
        )
    hmy_rows = "".join(hmy_rows_parts)

    checklist_items: list[tuple[str, bool]] = [
        (
            "1. Έντυπο Υποβολής Αίτησης για Έλεγχο Ηλεκτρολογικής Εγκατάστασης (Ε-ΔΔ-707)",
            False,
        ),
        (
            "2. Υπεύθυνη Δήλωση Αδειούχου Μελετητή ΕΤΕΚ & Εγκαταστάτη (Έντυπο Ε-ΔΔ-516)",
            True,
        ),
        (
            "3. Πιστοποιητικό Καταλληλότητας Ηλεκτρικής Εγκατάστασης "
            "(Έντυπο Η.Μ.Υ. 58.18-1 — Πίνακες Α & Β)",
            True,
        ),
        (
            "4. Δήλωση Ηλεκτρολόγου ότι η Εγκατάσταση είναι Έτοιμη για Έλεγχο (Έντυπο Ε-ΔΔ-514)",
            True,
        ),
        ("5. Μονογραμμικά Ηλεκτρολογικά Διαγράμματα (SLD — E-201 with Earthing & SPD)", True),
        ("6. Ηλεκτρολογικές Κατόψεις ISO A3/A1 @ 1:50–1:100 (E-101, E-102, E-103, E-104)", True),
        ("7. Αντίγραφο Άδειας Μελετητή ΕΤΕΚ & Εγκαταστάτη ΗΜΥ + Κτηματολογικό Σχέδιο", False),
        ("8. BS 7671 Load Schedule, Voltage Drop, Zs & Adiabatic Calculation Pack", True),
    ]
    if d.building.pv_capacity_kva > 0:
        checklist_items.extend(
            [
                (
                    "9. Δήλωση Εγκαταστάτη Φ/Β ≤30 kW (Ε-ΔΔ-709) & "
                    "Υπεύθυνη Δήλωση Μελετητή (Ε-ΔΔ-744 / Παράρτημα VI)",
                    False,
                ),
                (
                    "10. Πίνακας Παραμέτρων Φ/Β & Αποθήκευσης (Παράρτημα VII) + "
                    "Βεβαίωση ≤20 kW (Παράρτημα IV) + Δέκτες R1/R2",
                    False,
                ),
            ]
        )
    if d.building.ev_charger_kw > 0:
        checklist_items.append(
            (
                "EV. Αίτηση-Ενημέρωση & Πίνακας Παραμέτρων Φόρτισης EV "
                "(BS 7671 Sec. 722 / EAC EV Guide 2026)",
                False,
            )
        )
    if eac.ct_metering_required or eac.substation_consultation_required:
        checklist_items.append(
            (
                "HV/CT. EAC CT Metering Chamber (>100 A/ph) / "
                "Dedicated Substation (>=150 kVA) Layout Drawings",
                False,
            )
        )
    chk_rows = "".join(
        f"<tr><td style='width:28px;text-align:center'>&#9744;</td>"
        f"<td>{esc(item)}</td>"
        f"<td style='width:128px'>"
        f"{'Included in Pack' if included else 'Applicant / Site Filing'}</td>"
        f"</tr>"
        for item, included in checklist_items
    )

    table_b_items = [
        (
            "1. Παρουσία RCD (30 mA / 100 mA Type-S)",
            "11. Επάρκεια πρόσβασης σε διακόπτες & πίνακες",
        ),
        (
            "2. Αγωγός γείωσης & CPC κυκλωμάτων (≤ 1.0 Ω)",
            "12. Ειδικοί χώροι (Μπάνιο Sec. 701 / Πισίνα / EV 722)",
        ),
        (
            "3. Κύρια ισοδυναμική γεφύρωση (10 mm² Cu, ≤ 0.5 Ω)",
            "13. Ορθή σύνδεση συσκευών & εξοπλισμού",
        ),
        (
            "4. Συμπληρωματική ισοδυναμική γεφύρωση (όπου απαιτ.)",
            "14. Επιλογή & ρύθμιση συσκευών προστασίας (MCB/RCBO)",
        ),
        (
            "5. Παρουσία διαγραμμάτων (SLD E-201) εντός Πίνακα",
            "15. Επιλογή εξοπλισμού διακοπής & απόζευξης",
        ),
        (
            "6. Πινακίδες κινδύνου, RCD test & PV dual-supply",
            "16. Εξωτερικές επιδράσεις (IP44 wet / IP65 outdoor)",
        ),
        (
            "7. Σήμανση κυκλωμάτων, διακοπτών & αποζευκτών",
            "17. Προστασία χωστών καλωδίων σε τοίχους / δάπεδα",
        ),
        (
            "8. Ορθή σύνδεση & σύσφιξη αγωγών",
            "18. Παρουσία προστασίας έναντι Υπέρτασης (SPD)",
        ),
        (
            "9. Διατομή αγωγών (Iz ≥ In) & πτώση τάσης (≤3%/5%)",
            "19. Ένδειξη ότι το SPD είναι λειτουργικό (green status)",
        ),
        (
            "10. Χωροθέτηση εξοπλισμού απομόνωσης & διακοπής",
            "Β. Μετρήσεις: Ra/Ze, Ipf, RCD (≤200ms/≤40ms), IR≥1MΩ",
        ),
    ]
    table_b_rows = "".join(
        f"<tr><td style='width:44%'>{esc(left)}</td>"
        f"<td style='width:6%;text-align:center'>&#9744;</td>"
        f"<td style='width:44%'>{esc(right)}</td>"
        f"<td style='width:6%;text-align:center'>&#9744;</td></tr>"
        for left, right in table_b_items
    )
    return submain_section, hmy_rows, chk_rows, table_b_rows


def render_eac_application_form_html(result: Phase2DesignResult) -> str:
    """Render official Cyprus EAC (Ε-ΔΔ-707/516/514) & HMY 58.18-1 Certificate Pack HTML."""
    d = result.design
    eac = d.eac_compliance
    rows = build_eac_application_rows(result)
    is_comm = _is_commercial_type(d.building.building_type)
    lps_req = bool(d.lightning_risk and d.lightning_risk.lps_required)

    reinspection_text = (
        "5 (Πέντε) χρόνια — Commercial / Industrial (IET GN3)"
        if is_comm
        else "10 (Δέκα) χρόνια — Domestic Residential (IET GN3)"
    )
    main_feeder_vd_pct = 0.50
    max_sub_vd_pct = max((sm.voltage_drop_pct for sm in d.sub_mains), default=0.0)
    upstream_vd_pct = round(main_feeder_vd_pct + max_sub_vd_pct, 2)
    rem_lighting_vd_pct = max(0.50, round(3.0 - upstream_vd_pct, 2))
    rem_power_vd_pct = max(1.00, round(5.0 - upstream_vd_pct, 2))

    main_a = eac.recommended_main_breaker_a
    cores = 4 if eac.supply_phases == 3 else 2
    if eac.ct_metering_required or main_a > 100:
        main_cable_spec = f"Code G (XLPE/SWA) · {cores}×50 mm² Cu / 25 mm² PE"
    elif main_a > 63:
        main_cable_spec = f"Code C (XLPE/PVC) · {cores}×25 mm² Cu / 16 mm² PE"
    else:
        main_cable_spec = f"Code C (XLPE/PVC) · {cores}×16 mm² Cu / 16 mm² PE"

    meta = d.eac_metadata
    is_overhead = bool(meta is not None and meta.supply_connection_type == "overhead")
    spd_type12 = lps_req or is_overhead
    spd_type = "Type 1+2 (Class I+II)" if spd_type12 else "Type 2 (Class II)"
    spd_iimp = "12.5 kA (10/350µs)" if spd_type12 else "N/A (Underground / No LPS*1)"
    spd_in = "20 kA nominal / 40 kA Imax (8/20µs)"
    spd_up = "≤ 1.5 kV"
    spd_uc = "275 V AC (L-N / L-PE)"

    supply_chk_html = (
        "&#9745; <strong>Εναέρια (Overhead)</strong> &nbsp; &#9744; Υπόγεια (Underground)"
        if is_overhead
        else "&#9744; Εναέρια (Overhead) &nbsp; &#9745; <strong>Υπόγεια (Underground)</strong>"
    )
    mb_box, sb_box, comm_summary_row = _render_commissioning_boxes(meta)
    grouped_html = _render_grouped_application_tables(rows)
    submain_section, hmy_rows, chk_rows, table_b_rows = _render_hmy_table_a_and_b_rows(result)
    pv_storage_section = _render_pv_storage_html(d)
    district_rows = "".join(
        f"<tr><td>{esc(dist)}</td><td><code>{esc(email)}</code></td></tr>"
        for dist, email in EAC_DISTRICT_EMAILS.items()
    )
    poles_lbl = "4P" if eac.supply_phases == 3 else "2P"
    origin_rcd_lbl = (
        "100 mA Type-S (130–500 ms)"
        if d.earthing_system.value == "TT"
        else "N/A (TN-C-S — 30 mA RCBOs at DB)"
    )
    return render_template(
        "eac_application_form.html",
        {
            "__PROJECT_NAME__": esc(d.building.project_name),
            "__EAC_FORM_CSS__": load_indented_css("eac_application_form.css"),
            "__GROUPED_HTML__": grouped_html,
            "__SUBMAIN_SECTION__": submain_section,
            "__PV_STORAGE_SECTION__": pv_storage_section,
            "__REINSPECTION_TEXT__": esc(reinspection_text),
            "__EARTHING_DETAIL__": (
                f"{esc(d.earthing_system.value)} (Design Ze/Ra = {d.ze_or_ra_ohm:.2f} Ω)"
            ),
            "__SUPPLY_CHK_HTML__": supply_chk_html,
            "__MAIN_CABLE_SPEC__": esc(main_cable_spec),
            "__MAIN_FEEDER_VD_PCT__": f"{main_feeder_vd_pct:.2f}",
            "__REM_LIGHTING_VD_PCT__": f"{rem_lighting_vd_pct:.2f}",
            "__REM_POWER_VD_PCT__": f"{rem_power_vd_pct:.2f}",
            "__MAIN_BREAKER_A__": str(eac.recommended_main_breaker_a),
            "__POLES_LBL__": poles_lbl,
            "__IPF_KA__": f"{d.prospective_fault_current_ka:.1f}",
            "__ORIGIN_RCD_LBL__": esc(origin_rcd_lbl),
            "__COMM_SUMMARY_ROW__": comm_summary_row,
            "__MB_BOX__": mb_box,
            "__SB_BOX__": sb_box,
            "__SPD_TYPE__": esc(spd_type),
            "__SPD_IIMP__": esc(spd_iimp),
            "__SPD_IN__": esc(spd_in),
            "__SPD_UP__": esc(spd_up),
            "__SPD_UC__": esc(spd_uc),
            "__HMY_ROWS__": hmy_rows,
            "__TABLE_B_ROWS__": table_b_rows,
            "__CHK_ROWS__": chk_rows,
            "__DISTRICT_ROWS__": district_rows,
        },
    )


def export_eac_application_form(
    result: Phase2DesignResult,
    out_dir: Path,
    *,
    export_pdf: bool = False,
) -> dict[str, Path]:
    """Write Cyprus EAC (Ε-ΔΔ-516) + HMY 58 Application Form HTML (and optional WeasyPrint PDF)."""
    from cyelec.render.pdf_export import export_html_to_pdf

    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "eac_application_form.html"
    html_path.write_text(render_eac_application_form_html(result), encoding="utf-8")
    out: dict[str, Path] = {"eac_form_html": html_path}
    if export_pdf:
        pdf_path, _ = export_html_to_pdf(html_path, out_dir / "eac_application_form.pdf")
        if pdf_path is not None:
            out["eac_form_pdf"] = pdf_path
    return out
