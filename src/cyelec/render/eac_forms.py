"""Official Cyprus EAC (ΑΗΚ) Plan Approval & Electrical Supply Application Form renderer."""

from __future__ import annotations

import html
from pathlib import Path

from cyelec.models.electrical import CircuitCategory, ReviewSeverity
from cyelec.models.spatial import Phase2DesignResult


def build_eac_application_rows(result: Phase2DesignResult) -> list[tuple[str, str, str, str]]:
    """Return structured bilingual (Section, Field EN, Field EL, Value) EAC form rows."""
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
            "District / Municipality / Plot No.",
            "Επαρχία / Δήμος / Τεμάχιο",
            "[To be completed by Applicant / ETEK Engineer]",
        ),
        (
            "B. ETEK Engineer / Μηχανικός ΕΤΕΚ",
            "Consulting Firm",
            "Μελετητικό Γραφείο",
            (br.firm_name if br and br.firm_name else "[ETEK Licensed Practice]"),
        ),
        (
            "B. ETEK Engineer / Μηχανικός ΕΤΕΚ",
            "Lead Electrical Engineer",
            "Υπεύθυνος Ηλεκτρολόγος Μηχανικός",
            (br.engineer_name if br and br.engineer_name else "[Name & Signature]"),
        ),
        (
            "B. ETEK Engineer / Μηχανικός ΕΤΕΚ",
            "ETEK Registration No.",
            "Αρ. Μητρώου ΕΤΕΚ",
            (br.etek_reg_no if br and br.etek_reg_no else "[ETEK-E-XXXX]"),
        ),
        (
            "B. ETEK Engineer / Μηχανικός ΕΤΕΚ",
            "Applicable BS 7671 / EAC Profile",
            "Εφαρμοστέοι Κανονισμοί ΑΗΚ",
            eac.eac_profile.value,
        ),
        (
            "B. ETEK Engineer / Μηχανικός ΕΤΕΚ",
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
                f"{pb.max_phase_current_a:.2f} A ({pb.imbalance_pct:.2f}% imbalance)"
                if pb is not None
                else f"{eac.max_phase_demand_a:.2f} A (Single-Phase)"
            ),
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "CT Metering Chamber (>100 A/ph)",
            "Θάλαμος Μετρητών CT (>100 A)",
            "REQUIRED / ΑΠΑΙΤΕΙΤΑΙ" if eac.ct_metering_required else "Not Required (Direct)",
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "EAC Substation Consultation (>=150 kVA)",
            "Διαβούλευση Υποσταθμού ΑΗΚ",
            (
                "MANDATORY BLOCKER (>=150 kVA)"
                if eac.substation_consultation_required
                else ("Advisory Warning (>=100 kVA)" if eac.substation_warning else "Not Required")
            ),
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Earthing Arrangement & Ze / Ra",
            "Σύστημα Γείωσης & Αντίσταση",
            f"{d.earthing_system.value} ({d.ze_or_ra_ohm:.2f} Ω)",
        ),
        (
            "C. EAC Supply & Metering / Παροχή ΑΗΚ",
            "Section 701 Bathroom Socket Exclusion",
            "Ζώνη Αποκλεισμού Μπάνιου (Sec 701)",
            f"{eac.bathroom_socket_exclusion_m:.1f} m from Zone 1",
        ),
        (
            "D. Special Systems / Ειδικά Συστήματα",
            "Photovoltaic (PV) Net-Metering / Net-Billing",
            "Φωτοβολταϊκό Σύστημα (Φ/Β)",
            (
                f"{b.pv_capacity_kva:g} kVA (Bidirectional RCD & 3P > 5 kVA verified)"
                if b.pv_capacity_kva > 0.0
                else "None"
            ),
        ),
        (
            "D. Special Systems / Ειδικά Συστήματα",
            "EV Mode 3 Charging (BS 7671 Sec 722)",
            "Φορτιστής Ηλεκτρικού Οχήματος (EV)",
            f"{b.ev_charger_kw:g} kW (Type A + 6 mA DC RDC-DD)" if has_ev else "None",
        ),
        (
            "D. Special Systems / Ειδικά Συστήματα",
            "Commercial Life Safety & Critical Plant",
            "Ασφάλεια Ζωής & Κρίσιμα Φορτία",
            ", ".join(
                p
                for p, ok in [
                    ("BS 5266 Emergency Lighting", has_emg),
                    ("BS 5839 Fire Alarm Spur", has_fa),
                    ("Server Room UPS Feeder", has_ups),
                    ("3P Type D Lift Motor Feeder", has_lift),
                ]
                if ok
            )
            or "Standard Domestic Installation",
        ),
        (
            "E. Verification Status / Έλεγχος",
            "BS 7671 Circuit & Live Adiabatic Pass",
            "Επιτυχία Ελέγχων BS 7671",
            (
                f"ALL {len(d.circuits)} CIRCUITS PASS"
                if all_pass
                else f"REVIEW REQUIRED ({len(d.circuits)} circuits)"
            ),
        ),
        (
            "E. Verification Status / Έλεγχος",
            "ETEK Review Flags Summary",
            "Σύνοψη Σημαιών Ελέγχου ΕΤΕΚ",
            f"{blockers} Blockers / {warnings} Warnings / {len(d.review_flags)} Total Flags",
        ),
    ]


def render_eac_application_form_html(result: Phase2DesignResult) -> str:
    """Render printable bilingual A4 Cyprus EAC Plan Approval & Supply Application Form HTML."""
    d = result.design
    rows = build_eac_application_rows(result)
    esc = html.escape

    grouped_html: list[str] = []
    cur_sec = ""
    for sec, en_lbl, el_lbl, val in rows:
        if sec != cur_sec:
            if cur_sec:
                grouped_html.append("</tbody></table>")
            grouped_html.append(
                f"<h2>{esc(sec)}</h2>"
                "<table><thead><tr>"
                "<th style='width:34%;'>Parameter (EN)</th>"
                "<th style='width:30%;'>Παράμετρος (EL)</th>"
                "<th style='width:36%;'>Declared Engineering Value</th>"
                "</tr></thead><tbody>"
            )
            cur_sec = sec
        grouped_html.append(
            f"<tr><td><strong>{esc(en_lbl)}</strong></td>"
            f"<td>{esc(el_lbl)}</td>"
            f"<td>{esc(val)}</td></tr>"
        )
    if cur_sec:
        grouped_html.append("</tbody></table>")

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

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Cyprus EAC Plan Approval Application Form — {esc(d.building.project_name)}</title>
  <style>
    @page {{ size: A4 portrait; margin: 14mm; }}
    body {{
      font-family: "Noto Sans", "DejaVu Sans", sans-serif;
      color: #102a43;
      font-size: 9.5pt;
      line-height: 1.4;
      margin: 0;
    }}
    .hdr {{
      background: #1b365d;
      color: #ffffff;
      padding: 12px 16px;
      border-bottom: 4px solid #0284c7;
      margin-bottom: 12px;
    }}
    .hdr h1 {{ margin: 0 0 4px 0; font-size: 13pt; }}
    .hdr p {{ margin: 0; font-size: 8.5pt; opacity: 0.9; }}
    h2 {{
      font-size: 10pt;
      color: #1b365d;
      border-bottom: 1.5px solid #1b365d;
      padding-bottom: 2px;
      margin: 12px 0 6px 0;
      page-break-after: avoid;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 8px;
      font-size: 8.8pt;
    }}
    th, td {{
      border: 1px solid #cbd5e1;
      padding: 4px 7px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: #e2e8f0; color: #1b365d; font-weight: 700; }}
    .signbox {{
      margin-top: 14px;
      border: 1.5px solid #1b365d;
      padding: 10px 14px;
      background: #f8fafc;
      page-break-inside: avoid;
    }}
    .sig-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-top: 14px;
    }}
    .sig-line {{
      border-top: 1px dashed #475569;
      padding-top: 4px;
      margin-top: 28px;
      font-size: 8.5pt;
      color: #475569;
    }}
  </style>
</head>
<body>
  <div class="hdr">
    <h1>ELECTRICITY AUTHORITY OF CYPRUS (EAC / ΑΗΚ) — PLAN APPROVAL APPLICATION SCHEDULE</h1>
    <p>
      ΑΙΤΗΣΗ ΕΓΚΡΙΣΗΣ ΗΛΕΚΤΡΟΛΟΓΙΚΩΝ ΣΧΕΔΙΩΝ &amp; ΤΕΧΝΙΚΗ ΔΗΛΩΣΗ ΜΕΛΕΤΗΤΗ ΕΤΕΚ
      (BS 7671 / IEC 60364)
    </p>
  </div>
  {"".join(grouped_html)}
  {submain_section}
  <div class="signbox">
    <strong>
      Statutory ETEK Declaration / Υπεύθυνη Δήλωση Μελετητή Ηλεκτρολόγου Μηχανικού (ΕΤΕΚ):
    </strong>
    <p style="margin:6px 0 0 0;font-size:8.6pt;">
      I hereby verify that the electrical installation drawings, Single-Line Diagram, BS 7671
      calculations, earthing arrangements, and Cyprus EAC protective provisions attached hereto
      have been independently reviewed and approved for EAC plan submission. /
      Βεβαιώνω ότι τα συνημμένα ηλεκτρολογικά σχέδια, το μονογραμμικό διάγραμμα και οι υπολογισμοί
      BS 7671 έχουν ελεγχθεί και εγκριθεί για υποβολή στην Αρχή Ηλεκτρισμού Κύπρου (ΑΗΚ).
    </p>
    <div class="sig-grid">
      <div class="sig-line">
        Licensed ETEK Engineer Signature &amp; Stamp / Υπογραφή &amp; Σφραγίδα ΕΤΕΚ
      </div>
      <div class="sig-line">
        Date &amp; EAC Submission Ref / Ημερομηνία &amp; Αρ. Αίτησης ΑΗΚ
      </div>
    </div>
  </div>
</body>
</html>
"""


def export_eac_application_form(
    result: Phase2DesignResult,
    out_dir: Path,
    *,
    export_pdf: bool = False,
) -> dict[str, Path]:
    """Write Cyprus EAC Application Form HTML (and optional native PDF via WeasyPrint)."""
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
