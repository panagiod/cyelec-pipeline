"""Official Cyprus EAC (ΑΗΚ) & HMY 58 (ΗΜΥ 58) Bilingual Plan Approval & Test Pack Generator."""

from __future__ import annotations

from html import escape as esc
from pathlib import Path

from cyelec.models.building import BuildingType
from cyelec.models.electrical import CircuitCategory, ReviewSeverity
from cyelec.models.spatial import Phase2DesignResult

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


def render_eac_application_form_html(result: Phase2DesignResult) -> str:
    """Render official Cyprus EAC (Ε-ΔΔ-707/516/514) & HMY 58.18-1 Certificate Pack HTML."""
    d = result.design
    eac = d.eac_compliance
    rows = build_eac_application_rows(result)
    is_comm = _is_commercial_type(d.building.building_type)
    lps_req = bool(d.lightning_risk and d.lightning_risk.lps_required)
    points_by_cid = _count_circuit_points(result)

    # Official HMY 58.18-1 Section Θ re-inspection period (IET GN3 / Cyprus EMS)
    reinspection_text = (
        "5 (Πέντε) χρόνια — Commercial / Industrial (IET GN3)"
        if is_comm
        else "10 (Δέκα) χρόνια — Domestic Residential (IET GN3)"
    )

    # Official HMY 58.18-1 Footnote (1): remaining permissible circuit VD after main feeder VD
    main_feeder_vd_pct = 0.50
    max_sub_vd_pct = max((sm.voltage_drop_pct for sm in d.sub_mains), default=0.0)
    upstream_vd_pct = round(main_feeder_vd_pct + max_sub_vd_pct, 2)
    rem_lighting_vd_pct = max(0.50, round(3.0 - upstream_vd_pct, 2))
    rem_power_vd_pct = max(1.00, round(5.0 - upstream_vd_pct, 2))

    # Main supply cable spec matching SLD (E-201)
    main_a = eac.recommended_main_breaker_a
    cores = 4 if eac.supply_phases == 3 else 2
    if eac.ct_metering_required or main_a > 100:
        main_cable_spec = f"Code G (XLPE/SWA) · {cores}×50 mm² Cu / 25 mm² PE"
    elif main_a > 63:
        main_cable_spec = f"Code C (XLPE/PVC) · {cores}×25 mm² Cu / 16 mm² PE"
    else:
        main_cable_spec = f"Code C (XLPE/PVC) · {cores}×16 mm² Cu / 16 mm² PE"

    # Official HMY 58.18-1 Section I SPD parameters (Footnote *1: overhead or LPS -> Type 1+2)
    meta = d.eac_metadata
    is_overhead = bool(meta is not None and meta.supply_connection_type == "overhead")
    spd_type12 = lps_req or is_overhead
    spd_type = "Type 1+2 (Class I+II)" if spd_type12 else "Type 2 (Class II)"
    spd_iimp = (
        "12.5 kA (10/350µs)"
        if spd_type12
        else "N/A (Underground / No LPS*1)"
    )
    spd_in = "20 kA nominal / 40 kA Imax (8/20µs)"
    spd_up = "≤ 1.5 kV"
    spd_uc = "275 V AC (L-N / L-PE)"

    # Official 2026 EAC Παράρτημα VII — PV & Battery Storage Technical Parameters Table
    pv_storage_section = ""
    pvs = d.pv_storage_design
    if pvs is not None:
        voc_badge = "PASS" if pvs.voc_cold_pass else "FAIL"
        mppt_badge = "PASS" if pvs.mppt_window_pass else "CHECK"
        dcvd_badge = "PASS" if pvs.dc_vd_pass else "CHECK"
        bess_rows = ""
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
        pv_storage_section = (
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

    # Commissioning measurements pre-fill (optional engineer-entered site test readings)
    supply_chk_html = (
        "&#9745; <strong>Εναέρια (Overhead)</strong> &nbsp; &#9744; Υπόγεια (Underground)"
        if is_overhead
        else "&#9744; Εναέρια (Overhead) &nbsp; &#9745; <strong>Υπόγεια (Underground)</strong>"
    )
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

    # Official HMY 58.18-1 ΠΙΝΑΚΑΣ Α Pre-Populated Circuit & Initial Verification Test Schedule
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

    district_rows = "".join(
        f"<tr><td>{esc(dist)}</td><td><code>{esc(email)}</code></td></tr>"
        for dist, email in EAC_DISTRICT_EMAILS.items()
    )

    # Official 2026 EAC ΚΑΤΑΛΟΓΟΣ ΕΠΙΣΥΝΑΠΤΟΜΕΝΩΝ ΕΓΓΡΑΦΩΝ (New Install + PV Net-Billing / EV)
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

    # Official HMY 58.18-1 ΠΙΝΑΚΑΣ Β 19-Point Visual Inspection Summary
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

    poles_lbl = "4P" if eac.supply_phases == 3 else "2P"
    origin_rcd_lbl = (
        "100 mA Type-S (130–500 ms)"
        if d.earthing_system.value == "TT"
        else "N/A (TN-C-S — 30 mA RCBOs at DB)"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>
    Cyprus EAC (Ε-ΔΔ-707 / Ε-ΔΔ-516) &amp; HMY 58.18-1 — {esc(d.building.project_name)}
  </title>
  <style>
    @page {{ size: A4 portrait; margin: 11mm; }}
    body {{
      font-family: "DejaVu Sans", "Noto Sans", sans-serif;
      color: #102a43;
      font-size: 8.6pt;
      line-height: 1.35;
      margin: 0;
    }}
    .hdr {{
      background: #1b365d;
      color: #ffffff;
      padding: 9px 13px;
      border-bottom: 4px solid #0284c7;
      margin-bottom: 9px;
    }}
    .hdr h1 {{ margin: 0 0 3px 0; font-size: 11.8pt; }}
    .hdr p {{ margin: 0; font-size: 7.9pt; opacity: 0.92; }}
    h2 {{
      font-size: 9.2pt;
      color: #1b365d;
      border-bottom: 1.5px solid #1b365d;
      padding-bottom: 2px;
      margin: 9px 0 4px 0;
      page-break-after: avoid;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 6px;
      font-size: 7.9pt;
    }}
    th, td {{
      border: 1px solid #cbd5e1;
      padding: 3px 5px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: #e2e8f0; color: #1b365d; font-weight: 700; }}
    .test-tbl {{ font-size: 6.8pt; }}
    .test-tbl th, .test-tbl td {{ padding: 2.5px 3.2px; }}
    .legend-box {{
      background: #f8fafc;
      border: 1px solid #cbd5e1;
      padding: 5px 8px;
      font-size: 7.1pt;
      margin-bottom: 7px;
    }}
    .note {{ font-size: 7.1pt; color: #475569; margin: 3px 0 6px 0; }}
    .page-break {{ page-break-before: always; }}
    .signbox {{
      margin-top: 9px;
      border: 1.5px solid #1b365d;
      padding: 8px 11px;
      background: #f8fafc;
      page-break-inside: avoid;
    }}
    .sig-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-top: 8px;
    }}
    .sig-line {{
      border-top: 1px dashed #475569;
      padding-top: 4px;
      margin-top: 22px;
      font-size: 7.6pt;
      color: #475569;
    }}
  </style>
</head>
<body>
  <div class="hdr">
    <h1>ELECTRICITY AUTHORITY OF CYPRUS (EAC / ΑΗΚ) — PLAN APPROVAL &amp; Ε-ΔΔ-516 SCHEDULE</h1>
    <p>
      ΑΙΤΗΣΗ ΕΛΕΓΧΟΥ (Ε-ΔΔ-707), ΥΠΕΥΘΥΝΗ ΔΗΛΩΣΗ ΜΕΛΕΤΗΤΗ &amp; ΕΓΚΑΤΑΣΤΑΤΗ (Ε-ΔΔ-516 / Ε-ΔΔ-514)
      &amp; ΠΙΣΤΟΠΟΙΗΤΙΚΟ ΚΑΤΑΛΛΗΛΟΤΗΤΑΣ ΕΝΤΥΠΟ Η.Μ.Υ. 58.18-1 (ΗΜΥ 58 · BS 7671 / ΚΔΠ 168/2017)
    </p>
  </div>
  {"".join(grouped_html)}
  {submain_section}
  {pv_storage_section}

  <div class="signbox">
    <strong>
      Statutory ETEK Declaration (Ε-ΔΔ-516) / Υπεύθυνη Δήλωση Αδειούχου Μελετητή (ΕΤΕΚ) &amp;
      Εγκαταστάτη (Ε-ΔΔ-514):
    </strong>
    <p style="margin:4px 0 0 0;font-size:7.9pt;">
      I hereby verify that the electrical installation drawings, Single-Line Diagram, BS 7671
      calculations, earthing arrangements, and Cyprus EAC protective provisions attached hereto
      have been independently reviewed and approved for EAC plan submission. /
      Βεβαιώνω υπεύθυνα ότι τα συνημμένα ηλεκτρολογικά σχέδια, το μονογραμμικό διάγραμμα και οι
      υπολογισμοί BS 7671 έχουν ελεγχθεί και συμμορφώνονται με τους Κανονισμούς της ΑΗΚ και ΗΜΥ.
    </p>
    <div class="sig-grid">
      <div class="sig-line">
        Licensed ETEK Engineer Signature, HMY Reg. &amp; kVA Limit /
        Μελετητής ΕΤΕΚ (Όρια Ευθύνης kVA)
      </div>
      <div class="sig-line">
        Licensed Electrical Contractor Signature &amp; HMY Reg. / Εγκαταστάτης (Ε-ΔΔ-514)
      </div>
      <div class="sig-line">
        Applicant / Owner Signature &amp; ID / Υπογραφή Αιτητή
      </div>
      <div class="sig-line">
        Date &amp; EAC Application Ref / Ημερομηνία &amp; Αρ. Φακέλου ΑΗΚ
      </div>
    </div>
  </div>

  <div class="page-break"></div>
  <div class="hdr">
    <h1>ΕΝΤΥΠΟ Η.Μ.Υ. 58.18-1 — ΠΙΣΤΟΠΟΙΗΤΙΚΟ ΚΑΤΑΛΛΗΛΟΤΗΤΑΣ &amp; ΠΙΝΑΚΑΣ Α</h1>
    <p>
      ΤΜΗΜΑ ΗΛΕΚΤΡΟΜΗΧΑΝΟΛΟΓΙΚΩΝ ΥΠΗΡΕΣΙΩΝ (ΚΥΠΡΟΣ) — ΜΕΡΗ Θ, Ι &amp; ΠΙΝΑΚΑΣ Α
      (ΛΕΠΤΟΜΕΡΕΙΕΣ ΚΥΚΛΩΜΑΤΩΝ &amp; ΑΠΟΤΕΛΕΣΜΑΤΑ ΕΛΕΓΧΟΥ)
    </p>
  </div>

  <h2>
    Θ. ΕΠΟΜΕΝΗ ΕΠΙΘΕΩΡΗΣΗ &amp; Ι. ΣΤΟΙΧΕΙΑ ΕΓΚΑΤΑΣΤΑΣΗΣ /
    Next Inspection &amp; Installation Details
  </h2>
  <table>
    <tbody>
      <tr>
        <td style="width:32%"><strong>ΕΠΟΜΕΝΗ ΕΠΙΘΕΩΡΗΣΗ / Next Inspection</strong></td>
        <td colspan="3">{esc(reinspection_text)}</td>
      </tr>
      <tr>
        <td><strong>Σύστημα Γείωσης / Earthing</strong></td>
        <td>{esc(d.earthing_system.value)} (Design Ze/Ra = {d.ze_or_ra_ohm:.2f} Ω)</td>
        <td style="width:24%"><strong>Τύπος Παροχής / Supply Type</strong></td>
        <td>{supply_chk_html}</td>
      </tr>
      <tr>
        <td><strong>Κεντρική Παροχή / Main Feeder</strong></td>
        <td>{esc(main_cable_spec)}</td>
        <td><strong>Πτώση Τάσης Κεντρικής (ΔV)</strong></td>
        <td>
          Est. {main_feeder_vd_pct:.2f}% | Max Final ΔV:
          L ≤ {rem_lighting_vd_pct:.2f}% · P ≤ {rem_power_vd_pct:.2f}%<sup>(1)</sup>
        </td>
      </tr>
      <tr>
        <td><strong>Κεντρική Προστατευτική Συσκευή</strong></td>
        <td>
          {eac.recommended_main_breaker_a}A {poles_lbl} Isolator /
          Ipf {d.prospective_fault_current_ka:.1f} kA
        </td>
        <td><strong>RCD στην Αφετηρία (όπου απαιτ.)</strong></td>
        <td>{esc(origin_rcd_lbl)}</td>
      </tr>
      {comm_summary_row}
    </tbody>
  </table>

  <table>
    <thead>
      <tr>
        <th colspan="5">
          Έλεγχος Συνέχειας Γειώσεων (✓) — EAC Limits: Main Bonding ≤ 0.5 Ω ·
          Earth Continuity ≤ 1.0 Ω · IR ≥ 1.0 MΩ · 30mA RCD ≤ 200ms (1×) / ≤ 40ms (5×)
        </th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>&#9744; Προστατευτικών αγωγών κυκλωμάτων (CPC)</td>
        <td>{mb_box}</td>
        <td>{sb_box}</td>
        <td>&#9744; Αγωγού Γείωσης (MEC, ≤ 1.0 Ω)</td>
        <td>&#9744; Γεφύρωσης εξωτερικών αγώγιμων μερών</td>
      </tr>
    </tbody>
  </table>

  <table>
    <thead>
      <tr>
        <th colspan="5">
          Συσκευή Προστασίας από Υπέρταση στην Αρχή της Εγκατάστασης (SPD)<sup>*1</sup>
        </th>
      </tr>
      <tr>
        <th>Τύπος / Type</th>
        <th>Iimp (10/350µs)</th>
        <th>In / Imax (8/20µs)</th>
        <th>Παραμένουσα Τάση (Up)</th>
        <th>Μέγιστη Συνεχής Τάση (Uc)</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>{esc(spd_type)}</strong></td>
        <td>{esc(spd_iimp)}</td>
        <td>{esc(spd_in)}</td>
        <td>{esc(spd_up)}</td>
        <td>{esc(spd_uc)}</td>
      </tr>
    </tbody>
  </table>
  <p class="note">
    <sup>(1)</sup> ΕΝΤΥΠΟ Η.Μ.Υ. 58.18-1 Σημ. 1: Αφαιρείται από το 3% (φωτισμός)
    και το 5% (ισχύς) η πτώση τάσης της κεντρικής παροχής/υποπαροχής.
    <sup>*1</sup> Όπου η παροχή είναι υπόγεια χωρίς αντικεραυνική προστασία (LPS),
    SPD Τύπου 2 επαρκεί· άλλως απαιτείται SPD Τύπου 1+2 ή Μελέτη Κινδύνου IEC 62305-2.
  </p>

  <h2>ΠΙΝΑΚΑΣ Α: ΛΕΠΤΟΜΕΡΕΙΕΣ ΚΥΚΛΩΜΑΤΩΝ &amp; ΑΠΟΤΕΛΕΣΜΑΤΑ ΕΛΕΓΧΟΥ (ΕΝΤΥΠΟ Η.Μ.Υ. 58.18-1)</h2>
  <table class="test-tbl">
    <thead>
      <tr>
        <th>Πίνακας</th>
        <th>Αρ./Φάση</th>
        <th>Περιγραφή Κυκλώματος</th>
        <th>Κωδ. Συρμ.</th>
        <th>Αρ. Σημείων</th>
        <th>L/N / CPC (mm²)</th>
        <th>Συσκευή Προστασίας</th>
        <th>IΔn (mA)</th>
        <th>Max Zs (Ω)</th>
        <th>R1+R2 (Ω)</th>
        <th>Δακτ. (✓)</th>
        <th>IR (MΩ)</th>
        <th>Zs (Ω)</th>
        <th>Πολ. (✓)</th>
        <th>ΔV (%)</th>
        <th>Λειτ. (✓)</th>
      </tr>
    </thead>
    <tbody>
      {hmy_rows}
    </tbody>
  </table>
  <div class="legend-box">
    <strong>ΚΩΔΙΚΕΣ ΓΙΑ ΤΟΝ ΤΥΠΟ ΣΥΡΜΑΤΩΣΗΣ (Η.Μ.Υ. 58.18-1):</strong>
    <strong>A</strong>=PVC/PVC &nbsp;|&nbsp;
    <strong>B</strong>=PVC σε μεταλ. σωλήνα &nbsp;|&nbsp;
    <strong>C</strong>=PVC σε μη μεταλ. σωλήνα (Standard Cyprus Conduit) &nbsp;|&nbsp;
    <strong>D</strong>=PVC σε μεταλ. Trunking &nbsp;|&nbsp;
    <strong>E</strong>=PVC σε μη μεταλ. Trunking &nbsp;|&nbsp;
    <strong>F</strong>=PVC/SWA &nbsp;|&nbsp;
    <strong>G</strong>=XLPE/SWA &nbsp;|&nbsp;
    <strong>H</strong>=Mineral (MI) &nbsp;|&nbsp;
    <strong>O</strong>=Άλλος (Να καταγραφεί)
  </div>

  <h2>ΠΙΝΑΚΑΣ Β: ΕΠΙΘΕΩΡΗΣΗ ΚΑΙ ΕΛΕΓΧΟΣ (Η.Μ.Υ. 58.18-1 — 19 ΣΗΜΕΙΑ ΕΠΙΘΕΩΡΗΣΗΣ)</h2>
  <table class="test-tbl">
    <tbody>
      {table_b_rows}
    </tbody>
  </table>

  <h2>ΚΑΤΑΛΟΓΟΣ ΕΠΙΣΥΝΑΠΤΟΜΕΝΩΝ ΕΓΓΡΑΦΩΝ / Official 2026 EAC Inspection Submission Checklist</h2>
  <table>
    <thead>
      <tr><th>✓</th><th>Required Document / Απαιτούμενο Έγγραφο</th><th>Status</th></tr>
    </thead>
    <tbody>{chk_rows}</tbody>
  </table>

  <h2>EAC District Submission Offices / Κέντρα Εξυπηρέτησης Διανομής ΑΗΚ</h2>
  <table>
    <thead><tr><th>District / Επαρχία</th><th>Official EAC Submission Email</th></tr></thead>
    <tbody>{district_rows}</tbody>
  </table>
</body>
</html>
"""


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
