"""Packaged static HTML, CSS, and JavaScript templates for the CyElec Workbench & User Guide."""

from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def load_template(filename: str) -> str:
    """Read and cache a UTF-8 template asset from ``cyelec.api.templates``."""
    return files("cyelec.api.templates").joinpath(filename).read_text(encoding="utf-8")


def render_template(filename: str, replacements: dict[str, str]) -> str:
    """Load a packaged template and substitute explicit ``__TOKEN__`` placeholders."""
    rendered = load_template(filename)
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered
