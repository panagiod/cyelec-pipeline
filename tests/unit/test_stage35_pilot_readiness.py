"""Stage 3.5 Closed Pilot Beta readiness tests covering WS1–WS5 end-to-end."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import ezdxf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from cyelec.api.app import create_app
from cyelec.ingest.dxf_ingest import ingest_dxf
from cyelec.models.building import BenchmarkTemplate
from cyelec.models.electrical import DesignOverrides, TenantBranding
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.eac_forms import render_eac_application_form_html
from cyelec.saas.firestore_store import FirestoreWorkspaceStore
from cyelec.synth.benchmarks import generate_benchmark_dxf


def _make_simple_floor_dxf(path: Path, custom_layer: str, label: str) -> None:
    """Create a tiny valid architectural DXF with a distinctive custom furniture layer."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4  # Millimeters
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (6000, 0), (6000, 5000), (0, 5000)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    msp.add_lwpolyline(
        [(6000, 0), (10000, 0), (10000, 5000), (6000, 5000)],
        close=True,
        dxfattribs={"layer": "A-ROOM"},
    )
    msp.add_text(label, dxfattribs={"layer": "A-TEXT", "insert": (3000, 2500)})
    msp.add_text("BEDROOM", dxfattribs={"layer": "A-TEXT", "insert": (8000, 2500)})
    msp.add_lwpolyline(
        [(1000, 1000), (2200, 1000), (2200, 1800), (1000, 1800)],
        close=True,
        dxfattribs={"layer": custom_layer},
    )
    doc.saveas(path)


# ---------------------------------------------------------------------------
# WS1: Native .dwg upload endpoints & multi-floor architectural underlay preservation
# ---------------------------------------------------------------------------


def test_ws1_dwg_layers_multifloor_and_underlay_preservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gf_dxf = tmp_path / "gf_converted.dxf"
    f1_dxf = tmp_path / "f1_converted.dxf"
    _make_simple_floor_dxf(gf_dxf, "A-CUSTOM-GF-SOFA", "LIVING ROOM")
    _make_simple_floor_dxf(f1_dxf, "A-CUSTOM-1F-BED", "MASTER BEDROOM")

    def fake_convert_dwg(
        dwg_path: Path,
        out_dir: Path,
        *,
        timeout_s: float = 60.0,
    ) -> tuple[Path, object]:
        from cyelec.models.electrical import ReviewFlag, ReviewSeverity

        _ = timeout_s
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{dwg_path.stem}.dxf"
        src = f1_dxf if b"1F" in dwg_path.read_bytes() else gf_dxf
        out.write_bytes(src.read_bytes())
        return (
            out,
            ReviewFlag(
                code="DWG_CONVERTED_TO_DXF",
                severity=ReviewSeverity.INFO,
                category="geometry",
                message="Mock DWG converted via dwg2dxf.",
            ),
        )

    monkeypatch.setattr(
        "cyelec.ingest.dwg_bridge.find_dwg_converter",
        lambda: ("dwg2dxf", "/usr/local/bin/dwg2dxf"),
    )
    monkeypatch.setattr("cyelec.ingest.dwg_bridge.convert_dwg_to_dxf", fake_convert_dwg)
    monkeypatch.setattr("cyelec.pipeline.convert_dwg_to_dxf", fake_convert_dwg)

    app = create_app(db_path=tmp_path / "ws1.db")
    client = TestClient(app)

    # 1. /health reports dwg_available=True
    health = client.get("/health").json()
    assert health["dwg_available"] is True

    # 2. /v1/dxf/layers accepts native .dwg upload and inspects converted layers
    layer_resp = client.post(
        "/v1/dxf/layers",
        files={"file": ("arch_gf.dwg", b"AC1032FAKE_DWG_BYTES", "application/acad")},
    )
    assert layer_resp.status_code == 200
    layer_names = {row["layer_name"] for row in layer_resp.json()["layers"]}
    assert "A-ROOM" in layer_names
    assert "A-CUSTOM-GF-SOFA" in layer_names

    # 3. /v1/solve/dxf/multifloor accepts 2 native .dwg floorplans
    mf_resp = client.post(
        "/v1/solve/dxf/multifloor",
        files=[
            ("files", ("floor_gf.dwg", b"AC1032_GF", "application/acad")),
            ("files", ("floor_1f.dwg", b"AC1032_1F", "application/acad")),
        ],
        data={"floor_names_json": json.dumps(["GF", "1F"])},
    )
    assert mf_resp.status_code == 200
    mf_data = mf_resp.json()
    assert len(mf_data["result"]["design"]["sub_mains"]) == 2

    # 4. /v1/solve/dxf/multifloor/bundle preserves REAL per-floor architectural DXFs & sheets
    zip_resp = client.post(
        "/v1/solve/dxf/multifloor/bundle",
        files=[
            ("files", ("floor_gf.dwg", b"AC1032_GF", "application/acad")),
            ("files", ("floor_1f.dwg", b"AC1032_1F", "application/acad")),
        ],
        data={"floor_names_json": json.dumps(["GF", "1F"])},
    )
    assert zip_resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(zip_resp.content))
    dxf_bytes = zf.read("electrical_layout.dxf")
    layout_path = tmp_path / "mf_layout.dxf"
    layout_path.write_bytes(dxf_bytes)
    doc = ezdxf.readfile(str(layout_path))

    # Original per-floor architectural blocks imported via ezdxf Importer
    arch_blocks = [b.name for b in doc.blocks if b.name.startswith("ARCH_FLOOR_")]
    assert len(arch_blocks) == 2
    all_blk_layers = {
        e.dxf.layer
        for bname in arch_blocks
        for e in doc.blocks.get(bname)
    }
    assert "A-CUSTOM-GF-SOFA" in all_blk_layers
    assert "A-CUSTOM-1F-BED" in all_blk_layers

    # Dedicated per-floor print sheets generated alongside overview
    tab_names = [lyt.name for lyt in doc.layouts if lyt.name != "Model"]
    assert any(t.startswith("EAC-GF-") for t in tab_names)
    assert any(t.startswith("EAC-1F-") for t in tab_names)


# ---------------------------------------------------------------------------
# WS2: Directly stampable AutoCAD CAD, exact true-scale VP, SLD sheet, SPLINE & XREF
# ---------------------------------------------------------------------------


def test_ws2_stampable_cad_blocks_exact_scale_zero_overlaps_and_sld_sheet(
    tmp_path: Path,
) -> None:
    apt_dxf = tmp_path / "apt.dxf"
    generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED, out_dxf=apt_dxf)
    p2 = run_phase2_pipeline(apt_dxf)
    outputs = export_phase3_deliverables(p2, tmp_path / "pack")

    doc = ezdxf.readfile(str(outputs["layout_dxf"]))
    assert len(doc.audit().errors) == 0
    msp = doc.modelspace()

    # 1. Named IEC 60617 BLOCK definitions & wall-rotated INSERT references
    for blk_name in (
        "CYELEC_LUMINAIRE",
        "CYELEC_SWITCH",
        "CYELEC_SOCKET_13A",
        "CYELEC_AC_ISOL",
        "CYELEC_APPL",
    ):
        assert blk_name in doc.blocks
    inserts = list(msp.query("INSERT"))
    assert len(inserts) >= 40
    rotated = [ins for ins in inserts if abs(float(ins.dxf.rotation)) > 0.5]
    assert len(rotated) >= 10

    # 2. Split conduit layers (Lighting / Power / Dedicated) populated with parallel routes
    cond_layers = {
        e.dxf.layer for e in msp.query("LINE") if e.dxf.layer.startswith("E-COND-")
    }
    assert cond_layers == {"E-COND-LITE", "E-COND-PWR", "E-COND-DED"}

    # 3. Zero overlapping electrical TEXT label bounding boxes
    boxes: list[tuple[float, float, float, float]] = []
    for txt in msp.query("TEXT"):
        if not txt.dxf.layer.startswith("E-"):
            continue
        x, y = float(txt.dxf.insert.x), float(txt.dxf.insert.y)
        h = float(txt.dxf.height)
        w = len(str(txt.dxf.text)) * h * 0.65
        boxes.append((x, y, x + w, y + h))
    assert len(boxes) >= 40
    overlaps = 0
    for i in range(len(boxes)):
        ax1, ay1, ax2, ay2 = boxes[i]
        for j in range(i + 1, len(boxes)):
            bx1, by1, bx2, by2 = boxes[j]
            if not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1):
                overlaps += 1
    assert overlaps == 0

    # 4. Paper-Space-only sheet border/titleblock when real architect underlay preserved
    assert not any(
        e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "E-TITLE" for e in msp
    )

    # 5. Exact true-scale 1:50.0000 viewport on EAC-A3-1-50 (0.00% distortion)
    assert "EAC-A3-1-50" in doc.layouts
    psp = doc.layouts.get("EAC-A3-1-50")
    active_vps = [v for v in psp.query("VIEWPORT") if v.dxf.id != 1]
    assert len(active_vps) == 1
    actual_scale = float(active_vps[0].dxf.view_height) / float(active_vps[0].dxf.height)
    assert actual_scale == pytest.approx(50.0, abs=1e-4)

    # 6. single_line_diagram.dxf includes ISO Paper-Space tab EAC-SLD-A3 with VIEWPORT
    sld_doc = ezdxf.readfile(str(outputs["sld_dxf"]))
    assert len(sld_doc.audit().errors) == 0
    assert "EAC-SLD-A3" in sld_doc.layouts
    sld_vps = [v for v in sld_doc.layouts.get("EAC-SLD-A3").query("VIEWPORT") if v.dxf.id != 1]
    assert len(sld_vps) == 1


def test_ws2_spline_geometry_ingestion_and_xref_warning(tmp_path: Path) -> None:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    # Closed SPLINE room boundary + curved SPLINE wall
    pts = [(0.0, 0.0), (5000.0, 0.0), (5000.0, 4000.0), (0.0, 4000.0), (0.0, 0.0)]
    msp.add_spline(fit_points=pts, dxfattribs={"layer": "A-ROOM"})
    msp.add_spline(
        fit_points=[(0.0, 0.0), (2500.0, 400.0), (5000.0, 0.0)],
        dxfattribs={"layer": "A-WALL"},
    )
    msp.add_text("LIVING ROOM", dxfattribs={"layer": "A-TEXT", "insert": (2500.0, 2000.0)})

    # Simulate an unresolved AutoCAD external XREF block attachment
    doc.add_xref_def(filename="site_grid_xref.dwg", name="SITE_GRID_XREF")

    dxf_path = tmp_path / "spline_xref.dxf"
    doc.saveas(dxf_path)

    building, flags = ingest_dxf(dxf_path)
    assert len(building.rooms) >= 1
    assert 15.0 <= building.total_floor_area_m2 <= 32.0
    assert len(building.walls) >= 1
    assert any(f.code == "DXF_UNRESOLVED_XREFS" for f in flags)


# ---------------------------------------------------------------------------
# WS3: Official Cyprus EAC (ΑΗΚ) Ε-ΔΔ-516 / Ε-ΔΔ-514 & ΗΜΥ 58 (18-1) Test Pack
# ---------------------------------------------------------------------------


def test_ws3_eac_edd516_hmy58_html_and_5_sheet_excel(tmp_path: Path) -> None:
    bldg = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)
    p2 = run_phase2_pipeline(
        bldg,
        overrides=DesignOverrides(
            prospective_fault_current_ka=8.5,
            branding=TenantBranding(
                firm_name="Nicosia MEP Associates",
                engineer_name="E. Georgiou",
                etek_reg_no="ETEK-E-3310",
            ),
        ),
    )
    outputs = export_phase3_deliverables(p2, tmp_path / "eac_pack")

    # 1. Official bilingual Ε-ΔΔ-516 + Ε-ΔΔ-514 + ΗΜΥ 58 HTML
    html = render_eac_application_form_html(p2)
    assert "Ε-ΔΔ-516" in html
    assert "Ε-ΔΔ-514" in html
    assert "ΗΜΥ 58" in html
    assert "8.5 kA" in html
    assert "amc-lefkosia@eac.com.cy" in html
    assert "amc-lemesos@eac.com.cy" in html
    assert "amc-larnaca@eac.com.cy" in html
    assert "amc-pafos@eac.com.cy" in html
    assert "ETEK-E-3310" in html

    # 2. 5-sheet Excel workbook with dedicated HMY 58 Test Schedule
    wb = load_workbook(str(outputs["xlsx"]), data_only=False)
    assert wb.sheetnames == [
        "Load Schedule",
        "EAC & Review Flags",
        "Bill of Quantities",
        "EAC Application",
        "HMY 58 Test Schedule",
    ]
    ws5 = wb["HMY 58 Test Schedule"]
    # Header + at least one row per circuit
    assert ws5.max_row >= len(p2.design.circuits) + 4
    ws5_text = " ".join(
        str(ws5.cell(row=r, column=c).value or "")
        for r in range(1, ws5.max_row + 1)
        for c in range(1, ws5.max_column + 1)
    )
    assert "HMY 58 (18-1)" in ws5_text
    assert p2.design.circuits[0].spec.circuit_id in ws5_text


# ---------------------------------------------------------------------------
# WS4: Closed Pilot Invite Gate, Login-First UI, Password Change/Reset & Firestore Parity
# ---------------------------------------------------------------------------


def test_ws4_pilot_invite_gate_login_first_ui_and_password_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "1")
    monkeypatch.setenv("CYELEC_PILOT_INVITE_CODE", "CY-ETEK-PILOT-2026")
    monkeypatch.setenv("CYELEC_JWT_SECRET", "stage35-test-jwt-secret-key-32bytes!")
    monkeypatch.setenv("CYELEC_ADMIN_KEY", "stage35-super-admin-secret-key")
    monkeypatch.setenv("CYELEC_DISABLE_OPENAPI", "1")

    app = create_app(db_path=tmp_path / "ws4.db")
    client = TestClient(app)

    # 1. /health and / expose auth_required & invite_code_required cleanly
    health = client.get("/health").json()
    assert health["auth_required"] is True
    assert health["invite_code_required"] is True

    home = client.get("/")
    assert home.status_code == 200
    assert "const AUTH_REQUIRED = true;" in home.text
    assert "const INVITE_CODE_REQUIRED = true;" in home.text
    assert 'id="authModal"' in home.text
    assert 'href="/docs"' not in home.text

    # 2. Invite code gate rejects missing/wrong invite code (403)
    bad_reg = client.post(
        "/v1/auth/register",
        json={
            "email": "owner@pilotmep.cy",
            "password": "InitialPassword123!",
            "display_name": "Elena Ioannou",
            "firm_name": "Pilot MEP Cyprus",
            "invite_code": "WRONG-CODE",
        },
    )
    assert bad_reg.status_code == 403

    # 3. Valid invite code registers firm & auto-grants 'pilot' tier (100 solves, 3 seats)
    good_reg = client.post(
        "/v1/auth/register",
        json={
            "email": "owner@pilotmep.cy",
            "password": "InitialPassword123!",
            "display_name": "Elena Ioannou",
            "firm_name": "Pilot MEP Cyprus",
            "invite_code": "CY-ETEK-PILOT-2026",
        },
    )
    assert good_reg.status_code == 201
    owner_data = good_reg.json()
    owner_jwt = owner_data["access_token"]
    assert owner_data["quota"]["plan_id"] == "pilot"
    assert owner_data["quota"]["solves_limit"] == 100

    # 4. Owner adds Engineer and Viewer seats
    eng_resp = client.post(
        "/v1/team/members",
        headers={"Authorization": f"Bearer {owner_jwt}"},
        json={
            "email": "eng@pilotmep.cy",
            "display_name": "Andreas Eng",
            "password": "EngPassword123!",
            "role": "engineer",
        },
    )
    assert eng_resp.status_code == 201
    eng_uid = eng_resp.json()["user_id"]

    vw_resp = client.post(
        "/v1/team/members",
        headers={"Authorization": f"Bearer {owner_jwt}"},
        json={
            "email": "viewer@pilotmep.cy",
            "display_name": "Maria Viewer",
            "password": "ViewerPassword123!",
            "role": "viewer",
        },
    )
    assert vw_resp.status_code == 201

    # 5. Viewer can change OWN password via /v1/auth/change-password, but stays read-only elsewhere
    vw_jwt = client.post(
        "/v1/auth/login",
        json={"email": "viewer@pilotmep.cy", "password": "ViewerPassword123!"},
    ).json()["access_token"]

    assert (
        client.post(
            "/v1/projects",
            headers={"Authorization": f"Bearer {vw_jwt}"},
            json={"name": "Blocked Project"},
        ).status_code
        == 403
    )

    wrong_cur = client.post(
        "/v1/auth/change-password",
        headers={"Authorization": f"Bearer {vw_jwt}"},
        json={"current_password": "NotThePassword!", "new_password": "NewViewerPass99!"},
    )
    assert wrong_cur.status_code == 401

    chg_ok = client.post(
        "/v1/auth/change-password",
        headers={"Authorization": f"Bearer {vw_jwt}"},
        json={"current_password": "ViewerPassword123!", "new_password": "NewViewerPass99!"},
    )
    assert chg_ok.status_code == 200
    assert chg_ok.json()["updated"] is True
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": "viewer@pilotmep.cy", "password": "ViewerPassword123!"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": "viewer@pilotmep.cy", "password": "NewViewerPass99!"},
        ).status_code
        == 200
    )

    # 6. Engineer is BLOCKED from resetting colleagues; Owner succeeds
    eng_jwt = client.post(
        "/v1/auth/login",
        json={"email": "eng@pilotmep.cy", "password": "EngPassword123!"},
    ).json()["access_token"]
    assert (
        client.post(
            f"/v1/team/members/{eng_uid}/reset-password",
            headers={"Authorization": f"Bearer {eng_jwt}"},
            json={"new_password": "TempEngPass88!"},
        ).status_code
        == 403
    )
    owner_reset = client.post(
        f"/v1/team/members/{eng_uid}/reset-password",
        headers={"Authorization": f"Bearer {owner_jwt}"},
        json={"new_password": "TempEngPass88!"},
    )
    assert owner_reset.status_code == 200
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": "eng@pilotmep.cy", "password": "TempEngPass88!"},
        ).status_code
        == 200
    )

    # 7. Admin recovery endpoint requires X-Admin-Key and recovers locked-out Owner
    assert (
        client.post(
            "/v1/admin/users/reset-password",
            json={"email": "owner@pilotmep.cy", "new_password": "RecoveredOwner77!"},
        ).status_code
        == 401
    )
    adm_reset = client.post(
        "/v1/admin/users/reset-password",
        headers={"X-Admin-Key": "stage35-super-admin-secret-key"},
        json={"email": "owner@pilotmep.cy", "new_password": "RecoveredOwner77!"},
    )
    assert adm_reset.status_code == 200
    assert (
        client.post(
            "/v1/auth/login",
            json={"email": "owner@pilotmep.cy", "password": "RecoveredOwner77!"},
        ).status_code
        == 200
    )


def test_ws4_firestore_store_update_user_password_parity() -> None:
    """Verify FirestoreWorkspaceStore.update_user_password merges new password hash & salt."""
    mock_client = MagicMock()
    mock_user_doc = MagicMock()
    mock_snap = MagicMock()
    mock_snap.exists = True
    mock_snap.to_dict.return_value = {
        "user_id": "usr_1",
        "tenant_id": "tnt_1",
        "email": "pilot@cy.com",
        "display_name": "Pilot Eng",
        "role": "owner",
        "auth_provider": "password",
        "password_hash": "old_hash",
        "password_salt": "old_salt",
        "created_at": "2026-11-26T00:00:00Z",
        "updated_at": "2026-11-26T00:00:00Z",
    }
    mock_user_doc.get.return_value = mock_snap
    (
        mock_client.collection.return_value.document.return_value
        .collection.return_value.document.return_value
    ) = mock_user_doc

    store = FirestoreWorkspaceStore(firestore_client=mock_client, blob_store=MagicMock())
    updated = store.update_user_password("tnt_1", "usr_1", "BrandNewPass123!")
    assert updated is not None
    assert updated.email == "pilot@cy.com"
    mock_user_doc.set.assert_called_once()
    update_payload = mock_user_doc.set.call_args[0][0]
    assert update_payload["password_hash"] != "old_hash"
    assert update_payload["password_salt"] != "old_salt"
    assert mock_user_doc.set.call_args[1].get("merge") is True


# ---------------------------------------------------------------------------
# WS5: BS 7671 Table 4C1 Corridor Cg Grouping Physics
# ---------------------------------------------------------------------------


def test_ws5_corridor_cg_grouping_warning_and_derating_propagation() -> None:
    bldg = generate_benchmark_dxf(BenchmarkTemplate.APARTMENT_3BED)

    baseline = run_phase2_pipeline(bldg)
    grouped = run_phase2_pipeline(
        bldg,
        overrides=DesignOverrides(apply_corridor_grouping=True),
    )

    peak_corridor = max(
        rc.max_parallel_circuits_on_trunk for rc in baseline.spatial.routed_circuits
    )
    assert peak_corridor >= 4
    assert any(
        f.code == "CONDUIT_CORRIDOR_GROUPING_CHECK" for f in baseline.design.review_flags
    )
    assert any(
        f.code == "CORRIDOR_CG_GROUPING_APPLIED" for f in grouped.design.review_flags
    )

    # Co-routed circuits receive BS 7671 Table 4C1 Cg < 1.0 and remain thermally compliant
    base_by_id = {c.spec.circuit_id: c for c in baseline.design.circuits}
    grp_by_id = {c.spec.circuit_id: c for c in grouped.design.circuits}
    assert any(c.cg < 1.0 for c in grouped.design.circuits)
    for cid, g_circ in grp_by_id.items():
        assert g_circ.overall_pass is True
        assert g_circ.cable_mm2 >= base_by_id[cid].cable_mm2
