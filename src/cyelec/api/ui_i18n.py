"""Greek (ΕΛ) UI Localization for the CyElec ETEK Workbench & User Guide."""

from __future__ import annotations

import html
import json
from typing import Literal

from cyelec import __version__

UiLang = Literal["en", "el"]

JS_I18N_EN: dict[str, object] = {
    "ready": "Ready. Click Run Deterministic Solve.",
    "solving": "Running deterministic BS 7671 / EAC / NetworkX solve...",
    "solvingBtn": "Solving...",
    "solvedCleanly": "Solved cleanly",
    "solvedIn": "in",
    "solveFailed": "Solve failed",
    "settingsChanged": "Settings updated — click Run Deterministic Solve to refresh outputs.",
    "runSolveBtn": "Run Deterministic Solve",
    "runSolveDirty": "Run Deterministic Solve ●",
    "downloadingZip": "Building EAC ZIP Pack...",
    "downloadingStatus": "Generating complete EAC DXF / SVG / PDF / Excel ZIP pack...",
    "downloadedZipOk": "Downloaded eac_submission_pack.zip cleanly.",
    "downloadFailed": "Bundle download failed",
    "downloadZipBtn": "Download EAC ZIP Pack",
    "unpricedBoq": "Tender BOQ (Unpriced)",
    "dinDbs": "DIN-Rail DBs",
    "spare": "spare",
    "selPass": "Selectivity PASS",
    "selCheck": "Check Icu",
    "lpsReq": "LPS Class IV Req.",
    "noLps": "No External LPS",
    "pvBessHdr": "EAC 2026 PV/BESS (PVS-S-1.2):",
    "storageReady": " · Storage-Ready",
    "photTitle": "Point-by-Point 2D Photometrics & Uniformity U₀ (BS EN 12464-1)",
    "compliant": "Compliant",
    "thRoom": "Room",
    "thArea": "Area",
    "thLums": "Luminaires",
    "thTarget": "Target",
    "thCheck": "Check",
    "pass": "PASS",
    "check": "CHECK",
    "fail": "FAIL",
    "blk": "Blk",
    "wrn": "Wrn",
    "info": "Info",
    "flagsTab": "ETEK Review Flags",
    "subBoard": "Sub-Board",
    "parent": "Parent",
    "floor": "Floor",
    "upProt": "Upstream Protection",
    "subCable": "Sub-Main Cable",
    "cumulLpVd": "Cumul. L/P ΔV",
    "autoPhase": "Auto",
    "noProjects": "-- No projects yet (create one above) --",
    "revLabel": "rev",
    "thRev": "Rev",
    "thSupplyTier": "Supply Tier",
    "thTimestamp": "Timestamp",
    "need2Revs": "Save at least 2 revisions to compare diffs.",
    "noSeatsYet": "No user accounts registered on this tenant yet.",
    "relocatePrefix": "Relocate",
    "relocateSuffix": "on Floorplan",
    "clickPlacePrefix": "Click Floorplan to Place",
    "cancelTag": "(Cancel)",
    "pendingPrefix": "pending circuit override(s) staged —",
    "pendingSuffix": "click Apply & Re-Solve to recalculate BS 7671 checks and SLD.",
    "savedDefaultsOk": "Saved tenant firm branding, language & VAT defaults.",
    "saveDefaultsFail": "Save defaults failed",
    "authSignInPrompt": "Sign in or create your firm account to start designing.",
    "signInBtn": "Sign In",
    "createFirmBtn": "Create Firm Account",
    "signInRegBtn": "Sign In / Register",
    "signedOut": "Signed out.",
    "boqSecTitles": {
        "01": "SEC 01 — Switchgear & Distribution Boards",
        "02": "SEC 02 — Cables & Containment",
        "03": "SEC 03 — Lighting & Switching",
        "04": "SEC 04 — Power Outlets & Fixed Accessories",
        "05": "SEC 05 — Mechanical, HVAC, Shutters & Plant",
        "06": "SEC 06 — ELV, Security, Intercom & Life Safety",
        "07": "SEC 07 — Earthing, Bonding & Lightning Protection",
        "08": "SEC 08 — External Site Works & Trenching",
        "09": "SEC 09 — Smart Home / KNX Automation",
        "10": "SEC 10 — Testing, Commissioning & As-Builts (HMY 58)",
    },
    "secSubtotal": "Section Subtotal (SEC",
    "tenderRate": "Tender",
    "tenderFormula": "Formula",
    "boqSub": "Subtotal (excl. VAT)",
    "boqVat": "Cyprus VAT",
    "boqGrand": "GRAND TOTAL (incl. VAT)",
    "boqTender": "UNPRICED TENDER BOQ (Contractor to Fill Rates)",
}

JS_I18N_EL: dict[str, object] = {
    "ready": "Έτοιμο. Πατήστε Εκτέλεση Επίλυσης.",
    "solving": "Εκτέλεση ντετερμινιστικής επίλυσης BS 7671 / ΑΗΚ...",
    "solvingBtn": "Επίλυση...",
    "solvedCleanly": "Επιτυχής επίλυση",
    "solvedIn": "σε",
    "solveFailed": "Αποτυχία επίλυσης",
    "settingsChanged": "Οι ρυθμίσεις άλλαξαν — πατήστε Εκτέλεση Επίλυσης για ενημέρωση.",
    "runSolveBtn": "Εκτέλεση Επίλυσης",
    "runSolveDirty": "Εκτέλεση Επίλυσης ●",
    "downloadingZip": "Δημιουργία πακέτου ZIP ΑΗΚ...",
    "downloadingStatus": "Δημιουργία πλήρους φακέλου ΑΗΚ (DXF / SVG / PDF / Excel ZIP)...",
    "downloadedZipOk": "Η λήψη του eac_submission_pack.zip ολοκληρώθηκε.",
    "downloadFailed": "Αποτυχία λήψης πακέτου",
    "downloadZipBtn": "Λήψη Πακέτου ZIP ΑΗΚ",
    "unpricedBoq": "Ατιμολόγητο BOQ Προσφοράς",
    "dinDbs": "Ηλεκτρικοί Πίνακες",
    "spare": "εφεδρ.",
    "selPass": "Επιλεκτικότητα PASS",
    "selCheck": "Έλεγχος Icu",
    "lpsReq": "Απαιτείται Αλεξικέραυνο (LPS IV)",
    "noLps": "Χωρίς Εξωτ. LPS",
    "pvBessHdr": "ΑΗΚ 2026 Φ/Β & BESS (PVS-S-1.2):",
    "storageReady": " · Έτοιμο για Μπαταρία",
    "photTitle": "Φωτομετρία 2D Σημείο-προς-Σημείο & Ομοιομορφία U₀ (BS EN 12464-1)",
    "compliant": "Συμμόρφωση",
    "thRoom": "Χώρος",
    "thArea": "Εμβαδόν",
    "thLums": "Φωτιστικά",
    "thTarget": "Στόχος",
    "thCheck": "Έλεγχος",
    "pass": "ΕΓΚΡΙΤΟ",
    "check": "ΕΛΕΓΧΟΣ",
    "fail": "ΑΠΟΡΡΙΨΗ",
    "blk": "Κρίσ.",
    "wrn": "Προειδ.",
    "info": "Πληρ.",
    "flagsTab": "Επισημάνσεις ΕΤΕΚ",
    "subBoard": "Δευτερεύων Πίνακας",
    "parent": "Τροφοδότηση",
    "floor": "Όροφος",
    "upProt": "Προστασία Αναχώρησης",
    "subCable": "Καλώδιο Υποπίνακα",
    "cumulLpVd": "Συνολ. ΔV Φ/Ι",
    "autoPhase": "Αυτόμ.",
    "noProjects": "-- Δεν υπάρχουν έργα (δημιουργήστε ένα) --",
    "revLabel": "αναθ.",
    "thRev": "Αναθ.",
    "thSupplyTier": "Παροχή ΑΗΚ",
    "thTimestamp": "Ημερομηνία",
    "need2Revs": "Αποθηκεύστε τουλάχιστον 2 αναθεωρήσεις για σύγκριση.",
    "noSeatsYet": "Δεν υπάρχουν εγγεγραμμένοι χρήστες σε αυτό το γραφείο.",
    "relocatePrefix": "Μετακίνηση",
    "relocateSuffix": "στην Κάτοψη",
    "clickPlacePrefix": "Κλικ στην Κάτοψη για Τοποθέτηση",
    "cancelTag": "(Ακύρωση)",
    "pendingPrefix": "εκκρεμείς αλλαγές κυκλωμάτων —",
    "pendingSuffix": "πατήστε Εφαρμογή & Επίλυση για επανυπολογισμό BS 7671 & μονογραμμικού.",
    "savedDefaultsOk": "Αποθηκεύτηκαν τα στοιχεία γραφείου, γλώσσα & ΦΠΑ.",
    "saveDefaultsFail": "Αποτυχία αποθήκευσης",
    "authSignInPrompt": "Συνδεθείτε ή δημιουργήστε λογαριασμό γραφείου για να ξεκινήσετε.",
    "signInBtn": "Είσοδος",
    "createFirmBtn": "Δημιουργία Γραφείου",
    "signInRegBtn": "Είσοδος / Εγγραφή",
    "signedOut": "Αποσυνδεθήκατε.",
    "boqSecTitles": {
        "01": "ΕΝΟΤΗΤΑ 01 — Ηλεκτρικοί Πίνακες & Υλικό Διανομής",
        "02": "ΕΝΟΤΗΤΑ 02 — Καλώδια, Σωληνώσεις & Σχάρες",
        "03": "ΕΝΟΤΗΤΑ 03 — Φωτισμός & Διακόπτες",
        "04": "ΕΝΟΤΗΤΑ 04 — Ρευματοδότες & Μόνιμα Εξαρτήματα",
        "05": "ΕΝΟΤΗΤΑ 05 — Μηχανολογικά, Κλιματισμός, Ρολά & Αντλίες",
        "06": "ΕΝΟΤΗΤΑ 06 — Ασθενή Ρεύματα (ELV), Ασφάλεια, Θυροτηλέφωνο & Πυρανίχνευση",
        "07": "ΕΝΟΤΗΤΑ 07 — Γείωση, Ισοδυναμικές Συνδέσεις & Αντικεραυνική Προστασία",
        "08": "ΕΝΟΤΗΤΑ 08 — Εξωτερικά Έργα Οικοπέδου & Εκσκαφές",
        "09": "ΕΝΟΤΗΤΑ 09 — Έξυπνο Σπίτι / Αυτοματισμός KNX",
        "10": "ΕΝΟΤΗΤΑ 10 — Έλεγχοι, Δοκιμές & Τεκμηρίωση ΑΗΚ (Η.Μ.Υ. 58)",
    },
    "secSubtotal": "Μερικό Σύνολο (ΕΝΟΤΗΤΑ",
    "tenderRate": "Προσφορά",
    "tenderFormula": "Τύπος",
    "boqSub": "Μερικό Σύνολο (χωρίς ΦΠΑ)",
    "boqVat": "ΦΠΑ Κύπρου",
    "boqGrand": "ΓΕΝΙΚΟ ΣΥΝΟΛΟ (με ΦΠΑ)",
    "boqTender": "ΑΤΙΜΟΛΟΓΗΤΟ BOQ ΠΡΟΣΦΟΡΑΣ (Για συμπλήρωση από Εργολάβο)",
}


def get_js_i18n_json(ui_lang: UiLang) -> str:
    """Return JSON-encoded UI dictionary safe for embedding in <script>."""
    payload = JS_I18N_EL if ui_lang == "el" else JS_I18N_EN
    return json.dumps(payload, ensure_ascii=False)


# Ordered HTML chrome replacements applied ONLY to the static HTML above <script>
_STATIC_HTML_EL_MAP: tuple[tuple[str, str], ...] = (
    ('<html lang="en">', '<html lang="el">'),
    (
        "CyElec — ETEK Engineer Review &amp; Pass-2 Workbench",
        "CyElec — Πλατφόρμα Ηλεκτρολογικών Μελετών ΕΤΕΚ / ΑΗΚ",
    ),
    (
        "CyElec — ETEK Engineer Review &amp; Visual Pass-2 Workbench",
        "CyElec — Πλατφόρμα Μελετών &amp; Ελέγχου Ηλεκτρολόγου Μηχανικού ΕΤΕΚ",
    ),
    (
        "Deterministic BS 7671, IEC 60364 &amp; Cyprus EAC electrical engineering workbench.",
        "Ντετερμινιστική πλατφόρμα ηλεκτρολογικών μελετών BS 7671, IEC 60364 &amp; ΑΗΚ Κύπρου.",
    ),
    # Header & Modals
    ("Sign In / Register", "Είσοδος / Εγγραφή"),
    ("User Guide &amp; Know-How", "Οδηγός Χρήσης"),
    ("API Docs", "Τεκμηρίωση API"),
    ("BS 7671 / Cyprus EAC", "BS 7671 / ΑΗΚ Κύπρου"),
    ("CyElec ETEK Workspace", "Χώρος Εργασίας CyElec ΕΤΕΚ"),
    (
        "Sign in with your firm account or create a new consulting practice workspace.",
        "Συνδεθείτε στον λογαριασμό του γραφείου σας ή δημιουργήστε νέο χώρο εργασίας μελετητή.",
    ),
    ("Create Firm Account", "Δημιουργία Γραφείου"),
    ("Consulting Firm Name", "Επωνυμία Μελετητικού Γραφείου"),
    ("Lead Engineer Full Name", "Ονοματεπώνυμο Υπεύθυνου Μηχανικού"),
    ("Closed Pilot Invite Code", "Κωδικός Πρόσκλησης Πιλοτικού"),
    ("Work Email", "Επαγγελματικό Email"),
    ("Password (min 8 chars)", "Κωδικός Πρόσβασης (τουλ. 8 χαρακτ.)"),
    ("Min 8 characters", "Τουλάχιστον 8 χαρακτήρες"),
    (">Password<", ">Κωδικός Πρόσβασης<"),
    (">Sign In<", ">Είσοδος<"),
    (">Close<", ">Κλείσιμο<"),
    ("Close (Esc)", "Κλείσιμο (Esc)"),
    ("Drawing Sheet Viewer", "Προβολή Σχεδίου"),
    (
        "Forgot your password? Contact your firm Owner (Team Seats -> Reset Password) or Pilot\n"
        "        Administrator for instant recovery.",
        "Ξεχάσατε τον κωδικό σας; Επικοινωνήστε με τον Διαχειριστή του γραφείου "
        "(Ομάδα -> Επαναφορά Κωδικού) για άμεση ανάκτηση.",
    ),
    # Sidebar Section 1
    ("1. Input &amp; CAD Layer Wizard", "1. Εισαγωγή Κάτοψης &amp; Επίπεδα CAD"),
    ("Cyprus Benchmark Fixture", "Έτοιμο Πρότυπο Κτιρίου Κύπρου"),
    ("3-Bed Cyprus Apartment (120 m²)", "Διαμέρισμα 3 Υπνοδωματίων Κύπρου (120 m²)"),
    (
        "Detached Villa + 7.5 kVA PV + 7.4 kW EV (216 m²)",
        "Μονοκατοικία + 7.5 kVA Φ/Β + 7.4 kW EV (216 m²)",
    ),
    (
        "2-Storey Villa 228 m² (MDB -&gt; DB-GF &amp; DB-1F)",
        "Διώροφη Κατοικία 228 m² (MDB -&gt; DB-GF &amp; DB-1F)",
    ),
    (
        "Commercial Office 450 m² (CT Metering &amp; Substation)",
        "Εμπορικό Γραφείο 450 m² (Μετρητής CT 3×200A)",
    ),
    (
        "<strong>Native CAD contract:</strong> Layered AutoCAD/BIM <code>.dxf</code> /\n"
        "        <code>.dwg</code> / <code>.ifc</code> with <code>TEXT/MTEXT</code> room labels\n"
        "        (raw PDF-to-DXF vector dumps fail fast with HTTP 422).",
        "<strong>Υποστηριζόμενα αρχεία:</strong> Αρχιτεκτονικές κατόψεις <code>.dxf</code> / "
        "<code>.dwg</code> / <code>.ifc</code> ή διανυσματικά CAD <code>.pdf</code> με "
        "ετικέτες χώρων (σαρωμένες εικόνες PDF απορρίπτονται με HTTP 422).",
    ),
    ("Clear File", "Εκκαθάριση"),
    ("Multi-Floor Combiner (MDB -&gt; Sub-DBs)", "Συνδυασμός Ορόφων (MDB -&gt; Υποπίνακες)"),
    ("CAD Layer Mapping Wizard", "Οδηγός Αντιστοίχισης Επιπέδων CAD"),
    ("Advanced / API Key Access (Optional)", "Προχωρημένα / Πρόσβαση API Key (Προαιρετικό)"),
    ("Tenant Workspace ID", "Αναγνωριστικό Χώρου Εργασίας"),
    ("Tenant API Key (session only)", "Κλειδί API (μόνο για την τρέχουσα συνεδρία)"),
    # Sidebar Section 2
    ("2. BS 7671 &amp; EAC Overrides", "2. Παράμετροι BS 7671 &amp; ΑΗΚ"),
    ("EAC / BS 7671 Profile", "Πρότυπο ΑΗΚ / BS 7671"),
    ("cyprus_eac_18th_a2 (2.5 m Bath Excl)", "ΑΗΚ 18η Έκδοση A2 (Ζώνη Λουτρού 2.5 m)"),
    ("cyprus_eac_17th_a3 (3.0 m Bath Excl)", "ΑΗΚ 17η Έκδοση A3 (Ζώνη Λουτρού 3.0 m)"),
    ("Earthing System", "Σύστημα Γείωσης"),
    ("TT (Cyprus EAC Default)", "TT (Προεπιλογή ΑΗΚ Κύπρου)"),
    ("Ze / Ra (Ω, optional)", "Ze / Ra (Ω, προαιρετικό)"),
    ("Default (TT=80, TN-S=0.8)", "Προεπιλογή (TT=80, TN-S=0.8)"),
    ("Global Ambient Override (°C, optional)", "Θερμοκρασία Περιβάλλοντος (°C, προαιρ.)"),
    ("Default (40°C / 45°C)", "Προεπιλογή (40°C / 45°C)"),
    ("PV Capacity Override (kVA, optional)", "Ισχύς Φωτοβολταϊκών Φ/Β (kVA, προαιρ.)"),
    ("Use model default", "Προεπιλογή μοντέλου"),
    ("EAC Supply Headroom / Min Main Breaker", "Περιθώριο Παροχής ΑΗΚ / Ελάχ. Γεν. Ασφάλεια"),
    ("Auto (Exact Diversified Demand)", "Αυτόματα (Βάσει Ετεροχρονισμένης Ζήτησης)"),
    ("Future-Ready (+20% Spare Headroom)", "Μελλοντική Επέκταση (+20% Περιθώριο)"),
    ("Minimum 3×60A (41.6 kVA)", "Ελάχιστο 3×60A (41.6 kVA)"),
    ("Minimum 3×80A (55.4 kVA)", "Ελάχιστο 3×80A (55.4 kVA)"),
    ("Minimum 3×100A (69.3 kVA)", "Ελάχιστο 3×100A (69.3 kVA)"),
    (
        "Apply Auto Corridor Cg Grouping (Trunking / Tray Mode)",
        "Αυτόματος Συντελεστής Ομαδοποίησης Cg Διαδρόμου (Σχάρα / Κανάλι)",
    ),
    (
        "Include 100mA Type-S Main RCD on TT SLD (E-201)",
        "Συμπερίληψη Γενικού ΔΔΕ 100mA Type-S στο Μονογραμμικό TT (E-201)",
    ),
    # Sidebar Section 3
    ("3. Branding, BOQ &amp; Language", "3. Στοιχεία Γραφείου, BOQ &amp; Γλώσσα"),
    ("Deliverable Language", "Γλώσσα Παραδοτέων &amp; Εκθέσεων"),
    (
        '<option value="el">Ελληνικά (EL)</option>',
        '<option value="el" selected>Ελληνικά (EL)</option>',
    ),
    ("Bilingual (EN / EL)", "Δίγλωσσα (EN / ΕΛ)"),
    ("Cyprus VAT Rate (BOQ)", "Συντελεστής ΦΠΑ Κύπρου (BOQ)"),
    ("19% Standard Cyprus VAT", "19% Κανονικός ΦΠΑ Κύπρου"),
    ("5% Reduced Primary Residence VAT", "5% Μειωμένος ΦΠΑ Κύριας Κατοικίας"),
    (
        "Unpriced Tender BOQ (Blank Contractor Rates)",
        "Ατιμολόγητο BOQ Προσφορών (Κενές Τιμές Εργολάβου)",
    ),
    ("ETEK Engineer Name", "Ονοματεπώνυμο Μηχανικού ΕΤΕΚ"),
    ("ETEK Registration No.", "Αρ. Μητρώου ΕΤΕΚ"),
    ("Save Firm Branding &amp; VAT Defaults", "Αποθήκευση Στοιχείων Γραφείου &amp; ΦΠΑ"),
    # Sidebar Section 3b
    (
        "3b. 60-Sec Cyprus Engineering Brief (HVAC / BESS / ELV / Site)",
        "3β. Τεχνικό Δελτίο Κύπρου (Κλιματισμός / BESS / ELV / Οικόπεδο)",
    ),
    ("HVAC Plant System", "Σύστημα Κλιματισμού / Θέρμανσης (HVAC)"),
    ("Split A/C Units (20A DP Isolators)", "Κλιματιστικά Split A/C (Διπολικοί 20A)"),
    (
        "Air-to-Water Heat Pump (3P) + UFH Manifolds",
        "Αντλία Θερμότητας Αέρα-Νερού (3Φ) + Ενδοδαπέδια",
    ),
    ("VRV / VRF Central (3P) + Indoor FCUs", "Κεντρικό Σύστημα VRV / VRF (3Φ) + FCUs"),
    ("EV Wallbox (Sec. 722)", "Φορτιστής EV (Τμήμα 722)"),
    ("Auto / Model", "Αυτόματα / Μοντέλο"),
    ("None (0 kW)", "Χωρίς (0 kW)"),
    ("CAD Conduit Style", "Στυλ Σωληνώσεων CAD"),
    ("Cyprus Home-Run Arrows", "Κυπριακά Βέλη Αναχώρησης"),
    ("Full Routed Conduits", "Πλήρης Όδευση Σωληνώσεων"),
    ("Battery Storage BESS (kWh)", "Μπαταρία Αποθήκευσης BESS (kWh)"),
    ("PV Module Rating (Wp)", "Ισχύς Πλαισίου Φ/Β (Wp)"),
    ("Site Earthing", "Γείωση Οικοπέδου"),
    ("Foundation / Ring Earth", "Θεμελιακή / Δακτυλιοειδής Γείωση"),
    ("Copper Rod Pit (Ø16)", "Φρεάτιο Ράβδου Χαλκού (Ø16)"),
    ("Building Height (m)", "Ύψος Κτιρίου (m)"),
    ("Plot Width (m)", "Πλάτος Οικοπέδου (m)"),
    ("Plot Length (m)", "Μήκος Οικοπέδου (m)"),
    ("Motorized Electric Shutters", "Ηλεκτρικά Ρολά / Παντζούρια"),
    (
        "ELV Pack (9U Rack, Cat6, TV, Wi-Fi, Intercom, Alarm, CCTV)",
        "Ασθενή Ρεύματα (Rack 9U, Cat6, TV, Wi-Fi, Θυροτηλέφωνο, Συναγερμός, CCTV)",
    ),
    (
        "BS 5839-6 Grade D1 Interlinked Smoke/Heat",
        "Διασυνδεδεμένοι Ανιχνευτές Καπνού/Θερμότητας (BS 5839-6)",
    ),
    ("Kitchen Hood / Fridge / Microwave FCUs", "Παροχές Απορροφητήρα / Ψυγείου / Φούρνου (FCUs)"),
    ("Water Booster &amp; Hot Water Recirc Pumps", "Πιεστικό Νερού &amp; Κυκλοφορητής ΖΝΧ"),
    ("Sliding Yard Gate / Garage Motor", "Ηλεκτρική Αυλόπορτα / Γκαραζόπορτα"),
    (
        "Site Trenching, Garden Bollards &amp; IEC 62305-2 LPS",
        "Εξωτερικά Έργα, Φωτισμός Κήπου &amp; Αλεξικέραυνο IEC 62305-2",
    ),
    (
        "Decorative Wall Sconces (Φωτιστικά Τοίχου — Bed &amp; Living)",
        "Διακοσμητικές Απλίκες Τοίχου (Υπνοδωμάτια &amp; Καθιστικό)",
    ),
    (
        "EPS Backup Automatic Transfer Switch (Requires BESS kWh &gt; 0)",
        "Εφεδρικός Πίνακας EPS + Αυτόματος Μεταγωγέας (Απαιτεί BESS &gt; 0)",
    ),
    (
        "Swimming Pool Plant Sub-DB (BS 7671 Sec. 702)",
        "Υποπίνακας Μηχανοστασίου Πισίνας IP65 (BS 7671 Sec. 702)",
    ),
    ("Smart Home KNX Bus Automation &amp; Keypads", "Αυτοματισμός Έξυπνου Σπιτιού KNX"),
    # Sidebar Section 4
    (
        "4. EAC Cadastral, Contractor &amp; Η.Μ.Υ. 58.18-1 Pre-Fill",
        "4. Στοιχεία ΑΗΚ, Κτηματολογίου &amp; Έντυπο Η.Μ.Υ. 58.18-1",
    ),
    ("EAC District (Επαρχία)", "Επαρχία ΑΗΚ"),
    ("-- Optional --", "-- Προαιρετικό --"),
    ("Municipality / Village", "Δήμος / Κοινότητα"),
    ("Cadastral Sheet / Plan", "Φύλλο / Σχέδιο Κτηματολογίου"),
    ("Plot No. (Αρ. Τεμαχίου)", "Αρ. Τεμαχίου"),
    ("Building Permit No.", "Αρ. Άδειας Οικοδομής"),
    ("Property Owner / Applicant", "Ιδιοκτήτης / Αιτητής"),
    ("EAC Premises / Account No.", "Αρ. Υποστατικού / Λογαριασμού ΑΗΚ"),
    ("EAC Service Feed Type (Section I / SPD*1)", "Τύπος Παροχής ΑΗΚ (Τμήμα Ι / SPD*1)"),
    (
        "Underground Cable (Υπόγεια — SPD Type 2)",
        "Υπόγειο Καλώδιο (SPD Τύπου 2)",
    ),
    (
        "Overhead Line (Εναέρια — Escalates SPD Type 1+2)",
        "Εναέρια Γραμμή (Κλιμάκωση SPD Τύπου 1+2)",
    ),
    ("EAC Tariff Code (optional)", "Κωδικός Διατίμησης ΑΗΚ (προαιρετικό)"),
    ("Licensed Installer Name", "Όνομα Αδειούχου Εγκαταστάτη"),
    ("Contractor HMY Licence No.", "Αρ. Άδειας Η.Μ.Υ. Εργολάβου"),
    ("Commissioning / Inspection Date", "Ημερομηνία Επιθεώρησης / Δοκιμών"),
    (
        "<strong>Site Commissioning Measurements (Optional Η.Μ.Υ. 58.18-1 Pre-Fill):</strong>\n"
        "          Physical site testing &amp; licensed ETEK/HMY stamp remain legally mandatory.",
        "<strong>Μετρήσεις Επί Τόπου Ελέγχου (Προαιρετική Συμπλήρωση Η.Μ.Υ. 58.18-1):</strong> "
        "Οι φυσικές μετρήσεις και η σφραγίδα αδειούχου μηχανικού ΕΤΕΚ/ΗΜΥ παραμένουν υποχρεωτικές.",
    ),
    ("Measured Ze (Ω)", "Μέτρηση Ze (Ω)"),
    ("Measured Ra (Ω)", "Μέτρηση Ra (Ω)"),
    ("Main Bond (Ω ≤0.5)", "Κύρια Ισοδ. (Ω ≤0.5)"),
    ("Min IR (MΩ ≥1.0)", "Ελάχ. Μόνωση (MΩ ≥1.0)"),
    # Sidebar Action Buttons
    ("Run Deterministic Solve", "Εκτέλεση Επίλυσης"),
    ("Download EAC ZIP Pack", "Λήψη Πακέτου ZIP ΑΗΚ"),
    ("Ready. Click Run Deterministic Solve.", "Έτοιμο. Πατήστε Εκτέλεση Επίλυσης."),
    # Main Banner & KPI Cards
    ("ETEK Firm Sign-In Required:", "Απαιτείται Σύνδεση Γραφείου ΕΤΕΚ:"),
    (
        "Sign in or create a firm account to run solves and export EAC submission packs.",
        "Συνδεθείτε ή δημιουργήστε λογαριασμό γραφείου για επίλυση και εξαγωγή φακέλου ΑΗΚ.",
    ),
    ("Recommended EAC Supply", "Προτεινόμενη Παροχή ΑΗΚ"),
    ("Diversified / Connected", "Ετεροχρ. / Εγκατεστημένη"),
    ("3-Phase Balance / Conduit", "Ισοζύγιο 3Φ / Σωληνώσεις"),
    ("Indicative BOQ (incl. VAT)", "Ενδεικτικό BOQ (με ΦΠΑ)"),
    ("ETEK Review Gate", "Πύλη Ελέγχου ΕΤΕΚ"),
    # Tabs
    ("Circuits &amp; Pass-2 Overrides", "Κυκλώματα &amp; Παραμετροποίηση"),
    ("Drawing Set (E-100..E-103)", "Σχέδια Κάτοψης (E-100..E-103)"),
    ("Site &amp; Earthing (E-104)", "Οικόπεδο &amp; Γείωση (E-104)"),
    ("SLD (E-201)", "Μονογραμμικό (E-201)"),
    ("Riser &amp; DIN-Rail DB (E-202)", "Κατακόρυφο &amp; Πίνακες (E-202)"),
    ("I-t Selectivity (E-203)", "Επιλεκτικότητα I-t (E-203)"),
    ("Board Designs (E-204)", "Σχέδια Πινάκων (E-204)"),
    ("Details &amp; Notes (E-301)", "Λεπτομέρειες &amp; Σημειώσεις (E-301)"),
    ("Bill of Quantities (EUR)", "Προμέτρηση / BOQ (EUR)"),
    ("ETEK Review Flags", "Επισημάνσεις ΕΤΕΚ"),
    ("Workspace &amp; Revision Diff", "Έργα, Αναθεωρήσεις &amp; Ομάδα"),
    ("Guide &amp; Know-How", "Οδηγός &amp; Τεχνογνωσία"),
    # Circuits Tab
    ("Pending circuit edits.", "Εκκρεμείς αλλαγές κυκλωμάτων."),
    ("Apply &amp; Re-Solve", "Εφαρμογή &amp; Επίλυση"),
    ("Discard Edits", "Ακύρωση Αλλαγών"),
    (
        "<th>ID</th><th>Board</th><th>Description</th><th>Phase (Override)</th><th>Cg</th>\n"
        '                  <th class="r">Ib (A)</th><th>Device</th><th class="c">Cable/CPC</th>\n'
        '                  <th class="r">L (m)</th><th class="r">Iz/Req</th>'
        '<th class="r">ΔV (%)</th>\n'
        '                  <th class="c">Status</th>',
        "<th>Α/Α</th><th>Πίνακας</th><th>Περιγραφή Κυκλώματος</th><th>Φάση</th><th>Cg</th>\n"
        '                  <th class="r">Ib (A)</th><th>Προστασία</th>'
        '<th class="c">Καλώδιο/PE</th>\n'
        '                  <th class="r">L (m)</th><th class="r">Iz/Απαιτ.</th>'
        '<th class="r">ΔV (%)</th>\n'
        '                  <th class="c">Έλεγχος</th>',
    ),
    # Drawing Toolbars & Buttons
    ("Sheet View:", "Επιλογή Σχεδίου:"),
    ("E-100 Combined", "E-100 Συνολικό"),
    ("E-101 Lighting", "E-101 Φωτισμός"),
    ("E-101 Lux Heatmap", "E-101 Χάρτης Lux"),
    ("E-102 Power &amp; Plant", "E-102 Ισχύς &amp; Εξοπλισμός"),
    ("E-103 ELV &amp; Security", "E-103 Ασθενή &amp; Ασφάλεια"),
    ("Anchor: DB1 / MDB", "Σημείο: Πίνακας DB1 / MDB"),
    ("Anchor: EAC Meter", "Σημείο: Μετρητής ΑΗΚ"),
    ("Anchor: TT Earth Pit", "Σημείο: Φρεάτιο Γείωσης TT"),
    ("Anchor: 9U Telecom", "Σημείο: Rack 9U Τηλεπ/νιών"),
    ("Relocate DB1 on Floorplan", "Μετακίνηση DB1 στην Κάτοψη"),
    ("Reset Visual Overrides", "Επαναφορά Θέσεων"),
    (">Fit<", ">Προσαρμογή<"),
    ("Fullscreen", "Πλήρης Οθόνη"),
    ("Save SVG", "Αποθήκευση SVG"),
    (
        "E-104 Site Works, Underground Trenching, Earthing &amp; LPS",
        "E-104 Οικόπεδο, Υπόγεια Δίκτυα, Γείωση &amp; Αντικεραυνική Προστασία",
    ),
    (
        "E-201 Single-Line Diagram (IEC 60617 / BS 7671)",
        "E-201 Μονογραμμικό Διάγραμμα (IEC 60617 / BS 7671)",
    ),
    (
        "E-202 Vertical Riser Diagram &amp; Physical 18mm DIN-Rail DB Elevation",
        "E-202 Κατακόρυφο Διάγραμμα &amp; Διάταξη Ηλεκτρικών Πινάκων",
    ),
    (
        "E-203 Time-Current (I-t) Selectivity &amp; IEC 60909 Coordination",
        "E-203 Καμπύλες Επιλεκτικότητας (I-t) &amp; Συντονισμός IEC 60909",
    ),
    (
        "E-204 Main &amp; Secondary Distribution Board Front Elevations &amp; Door Schedules",
        "E-204 Όψεις Κύριου &amp; Δευτερευόντων Πινάκων και Ευρετήρια Θύρας (IEC 61439)",
    ),
    (
        "E-301 Construction Details, Earthing &amp; General Notes",
        "E-301 Κατασκευαστικές Λεπτομέρειες, Γείωση &amp; Γενικές Σημειώσεις",
    ),
    # BOQ & Flags Tables
    (
        '<th>Item</th><th>Category</th><th>Description</th><th class="c">Unit</th>\n'
        '                  <th class="r">Qty</th><th class="r">Rate (EUR)</th>'
        '<th class="r">Total (EUR)</th>',
        '<th>Κωδ.</th><th>Κατηγορία</th><th>Περιγραφή Εργασίας / Υλικού</th>'
        '<th class="c">Μον.</th>\n'
        '                  <th class="r">Ποσ.</th><th class="r">Τιμή (€)</th>'
        '<th class="r">Σύνολο (€)</th>',
    ),
    (
        "<th>Severity</th><th>Code</th><th>Category</th>\n"
        "                  <th>Circuit</th><th>Regulation</th><th>Details</th>",
        "<th>Σοβαρότητα</th><th>Κωδικός</th><th>Κατηγορία</th>\n"
        "                  <th>Κύκλωμα</th><th>Κανονισμός</th><th>Λεπτομέρειες</th>",
    ),
    # Workspace Tab
    ('placeholder="New Project Name"', 'placeholder="Όνομα Νέου Έργου"'),
    ("Save as New Revision", "Αποθήκευση Αναθεώρησης"),
    ("Diff Latest 2 Revisions", "Σύγκριση 2 Τελευταίων Αναθ."),
    ("Export Data (GDPR Art. 20)", "Εξαγωγή Δεδομένων (GDPR Άρθ. 20)"),
    ("Erase Tenant (GDPR Art. 17)", "Διαγραφή Λογαριασμού (GDPR Άρθ. 17)"),
    ("Firm Sign In / Self-Serve Registration (JWT)", "Σύνδεση / Εγγραφή Μελετητικού Γραφείου"),
    ("Not signed in via JWT.", "Δεν έχετε συνδεθεί ακόμα."),
    ("Register Firm", "Εγγραφή Γραφείου"),
    ("Sign Out", "Αποσύνδεση"),
    ("Change My Password", "Αλλαγή Κωδικού Πρόσβασης"),
    ("Save New Password", "Αποθήκευση Νέου Κωδικού"),
    ("Plan, Monthly Solve Quota &amp; Stripe Billing", "Πλάνο, Μηνιαίο Όριο &amp; Χρέωση Stripe"),
    (
        "Click Refresh Billing to inspect plan &amp; monthly solves.",
        "Πατήστε Ανανέωση για προβολή πλάνου και υπολοίπου επιλύσεων.",
    ),
    ("Refresh Billing &amp; Team", "Ανανέωση Πλάνου &amp; Ομάδας"),
    ("Billing Portal", "Πύλη Χρεώσεων"),
    (
        "Firm Team Seats (Owner / Engineer / Viewer)",
        "Θέσεις Ομάδας (Ιδιοκτήτης / Μηχανικός / Προβολή)",
    ),
    ("No firm seats loaded.", "Δεν έχουν φορτωθεί μέλη ομάδας."),
    ('<option value="engineer">Engineer</option>', '<option value="engineer">Μηχανικός</option>'),
    (
        '<option value="viewer">Viewer (Read-Only)</option>',
        '<option value="viewer">Προβολή (Μόνο Ανάγνωση)</option>',
    ),
    ('<option value="owner">Co-Owner</option>', '<option value="owner">Συνδιαχειριστής</option>'),
    ("Add Team Seat (Owner)", "Προσθήκη Μέλους Ομάδας"),
    ('href="/guide"', 'href="/el/guide"'),
    # OpenGraph / Meta & Placeholders & Misc Labels
    (
        "CyElec — ETEK Electrical Engineering &amp; EAC Workbench",
        "CyElec — Πλατφόρμα Ηλεκτρολογικών Μελετών ΕΤΕΚ / ΑΗΚ",
    ),
    (
        "Deterministic BS 7671 / IEC 60364 / Cyprus EAC DXF layout, SLD, BOQ &amp; balancing.",
        "Ντετερμινιστική μελέτη BS 7671 / IEC 60364 / ΑΗΚ: σχέδια DXF, μονογραμμικό &amp; BOQ.",
    ),
    ('placeholder="e.g., Kypros MEP Consulting Ltd"', 'placeholder="π.χ. Κύπρος Η/Μ Μελέτες Λτδ"'),
    ('placeholder="e.g., N. Papadopoulos"', 'placeholder="π.χ. Ν. Παπαδόπουλος"'),
    ('placeholder="Enter your pilot invite code"', 'placeholder="Εισάγετε κωδικό πρόσκλησης"'),
    ('placeholder="Optional: cyelec_live_..."', 'placeholder="Προαιρετικό: cyelec_live_..."'),
    ('placeholder="e.g., ETEK-E-4821"', 'placeholder="π.χ. ΕΤΕΚ-Η-4821"'),
    ('placeholder="e.g., Strovolos / Germasogeia"', 'placeholder="π.χ. Στρόβολος / Γερμασόγεια"'),
    ('placeholder="e.g., 21/54E2"', 'placeholder="π.χ. 21/54E2"'),
    ('placeholder="e.g., 412"', 'placeholder="π.χ. 412"'),
    ('placeholder="e.g., BP-2026/0841"', 'placeholder="π.χ. ΑΟ-2026/0841"'),
    (
        'placeholder="Owner full name or company"',
        'placeholder="Ονοματεπώνυμο ή Εταιρεία Ιδιοκτήτη"',
    ),
    ('placeholder="e.g., 10482910"', 'placeholder="π.χ. 10482910"'),
    ('placeholder="Auto (e.g., Code 01 Domestic)"', 'placeholder="Αυτόματα (π.χ. Κωδ. 01)"'),
    ('placeholder="Class A Electrical Contractor"', 'placeholder="Εργολάβος Ηλεκτρολόγος Τάξης Α"'),
    ('placeholder="e.g., HMY-A-1420"', 'placeholder="π.χ. ΗΜΥ-Α-1420"'),
    ('placeholder="e.g., 2026-11-27"', 'placeholder="π.χ. 2026-11-27"'),
    ('placeholder="e.g., 0.35"', 'placeholder="π.χ. 0.35"'),
    ('placeholder="e.g., 28.5"', 'placeholder="π.χ. 28.5"'),
    ('placeholder="e.g., 0.18"', 'placeholder="π.χ. 0.18"'),
    ('placeholder="e.g., 500"', 'placeholder="π.χ. 500"'),
    ('placeholder="e.g., 24"', 'placeholder="π.χ. 24"'),
    ('placeholder="e.g., 14"', 'placeholder="π.χ. 14"'),
    ("RCD 1×IΔn (ms ≤200)", "ΔΔΕ 1×IΔn (ms ≤200)"),
    ("RCD 5×IΔn (ms ≤40)", "ΔΔΕ 5×IΔn (ms ≤40)"),
    ('placeholder="Firm Name (for new signup)"', 'placeholder="Επωνυμία Γραφείου (νέα εγγραφή)"'),
    (
        'placeholder="Lead Engineer Name (for new signup)"',
        'placeholder="Όνομα Μηχανικού (νέα εγγραφή)"',
    ),
    (
        'placeholder="Closed Pilot Invite Code (if required)"',
        'placeholder="Κωδικός Πρόσκλησης (αν απαιτείται)"',
    ),
    ('placeholder="Current Password"', 'placeholder="Τρέχων Κωδικός"'),
    ('placeholder="New Password (8+ chars)"', 'placeholder="Νέος Κωδικός (8+ χαρακτ.)"'),
    ('placeholder="Member Email"', 'placeholder="Email Μέλους"'),
    ('placeholder="Full Name"', 'placeholder="Ονοματεπώνυμο"'),
    ('placeholder="Temp Password (8+)"', 'placeholder="Προσωρινός Κωδ. (8+)"'),
    ('title="Select key anchor to relocate"', 'title="Επιλογή σημείου για μετακίνηση"'),
    ("Pilot €29/mo", "Pilot €29/μήνα"),
    ("Pro €99/mo", "Pro €99/μήνα"),
    ("Run a solve to render floorplan overlay.", "Εκτελέστε επίλυση για προβολή κάτοψης."),
    ("Run a solve to render Single-Line Diagram.", "Εκτελέστε επίλυση για προβολή μονογραμμικού."),
    (
        "Run a solve to render E-104 Site Works, Underground Trenching, Earthing &amp; LPS.",
        "Εκτελέστε επίλυση για προβολή σχεδίου E-104.",
    ),
    (
        "Run a solve to render E-202 Vertical Riser Diagram &amp; 18mm DIN-Rail DB Elevation.",
        "Εκτελέστε επίλυση για προβολή σχεδίου E-202.",
    ),
    (
        "Run a solve to render E-203 Log-Log Time-Current (I-t) Selectivity Curves.",
        "Εκτελέστε επίλυση για προβολή καμπυλών E-203.",
    ),
    (
        "Run a solve to render E-204 Main &amp; Secondary Distribution Board Designs.",
        "Εκτελέστε επίλυση για προβολή σχεδίων πινάκων E-204.",
    ),
    (
        "Run a solve to render E-301 Construction Details, Earthing &amp; General Notes.",
        "Εκτελέστε επίλυση για προβολή λεπτομερειών E-301.",
    ),
    ("'Drawing Sheet (E-100..E-103)'", "'Σχέδιο Κάτοψης (E-100..E-103)'"),
    ("'E-104 Site Works & Earthing'", "'E-104 Οικόπεδο & Γείωση'"),
    ("'E-201 Single-Line Diagram'", "'E-201 Μονογραμμικό Διάγραμμα'"),
    ("'E-202 Riser & DIN-Rail DB'", "'E-202 Κατακόρυφο & Πίνακες'"),
    ("'E-203 Time-Current Selectivity'", "'E-203 Καμπύλες Επιλεκτικότητας I-t'"),
    ("'E-204 Main & Secondary Board Designs'", "'E-204 Σχέδια Κύριου & Δευτερευόντων Πινάκων'"),
    ("'E-301 Construction Details & Notes'", "'E-301 Λεπτομέρειες & Σημειώσεις'"),
)


def localize_workbench_html_to_el(html_en: str) -> str:
    """Apply idiomatic Cyprus ETEK Greek localization to the static HTML above <script>."""
    marker = "  <script>"
    if marker in html_en:
        head_body, script_part = html_en.split(marker, 1)
    else:
        head_body, script_part = html_en, ""
    out = head_body
    for src, dst in _STATIC_HTML_EL_MAP:
        out = out.replace(src, dst)
    return out + (marker + script_part if script_part else "")


def render_workbench_guide_tab_el_html(*, show_api_docs: bool = True) -> str:
    """Return Greek quick-start guide HTML embedded inside the Workbench Guide tab."""
    api_li = (
        '<li><strong>Τεκμηρίωση REST API:</strong> Ανοίξτε το '
        '<a href="/docs" target="_blank" rel="noopener">/docs</a> για αυτοματοποιημένη '
        "ενσωμάτωση με ERP / BIM.</li>"
        if show_api_docs
        else ""
    )
    return f"""
    <div style="max-width:960px;line-height:1.6;color:#102a43;">
      <h2 style="color:#1b365d;margin-top:0;">
        Οδηγός Χρήσης CyElec — Ηλεκτρολογικές Μελέτες Κύπρου (BS 7671 / ΑΗΚ)
      </h2>
      <p style="color:#475569;">
        Το <strong>CyElec v{html.escape(__version__)}</strong> εκτελεί ντετερμινιστικούς
        υπολογισμούς BS 7671 (18η Έκδοση A2 / 17η A3) και κανονισμών ΑΗΚ Κύπρου
        (40°C εσωτερικοί χώροι, 45°C στέγη/εξωτερικοί, σύστημα γείωσης TT, κλίμακες παροχής
        ΑΗΚ, επιλεκτικότητα IEC 60909 και φωτομετρία 2D κατά BS EN 12464-1).
      </p>
      <h3 style="color:#1b365d;">Ροή Εργασίας σε 4 Βήματα</h3>
      <ol>
        <li>
          <strong>Εισαγωγή Κάτοψης:</strong> Επιλέξτε ένα έτοιμο πρότυπο Κύπρου ή ανεβάστε
          αρχιτεκτονικά αρχεία <code>.dxf</code>, <code>.dwg</code>, <code>.ifc</code> ή
          διανυσματικό CAD <code>.pdf</code> (2+ αρχεία για πολυώροφα κτίρια
          <code>MDB → DB-GF / DB-1F</code>).
        </li>
        <li>
          <strong>Παραμετροποίηση &amp; Τεχνικό Δελτίο:</strong> Προσαρμόστε γείωση (TT/TN-S),
          παροχή ΑΗΚ, Φ/Β + BESS/EPS, κλιματισμό (Split / Αντλία Θερμότητας / VRV),
          φορτιστή EV, πισίνα (<code>DB-POOL IP65</code>), ΦΠΑ (19% ή 5%) και στοιχεία
          εντύπου <code>Η.Μ.Υ. 58.18-1</code>.
        </li>
        <li>
          <strong>Οπτικός Έλεγχος Pass-2:</strong> Κάντε κλικ σε οποιονδήποτε χώρο στην
          κάτοψη για αλλαγή χρήσης, μετακινήστε τον πίνακα/μετρητή ΑΗΚ, ή αλλάξτε φάση/Cg
          ανά κύκλωμα στον πίνακα κυκλωμάτων.
        </li>
        <li>
          <strong>Εξαγωγή Πλήρους Φακέλου ΑΗΚ (ZIP):</strong> Περιλαμβάνει <strong>12 εκτυπώσιμα
          PDF</strong> (<code>E-101</code>..<code>E-104</code>, <code>E-201</code> Μονογραμμικό,
          <code>E-202</code> Κατακόρυφο, <code>E-203</code> Καμπύλες Επιλεκτικότητας I-t,
          <code>E-204</code> Σχέδια Κύριου &amp; Δευτερευόντων Πινάκων, <code>E-301</code>
          Λεπτομέρειες, Τεύχος Υπολογισμών, Έντυπο ΑΗΚ <code>Η.Μ.Υ. 58.18-1</code> &amp;
          Συνοπτικό Πακέτο), αρχεία <code>R2018 DXF</code> και προμέτρηση BOQ 10 ενοτήτων
          σε <code>Excel (.xlsx)</code>.
        </li>
        {api_li}
      </ol>
      <div style="padding:10px 12px;background:#fffbeb;border:1px solid #f59e0b;border-radius:6px;
                  font-size:12px;color:#92400e;">
        <strong>Νομική Επισήμανση ΕΤΕΚ / ΑΗΚ:</strong> Όλες οι μελέτες απαιτούν τελικό έλεγχο,
        φυσικές μετρήσεις επί τόπου (Ze/Ra, μόνωση, χρόνοι ΔΔΕ) και υπογραφή/σφραγίδα
        αδειούχου Ηλεκτρολόγου Μηχανικού ΕΤΕΚ πριν την υποβολή στην ΑΗΚ.
      </div>
    </div>
    """


def render_user_guide_el_html(
    base_url: str = "",
    *,
    show_api_docs: bool = True,
) -> str:
    """Render standalone Greek User Guide page served at GET /el/guide."""
    _ = base_url
    body = render_workbench_guide_tab_el_html(show_api_docs=show_api_docs)
    return f"""<!DOCTYPE html>
<html lang="el">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CyElec — Οδηγός Χρήσης ΕΤΕΚ / ΑΗΚ (Ελληνικά)</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <style>
    body {{
      margin: 0; font-family: "Noto Sans", "DejaVu Sans", system-ui, sans-serif;
      background: #f4f7fa; color: #102a43;
    }}
    header {{
      background: #1b365d; color: #fff; padding: 12px 24px;
      display: flex; justify-content: space-between; align-items: center;
    }}
    header a {{
      color: #fff; text-decoration: none; border: 1px solid rgba(255,255,255,0.4);
      padding: 5px 10px; border-radius: 5px; font-size: 12px; margin-left: 8px;
    }}
    main {{
      max-width: 980px; margin: 24px auto; background: #fff;
      border: 1px solid #d9e2ec; border-radius: 8px; padding: 24px 28px;
    }}
  </style>
</head>
<body>
  <header>
    <strong>CyElec v{html.escape(__version__)} — Οδηγός Χρήσης (ΕΛ)</strong>
    <div>
      <a href="/el">Επιστροφή στην Πλατφόρμα (ΕΛ)</a>
      <a href="/guide">English Guide (EN)</a>
    </div>
  </header>
  <main>{body}</main>
</body>
</html>
"""
