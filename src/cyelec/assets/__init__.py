"""Packaged CyElec brand assets (logo, favicon, OpenGraph thumbnail) with zero external CDNs."""

from __future__ import annotations

import base64
from functools import lru_cache
from importlib.resources import files

_ASSET_MIME: dict[str, str] = {
    "cyelec_logo.png": "image/png",
    "cyelec_logo.svg": "image/svg+xml",
    "favicon.ico": "image/x-icon",
    "og_thumbnail.jpg": "image/jpeg",
}


@lru_cache(maxsize=8)
def get_asset_bytes(filename: str) -> bytes:
    """Return raw bytes for a packaged brand asset inside cyelec.assets."""
    if filename not in _ASSET_MIME:
        raise KeyError(f"Unknown CyElec brand asset: '{filename}'")
    return files("cyelec.assets").joinpath(filename).read_bytes()


def get_asset_media_type(filename: str) -> str:
    """Return HTTP Content-Type MIME string for a packaged brand asset."""
    return _ASSET_MIME[filename]


@lru_cache(maxsize=2)
def get_logo_svg_data_uri() -> str:
    """Return inline base64 data URI for the vector CyElec logo (works 100% offline)."""
    b64 = base64.b64encode(get_asset_bytes("cyelec_logo.svg")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


@lru_cache(maxsize=2)
def get_logo_png_data_uri() -> str:
    """Return inline base64 data URI for the 256px PNG CyElec logo (works 100% offline)."""
    b64 = base64.b64encode(get_asset_bytes("cyelec_logo.png")).decode("ascii")
    return f"data:image/png;base64,{b64}"
