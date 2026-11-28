"""Shared rectangular room polygon helpers for synthetic Cyprus benchmark buildings."""

from __future__ import annotations

from cyelec.models.building import Point2D


def rect_poly(x0: float, y0: float, x1: float, y1: float) -> list[Point2D]:
    """Build a counter-clockwise 4-vertex rectangle polygon in millimetres."""
    return [
        Point2D(x_mm=x0, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y0),
        Point2D(x_mm=x1, y_mm=y1),
        Point2D(x_mm=x0, y_mm=y1),
    ]


def rect_area_m2(x0: float, y0: float, x1: float, y1: float) -> float:
    """Return rectangular area in square metres rounded to 4 decimal places."""
    return round(abs((x1 - x0) * (y1 - y0)) / 1_000_000.0, 4)
