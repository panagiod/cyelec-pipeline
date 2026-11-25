"""Tests for packaged CyElec logo, favicons, OpenGraph thumbnails & HTML report branding."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.assets import get_asset_bytes, get_logo_png_data_uri, get_logo_svg_data_uri
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf


def test_brand_asset_routes_and_workbench_og_thumbnails(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "brand.db"))

    # 1. All 4 packaged brand assets serve with proper MIME & caching
    expected = [
        ("/favicon.ico", "image/x-icon", b"\x00\x00\x01\x00"),
        ("/favicon.svg", "image/svg+xml", b"<svg"),
        ("/logo.png", "image/png", b"\x89PNG"),
        ("/og-thumbnail.jpg", "image/jpeg", b"\xff\xd8\xff"),
    ]
    for path, mime, magic in expected:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith(mime)
        assert "max-age=86400" in resp.headers.get("cache-control", "")
        assert resp.content.startswith(magic)

    # 2. Workbench HTML embeds favicons, absolute HTTPS og:image, and navbar logo
    ui = client.get(
        "/",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "cyelec-prod.example.run.app",
        },
    )
    assert ui.status_code == 200
    assert 'rel="icon" type="image/svg+xml" href="/favicon.svg"' in ui.text
    assert 'rel="icon" type="image/x-icon" href="/favicon.ico"' in ui.text
    assert (
        'property="og:image" content="https://cyelec-prod.example.run.app/og-thumbnail.jpg"'
        in ui.text
    )
    assert 'class="brand-logo"' in ui.text
    assert get_logo_svg_data_uri().startswith("data:image/svg+xml;base64,")
    assert get_logo_png_data_uri().startswith("data:image/png;base64,")
    assert len(get_asset_bytes("og_thumbnail.jpg")) > 20_000


def test_html_reports_embed_offline_favicon_and_logo(tmp_path: Path) -> None:
    building = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    p2 = run_phase2_pipeline(building)
    outputs = export_phase3_deliverables(p2, tmp_path / "brand_pack")

    calc_html = outputs["html"].read_text(encoding="utf-8")
    cover_html = outputs["eac_pack_html"].read_text(encoding="utf-8")

    for doc in (calc_html, cover_html):
        assert 'rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,' in doc
        assert 'class="hdr-logo"' in doc
