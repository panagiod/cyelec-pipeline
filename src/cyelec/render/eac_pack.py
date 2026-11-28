"""Cyprus EAC plan-approval submission cover pack (HTML + SHA-256 manifest + ZIP archive)."""

from __future__ import annotations

import hashlib
import html
import json
import zipfile
from pathlib import Path

from cyelec.assets import get_logo_svg_data_uri
from cyelec.models.electrical import ReviewSeverity
from cyelec.models.spatial import Phase2DesignResult
from cyelec.render.i18n import format_branding_line, t
from cyelec.render.templates import load_indented_css, render_template


def compute_file_sha256(path: Path) -> str:
    """Compute hex SHA-256 checksum of a file on disk."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_deliverables_manifest(
    result: Phase2DesignResult,
    deliverables: dict[str, Path],
    manifest_path: Path,
) -> Path:
    """Write deterministic SHA-256 manifest.json covering every generated submission artifact."""
    files_meta: dict[str, dict[str, object]] = {}
    for key, path in sorted(deliverables.items()):
        if path.is_file():
            files_meta[key] = {
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": compute_file_sha256(path),
            }

    payload = {
        "project_name": result.design.building.project_name,
        "pipeline_version": result.design.provenance.pipeline_version,
        "timestamp_utc": result.design.provenance.timestamp_utc,
        "input_sha256": result.design.provenance.input_sha256,
        "standards_versions": result.design.provenance.standards_versions,
        "eac_profile": result.design.eac_compliance.eac_profile.value,
        "earthing_system": result.design.earthing_system.value,
        "supply_tier": result.design.eac_compliance.recommended_supply_tier,
        "has_blockers": result.design.has_blockers,
        "etek_signoff_required": result.design.etek_signoff_required,
        "files": files_meta,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def render_eac_submission_pack_html(
    result: Phase2DesignResult,
    *,
    sld_svg: str,
    deliverables: dict[str, Path],
    floorplan_svg: str | None = None,
) -> str:
    """Render print-ready A4-landscape EAC Submission Pack Cover & Index HTML with inline SVGs."""
    design = result.design
    eac = design.eac_compliance
    prov = design.provenance
    sp = result.spatial
    lang = design.report_language
    branding_line = format_branding_line(design.branding)
    eng_name = (design.branding.engineer_name or "") if design.branding else ""
    etek_no = (design.branding.etek_reg_no or "") if design.branding else ""
    boq_txt = (
        f"€{design.boq.grand_total_eur:,.2f} (incl. VAT)"
        if design.boq is not None
        else "N/A"
    )

    def esc(val: object) -> str:
        return html.escape(str(val))

    blockers = sum(1 for f in design.review_flags if f.severity == ReviewSeverity.BLOCKER)
    warnings = sum(1 for f in design.review_flags if f.severity == ReviewSeverity.WARNING)
    infos = sum(1 for f in design.review_flags if f.severity == ReviewSeverity.INFO)

    file_rows: list[str] = []
    for key, path in sorted(deliverables.items()):
        if not path.is_file():
            continue
        sha = compute_file_sha256(path)
        file_rows.append(
            "<tr>"
            f"<td><code>{esc(key)}</code></td>"
            f"<td><strong>{esc(path.name)}</strong></td>"
            f"<td class='r'>{path.stat().st_size:,} B</td>"
            f"<td><code class='sha'>{esc(sha)}</code></td>"
            "</tr>"
        )

    bal_summary = (
        f"L1={design.phase_balance.phase_currents_a['L1']:.2f} A | "
        f"L2={design.phase_balance.phase_currents_a['L2']:.2f} A | "
        f"L3={design.phase_balance.phase_currents_a['L3']:.2f} A "
        f"(Imbalance {design.phase_balance.imbalance_pct:.2f}%)"
        if design.phase_balance is not None
        else f"Single-Phase ({eac.max_phase_demand_a:.2f} A)"
    )

    floorplan_section = (
        f"""
  <section>
    <h2>1. Electrical Floorplan Overlay (Section 701 Wet Zones, Devices &amp; Conduits)</h2>
    <div class="sld-frame">
      {floorplan_svg}
    </div>
  </section>
"""
        if floorplan_svg
        else ""
    )

    branding_banner = (
        f'<div class="brand-bar">{esc(branding_line)}</div>' if branding_line else ""
    )
    default_logo_uri = get_logo_svg_data_uri()
    header_logo_uri = (
        design.branding.logo_data_uri
        if (design.branding is not None and design.branding.logo_data_uri)
        else default_logo_uri
    )

    return render_template(
        "eac_submission_pack.html",
        {
            "__HTML_LANG__": esc(lang.value if lang.value != "bilingual" else "en"),
            "__DEFAULT_LOGO_URI__": default_logo_uri,
            "__PACK_TITLE__": esc(t("eac_pack_title", lang)),
            "__PROJECT_NAME__": esc(design.building.project_name),
            "__EAC_PACK_CSS__": load_indented_css("eac_submission_pack.css"),
            "__HEADER_LOGO_URI__": esc(header_logo_uri),
            "__PIPELINE_VERSION__": esc(prov.pipeline_version),
            "__TIMESTAMP_UTC__": esc(prov.timestamp_utc),
            "__BRANDING_BANNER__": branding_banner,
            "__SUPPLY_TIER__": esc(eac.recommended_supply_tier),
            "__EARTHING_PROFILE__": (
                f"{esc(design.earthing_system.value)} | {esc(eac.eac_profile.value)}"
            ),
            "__SPATIAL_LAYOUT__": f"{len(sp.devices)} Devices | {sp.total_conduit_m:.1f} m Conduit",
            "__BOQ_TOTAL__": esc(boq_txt),
            "__REVIEW_STATUS__": f"{blockers} Blockers / {warnings} Warn / {infos} Info",
            "__FLOOR_AREA_M2__": f"{design.building.total_floor_area_m2:.2f}",
            "__CIRCUIT_COUNT__": str(len(design.circuits)),
            "__DIVERSIFIED_KVA__": f"{eac.total_diversified_kva:.2f}",
            "__CONNECTED_KVA__": f"{eac.total_connected_kva:.2f}",
            "__BAL_SUMMARY__": esc(bal_summary),
            "__INPUT_SHA16__": esc(prov.input_sha256[:16]),
            "__FLOORPLAN_SECTION__": floorplan_section,
            "__SLD_TITLE__": esc(t("sld_title", lang)),
            "__DB_ANCHOR_ID__": esc(design.building.db_anchor.id),
            "__SLD_SVG__": sld_svg,
            "__FILE_ROWS__": "".join(file_rows),
            "__ETEK_SIGNOFF_TITLE__": esc(t("etek_signoff", lang)),
            "__ENG_NAME__": esc(eng_name or "ETEK Electrical Engineer Name"),
            "__ETEK_NO__": esc(etek_no or "ETEK Registration Number"),
        },
    )


def build_submission_zip(files: dict[str, Path], out_zip: Path) -> Path:
    """Package all generated EAC submission deliverables into a deterministic ZIP archive."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for _key, path in sorted(files.items()):
            if path.is_file():
                zf.write(path, arcname=path.name)
    return out_zip
