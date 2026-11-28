"""Architecture, modularity, packaging & coding best-practices regression guards.

Locks in structural improvements so regressions (circular imports, silent exception swallows,
production asserts, type:ignore sprawl, unvalidated inline JS, or route-table drift) fail CI.
"""

from __future__ import annotations

import ast
import collections
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cyelec.api.app import create_app
from cyelec.api.docs_ui import render_user_guide_html, render_workbench_guide_tab_html
from cyelec.api.templates import load_template
from cyelec.api.ui import render_etek_workbench_html

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "cyelec"
_PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]{2,}[A-Z0-9]__")


def _iter_src_py_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _iter_app_routes(app: object) -> list[tuple[str, tuple[str, ...]]]:
    """Flatten FastAPI routes across included APIRouters (compatible with FastAPI >=0.163)."""
    out: list[tuple[str, tuple[str, ...]]] = []

    def _walk(routes: list[object]) -> None:
        for r in routes:
            orig = getattr(r, "original_router", None)
            if orig is not None:
                _walk(list(orig.routes))
                continue
            path = getattr(r, "path", "")
            methods = tuple(sorted(getattr(r, "methods", []) or []))
            if path:
                out.append((path, methods))

    _walk(list(getattr(app, "routes", [])))
    return out


def test_zero_module_level_circular_imports() -> None:
    """Tarjan SCC check: the entire cyelec.* module graph must remain a strict DAG."""
    edges: dict[str, set[str]] = collections.defaultdict(set)
    for f in _iter_src_py_files():
        rel = f.relative_to(_SRC_ROOT).with_suffix("")
        src_mod = (
            "cyelec"
            if rel.parts == ("__init__",)
            else "cyelec." + ".".join(p for p in rel.parts if p != "__init__")
        )
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("cyelec")
            ):
                edges[src_mod].add(node.module)

    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(edges.get(v, ())):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                cycles.append(sorted(scc))

    for mod in sorted(edges):
        if mod not in indices:
            strongconnect(mod)

    assert cycles == [], f"Circular module dependencies detected: {cycles}"


def test_type_ignore_and_production_assert_ratchets() -> None:
    """Guard against type:ignore sprawl, production asserts (S101), and bare print() calls."""
    type_ignores: list[str] = []
    prod_asserts: list[str] = []
    print_calls: list[str] = []

    for f in _iter_src_py_files():
        src = f.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if "type: ignore" in line:
                type_ignores.append(f"{f.relative_to(_REPO_ROOT)}:{i}")
        tree = ast.parse(src, filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                prod_asserts.append(f"{f.relative_to(_REPO_ROOT)}:{node.lineno}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                print_calls.append(f"{f.relative_to(_REPO_ROOT)}:{node.lineno}")

    assert len(type_ignores) <= 2, f"Too many type:ignore suppressions: {type_ignores}"
    assert prod_asserts == [], f"Production asserts found (use RuntimeError): {prod_asserts}"
    assert print_calls == [], f"Direct print() calls found in src/cyelec: {print_calls}"


def _cyclomatic_complexity(node: ast.AST) -> int:
    """Compute McCabe cyclomatic complexity for a Python function AST node."""
    score = 1
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ExceptHandler,
                ast.With,
                ast.AsyncWith,
                ast.IfExp,
            ),
        ):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
    return score


def test_monolith_size_ratchets() -> None:
    """Keep decomposed API composition root, UI/report renderers & core functions bounded."""
    app_loc = len((_SRC_ROOT / "api" / "app.py").read_text(encoding="utf-8").splitlines())
    ui_loc = len((_SRC_ROOT / "api" / "ui.py").read_text(encoding="utf-8").splitlines())
    docs_loc = len((_SRC_ROOT / "api" / "docs_ui.py").read_text(encoding="utf-8").splitlines())
    assert app_loc <= 250, f"api/app.py regrew to {app_loc} LOC"
    assert ui_loc <= 150, f"api/ui.py regrew to {ui_loc} LOC"
    assert docs_loc <= 100, f"api/docs_ui.py regrew to {docs_loc} LOC"

    oversized: list[str] = []
    high_cc: list[str] = []
    for f in _iter_src_py_files():
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                flen = (node.end_lineno or node.lineno) - node.lineno + 1
                if flen > 400:
                    oversized.append(f"{f.relative_to(_REPO_ROOT)}:{node.name} ({flen} LOC)")
                cc = _cyclomatic_complexity(node)
                if cc > 85:
                    high_cc.append(f"{f.relative_to(_REPO_ROOT)}:{node.name} (CC={cc})")
    assert oversized == [], f"Functions exceeding 400 LOC ratchet: {oversized}"
    assert high_cc == [], f"Functions exceeding CC=85 complexity ratchet: {high_cc}"


def test_packaged_templates_load_and_render_without_leaked_tokens() -> None:
    """All Workbench & User Guide templates load cleanly and substitute every __TOKEN__."""
    for name in (
        "workbench.html",
        "workbench.css",
        "workbench.js",
        "guide.html",
        "guide.css",
        "guide_tab.html",
    ):
        assert len(load_template(name)) > 100

    for lang in ("en", "el"):
        rendered = render_etek_workbench_html("https://example.cy", ui_lang=lang)
        leaked = set(_PLACEHOLDER_RE.findall(rendered))
        assert leaked == set(), f"Unreplaced template tokens in workbench ({lang}): {leaked}"

    guide = render_user_guide_html("https://example.cy")
    assert set(_PLACEHOLDER_RE.findall(guide)) == set()
    guide_tab = render_workbench_guide_tab_html()
    assert set(_PLACEHOLDER_RE.findall(guide_tab)) == set()


def test_packaged_render_templates_load_and_render_without_leaked_tokens(tmp_path: Path) -> None:
    """All print-ready HTML/CSS report templates load and leave zero __TOKEN__ placeholders."""
    from cyelec.pipeline import run_phase2_pipeline
    from cyelec.render.calc_report import render_calc_report_html
    from cyelec.render.eac_forms import render_eac_application_form_html
    from cyelec.render.eac_pack import render_eac_submission_pack_html
    from cyelec.render.sld import render_sld_svg
    from cyelec.render.templates import load_template as load_render_template
    from cyelec.synth.benchmarks import build_cyprus_3bed_model

    for name in (
        "eac_application_form.html",
        "eac_application_form.css",
        "calc_report.html",
        "calc_report.css",
        "eac_submission_pack.html",
        "eac_submission_pack.css",
    ):
        assert len(load_render_template(name)) > 100

    res = run_phase2_pipeline(build_cyprus_3bed_model())
    for label, html_out in (
        ("eac_application_form", render_eac_application_form_html(res)),
        ("calc_report", render_calc_report_html(res.design)),
        (
            "eac_submission_pack",
            render_eac_submission_pack_html(
                res,
                sld_svg=render_sld_svg(res.design),
                deliverables={"dummy": tmp_path / "missing.pdf"},
            ),
        ),
    ):
        leaked = set(_PLACEHOLDER_RE.findall(html_out))
        assert leaked == set(), f"Unreplaced template tokens in {label}: {leaked}"


def test_workbench_js_passes_node_syntax_check() -> None:
    """Extracted workbench.js must remain valid JavaScript syntax under Node.js."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node binary not installed in environment")
    js_path = _SRC_ROOT / "api" / "templates" / "workbench.js"
    proc = subprocess.run(
        [node, "--check", str(js_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"workbench.js syntax error:\n{proc.stderr}"


def test_decomposed_api_registers_all_expected_routes(tmp_path: Path) -> None:
    """Router decomposition must preserve every core UI, solve, workspace, auth & billing route."""
    app = create_app(db_path=tmp_path / "routes.db")
    registered = set(_iter_app_routes(app))
    expected_core = {
        ("/", ("GET",)),
        ("/el", ("GET",)),
        ("/guide", ("GET",)),
        ("/el/guide", ("GET",)),
        ("/health", ("GET",)),
        ("/v1/standards", ("GET",)),
        ("/v1/solve/building", ("POST",)),
        ("/v1/solve/benchmark/{template}", ("POST",)),
        ("/v1/solve/benchmark/{template}/bundle", ("POST",)),
        ("/v1/dxf/layers", ("POST",)),
        ("/v1/solve/dxf", ("POST",)),
        ("/v1/solve/dxf/preview", ("POST",)),
        ("/v1/solve/dxf/bundle", ("POST",)),
        ("/v1/solve/dxf/multifloor", ("POST",)),
        ("/v1/solve/dxf/multifloor/bundle", ("POST",)),
        ("/v1/tenants/me", ("GET",)),
        ("/v1/tenants/me", ("PUT",)),
        ("/v1/tenants/me", ("DELETE",)),
        ("/v1/tenants/me/api-key", ("POST",)),
        ("/v1/tenants/me/export", ("GET",)),
        ("/v1/projects", ("GET",)),
        ("/v1/projects", ("POST",)),
        ("/v1/projects/{project_id}", ("GET",)),
        ("/v1/projects/{project_id}", ("DELETE",)),
        ("/v1/projects/{project_id}/revisions", ("GET",)),
        ("/v1/projects/{project_id}/revisions", ("POST",)),
        ("/v1/projects/{project_id}/revisions/dxf", ("POST",)),
        ("/v1/projects/{project_id}/diff/{rev_a}/{rev_b}", ("GET",)),
        ("/v1/auth/register", ("POST",)),
        ("/v1/auth/login", ("POST",)),
        ("/v1/auth/google", ("POST",)),
        ("/v1/auth/me", ("GET",)),
        ("/v1/team/members", ("GET",)),
        ("/v1/team/members", ("POST",)),
        ("/v1/billing/plans", ("GET",)),
        ("/v1/billing/subscription", ("GET",)),
        ("/v1/billing/checkout", ("POST",)),
        ("/v1/billing/portal", ("POST",)),
        ("/v1/billing/webhook", ("POST",)),
        ("/v1/admin/tenants/{tenant_id}/api-key", ("POST",)),
        ("/v1/admin/tenants/{tenant_id}/plan", ("PUT",)),
        ("/v1/admin/users/reset-password", ("POST",)),
    }
    missing = expected_core - registered
    assert not missing, f"Missing expected routes after decomposition: {sorted(missing)}"
