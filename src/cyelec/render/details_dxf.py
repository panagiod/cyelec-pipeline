"""Cyprus EAC & BS 7671 Standard Construction Details & General Notes sheet (E-301 DXF & SVG)."""

from __future__ import annotations

from html import escape
from pathlib import Path

from ezdxf.document import Drawing
from ezdxf.filemanagement import new as new_dxf

from cyelec.models.electrical import ElectricalDesignResult
from cyelec.models.spatial import Phase2DesignResult

GENERAL_NOTES_CYPRUS: list[str] = [
    "1. ALL WORKS SHALL STRICTLY COMPLY WITH BS 7671 (18TH ED. A2), CYS HD 384 & EAC (AHK) RULES.",
    (
        "2. FINAL CIRCUITS: H07V-U COPPER IN RECESSED HEAVY-DUTY PVC CONDUIT "
        "(MIN 20MM / 25MM FOR >=4MM2)."
    ),
    (
        "3. CONDUCTOR COLORS: L1=BROWN, L2=BLACK, L3=GREY, N=BLUE, "
        "PE/CPC=GREEN-YELLOW (BS 7671 TABLE 51)."
    ),
    (
        "4. PROTECTION: INDIVIDUAL 30MA TYPE A RCBO PER FINAL CIRCUIT; "
        "TYPE 2 SPD (40KA) AT ORIGIN (REG 443.4)."
    ),
    (
        "5. TT EARTHING: COPPER-BONDED ROD 16X1200MM IN INSPECTION PIT "
        "(RA <= 80 OHM); 16MM2 MEC TO MET."
    ),
    (
        "6. EQUIPOTENTIAL BONDING: 10MM2 CU MAIN BONDING TO INCOMING WATER, "
        "GAS & EXTRANEOUS METALWORK."
    ),
    (
        "7. WET ROOMS (SEC. 701): NO 13A SOCKETS WITHIN EAC 2.5M/3.0M EXCLUSION; "
        "MIN IP44 LIGHTS & FAN."
    ),
    "8. DISTRIBUTION BOARD: NON-COMBUSTIBLE ENCLOSURE (REG 421.1.201) WITH MIN 20% SPARE WAYS.",
    (
        "9. VERIFY EXACT EAC METER CABINET & EARTH PIT BOUNDARY LOCATION "
        "AGAINST LAND REGISTRY SITE PLAN."
    ),
    (
        "10. CONTRACTOR (HMY CLASS A) SHALL PERFORM FULL BS 7671 INITIAL VERIFICATION "
        "& COMPLETE FORM HMY 58."
    ),
]

MOUNTING_HEIGHTS_CYPRUS: list[tuple[str, str, str]] = [
    ("Twin 13A Socket Outlets (General)", "+450 mm FFL", "IP20 (IP65 Outdoor)"),
    ("Kitchen / Utility Worktop Sockets", "+1,150 mm FFL", "Min 300 mm from Sink/Hob"),
    ("Light Switches (1G1W / 2G1W / 1G2W)", "+1,200 mm FFL", "Outside Wet Rooms"),
    ("Master Bedroom Bedside 2-Way Switch", "+750 mm FFL", "1G2W Strapped to Entry"),
    ("Distribution Board (DB / MDB Top)", "+1,600 mm FFL", "Clear Access 1,000 mm"),
    ("45A DP Cooker Control Unit (Neon)", "+1,150 mm FFL", "Low-Level Outlet +450 mm"),
    ("20A DP Water Heater Switch (Neon)", "+1,200 mm FFL", "Flex Outlet +1,600 mm"),
    ("20A DP Split A/C & 3P Fan Isolators", "+2,200 mm FFL", "3P Fan Isolator Outside Bath"),
    ("IP44 Bathroom Vanity Mirror Light", "+2,000 mm FFL", "Zone 2 / Outside Zone 1"),
    ("Bathroom Exhaust Fan / EAC Meter Box", "+2,400 / +1,500 mm", "Verify Meter Box on Site Plan"),
]


def _unwrap_design(result: Phase2DesignResult | ElectricalDesignResult) -> ElectricalDesignResult:
    return result.design if isinstance(result, Phase2DesignResult) else result


def render_details_notes_dxf(result: Phase2DesignResult | ElectricalDesignResult) -> Drawing:
    """Build R2018 DXF E-301 Construction Details & General Notes sheet (ISO A3 Paper Space)."""
    design = _unwrap_design(result)
    doc = new_dxf("R2018", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1

    for lname, color in (
        ("E-TITLE", 7),
        ("E-DETAIL", 4),
        ("E-EARTHING", 92),
        ("E-WETZONE-Z0", 1),
        ("E-WETZONE-Z2", 30),
        ("E-WETZONE-EXCL", 6),
        ("E-NOTES", 2),
    ):
        if lname not in doc.layers:
            doc.layers.add(lname, color=color)

    msp = doc.modelspace()

    # Canvas in model-space mm (400 x 275 mm 1:1 schematic layout for clean A3 Paper Space)
    w_tot, h_tot = 400.0, 275.0
    msp.add_lwpolyline(
        [(0.0, 0.0), (w_tot, 0.0), (w_tot, h_tot), (0.0, h_tot)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_text(
        "E-301: CYPRUS EAC (AHK) & BS 7671 CONSTRUCTION DETAILS, EARTHING & GENERAL NOTES",
        dxfattribs={"layer": "E-TITLE", "height": 4.2},
    ).set_placement((8.0, h_tot - 10.0))
    msp.add_line((0.0, h_tot - 14.0), (w_tot, h_tot - 14.0), dxfattribs={"layer": "E-TITLE"})

    # Left Column (x=6..196): General Notes (top) + Mounting Heights Table (bottom)
    msp.add_text(
        "A. GENERAL ELECTRICAL NOTES (CYS HD 384 / BS 7671 18TH ED. A2 / EAC)",
        dxfattribs={"layer": "E-NOTES", "height": 3.0},
    ).set_placement((8.0, h_tot - 22.0))
    notes_dxf = list(GENERAL_NOTES_CYPRUS)
    if design.lightning_risk and design.lightning_risk.lps_required:
        notes_dxf[3] = (
            "4. PROTECTION: INDIVIDUAL 30MA TYPE A RCBO PER FINAL CIRCUIT; "
            "TYPE 1+2 SPD (12.5KA 10/350US) AT ORIGIN."
        )
    y_cursor = h_tot - 29.0
    for note in notes_dxf:
        msp.add_text(note[:92], dxfattribs={"layer": "E-TITLE", "height": 2.1}).set_placement(
            (8.0, y_cursor)
        )
        y_cursor -= 7.2

    # Mounting Heights Table (y=12..166)
    mh_top = 166.0
    msp.add_line((6.0, mh_top + 6.0), (196.0, mh_top + 6.0), dxfattribs={"layer": "E-TITLE"})
    msp.add_text(
        "B. STANDARD CYPRUS MOUNTING HEIGHTS SCHEDULE (ABOVE FFL)",
        dxfattribs={"layer": "E-NOTES", "height": 3.0},
    ).set_placement((8.0, mh_top))
    y_row = mh_top - 9.0
    msp.add_text(
        "ACCESSORY / EQUIPMENT", dxfattribs={"layer": "E-DETAIL", "height": 2.3}
    ).set_placement((8.0, y_row))
    msp.add_text("HEIGHT", dxfattribs={"layer": "E-DETAIL", "height": 2.3}).set_placement(
        (104.0, y_row)
    )
    msp.add_text("REQUIREMENT", dxfattribs={"layer": "E-DETAIL", "height": 2.3}).set_placement(
        (144.0, y_row)
    )
    msp.add_line((8.0, y_row - 2.0), (194.0, y_row - 2.0), dxfattribs={"layer": "E-TITLE"})
    y_row -= 7.0
    for item, height_s, req_s in MOUNTING_HEIGHTS_CYPRUS:
        msp.add_text(item[:40], dxfattribs={"layer": "E-TITLE", "height": 2.0}).set_placement(
            (8.0, y_row)
        )
        msp.add_text(height_s, dxfattribs={"layer": "E-NOTES", "height": 2.0}).set_placement(
            (104.0, y_row)
        )
        msp.add_text(req_s[:26], dxfattribs={"layer": "E-TITLE", "height": 1.9}).set_placement(
            (144.0, y_row)
        )
        y_row -= 7.6

    # Vertical divider between Notes/Table and 3 Vector Details
    msp.add_line((200.0, 0.0), (200.0, h_tot - 14.0), dxfattribs={"layer": "E-TITLE"})

    # DETAIL 1 (top right, x=204..394, y=178..258): TT Earth Pit & Equipotential Bonding
    msp.add_lwpolyline(
        [(204.0, 178.0), (394.0, 178.0), (394.0, 258.0), (204.0, 258.0)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_text(
        "DETAIL 1: TT EARTH INSPECTION PIT & EQUIPOTENTIAL BONDING (NTS)",
        dxfattribs={"layer": "E-EARTHING", "height": 2.8},
    ).set_placement((208.0, 252.0))
    # Ground line, concrete inspection pit, copper rod
    msp.add_line((212.0, 224.0), (315.0, 224.0), dxfattribs={"layer": "E-DETAIL"})
    msp.add_text("GROUND LEVEL", dxfattribs={"layer": "E-DETAIL", "height": 2.0}).set_placement(
        (214.0, 226.0)
    )
    msp.add_lwpolyline(
        [(246.0, 210.0), (266.0, 210.0), (266.0, 224.0), (246.0, 224.0)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_line((256.0, 218.0), (256.0, 184.0), dxfattribs={"layer": "E-EARTHING"})
    msp.add_circle((256.0, 217.0), radius=2.2, dxfattribs={"layer": "E-EARTHING"})
    msp.add_text(
        "CU ROD 16x1200mm", dxfattribs={"layer": "E-EARTHING", "height": 2.1}
    ).set_placement((262.0, 194.0))
    msp.add_text(
        f"RA <= {design.ze_or_ra_ohm:.0f} OHM (TT)",
        dxfattribs={"layer": "E-EARTHING", "height": 2.1},
    ).set_placement((262.0, 187.0))
    # 16mm2 MEC up to MET bar -> 10mm2 Bonding to Water & Gas
    msp.add_lwpolyline(
        [(256.0, 218.0), (256.0, 238.0), (318.0, 238.0)],
        dxfattribs={"layer": "E-EARTHING"},
    )
    msp.add_text("16mm2 CU MEC", dxfattribs={"layer": "E-EARTHING", "height": 2.0}).set_placement(
        (264.0, 240.0)
    )
    msp.add_lwpolyline(
        [(318.0, 232.0), (344.0, 232.0), (344.0, 244.0), (318.0, 244.0)],
        close=True,
        dxfattribs={"layer": "E-EARTHING"},
    )
    msp.add_text("MET", dxfattribs={"layer": "E-EARTHING", "height": 2.6}).set_placement(
        (325.0, 236.5)
    )
    for by, blbl in (
        (238.0, "10mm2 WATER PIPE"),
        (224.0, "10mm2 GAS / STEEL"),
        (210.0, "16mm2 TO MDB/DB1"),
    ):
        msp.add_line((344.0, 238.0), (354.0, by), dxfattribs={"layer": "E-EARTHING"})
        msp.add_text(blbl, dxfattribs={"layer": "E-TITLE", "height": 1.9}).set_placement(
            (356.0, by - 1.0)
        )

    # DETAIL 2 (mid right, x=204..394, y=92..174): EAC Meter Cabinet & Underground Duct
    msp.add_lwpolyline(
        [(204.0, 92.0), (394.0, 92.0), (394.0, 174.0), (204.0, 174.0)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_text(
        "DETAIL 2: CYPRUS EAC METER CABINET & INCOMING DUCT (NTS)",
        dxfattribs={"layer": "E-DETAIL", "height": 2.8},
    ).set_placement((208.0, 168.0))
    # Boundary pillar + Cabinet + Underground 100mm PVC duct
    msp.add_lwpolyline(
        [(220.0, 124.0), (274.0, 124.0), (274.0, 160.0), (220.0, 160.0)],
        close=True,
        dxfattribs={"layer": "E-DETAIL"},
    )
    msp.add_text("EAC METER BOX", dxfattribs={"layer": "E-TITLE", "height": 2.4}).set_placement(
        (226.0, 148.0)
    )
    msp.add_text("H=+1.5m FGL", dxfattribs={"layer": "E-NOTES", "height": 2.1}).set_placement(
        (228.0, 139.0)
    )
    msp.add_text("CUT-OUT & METER", dxfattribs={"layer": "E-TITLE", "height": 2.0}).set_placement(
        (225.0, 131.0)
    )
    msp.add_line((210.0, 114.0), (388.0, 114.0), dxfattribs={"layer": "E-DETAIL"})
    # Incoming duct from EAC street pillar + outgoing tails to MDB
    msp.add_lwpolyline(
        [(238.0, 124.0), (238.0, 100.0), (318.0, 100.0)],
        dxfattribs={"layer": "E-NOTES"},
    )
    msp.add_text(
        "100mm HEAVY PVC DUCT (MIN 600mm COVER) TO EAC PILLAR",
        dxfattribs={"layer": "E-NOTES", "height": 2.0},
    ).set_placement((242.0, 102.5))
    msp.add_lwpolyline(
        [(274.0, 144.0), (336.0, 144.0)],
        dxfattribs={"layer": "E-EARTHING"},
    )
    main_a = design.eac_compliance.recommended_main_breaker_a
    tail_mm2 = 16 if main_a <= 63 else 25
    msp.add_text(
        f"METER TAILS: 4x{tail_mm2}mm2 XLPE + 16mm2 E",
        dxfattribs={"layer": "E-TITLE", "height": 2.0},
    ).set_placement((280.0, 147.0))
    msp.add_lwpolyline(
        [(336.0, 134.0), (384.0, 134.0), (384.0, 154.0), (336.0, 154.0)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    msp.add_text("MDB / DB1", dxfattribs={"layer": "E-TITLE", "height": 2.5}).set_placement(
        (344.0, 143.0)
    )

    # DETAIL 3 (bottom right, x=204..394, y=6..88): BS 7671 Section 701 Bathroom Wet Zones
    msp.add_lwpolyline(
        [(204.0, 6.0), (394.0, 6.0), (394.0, 88.0), (204.0, 88.0)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    excl_m = design.eac_compliance.bathroom_socket_exclusion_m
    msp.add_text(
        f"DETAIL 3: BS 7671 SEC. 701 WET ZONES & EAC {excl_m:.1f}M SOCKET EXCLUSION",
        dxfattribs={"layer": "E-WETZONE-Z0", "height": 2.6},
    ).set_placement((208.0, 82.0))
    # Floor line & bathtub cross-section
    msp.add_line((212.0, 18.0), (388.0, 18.0), dxfattribs={"layer": "E-TITLE"})
    # Zone 0 (inside tub)
    msp.add_lwpolyline(
        [(216.0, 18.0), (258.0, 18.0), (258.0, 32.0), (216.0, 32.0)],
        close=True,
        dxfattribs={"layer": "E-WETZONE-Z0"},
    )
    msp.add_text("Z0 IPX7", dxfattribs={"layer": "E-WETZONE-Z0", "height": 2.2}).set_placement(
        (225.0, 23.0)
    )
    # Zone 1 (above tub to 2.25m)
    msp.add_lwpolyline(
        [(216.0, 32.0), (258.0, 32.0), (258.0, 66.0), (216.0, 66.0)],
        close=True,
        dxfattribs={"layer": "E-WETZONE-Z0"},
    )
    msp.add_text(
        "ZONE 1 (2.25m)", dxfattribs={"layer": "E-WETZONE-Z0", "height": 2.1}
    ).set_placement((219.0, 50.0))
    msp.add_text("IPX4 MIN", dxfattribs={"layer": "E-WETZONE-Z0", "height": 2.0}).set_placement(
        (224.0, 42.0)
    )
    # Zone 2 (0.60m outside tub)
    msp.add_lwpolyline(
        [(258.0, 18.0), (288.0, 18.0), (288.0, 66.0), (258.0, 66.0)],
        close=True,
        dxfattribs={"layer": "E-WETZONE-Z2"},
    )
    msp.add_text("ZONE 2", dxfattribs={"layer": "E-WETZONE-Z2", "height": 2.1}).set_placement(
        (262.0, 50.0)
    )
    msp.add_text("0.60m IPX4", dxfattribs={"layer": "E-WETZONE-Z2", "height": 1.9}).set_placement(
        (260.0, 42.0)
    )
    # EAC 2.5m/3.0m Socket Exclusion Boundary
    msp.add_lwpolyline(
        [(288.0, 18.0), (358.0, 18.0), (358.0, 66.0), (288.0, 66.0)],
        close=True,
        dxfattribs={"layer": "E-WETZONE-EXCL"},
    )
    msp.add_text(
        f"EAC NO 13A SOCKETS ({excl_m:.1f}m)",
        dxfattribs={"layer": "E-WETZONE-EXCL", "height": 2.1},
    ).set_placement((292.0, 46.0))
    msp.add_text(
        "SWITCHES OUTSIDE WET ROOM",
        dxfattribs={"layer": "E-WETZONE-EXCL", "height": 2.0},
    ).set_placement((292.0, 36.0))
    msp.add_text("OK", dxfattribs={"layer": "E-EARTHING", "height": 2.4}).set_placement(
        (366.0, 44.0)
    )

    # ISO A3 Paper-Space Layout Tab (EAC-DETAILS-A3)
    tab_name = "EAC-DETAILS-A3"
    if tab_name in doc.layouts:
        doc.layouts.delete(tab_name)
    psp = doc.layouts.new(tab_name)
    psp.page_setup(size=(420.0, 297.0), margins=(10.0, 10.0, 10.0, 10.0), units="mm")
    psp.add_lwpolyline(
        [(10.0, 10.0), (410.0, 10.0), (410.0, 287.0), (10.0, 287.0)],
        close=True,
        dxfattribs={"layer": "E-TITLE"},
    )
    psp.add_viewport(
        center=(210.0, 148.5),
        size=(394.0, 270.0),
        view_center_point=(200.0, 137.5),
        view_height=282.0,
        dxfattribs={"layer": "E-TITLE"},
    )
    return doc


def _wrap_note_lines(text: str, max_chars: int = 86) -> list[str]:
    """Word-wrap a note string cleanly to stay within a fixed SVG column width."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = f"   {w}"
    if cur:
        lines.append(cur)
    return lines


def render_details_notes_svg(result: Phase2DesignResult | ElectricalDesignResult) -> str:
    """Render print-ready A3 landscape SVG of E-301 Construction Details & General Notes."""
    design = _unwrap_design(result)
    eac = design.eac_compliance
    br = design.branding
    meta = design.eac_metadata
    firm = escape(br.firm_name if br and br.firm_name else "ETEK Licensed Electrical Practice")
    eng = escape(br.engineer_name if br and br.engineer_name else "ETEK Review Pending")
    reg = escape(br.etek_reg_no if br and br.etek_reg_no else "ETEK-E")
    proj = escape(design.building.project_name[:38])
    tier_s = escape(eac.recommended_supply_tier)
    excl_m = eac.bathroom_socket_exclusion_m
    main_a = eac.recommended_main_breaker_a
    tail_mm2 = 16 if main_a <= 63 else 25
    plot_str = escape(
        f"{meta.district} | Sheet/Plan: {meta.sheet_plan} | Plot: {meta.plot_no}"
        if meta and (meta.district or meta.plot_no)
        else "Verify Cadastral Plot & Site Boundary"
    )
    sha_short = design.provenance.input_sha256[:16]
    ra_ohm = f"{design.ze_or_ra_ohm:.0f}"
    lps_req = bool(design.lightning_risk and design.lightning_risk.lps_required)

    notes_list = list(GENERAL_NOTES_CYPRUS)
    if lps_req:
        notes_list[3] = (
            "4. PROTECTION: INDIVIDUAL 30MA TYPE A RCBO PER FINAL CIRCUIT; "
            "TYPE 1+2 SPD (12.5KA 10/350US) AT ORIGIN (IEC 62305-2 / REG 443.4)."
        )

    note_els: list[str] = []
    ny = 109
    for note in notes_list:
        for line in _wrap_note_lines(note, max_chars=82):
            note_els.append(f'  <text x="34" y="{ny}" class="note">{escape(line)}</text>')
            ny += 14
        ny += 4
    notes_svg = "\n".join(note_els)

    tbl_title_y = max(ny + 18, 434)
    tbl_hdr_y = tbl_title_y + 8
    mh_rows: list[str] = []
    for i, (item, h_s, req_s) in enumerate(MOUNTING_HEIGHTS_CYPRUS):
        ry = tbl_hdr_y + 40 + i * 26
        bg = "#f8fafc" if i % 2 == 0 else "#ffffff"
        mh_rows.append(
            f'  <rect x="30" y="{ry - 17}" width="606" height="26" fill="{bg}" stroke="#e2e8f0"/>'
            f'  <text x="38" y="{ry}" class="td">{escape(item)}</text>'
            f'  <text x="314" y="{ry}" class="td-h">{escape(h_s)}</text>'
            f'  <text x="444" y="{ry}" class="td-m">{escape(req_s)}</text>'
        )
    mh_svg = "\n".join(mh_rows)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" class="cy-e301" viewBox="0 0 1320 900"
     width="1320" height="900">
  <style>
    .cy-e301 text {{ font-family: "DejaVu Sans", Arial, sans-serif; }}
    .cy-e301 .wtitle {{ font-size: 15px; font-weight: 700; fill: #ffffff; }}
    .cy-e301 .wsub {{ font-size: 10.5px; fill: #cbd5e1; }}
    .cy-e301 .sec {{ font-size: 12px; font-weight: 700; fill: #1b365d; }}
    .cy-e301 .note {{ font-size: 9.8px; fill: #1e293b; }}
    .cy-e301 .th {{ font-size: 10.5px; font-weight: 700; fill: #ffffff; }}
    .cy-e301 .td {{ font-size: 10px; fill: #0f172a; }}
    .cy-e301 .td-h {{ font-size: 10px; font-weight: 700; fill: #1b365d; }}
    .cy-e301 .td-m {{ font-size: 9.5px; fill: #475569; }}
    .cy-e301 .d1-h {{ font-size: 11.5px; font-weight: 700; fill: #14532d; }}
    .cy-e301 .d2-h {{ font-size: 11.5px; font-weight: 700; fill: #1e3a8a; }}
    .cy-e301 .d3-h {{ font-size: 11.5px; font-weight: 700; fill: #991b1b; }}
    .cy-e301 .dlbl {{ font-size: 10.2px; font-weight: 700; fill: #0f172a; }}
    .cy-e301 .dtxt {{ font-size: 10px; fill: #334155; }}
    .cy-e301 .dgrn {{ font-size: 10.5px; font-weight: 700; fill: #15803d; }}
    .cy-e301 .damb {{ font-size: 10px; font-weight: 700; fill: #b45309; }}
    .cy-e301 .tb-h {{ font-size: 12.5px; font-weight: 700; fill: #1b365d; }}
    .cy-e301 .tb-s {{ font-size: 11px; fill: #334155; }}
    .cy-e301 .tb-m {{ font-size: 10px; fill: #64748b; }}
  </style>
  <rect x="0" y="0" width="1320" height="900" fill="#ffffff"/>
  <rect x="18" y="18" width="1284" height="864" fill="none" stroke="#1b365d" stroke-width="2.2"/>
  <rect x="18" y="18" width="1284" height="48" fill="#1b365d"/>
  <text x="34" y="48" class="wtitle">
    E-301 — CYPRUS EAC (ΑΗΚ) &amp; BS 7671 DETAILS, EARTHING &amp; NOTES
  </text>
  <text x="1286" y="47" text-anchor="end" class="wsub">
    {proj} · {tier_s} · {design.earthing_system.value}
  </text>

  <!-- Left Column (x=30..636): General Notes + Mounting Heights Table -->
  <text x="34" y="87" class="sec">
    A. GENERAL ELECTRICAL NOTES / ΓΕΝΙΚΕΣ ΣΗΜΕΙΩΣΕΙΣ (BS 7671 / CYS HD 384 / ΑΗΚ)
  </text>
  <line x1="30" y1="93" x2="636" y2="93" stroke="#1b365d" stroke-width="1.2"/>
{notes_svg}

  <text x="34" y="{tbl_title_y}" class="sec">
    B. STANDARD CYPRUS MOUNTING HEIGHTS SCHEDULE (ABOVE FINISHED FLOOR LEVEL)
  </text>
  <rect x="30" y="{tbl_hdr_y}" width="606" height="23" fill="#1b365d"/>
  <text x="38" y="{tbl_hdr_y + 15}" class="th">Accessory / Equipment</text>
  <text x="314" y="{tbl_hdr_y + 15}" class="th">Mounting Height</text>
  <text x="444" y="{tbl_hdr_y + 15}" class="th">Installation Note</text>
{mh_svg}

  <!-- Right Column (x=658..1290): 3 Standard Vector Details -->
  <!-- Detail 1: TT Earth Inspection Pit & Main Protective Bonding -->
  <rect x="658" y="76" width="632" height="226" fill="#fcfdfd" stroke="#1b365d" stroke-width="1.4"/>
  <rect x="658" y="76" width="632" height="26" fill="#e6f4ea"/>
  <text x="672" y="94" class="d1-h">
    DETAIL 1 — TT COPPER EARTH ROD PIT &amp; EQUIPOTENTIAL BONDING (BS 7671 CH. 54)
  </text>
  <line x1="676" y1="184" x2="914" y2="184" stroke="#64748b" stroke-width="2"/>
  <text x="680" y="178" class="dtxt">Finished Ground Level (FGL)</text>
  <rect x="768" y="184" width="54" height="42" fill="#e2e8f0" stroke="#334155" stroke-width="1.5"/>
  <line x1="795" y1="198" x2="795" y2="288" stroke="#15803d" stroke-width="4"/>
  <circle cx="795" cy="198" r="5" fill="#15803d"/>
  <text x="812" y="248" class="dgrn">Ø16×1200mm Cu Earth Rod</text>
  <text x="812" y="266" class="dtxt">Concrete Inspection Pit (Ra ≤ {ra_ohm} Ω)</text>
  <polyline points="795,198 795,142 958,142" fill="none" stroke="#15803d" stroke-width="2.6"/>
  <text x="798" y="135" class="dgrn">16 mm² Cu Main Earth (MEC)</text>
  <rect x="958" y="126" width="70" height="32" fill="#dcfce7" stroke="#15803d" stroke-width="2"/>
  <text x="993" y="147" text-anchor="middle" class="d1-h">MET</text>
  <line x1="1028" y1="136" x2="1076" y2="126" stroke="#15803d" stroke-width="2"/>
  <text x="1082" y="130" class="dlbl">10 mm² Cu → Water Pipe</text>
  <line x1="1028" y1="144" x2="1076" y2="160" stroke="#15803d" stroke-width="2"/>
  <text x="1082" y="164" class="dlbl">10 mm² Cu → Gas / Steel</text>
  <line x1="1028" y1="152" x2="1076" y2="194" stroke="#15803d" stroke-width="2"/>
  <text x="1082" y="198" class="dlbl">16 mm² Cu → MDB / DB1</text>

  <!-- Detail 2: EAC Meter Cabinet & Underground Duct -->
  <rect x="658" y="318" width="632" height="222" fill="#fcfdfd"
        stroke="#1b365d" stroke-width="1.4"/>
  <rect x="658" y="318" width="632" height="26" fill="#eff6ff"/>
  <text x="672" y="336" class="d2-h">
    DETAIL 2 — CYPRUS EAC METER CABINET (ΚΙΒΩΤΙΟ ΜΕΤΡΗΤΗ ΑΗΚ) &amp; INCOMING DUCT
  </text>
  <rect x="684" y="362" width="168" height="92" fill="#dbeafe" stroke="#1e40af" stroke-width="2"/>
  <text x="768" y="392" text-anchor="middle" class="d2-h">EAC METER BOX</text>
  <text x="768" y="411" text-anchor="middle" class="dtxt">Plot Boundary · H=+1.5m FGL</text>
  <text x="768" y="431" text-anchor="middle" class="dtxt">
    EAC Cut-Out + {escape(eac.recommended_supply_tier.split("(")[0].strip())}
  </text>
  <line x1="674" y1="472" x2="1274" y2="472" stroke="#64748b" stroke-width="1.8"/>
  <polyline points="742,454 742,506 1054,506" fill="none" stroke="#b45309" stroke-width="3.2"/>
  <text x="754" y="497" class="damb">
    Ø100 mm Heavy-Duty PVC Duct (Min 600 mm cover + warning tape)
  </text>
  <text x="754" y="523" class="dtxt">
    Underground service entry from EAC LV street distribution pillar
  </text>
  <line x1="852" y1="408" x2="1090" y2="408" stroke="#15803d" stroke-width="3"/>
  <text x="971" y="398" text-anchor="middle" class="dlbl">
    4×{tail_mm2} mm² XLPE/PVC + 16 mm² E
  </text>
  <rect x="1090" y="378" width="168" height="60" fill="#f1f5f9" stroke="#1b365d" stroke-width="2"/>
  <text x="1174" y="405" text-anchor="middle" class="sec">MDB / DB1</text>
  <text x="1174" y="424" text-anchor="middle" class="dtxt">H=+1.6m FFL (Indoor)</text>

  <!-- Detail 3: BS 7671 Section 701 Wet Zones Elevation -->
  <rect x="658" y="556" width="632" height="226" fill="#fcfdfd"
        stroke="#1b365d" stroke-width="1.4"/>
  <rect x="658" y="556" width="632" height="26" fill="#fef2f2"/>
  <text x="672" y="574" class="d3-h">
    DETAIL 3 — BS 7671 SECTION 701 WET ZONES &amp; CYPRUS EAC {excl_m:.1f} m SOCKET EXCLUSION
  </text>
  <rect x="680" y="714" width="136" height="44" fill="#fee2e2" stroke="#dc2626" stroke-width="1.8"/>
  <text x="748" y="740" text-anchor="middle" class="d3-h">ZONE 0 (IPX7)</text>
  <rect x="680" y="604" width="136" height="110" fill="#ffedd5"
        stroke="#ea580c" stroke-width="1.6"/>
  <text x="748" y="652" text-anchor="middle" class="dlbl">ZONE 1 (2.25 m)</text>
  <text x="748" y="672" text-anchor="middle" class="dtxt">Min IPX4 / SELV</text>
  <rect x="816" y="604" width="102" height="154" fill="#fef9c3"
        stroke="#ca8a04" stroke-width="1.6"/>
  <text x="867" y="670" text-anchor="middle" class="dlbl">ZONE 2</text>
  <text x="867" y="688" text-anchor="middle" class="dtxt">0.60 m (IPX4)</text>
  <rect x="918" y="604" width="256" height="154" fill="#fae8ff" stroke="#a21caf" stroke-width="1.6"
        stroke-dasharray="6,4"/>
  <text x="1046" y="668" text-anchor="middle" class="dlbl">
    EAC NO 13A SOCKETS ({excl_m:.1f} m)
  </text>
  <text x="1046" y="690" text-anchor="middle" class="dtxt">
    Switches &amp; 3P Fan Isolator Outside Wet Room
  </text>
  <rect x="1174" y="604" width="96" height="154" fill="#dcfce7"
        stroke="#15803d" stroke-width="1.5"/>
  <text x="1222" y="684" text-anchor="middle" class="dgrn">13A OK</text>
  <line x1="672" y1="758" x2="1276" y2="758" stroke="#0f172a" stroke-width="2"/>

  <!-- Bottom Titleblock Strip -->
  <rect x="18" y="800" width="1284" height="82" fill="#f8fafc" stroke="#1b365d" stroke-width="1.8"/>
  <text x="34" y="824" class="tb-h">
    FIRM: {firm} &#160;|&#160; ENGINEER: {eng} ({reg})
  </text>
  <text x="34" y="847" class="tb-s">
    CADASTRAL: {plot_str} &#160;|&#160; DRAWING: E-301 (ISO A3 — NOT TO SCALE)
  </text>
  <text x="34" y="868" class="tb-m">
    SHA256: {sha_short} · ETEK STATUTORY REVIEW &amp; STAMP REQUIRED BEFORE EAC SUBMISSION
  </text>
</svg>
"""


def export_details_notes(
    result: Phase2DesignResult | ElectricalDesignResult,
    out_dir: Path,
) -> dict[str, Path]:
    """Export E-301 Construction Details & General Notes in DXF and SVG formats."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dxf_path = out_dir / "E301_details_notes.dxf"
    svg_path = out_dir / "E301_details_notes.svg"
    render_details_notes_dxf(result).saveas(str(dxf_path))
    svg_path.write_text(render_details_notes_svg(result), encoding="utf-8")
    return {"e301_details_dxf": dxf_path, "e301_details_svg": svg_path}
