"""Real-world architectural workflow testing & engineer reference-plan evaluation engine."""

from cyelec.eval.evaluator import (
    CategoryComparison,
    EvaluationReport,
    ReferenceCircuitSpec,
    ReferenceElectricalPlan,
    evaluate_against_reference,
    export_evaluation_reports,
    load_reference_plan,
    render_evaluation_html,
    render_evaluation_md,
)

__all__ = [
    "CategoryComparison",
    "EvaluationReport",
    "ReferenceCircuitSpec",
    "ReferenceElectricalPlan",
    "evaluate_against_reference",
    "export_evaluation_reports",
    "load_reference_plan",
    "render_evaluation_html",
    "render_evaluation_md",
]
