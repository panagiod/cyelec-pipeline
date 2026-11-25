"""Bilingual EN / EL / Bilingual labels & ETEK tenant branding helpers for deliverables."""

from __future__ import annotations

from cyelec.models.electrical import ReportLanguage, TenantBranding

_STRINGS: dict[str, tuple[str, str]] = {
    "report_title": (
        "BS 7671 & Cyprus EAC Electrical Design Report",
        "ΜΕΛΕΤΗ ΗΛΕΚΤΡΟΛΟΓΙΚΗΣ ΕΓΚΑΤΑΣΤΑΣΗΣ — BS 7671 & ΑΗΚ",
    ),
    "sld_title": (
        "SINGLE-LINE DIAGRAM (SLD)",
        "ΜΟΝΟΓΡΑΜΜΙΚΟ ΔΙΑΓΡΑΜΜΑ (SLD)",
    ),
    "eac_pack_title": (
        "CYPRUS EAC PLAN-APPROVAL SUBMISSION PACK",
        "ΦΑΚΕΛΟΣ ΥΠΟΒΟΛΗΣ ΣΧΕΔΙΩΝ ΑΗΚ",
    ),
    "load_schedule_title": (
        "LOAD SCHEDULE",
        "ΠΙΝΑΚΑΣ ΦΟΡΤΙΩΝ",
    ),
    "boq_title": (
        "Bill of Quantities (Indicative EUR + 19% Cyprus VAT)",
        "ΠΡΟΜΕΤΡΗΣΗ & ΚΟΣΤΟΛΟΓΗΣΗ (BOQ — EUR + 19% ΦΠΑ Κύπρου)",
    ),
    "statutory_notice": (
        "Automated deterministic design output requires formal review, verification, "
        "and stamp by a licensed ETEK Electrical Engineer before EAC submission.",
        "Η παρούσα μελέτη απαιτεί έλεγχο, επαλήθευση και σφραγίδα από εγγεγραμμένο "
        "Ηλεκτρολόγο Μηχανικό ΕΤΕΚ πριν την υποβολή στην ΑΗΚ.",
    ),
    "etek_signoff": (
        "Statutory ETEK Electrical Engineer Sign-Off",
        "Υπογραφή & Σφραγίδα Ηλεκτρολόγου Μηχανικού ΕΤΕΚ",
    ),
}


def t(key: str, lang: ReportLanguage) -> str:
    """Resolve a localized heading in EN, EL, or BILINGUAL ('EN / EL') mode."""
    en_txt, el_txt = _STRINGS[key]
    if lang == ReportLanguage.EL:
        return el_txt
    if lang == ReportLanguage.BILINGUAL:
        return f"{en_txt} / {el_txt}"
    return en_txt


def format_branding_line(branding: TenantBranding | None) -> str:
    """Format a compact single-line firm + ETEK engineer branding string."""
    if branding is None:
        return ""
    parts: list[str] = []
    if branding.firm_name.strip():
        parts.append(branding.firm_name.strip())
    if branding.engineer_name.strip():
        eng = branding.engineer_name.strip()
        if branding.etek_reg_no.strip():
            eng = f"{eng} (ETEK {branding.etek_reg_no.strip()})"
        parts.append(eng)
    elif branding.etek_reg_no.strip():
        parts.append(f"ETEK {branding.etek_reg_no.strip()}")
    if branding.phone.strip():
        parts.append(branding.phone.strip())
    if branding.email.strip():
        parts.append(branding.email.strip())
    return " | ".join(parts)
