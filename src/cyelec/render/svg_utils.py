"""Shared lightweight SVG escaping and Phase-2 result unwrapping utilities for renderers."""

from __future__ import annotations

import html

from cyelec.models.electrical import ElectricalDesignResult
from cyelec.models.spatial import Phase2DesignResult


def esc(val: object) -> str:
    """XML/SVG-escape any value with double quotes escaped for safe attribute interpolation."""
    return html.escape(str(val), quote=True)


def unwrap_design(result: Phase2DesignResult | ElectricalDesignResult) -> ElectricalDesignResult:
    """Extract the underlying ElectricalDesignResult from either a Phase-1 or Phase-2 payload."""
    return result.design if isinstance(result, Phase2DesignResult) else result
