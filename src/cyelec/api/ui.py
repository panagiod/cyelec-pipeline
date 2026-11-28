"""Zero-dependency, GDPR-safe Interactive ETEK Engineer Review & Visual Pass-2 Workbench UI."""

from __future__ import annotations

import html

from cyelec import __version__
from cyelec.api.docs_ui import render_workbench_guide_tab_html
from cyelec.api.templates import load_template, render_template
from cyelec.api.ui_i18n import (
    UiLang,
    get_js_i18n_json,
    localize_workbench_html_to_el,
    render_workbench_guide_tab_el_html,
)


def render_etek_workbench_html(
    base_url: str = "",
    *,
    show_api_docs: bool = True,
    ifc_available: bool = False,
    dwg_available: bool = False,
    auth_required: bool = False,
    invite_code_required: bool = False,
    ui_lang: UiLang = "en",
) -> str:
    """Return self-contained HTML/CSS/JS ETEK Engineer Review Dashboard (EN at / or EL at /el)."""
    prefix = base_url.rstrip("/")
    og_img = html.escape(f"{prefix}/og-thumbnail.jpg", quote=True)
    logo_url = html.escape(f"{prefix}/logo.png", quote=True)
    guide_tab_html = (
        render_workbench_guide_tab_el_html(show_api_docs=show_api_docs)
        if ui_lang == "el"
        else render_workbench_guide_tab_html(show_api_docs=show_api_docs)
    )
    api_docs_link = (
        '<a class="hdr-link" href="/docs" target="_blank" rel="noopener">API Docs</a>'
        if show_api_docs
        else ""
    )
    exts = [".dxf"]
    if dwg_available:
        exts.append(".dwg")
    if ifc_available:
        exts.append(".ifc")
    exts.append(".pdf")
    upload_accept = ",".join(exts)
    fmt_list = " / ".join(exts)
    mf_exts = ".dxf/.dwg" if dwg_available else ".dxf"
    upload_label = (
        f"Ή Ανεβάστε Αρχιτεκτονική Κάτοψη ({fmt_list} — επιλέξτε 2+ {mf_exts} για πολυώροφο)"
        if ui_lang == "el"
        else (
            f"Or Upload Architectural Floorplan(s) ({fmt_list} — "
            f"select 2+ {mf_exts} for multi-floor)"
        )
    )
    en_cls = "active" if ui_lang == "en" else ""
    el_cls = "active" if ui_lang == "el" else ""
    lang_switch_html = (
        '<div id="langSwitch" class="lang-switch" role="group" aria-label="Language">'
        f'<a href="/" class="{en_cls}" title="English">EN</a>'
        f'<a href="/el" class="{el_cls}" title="Ελληνικά">ΕΛ</a>'
        "</div>"
    )
    js_i18n_json = get_js_i18n_json(ui_lang)
    auth_req_js = "true" if auth_required else "false"
    invite_req_js = "true" if invite_code_required else "false"
    invite_disp = "block" if invite_code_required else "none"

    workbench_js = render_template(
        "workbench.js",
        {
            "__UI_LANG__": ui_lang,
            "__JS_I18N_JSON__": js_i18n_json,
            "__AUTH_REQ_JS__": auth_req_js,
            "__INVITE_REQ_JS__": invite_req_js,
        },
    )
    html_out = render_template(
        "workbench.html",
        {
            "__WORKBENCH_CSS__": load_template("workbench.css"),
            "__WORKBENCH_JS__": workbench_js,
            "__OG_IMG__": og_img,
            "__LOGO_URL__": logo_url,
            "__INVITE_DISP__": invite_disp,
            "__LANG_SWITCH_HTML__": lang_switch_html,
            "__API_DOCS_LINK__": api_docs_link,
            "__VERSION__": __version__,
            "__UPLOAD_LABEL__": upload_label,
            "__UPLOAD_ACCEPT__": upload_accept,
            "__GUIDE_TAB_HTML__": guide_tab_html,
        },
    )
    return localize_workbench_html_to_el(html_out) if ui_lang == "el" else html_out
