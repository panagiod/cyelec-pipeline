"""Parametric synthetic DXF architectural fixtures for deterministic testing & benchmarking."""

from cyelec.synth.benchmarks import (
    BenchmarkTemplate,
    build_cyprus_commercial_office_model,
    build_cyprus_villa_pv_ev_model,
    generate_benchmark_dxf,
)
from cyelec.synth.sample_plan import (
    STANDARD_LAYERS,
    build_cyprus_3bed_model,
    generate_cyprus_3bed_dxf,
    render_building_to_dxf,
)

__all__ = [
    "STANDARD_LAYERS",
    "BenchmarkTemplate",
    "build_cyprus_3bed_model",
    "build_cyprus_commercial_office_model",
    "build_cyprus_villa_pv_ev_model",
    "generate_benchmark_dxf",
    "generate_cyprus_3bed_dxf",
    "render_building_to_dxf",
]
