"""Exact MILP 3-phase load balancer using scipy.optimize.milp (HiGHS) with symmetry breaking."""

from __future__ import annotations

from typing import Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from cyelec.models.electrical import (
    CircuitSpec,
    PhaseAssignment,
    PhaseBalanceResult,
    ReviewFlag,
    ReviewSeverity,
)

_PHASES: tuple[Literal["L1", "L2", "L3"], ...] = ("L1", "L2", "L3")
_PHASE_ENUMS: tuple[PhaseAssignment, ...] = (
    PhaseAssignment.L1,
    PhaseAssignment.L2,
    PhaseAssignment.L3,
)


def _lpt_deterministic_fallback(
    single_phase: list[CircuitSpec],
    base_current_a: float,
) -> list[int]:
    """Deterministic LPT + single-move local search fallback respecting fixed_phase."""
    n = len(single_phase)
    chosen = [0] * n
    totals = [base_current_a, base_current_a, base_current_a]
    free_indices: list[int] = []

    for i, circ in enumerate(single_phase):
        if circ.fixed_phase in _PHASE_ENUMS:
            p = _PHASE_ENUMS.index(circ.fixed_phase)
            chosen[i] = p
            totals[p] += circ.design_current_ib_a
        else:
            free_indices.append(i)

    # Assign largest currents first to the currently lightest phase (tie-break by phase index)
    free_indices.sort(key=lambda idx: (-single_phase[idx].design_current_ib_a, idx))
    for i in free_indices:
        best_p = min(range(3), key=lambda p: (totals[p], p))
        chosen[i] = best_p
        totals[best_p] += single_phase[i].design_current_ib_a

    # Deterministic greedy single-circuit move polish to reduce max(I) - min(I)
    improved = True
    while improved:
        improved = False
        cur_spread = max(totals) - min(totals)
        for i in free_indices:
            cur_p = chosen[i]
            cur_i = single_phase[i].design_current_ib_a
            for cand_p in range(3):
                if cand_p == cur_p:
                    continue
                totals[cur_p] -= cur_i
                totals[cand_p] += cur_i
                new_spread = max(totals) - min(totals)
                if new_spread < cur_spread - 1e-9:
                    chosen[i] = cand_p
                    cur_spread = new_spread
                    improved = True
                    break
                totals[cand_p] -= cur_i
                totals[cur_p] += cur_i
            if improved:
                break

    return chosen


def balance_three_phase_loads(
    circuits: list[CircuitSpec],
    *,
    imbalance_target_pct: float = 10.0,
) -> PhaseBalanceResult:
    """Assign single-phase circuits to L1/L2/L3 minimizing max(Ip) - min(Ip) via exact MILP."""
    single_phase: list[CircuitSpec] = [c for c in circuits if not c.is_three_phase]
    three_phase: list[CircuitSpec] = [c for c in circuits if c.is_three_phase]

    base_current_a = sum(c.design_current_ib_a for c in three_phase)
    base_power_kw = sum(c.diversified_load_w / 3000.0 for c in three_phase)

    assignments: dict[str, PhaseAssignment] = {
        c.circuit_id: PhaseAssignment.THREE_PHASE for c in three_phase
    }

    if not single_phase:
        currents: dict[Literal["L1", "L2", "L3"], float] = {
            p: round(base_current_a, 2) for p in _PHASES
        }
        powers: dict[Literal["L1", "L2", "L3"], float] = {
            p: round(base_power_kw, 3) for p in _PHASES
        }
        return PhaseBalanceResult(
            assignments=assignments,
            phase_currents_a=currents,
            phase_power_kw=powers,
            max_phase_current_a=round(base_current_a, 2),
            min_phase_current_a=round(base_current_a, 2),
            mean_phase_current_a=round(base_current_a, 2),
            imbalance_a=0.0,
            imbalance_pct=0.0,
            within_target=True,
            solver_status="trivial_no_1p_circuits",
            flags=[],
        )

    n = len(single_phase)
    # Variables: x[i, p] for i in 0..n-1, p in 0..2 (size 3n), followed by I_max (3n), I_min (3n+1)
    num_vars = 3 * n + 2
    idx_imax = 3 * n
    idx_imin = 3 * n + 1

    # Objective: minimize I_max - I_min
    c_obj = np.zeros(num_vars, dtype=float)
    c_obj[idx_imax] = 1.0
    c_obj[idx_imin] = -1.0

    integrality = np.zeros(num_vars, dtype=int)
    integrality[: 3 * n] = 1  # binary x[i, p]

    lb = np.zeros(num_vars, dtype=float)
    ub = np.ones(num_vars, dtype=float)
    total_i = base_current_a + sum(c.design_current_ib_a for c in single_phase)
    ub[idx_imax] = max(total_i + 1.0, 1.0)
    ub[idx_imin] = max(total_i + 1.0, 1.0)

    # Apply any engineer-fixed phase assignments via variable bounds
    has_fixed_phase = False
    for i, circ in enumerate(single_phase):
        if circ.fixed_phase in _PHASE_ENUMS:
            has_fixed_phase = True
            target_p = _PHASE_ENUMS.index(circ.fixed_phase)
            for p in range(3):
                if p == target_p:
                    lb[3 * i + p] = 1.0
                    ub[3 * i + p] = 1.0
                else:
                    lb[3 * i + p] = 0.0
                    ub[3 * i + p] = 0.0

    rows: list[np.ndarray] = []
    con_lb: list[float] = []
    con_ub: list[float] = []

    # 1. Exactly one phase per 1-phase circuit: sum_p x[i, p] == 1
    for i in range(n):
        row = np.zeros(num_vars, dtype=float)
        row[3 * i : 3 * i + 3] = 1.0
        rows.append(row)
        con_lb.append(1.0)
        con_ub.append(1.0)

    # 2. Phase current bounds: base + sum_i I_i x[i, p] <= I_max  and  >= I_min
    for p in range(3):
        # sum_i I_i x[i, p] - I_max <= -base_current_a
        row_max = np.zeros(num_vars, dtype=float)
        for i, circ in enumerate(single_phase):
            row_max[3 * i + p] = circ.design_current_ib_a
        row_max[idx_imax] = -1.0
        rows.append(row_max)
        con_lb.append(-np.inf)
        con_ub.append(-base_current_a)

        # sum_i I_i x[i, p] - I_min >= -base_current_a
        row_min = np.zeros(num_vars, dtype=float)
        for i, circ in enumerate(single_phase):
            row_min[3 * i + p] = circ.design_current_ib_a
        row_min[idx_imin] = -1.0
        rows.append(row_min)
        con_lb.append(-base_current_a)
        con_ub.append(np.inf)

    # 3. Exact lossless symmetry breaking when no phases are pre-fixed:
    #    enforce T_L1 >= T_L2 >= T_L3 across all 3! = 6 phase permutations.
    if not has_fixed_phase:
        for p_hi, p_lo in ((0, 1), (1, 2)):
            row_sym = np.zeros(num_vars, dtype=float)
            for i, circ in enumerate(single_phase):
                row_sym[3 * i + p_hi] = circ.design_current_ib_a
                row_sym[3 * i + p_lo] = -circ.design_current_ib_a
            rows.append(row_sym)
            con_lb.append(0.0)
            con_ub.append(np.inf)

    a_matrix = np.vstack(rows)
    constraints = LinearConstraint(a_matrix, lb=np.array(con_lb), ub=np.array(con_ub))
    bounds = Bounds(lb=lb, ub=ub)

    res = milp(
        c=c_obj,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": 2.0, "mip_rel_gap": 1e-3},
    )

    chosen_phases: list[int]
    if res.success and res.x is not None:
        solver_status = "optimal"
        chosen_phases = [int(np.argmax(res.x[3 * i : 3 * i + 3])) for i in range(n)]
    elif res.x is not None:
        # HiGHS reached time limit with a valid feasible near-optimal incumbent
        solver_status = "feasible_time_limit"
        chosen_phases = [int(np.argmax(res.x[3 * i : 3 * i + 3])) for i in range(n)]
    else:
        solver_status = "deterministic_lpt_fallback"
        chosen_phases = _lpt_deterministic_fallback(single_phase, base_current_a)

    phase_i: dict[Literal["L1", "L2", "L3"], float] = {p: base_current_a for p in _PHASES}
    phase_kw: dict[Literal["L1", "L2", "L3"], float] = {p: base_power_kw for p in _PHASES}

    for i, circ in enumerate(single_phase):
        chosen_p = chosen_phases[i]
        phase_key = _PHASES[chosen_p]
        assignments[circ.circuit_id] = _PHASE_ENUMS[chosen_p]
        phase_i[phase_key] += circ.design_current_ib_a
        phase_kw[phase_key] += circ.diversified_load_w / 1000.0

    max_i = max(phase_i.values())
    min_i = min(phase_i.values())
    mean_i = sum(phase_i.values()) / 3.0
    imbalance_a = max_i - min_i
    imbalance_pct = (imbalance_a / max_i * 100.0) if max_i > 1e-9 else 0.0
    within_target = imbalance_pct <= imbalance_target_pct + 1e-9

    flags: list[ReviewFlag] = []
    if not within_target:
        flags.append(
            ReviewFlag(
                code="PHASE_IMBALANCE_EXCEEDS_TARGET",
                severity=ReviewSeverity.WARNING,
                category="balancing",
                regulation_ref="EAC 3-Phase Load Balancing Target (<=10%)",
                message=(
                    f"3-phase load imbalance is {imbalance_pct:.2f}% "
                    f"(L1={phase_i['L1']:.2f} A, L2={phase_i['L2']:.2f} A, "
                    f"L3={phase_i['L3']:.2f} A), "
                    f"exceeding the {imbalance_target_pct:.1f}% target."
                ),
            )
        )

    return PhaseBalanceResult(
        assignments=assignments,
        phase_currents_a={p: round(v, 2) for p, v in phase_i.items()},
        phase_power_kw={p: round(v, 3) for p, v in phase_kw.items()},
        max_phase_current_a=round(max_i, 2),
        min_phase_current_a=round(min_i, 2),
        mean_phase_current_a=round(mean_i, 2),
        imbalance_a=round(imbalance_a, 2),
        imbalance_pct=round(imbalance_pct, 2),
        within_target=within_target,
        solver_status=solver_status,
        flags=flags,
    )
