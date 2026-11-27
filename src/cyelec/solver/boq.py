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
        unit_rate = round(base_rate * rates.three_phase_cable_multiplier, 2) if is_3p else base_rate
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
    needs_lps_spd = bool(
        design.lightning_risk is not None and design.lightning_risk.lps_required
    )
    if needs_lps_spd:
        _add(
            "Switchgear",
            f"Combined Surge Protection Device (SPD Type 1+2, 12.5 kA 10/350µs, "
            f"{'3P+N' if is_3p_supply else '1P+N'}) per IEC 62305 & BS 7671 Sec. 443",
            f"Συνδυασμένος Απαγωγός Υπερτάσεων (SPD Type 1+2, 12.5 kA, "
            f"{'3P+N' if is_3p_supply else '1P+N'})",
            "no.",
            1,
            round((sw["spd_type2_3p"] if is_3p_supply else sw["spd_type2_1p"]) * 2.2, 2),
        )
    else:
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
    if (
        design.earthing_system == EarthingSystem.TT
        and design.include_tt_main_rcd_s
        and not design.sub_mains
    ):
        if is_3p_supply:
            type_s_4p += 1
        else:
            type_s_2p += 1
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
        n_mirror = dev_counts[DeviceType.MIRROR_LIGHT]
        n_fan = dev_counts[DeviceType.EXHAUST_FAN]
        n_sw = dev_counts[DeviceType.SWITCH]
        n_sock = dev_counts[DeviceType.SOCKET_TWIN]
        n_ac = dev_counts[DeviceType.SPLIT_AC]
        n_ccu = dev_counts[DeviceType.COOKER_CONTROL_UNIT]
        n_wh_dp = dev_counts[DeviceType.WATER_HEATER_DP_SWITCH]
        n_appl = (
            dev_counts[DeviceType.COOKER_OUTLET]
            + dev_counts[DeviceType.OVEN_OUTLET]
            + dev_counts[DeviceType.APPLIANCE_OUTLET]
            + dev_counts[DeviceType.WATER_HEATER_ISOLATOR]
        )
    else:
        n_lum = max(2, len(design.building.rooms) * 2)
        n_wet = sum(1 for r in design.building.rooms if r.is_wet_room)
        n_mirror = n_wet
        n_fan = n_wet
        n_sw = len(design.building.rooms)
        n_sock = sum(
            standards.room_templates.rooms[r.normalized_type].socket_count_default
            for r in design.building.rooms
        )
        n_ac = sum(1 for c in design.circuits if c.spec.category == CircuitCategory.SPLIT_AC)
        n_ccu = sum(1 for c in design.circuits if c.spec.category == CircuitCategory.COOKER)
        n_wh_dp = sum(1 for c in design.circuits if c.spec.category == CircuitCategory.WATER_HEATER)
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
        "IP44 Bathroom Vanity Mirror Light Point (Zone 2 compliant, H=+2000 mm)",
        "Φωτιστικό Καθρέφτη Μπάνιου IP44 (Ζώνη 2, H=+2000 mm)",
        "no.",
        n_mirror,
        acc.get("mirror_light_ip44", 48.0),
    )
    _add(
        "Accessories",
        "Bathroom Exhaust Fan (IP44) + 3-Pole Fan Isolator Switch (H=+2200 mm)",
        "Ανεμιστήρας Μπάνιου IP44 + Τριπολικός Διακόπτης Απομόνωσης",
        "set",
        n_fan,
        acc.get("exhaust_fan_isolator_set", 78.0),
    )
    _add(
        "Accessories",
        "10 A Lighting Switch (1G1W / 2G1W / 1G2W flush-mounted BS EN 60669)",
        "Διακόπτης Φωτισμού 10 A (Απλός / Διπλός / Αλλέ-Ρετούρ)",
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
        "45 A Double-Pole Cooker Control Unit with Neon (H=+1150 mm)",
        "Διπολικός Διακόπτης Κουζίνας 45 A με Ενδεικτικό",
        "no.",
        n_ccu,
        acc.get("cooker_control_unit_45a", 46.0),
    )
    _add(
        "Accessories",
        "20 A Double-Pole Water Heater Switch with Neon (H=+1200 mm)",
        "Διπολικός Διακόπτης Θερμοσίφωνα 20 A με Ενδεικτικό",
        "no.",
        n_wh_dp,
        acc.get("water_heater_dp_switch_20a", 32.0),
    )
    _add(
        "Accessories",
        "Dedicated Fixed Appliance / Cooker Connection Outlet (20–45 A)",
        "Σημείο Σύνδεσης Αφιερωμένης Συσκευής / Κουζίνας (20–45 A)",
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
    # 5. Earthing, Main Equipotential Bonding, PV AC Kit & EV Equipment
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
    _add(
        "Earthing",
        "Main Protective Equipotential Bonding (10 mm² Cu to Water, Gas & MET Clamps)",
        "Κύρια Ισοδυναμική Σύνδεση (10 mm² Cu σε Ύδρευση, Αέριο & MET)",
        "set",
        1,
        acc.get("main_equipotential_bonding_10mm", 120.0),
    )

    if design.building.pv_capacity_kva > 0.0:
        _add(
            "Specialist",
            "PV AC Lockable Rotary Isolator, Generation Meter Enclosure & Labeling (EAC Net-Meter)",
            "Περιστροφικός Αποζεύκτης AC Φ/Β, Κιβώτιο Μετρητή Παραγωγής & Σήμανση ΑΗΚ",
            "set",
            1,
            acc.get("pv_ac_isolator_meter_kit", 240.0),
        )
        pvs = design.pv_storage_design
        if pvs is not None and pvs.battery_enabled:
            _add(
                "Specialist",
                (
                    f"Battery Energy Storage System {pvs.battery_nominal_kwh:g} kWh LiFePO4 "
                    f"({pvs.battery_bus_voltage_v}V DC) + {pvs.battery_dc_breaker_spec} & DC Cables"
                ),
                (
                    f"Σύστημα Αποθήκευσης Ενέργειας (BESS) {pvs.battery_nominal_kwh:g} kWh "
                    f"LiFePO4 ({pvs.battery_bus_voltage_v}V DC) με Προστασία DC"
                ),
                "kWh",
                pvs.battery_nominal_kwh,
                acc.get("bess_lfp_per_kwh", 420.0),
            )
            if pvs.eps_backup_enabled:
                _add(
                    "Specialist",
                    (
                        "EPS Automatic Transfer Switch (ATS) & Critical-Load "
                        "Backup Sub-Busbar (EAC PVS-S-1.2)"
                    ),
                    (
                        "Αυτόματος Μεταγωγέας Εφεδρικής Τροφοδοσίας (EPS / ATS) "
                        "& Υποζυγός Κρίσιμων Φορτίων"
                    ),
                    "no.",
                    1,
                    acc.get("eps_backup_ats_kit", 680.0),
                )

    effective_ev_kw = (
        design.residential_brief.ev_charger_kw
        if design.residential_brief.ev_charger_kw is not None
        else design.building.ev_charger_kw
    )
    if effective_ev_kw > 0.0:
        is_22kw = effective_ev_kw > 7.4
        _add(
            "Specialist",
            f"Mode 3 Wallbox EV Charger ({effective_ev_kw:g} kW) supply & install",
            (f"Επιτοίχιος Φορτιστής Ηλεκτρικού Οχήματος Mode 3 ({effective_ev_kw:g} kW)"),
            "no.",
            1,
            acc["ev_charger_22kw"] if is_22kw else acc["ev_charger_7kw"],
        )

    # ------------------------------------------------------------------
    # 6. Stage 5: Lighting Upgrades, Mechanical Plant, ELV, Site, KNX & Testing
    # ------------------------------------------------------------------
    brief = design.residential_brief
    if spatial is not None:
        dev_counts = Counter(d.device_type for d in spatial.devices)
        _add(
            "Lighting",
            "LED Rotary Dimmer Switch (Trailing-Edge 250W, H=+1200 mm)",
            "Ρυθμιστής Έντασης LED (Dimmer 250W, H=+1200 mm)",
            "no.",
            dev_counts[DeviceType.DIMMER_SWITCH],
            acc.get("dimmer_switch", 48.0),
        )
        _add(
            "Lighting",
            "Dining / Feature Pendant Luminaire Point (3000K)",
            "Φωτιστικό Κρεμαστό Τραπεζαρίας (3000K)",
            "no.",
            dev_counts[DeviceType.PENDANT_LIGHT],
            acc.get("pendant_light_point", 55.0),
        )
        _add(
            "Lighting",
            "Decorative LED Wall Sconce Point (8-10W, 3000K, H=+1600-1800 mm)",
            "Φωτιστικό Τοίχου LED (Απλίκα 8-10W, 3000K, H=+1600-1800 mm)",
            "no.",
            dev_counts[DeviceType.WALL_SCONCE],
            acc.get("wall_sconce_point", 46.0),
        )
        _add(
            "Lighting",
            "IP65 Outdoor LED PIR Security / Entry Floodlight (20W)",
            "Εξωτερικός Προβολέας LED IP65 με Αισθητήρα Κίνησης PIR",
            "no.",
            dev_counts[DeviceType.OUTDOOR_PIR_LIGHT],
            acc.get("outdoor_pir_light_ip65", 68.0),
        )
        # Mechanical / Shutters / Plant
        _add(
            "Mechanical & Plant",
            "Motorized Window Shutter Double-Pole Up/Down Switch & Conduit (H=+1200 mm)",
            "Διακόπτης Ηλεκτρικού Ρολού (Πάνω/Κάτω) με Σωλήνωση",
            "no.",
            dev_counts[DeviceType.SHUTTER_SWITCH],
            acc.get("shutter_switch_dp", 34.0),
        )
        _add(
            "Mechanical & Plant",
            "13 A Switched Fused Connection Unit (Kitchen Hood / Fridge / Microwave)",
            "Ασφαλειοδιακόπτης 13 A FCU (Απορροφητήρας / Ψυγείο / Φούρνος Μικροκυμάτων)",
            "no.",
            dev_counts[DeviceType.KITCHEN_FCU],
            acc.get("kitchen_fcu_13a", 32.0),
        )
        _add(
            "Mechanical & Plant",
            "Room Heating/Cooling Thermostat Point & Control Conduit (H=+1500 mm)",
            "Θερμοστάτης Χώρου (Ενδοδαπέδια / VRV) με Σωλήνωση Ελέγχου",
            "no.",
            dev_counts[DeviceType.ROOM_THERMOSTAT],
            acc.get("room_thermostat_point", 58.0),
        )
        if dev_counts[DeviceType.HEAT_PUMP_OUTDOOR] > 0:
            _add(
                "Mechanical & Plant",
                "3-Phase IP65 Rotary Isolator & Hydrobox/UFH Manifold Power Connection Kit",
                "Τριφασικός Αποζεύκτης IP65 Αντλίας Θερμότητας & Σύνδεση Ενδοδαπέδιας",
                "set",
                1,
                acc.get("heat_pump_3p_isolator_kit", 185.0),
            )
        if dev_counts[DeviceType.VRV_OUTDOOR] > 0:
            _add(
                "Mechanical & Plant",
                "3-Phase IP65 VRV/VRF Outdoor Isolator & Indoor FCU Isolation Kit",
                "Τριφασικός Αποζεύκτης IP65 Εξωτερικής Μονάδας VRV & Εσωτερικών Μονάδων",
                "set",
                1,
                acc.get("vrv_3p_isolator_kit", 195.0),
            )
        if dev_counts[DeviceType.WATER_BOOSTER_PUMP] > 0:
            _add(
                "Mechanical & Plant",
                "Water Pressure Booster & Hot-Water Recirculation Pump IP55 Isolator Kit",
                "Αποζεύκτης IP55 Πιεστικού Νερού & Κυκλοφορητή Ζεστού Νερού",
                "set",
                1,
                acc.get("water_pump_isolator_kit", 72.0),
            )
        if dev_counts[DeviceType.GATE_MOTOR] > 0:
            _add(
                "Mechanical & Plant",
                "Sliding Yard Gate / Garage Motor IP65 Isolator, Safety Photocells & Beacon Wiring",
                "Ηλεκτρική Παροχή Αυλόπορτας / Γκαραζόπορτας με Φωτοκύτταρα Ασφαλείας",
                "set",
                1,
                acc.get("gate_motor_supply_kit", 110.0),
            )
        if dev_counts[DeviceType.POOL_SUB_DB] > 0:
            _add(
                "Mechanical & Plant",
                "Swimming Pool IP65 Sub-DB, 30mA RCBOs & SELV 12V Transformer (BS 7671 Sec. 702)",
                "Υποπίνακας Πισίνας IP65, 30mA RCBO & Μετασχηματιστής SELV 12V (Sec. 702)",
                "set",
                1,
                acc.get("pool_sub_db_sec702_kit", 680.0),
            )
        # ELV / Weak-Current / Security / Life Safety
        if dev_counts[DeviceType.TELECOM_CABINET] > 0:
            _add(
                "ELV & Security",
                "9U Wall-Mounted Telecom Rack, 24-Port Cat6 Patch Panel & Cyta Fiber ONT Provision",
                (
                    "Επιτοίχιο Καμπινέ Τηλεπικοινωνιών 9U, Patch Panel 24-Port Cat6 "
                    "& Πρόνοια Οπτικής Ίνας"
                ),
                "set",
                1,
                acc.get("telecom_cabinet_9u_patch", 390.0),
            )
        _add(
            "ELV & Security",
            "Dual Cat6 UTP RJ45 Data Outlet incl. Cable Run to Telecom Rack (H=+450 mm)",
            "Διπλή Πρίζα Δεδομένων Cat6 RJ45 με Καλωδίωση στο Καμπινέ",
            "no.",
            dev_counts[DeviceType.DATA_RJ45],
            acc.get("cat6_dual_rj45_point", 68.0),
        )
        _add(
            "ELV & Security",
            "TV / Satellite RG6 Coaxial Outlet incl. Star Cable to Rack",
            "Πρίζα Τηλεόρασης / Δορυφορικής RG6 με Καλωδίωση",
            "no.",
            dev_counts[DeviceType.TV_OUTLET],
            acc.get("tv_coax_point", 42.0),
        )
        _add(
            "ELV & Security",
            "Ceiling PoE Wi-Fi 6 Access Point Cat6A Drop & Conduit",
            "Σημείο Οροφής PoE Wi-Fi 6 Access Point (Cat6A)",
            "no.",
            dev_counts[DeviceType.WIFI_AP],
            acc.get("wifi_poe_ap_drop", 55.0),
        )
        if dev_counts[DeviceType.INTERCOM_OUTDOOR] > 0:
            _add(
                "ELV & Security",
                "IP Video Intercom System (IP65 Outdoor Gate Unit, 1x Monitor & Electric Strike)",
                "Σύστημα Θυροτηλεόρασης IP (Εξωτερική Μονάδα IP65, 1x Οθόνη & Ηλεκτρική Κλειδαριά)",
                "set",
                1,
                acc.get("video_intercom_kit_outdoor_indoor", 440.0),
            )
            extra_mon = max(0, dev_counts[DeviceType.INTERCOM_INDOOR] - 1)
            _add(
                "ELV & Security",
                "Additional Floor Video Intercom Indoor Monitor (7-inch Touch)",
                "Επιπρόσθετη Εσωτερική Οθόνη Θυροτηλεόρασης Ορόφου",
                "no.",
                extra_mon,
                acc.get("video_intercom_extra_monitor", 165.0),
            )
        if dev_counts[DeviceType.ALARM_PANEL] > 0:
            _add(
                "ELV & Security",
                "EN 50131 Grade 2 Intruder Alarm Control Panel, Entry Keypad & External Siren",
                "Κεντρικός Πίνακας Συναγερμού Grade 2, Πληκτρολόγιο & Εξωτερική Σειρήνα",
                "set",
                1,
                acc.get("intruder_alarm_panel_keypad_siren", 520.0),
            )
        _add(
            "ELV & Security",
            "Intruder Alarm Dual-Tech PIR Motion Detector & Magnetic Contact Wiring",
            "Αισθητήρας Κίνησης Συναγερμού Dual-Tech PIR & Μαγνητική Επαφή",
            "no.",
            dev_counts[DeviceType.ALARM_PIR],
            acc.get("intruder_alarm_pir_point", 58.0),
        )
        _add(
            "ELV & Security",
            "IP67 4MP Exterior PoE CCTV Camera Point & Cat6 Drop to NVR (H=+2800 mm)",
            "Σημείο Εξωτερικής Κάμερας CCTV IP67 PoE με Καλωδίωση Cat6",
            "no.",
            dev_counts[DeviceType.CCTV_CAMERA],
            acc.get("cctv_poe_camera_point", 145.0),
        )
        n_smoke_heat = dev_counts[DeviceType.SMOKE_DETECTOR] + dev_counts[DeviceType.HEAT_DETECTOR]
        _add(
            "ELV & Security",
            "BS 5839-6 Grade D1 Interlinked Optical Smoke / Kitchen Heat Detector (230V+Bat)",
            "Διασυνδεδεμένος Ανιχνευτής Καπνού / Θερμότητας (BS 5839-6 Grade D1)",
            "no.",
            n_smoke_heat,
            acc.get("smoke_heat_detector_grade_d1", 52.0),
        )
        # External Site Works & Trenching
        if dev_counts[DeviceType.GARDEN_BOLLARD] > 0:
            _add(
                "Site Works",
                "IP65 Exterior LED Garden / Driveway Bollard & SWA Underground Connection",
                "Εξωτερικό Φωτιστικό Κήπου Bollard IP65 με Υπόγεια Παροχή",
                "no.",
                dev_counts[DeviceType.GARDEN_BOLLARD],
                acc.get("garden_bollard_ip65_point", 95.0),
            )
        if brief.include_site_works and design.building.building_type.value == "independent_house":
            trench_m = round(max(6.0, brief.street_setback_m + 3.0), 1)
            _add(
                "Site Works",
                "Underground EAC Supply Trench (2xØ110mm HDPE Ducts, Sand Bed & Warning Tape)",
                "Υπόγεια Εκσκαφή & Σωλήνες Παροχής ΑΗΚ (2xØ110mm HDPE με Ταινία Σήμανσης)",
                "m",
                trench_m,
                acc.get("underground_trench_eac_duct_per_m", 48.0),
            )
            _add(
                "Site Works",
                "Underground Telecom / Cyta Fiber Entry Ducts (2xØ32mm HDPE + Draw Pit)",
                "Υπόγειοι Σωλήνες Τηλεπικοινωνιών / Cyta (2xØ32mm HDPE & Φρεάτιο)",
                "m",
                trench_m,
                acc.get("underground_telecom_duct_per_m", 18.0),
            )
        # Foundation Earthing & IEC 62305-2 LPS
        if brief.earthing_electrode_type == "foundation_ring" and brief.include_site_works:
            _add(
                "Earthing",
                "Foundation / Ring Earth Electrode (30x3.5mm Cu/Galv Tape, Clamps & Test Links)",
                "Θεμελιακή / Δακτυλιοειδής Γείωση (Ταινία 30x3.5mm, Σφιγκτήρες & Σημεία Ελέγχου)",
                "set",
                1,
                acc.get("foundation_ring_earthing_kit", 420.0),
            )
        if design.lightning_risk is not None and design.lightning_risk.lps_required:
            _add(
                "Earthing",
                (
                    "IEC 62305 External Lightning Protection System "
                    "(Class IV Air Term, Down Conductors)"
                ),
                "Σύστημα Εξωτερικής Αντικεραυνικής Προστασίας (IEC 62305 Κατηγορία IV)",
                "set",
                1,
                acc.get("lps_class4_external_kit", 2450.0),
            )
        # Optional Smart Home KNX
        if brief.include_knx:
            _add(
                "KNX Automation",
                "KNX Bus Power Supply 640mA (DIN-Rail) & IP/DALI Interface Gateway",
                "Τροφοδοτικό KNX 640mA (Ράγας DIN) & Πύλη IP/DALI",
                "set",
                1,
                acc.get("knx_psu_640ma_din", 240.0),
            )
            _add(
                "KNX Automation",
                "KNX 8-Channel Switching / Dimming / Shutter DIN-Rail Actuator",
                "Ενεργοποιητής KNX 8 Καναλιών (Φωτισμός / Ρολά / Dimming)",
                "no.",
                2,
                acc.get("knx_actuator_8ch_din", 340.0),
            )
            _add(
                "KNX Automation",
                "KNX 4-Fold Tactile Scene / Temperature Keypad incl. EIB Bus Cable",
                "Πληκτρολόγιο Σκηνών KNX 4-Πλήκτρων με Καλώδιο EIB Bus",
                "no.",
                dev_counts[DeviceType.KNX_KEYPAD],
                acc.get("knx_scene_keypad", 145.0),
            )

    # Section 10: Testing, Commissioning, HMY 58 Certification & As-Built Drawings
    _add(
        "Testing & Commissioning",
        (
            "BS 7671 Initial Verification, Form HMY 58 Certification, "
            "EAC Inspection & As-Built Drawings"
        ),
        "Αρχικός Έλεγχος BS 7671, Πιστοποιητικό ΗΜΥ 58, Επιθεώρηση ΑΗΚ & Σχέδια As-Built",
        "item",
        1,
        acc.get("testing_commissioning_hmy58_asbuilt", 450.0),
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
