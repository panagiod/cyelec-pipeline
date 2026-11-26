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
            eac.recommended_supply_tier,
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


def render_eac_application_form_html(result: Phase2DesignResult) -> str:
    """Render official Cyprus EAC (Ε-ΔΔ-516) + HMY 58 Test Schedule + Checklist HTML."""
    d = result.design
    rows = build_eac_application_rows(result)

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

    # HMY 58 (18-1) Pre-Populated BS 7671 Initial Verification Circuit Test Schedule
    hmy_rows = "".join(
        f"<tr>"
        f"<td>{esc(c.spec.board_id)}</td>"
        f"<td><strong>{esc(c.spec.circuit_id)}</strong></td>"
        f"<td>{esc(c.phase.value)}</td>"
        f"<td>{esc(c.spec.description[:30])}</td>"
        f"<td>{c.cable_mm2:g}/{c.cpc_mm2:g}</td>"
        f"<td>{esc(c.device_label)}</td>"
        f"<td>{f'{c.spec.rcd_idn_ma}mA' if c.spec.rcd_idn_ma else '-'}</td>"
        f"<td>{c.zs_max_ohm:.2f}</td>"
        f"<td></td><td></td><td></td><td></td><td></td><td></td>"
        f"</tr>"
        for c in d.circuits
    )

    district_rows = "".join(
        f"<tr><td>{esc(dist)}</td><td><code>{esc(email)}</code></td></tr>"
        for dist, email in EAC_DISTRICT_EMAILS.items()
    )

    checklist_items = [
        ("Electrical Floorplan Layouts (ISO A3/A1 @ 1:50 or 1:100 — E-101)", True),
        ("Single-Line Diagram (SLD — E-201) with Earthing & SPD", True),
        ("BS 7671 Load Schedule, Voltage Drop, Zs & Adiabatic Calculation Pack", True),
        ("EAC Form Ε-ΔΔ-516 (Statutory Declaration — ETEK Designer & Installer)", True),
        ("HMY 58 (18-1) Certificate of Suitability & Initial Verification Test Results", True),
        ("EAC Form Ε-ΔΔ-514 (Electrician Declaration of Readiness for Inspection)", True),
        ("Cadastral Site Plan & Copy of Title Deed / Building Permit", False),
        (
            "EAC PV Net-Metering / Net-Billing Technical Data Sheet & Inverter Cert.",
            d.building.pv_capacity_kva > 0,
        ),
        (
            "EAC CT Metering Chamber / Substation Layout Drawings",
            d.eac_compliance.ct_metering_required
            or d.eac_compliance.substation_consultation_required,
        ),
    ]
    chk_rows = "".join(
        f"<tr><td style='width:28px;text-align:center'>&#9744;</td>"
        f"<td>{esc(item)}</td>"
        f"<td style='width:120px'>{'Included in Pack' if included else 'Applicant Attachment'}</td>"
        f"</tr>"
        for item, included in checklist_items
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Cyprus EAC (Ε-ΔΔ-516) &amp; HMY 58 Pack — {esc(d.building.project_name)}</title>
  <style>
    @page {{ size: A4 portrait; margin: 12mm; }}
    body {{
      font-family: "DejaVu Sans", "Noto Sans", sans-serif;
      color: #102a43;
      font-size: 9pt;
      line-height: 1.38;
      margin: 0;
    }}
    .hdr {{
      background: #1b365d;
      color: #ffffff;
      padding: 10px 14px;
      border-bottom: 4px solid #0284c7;
      margin-bottom: 10px;
    }}
    .hdr h1 {{ margin: 0 0 3px 0; font-size: 12.2pt; }}
    .hdr p {{ margin: 0; font-size: 8.2pt; opacity: 0.92; }}
    h2 {{
      font-size: 9.6pt;
      color: #1b365d;
      border-bottom: 1.5px solid #1b365d;
      padding-bottom: 2px;
      margin: 10px 0 5px 0;
      page-break-after: avoid;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 7px;
      font-size: 8.2pt;
    }}
    th, td {{
      border: 1px solid #cbd5e1;
      padding: 3.5px 6px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: #e2e8f0; color: #1b365d; font-weight: 700; }}
    .test-tbl {{ font-size: 7.4pt; }}
    .test-tbl th, .test-tbl td {{ padding: 3px 4px; }}
    .page-break {{ page-break-before: always; }}
    .signbox {{
      margin-top: 10px;
      border: 1.5px solid #1b365d;
      padding: 9px 12px;
      background: #f8fafc;
      page-break-inside: avoid;
    }}
    .sig-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-top: 10px;
    }}
    .sig-line {{
      border-top: 1px dashed #475569;
      padding-top: 4px;
      margin-top: 26px;
      font-size: 8pt;
      color: #475569;
    }}
  </style>
</head>
<body>
  <div class="hdr">
    <h1>ELECTRICITY AUTHORITY OF CYPRUS (EAC / ΑΗΚ) — PLAN APPROVAL &amp; Ε-ΔΔ-516 SCHEDULE</h1>
    <p>
      ΑΙΤΗΣΗ ΠΑΡΟΧΗΣ ΥΠΗΡΕΣΙΩΝ ΑΗΚ, ΕΝΤΥΠΟ Ε-ΔΔ-516 (ΥΠΕΥΘΥΝΗ ΔΗΛΩΣΗ ΜΕΛΕΤΗΤΗ &amp; ΕΓΚΑΤΑΣΤΑΤΗ)
      &amp; ΠΡΟΕΤΟΙΜΑΣΙΑ ΕΝΤΥΠΟΥ ΗΜΥ 58 (BS 7671 / IEC 60364)
    </p>
  </div>
  {"".join(grouped_html)}
  {submain_section}

  <div class="signbox">
    <strong>
      Statutory ETEK Declaration (Ε-ΔΔ-516) / Υπεύθυνη Δήλωση Αδειούχου Μελετητή (ΕΤΕΚ) &amp;
      Εγκαταστάτη (Ε-ΔΔ-514):
    </strong>
    <p style="margin:5px 0 0 0;font-size:8.2pt;">
      I hereby verify that the electrical installation drawings, Single-Line Diagram, BS 7671
      calculations, earthing arrangements, and Cyprus EAC protective provisions attached hereto
      have been independently reviewed and approved for EAC plan submission. /
      Βεβαιώνω υπεύθυνα ότι τα συνημμένα ηλεκτρολογικά σχέδια, το μονογραμμικό διάγραμμα και οι
      υπολογισμοί BS 7671 έχουν ελεγχθεί και συμμορφώνονται με τους Κανονισμούς της ΑΗΚ και ΗΜΥ.
    </p>
    <div class="sig-grid">
      <div class="sig-line">
        Licensed ETEK Engineer Signature &amp; Stamp / Υπογραφή &amp; Σφραγίδα Μελετητή ΕΤΕΚ
      </div>
      <div class="sig-line">
        Licensed Electrical Contractor Signature &amp; HMY Reg. / Εγκαταστάτης (Ε-ΔΔ-514)
      </div>
      <div class="sig-line">
        Applicant / Owner Signature &amp; ID / Υπογραφή Αιτητή
      </div>
      <div class="sig-line">
        Date &amp; EAC Application Ref / Ημερομηνία &amp; Αρ. Αίτησης ΑΗΚ
      </div>
    </div>
  </div>

  <div class="page-break"></div>
  <div class="hdr">
    <h1>CYPRUS HMY 58 (18-1) — BS 7671 INITIAL VERIFICATION CIRCUIT TEST SCHEDULE</h1>
    <p>
      ΕΝΤΥΠΟ ΗΜΥ 58 — ΠΙΣΤΟΠΟΙΗΤΙΚΟ ΚΑΤΑΛΛΗΛΟΤΗΤΑΣ &amp; ΠΙΝΑΚΑΣ ΜΕΤΡΗΣΕΩΝ ΕΛΕΓΧΟΥ ΕΓΚΑΤΑΣΤΑΣΗΣ
    </p>
  </div>
  <table class="test-tbl">
    <thead>
      <tr>
        <th>Board</th>
        <th>Cct</th>
        <th>Ph</th>
        <th>Circuit Description</th>
        <th>Live/CPC (mm²)</th>
        <th>Device</th>
        <th>RCD</th>
        <th>Max Zs (Ω)</th>
        <th>R1+R2 (Ω)</th>
        <th>IR (MΩ ≥1)</th>
        <th>Pol. (✓)</th>
        <th>Meas. Zs/Ra (Ω)</th>
        <th>RCD 1× (ms)</th>
        <th>RCD 5× (ms)</th>
      </tr>
    </thead>
    <tbody>
      {hmy_rows}
    </tbody>
  </table>

  <h2>ΚΑΤΑΛΟΓΟΣ ΕΠΙΣΥΝΑΠΤΟΜΕΝΩΝ ΕΓΓΡΑΦΩΝ / EAC Submission Document Checklist</h2>
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
