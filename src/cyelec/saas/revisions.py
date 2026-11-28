"""Deterministic architectural/electrical revision diffing & Pass-2 override carry-forward."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cyelec.models.electrical import DesignOverrides, SolvedCircuit
from cyelec.models.spatial import Phase2DesignResult


class RoomDelta(BaseModel):
    """Room-level architectural or classification difference between two revisions."""

    model_config = ConfigDict(extra="forbid")

    room_id: str
    raw_label: str
    change: Literal["added", "removed", "resized", "reclassified"]
    old_area_m2: float | None = None
    new_area_m2: float | None = None
    old_type: str | None = None
    new_type: str | None = None


class CircuitDelta(BaseModel):
    """Circuit-level BS 7671 engineering change between two revisions."""

    model_config = ConfigDict(extra="forbid")

    circuit_id: str
    description: str
    change: Literal[
        "added",
        "removed",
        "cable_changed",
        "breaker_changed",
        "phase_changed",
        "length_changed",
    ]
    old_summary: str | None = None
    new_summary: str | None = None


class RevisionDiff(BaseModel):
    """Structured engineering delta between Revision A and Revision B of a project."""

    model_config = ConfigDict(extra="forbid")

    rev_a: int
    rev_b: int
    room_deltas: list[RoomDelta] = Field(default_factory=list)
    circuit_deltas: list[CircuitDelta] = Field(default_factory=list)
    supply_tier_a: str
    supply_tier_b: str
    supply_tier_changed: bool
    diversified_kva_delta: float
    conduit_m_delta: float
    boq_eur_delta: float
    imbalance_pct_delta: float | None = None
    flags_added: list[str] = Field(default_factory=list)
    flags_resolved: list[str] = Field(default_factory=list)


def _circuit_summary(c: SolvedCircuit) -> str:
    return (
        f"{c.phase.value} | {c.breaker_in_a}A {c.breaker_curve.value} | "
        f"{c.cable_mm2:g}/{c.cpc_mm2:g}mm² | {c.spec.length_m:.1f}m | ΔV {c.voltage_drop_pct:.2f}%"
    )


def compute_revision_diff(
    rev_a: int,
    res_a: Phase2DesignResult,
    rev_b: int,
    res_b: Phase2DesignResult,
) -> RevisionDiff:
    """Deterministically diff rooms, circuits, supply tier, BOQ, conduit & flags across revs."""
    da, db = res_a.design, res_b.design

    # 1. Room deltas
    rooms_a = {r.id: r for r in da.building.rooms}
    rooms_b = {r.id: r for r in db.building.rooms}
    room_deltas: list[RoomDelta] = []

    for rid in sorted(set(rooms_a) | set(rooms_b)):
        ra = rooms_a.get(rid)
        rb = rooms_b.get(rid)
        if ra is None and rb is not None:
            room_deltas.append(
                RoomDelta(
                    room_id=rid,
                    raw_label=rb.raw_label,
                    change="added",
                    new_area_m2=rb.area_m2,
                    new_type=rb.normalized_type.value,
                )
            )
        elif rb is None and ra is not None:
            room_deltas.append(
                RoomDelta(
                    room_id=rid,
                    raw_label=ra.raw_label,
                    change="removed",
                    old_area_m2=ra.area_m2,
                    old_type=ra.normalized_type.value,
                )
            )
        elif ra is not None and rb is not None:
            if ra.normalized_type != rb.normalized_type:
                room_deltas.append(
                    RoomDelta(
                        room_id=rid,
                        raw_label=rb.raw_label,
                        change="reclassified",
                        old_area_m2=ra.area_m2,
                        new_area_m2=rb.area_m2,
                        old_type=ra.normalized_type.value,
                        new_type=rb.normalized_type.value,
                    )
                )
            elif abs(ra.area_m2 - rb.area_m2) >= 0.05:
                room_deltas.append(
                    RoomDelta(
                        room_id=rid,
                        raw_label=rb.raw_label,
                        change="resized",
                        old_area_m2=ra.area_m2,
                        new_area_m2=rb.area_m2,
                        old_type=ra.normalized_type.value,
                        new_type=rb.normalized_type.value,
                    )
                )

    # 2. Circuit deltas
    ckts_a = {c.spec.circuit_id: c for c in da.circuits}
    ckts_b = {c.spec.circuit_id: c for c in db.circuits}
    circuit_deltas: list[CircuitDelta] = []

    for cid in sorted(set(ckts_a) | set(ckts_b)):
        ca = ckts_a.get(cid)
        cb = ckts_b.get(cid)
        if ca is None and cb is not None:
            circuit_deltas.append(
                CircuitDelta(
                    circuit_id=cid,
                    description=cb.spec.description,
                    change="added",
                    new_summary=_circuit_summary(cb),
                )
            )
        elif cb is None and ca is not None:
            circuit_deltas.append(
                CircuitDelta(
                    circuit_id=cid,
                    description=ca.spec.description,
                    change="removed",
                    old_summary=_circuit_summary(ca),
                )
            )
        elif ca is not None and cb is not None:
            change: Literal[
                "cable_changed", "breaker_changed", "phase_changed", "length_changed"
            ] | None = None
            if ca.cable_mm2 != cb.cable_mm2 or ca.cpc_mm2 != cb.cpc_mm2:
                change = "cable_changed"
            elif ca.breaker_in_a != cb.breaker_in_a or ca.breaker_curve != cb.breaker_curve:
                change = "breaker_changed"
            elif ca.phase != cb.phase:
                change = "phase_changed"
            elif abs(ca.spec.length_m - cb.spec.length_m) >= 0.2:
                change = "length_changed"

            if change is not None:
                circuit_deltas.append(
                    CircuitDelta(
                        circuit_id=cid,
                        description=cb.spec.description,
                        change=change,
                        old_summary=_circuit_summary(ca),
                        new_summary=_circuit_summary(cb),
                    )
                )

    # 3. KPI & Review Flag deltas
    boq_a = da.boq.grand_total_eur if da.boq is not None else 0.0
    boq_b = db.boq.grand_total_eur if db.boq is not None else 0.0
    imb_delta = (
        round(db.phase_balance.imbalance_pct - da.phase_balance.imbalance_pct, 2)
        if (da.phase_balance is not None and db.phase_balance is not None)
        else None
    )

    def _flag_key(code: str, cid: str | None) -> str:
        return f"{code}:{cid}" if cid else code

    flags_a = {_flag_key(f.code, f.circuit_id) for f in da.review_flags}
    flags_b = {_flag_key(f.code, f.circuit_id) for f in db.review_flags}

    return RevisionDiff(
        rev_a=rev_a,
        rev_b=rev_b,
        room_deltas=room_deltas,
        circuit_deltas=circuit_deltas,
        supply_tier_a=da.eac_compliance.recommended_supply_tier,
        supply_tier_b=db.eac_compliance.recommended_supply_tier,
        supply_tier_changed=(
            da.eac_compliance.recommended_supply_tier != db.eac_compliance.recommended_supply_tier
        ),
        diversified_kva_delta=round(
            db.eac_compliance.total_diversified_kva - da.eac_compliance.total_diversified_kva, 2
        ),
        conduit_m_delta=round(res_b.spatial.total_conduit_m - res_a.spatial.total_conduit_m, 2),
        boq_eur_delta=round(boq_b - boq_a, 2),
        imbalance_pct_delta=imb_delta,
        flags_added=sorted(flags_b - flags_a),
        flags_resolved=sorted(flags_a - flags_b),
    )


def carry_forward_overrides(
    prev_overrides: DesignOverrides | None,
    new_overrides: DesignOverrides | None,
) -> DesignOverrides | None:
    """Merge previous revision's Pass-2 DesignOverrides into a new revision cleanly."""
    if prev_overrides is None:
        return new_overrides
    if new_overrides is None:
        return prev_overrides.model_copy(deep=True)

    defaults = DesignOverrides()
    merged_circuits = dict(prev_overrides.circuit_overrides)
    merged_circuits.update(new_overrides.circuit_overrides)

    merged_rooms = dict(prev_overrides.room_type_overrides)
    merged_rooms.update(new_overrides.room_type_overrides)

    def _pick[T](nv: T, pv: T, dv: T) -> T:
        if dv is None:
            return nv if nv is not None else pv
        return nv if nv != dv else pv

    return DesignOverrides(
        eac_profile=_pick(
            new_overrides.eac_profile, prev_overrides.eac_profile, defaults.eac_profile
        ),
        earthing_system=_pick(
            new_overrides.earthing_system,
            prev_overrides.earthing_system,
            defaults.earthing_system,
        ),
        ze_or_ra_ohm=_pick(
            new_overrides.ze_or_ra_ohm, prev_overrides.ze_or_ra_ohm, defaults.ze_or_ra_ohm
        ),
        ambient_temp_c=_pick(
            new_overrides.ambient_temp_c, prev_overrides.ambient_temp_c, defaults.ambient_temp_c
        ),
        pv_capacity_kva=_pick(
            new_overrides.pv_capacity_kva,
            prev_overrides.pv_capacity_kva,
            defaults.pv_capacity_kva,
        ),
        prospective_fault_current_ka=_pick(
            new_overrides.prospective_fault_current_ka,
            prev_overrides.prospective_fault_current_ka,
            defaults.prospective_fault_current_ka,
        ),
        circuit_overrides=merged_circuits,
        room_type_overrides=merged_rooms,
        db_location_override_mm=_pick(
            new_overrides.db_location_override_mm,
            prev_overrides.db_location_override_mm,
            defaults.db_location_override_mm,
        ),
        meter_location_override_mm=_pick(
            new_overrides.meter_location_override_mm,
            prev_overrides.meter_location_override_mm,
            defaults.meter_location_override_mm,
        ),
        earth_pit_location_override_mm=_pick(
            new_overrides.earth_pit_location_override_mm,
            prev_overrides.earth_pit_location_override_mm,
            defaults.earth_pit_location_override_mm,
        ),
        telecom_location_override_mm=_pick(
            new_overrides.telecom_location_override_mm,
            prev_overrides.telecom_location_override_mm,
            defaults.telecom_location_override_mm,
        ),
        residential_brief=_pick(
            new_overrides.residential_brief,
            prev_overrides.residential_brief,
            defaults.residential_brief,
        ),
        report_language=_pick(
            new_overrides.report_language,
            prev_overrides.report_language,
            defaults.report_language,
        ),
        branding=_pick(new_overrides.branding, prev_overrides.branding, defaults.branding),
        boq_rate_overrides=_pick(
            new_overrides.boq_rate_overrides,
            prev_overrides.boq_rate_overrides,
            defaults.boq_rate_overrides,
        ),
        tender_boq_blank_rates=_pick(
            new_overrides.tender_boq_blank_rates,
            prev_overrides.tender_boq_blank_rates,
            defaults.tender_boq_blank_rates,
        ),
        apply_corridor_grouping=_pick(
            new_overrides.apply_corridor_grouping,
            prev_overrides.apply_corridor_grouping,
            defaults.apply_corridor_grouping,
        ),
        include_tt_main_rcd_s=_pick(
            new_overrides.include_tt_main_rcd_s,
            prev_overrides.include_tt_main_rcd_s,
            defaults.include_tt_main_rcd_s,
        ),
        eac_metadata=_pick(
            new_overrides.eac_metadata, prev_overrides.eac_metadata, defaults.eac_metadata
        ),
        notes=_pick(new_overrides.notes, prev_overrides.notes, defaults.notes),
    )
