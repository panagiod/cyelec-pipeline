"""Deterministic Cyprus Electrical Bill of Quantities (BOQ) generator in EUR + 19% VAT."""

from __future__ import annotations

from collections import Counter, defaultdict

from cyelec.models.electrical import (
    BoqItem,
    BoqRateOverrides,
    BoqSummary,
    CircuitCategory,
    EarthingSystem,
    ElectricalDesignResult,
)
from cyelec.models.spatial import DeviceType, SpatialLayoutResult
from cyelec.standards_loader import CyprusBoqRates, StandardsBundle


def _merge_boq_rates(
    base: CyprusBoqRates,
    overrides: BoqRateOverrides | None,
) -> tuple[CyprusBoqRates, bool]:
    """Merge per-tenant or per-project custom EUR rates onto the base Cyprus BOQ table."""
    if overrides is None:
        return base, False
    has_any = bool(
        overrides.cable_eur_per_m
        or overrides.conduit_eur_per_m
        or overrides.switchgear_eur
        or overrides.accessories_eur
        or overrides.vat_rate is not None
    )
    if not has_any:
        return base, False

    cables = dict(base.cable_eur_per_m)
    for k, v in overrides.cable_eur_per_m.items():
        try:
            norm_k = f"{float(k):.1f}"
        except ValueError:
            norm_k = k
        cables[norm_k] = v

    conduits = {**base.conduit_eur_per_m, **overrides.conduit_eur_per_m}
    switchgear = {**base.switchgear_eur, **overrides.switchgear_eur}
    accessories = {**base.accessories_eur, **overrides.accessories_eur}
    vat_rate = overrides.vat_rate if overrides.vat_rate is not None else base.vat_rate

    merged = base.model_copy(
        update={
            "cable_eur_per_m": cables,
            "conduit_eur_per_m": conduits,
            "switchgear_eur": switchgear,
            "accessories_eur": accessories,
            "vat_rate": vat_rate,
        }
    )
    return merged, True


def generate_boq(
    design: ElectricalDesignResult,
    standards: StandardsBundle,
    *,
    spatial: SpatialLayoutResult | None = None,
    rate_overrides: BoqRateOverrides | None = None,
    tender_blank_rates: bool = False,
) -> BoqSummary:
    """Compute an itemized Cyprus electrical BOQ (EUR + VAT) with custom/tender support."""
    rates, custom_applied = _merge_boq_rates(standards.boq_rates, rate_overrides)
    eac = design.eac_compliance
    items: list[BoqItem] = []
    seq = 1

    def _add(
        category: str,
        desc_en: str,
        desc_el: str,
        unit: str,
        qty: float,
        unit_rate: float,
    ) -> None:
        nonlocal seq
        q = round(qty, 2)
        if q <= 0.0:
            return
        eff_rate = 0.0 if tender_blank_rates else round(unit_rate, 2)
        total = 0.0 if tender_blank_rates else round(q * eff_rate, 2)
        items.append(
            BoqItem(
                item_no=f"BOQ-{seq:02d}",
                category=category,
                description_en=desc_en,
                description_el=desc_el,
                unit=unit,
                quantity=q,
                unit_rate_eur=eff_rate,
                total_eur=total,
            )
        )
        seq += 1

    # ------------------------------------------------------------------
    # 1. Cables (Cu PVC 70°C / XLPE 90°C) grouped by (size_mm2, is_3p)
    # ------------------------------------------------------------------
    cable_m: dict[tuple[float, bool], float] = defaultdict(float)
    conduit_20_m = 0.0
    conduit_25_m = 0.0
    conduit_32_m = 0.0
    cable_tray_m = 0.0

    for circ in design.circuits:
        s = circ.spec
        key = (circ.cable_mm2, s.is_three_phase)
        cable_m[key] += s.length_m * rates.wastage_factor
        if circ.recommended_containment == "20mm PVC":
            conduit_20_m += s.length_m
        elif circ.recommended_containment == "25mm PVC":
            conduit_25_m += s.length_m
        elif circ.recommended_containment == "32mm PVC":
            conduit_32_m += s.length_m
        else:
            cable_tray_m += s.length_m

    for sm in design.sub_mains:
        key = (sm.cable_mm2, sm.is_three_phase)
        cable_m[key] += sm.length_m * rates.wastage_factor
        if sm.cable_mm2 >= 25.0 or (sm.is_three_phase and sm.cable_mm2 >= 16.0):
            cable_tray_m += sm.length_m
        else:
            conduit_25_m += sm.length_m

    for (size_mm2, is_3p), length_m in sorted(cable_m.items()):
        base_rate = rates.get_cable_rate(size_mm2)
        unit_rate = (
            round(base_rate * rates.three_phase_cable_multiplier, 2) if is_3p else base_rate
        )
        cores = "5-core (3P+N+PE)" if is_3p else "3-core (L+N+PE)"
        cores_el = "5-κλωνο (3Φ+N+PE)" if is_3p else "3-κλωνο (L+N+PE)"
        insul_en = "Cu XLPE/SWA Cable" if size_mm2 >= 50.0 else "Cu PVC Cable"
        insul_el = "Καλώδιο Χαλκού XLPE/SWA" if size_mm2 >= 50.0 else "Καλώδιο Χαλκού"
        _add(
            "Cables",
            f"{insul_en} {size_mm2:g} mm² {cores}, incl. 10% termination wastage",
            f"{insul_el} {size_mm2:g} mm² {cores_el} (+10% φύρα)",
            "m",
            length_m,
            unit_rate,
        )

    # ------------------------------------------------------------------
    # 2. PVC Conduits & Galvanized Cable Tray (BS 7671 45% fill sizing)
    # ------------------------------------------------------------------
    _add(
        "Conduits",
        "Heavy-duty 20 mm PVC Conduit in masonry/slab (Method B), incl. bends & boxes",
        "Σωλήνας PVC Φ20 βαρέως τύπου εντοιχισμένος (Μέθοδος B)",
        "m",
        conduit_20_m,
        rates.conduit_eur_per_m["20mm"],
    )
    _add(
        "Conduits",
        "Heavy-duty 25 mm PVC Conduit for power/sub-mains (Method B)",
        "Σωλήνας PVC Φ25 βαρέως τύπου για ισχύ/υποπίνακες (Μέθοδος B)",
        "m",
        conduit_25_m,
        rates.conduit_eur_per_m["25mm"],
    )
    _add(
        "Conduits",
        "Heavy-duty 32 mm PVC Conduit for grouped/heavy power circuits (Method B)",
        "Σωλήνας PVC Φ32 βαρέως τύπου για ομαδοποιημένα/ισχυρά κυκλώματα",
        "m",
        conduit_32_m,
        rates.conduit_eur_per_m.get("32mm", 5.40),
    )
    _add(
        "Conduits",
        "Perforated Galvanized Steel Cable Tray (150–300 mm) for heavy feeders/sub-mains",
        "Διάτρητη Γαλβανισμένη Σχάρα Καλωδίων (150–300 mm) για κύριες παροχές",
        "m",
        cable_tray_m,
        rates.conduit_eur_per_m.get("cable_tray", 24.00),
    )

    # ------------------------------------------------------------------
    # 3. Distribution Boards & Switchgear
    # ------------------------------------------------------------------
    sw = rates.switchgear_eur
    is_3p_supply = eac.supply_phases == 3
    _add(
        "Switchgear",
        f"Main Distribution Board ({design.building.db_anchor.id}) — "
        f"{'3-Phase' if is_3p_supply else '1-Phase'} flush-mounted IP40 enclosure",
        f"Κύριος Πίνακας Διανομής ({design.building.db_anchor.id}) — "
        f"{'Τριφασικός' if is_3p_supply else 'Μονοφασικός'} εντοιχισμένος IP40",
        "no.",
        1,
        sw["main_board_3p"] if is_3p_supply else sw["main_board_1p"],
    )
    if design.sub_mains:
        _add(
            "Switchgear",
            "Floor Sub-Distribution Board Enclosure (flush-mounted 3-Phase IP40)",
            "Υποπίνακας Ορόφου (εντοιχισμένος τριφασικός IP40)",
            "no.",
            len(design.sub_mains),
            sw["sub_board_3p"],
        )

    _add(
        "Switchgear",
        f"Main Switch-Disconnector {eac.recommended_main_breaker_a} A "
        f"({'4P' if is_3p_supply else '2P'})",
        f"Γενικός Διακόπτης Απομόνωσης {eac.recommended_main_breaker_a} A "
        f"({'4P' if is_3p_supply else '2P'})",
        "no.",
        1,
        sw["main_isolator_4p"] if is_3p_supply else sw["main_isolator_2p"],
    )
    _add(
        "Switchgear",
        f"Surge Protection Device (SPD Type 2, {'3P+N' if is_3p_supply else '1P+N'}) "
        "per BS 7671 Section 443",
        f"Απαγωγός Υπερτάσεων (SPD Type 2, {'3P+N' if is_3p_supply else '1P+N'})",
        "no.",
        1,
        sw["spd_type2_3p"] if is_3p_supply else sw["spd_type2_1p"],
    )

    if eac.ct_metering_required:
        _add(
            "Switchgear",
            "EAC CT Metering Enclosure & Current Transformer Set (>100 A/phase)",
            "Κιβώτιο Μετρητή ΑΗΚ με Μετασχηματιστές Έντασης CT (>100 A/φάση)",
            "no.",
            1,
            sw["ct_metering_enclosure"],
        )

    # Final circuit protective devices
    rcbo_1p = 0
    rcbo_3p = 0
    rcbo_ev = 0
    mcb_1p = 0
    mcb_3p = 0
    mccb_100 = 0
    mccb_250 = 0
    mccb_400 = 0
    for circ in design.circuits:
        s = circ.spec
        if circ.breaker_in_a > 63:
            if circ.breaker_in_a <= 125:
                mccb_100 += 1
            elif circ.breaker_in_a <= 250:
                mccb_250 += 1
            else:
                mccb_400 += 1
        elif s.category == CircuitCategory.EV_CHARGER:
            rcbo_ev += 1
        elif s.rcd_protected:
            if s.is_three_phase:
                rcbo_3p += 1
            else:
                rcbo_1p += 1
        else:
            if s.is_three_phase:
                mcb_3p += 1
            else:
                mcb_1p += 1

    for sm in design.sub_mains:
        if sm.breaker_in_a > 63:
            if sm.breaker_in_a <= 125:
                mccb_100 += 1
            elif sm.breaker_in_a <= 250:
                mccb_250 += 1
            else:
                mccb_400 += 1

    _add(
        "Switchgear",
        "RCBO 1P+N 30 mA Type A (BS EN 61009, 6 kA) final circuit breaker",
        "Αυτόματος Διακόπτης Διαρροής RCBO 1P+N 30 mA Type A (6 kA)",
        "no.",
        rcbo_1p,
        sw["rcbo_30ma_1p"],
    )
    _add(
        "Switchgear",
        "RCBO 3P+N 30 mA Type A (BS EN 61009, 10 kA) 3-phase breaker",
        "Τριφασικός RCBO 3P+N 30 mA Type A (10 kA)",
        "no.",
        rcbo_3p,
        sw["rcbo_30ma_3p"],
    )
    _add(
        "Switchgear",
        "EV Charger RCBO Type A + 6 mA DC RDC-DD (BS 7671 Section 722)",
        "Διακόπτης Φορτιστή EV RCBO Type A + 6 mA DC (Section 722)",
        "no.",
        rcbo_ev,
        sw["rcbo_ev_type_a_dc"],
    )
    _add(
        "Switchgear",
        "MCB 1P (BS EN 60898, 6 kA)",
        "Μικροαυτόματος Διακόπτης MCB 1P (6 kA)",
        "no.",
        mcb_1p,
        sw["mcb_1p"],
    )
    _add(
        "Switchgear",
        "MCB 3P (BS EN 60898, 10 kA)",
        "Τριφασικός Μικροαυτόματος MCB 3P (10 kA)",
        "no.",
        mcb_3p,
        sw["mcb_3p"],
    )
    _add(
        "Switchgear",
        "MCCB 3P/4P 80–125 A (BS EN 60947-2, 25–36 kA)",
        "Διακόπτης Ισχύος MCCB 3P/4P 80–125 A (25–36 kA)",
        "no.",
        mccb_100,
        sw.get("mccb_3p_100a", 245.0),
    )
    _add(
        "Switchgear",
        "MCCB 3P/4P 160–250 A (BS EN 60947-2, 36 kA)",
        "Διακόπτης Ισχύος MCCB 3P/4P 160–250 A (36 kA)",
        "no.",
        mccb_250,
        sw.get("mccb_3p_250a", 460.0),
    )
    _add(
        "Switchgear",
        "MCCB 3P/4P 315–400 A (BS EN 60947-2, 50 kA)",
        "Διακόπτης Ισχύος MCCB 3P/4P 315–400 A (50 kA)",
        "no.",
        mccb_400,
        sw.get("mccb_3p_400a", 780.0),
    )

    type_s_4p = sum(1 for sm in design.sub_mains if sm.rcd_selective_type_s and sm.is_three_phase)
    type_s_2p = sum(
        1 for sm in design.sub_mains if sm.rcd_selective_type_s and not sm.is_three_phase
    )
    _add(
        "Switchgear",
        "Time-Delayed Selective RCD 4P 100 mA Type S (BS 7671 Reg 536.4.1.4)",
        "Επιλεκτικός ΔΔΕ Χρονοκαθυστέρησης 4P 100 mA Type S",
        "no.",
        type_s_4p,
        sw["rcd_type_s_100ma_4p"],
    )
    _add(
        "Switchgear",
        "Time-Delayed Selective RCD 2P 100 mA Type S (BS 7671 Reg 536.4.1.4)",
        "Επιλεκτικός ΔΔΕ Χρονοκαθυστέρησης 2P 100 mA Type S",
        "no.",
        type_s_2p,
        sw["rcd_type_s_100ma_2p"],
    )

    # ------------------------------------------------------------------
    # 4. Wiring Accessories & Final Points (exact placed devices or fallback)
    # ------------------------------------------------------------------
    acc = rates.accessories_eur
    if spatial is not None and spatial.devices:
        dev_counts = Counter(d.device_type for d in spatial.devices)
        n_lum = dev_counts[DeviceType.LUMINAIRE]
        n_sw = dev_counts[DeviceType.SWITCH]
        n_sock = dev_counts[DeviceType.SOCKET_TWIN]
        n_ac = dev_counts[DeviceType.SPLIT_AC]
        n_appl = (
            dev_counts[DeviceType.COOKER_OUTLET]
            + dev_counts[DeviceType.OVEN_OUTLET]
            + dev_counts[DeviceType.APPLIANCE_OUTLET]
            + dev_counts[DeviceType.WATER_HEATER_ISOLATOR]
        )
    else:
        n_lum = max(2, len(design.building.rooms) * 2)
        n_sw = len(design.building.rooms)
        n_sock = sum(
            standards.room_templates.rooms[r.normalized_type].socket_count_default
            for r in design.building.rooms
        )
        n_ac = sum(1 for c in design.circuits if c.spec.category == CircuitCategory.SPLIT_AC)
        n_appl = sum(
            1
            for c in design.circuits
            if c.spec.category
            in (
                CircuitCategory.COOKER,
                CircuitCategory.OVEN,
                CircuitCategory.APPLIANCE,
                CircuitCategory.WATER_HEATER,
            )
        )

    _add(
        "Accessories",
        "Ceiling Luminaire Point (pendant / LED rose & wiring)",
        "Φωτιστικό Σημείο Οροφής",
        "no.",
        n_lum,
        acc["luminaire_point"],
    )
    _add(
        "Accessories",
        "10 A Lighting Switch (flush-mounted BS EN 60669)",
        "Διακόπτης Φωτισμού 10 A (εντοιχισμένος)",
        "no.",
        n_sw,
        acc["light_switch"],
    )
    _add(
        "Accessories",
        "Twin 13 A Switched Socket-Outlet (BS 1363 — Cyprus standard)",
        "Διπλή Πρίζα 13 A με Διακόπτη (BS 1363 — Πρότυπο Κύπρου)",
        "no.",
        n_sock,
        acc["twin_13a_socket"],
    )
    _add(
        "Accessories",
        "20 A Double-Pole A/C Isolator Switch with Neon Indicator",
        "Διπολικός Διακόπτης Κλιματιστικού 20 A με Ενδεικτικό",
        "no.",
        n_ac,
        acc["ac_isolator_20a"],
    )
    _add(
        "Accessories",
        "45 A Cooker Control Unit / 20 A Dedicated Appliance Connection Unit",
        "Διακόπτης Κουζίνας 45 A / Σημείο Αφιερωμένης Συσκευής 20 A",
        "no.",
        n_appl,
        acc["appliance_outlet_45a"],
    )

    n_emg_circ = sum(
        1 for c in design.circuits if c.spec.category == CircuitCategory.EMERGENCY_LIGHTING
    )
    if n_emg_circ > 0:
        n_emg_pts = max(4, len(design.building.rooms))
        _add(
            "Accessories",
            "BS 5266 Self-Contained 3h LED Emergency Exit / Bulkhead Luminaire",
            "Φωτιστικό Ασφαλείας LED 3 ωρών (BS 5266 / EN 1838)",
            "no.",
            n_emg_pts,
            acc.get("emergency_luminaire", 115.0),
        )

    n_fa_circ = sum(1 for c in design.circuits if c.spec.category == CircuitCategory.FIRE_ALARM)
    if n_fa_circ > 0:
        _add(
            "Accessories",
            "BS 5839 Fire Alarm Panel Lockable Red Fused Connection Unit (3 A)",
            "Ασφαλειοδιακόπτης Πυρανίχνευσης (BS 5839, κλειδωμένος, κόκκινος)",
            "no.",
            n_fa_circ,
            acc.get("fire_alarm_spur", 95.0),
        )

    # ------------------------------------------------------------------
    # 5. Earthing & EV Specialist Equipment
    # ------------------------------------------------------------------
    if design.earthing_system == EarthingSystem.TT:
        _add(
            "Earthing",
            "TT Earth Electrode Kit (copper-clad rod, inspection pit, 16 mm² earthing conductor)",
            "Σύστημα Γείωσης TT (ράβδος χαλκού, φρεάτιο επιθεώρησης, αγωγός 16 mm²)",
            "set",
            1,
            acc["tt_earth_rod_kit"],
        )

    if design.building.ev_charger_kw > 0.0:
        is_22kw = design.building.ev_charger_kw > 7.4
        _add(
            "Specialist",
            f"Mode 3 Wallbox EV Charger ({design.building.ev_charger_kw:g} kW) supply & install",
            (
                "Επιτοίχιος Φορτιστής Ηλεκτρικού Οχήματος Mode 3 "
                f"({design.building.ev_charger_kw:g} kW)"
            ),
            "no.",
            1,
            acc["ev_charger_22kw"] if is_22kw else acc["ev_charger_7kw"],
        )

    subtotal = 0.0 if tender_blank_rates else round(sum(item.total_eur for item in items), 2)
    vat_eur = 0.0 if tender_blank_rates else round(subtotal * rates.vat_rate, 2)
    grand_total = 0.0 if tender_blank_rates else round(subtotal + vat_eur, 2)

    rates_ver = rates.standards_version
    if tender_blank_rates:
        rates_ver = f"{rates_ver} (Tender Unpriced)"
    elif custom_applied:
        rates_ver = f"{rates_ver} (+tenant custom rates)"

    return BoqSummary(
        currency=rates.currency,
        vat_rate=rates.vat_rate,
        rates_version=rates_ver,
        is_tender_blank_rates=tender_blank_rates,
        items=items,
        subtotal_eur=subtotal,
        vat_eur=vat_eur,
        grand_total_eur=grand_total,
    )
