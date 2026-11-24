"""Parametric synthetic DXF architectural fixtures for deterministic testing."""

from cyelec.synth.sample_plan import (
    STANDARD_LAYERS,
    build_cyprus_3bed_model,
    generate_cyprus_3bed_dxf,
    render_building_to_dxf,
)

__all__ = [
    "STANDARD_LAYERS",
    "build_cyprus_3bed_model",
    "generate_cyprus_3bed_dxf",
    "render_building_to_dxf",
]
