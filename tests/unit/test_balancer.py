"""Unit tests for exact MILP 3-phase load balancer."""

from __future__ import annotations

import pytest

from cyelec.models.electrical import (
    CircuitCategory,
    CircuitSpec,
    CircuitTopology,
    PhaseAssignment,
    ReviewSeverity,
)
from cyelec.solver.balancer import balance_three_phase_loads


def _spec(
    cid: str,
    ib_a: float,
    *,
    fixed_phase: PhaseAssignment | None = None,
    is_three_phase: bool = False,
) -> CircuitSpec:
    watts = ib_a * 230.0
    return CircuitSpec(
        circuit_id=cid,
        description=f"Circuit {cid}",
        category=CircuitCategory.APPLIANCE,
        topology=CircuitTopology.RADIAL,
        connected_load_w=watts,
        diversity_factor=1.0,
        diversified_load_w=watts,
        power_factor=1.0,
        design_current_ib_a=ib_a,
        length_m=15.0,
        is_three_phase=is_three_phase,
        fixed_phase=fixed_phase,
    )


def test_milp_balancer_exact_optimality_with_lossless_symmetry_breaking() -> None:
    """[10, 6, 6, 5, 5] has unique optimal partition (11, 11, 10) -> spread = 1.0 A."""
    specs = [
        _spec("C1", 10.0),
        _spec("C2", 6.0),
        _spec("C3", 6.0),
        _spec("C4", 5.0),
        _spec("C5", 5.0),
    ]
    res = balance_three_phase_loads(specs)

    assert res.solver_status == "optimal"
    assert res.max_phase_current_a == pytest.approx(11.0)
    assert res.min_phase_current_a == pytest.approx(10.0)
    assert res.imbalance_a == pytest.approx(1.0)
    assert res.within_target is True
    assert res.flags == []


def test_milp_balancer_flags_imbalance_above_10_pct() -> None:
    specs = [_spec("C1", 30.0), _spec("C2", 2.0), _spec("C3", 2.0)]
    res = balance_three_phase_loads(specs, imbalance_target_pct=10.0)

    assert res.within_target is False
    assert res.imbalance_pct > 10.0
    assert any(
        f.code == "PHASE_IMBALANCE_EXCEEDS_TARGET" and f.severity == ReviewSeverity.WARNING
        for f in res.flags
    )


def test_milp_balancer_respects_fixed_phase_and_three_phase_base() -> None:
    specs = [
        _spec("C1", 10.0, fixed_phase=PhaseAssignment.L3),
        _spec("C2", 10.0),
        _spec("C3", 10.0),
        _spec("C4", 5.0, is_three_phase=True),
    ]
    res = balance_three_phase_loads(specs)

    assert res.assignments["C1"] == PhaseAssignment.L3
    assert res.assignments["C4"] == PhaseAssignment.THREE_PHASE
    assert res.phase_currents_a == {"L1": 15.0, "L2": 15.0, "L3": 15.0}
    assert res.imbalance_pct == pytest.approx(0.0)
