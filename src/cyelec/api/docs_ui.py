"""Zero-CDN, GDPR-safe User Documentation & Cyprus BS 7671 / EAC Engineering Know-How Portal."""

from __future__ import annotations

import html

from cyelec import __version__
from cyelec.api.templates import load_template, render_template


def render_workbench_guide_tab_html(*, show_api_docs: bool = True) -> str:
    """Return compact Quick-Reference & Know-How HTML embedded inside the Workbench tab."""
    docs_btn = (
        '<a href="/docs" target="_blank" rel="noopener"'
        ' class="btn btn-secondary btn-sm" style="text-decoration:none;">'
        "REST API Reference (/docs) ↗</a>"
        if show_api_docs
        else ""
    )
    return render_template("guide_tab.html", {"__DOCS_BTN__": docs_btn})


def render_user_guide_html(base_url: str = "", *, show_api_docs: bool = True) -> str:
    """Return full standalone searchable User Documentation & Engineering Know-How page."""
    prefix = base_url.rstrip("/")
    og_img = html.escape(f"{prefix}/og-thumbnail.jpg", quote=True)
    logo_url = html.escape(f"{prefix}/logo.png", quote=True)
    docs_nav_link = (
        '<a class="nav-btn" href="/docs">OpenAPI (/docs)</a>' if show_api_docs else ""
    )
    return render_template(
        "guide.html",
        {
            "__GUIDE_CSS__": load_template("guide.css"),
            "__OG_IMG__": og_img,
            "__LOGO_URL__": logo_url,
            "__DOCS_NAV_LINK__": docs_nav_link,
            "__VERSION__": __version__,
        },
    )
