"""Automated Playwright Browser UI Regression Suite (CI + Live Staging).

Runs against an ephemeral local uvicorn instance by default, or against a live URL when
`CYELEC_E2E_URL` is set. Saves visual evidence screenshots into `CYELEC_SCREENSHOT_DIR`
(defaults to `out/ui_screenshots`) so GitHub Actions can archive them as build artifacts.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf

playwright_sync = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _chrome_launch_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    }
    sys_chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if sys_chrome and not os.environ.get("PLAYWRIGHT_USE_BUNDLED_CHROMIUM"):
        kwargs["executable_path"] = sys_chrome
    return kwargs


@pytest.fixture(scope="module")
def workbench_url() -> Iterator[str]:
    live_url = os.environ.get("CYELEC_E2E_URL", "").strip().rstrip("/")
    if live_url:
        yield live_url
        return

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "cyelec.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready = False
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.25)
        assert ready, f"Local uvicorn failed to start on {base}"
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.ui
def test_workbench_full_interactive_regression(workbench_url: str, tmp_path: Path) -> None:
    """Interactive Chrome UI regression covering all 5 sheets, overrides, upload & viewports."""
    shot_dir = Path(os.environ.get("CYELEC_SCREENSHOT_DIR", str(tmp_path / "screenshots")))
    shot_dir.mkdir(parents=True, exist_ok=True)

    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []

    with playwright_sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch(**_chrome_launch_kwargs())
        except Exception:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            accept_downloads=True,
        )
        page = ctx.new_page()
        page.on(
            "console",
            lambda m: console_errors.append(f"{m.type}: {m.text}")
            if m.type == "error"
            else None,
        )
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on(
            "response",
            lambda r: http_errors.append(f"HTTP {r.status} {r.url}")
            if r.status >= 400 and not r.url.endswith("/favicon.ico")
            else None,
        )

        # 1. Load Workbench & verify auto-solve + Stage 5 Photometrics + 0 overflow
        page.goto(workbench_url, wait_until="networkidle", timeout=60000)
        page.wait_for_function(
            "() => document.getElementById('kSupply').textContent !== '-'",
            timeout=45000,
        )
        desktop_metrics = page.evaluate("""() => {
            const r2 = document.querySelector('.row2');
            return {
                overflowX: document.documentElement.scrollWidth > window.innerWidth + 2,
                row2Display: r2 ? getComputedStyle(r2).display : 'missing',
            };
        }""")
        assert desktop_metrics["overflowX"] is False
        assert desktop_metrics["row2Display"] == "grid"

        s5_txt = page.locator("#stage5EngSummary").inner_text()
        assert "DIN-Rail DBs" in s5_txt
        assert "11/11 Compliant" in s5_txt
        assert "undefined" not in s5_txt and "NaN" not in s5_txt
        page.click("#stage5EngSummary details summary")
        phot_txt = page.locator("#stage5EngSummary").inner_text()
        assert "undefined" not in phot_txt and "NaN" not in phot_txt
        page.screenshot(path=str(shot_dir / "01_desktop_circuits.png"), full_page=False)
        page.click("#stage5EngSummary details summary")

        # 2. Interactive Pass-2 circuit override + Apply & Re-Solve
        page.locator("#circuitsBody select.phase-sel").first.select_option("L3")
        cg_inp = page.locator("#circuitsBody input.mini-input").first
        cg_inp.fill("2")
        cg_inp.dispatch_event("change")
        assert page.locator("#pendingEditsBar").is_visible()
        prev_st = page.locator("#statusLine").text_content() or ""
        page.click("#pendingEditsBar button:has-text('Apply')")
        page.wait_for_function(
            "(prev) => { const t = document.getElementById('statusLine').textContent || ''; "
            "return t !== prev && t.includes('Solved cleanly'); }",
            arg=prev_st,
            timeout=45000,
        )

        # 3. Villa 2-Storey Turnkey Brief + Unpriced Tender BOQ toggle & SEC 01..10 order
        prev_st = page.locator("#statusLine").text_content() or ""
        page.select_option("#benchmarkSel", "villa_2storey")
        page.wait_for_function(
            "(prev) => { const t = document.getElementById('statusLine').textContent || ''; "
            "return t !== prev && t.includes('Solved cleanly'); }",
            arg=prev_st,
            timeout=45000,
        )
        page.click("#briefBox summary")
        page.select_option("#briefHvac", "heat_pump_ufh")
        page.select_option("#briefEvKw", "11")
        page.check("#briefPool")
        page.check("#briefKnx")
        page.fill("#pvKva", "6")
        page.fill("#briefHeightM", "12")
        page.check("#tenderBoqChk")
        prev_st = page.locator("#statusLine").text_content() or ""
        page.click("#btnRunSolve")
        page.wait_for_function(
            "(prev) => { const t = document.getElementById('statusLine').textContent || ''; "
            "return t !== prev && t.includes('Solved cleanly'); }",
            arg=prev_st,
            timeout=45000,
        )
        assert "3x80A" in (page.locator("#kSupply").text_content() or "")
        assert "Unpriced" in (page.locator("#kBoq").text_content() or "")
        page.click("button.tab:has-text('Bill of Quantities')")
        sec_headers = page.locator("#boqBody tr.boq-sec-row").all_inner_texts()
        assert len(sec_headers) >= 8
        assert sec_headers[0].startswith("SEC 01"), f"BOQ out of order: {sec_headers[:3]}"
        assert sec_headers[-1].startswith("SEC 10"), f"BOQ out of order: {sec_headers[-3:]}"
        page.screenshot(path=str(shot_dir / "02_boq_tender.png"), full_page=False)
        page.uncheck("#tenderBoqChk")

        # 4. Drawing Set (E-100..E-103) + Click-to-Relocate DB1 + Zoom + Fullscreen ESC
        page.click("button.tab:has-text('Drawing Set')")
        page.click("#btnSheetElv")
        page.screenshot(path=str(shot_dir / "03_sheet_E103_elv.png"), full_page=False)
        page.click("#btnSheetCombined")
        page.click("#btnDbMode")
        svg_box = page.locator("#floorplanSvg svg").bounding_box()
        assert svg_box is not None
        prev_st = page.locator("#statusLine").text_content() or ""
        page.mouse.click(
            svg_box["x"] + svg_box["width"] * 0.28,
            svg_box["y"] + svg_box["height"] * 0.45,
        )
        page.wait_for_function(
            "(prev) => { const t = document.getElementById('statusLine').textContent || ''; "
            "return t !== prev && t.includes('Solved cleanly'); }",
            arg=prev_st,
            timeout=45000,
        )
        page.click("#tabFloorplan button[onclick=\"zoomSvg('floorplanSvg', 25)\"]")
        assert (page.locator("#zoomLbl_floorplanSvg").text_content() or "") == "125%"
        page.click("#tabFloorplan button:has-text('Fullscreen')")
        assert page.locator("#fsModal.open").count() == 1
        page.keyboard.press("Escape")
        assert page.locator("#fsModal.open").count() == 0

        # 5. E-104, E-201, E-202, E-301 & live Chrome computed white header CSS check
        for tab_name in ("Site & Earthing", "SLD (E-201)", "Riser & DIN-Rail", "Details & Notes"):
            page.click(f"button.tab:has-text('{tab_name}')")
            time.sleep(0.15)
        e301_fills = page.evaluate("""() => {
            const wt = document.querySelector('#detailsSvg .cy-e301 .wtitle');
            const th = document.querySelector('#detailsSvg .cy-e301 .th');
            return {
                wt: wt ? getComputedStyle(wt).fill : 'MISSING',
                th: th ? getComputedStyle(th).fill : 'MISSING',
            };
        }""")
        assert e301_fills["wt"] == "rgb(255, 255, 255)", f"E-301 wtitle CSS bleed: {e301_fills}"
        assert e301_fills["th"] == "rgb(255, 255, 255)", f"E-301 th CSS bleed: {e301_fills}"
        page.screenshot(path=str(shot_dir / "04_tab_E301_details.png"), full_page=False)

        # 6. Real DXF upload -> Layer Wizard -> Solve -> ZIP download -> Clear File
        upload_dxf = tmp_path / "ui_upload_apt.dxf"
        generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=upload_dxf)
        page.set_input_files("#dxfFile", str(upload_dxf))
        page.wait_for_function(
            "() => document.getElementById('btnClearFiles').offsetParent !== null",
            timeout=20000,
        )
        prev_st = page.locator("#statusLine").text_content() or ""
        page.click("#btnRunSolve")
        page.wait_for_function(
            "(prev) => { const t = document.getElementById('statusLine').textContent || ''; "
            "return t !== prev && t.includes('Solved cleanly'); }",
            arg=prev_st,
            timeout=45000,
        )
        with page.expect_download(timeout=60000) as dl_info:
            page.click("#btnDownloadZip")
        dl_zip = tmp_path / "ui_downloaded_pack.zip"
        dl_info.value.save_as(str(dl_zip))
        with zipfile.ZipFile(dl_zip) as zf:
            znames = zf.namelist()
            assert "E101_lighting_layout.dxf" in znames
            assert "E201_single_line_diagram.svg" in znames
            assert "tender_boq_10section.xlsx" in znames

        page.click("#btnClearFiles")
        time.sleep(0.2)
        assert not page.locator("#btnClearFiles").is_visible()

        # 7. Responsive Tablet 768x1024 & Mobile 390x844 (0 horizontal overflow) + /guide
        page.click("button.tab:has-text('Circuits')")
        for label, w, h in (("tablet_768", 768, 1024), ("mobile_390", 390, 844)):
            page.set_viewport_size({"width": w, "height": h})
            time.sleep(0.2)
            ov = page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth + 2")
            assert ov is False, f"Horizontal overflow at {label} ({w}x{h})"
            page.screenshot(path=str(shot_dir / f"05_{label}.png"), full_page=False)

        page.goto(f"{workbench_url}/guide", wait_until="networkidle", timeout=30000)
        assert "BS 7671" in page.content()

        browser.close()

    assert console_errors == [], f"Browser console errors: {console_errors}"
    assert page_errors == [], f"Uncaught page errors: {page_errors}"
    assert http_errors == [], f"HTTP >=400 errors: {http_errors}"
