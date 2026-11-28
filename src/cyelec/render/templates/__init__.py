"""Packaged static HTML and CSS templates for print-ready CyElec engineering reports."""

from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def load_template(filename: str) -> str:
    """Read and cache a UTF-8 template asset from ``cyelec.render.templates``."""
    return files("cyelec.render.templates").joinpath(filename).read_text(encoding="utf-8")


@cache
def load_indented_css(filename: str, spaces: int = 4) -> str:
    """Load a CSS asset and indent non-empty lines for clean inline ``<style>`` embedding."""
    prefix = " " * spaces
    raw = load_template(filename).rstrip("\n")
    return "\n".join(f"{prefix}{line}" if line else "" for line in raw.splitlines())


def render_template(filename: str, replacements: dict[str, str]) -> str:
    """Load a packaged report template and substitute explicit ``__TOKEN__`` placeholders."""
    rendered = load_template(filename)
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered
