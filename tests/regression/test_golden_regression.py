"""Strict 0%-tolerance exact-match golden engineering regression suite.

Any numerical, sizing, routing, BOQ, or CAD/SVG invariant change fails immediately with
an exact field-by-field diff. To update baselines intentionally after an authorized
BS 7671 / Cyprus EAC / BOQ rate change, run: `python3 scripts/update_golden.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from update_golden import (  # noqa: E402
    GOLDEN_PATH,
    SCENARIOS,
    compute_scenario_snapshot,
)


def _load_golden() -> dict[str, dict[str, Any]]:
    assert GOLDEN_PATH.is_file(), (
        f"Golden baselines file missing: {GOLDEN_PATH}. "
        "Generate with: python3 scripts/update_golden.py"
    )
    data: dict[str, dict[str, Any]] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return data


@pytest.mark.golden
@pytest.mark.parametrize(
    ("name", "template", "overrides"),
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_exact_match_golden_engineering_snapshot(
    name: str,
    template: Any,
    overrides: Any,
) -> None:
    """Verify exact 0%-tolerance parity against frozen engineering & CAD baseline."""
    golden_all = _load_golden()
    assert name in golden_all, f"Missing golden snapshot for {name!r}"
    expected = golden_all[name]
    actual = compute_scenario_snapshot(name, template, overrides)

    # Hard engineering invariants must always hold independent of snapshot updates
    inv = actual["invariants"]
    assert inv["lumen_non_compliant"] == 0, f"{name}: non-compliant lux rooms detected"
    assert inv["stacked_devices_lt_100mm"] == 0, f"{name}: stacked devices (<100mm) detected"
    assert inv["dxf_audit_errors"] == 0, f"{name}: ezdxf audit errors detected"
    assert inv["e201_subboard_overlaps"] == 0, f"{name}: overlapping SLD sub-board headers"
    assert inv["e301_scoped_css"] is True, f"{name}: E-301 missing scoped .cy-e301 CSS"
    assert inv["eac_html_has_literal_none"] is False, f"{name}: literal '>None<' in EAC HTML"
    assert actual["all_circuits_pass"] is True, f"{name}: failing BS 7671 circuit detected"
    assert actual["blocker_count"] == 0, f"{name}: unexpected ETEK blocker flag"

    # Strict exact-match (0% tolerance) against frozen golden JSON
    assert actual == expected, (
        f"Golden regression drift detected on '{name}'!\n"
        "If this change is an intentional BS 7671 / EAC / BOQ update, refresh baselines via:\n"
        "  python3 scripts/update_golden.py\n"
        "and document the engineering justification in your PR."
    )
