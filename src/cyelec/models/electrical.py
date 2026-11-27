"""Electrical engineering, BS 7671, EAC compliance, override, and provenance models."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cyelec.models.building import BuildingModel, Point2D, RoomType


class ReviewSeverity(StrEnum):
    """Severity level for the statutory ETEK Electrical Engineer review flag register."""

    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


class ReviewFlag(BaseModel):
    """Auditable flag gating statutory ETEK Electrical Engineer review/sign-off."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: ReviewSeverity
    category: Literal["bs7671", "eac", "balancing", "geometry", "governance"]
    message: str
    circuit_id: str | None = None
    regulation_ref: str | None = None
    requires_etek_signoff: bool = True


class EarthingSystem(StrEnum):
    """Supported earthing arrangements (TT is the Cyprus EAC default)."""

    TT = "TT"
    TN_S = "TN-S"
    TN_C_S = "TN-C-S"


class EacProfile(StrEnum):
    """Dual EAC / BS 7671 edition profiles supported in Cyprus."""

    EAC_17TH_A3 = "cyprus_eac_17th_a3"
    EAC_18TH_A2 = "cyprus_eac_18th_a2"


class CircuitCategory(StrEnum):
    """Functional category of a final circuit."""

    LIGHTING = "lighting"
    EMERGENCY_LIGHTING = "emergency_lighting"
    FIRE_ALARM = "fire_alarm"
    SOCKET_RING = "socket_ring"
    SOCKET_RADIAL = "socket_radial"
    SPLIT_AC = "split_ac"
    HVAC_3P = "hvac_3p"
    COOKER = "cooker"
    OVEN = "oven"
    APPLIANCE = "appliance"
    WATER_HEATER = "water_heater"
    EV_CHARGER = "ev_charger"
    PV = "pv"
    UPS_SERVER = "ups_server"
    LIFT = "lift"
    SHUTTERS = "shutters"
    HEAT_PUMP = "heat_pump"
    WATER_PUMP = "water_pump"
    GATE_GARAGE = "gate_garage"
    POOL = "pool"
    ELV_POWER = "elv_power"
    KNX_BUS = "knx_bus"
    SMOKE_DETECTION = "smoke_detection"
    OTHER = "other"


class CircuitTopology(StrEnum):
    """Wiring topology determining current-carrying and voltage drop formulas."""

    RADIAL = "radial"
    RING_FINAL = "ring_final"


class BreakerCurve(StrEnum):
    """BS EN 60898/61009 (B/C/D), BS EN 60947-2 (MCCB), and BS 88-2 gG HRC fuse characteristics."""

    B = "B"
    C = "C"
    D = "D"
    MCCB = "MCCB"
    BS88 = "BS88"


class PhaseAssignment(StrEnum):
    """Phase assignment on a 3-phase (L1/L2/L3/3P) or 1-phase (L1) distribution board."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    THREE_PHASE = "3P"


class CircuitOverride(BaseModel):
    """Per-circuit engineer override for Pass-2 SaaS reconciliation."""

    model_config = ConfigDict(extra="forbid")

    circuit_id: str
    phase: PhaseAssignment | None = None
    breaker_in_a: int | None = Field(default=None, gt=0)
    breaker_curve: BreakerCurve | None = None
    cable_mm2: float | None = Field(default=None, gt=0.0)
    cpc_mm2: float | None = Field(default=None, gt=0.0)
    length_m: float | None = Field(default=None, gt=0.0)
    grouping_count: int | None = Field(default=None, ge=1)
    ambient_temp_c: int | None = Field(default=None, ge=10, le=70)
    rcd_protected: bool | None = None
    rcd_idn_ma: int | None = Field(default=None, gt=0)
    diversity_factor: float | None = Field(default=None, gt=0.0, le=1.0)


class ReportLanguage(StrEnum):
    """Deliverable report & titleblock language mode for Cyprus EAC submissions."""

    EN = "en"
    EL = "el"
    BILINGUAL = "bilingual"


class TenantBranding(BaseModel):
    """Consulting firm & licensed ETEK engineer branding for branded deliverables."""

    model_config = ConfigDict(extra="forbid")

    firm_name: str = ""
    engineer_name: str = ""
    etek_reg_no: str = ""
    phone: str = ""
    email: str = ""
    logo_data_uri: str | None = None


class BoqRateOverrides(BaseModel):
    """Per-tenant or per-project custom EUR unit rate overrides for the Cyprus BOQ."""

    model_config = ConfigDict(extra="forbid")

    cable_eur_per_m: dict[str, float] = Field(default_factory=dict)
    conduit_eur_per_m: dict[str, float] = Field(default_factory=dict)
    switchgear_eur: dict[str, float] = Field(default_factory=dict)
    accessories_eur: dict[str, float] = Field(default_factory=dict)
    vat_rate: float | None = Field(default=None, ge=0.0, le=0.5)


class BoqItem(BaseModel):
    """Single itemized Bill of Quantities line item in EUR."""

    model_config = ConfigDict(extra="forbid")

    item_no: str
    category: str
    description_en: str
    description_el: str
    unit: str
    quantity: float = Field(ge=0.0)
    unit_rate_eur: float = Field(ge=0.0)
    total_eur: float = Field(ge=0.0)


class BoqSummary(BaseModel):
    """Deterministic Cyprus electrical Bill of Quantities with 19% VAT."""

    model_config = ConfigDict(extra="forbid")

    currency: str = "EUR"
    vat_rate: float = 0.19
    rates_version: str
    is_tender_blank_rates: bool = False
    items: list[BoqItem] = Field(default_factory=list)
    subtotal_eur: float = Field(ge=0.0)
    vat_eur: float = Field(ge=0.0)
    grand_total_eur: float = Field(ge=0.0)


class EacProjectMetadata(BaseModel):
    """Cyprus EAC (ΑΗΚ) cadastral, tariff, contractor & HMY 58.18-1 commissioning metadata."""

    model_config = ConfigDict(extra="forbid")

    district: str = ""
    municipality: str = ""
    sheet_plan: str = ""
    plot_no: str = ""
    building_permit_no: str = ""
    owner_name: str = ""
    eac_premises_no: str = ""
    eac_tariff: str = ""
    contractor_name: str = ""
    contractor_hmy_reg: str = ""
    supply_connection_type: Literal["underground", "overhead"] = "underground"
    inspection_date: str = ""
    # Optional site commissioning test values for Η.Μ.Υ. 58.18-1 pre-fill
    measured_ze_ohm: float | None = Field(default=None, ge=0.0, le=2000.0)
    measured_ra_ohm: float | None = Field(default=None, ge=0.0, le=2000.0)
    measured_main_bonding_ohm: float | None = Field(default=None, ge=0.0, le=50.0)
    measured_supp_bonding_ohm: float | None = Field(default=None, ge=0.0, le=50.0)
    measured_ir_mohm: float | None = Field(default=None, ge=0.0, le=10000.0)
    measured_rcd_1x_ms: float | None = Field(default=None, ge=0.0, le=2000.0)
    measured_rcd_5x_ms: float | None = Field(default=None, ge=0.0, le=1000.0)


class ResidentialSystemsBrief(BaseModel):
    """60-Second Cyprus Residential Engineering Brief driving turnkey LV/ELV/Plant/Site pack."""

    model_config = ConfigDict(extra="forbid")

    hvac_mode: Literal["split_ac", "vrv", "heat_pump_ufh"] = "split_ac"
    heat_pump_kw: float = Field(default=4.5, gt=0.0, le=30.0)
    vrv_outdoor_kw: float = Field(default=5.5, gt=0.0, le=40.0)
    include_electric_shutters: bool = True
    include_kitchen_fcus: bool = True
    include_water_pumps: bool = True
    include_gate_motor: bool = True
    include_elv: bool = True
    include_smoke_detectors: bool = True
    include_pool: bool = False
    include_site_works: bool = True
    include_decorative_wall_sconces: bool = False
    earthing_electrode_type: Literal["rod_pit", "foundation_ring"] = "foundation_ring"
    building_height_m: float = Field(default=7.5, gt=2.0, le=50.0)
    lightning_flash_density_ng: float = Field(default=1.5, gt=0.0, le=20.0)
    include_knx: bool = False
    ev_charger_kw: float | None = Field(default=None, ge=0.0, le=22.0)
    pv_module_wp: int = Field(default=550, ge=300, le=750)
    battery_storage_kwh: float = Field(default=0.0, ge=0.0, le=200.0)
    include_eps_backup: bool = False
    plot_length_m: float = Field(default=24.0, gt=5.0, le=200.0)
    plot_width_m: float = Field(default=18.0, gt=5.0, le=200.0)
    street_setback_m: float = Field(default=6.0, gt=1.0, le=60.0)
    conduit_drafting_style: Literal["homerun_arcs", "full_routed"] = "homerun_arcs"


class PvStorageEngineeringResult(BaseModel):
    """DC PV stringing & Battery Storage (BESS) sizing per BS 7671 Sec. 712 & EAC 2026."""

    model_config = ConfigDict(extra="forbid")

    inverter_ac_kva: float = Field(gt=0.0)
    inverter_phases: Literal[1, 3]
    inverter_max_dc_v: int
    mppt_min_v: int
    mppt_max_v: int
    module_wp: int
    module_count: int = Field(ge=1)
    array_dc_kwp: float = Field(gt=0.0)
    dc_ac_ratio: float = Field(gt=0.0)
    strings_count: int = Field(ge=1)
    modules_per_string: int = Field(ge=1)
    string_voc_stc_v: float
    string_voc_cold_0c_v: float
    string_vmp_stc_v: float
    string_vmp_hot_70c_v: float
    string_isc_max_a: float
    dc_cable_mm2: float
    dc_cable_spec: str
    dc_run_length_m: float
    dc_voltage_drop_pct: float
    dc_isolator_spec: str
    dc_spd_spec: str
    voc_cold_pass: bool
    mppt_window_pass: bool
    dc_vd_pass: bool
    # Optional Battery Energy Storage System (BESS) per March 2026 EAC DRG. No PVS-S-1.2
    battery_enabled: bool = False
    battery_nominal_kwh: float = 0.0
    battery_usable_kwh: float = 0.0
    battery_chemistry: str = "LiFePO4 (LFP)"
    battery_bus_voltage_v: int = 0
    battery_max_power_kw: float = 0.0
    battery_max_current_a: float = 0.0
    battery_dc_breaker_spec: str = ""
    battery_dc_cable_spec: str = ""
    eps_backup_enabled: bool = False
    eps_changeover_spec: str = ""
    eac_2026_drg_ref: str = "EAC DRG. No PVS-S-1.2 (2026) · R1 Curtailment + R2 Zero-Export"
    standard_ref: str = "BS 7671 Sec. 712 / IEC 62548 / EAC Storage Guide 2026"


class RoomLumenCheck(BaseModel):
    """Point-by-point 2D grid + CIBSE / BS EN 12464-1 photometric verification record."""

    model_config = ConfigDict(extra="forbid")

    room_id: str
    room_label: str
    floor: str = "GF"
    area_m2: float = Field(gt=0.0)
    fixture_type: str
    luminaire_count: int = Field(ge=1)
    watts_per_fixture: float = Field(gt=0.0)
    lumens_per_fixture: int = Field(gt=0)
    target_lux: int = Field(gt=0)
    achieved_lux: float = Field(ge=0.0)
    min_lux: float | None = Field(default=None, ge=0.0)
    max_lux: float | None = Field(default=None, ge=0.0)
    uniformity_u0: float | None = Field(default=None, ge=0.0, le=1.0)
    u0_target: float = Field(default=0.40, ge=0.0, le=1.0)
    u0_compliant: bool = True
    grid_points_evaluated: int = Field(default=0, ge=0)
    utilization_factor: float = Field(gt=0.0, le=1.0)
    maintenance_factor: float = Field(gt=0.0, le=1.0)
    compliant: bool


class DinRailRow(BaseModel):
    """Single horizontal DIN rail inside a physical distribution board enclosure."""

    model_config = ConfigDict(extra="forbid")

    row_no: int = Field(ge=1)
    modules_used: int = Field(ge=0)
    modules_capacity: int = Field(ge=12)
    items: list[str] = Field(default_factory=list)


class DinRailBoardLayout(BaseModel):
    """Physical 18 mm DIN-rail module schedule, enclosure sizing & selectivity cascade."""

    model_config = ConfigDict(extra="forbid")

    board_id: str
    floor: str
    total_modules_used: int = Field(ge=0)
    spare_modules: int = Field(ge=0)
    total_modules_capacity: int = Field(ge=12)
    spare_pct: float = Field(ge=0.0)
    enclosure_spec: str
    rows: list[DinRailRow] = Field(default_factory=list)
    incoming_ipf_ka: float = Field(gt=0.0)
    min_device_icu_ka: float = Field(gt=0.0)
    selectivity_pass: bool = True
    selectivity_note: str = ""


class LightningRiskAssessment(BaseModel):
    """IEC 62305-2 simplified residential lightning risk assessment summary."""

    model_config = ConfigDict(extra="forbid")

    ng_flashes_km2_yr: float
    collection_area_ad_m2: float
    nd_events_per_yr: float
    tolerable_rt: float = 1e-5
    risk_r1: float
    lps_required: bool
    recommended_lps_class: str
    spd_requirement: str
    standard_ref: str = "IEC 62305-2 / CYS EN 62305-2"


class DesignOverrides(BaseModel):
    """Structured stateless engineer overrides accepted by every pipeline run."""

    model_config = ConfigDict(extra="forbid")

    eac_profile: EacProfile | None = None
    earthing_system: EarthingSystem | None = None
    ze_or_ra_ohm: float | None = Field(default=None, gt=0.0)
    ambient_temp_c: int | None = Field(default=None, ge=10, le=70)
    pv_capacity_kva: float | None = Field(default=None, ge=0.0)
    prospective_fault_current_ka: float | None = Field(default=None, gt=0.0)
    min_supply_headroom_pct: float = Field(default=0.0, ge=0.0, le=50.0)
    min_main_breaker_a: int | None = Field(default=None, ge=30, le=400)
    circuit_overrides: dict[str, CircuitOverride] = Field(default_factory=dict)
    room_type_overrides: dict[str, RoomType] = Field(default_factory=dict)
    db_location_override_mm: Point2D | None = None
    meter_location_override_mm: Point2D | None = None
    earth_pit_location_override_mm: Point2D | None = None
    telecom_location_override_mm: Point2D | None = None
    residential_brief: ResidentialSystemsBrief | None = None
    report_language: ReportLanguage = ReportLanguage.EN
    branding: TenantBranding | None = None
    boq_rate_overrides: BoqRateOverrides | None = None
    tender_boq_blank_rates: bool = False
    apply_corridor_grouping: bool = False
    include_tt_main_rcd_s: bool = True
    eac_metadata: EacProjectMetadata | None = None
    notes: str | None = None


class CircuitSpec(BaseModel):
    """Pre-solve final circuit specification generated by the deterministic grouper."""

    model_config = ConfigDict(extra="forbid")

    circuit_id: str
    board_id: str = "DB1"
    description: str
    category: CircuitCategory
    topology: CircuitTopology
    room_ids: list[str] = Field(default_factory=list)
    connected_load_w: float = Field(gt=0.0)
    diversity_factor: float = Field(gt=0.0, le=1.0)
    diversified_load_w: float = Field(gt=0.0)
    power_factor: float = Field(default=0.95, gt=0.0, le=1.0)
    design_current_ib_a: float = Field(gt=0.0)
    nominal_voltage_v: float = Field(default=230.0, gt=0.0)
    is_three_phase: bool = False
    length_m: float = Field(gt=0.0)
    electrical_path_length_m: float | None = Field(default=None, gt=0.0)
    distributed_vd_factor: float | None = Field(default=None, gt=0.0, le=1.0)
    worst_case_node_id: str | None = None
    installation_method: Literal["B", "C"] = "B"
    insulation_type: Literal["pvc_70c", "xlpe_90c"] = "pvc_70c"
    ambient_temp_c: int = Field(default=40, ge=10, le=70)
    grouping_count: int = Field(default=1, ge=1)
    thermal_insulation_mm: int = Field(default=0, ge=0)
    preferred_curve: BreakerCurve = BreakerCurve.B
    min_breaker_in_a: int | None = Field(default=None, gt=0)
    min_cable_mm2: float = Field(default=1.5, gt=0.0)
    rcd_protected: bool = True
    rcd_idn_ma: int | None = Field(default=30, gt=0)
    vd_limit_pct: float = Field(default=5.0, gt=0.0)
    fixed_phase: PhaseAssignment | None = None


class SolvedCircuit(BaseModel):
    """Fully verified BS 7671 & IEC 60909 final circuit calculation record."""

    model_config = ConfigDict(extra="forbid")

    spec: CircuitSpec
    phase: PhaseAssignment
    breaker_in_a: int
    breaker_curve: BreakerCurve
    device_label: str
    vendor_catalog_ref: str = ""
    cable_mm2: float
    cpc_mm2: float
    electrical_path_length_m: float | None = None
    worst_case_node_id: str | None = None
    ca: float
    cg: float
    ci: float
    effective_derating: float
    it_tabulated_a: float
    iz_derated_a: float
    required_iz_a: float
    thermal_pass: bool
    mv_per_a_per_m: float
    voltage_drop_v: float
    voltage_drop_pct: float
    voltage_drop_pass: bool
    r1_plus_r2_ohm: float
    zs_ohm: float
    zs_max_ohm: float
    earth_fault_pass: bool
    earth_fault_method: Literal["TT_RCD", "TN_ZS"]
    tt_touch_voltage_v: float | None = None
    adiabatic_i2t_a2s: float
    cpc_min_mm2: float
    cpc_adiabatic_pass: bool
    live_adiabatic_pass: bool = True
    ik1_min_a: float | None = None
    ipe_min_a: float | None = None
    magnetic_trip_max_a: float | None = None
    instantaneous_trip_pass: bool = True
    recommended_containment: str = "20mm PVC"
    overall_pass: bool
    upsize_reasons: list[str] = Field(default_factory=list)
    flags: list[ReviewFlag] = Field(default_factory=list)


class BoardFaultAndSelectivity(BaseModel):
    """IEC 60909 short-circuit fault profile & upstream-downstream selectivity per board."""

    model_config = ConfigDict(extra="forbid")

    board_id: str
    parent_board_id: str | None = None
    upstream_device_label: str
    downstream_max_device_label: str
    ik3_max_ka: float = Field(gt=0.0)
    ik1_min_ka: float = Field(gt=0.0)
    device_icu_ka: float = Field(gt=0.0)
    breaking_capacity_pass: bool
    discrimination_limit_is_ka: float = Field(ge=0.0)
    selectivity_type: Literal["total", "partial", "check_required"]
    rcd_selectivity_pass: bool = True
    vendor_coordination_ref: str = ""
    note: str = ""


class SelectivityAnalysisResult(BaseModel):
    """Installation-wide IEC 60909 fault profile and time-current (I-t) selectivity summary."""

    model_config = ConfigDict(extra="forbid")

    origin_ik3_max_ka: float = Field(gt=0.0)
    origin_ik1_min_ka: float = Field(gt=0.0)
    eac_main_fuse_a: int = Field(gt=0)
    boards: list[BoardFaultAndSelectivity] = Field(default_factory=list)
    all_breaking_capacity_pass: bool = True
    all_instantaneous_trip_pass: bool = True
    all_selectivity_pass: bool = True
    standard_ref: str = "IEC 60909 / IEC 60898-1 / BS 7671 Reg 536.4"


class SolvedSubMain(BaseModel):
    """Verified BS 7671 sub-main feeder connecting MDB to a floor sub-distribution board."""

    model_config = ConfigDict(extra="forbid")

    board_id: str
    parent_board_id: str
    floor: str
    circuit_ids: list[str] = Field(default_factory=list)
    connected_kva: float
    diversified_kva: float
    design_current_ib_a: float
    is_three_phase: bool
    breaker_in_a: int
    breaker_curve: BreakerCurve
    rcd_selective_type_s: bool
    rcd_idn_ma: int | None = None
    device_label: str
    cable_mm2: float
    cpc_mm2: float
    length_m: float
    installation_method: Literal["B", "C"]
    ca: float
    cg: float
    ci: float
    it_tabulated_a: float
    iz_derated_a: float
    thermal_pass: bool
    voltage_drop_v: float
    voltage_drop_pct: float
    max_final_lighting_vd_pct: float = 0.0
    max_final_power_vd_pct: float = 0.0
    cumulative_lighting_vd_pct: float = 0.0
    cumulative_power_vd_pct: float = 0.0
    cumulative_vd_pass: bool = True
    r1_plus_r2_ohm: float
    zs_ohm: float
    zs_max_ohm: float
    earth_fault_pass: bool
    downstream_ipf_ka: float | None = None
    overall_pass: bool
    flags: list[ReviewFlag] = Field(default_factory=list)


class PhaseBalanceResult(BaseModel):
    """Exact MILP 3-phase load balancing result."""

    model_config = ConfigDict(extra="forbid")

    assignments: dict[str, PhaseAssignment]
    phase_currents_a: dict[Literal["L1", "L2", "L3"], float]
    phase_power_kw: dict[Literal["L1", "L2", "L3"], float]
    max_phase_current_a: float
    min_phase_current_a: float
    mean_phase_current_a: float
    imbalance_a: float
    imbalance_pct: float
    within_target: bool
    solver_status: str
    flags: list[ReviewFlag] = Field(default_factory=list)


class EacComplianceResult(BaseModel):
    """Electricity Authority of Cyprus (EAC) supply tier and compliance determination."""

    model_config = ConfigDict(extra="forbid")

    eac_profile: EacProfile
    supply_phases: Literal[1, 3]
    mandatory_three_phase: bool
    three_phase_reasons: list[str] = Field(default_factory=list)
    total_connected_kva: float
    total_diversified_kva: float
    max_phase_demand_a: float
    recommended_supply_tier: str
    recommended_main_breaker_a: int
    supply_tier_utilization_pct: float = 0.0
    next_supply_tier: str | None = None
    ct_metering_required: bool
    substation_warning: bool
    substation_consultation_required: bool
    bathroom_socket_exclusion_m: float
    flags: list[ReviewFlag] = Field(default_factory=list)


class RunProvenance(BaseModel):
    """Immutable run provenance for multi-tenant SaaS auditability."""

    model_config = ConfigDict(extra="forbid")

    pipeline_version: str
    standards_versions: dict[str, str]
    input_sha256: str
    eac_profile: EacProfile
    earthing_system: EarthingSystem
    timestamp_utc: str
    overrides_applied: bool


class ElectricalDesignResult(BaseModel):
    """Top-level stateless Phase 1 pipeline output artifact."""

    model_config = ConfigDict(extra="forbid")

    provenance: RunProvenance
    building: BuildingModel
    earthing_system: EarthingSystem
    ze_or_ra_ohm: float
    prospective_fault_current_ka: float = 6.0
    circuits: list[SolvedCircuit]
    sub_mains: list[SolvedSubMain] = Field(default_factory=list)
    phase_balance: PhaseBalanceResult | None = None
    eac_compliance: EacComplianceResult
    boq: BoqSummary | None = None
    report_language: ReportLanguage = ReportLanguage.EN
    branding: TenantBranding | None = None
    include_tt_main_rcd_s: bool = True
    eac_metadata: EacProjectMetadata | None = None
    residential_brief: ResidentialSystemsBrief = Field(default_factory=ResidentialSystemsBrief)
    lumen_checks: list[RoomLumenCheck] = Field(default_factory=list)
    din_rail_boards: list[DinRailBoardLayout] = Field(default_factory=list)
    lightning_risk: LightningRiskAssessment | None = None
    pv_storage_design: PvStorageEngineeringResult | None = None
    selectivity_analysis: SelectivityAnalysisResult | None = None
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    has_blockers: bool
    etek_signoff_required: bool = True
