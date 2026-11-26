"""Optional native PDF exporter using BSD-licensed WeasyPrint (zero AGPLv3 dependencies)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from cyelec.models.electrical import ReviewFlag, ReviewSeverity


def is_weasyprint_available() -> bool:
    """Return True if optional BSD-licensed weasyprint package is importable."""
    return importlib.util.find_spec("weasyprint") is not None


def export_html_to_pdf(
    html_path: Path,
    pdf_path: Path,
) -> tuple[Path | None, ReviewFlag | None]:
    """Render a print-ready HTML report to PDF using optional WeasyPrint, gracefully skipping."""
    if not is_weasyprint_available():
        return None, ReviewFlag(
            code="PDF_EXPORT_SKIPPED",
            severity=ReviewSeverity.INFO,
            category="governance",
            message=(
                f"Optional WeasyPrint PDF extra not installed; skipped '{pdf_path.name}'. "
                "Install `pip install 'cyelec-pipeline[pdf]'` or print HTML to PDF."
            ),
            requires_etek_signoff=False,
        )

    try:
        import weasyprint  # type: ignore[import-not-found]

        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        weasyprint.HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        return pdf_path, None
    except Exception as exc:  # pragma: no cover - system Cairo/Pango library absence
        return None, ReviewFlag(
            code="PDF_EXPORT_SKIPPED",
            severity=ReviewSeverity.INFO,
            category="governance",
            message=f"WeasyPrint PDF rendering unavailable ({exc}); use A4 HTML print export.",
            requires_etek_signoff=False,
        )


def export_svg_sheet_to_pdf(
    svg_path: Path,
    pdf_path: Path,
    *,
    page_size: str = "A3 landscape",
) -> tuple[Path | None, ReviewFlag | None]:
    """Wrap a vector drawing SVG in a full-bleed ISO landscape HTML page and render to PDF."""
    if not is_weasyprint_available():
        return None, ReviewFlag(
            code="PDF_EXPORT_SKIPPED",
            severity=ReviewSeverity.INFO,
            category="governance",
            message=(
                f"Optional WeasyPrint PDF extra not installed; skipped '{pdf_path.name}'."
            ),
            requires_etek_signoff=False,
        )
    try:
        import weasyprint

        svg_content = svg_path.read_text(encoding="utf-8")
        # Strip optional XML declaration so inline SVG embeds cleanly inside HTML5
        if svg_content.lstrip().startswith("<?xml"):
            svg_content = svg_content.split("?>", 1)[1].lstrip()
        html_doc = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            f"<style>@page {{ size: {page_size}; margin: 4mm; }} "
            "html,body { margin:0; padding:0; width:100%; height:100%; background:#fff; } "
            "svg { width:100%; height:100%; max-height:100%; display:block; }</style>"
            f"</head><body>{svg_content}</body></html>"
        )
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        weasyprint.HTML(string=html_doc).write_pdf(str(pdf_path))
        return pdf_path, None
    except Exception as exc:  # pragma: no cover
        return None, ReviewFlag(
            code="PDF_EXPORT_SKIPPED",
            severity=ReviewSeverity.INFO,
            category="governance",
            message=f"WeasyPrint drawing PDF unavailable ({exc}); use vector SVG/DXF output.",
            requires_etek_signoff=False,
        )
