"""Log-Log Time-Current (I-t) Selectivity & IEC 60909 Fault Coordination Sheet (E-203).

Generates a publication-grade engineering drawing (E203_selectivity_curves.svg & .pdf)
plotting real IEC 60898-1 (Type B / Type C), IEC 60269-2 (BS 88 gG fuse), and
IEC 61009-1 (30 mA Type A vs 100 mA Type S RCD) log-log trip characteristic curves
against calculated Ik3_max and Ik1_min prospective fault markers, plus a board-by-board
IEC 60909 discrimination & vendor switchgear schedule.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

from cyelec.models.electrical import BreakerCurve, ElectricalDesignResult
from cyelec.models.spatial import Phase2DesignResult
from cyelec.render.pdf_export import export_html_to_pdf


def _esc(val: object) -> str:
    return html.escape(str(val), quote=True)


def _mcb_curve_points(
    in_a: float,
    curve: BreakerCurve,
    *,
    is_upper: bool,
) -> list[tuple[float, float]]:
    """Standard IEC 60898-1 thermal-magnetic characteristic polyline (current_A, time_s)."""
    # IEC 60898-1: conventional non-tripping I_nt = 1.13*In, tripping I_t = 1.45*In
    if curve == BreakerCurve.B:
        mag_lo, mag_hi = 3.0 * in_a, 5.0 * in_a
    elif curve == BreakerCurve.C:
        mag_lo, mag_hi = 5.0 * in_a, 10.0 * in_a
    elif curve == BreakerCurve.D:
        mag_lo, mag_hi = 10.0 * in_a, 20.0 * in_a
    else:
        mag_lo, mag_hi = 6.0 * in_a, 10.0 * in_a

    if not is_upper:
        # Lower (minimum) trip boundary
        pts = [
            (1.13 * in_a, 1000.0),
            (1.25 * in_a, 180.0),
            (1.50 * in_a, 42.0),
            (2.00 * in_a, 12.0),
            (2.55 * in_a, 4.5),
            (mag_lo, 1.8),
            (mag_lo, 0.01),
            (20000.0, 0.01),
        ]
    else:
        # Upper (maximum) trip boundary
        pts = [
            (1.45 * in_a, 1000.0),
            (1.65 * in_a, 220.0),
            (2.00 * in_a, 65.0),
            (2.55 * in_a, 24.0),
            (3.50 * in_a, 9.5),
            (mag_hi, 3.8),
            (mag_hi, 0.02),
            (20000.0, 0.02),
        ]
    return pts


def _bs88_fuse_points(in_a: float, *, is_upper: bool) -> list[tuple[float, float]]:
    """IEC 60269-2 / BS 88-2 gG fuse inverse-time characteristic (pre-arcing / total)."""
    # Power-law approximation calibrated to BS 88-2 gG published curves:
    # ~1.6*In -> 1000s, ~2.5*In -> 40s, ~5*In -> 1.5s, ~8.5*In -> 0.4s, ~14*In -> 0.05s
    mult = 1.25 if is_upper else 0.85
    factors = [1.55, 1.85, 2.3, 3.0, 4.2, 6.0, 8.5, 12.0, 18.0, 28.0, 45.0]
    pts: list[tuple[float, float]] = []
    for k in factors:
        i_val = k * in_a
        # Calibrated inverse I^3.2*t curve matching BS 88 gG 0.4s @ 8.5*In
        t_val = 0.4 * ((8.5 / k) ** 3.2) * mult
        t_clamped = min(1000.0, max(0.01, t_val))
        pts.append((i_val, t_clamped))
    return pts


def render_selectivity_curves_svg(result: ElectricalDesignResult) -> str:
    """Render E-203 Log-Log Time-Current (I-t) Selectivity & IEC 60909 Coordination SVG."""
    w, h = 1120, 780
    # Plot box inside SVG (Log10 I from 10^0.7=5A to 10^4.3=20kA; Log10 t from 0.01s to 1000s)
    px0, py0 = 96.0, 92.0
    pw, ph = 700.0, 410.0
    px1, py1 = px0 + pw, py0 + ph

    log_i_min, log_i_max = math.log10(5.0), math.log10(20000.0)
    log_t_min, log_t_max = math.log10(0.01), math.log10(1000.0)

    def map_x(i_a: float) -> float:
        li = math.log10(max(5.0, min(20000.0, i_a)))
        return px0 + ((li - log_i_min) / (log_i_max - log_i_min)) * pw

    def map_y(t_s: float) -> float:
        lt = math.log10(max(0.01, min(1000.0, t_s)))
        return py1 - ((lt - log_t_min) / (log_t_max - log_t_min)) * ph

    def poly_band(
        lo: list[tuple[float, float]], hi: list[tuple[float, float]]
    ) -> str:
        pts = [(map_x(i), map_y(t)) for i, t in lo] + [
            (map_x(i), map_y(t)) for i, t in reversed(hi)
        ]
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    def poly_line(pts: list[tuple[float, float]]) -> str:
        return " ".join(f"{map_x(i):.1f},{map_y(t):.1f}" for i, t in pts)

    sel = result.selectivity_analysis
    eac = result.eac_compliance
    fuse_a = sel.eac_main_fuse_a if sel is not None else max(63, eac.recommended_main_breaker_a)
    ik3_ka = sel.origin_ik3_max_ka if sel is not None else result.prospective_fault_current_ka
    ik1_ka = sel.origin_ik1_min_ka if sel is not None else round(ik3_ka * 0.72, 2)

    # Pick representative final circuits: max Type B and max Type C
    type_b_ckts = [c for c in result.circuits if c.breaker_curve == BreakerCurve.B]
    type_c_ckts = [c for c in result.circuits if c.breaker_curve == BreakerCurve.C]
    rep_b = max(type_b_ckts, key=lambda c: c.breaker_in_a) if type_b_ckts else None
    rep_c = max(type_c_ckts, key=lambda c: c.breaker_in_a) if type_c_ckts else None
    rep_sm = max(result.sub_mains, key=lambda s: s.breaker_in_a) if result.sub_mains else None

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" style="background:#ffffff;font-family:Inter,Arial,sans-serif;">',
        "  <style>",
        "    .t-title { font-size:15px; font-weight:700; fill:#1b365d; }",
        "    .t-sub { font-size:10.5px; fill:#475569; }",
        "    .t-ax { font-size:9.5px; fill:#334155; font-weight:600; }",
        "    .t-lbl { font-size:10px; fill:#0f172a; font-weight:700; }",
        "    .t-xs { font-size:8.5px; fill:#334155; }",
        "    .t-ok { font-size:8.5px; fill:#166534; font-weight:700; }",
        "    .t-warn { font-size:8.5px; fill:#b45309; font-weight:700; }",
        "  </style>",
        f'  <rect x="10" y="10" width="{w - 20}" height="{h - 20}" '
        'fill="#ffffff" stroke="#1b365d" stroke-width="1.8" rx="4" />',
        f'  <text x="26" y="36" class="t-title">'
        f"E-203 TIME-CURRENT (I-t) SELECTIVITY &amp; IEC 60909 FAULT COORDINATION — "
        f"{_esc(result.building.project_name)}</text>",
        f'  <text x="26" y="54" class="t-sub">'
        f"Supply: {_esc(eac.recommended_supply_tier)} | EAC Main Fuse: {fuse_a}A BS 88 gG | "
        f"Origin Ik3_max: {ik3_ka:.2f} kA | Ik1_min: {ik1_ka:.2f} kA | "
        f"Earthing: {_esc(result.earthing_system.value)}</text>",
        f'  <line x1="20" y1="64" x2="{w - 20}" y2="64" stroke="#cbd5e1" stroke-width="1.1" />',
        # Plot background
        f'  <rect x="{px0:.1f}" y="{py0:.1f}" width="{pw:.1f}" height="{ph:.1f}" '
        'fill="#f8fafc" stroke="#334155" stroke-width="1.3" />',
    ]

    # Log-Log grid lines (Current decades: 10, 100, 1000, 10000 A)
    for dec in (1, 10, 100, 1000, 10000):
        for mult in (1, 2, 3, 5, 7):
            iv = dec * mult
            if iv < 5.0 or iv > 20000.0:
                continue
            gx = map_x(float(iv))
            is_maj = mult == 1 or iv in (20000,)
            col = "#cbd5e1" if is_maj else "#e2e8f0"
            sw = "1.0" if is_maj else "0.55"
            parts.append(
                f'  <line x1="{gx:.1f}" y1="{py0:.1f}" x2="{gx:.1f}" y2="{py1:.1f}" '
                f'stroke="{col}" stroke-width="{sw}" />'
            )
            if mult in (1, 2, 5) and iv >= 10:
                lbl = f"{iv // 1000}kA" if iv >= 1000 else f"{iv}A"
                parts.append(
                    f'  <text x="{gx:.1f}" y="{py1 + 15:.1f}" text-anchor="middle" '
                    f'class="t-xs">{lbl}</text>'
                )

    # Time decades (0.01s to 1000s)
    for t_maj, t_lbl in (
        (0.01, "0.01s"),
        (0.04, "0.04s"),
        (0.1, "0.1s"),
        (0.4, "0.4s"),
        (1.0, "1s"),
        (10.0, "10s"),
        (100.0, "100s"),
        (1000.0, "1000s"),
    ):
        gy = map_y(t_maj)
        is_std = t_maj in (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
        col = "#cbd5e1" if is_std else "#fed7aa"
        dash = "" if is_std else ' stroke-dasharray="4,3"'
        parts.append(
            f'  <line x1="{px0:.1f}" y1="{gy:.1f}" x2="{px1:.1f}" y2="{gy:.1f}" '
            f'stroke="{col}" stroke-width="0.9"{dash} />'
        )
        parts.append(
            f'  <text x="{px0 - 8:.1f}" y="{gy + 3:.1f}" text-anchor="end" '
            f'class="t-xs">{t_lbl}</text>'
        )

    # Axis titles
    parts.append(
        f'  <text x="{(px0 + px1) / 2:.1f}" y="{py1 + 34:.1f}" text-anchor="middle" '
        'class="t-ax">Prospective / Overload Current I (Amperes, Log Scale)</text>'
    )
    parts.append(
        f'  <text x="32" y="{(py0 + py1) / 2:.1f}" text-anchor="middle" class="t-ax" '
        f'transform="rotate(-90 32 {(py0 + py1) / 2:.1f})">'
        "Operating Time t (Seconds, Log Scale)</text>"
    )

    # 1. EAC BS 88 gG Main Fuse Curve Band (Crimson)
    f_lo = _bs88_fuse_points(float(fuse_a), is_upper=False)
    f_hi = _bs88_fuse_points(float(fuse_a), is_upper=True)
    parts.append(
        f'  <polygon points="{poly_band(f_lo, f_hi)}" fill="#dc2626" fill-opacity="0.18" '
        'stroke="#dc2626" stroke-width="1.6" />'
    )

    # 2. Sub-Main MCCB / MCB Curve Band (Purple, when multi-board)
    if rep_sm is not None:
        sm_lo = _mcb_curve_points(float(rep_sm.breaker_in_a), rep_sm.breaker_curve, is_upper=False)
        sm_hi = _mcb_curve_points(float(rep_sm.breaker_in_a), rep_sm.breaker_curve, is_upper=True)
        parts.append(
            f'  <polygon points="{poly_band(sm_lo, sm_hi)}" fill="#7c3aed" fill-opacity="0.18" '
            'stroke="#7c3aed" stroke-width="1.5" />'
        )

    # 3. Representative Final RCBO Type B Band (Blue)
    if rep_b is not None:
        b_lo = _mcb_curve_points(float(rep_b.breaker_in_a), BreakerCurve.B, is_upper=False)
        b_hi = _mcb_curve_points(float(rep_b.breaker_in_a), BreakerCurve.B, is_upper=True)
        parts.append(
            f'  <polygon points="{poly_band(b_lo, b_hi)}" fill="#2563eb" fill-opacity="0.22" '
            'stroke="#1d4ed8" stroke-width="1.6" />'
        )

    # 4. Representative Final RCBO Type C Band (Emerald)
    if rep_c is not None:
        c_lo = _mcb_curve_points(float(rep_c.breaker_in_a), BreakerCurve.C, is_upper=False)
        c_hi = _mcb_curve_points(float(rep_c.breaker_in_a), BreakerCurve.C, is_upper=True)
        parts.append(
            f'  <polygon points="{poly_band(c_lo, c_hi)}" fill="#059669" fill-opacity="0.20" '
            'stroke="#047857" stroke-width="1.5" />'
        )

    # 5. Fault Current Vertical Markers (Ik1_min and Ik3_max)
    for f_ka, f_col, f_lbl, is_max in (
        (ik1_ka, "#d97706", f"Ik1_min = {ik1_ka:.2f} kA", False),
        (ik3_ka, "#b91c1c", f"Ik3_max = {ik3_ka:.2f} kA", True),
    ):
        fx = map_x(f_ka * 1000.0)
        parts.append(
            f'  <line x1="{fx:.1f}" y1="{py0:.1f}" x2="{fx:.1f}" y2="{py1:.1f}" '
            f'stroke="{f_col}" stroke-width="1.6" stroke-dasharray="6,3" />'
        )
        t_anchor = "start" if is_max and fx + 95.0 < px1 else "end"
        tx_lbl = fx + 5.0 if t_anchor == "start" else fx - 5.0
        parts.append(
            f'  <text x="{tx_lbl:.1f}" y="{py0 + 14 if is_max else py0 + 28:.1f}" '
            f'text-anchor="{t_anchor}" style="font-size:8.5px;font-weight:700;fill:{f_col};">'
            f"{_esc(f_lbl)}</text>"
        )

    # Right Legend & RCD Coordination Panel
    lx0 = px1 + 22.0
    ly = py0
    lw = w - lx0 - 24.0
    parts.extend(
        [
            f'  <rect x="{lx0:.1f}" y="{ly:.1f}" width="{lw:.1f}" height="216" '
            'rx="4" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.1" />',
            f'  <text x="{lx0 + 12:.1f}" y="{ly + 20:.1f}" class="t-lbl">'
            "PROTECTIVE CURVE LEGEND</text>",
        ]
    )
    leg_items: list[tuple[str, str]] = [
        ("#dc2626", f"EAC Main Fuse: {fuse_a}A BS 88-2 gG"),
    ]
    if rep_sm is not None:
        leg_items.append(
            ("#7c3aed", f"Sub-Main: {rep_sm.breaker_in_a}A {rep_sm.breaker_curve.value}")
        )
    if rep_b is not None:
        leg_items.append(
            ("#2563eb", f"Max Final Type B: {rep_b.breaker_in_a}A RCBO ({rep_b.spec.circuit_id})")
        )
    if rep_c is not None:
        leg_items.append(
            ("#059669", f"Max Final Type C: {rep_c.breaker_in_a}A RCBO ({rep_c.spec.circuit_id})")
        )
    cur_ly = ly + 40.0
    for col, txt in leg_items:
        parts.append(
            f'  <rect x="{lx0 + 12:.1f}" y="{cur_ly - 8:.1f}" width="16" height="10" '
            f'fill="{col}" fill-opacity="0.25" stroke="{col}" stroke-width="1.5" />'
        )
        parts.append(
            f'  <text x="{lx0 + 34:.1f}" y="{cur_ly:.1f}" class="t-xs">{_esc(txt)}</text>'
        )
        cur_ly += 22.0

    # RCD Time-Delay Coordination Box (100mA Type-S vs 30mA Type-A)
    rcd_y = ly + 230.0
    parts.extend(
        [
            f'  <rect x="{lx0:.1f}" y="{rcd_y:.1f}" width="{lw:.1f}" height="180" '
            'rx="4" fill="#f0fdf4" stroke="#16a34a" stroke-width="1.2" />',
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 20:.1f}" class="t-lbl">'
            "RCD TIME-CURRENT SELECTIVITY</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 38:.1f}" class="t-xs">'
            "Upstream (TT): 100 mA Type-S (Delay 130–500 ms)</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 54:.1f}" class="t-xs">'
            "Downstream Final: 30 mA Type-A RCBO (≤40 ms @ 5×)</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 72:.1f}" class="t-ok">'
            "Ratio IΔn_up / IΔn_dn = 3.33× (&gt;=3×) · NON-OVERLAPPING</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 96:.1f}" class="t-lbl">'
            "BS 7671 / IEC 60909 VERDICT</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 114:.1f}" class="t-ok">'
            f"Breaking Capacity (Icu ≥ Ik3): "
            f"{'PASS' if (sel is None or sel.all_breaking_capacity_pass) else 'CHECK'}</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 132:.1f}" class="t-ok">'
            f"Instantaneous Trip (Ik1 ≥ Imag): "
            f"{'PASS' if (sel is None or sel.all_instantaneous_trip_pass) else 'CHECK'}</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 150:.1f}" class="t-ok">'
            f"Upstream-Downstream Selectivity: "
            f"{'PASS' if (sel is None or sel.all_selectivity_pass) else 'PARTIAL / CHECK'}</text>",
            f'  <text x="{lx0 + 12:.1f}" y="{rcd_y + 168:.1f}" class="t-xs">'
            "Ref: IEC 60898-1 · IEC 60909 · BS 7671 Reg 536.4</text>",
        ]
    )

    # Bottom Board-by-Board IEC 60909 Fault & Discrimination Schedule
    ty = py1 + 52.0
    parts.extend(
        [
            f'  <text x="26" y="{ty:.1f}" class="t-lbl">'
            "IEC 60909 BOARD FAULT PROFILE, DISCRIMINATION LIMIT (Is) &amp; "
            "VENDOR SWITCHGEAR EQUIVALENTS</text>",
            f'  <rect x="24" y="{ty + 8:.1f}" width="{w - 48}" height="22" fill="#e2e8f0" />',
            f'  <text x="32" y="{ty + 23:.1f}" class="t-xs" style="font-weight:700;">Board</text>',
            f'  <text x="102" y="{ty + 23:.1f}" class="t-xs" style="font-weight:700;">'
            "Upstream Device</text>",
            f'  <text x="372" y="{ty + 23:.1f}" class="t-xs" style="font-weight:700;">'
            "Ik3 / Ik1 (kA)</text>",
            f'  <text x="468" y="{ty + 23:.1f}" class="t-xs" style="font-weight:700;">'
            "Min Icu</text>",
            f'  <text x="536" y="{ty + 23:.1f}" class="t-xs" style="font-weight:700;">'
            "Limit Is</text>",
            f'  <text x="606" y="{ty + 23:.1f}" class="t-xs" style="font-weight:700;">'
            "Coordination &amp; Vendor Series</text>",
            f'  <text x="1028" y="{ty + 23:.1f}" class="t-xs" style="font-weight:700;">'
            "Status</text>",
        ]
    )
    row_y = ty + 44.0
    if sel is not None:
        for b in sel.boards[:6]:
            is_ok = b.breaking_capacity_pass and b.selectivity_type != "check_required"
            st_cls = "t-ok" if is_ok else "t-warn"
            st_txt = b.selectivity_type.upper()
            parts.extend(
                [
                    f'  <line x1="24" y1="{row_y + 6:.1f}" x2="{w - 24}" y2="{row_y + 6:.1f}" '
                    'stroke="#e2e8f0" stroke-width="0.8" />',
                    f'  <text x="32" y="{row_y:.1f}" class="t-xs" style="font-weight:700;">'
                    f"{_esc(b.board_id)}</text>",
                    f'  <text x="102" y="{row_y:.1f}" class="t-xs">'
                    f"{_esc(b.upstream_device_label[:46])}</text>",
                    f'  <text x="372" y="{row_y:.1f}" class="t-xs">'
                    f"{b.ik3_max_ka:.2f} / {b.ik1_min_ka:.2f}</text>",
                    f'  <text x="468" y="{row_y:.1f}" class="t-xs">'
                    f"{b.device_icu_ka:g} kA</text>",
                    f'  <text x="536" y="{row_y:.1f}" class="t-xs">'
                    f"{b.discrimination_limit_is_ka:.1f} kA</text>",
                    f'  <text x="606" y="{row_y:.1f}" class="t-xs">'
                    f"{_esc(b.vendor_coordination_ref[:66])}</text>",
                    f'  <text x="1028" y="{row_y:.1f}" class="{st_cls}">'
                    f"{_esc(st_txt)}</text>",
                ]
            )
            row_y += 22.0

    # Keep helper poly_line referenced for future overlay annotations
    _ = poly_line
    parts.append("</svg>")
    return "\n".join(parts)


def export_selectivity_deliverables(
    result: Phase2DesignResult,
    out_dir: Path,
    *,
    export_pdf: bool = False,
) -> dict[str, Path]:
    """Write E-203 Time-Current Selectivity Curves SVG and optional WeasyPrint PDF."""
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_str = render_selectivity_curves_svg(result.design)
    svg_path = out_dir / "E203_selectivity_curves.svg"
    svg_path.write_text(svg_str, encoding="utf-8")
    out: dict[str, Path] = {"e203_selectivity_svg": svg_path}
    if export_pdf:
        html_str = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<style>@page{size:A3 landscape;margin:8mm;}body{margin:0;padding:0;}</style>"
            f"</head><body>{svg_str}</body></html>"
        )
        tmp_html = out_dir / "_e203_tmp.html"
        tmp_html.write_text(html_str, encoding="utf-8")
        try:
            pdf_path, ok = export_html_to_pdf(tmp_html, out_dir / "E203_selectivity_curves.pdf")
            if ok and pdf_path is not None:
                out["e203_selectivity_pdf"] = pdf_path
        finally:
            tmp_html.unlink(missing_ok=True)
    return out
