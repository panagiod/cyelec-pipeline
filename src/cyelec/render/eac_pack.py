"""Cyprus EAC plan-approval submission cover pack (HTML + SHA-256 manifest + ZIP archive)."""

from __future__ import annotations

import hashlib
import html
import json
import zipfile
from pathlib import Path

from cyelec.models.electrical import ReviewSeverity
from cyelec.models.spatial import Phase2DesignResult
from cyelec.render.i18n import format_branding_line, t


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
            f"<td><code>{esc(sha[:24])}…</code></td>"
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

    return f"""<!DOCTYPE html>
<html lang="{esc(lang.value if lang.value != 'bilingual' else 'en')}">
<head>
  <meta charset="utf-8" />
  <title>{esc(t('eac_pack_title', lang))} — {esc(design.building.project_name)}</title>
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
      margin-bottom: 12px;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
    }}
    h1 {{ font-size: 14pt; color: #1b365d; margin: 0; }}
    h2 {{
      font-size: 11pt;
      color: #1b365d;
      margin: 14px 0 6px 0;
      border-bottom: 1px solid #cbd5e0;
      padding-bottom: 2px;
    }}
    .brand-bar {{
      background: #edf2f7;
      border-left: 4px solid #1b365d;
      padding: 5px 10px;
      margin-bottom: 10px;
      font-size: 8.8pt;
      font-weight: 600;
      color: #1b365d;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 10px;
      margin-bottom: 12px;
    }}
    .card {{
      background: #f7fafc;
      border: 1px solid #cbd5e0;
      border-left: 4px solid #1b365d;
      padding: 8px 10px;
      border-radius: 4px;
    }}
    .card-k {{ font-size: 7.8pt; color: #4a5568; text-transform: uppercase; }}
    .card-v {{ font-size: 10.5pt; font-weight: 700; color: #1b365d; margin-top: 2px; }}
    .sld-frame {{
      border: 1px solid #cbd5e0;
      padding: 6px;
      background: #ffffff;
      overflow: hidden;
      page-break-inside: avoid;
    }}
    .sld-frame svg {{ width: 100%; height: auto; display: block; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 8.5pt; }}
    th, td {{ border: 1px solid #cbd5e0; padding: 5px 7px; }}
    th {{ background: #1b365d; color: #ffffff; text-align: left; }}
    .r {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .signoff {{
      margin-top: 14px;
      border: 1.5px solid #1b365d;
      padding: 10px;
      page-break-inside: avoid;
    }}
    .signoff-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-top: 20px;
    }}
    .sig-line {{ border-top: 1px solid #4a5568; padding-top: 4px; font-size: 8pt; color: #4a5568; }}
    code {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; }}
  </style>
</head>
<body>
  <header>
    <h1>{esc(t('eac_pack_title', lang))} — {esc(design.building.project_name)}</h1>
    <div>
      <code>cyelec-pipeline v{esc(prov.pipeline_version)}</code> | {esc(prov.timestamp_utc)}
    </div>
  </header>

  {branding_banner}

  <div class="cards">
    <div class="card">
      <div class="card-k">Recommended EAC Supply</div>
      <div class="card-v">{esc(eac.recommended_supply_tier)}</div>
    </div>
    <div class="card">
      <div class="card-k">Earthing &amp; Profile</div>
      <div class="card-v">{esc(design.earthing_system.value)} | {esc(eac.eac_profile.value)}</div>
    </div>
    <div class="card">
      <div class="card-k">Spatial Layout</div>
      <div class="card-v">{len(sp.devices)} Devices | {sp.total_conduit_m:.1f} m Conduit</div>
    </div>
    <div class="card">
      <div class="card-k">Indicative BOQ Total</div>
      <div class="card-v">{esc(boq_txt)}</div>
    </div>
    <div class="card">
      <div class="card-k">Review Gate Status</div>
      <div class="card-v">{blockers} Blockers / {warnings} Warn / {infos} Info</div>
    </div>
  </div>

  <p>
    <strong>Floor Area:</strong> {design.building.total_floor_area_m2:.2f} m² |
    <strong>Circuits:</strong> {len(design.circuits)} |
    <strong>Demand:</strong> {eac.total_diversified_kva:.2f} kVA diversified
    ({eac.total_connected_kva:.2f} kVA connected) |
    <strong>Balance:</strong> {esc(bal_summary)} |
    <strong>Input SHA-256:</strong> <code>{esc(prov.input_sha256[:16])}</code>
  </p>

  {floorplan_section}

  <section>
    <h2>2. {esc(t('sld_title', lang))} — {esc(design.building.db_anchor.id)}</h2>
    <div class="sld-frame">
      {sld_svg}
    </div>
  </section>

  <section>
    <h2>3. Submission Deliverable Manifest (SHA-256 Verified)</h2>
    <table>
      <thead>
        <tr><th>Artifact Key</th><th>Filename</th><th class="r">Size</th><th>SHA-256</th></tr>
      </thead>
      <tbody>
        {''.join(file_rows)}
      </tbody>
    </table>
  </section>

  <section class="signoff">
    <strong>4. {esc(t('etek_signoff', lang))}</strong>
    <div class="signoff-grid">
      <div class="sig-line">{esc(eng_name or 'ETEK Electrical Engineer Name')}</div>
      <div class="sig-line">{esc(etek_no or 'ETEK Registration Number')}</div>
      <div class="sig-line">Signature &amp; Official Stamp</div>
      <div class="sig-line">Date</div>
    </div>
  </section>
</body>
</html>
"""


def build_submission_zip(files: dict[str, Path], out_zip: Path) -> Path:
    """Package all generated EAC submission deliverables into a deterministic ZIP archive."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for _key, path in sorted(files.items()):
            if path.is_file():
                zf.write(path, arcname=path.name)
    return out_zip
