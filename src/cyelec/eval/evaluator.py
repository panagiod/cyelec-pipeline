"""Quantitative evaluator comparing CyElec designs against licensed engineer electrical plans."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cyelec.assets import get_logo_svg_data_uri
from cyelec.models.building import BuildingType
from cyelec.models.electrical import CircuitCategory, ReviewSeverity
from cyelec.models.spatial import DeviceType, Phase2DesignResult


class ReferenceCircuitSpec(BaseModel):
    """Circuit group from a real licensed engineer's electrical schedule."""

    model_config = ConfigDict(extra="forbid")

    category: CircuitCategory
    description: str
    count: int = Field(default=1, ge=1)
    breaker_in_a: int = Field(gt=0)
    cable_mm2: float = Field(gt=0.0)
    is_three_phase: bool = False
    rcd_protected: bool = True


class ReferenceElectricalPlan(BaseModel):
    """Ground-truth electrical design from a real engineer's plan/schedule for comparison."""

    model_config = ConfigDict(extra="forbid")

    plan_name: str
    source_reference: str
    building_type: BuildingType
    expected_area_m2: float = Field(gt=0.0)
    expected_room_count: int = Field(ge=1)
    expected_supply_phases: Literal[1, 3]
    expected_supply_tier: str
    expected_main_breaker_a: int = Field(gt=0)
    expected_diversified_kva: float = Field(gt=0.0)
    expected_max_phase_a: float = Field(gt=0.0)
    expected_imbalance_pct: float | None = Field(default=None, ge=0.0)
    expected_total_circuits: int = Field(ge=1)
    reference_circuits: list[ReferenceCircuitSpec] = Field(min_length=1)
    expected_sockets_min: int | None = Field(default=None, ge=0)
    expected_luminaires_min: int | None = Field(default=None, ge=0)
    expected_split_ac_count: int | None = Field(default=None, ge=0)
    expected_conduit_m: float | None = Field(default=None, gt=0.0)
    expected_boq_eur: float | None = Field(default=None, gt=0.0)


class CategoryComparison(BaseModel):
    """Per-category comparison between CyElec generated circuits and real engineer reference."""

    model_config = ConfigDict(extra="forbid")

    category: CircuitCategory
    ref_count: int
    actual_count: int
    ref_breaker_a: int
    actual_min_breaker_a: int | None
    ref_cable_mm2: float
    actual_min_cable_mm2: float | None
    status: Literal[
        "EXACT_MATCH",
        "CONSERVATIVE_UPSIZE",
        "COUNT_VARIANCE",
        "UNDERSIZED_SAFETY_FAIL",
        "MISSING_CATEGORY",
    ]
    note: str


class EvaluationReport(BaseModel):
    """Structured quantitative evaluation contrasting CyElec output vs real engineer plan."""

    model_config = ConfigDict(extra="forbid")

    plan_name: str
    source_reference: str
    verdict: Literal["PASS", "PASS_WITH_REVIEW", "FAIL_SAFETY"]
    concordance_score_pct: float
    area_ref_m2: float
    area_actual_m2: float
    area_variance_pct: float
    rooms_ref: int
    rooms_actual: int
    supply_phases_match: bool
    supply_tier_ref: str
    supply_tier_actual: str
    main_breaker_ref_a: int
    main_breaker_actual_a: int
    main_breaker_adequate: bool
    diversified_kva_ref: float
    diversified_kva_actual: float
    diversified_kva_variance_pct: float
    max_phase_a_ref: float
    max_phase_a_actual: float
    imbalance_pct_actual: float | None
    circuits_ref: int
    circuits_actual: int
    category_comparisons: list[CategoryComparison]
    safety_failures: list[str] = Field(default_factory=list)
    review_observations: list[str] = Field(default_factory=list)
    db_in_wet_room: bool
    all_circuits_bs7671_pass: bool
    blocker_flag_count: int
    sockets_actual: int
    luminaires_actual: int
    split_ac_actual: int
    conduit_actual_m: float
    conduit_ref_m: float | None = None
    boq_actual_eur: float | None = None
    boq_ref_eur: float | None = None


def load_reference_plan(path: Path) -> ReferenceElectricalPlan:
    """Load a ground-truth ReferenceElectricalPlan from YAML or JSON."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ReferenceElectricalPlan.model_validate(raw)


def _pct_delta(actual: float, ref: float) -> float:
    if abs(ref) < 1e-9:
        return 0.0
    return round((actual - ref) / ref * 100.0, 2)


def evaluate_against_reference(
    result: Phase2DesignResult,
    ref: ReferenceElectricalPlan,
) -> EvaluationReport:
    """Compare a solved Phase2DesignResult quantitatively against a real engineer reference plan."""
    design = result.design
    eac = design.eac_compliance
    spatial = result.spatial

    safety_failures: list[str] = []
    observations: list[str] = []

    area_actual = round(design.building.total_floor_area_m2, 2)
    area_var = _pct_delta(area_actual, ref.expected_area_m2)
    if abs(area_var) > 15.0:
        observations.append(
            f"Detected floor area ({area_actual:.1f} m²) differs by {area_var:+.1f}% vs "
            f"reference ({ref.expected_area_m2:.1f} m²); verify DXF room boundaries."
        )

    supply_phases_match = eac.supply_phases == ref.expected_supply_phases
    if not supply_phases_match:
        safety_failures.append(
            f"Supply phase mismatch: CyElec selected {eac.supply_phases}-phase vs "
            f"reference {ref.expected_supply_phases}-phase."
        )

    main_breaker_adequate = eac.recommended_main_breaker_a >= ref.expected_main_breaker_a
    if not main_breaker_adequate:
        observations.append(
            f"Main breaker smaller than reference ({eac.recommended_main_breaker_a} A vs "
            f"{ref.expected_main_breaker_a} A) due to lower diversified load estimate."
        )

    kva_var = _pct_delta(eac.total_diversified_kva, ref.expected_diversified_kva)

    # Check DB1 is NEVER placed inside a wet room
    from shapely.geometry import Point, Polygon

    db_loc = design.building.db_anchor.location_mm
    db_pt = Point(db_loc.x_mm, db_loc.y_mm)
    db_in_wet_room = False
    for rm in design.building.rooms:
        if rm.is_wet_room and len(rm.polygon_mm) >= 3:
            rpoly = Polygon([(p.x_mm, p.y_mm) for p in rm.polygon_mm])
            if rpoly.contains(db_pt):
                db_in_wet_room = True
                safety_failures.append(
                    f"CRITICAL BS 7671 Sec 701 violation: DB '{design.building.db_anchor.id}' "
                    f"located inside wet room '{rm.raw_label}'."
                )

    all_bs7671_pass = all(c.overall_pass for c in design.circuits)
    if not all_bs7671_pass:
        failing_ids = [c.spec.circuit_id for c in design.circuits if not c.overall_pass]
        safety_failures.append(f"BS 7671 circuit failures present: {', '.join(failing_ids)}.")

    # Category-by-category circuit comparison
    by_cat: dict[CircuitCategory, list[tuple[int, float]]] = {}
    for circ in design.circuits:
        by_cat.setdefault(circ.spec.category, []).append((circ.breaker_in_a, circ.cable_mm2))

    cat_rows: list[CategoryComparison] = []
    matched_categories = 0
    for rspec in ref.reference_circuits:
        actual_list = by_cat.get(rspec.category, [])
        actual_cnt = len(actual_list)
        if actual_cnt == 0:
            cat_rows.append(
                CategoryComparison(
                    category=rspec.category,
                    ref_count=rspec.count,
                    actual_count=0,
                    ref_breaker_a=rspec.breaker_in_a,
                    actual_min_breaker_a=None,
                    ref_cable_mm2=rspec.cable_mm2,
                    actual_min_cable_mm2=None,
                    status="MISSING_CATEGORY",
                    note=f"Expected {rspec.count}x {rspec.description}; none generated.",
                )
            )
            observations.append(
                f"Missing circuit category '{rspec.category.value}' ({rspec.description})."
            )
            continue

        min_in = min(b for b, _ in actual_list)
        min_csa = min(csa for _, csa in actual_list)

        if min_csa + 1e-6 < rspec.cable_mm2:
            status: Literal[
                "EXACT_MATCH",
                "CONSERVATIVE_UPSIZE",
                "COUNT_VARIANCE",
                "UNDERSIZED_SAFETY_FAIL",
                "MISSING_CATEGORY",
            ] = "UNDERSIZED_SAFETY_FAIL"
            note = (
                f"Cable undersized vs reference ({min_csa:g} mm² < {rspec.cable_mm2:g} mm²)."
            )
            safety_failures.append(f"{rspec.category.value}: {note}")
        elif min_in < rspec.breaker_in_a:
            status = "UNDERSIZED_SAFETY_FAIL"
            note = f"Breaker undersized vs reference ({min_in} A < {rspec.breaker_in_a} A)."
            safety_failures.append(f"{rspec.category.value}: {note}")
        elif min_csa > rspec.cable_mm2 + 1e-6 or min_in > rspec.breaker_in_a:
            status = "CONSERVATIVE_UPSIZE"
            matched_categories += 1
            note = (
                f"Safe conservative upsize at Cyprus 40°C/ΔV ({min_in} A / {min_csa:g} mm² vs "
                f"ref {rspec.breaker_in_a} A / {rspec.cable_mm2:g} mm²)."
            )
        elif actual_cnt != rspec.count:
            status = "COUNT_VARIANCE"
            matched_categories += 1
            note = (
                f"Sizing matches ({min_in} A / {min_csa:g} mm²); "
                f"count {actual_cnt} vs ref {rspec.count}."
            )
        else:
            status = "EXACT_MATCH"
            matched_categories += 1
            note = f"Exact count & sizing match ({actual_cnt}x {min_in} A / {min_csa:g} mm²)."

        cat_rows.append(
            CategoryComparison(
                category=rspec.category,
                ref_count=rspec.count,
                actual_count=actual_cnt,
                ref_breaker_a=rspec.breaker_in_a,
                actual_min_breaker_a=min_in,
                ref_cable_mm2=rspec.cable_mm2,
                actual_min_cable_mm2=min_csa,
                status=status,
                note=note,
            )
        )

    sockets_actual = sum(1 for d in spatial.devices if d.device_type == DeviceType.SOCKET_TWIN)
    luminaires_actual = sum(1 for d in spatial.devices if d.device_type == DeviceType.LUMINAIRE)
    split_ac_actual = sum(1 for c in design.circuits if c.spec.category == CircuitCategory.SPLIT_AC)
    blockers = sum(1 for f in design.review_flags if f.severity == ReviewSeverity.BLOCKER)
    imb_actual = design.phase_balance.imbalance_pct if design.phase_balance is not None else None

    if imb_actual is not None and imb_actual > 10.0:
        observations.append(f"3-phase imbalance is {imb_actual:.2f}% (> 10% EAC target).")

    # Real-world BS 7671 installability / practicability checks
    for circ in design.circuits:
        if circ.spec.category == CircuitCategory.LIGHTING and circ.cable_mm2 > 2.5:
            observations.append(
                f"Practicability advisory ({circ.spec.circuit_id}): lighting circuit upsized to "
                f"{circ.cable_mm2:g} mm² across {circ.spec.length_m:.1f} m; consider splitting "
                "into 2 lighting zones to keep 1.5 mm² switch/luminaire terminals."
            )
        if circ.spec.category == CircuitCategory.SOCKET_RING and circ.spec.length_m > 120.0:
            observations.append(
                f"Practicability advisory ({circ.spec.circuit_id}): ring final total length is "
                f"{circ.spec.length_m:.1f} m (> 120 m practical BS 7671 App. 15 guideline); "
                "consider splitting into 2 rings to stay on 2.5 mm²."
            )

    # Composite engineering concordance score (0-100%)
    cat_score = (matched_categories / max(1, len(ref.reference_circuits))) * 45.0
    supply_score = (15.0 if supply_phases_match else 0.0) + (10.0 if main_breaker_adequate else 5.0)
    kva_score = max(0.0, 15.0 * (1.0 - min(1.0, abs(kva_var) / 50.0)))
    safety_score = 15.0 if (not safety_failures and all_bs7671_pass and not db_in_wet_room) else 0.0
    score = round(min(100.0, cat_score + supply_score + kva_score + safety_score), 1)

    if safety_failures:
        verdict: Literal["PASS", "PASS_WITH_REVIEW", "FAIL_SAFETY"] = "FAIL_SAFETY"
    elif observations or any(r.status != "EXACT_MATCH" for r in cat_rows):
        verdict = "PASS_WITH_REVIEW"
    else:
        verdict = "PASS"

    return EvaluationReport(
        plan_name=ref.plan_name,
        source_reference=ref.source_reference,
        verdict=verdict,
        concordance_score_pct=score,
        area_ref_m2=ref.expected_area_m2,
        area_actual_m2=area_actual,
        area_variance_pct=area_var,
        rooms_ref=ref.expected_room_count,
        rooms_actual=len(design.building.rooms),
        supply_phases_match=supply_phases_match,
        supply_tier_ref=ref.expected_supply_tier,
        supply_tier_actual=eac.recommended_supply_tier,
        main_breaker_ref_a=ref.expected_main_breaker_a,
        main_breaker_actual_a=eac.recommended_main_breaker_a,
        main_breaker_adequate=main_breaker_adequate,
        diversified_kva_ref=ref.expected_diversified_kva,
        diversified_kva_actual=eac.total_diversified_kva,
        diversified_kva_variance_pct=kva_var,
        max_phase_a_ref=ref.expected_max_phase_a,
        max_phase_a_actual=eac.max_phase_demand_a,
        imbalance_pct_actual=imb_actual,
        circuits_ref=ref.expected_total_circuits,
        circuits_actual=len(design.circuits),
        category_comparisons=cat_rows,
        safety_failures=safety_failures,
        review_observations=observations,
        db_in_wet_room=db_in_wet_room,
        all_circuits_bs7671_pass=all_bs7671_pass,
        blocker_flag_count=blockers,
        sockets_actual=sockets_actual,
        luminaires_actual=luminaires_actual,
        split_ac_actual=split_ac_actual,
        conduit_actual_m=spatial.total_conduit_m,
        conduit_ref_m=ref.expected_conduit_m,
        boq_actual_eur=design.boq.grand_total_eur if design.boq is not None else None,
        boq_ref_eur=ref.expected_boq_eur,
    )


def render_evaluation_md(report: EvaluationReport) -> str:
    """Render auditable Markdown evaluation report comparing CyElec vs real engineer plan."""
    bs_str = "YES" if report.all_circuits_bs7671_pass else "NO"
    wet_str = "YES (FAIL)" if report.db_in_wet_room else "No (Safe)"
    ph_str = "PHASES MATCH" if report.supply_phases_match else "MISMATCH"
    brk_str = "ADEQUATE" if report.main_breaker_adequate else "LOWER"
    max_a_delta = _pct_delta(report.max_phase_a_actual, report.max_phase_a_ref)
    imb_str = (
        f"{report.imbalance_pct_actual:.2f}%"
        if report.imbalance_pct_actual is not None
        else "1P"
    )
    cond_ref = f"{report.conduit_ref_m:.1f}" if report.conduit_ref_m else "-"
    boq_ref = f"€{report.boq_ref_eur:,.2f}" if report.boq_ref_eur else "-"
    boq_act = f"€{report.boq_actual_eur:,.2f}" if report.boq_actual_eur else "-"

    lines = [
        f"# Engineering Evaluation Report — {report.plan_name}",
        "",
        f"- **Reference Basis:** {report.source_reference}",
        f"- **Verdict:** **{report.verdict}** | "
        f"**Concordance Score:** `{report.concordance_score_pct:.1f}%`",
        f"- **BS 7671 All Circuits Pass:** `{bs_str}` | "
        f"**DB in Wet Room:** `{wet_str}` | "
        f"**Blocker Flags:** `{report.blocker_flag_count}`",
        "",
        "## 1. Headline Metrics (CyElec vs. Reference Engineer Plan)",
        "",
        "| Metric | Reference Engineer Plan | CyElec Output | Delta / Status |",
        "| --- | ---: | ---: | --- |",
        f"| Floor Area (m²) | {report.area_ref_m2:.1f} | {report.area_actual_m2:.1f} "
        f"| `{report.area_variance_pct:+.1f}%` |",
        f"| Rooms Detected | {report.rooms_ref} | {report.rooms_actual} "
        f"| `{report.rooms_actual - report.rooms_ref:+d}` |",
        f"| Supply Tier | {report.supply_tier_ref} | {report.supply_tier_actual} | `{ph_str}` |",
        f"| Main Breaker (A) | {report.main_breaker_ref_a} A "
        f"| {report.main_breaker_actual_a} A | `{brk_str}` |",
        f"| Diversified Demand (kVA) | {report.diversified_kva_ref:.2f} "
        f"| {report.diversified_kva_actual:.2f} | `{report.diversified_kva_variance_pct:+.1f}%` |",
        f"| Peak Phase Demand (A) | {report.max_phase_a_ref:.1f} "
        f"| {report.max_phase_a_actual:.1f} | `{max_a_delta:+.1f}%` |",
        f"| Total Final Circuits | {report.circuits_ref} | {report.circuits_actual} "
        f"| `{report.circuits_actual - report.circuits_ref:+d}` |",
        f"| 3-Phase Imbalance (%) | <= 10.0% | {imb_str} | `MILP` |",
        f"| Routed Conduit (m) | {cond_ref} | {report.conduit_actual_m:.1f} | `NetworkX` |",
        f"| Indicative BOQ (EUR) | {boq_ref} | {boq_act} | `19% VAT` |",
        "",
        "## 2. Circuit Category Sizing & Safety Audit",
        "",
        "| Category | Ref Qty | CyElec Qty | Ref Sizing | CyElec Min Sizing | Status | Notes |",
        "| --- | ---: | ---: | :---: | :---: | :---: | --- |",
    ]
    for r in report.category_comparisons:
        actual_sz = (
            f"{r.actual_min_breaker_a} A / {r.actual_min_cable_mm2:g} mm²"
            if (r.actual_min_breaker_a is not None and r.actual_min_cable_mm2 is not None)
            else "-"
        )
        lines.append(
            f"| `{r.category.value}` | {r.ref_count} | {r.actual_count} | "
            f"{r.ref_breaker_a} A / {r.ref_cable_mm2:g} mm² | {actual_sz} | "
            f"**{r.status}** | {r.note} |"
        )

    if report.safety_failures:
        lines.extend(["", "## 3. Safety Failures (Action Required)", ""])
        lines.extend(f"- **[FAIL]** {s}" for s in report.safety_failures)
    if report.review_observations:
        lines.extend(["", "## 4. Engineering Review Observations", ""])
        lines.extend(f"- {obs}" for obs in report.review_observations)
    lines.append("")
    return "\n".join(lines)


def render_evaluation_html(report: EvaluationReport) -> str:
    """Render print-ready side-by-side HTML Evaluation Report with CyElec branding."""
    esc = html.escape
    logo_uri = get_logo_svg_data_uri()
    v_color = {
        "PASS": "#155724",
        "PASS_WITH_REVIEW": "#856404",
        "FAIL_SAFETY": "#721c24",
    }[report.verdict]
    v_bg = {
        "PASS": "#d4edda",
        "PASS_WITH_REVIEW": "#fff3cd",
        "FAIL_SAFETY": "#f8d7da",
    }[report.verdict]

    cat_parts: list[str] = []
    for r in report.category_comparisons:
        act_cell = (
            f"{r.actual_min_breaker_a} A / {r.actual_min_cable_mm2:g} mm²"
            if (r.actual_min_breaker_a is not None and r.actual_min_cable_mm2 is not None)
            else "-"
        )
        cat_parts.append(
            f"<tr><td><code>{esc(r.category.value)}</code></td>"
            f"<td class='r'>{r.ref_count}</td><td class='r'>{r.actual_count}</td>"
            f"<td class='c'>{r.ref_breaker_a} A / {r.ref_cable_mm2:g} mm²</td>"
            f"<td class='c'>{act_cell}</td>"
            f"<td class='c'><strong>{esc(r.status)}</strong></td>"
            f"<td>{esc(r.note)}</td></tr>"
        )
    cat_rows_html = "".join(cat_parts)
    obs_html = (
        "".join(f"<li>{esc(o)}</li>" for o in report.review_observations) or "<li>None</li>"
    )
    fail_html = (
        "".join(f"<li><strong>{esc(s)}</strong></li>" for s in report.safety_failures)
        or "<li>None — zero safety regressions</li>"
    )
    ph_status = "PHASES MATCH" if report.supply_phases_match else "MISMATCH"
    cond_ref_txt = f"{report.conduit_ref_m:.1f} m" if report.conduit_ref_m else "-"
    boq_txt = f"€{report.boq_actual_eur or 0:,.2f}"
    dev_summary = (
        f"Devices: {report.luminaires_actual} lights, "
        f"{report.sockets_actual} twin sockets, {report.split_ac_actual} A/C"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <link rel="icon" type="image/svg+xml" href="{logo_uri}" />
  <title>CyElec Evaluation Report — {esc(report.plan_name)}</title>
  <style>
    body {{
      font-family: "Noto Sans", "DejaVu Sans", Arial, sans-serif;
      margin: 20px; color: #102a43; font-size: 13px;
    }}
    header {{
      display:flex; justify-content:space-between; align-items:center;
      border-bottom:3px solid #1b365d; padding-bottom:10px; margin-bottom:16px;
    }}
    .hdr-brand {{ display:flex; align-items:center; gap:10px; }}
    .hdr-logo {{ width:32px; height:32px; border-radius:6px; }}
    h1 {{ margin:0; font-size:18px; color:#1b365d; }}
    .verdict {{
      padding:6px 12px; border-radius:6px; font-weight:700;
      color:{v_color}; background:{v_bg};
    }}
    table {{ width:100%; border-collapse:collapse; margin:10px 0 18px; font-size:12.5px; }}
    th, td {{ border:1px solid #cbd5e0; padding:6px 8px; }}
    th {{ background:#1b365d; color:#fff; text-align:left; }}
    .r {{ text-align:right; }} .c {{ text-align:center; }}
  </style>
</head>
<body>
  <header>
    <div class="hdr-brand">
      <img src="{logo_uri}" alt="CyElec" class="hdr-logo" />
      <div>
        <h1>CyElec vs. Real Engineer Plan — {esc(report.plan_name)}</h1>
        <div style="font-size:11.5px;color:#486581;">{esc(report.source_reference)}</div>
      </div>
    </div>
    <div class="verdict">{esc(report.verdict)} ({report.concordance_score_pct:.1f}%)</div>
  </header>
  <table>
    <thead>
      <tr>
        <th>Metric</th><th class="r">Reference Plan</th>
        <th class="r">CyElec Output</th><th>Delta / Status</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Floor Area (m²)</td><td class="r">{report.area_ref_m2:.1f}</td>
        <td class="r">{report.area_actual_m2:.1f}</td><td>{report.area_variance_pct:+.1f}%</td>
      </tr>
      <tr>
        <td>Rooms Detected</td><td class="r">{report.rooms_ref}</td>
        <td class="r">{report.rooms_actual}</td>
        <td>{report.rooms_actual - report.rooms_ref:+d}</td>
      </tr>
      <tr>
        <td>Supply Tier</td><td class="r">{esc(report.supply_tier_ref)}</td>
        <td class="r">{esc(report.supply_tier_actual)}</td><td>{ph_status}</td>
      </tr>
      <tr>
        <td>Diversified Demand (kVA)</td><td class="r">{report.diversified_kva_ref:.2f}</td>
        <td class="r">{report.diversified_kva_actual:.2f}</td>
        <td>{report.diversified_kva_variance_pct:+.1f}%</td>
      </tr>
      <tr>
        <td>Final Circuits</td><td class="r">{report.circuits_ref}</td>
        <td class="r">{report.circuits_actual}</td>
        <td>{report.circuits_actual - report.circuits_ref:+d}</td>
      </tr>
      <tr>
        <td>Conduit / BOQ</td><td class="r">{cond_ref_txt}</td>
        <td class="r">{report.conduit_actual_m:.1f} m / {boq_txt}</td>
        <td>{dev_summary}</td>
      </tr>
    </tbody>
  </table>
  <h2>Circuit Category Sizing &amp; Safety Audit</h2>
  <table>
    <thead>
      <tr>
        <th>Category</th><th class="r">Ref</th><th class="r">CyElec</th>
        <th class="c">Ref Sizing</th><th class="c">CyElec Min Sizing</th>
        <th class="c">Status</th><th>Notes</th>
      </tr>
    </thead>
    <tbody>{cat_rows_html}</tbody>
  </table>
  <h3>Safety Failures</h3><ul>{fail_html}</ul>
  <h3>Engineering Review Observations</h3><ul>{obs_html}</ul>
</body>
</html>"""


def export_evaluation_reports(
    report: EvaluationReport,
    out_dir: Path,
) -> dict[str, Path]:
    """Write JSON, Markdown, and HTML evaluation artifacts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "evaluation_report.json"
    md_path = out_dir / "evaluation_report.md"
    html_path = out_dir / "evaluation_report.html"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_evaluation_md(report), encoding="utf-8")
    html_path.write_text(render_evaluation_html(report), encoding="utf-8")
    return {"json": json_path, "md": md_path, "html": html_path}
