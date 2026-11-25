"""Contract & integration tests for GCP FirestoreWorkspaceStore + GCS gzipped blob adapter."""

from __future__ import annotations

import copy
import gzip
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cyelec.api.app import create_app
from cyelec.models.electrical import BoqRateOverrides, ReportLanguage, TenantBranding
from cyelec.saas.firestore_store import (
    BlobStoreProtocol,
    FirestoreWorkspaceStore,
    build_workspace_store_from_env,
)
from cyelec.saas.store import WorkspaceStoreProtocol


# ----------------------------------------------------------------------
# Deterministic In-Memory Fakes matching google-cloud-firestore & GCS API
# ----------------------------------------------------------------------
class _FakeSnapshot:
    def __init__(self, ref: _FakeDocRef, data: dict[str, Any] | None) -> None:
        self.reference = ref
        self._data = copy.deepcopy(data) if data is not None else None

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._data)


class _FakeDocRef:
    def __init__(self, db: _FakeFirestoreClient, path: str) -> None:
        self._db = db
        self.path = path

    def collection(self, name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self._db, f"{self.path}/{name}")

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self, self._db.docs.get(self.path))

    def set(self, data: dict[str, Any], *, merge: bool = False) -> None:
        if merge and self.path in self._db.docs:
            merged = copy.deepcopy(self._db.docs[self.path])
            merged.update(copy.deepcopy(data))
            self._db.docs[self.path] = merged
        else:
            self._db.docs[self.path] = copy.deepcopy(data)

    def delete(self) -> None:
        self._db.docs.pop(self.path, None)


class _FakeQuery:
    def __init__(
        self,
        col: _FakeCollectionRef,
        field: str,
        op: str,
        value: Any,
    ) -> None:
        self._col = col
        self._field = field
        self._op = op
        self._value = value

    def stream(self) -> list[_FakeSnapshot]:
        out: list[_FakeSnapshot] = []
        for snap in self._col.stream():
            d = snap.to_dict() or {}
            if self._op == "==" and d.get(self._field) == self._value:
                out.append(snap)
        return out


class _FakeCollectionRef:
    def __init__(self, db: _FakeFirestoreClient, path: str) -> None:
        self._db = db
        self.path = path

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._db, f"{self.path}/{doc_id}")

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        return _FakeQuery(self, field, op, value)

    def stream(self) -> list[_FakeSnapshot]:
        prefix = f"{self.path}/"
        snaps: list[_FakeSnapshot] = []
        for k, v in sorted(self._db.docs.items()):
            if k.startswith(prefix) and "/" not in k[len(prefix) :]:
                snaps.append(_FakeSnapshot(_FakeDocRef(self._db, k), v))
        return snaps


class _FakeBatch:
    def __init__(self) -> None:
        self._ops: list[tuple[_FakeDocRef, dict[str, Any], bool]] = []

    def set(self, ref: _FakeDocRef, data: dict[str, Any], *, merge: bool = False) -> None:
        self._ops.append((ref, data, merge))

    def commit(self) -> None:
        for ref, data, merge in self._ops:
            ref.set(data, merge=merge)
        self._ops.clear()


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self, name)

    def batch(self) -> _FakeBatch:
        return _FakeBatch()


class _InMemoryGzipBlobStore:
    """In-memory gzipped BlobStoreProtocol verifying real gzip compression & prefix erasure."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put_gzip_json(self, path: str, json_text: str) -> None:
        self.blobs[path] = gzip.compress(json_text.encode("utf-8"), compresslevel=6)

    def get_gzip_json(self, path: str) -> str | None:
        raw = self.blobs.get(path)
        if raw is None:
            return None
        return gzip.decompress(raw).decode("utf-8")

    def delete_blob(self, path: str) -> None:
        self.blobs.pop(path, None)

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.blobs if k.startswith(prefix)]
        for k in keys:
            del self.blobs[k]
        return len(keys)


def test_firestore_store_protocol_full_saas_and_gdpr_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_db = _FakeFirestoreClient()
    fake_blobs = _InMemoryGzipBlobStore()
    assert isinstance(fake_blobs, BlobStoreProtocol)

    store = FirestoreWorkspaceStore(
        database="cyelec-prod",
        firestore_client=fake_db,
        blob_store=fake_blobs,
    )
    assert isinstance(store, WorkspaceStoreProtocol)

    app = create_app(store=store)
    client = TestClient(app)

    # 1. /health reports store_backend == "firestore"
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["store_backend"] == "firestore"

    # 2. Admin key provisioning + strict CYELEC_AUTH_REQUIRED=1
    monkeypatch.setenv("CYELEC_ADMIN_KEY", "gcp-secret-manager-admin-key")
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "1")

    key_a = client.post(
        "/v1/admin/tenants/firm-limassol/api-key",
        headers={"X-Admin-Key": "gcp-secret-manager-admin-key"},
    ).json()["api_key"]
    key_b = client.post(
        "/v1/admin/tenants/firm-paphos/api-key",
        headers={"X-Admin-Key": "gcp-secret-manager-admin-key"},
    ).json()["api_key"]

    ha = {"X-API-Key": key_a}
    hb = {"X-API-Key": key_b}

    # 3. Tenant A saves branding + custom BOQ rate card & creates project + 2 revisions
    upd = client.put(
        "/v1/tenants/me",
        headers=ha,
        json={
            "display_name": "Limassol Cloud MEP",
            "report_language": ReportLanguage.BILINGUAL.value,
            "branding": TenantBranding(
                firm_name="Limassol Cloud MEP",
                engineer_name="K. Nicolaou",
                etek_reg_no="ETEK-E-9001",
            ).model_dump(),
            "boq_rate_overrides": BoqRateOverrides(
                switchgear_eur={"main_board_3p": 610.0},
                vat_rate=0.05,
            ).model_dump(),
        },
    )
    assert upd.status_code == 200, upd.text

    proj_a = client.post("/v1/projects", headers=ha, json={"name": "Marina Tower A"}).json()[
        "project_id"
    ]
    proj_b = client.post("/v1/projects", headers=hb, json={"name": "Paphos Villa B"}).json()[
        "project_id"
    ]

    r1 = client.post(
        f"/v1/projects/{proj_a}/revisions",
        headers=ha,
        json={
            "benchmark_template": "apartment_3bed",
            "overrides": {
                "circuit_overrides": {"C3": {"circuit_id": "C3", "grouping_count": 3}},
            },
            "notes": "Rev 1 baseline",
        },
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["result"]["design"]["boq"]["vat_rate"] == 0.05
    assert r1.json()["result"]["design"]["branding"]["etek_reg_no"] == "ETEK-E-9001"

    # Verify gzipped GCS blob was actually written & compressed
    assert len(fake_blobs.blobs) == 1
    blob_key, blob_bytes = next(iter(fake_blobs.blobs.items()))
    assert blob_key.startswith(f"tenants/firm-limassol/projects/{proj_a}/revisions/")
    assert blob_bytes[:2] == b"\x1f\x8b"  # gzip magic header

    r2 = client.post(
        f"/v1/projects/{proj_a}/revisions",
        headers=ha,
        json={
            "benchmark_template": "apartment_3bed",
            "overrides": {"pv_capacity_kva": 6.0},
            "carry_forward_overrides": True,
            "notes": "Rev 2 added 6 kVA PV",
        },
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["summary"]["revision_number"] == 2
    assert r2.json()["overrides"]["circuit_overrides"]["C3"]["grouping_count"] == 3
    assert len(fake_blobs.blobs) == 2

    # Revision diff across GCS-hydrated revisions works identically to SQLite
    diff = client.get(f"/v1/projects/{proj_a}/diff/1/2", headers=ha)
    assert diff.status_code == 200, diff.text
    assert diff.json()["rev_a"] == 1 and diff.json()["rev_b"] == 2

    # Cross-tenant isolation (Tenant B cannot read or delete Tenant A's project)
    assert client.get(f"/v1/projects/{proj_a}", headers=hb).status_code == 404
    assert client.delete(f"/v1/projects/{proj_a}", headers=hb).status_code == 404

    # 4. GDPR Article 20 portable export (hydrated from Firestore + GCS, zero secret leakage)
    exp = client.get("/v1/tenants/me/export", headers=ha)
    assert exp.status_code == 200, exp.text
    bundle = exp.json()
    assert bundle["tenant"]["tenant_id"] == "firm-limassol"
    assert len(bundle["revisions_by_project"][proj_a]) == 2
    assert bundle["usage"]["total_events"] == 2
    assert key_a not in exp.text
    assert "api_key_hash" not in exp.text

    # 5. GDPR Article 17 cascade erasure deletes BOTH Firestore docs AND GCS blobs!
    del_me = client.delete("/v1/tenants/me", headers=ha)
    assert del_me.status_code == 200
    assert del_me.json() == {"deleted": True, "tenant_id": "firm-limassol"}
    assert fake_blobs.blobs == {}  # all gzipped GCS blobs erased!
    assert client.get("/v1/tenants/me", headers=ha).status_code == 401  # API key revoked!

    # Tenant B remains untouched
    assert client.get(f"/v1/projects/{proj_b}", headers=hb).status_code == 200


def test_build_workspace_store_from_env_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYELEC_STORE_BACKEND", "invalid_db")
    with pytest.raises(ValueError, match="Unsupported CYELEC_STORE_BACKEND"):
        build_workspace_store_from_env()
