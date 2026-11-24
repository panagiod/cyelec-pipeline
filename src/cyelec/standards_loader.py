"""Typed YAML loader and validator for versioned BS 7671 & Cyprus EAC standards tables."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cyelec.models.building import BuildingType, RoomType
from cyelec.models.electrical import BreakerCurve, CircuitCategory, EacProfile, EarthingSystem


class CableConstants(BaseModel):
    """Physical and regulatory cable calculation constants."""

    model_config = ConfigDict(extra="forbid")

    reference_ambient_c: int
    temp_resistance_multiplier_70c: float
    adiabatic_k_pvc_copper: float
    ring_final_min_iz_a: float
    ring_final_min_cable_mm2: float


class CableEntry(BaseModel):
    """Tabulated Appendix 4 copper cable properties for a single cross-sectional area."""

    model_config = ConfigDict(extra="forbid")

    size_mm2: float = Field(gt=0.0)
    default_cpc_mm2: float = Field(gt=0.0)
    method_b_1p_a: float = Field(gt=0.0)
    method_b_3p_a: float = Field(gt=0.0)
    method_c_1p_a: float = Field(gt=0.0)
    method_c_3p_a: float = Field(gt=0.0)
    mv_per_a_per_m_1p: float = Field(gt=0.0)
    mv_per_a_per_m_3p: float = Field(gt=0.0)
    r20_mohm_per_m: float = Field(gt=0.0)


class CableStandards(BaseModel):
    """Validated BS 7671 Appendix 4 cable table."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    standards_version: str
    source_reference: str
    constants: CableConstants
    cables: list[CableEntry] = Field(min_length=1)
    extra_cpc_r20_mohm_per_m: dict[float, float] = Field(default_factory=dict)
    ca_ambient: dict[Literal["pvc_70c", "xlpe_90c"], dict[int, float]]
    cg_grouping: dict[int, float]
    ci_thermal_insulation: dict[int, float]

    def sorted_cables(self) -> list[CableEntry]:
        """Return cable entries ordered ascending by size_mm2."""
        return sorted(self.cables, key=lambda c: c.size_mm2)

    def get_cable(self, size_mm2: float) -> CableEntry:
        """Look up a cable entry by exact cross-sectional area."""
        for entry in self.cables:
            if abs(entry.size_mm2 - size_mm2) < 1e-6:
                return entry
        raise KeyError(f"Unsupported cable size: {size_mm2} mm²")

    def get_r20_mohm_per_m(self, size_mm2: float) -> float:
        """Return 20°C copper resistance in mΩ/m for a line or CPC conductor size."""
        for entry in self.cables:
            if abs(entry.size_mm2 - size_mm2) < 1e-6:
                return entry.r20_mohm_per_m
        for extra_size, r20 in self.extra_cpc_r20_mohm_per_m.items():
            if abs(extra_size - size_mm2) < 1e-6:
                return r20
        raise KeyError(f"No 20°C resistance value for conductor size: {size_mm2} mm²")

    def get_ca(self, ambient_temp_c: int, insulation: Literal["pvc_70c", "xlpe_90c"]) -> float:
        """Look up Table 4B1 ambient rating factor (conservatively stepping up if intermediate)."""
        table = self.ca_ambient[insulation]
        if ambient_temp_c in table:
            return table[ambient_temp_c]
        sorted_temps = sorted(table)
        if ambient_temp_c < sorted_temps[0]:
            return table[sorted_temps[0]]
        for temp in sorted_temps:
            if temp >= ambient_temp_c:
                return table[temp]
        raise ValueError(
            f"Ambient temperature {ambient_temp_c}°C exceeds maximum tabulated "
            f"{sorted_temps[-1]}°C for {insulation}"
        )

    def get_cg(self, grouping_count: int) -> tuple[float, bool]:
        """Look up Table 4C1 grouping factor. Returns (cg, exceeded_table_max)."""
        if grouping_count < 1:
            raise ValueError(f"grouping_count must be >= 1, got {grouping_count}")
        if grouping_count in self.cg_grouping:
            return self.cg_grouping[grouping_count], False
        sorted_counts = sorted(self.cg_grouping)
        for count in sorted_counts:
            if count >= grouping_count:
                return self.cg_grouping[count], False
        return self.cg_grouping[sorted_counts[-1]], True

    def get_ci(self, thermal_insulation_mm: int) -> float:
        """Look up Table 52.2 thermal insulation rating factor."""
        if thermal_insulation_mm < 0:
            raise ValueError(f"thermal_insulation_mm must be >= 0, got {thermal_insulation_mm}")
        if thermal_insulation_mm in self.ci_thermal_insulation:
            return self.ci_thermal_insulation[thermal_insulation_mm]
        sorted_lengths = sorted(self.ci_thermal_insulation)
        for length in sorted_lengths:
            if length >= thermal_insulation_mm:
                return self.ci_thermal_insulation[length]
        return self.ci_thermal_insulation[sorted_lengths[-1]]


class DeviceSpec(BaseModel):
    """BS 7671 Table 41.3 Zs_max and let-through I²t for one MCB/RCBO rating."""

    model_config = ConfigDict(extra="forbid")

    zs_max_ohm: float = Field(gt=0.0)
    i2t_a2s: float = Field(gt=0.0)


class DeviceStandards(BaseModel):
    """Validated BS 7671 protective device table."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    standards_version: str
    source_reference: str
    u0_v: float
    cmin: float
    standard_ratings_a: list[int] = Field(min_length=1)
    devices: dict[BreakerCurve, dict[int, DeviceSpec]]

    def get_device(self, curve: BreakerCurve, rating_a: int) -> DeviceSpec:
        """Return Zs_max and I²t for the requested breaker curve and rating."""
        curve_table = self.devices[curve]
        if rating_a not in curve_table:
            raise KeyError(f"Unsupported protective device rating: Type {curve.value} {rating_a}A")
        return curve_table[rating_a]

    def select_rating(self, required_amps: float, min_rating_a: int | None = None) -> int:
        """Select smallest standard rating >= max(required_amps, min_rating_a)."""
        floor_a = max(required_amps, float(min_rating_a or 0))
        for rating in sorted(self.standard_ratings_a):
            if float(rating) >= floor_a - 1e-9:
                return rating
        raise ValueError(
            f"Design current {required_amps:.2f} A exceeds largest standard MCB rating "
            f"({max(self.standard_ratings_a)} A)"
        )


class EacProfileSpec(BaseModel):
    """Edition-specific EAC / BS 7671 profile parameters."""

    model_config = ConfigDict(extra="forbid")

    standard_ref: str
    bathroom_socket_exclusion_m: float = Field(gt=0.0)
    rcd_socket_max_rating_a: int = Field(gt=0)


class SystemNominal(BaseModel):
    """Nominal Cyprus mains voltage and frequency parameters."""

    model_config = ConfigDict(extra="forbid")

    u0_v: float
    ul_v: float
    frequency_hz: float
    cmin: float


class AmbientDefaults(BaseModel):
    """Cyprus ambient temperature and conduit installation method defaults."""

    model_config = ConfigDict(extra="forbid")

    indoor_conduit_c: int
    roof_outdoor_c: int
    default_installation_method: Literal["B", "C"]


class EarthingRules(BaseModel):
    """Cyprus TT default and TN-S / TN-C-S earthing rules."""

    model_config = ConfigDict(extra="forbid")

    default_system: EarthingSystem
    supported_systems: list[EarthingSystem]
    tt_touch_voltage_limit_v: float
    tt_electrode_stability_warning_ohm: float
    default_rcd_idn_ma: int
    default_ze_or_ra_ohm: dict[EarthingSystem, float]


class VoltageDropLimits(BaseModel):
    """BS 7671 Appendix 4 maximum permissible voltage drop percentages."""

    model_config = ConfigDict(extra="forbid")

    lighting: float
    power: float


class SupplyRules(BaseModel):
    """EAC 1-phase/3-phase, PV, CT metering, and substation escalation thresholds."""

    model_config = ConfigDict(extra="forbid")

    mandatory_three_phase_building_types: list[BuildingType]
    single_phase_allowed_building_types: list[BuildingType]
    pv_three_phase_threshold_kva: float
    single_phase_max_kva: float
    ct_metering_threshold_a_per_phase: float
    substation_warning_kva: float
    substation_blocker_kva: float
    phase_imbalance_target_pct: float


class SupplyTier(BaseModel):
    """Standard EAC supply breaker / kVA tier."""

    model_config = ConfigDict(extra="forbid")

    label: str
    amps: int = Field(gt=0)
    kva: float = Field(gt=0.0)


class SupplyTiers(BaseModel):
    """Single-phase and three-phase EAC supply tier schedules."""

    model_config = ConfigDict(extra="forbid")

    single_phase: list[SupplyTier] = Field(min_length=1)
    three_phase: list[SupplyTier] = Field(min_length=1)


class EacCyprusRules(BaseModel):
    """Validated Cyprus EAC rules table."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    standards_version: str
    source_reference: str
    default_profile: EacProfile
    profiles: dict[EacProfile, EacProfileSpec]
    system_nominal: SystemNominal
    ambient_defaults_c: AmbientDefaults
    earthing: EarthingRules
    voltage_drop_limits_pct: VoltageDropLimits
    supply_rules: SupplyRules
    supply_tiers: SupplyTiers


class DedicatedApplianceTemplate(BaseModel):
    """Template for a dedicated appliance circuit in a room."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    category: CircuitCategory
    connected_w: float = Field(gt=0.0)
    diversity: float = Field(gt=0.0, le=1.0)
    power_factor: float = Field(gt=0.0, le=1.0)
    min_breaker_in_a: int = Field(gt=0)
    min_cable_mm2: float = Field(gt=0.0)
    curve: BreakerCurve


class RoomTemplate(BaseModel):
    """Bilingual aliases and Cyprus electrical load template for a room type."""

    model_config = ConfigDict(extra="forbid")

    aliases_en: list[str]
    aliases_el: list[str]
    lighting_w_per_m2: float = Field(ge=0.0)
    min_lighting_points: int = Field(ge=0)
    lighting_zone: Literal["day", "night"]
    socket_count_default: int = Field(ge=0)
    socket_group: Literal["kitchen_utility", "general", "none"]
    has_split_ac: bool
    ac_electrical_w: float = Field(ge=0.0)
    is_wet_room: bool
    is_outdoor: bool
    dedicated_appliances: list[DedicatedApplianceTemplate] = Field(default_factory=list)


class CircuitDefaults(BaseModel):
    """Global residential circuit grouping defaults."""

    model_config = ConfigDict(extra="forbid")

    lighting_diversity: float
    lighting_power_factor: float
    lighting_circuit_max_connected_w: float
    socket_ring_max_area_m2: float
    kitchen_ring_connected_w: float
    kitchen_ring_diversity: float
    general_ring_connected_w: float
    general_ring_diversity: float
    socket_power_factor: float
    split_ac_diversity: float
    split_ac_power_factor: float
    vertical_drop_allowance_m: float


class RoomTemplatesStandards(BaseModel):
    """Validated bilingual EN/EL room templates table."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    standards_version: str
    source_reference: str
    circuit_defaults: CircuitDefaults
    rooms: dict[RoomType, RoomTemplate]

    def build_alias_lookup(self) -> dict[str, RoomType]:
        """Build deterministic Unicode-normalized EN/EL alias -> RoomType map."""
        lookup: dict[str, RoomType] = {}
        for room_type, template in self.rooms.items():
            for alias in [*template.aliases_en, *template.aliases_el]:
                lookup[normalize_label(alias)] = room_type
        return lookup


class StandardsBundle(BaseModel):
    """All versioned BS 7671 & EAC standards loaded and validated together."""

    model_config = ConfigDict(extra="forbid")

    cables: CableStandards
    devices: DeviceStandards
    eac_rules: EacCyprusRules
    room_templates: RoomTemplatesStandards

    def versions_map(self) -> dict[str, str]:
        """Return provenance map of standards table names to versions."""
        return {
            "bs7671_cables": self.cables.standards_version,
            "bs7671_devices": self.devices.standards_version,
            "eac_cyprus_rules": self.eac_rules.standards_version,
            "room_templates": self.room_templates.standards_version,
        }


def normalize_label(text: str) -> str:
    """Normalize English/Greek text via Unicode NFD accent stripping and case folding."""
    decomposed = unicodedata.normalize("NFD", text.strip())
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(without_marks.casefold().split())


def resolve_standards_dir(standards_dir: Path | None = None) -> Path:
    """Resolve the standards/ directory from explicit arg, env var, or repo root."""
    if standards_dir is not None:
        return standards_dir

    env_dir = os.environ.get("CYELEC_STANDARDS_DIR")
    if env_dir:
        candidate = Path(env_dir)
        if candidate.is_dir():
            return candidate

    # Relative to source tree: src/cyelec/standards_loader.py -> repo root / standards
    repo_candidate = Path(__file__).resolve().parents[2] / "standards"
    if repo_candidate.is_dir():
        return repo_candidate

    cwd_candidate = Path.cwd() / "standards"
    if cwd_candidate.is_dir():
        return cwd_candidate

    raise FileNotFoundError(
        "Could not locate standards/ directory. Pass standards_dir explicitly or set "
        "CYELEC_STANDARDS_DIR."
    )


def _read_yaml(path: Path) -> object:
    if not path.is_file():
        raise FileNotFoundError(f"Missing standards table: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_standards(standards_dir: Path | None = None) -> StandardsBundle:
    """Load and strictly validate all BS 7671 and Cyprus EAC YAML tables."""
    root = resolve_standards_dir(standards_dir)
    cables = CableStandards.model_validate(_read_yaml(root / "bs7671_cables.yaml"))
    devices = DeviceStandards.model_validate(_read_yaml(root / "bs7671_devices.yaml"))
    eac_rules = EacCyprusRules.model_validate(_read_yaml(root / "eac_cyprus_rules.yaml"))
    room_templates = RoomTemplatesStandards.model_validate(
        _read_yaml(root / "room_templates.yaml")
    )
    return StandardsBundle(
        cables=cables,
        devices=devices,
        eac_rules=eac_rules,
        room_templates=room_templates,
    )
