"""Stage 3 Commercial B2B SaaS tests: JWT, RBAC, Quotas, Stripe, EAC Forms, ISO A3/A1 CAD."""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import hmac
import importlib
import json
import time
from pathlib import Path
from typing import Any

import ezdxf
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from cyelec.api.app import create_app
from cyelec.models.electrical import DesignOverrides, TenantBranding
from cyelec.pipeline import export_phase3_deliverables, run_phase2_pipeline
from cyelec.render.eac_forms import build_eac_application_rows, render_eac_application_form_html
from cyelec.saas import auth as auth_mod
from cyelec.saas import billing as billing_mod
from cyelec.saas.auth import (
    Role,
    create_access_token,
    get_jwt_secret,
    hash_password,
    verify_access_token,
    verify_password,
)
from cyelec.saas.billing import (
    PLAN_CATALOG,
    resolve_plan_spec,
    verify_stripe_signature,
)
from cyelec.saas.budget_gate import create_budget_gate_app
from cyelec.saas.firestore_store import FirestoreWorkspaceStore
from cyelec.saas.store import WorkspaceStore
from cyelec.synth.benchmarks import BenchmarkTemplate, generate_benchmark_dxf
from cyelec.synth.sample_plan import generate_cyprus_3bed_dxf


def _make_stripe_sig(payload: bytes, secret: str, ts: int | None = None) -> str:
    t = int(time.time()) if ts is None else ts
    signed = f"{t}.".encode("ascii") + payload
    v1 = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


# ----------------------------------------------------------------------
# 1. Password Hashing, HS256 JWT Security & Algorithm Confusion Rejection
# ----------------------------------------------------------------------
def test_password_hashing_and_jwt_security(monkeypatch: pytest.MonkeyPatch) -> None:
    h, salt = hash_password("CyElec2026!Secure")
    assert verify_password("CyElec2026!Secure", h, salt) is True
    assert verify_password("WrongPassword123", h, salt) is False
    with pytest.raises(ValueError, match="at least 8"):
        hash_password("short")

    # Domain-separated derivation from CYELEC_ADMIN_KEY survives cold starts
    monkeypatch.delenv("CYELEC_JWT_SECRET", raising=False)
    monkeypatch.setenv("CYELEC_ADMIN_KEY", "prod-admin-key-xyz")
    derived_1 = get_jwt_secret()
    derived_2 = get_jwt_secret()
    assert derived_1 == derived_2
    assert derived_1 != b"prod-admin-key-xyz"

    tok = create_access_token(
        user_id="usr_1",
        tenant_id="nicosia-mep",
        email="lead@nicosia-mep.cy",
        role=Role.OWNER,
        ttl_seconds=300,
    )
    principal = verify_access_token(tok)
    assert principal is not None
    assert principal.user_id == "usr_1"
    assert principal.tenant_id == "nicosia-mep"
    assert principal.role == Role.OWNER
    assert principal.auth_method == "jwt"

    # Expired token rejected
    assert verify_access_token(tok, now_ts=int(time.time()) + 600) is None
    # Wrong key rejected
    assert verify_access_token(tok, secret=b"wrong-secret-key") is None
    # Tampered payload rejected
    h_seg, p_seg, s_seg = tok.split(".")
    assert verify_access_token(f"{h_seg}.{p_seg}x.{s_seg}") is None

    # Algorithm confusion ("alg": "none") rejected
    none_hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode("ascii")
    assert verify_access_token(f"{none_hdr}.{p_seg}.") is None


# ----------------------------------------------------------------------
# 2. Self-Serve Firm Auth, RBAC Matrix, Seat Limits & Instant Revocation
# ----------------------------------------------------------------------
def test_firm_auth_rbac_matrix_and_instant_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "1")
    monkeypatch.setenv("CYELEC_ADMIN_KEY", "stage3-admin-secret")
    monkeypatch.delenv("CYELEC_API_KEY", raising=False)

    store = WorkspaceStore(tmp_path / "stage3_rbac.db")
    client = TestClient(create_app(store=store))

    # Public billing catalog remains reachable even with AUTH_REQUIRED=1
    plans_resp = client.get("/v1/billing/plans")
    assert plans_resp.status_code == 200
    assert [p["plan_id"] for p in plans_resp.json()] == ["free", "pilot", "pro", "enterprise"]

    # Register Firm #1
    reg1 = client.post(
        "/v1/auth/register",
        json={
            "firm_name": "Nicosia MEP Partners",
            "display_name": "Andreas Georgiou",
            "email": "andreas@nicosiamep.cy",
            "password": "StrongPassword!1",
        },
    )
    assert reg1.status_code == 201
    r1_data = reg1.json()
    owner_jwt = r1_data["access_token"]
    tid1 = r1_data["tenant"]["tenant_id"]
    owner_uid = r1_data["user"]["user_id"]
    assert r1_data["user"]["role"] == "owner"
    assert r1_data["tenant"]["plan"] == "free"
    assert r1_data["quota"]["solves_limit"] == 15

    # Duplicate email rejected
    dup_reg = client.post(
        "/v1/auth/register",
        json={
            "firm_name": "Other Firm",
            "display_name": "Clone",
            "email": "ANDREAS@nicosiamep.cy",
            "password": "StrongPassword!2",
        },
    )
    assert dup_reg.status_code == 400

    # Same firm name gets distinct tenant slug (no tenant hijacking!)
    reg2 = client.post(
        "/v1/auth/register",
        json={
            "firm_name": "Nicosia MEP Partners",
            "display_name": "Elena Kyriakou",
            "email": "elena@other-mep.cy",
            "password": "StrongPassword!3",
        },
    )
    assert reg2.status_code == 201
    assert reg2.json()["tenant"]["tenant_id"] != tid1

    # Password login succeeds & wrong password fails
    bad_login = client.post(
        "/v1/auth/login",
        json={"email": "andreas@nicosiamep.cy", "password": "WrongPassword!9"},
    )
    assert bad_login.status_code == 401
    good_login = client.post(
        "/v1/auth/login",
        json={"email": "andreas@nicosiamep.cy", "password": "StrongPassword!1"},
    )
    assert good_login.status_code == 200
    assert good_login.json()["tenant"]["tenant_id"] == tid1

    owner_hdrs = {"Authorization": f"Bearer {owner_jwt}"}

    # Free tier (1 seat) blocks adding a 2nd team member
    blocked_seat = client.post(
        "/v1/team/members",
        headers=owner_hdrs,
        json={
            "email": "eng@nicosiamep.cy",
            "display_name": "Maria Engineer",
            "role": "engineer",
            "password": "EngineerPass!1",
        },
    )
    assert blocked_seat.status_code == 400
    assert "Seat limit" in blocked_seat.json()["detail"]

    # Upgrade tenant to Pro (10 seats, 500 solves/mo) via Admin endpoint
    up_resp = client.put(
        f"/v1/admin/tenants/{tid1}/plan",
        headers={"X-Admin-Key": "stage3-admin-secret"},
        json={"plan_id": "pro", "subscription_status": "active"},
    )
    assert up_resp.status_code == 200
    assert up_resp.json()["plan"] == "pro"

    # Now add Engineer and Viewer seats
    eng_resp = client.post(
        "/v1/team/members",
        headers=owner_hdrs,
        json={
            "email": "eng@nicosiamep.cy",
            "display_name": "Maria Engineer",
            "role": "engineer",
            "password": "EngineerPass!1",
        },
    )
    assert eng_resp.status_code == 201
    eng_uid = eng_resp.json()["user_id"]

    vw_resp = client.post(
        "/v1/team/members",
        headers=owner_hdrs,
        json={
            "email": "viewer@nicosiamep.cy",
            "display_name": "Client Reviewer",
            "role": "viewer",
            "password": "ViewerPass!1",
        },
    )
    assert vw_resp.status_code == 201
    vw_uid = vw_resp.json()["user_id"]

    eng_jwt = client.post(
        "/v1/auth/login",
        json={"email": "eng@nicosiamep.cy", "password": "EngineerPass!1"},
    ).json()["access_token"]
    vw_jwt = client.post(
        "/v1/auth/login",
        json={"email": "viewer@nicosiamep.cy", "password": "ViewerPass!1"},
    ).json()["access_token"]
    eng_hdrs = {"Authorization": f"Bearer {eng_jwt}"}
    vw_hdrs = {"Authorization": f"Bearer {vw_jwt}"}

    # Viewer: read-only allowed, mutations & solves blocked (403)
    assert client.get("/v1/auth/me", headers=vw_hdrs).status_code == 200
    assert client.get("/v1/projects", headers=vw_hdrs).status_code == 200
    assert client.post("/v1/solve/benchmark/apartment_3bed", headers=vw_hdrs).status_code == 403
    assert client.post("/v1/projects", headers=vw_hdrs, json={"name": "Nope"}).status_code == 403
    assert client.delete("/v1/tenants/me", headers=vw_hdrs).status_code == 403

    # Engineer: solves & project creation allowed, owner-only admin routes blocked (403)
    assert client.post("/v1/solve/benchmark/apartment_3bed", headers=eng_hdrs).status_code == 200
    proj_resp = client.post("/v1/projects", headers=eng_hdrs, json={"name": "Tower A"})
    assert proj_resp.status_code == 201
    assert client.post("/v1/tenants/me/api-key", headers=eng_hdrs).status_code == 403
    assert client.delete("/v1/tenants/me", headers=eng_hdrs).status_code == 403
    assert (
        client.post(
            "/v1/billing/checkout", headers=eng_hdrs, json={"plan_id": "enterprise"}
        ).status_code
        == 403
    )

    # Last-owner protection: sole owner cannot demote or delete themselves
    assert (
        client.patch(
            f"/v1/team/members/{owner_uid}/role",
            headers=owner_hdrs,
            json={"role": "engineer"},
        ).status_code
        == 400
    )
    assert client.delete(f"/v1/team/members/{owner_uid}", headers=owner_hdrs).status_code == 400

    # Instant RBAC demotion: demoting engineer -> viewer immediately blocks their existing JWT!
    demote = client.patch(
        f"/v1/team/members/{eng_uid}/role",
        headers=owner_hdrs,
        json={"role": "viewer"},
    )
    assert demote.status_code == 200
    assert demote.json()["role"] == "viewer"
    assert client.post("/v1/solve/benchmark/apartment_3bed", headers=eng_hdrs).status_code == 403

    # Instant session revocation: deleting viewer immediately invalidates their existing JWT!
    del_vw = client.delete(f"/v1/team/members/{vw_uid}", headers=owner_hdrs)
    assert del_vw.status_code == 200
    assert client.get("/v1/auth/me", headers=vw_hdrs).status_code == 401


# ----------------------------------------------------------------------
# 3. Demo-Lock Guard & Mocked Google OIDC SSO
# ----------------------------------------------------------------------
def test_demo_lock_and_google_oidc_sso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "0")
    monkeypatch.setenv("CYELEC_DEMO_LOCK_TENANT_MUTATIONS", "1")
    monkeypatch.delenv("CYELEC_GOOGLE_CLIENT_ID", raising=False)

    store = WorkspaceStore(tmp_path / "stage3_demo_google.db")
    client = TestClient(create_app(store=store))

    # Unauthenticated staging visitors cannot mutate team seats or trigger checkout
    assert (
        client.post(
            "/v1/team/members",
            json={
                "email": "bad@example.com",
                "display_name": "Bad",
                "role": "owner",
                "password": "Password!1",
            },
        ).status_code
        == 403
    )
    assert client.post("/v1/billing/checkout", json={"plan_id": "pro"}).status_code == 403

    # Google SSO returns 503 when CYELEC_GOOGLE_CLIENT_ID is unset
    assert client.post("/v1/auth/google", json={"id_token": "tok"}).status_code == 503

    # Enable Google SSO with mocked tokeninfo verification
    monkeypatch.setenv("CYELEC_GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")

    def _fake_verify(tok: str, cid: str, *, timeout_s: float = 10.0) -> dict[str, str]:
        if tok != "valid-google-token":
            raise ValueError("Invalid Google token")
        return {
            "sub": "google-sub-999",
            "email": "sso.engineer@limassol-mep.cy",
            "name": "Nikos SSO",
        }

    api_app_mod = importlib.import_module("cyelec.api.app")
    monkeypatch.setattr(api_app_mod, "verify_google_id_token", _fake_verify)
    assert client.post("/v1/auth/google", json={"id_token": "bad"}).status_code == 401

    # First login provisions a brand-new firm owner
    g1 = client.post(
        "/v1/auth/google",
        json={"id_token": "valid-google-token", "firm_name": "Limassol SSO MEP"},
    )
    assert g1.status_code == 200
    g1_data = g1.json()
    assert g1_data["user"]["auth_provider"] == "google"
    assert g1_data["user"]["role"] == "owner"

    # Second login returns the same tenant & user idempotently
    g2 = client.post("/v1/auth/google", json={"id_token": "valid-google-token"})
    assert g2.status_code == 200
    assert g2.json()["tenant"]["tenant_id"] == g1_data["tenant"]["tenant_id"]
    assert g2.json()["user"]["user_id"] == g1_data["user"]["user_id"]

    # Health reports google_sso_configured=True
    assert client.get("/health").json()["google_sso_configured"] is True


# ----------------------------------------------------------------------
# 4. Monthly Solve Quota Enforcement (429 + Headers + 4xx Non-Consumption)
# ----------------------------------------------------------------------
def test_monthly_quota_enforcement_and_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "1")
    monkeypatch.setenv("CYELEC_ADMIN_KEY", "quota-admin")
    monkeypatch.setenv("CYELEC_ENFORCE_QUOTAS", "1")

    store = WorkspaceStore(tmp_path / "stage3_quota.db")
    client = TestClient(create_app(store=store))

    reg = client.post(
        "/v1/auth/register",
        json={
            "firm_name": "Paphos Solar & Electrical",
            "display_name": "Costas",
            "email": "costas@paphos-elec.cy",
            "password": "StrongPassword!1",
        },
    ).json()
    tid = reg["tenant"]["tenant_id"]
    hdrs = {"Authorization": f"Bearer {reg['access_token']}"}

    # Cap monthly quota to 2 solves
    cap = client.put(
        f"/v1/admin/tenants/{tid}/plan",
        headers={"X-Admin-Key": "quota-admin"},
        json={"plan_id": "free", "monthly_quota_override": 2},
    )
    assert cap.status_code == 200

    # Solve #1 succeeds with X-Quota-* headers
    s1 = client.post("/v1/solve/benchmark/apartment_3bed", headers=hdrs)
    assert s1.status_code == 200
    assert s1.headers["x-quota-limit"] == "2"
    assert s1.headers["x-quota-used"] == "1"
    assert s1.headers["x-quota-remaining"] == "1"

    # Invalid solve (422) does NOT consume quota
    bad = client.post("/v1/solve/benchmark/does_not_exist", headers=hdrs)
    assert bad.status_code == 422
    assert store.get_monthly_solve_count(tid) == 1

    # Solve #2 reaches limit (2/2)
    s2 = client.post("/v1/solve/benchmark/apartment_3bed", headers=hdrs)
    assert s2.status_code == 200
    assert s2.headers["x-quota-used"] == "2"
    assert s2.headers["x-quota-remaining"] == "0"

    # Solve #3 blocked with HTTP 429
    s3 = client.post("/v1/solve/benchmark/apartment_3bed", headers=hdrs)
    assert s3.status_code == 429
    assert "Monthly solve quota exceeded" in s3.json()["detail"]
    assert s3.headers["x-quota-remaining"] == "0"

    # Upgrading plan clears override -> Solve #3 immediately succeeds
    client.put(
        f"/v1/admin/tenants/{tid}/plan",
        headers={"X-Admin-Key": "quota-admin"},
        json={"plan_id": "pilot", "clear_quota_override": True},
    )
    s4 = client.post("/v1/solve/benchmark/apartment_3bed", headers=hdrs)
    assert s4.status_code == 200
    assert s4.headers["x-quota-limit"] == "100"
    assert s4.headers["x-quota-used"] == "3"


# ----------------------------------------------------------------------
# 5. Stripe Zero-SDK Webhook HMAC-SHA256 Verification & Lifecycle
# ----------------------------------------------------------------------
def test_stripe_webhook_hmac_and_subscription_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wh_secret = "whsec_cyelec_test_secret_12345"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", wh_secret)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_cyelec_dummy")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_cy_pro_99")
    monkeypatch.setenv("STRIPE_PRICE_ENTERPRISE", "price_cy_ent_299")
    monkeypatch.setenv("CYELEC_AUTH_REQUIRED", "1")

    store = WorkspaceStore(tmp_path / "stage3_stripe.db")
    client = TestClient(create_app(store=store))

    assert resolve_plan_spec("pro_cyprus").plan_id == "pro"
    assert PLAN_CATALOG["pro"].monthly_solve_quota == 500
    assert client.get("/health").json()["stripe_configured"] is True

    reg = client.post(
        "/v1/auth/register",
        json={
            "firm_name": "Larnaca MEP Studio",
            "display_name": "Despina",
            "email": "despina@larnacamep.cy",
            "password": "StrongPassword!1",
        },
    ).json()
    tid = reg["tenant"]["tenant_id"]
    hdrs = {"Authorization": f"Bearer {reg['access_token']}"}

    # Replay & invalid signature checks
    sample_body = b'{"type":"checkout.session.completed","data":{"object":{}}}'
    assert verify_stripe_signature(sample_body, _make_stripe_sig(sample_body, wh_secret), wh_secret)
    stale_sig = _make_stripe_sig(sample_body, wh_secret, ts=int(time.time()) - 900)
    assert verify_stripe_signature(sample_body, stale_sig, wh_secret) is False
    assert (
        client.post(
            "/v1/billing/webhook",
            content=sample_body,
            headers={"Stripe-Signature": "t=123,v1=deadbeef"},
        ).status_code
        == 400
    )

    # 1) checkout.session.completed activates 'pro' & records customer/subscription IDs
    ev_checkout = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_cy_001",
                    "subscription": "sub_cy_001",
                    "metadata": {"tenant_id": tid, "plan_id": "pro"},
                }
            },
        }
    ).encode("utf-8")
    r_co = client.post(
        "/v1/billing/webhook",
        content=ev_checkout,
        headers={"Stripe-Signature": _make_stripe_sig(ev_checkout, wh_secret)},
    )
    assert r_co.status_code == 200
    assert r_co.json()["action"] == "activated_plan:pro"

    sub_state = client.get("/v1/billing/subscription", headers=hdrs).json()
    assert sub_state["plan"]["plan_id"] == "pro"
    assert sub_state["stripe_customer_id"] == "cus_cy_001"
    assert sub_state["seats_limit"] == 10

    # 2) invoice.payment_failed resolved purely by stripe_customer_id -> past_due
    ev_fail = json.dumps(
        {
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_cy_001"}},
        }
    ).encode("utf-8")
    r_fail = client.post(
        "/v1/billing/webhook",
        content=ev_fail,
        headers={"Stripe-Signature": _make_stripe_sig(ev_fail, wh_secret)},
    )
    assert r_fail.status_code == 200
    assert store.get_or_create_tenant(tid).subscription_status == "past_due"

    # 3) customer.subscription.updated via Stripe Price ID -> upgrades to enterprise
    ev_upd = json.dumps(
        {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_cy_001",
                    "customer": "cus_cy_001",
                    "status": "active",
                    "items": {"data": [{"price": {"id": "price_cy_ent_299"}}]},
                }
            },
        }
    ).encode("utf-8")
    r_upd = client.post(
        "/v1/billing/webhook",
        content=ev_upd,
        headers={"Stripe-Signature": _make_stripe_sig(ev_upd, wh_secret)},
    )
    assert r_upd.status_code == 200
    assert store.get_or_create_tenant(tid).plan == "enterprise"

    # 4) customer.subscription.deleted -> downgrades safely to free & canceled
    ev_del = json.dumps(
        {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_cy_001"}},
        }
    ).encode("utf-8")
    r_del = client.post(
        "/v1/billing/webhook",
        content=ev_del,
        headers={"Stripe-Signature": _make_stripe_sig(ev_del, wh_secret)},
    )
    assert r_del.status_code == 200
    t_after = store.get_or_create_tenant(tid)
    assert t_after.plan == "free"
    assert t_after.subscription_status == "canceled"

    # Mocked Checkout & Portal endpoints return Stripe URLs cleanly
    def _fake_stripe_post(
        path: str, form: dict[str, str], *, secret_key: str, timeout_s: float = 15.0
    ) -> dict[str, Any]:
        if "checkout" in path:
            return {"id": "cs_test_123", "url": "https://checkout.stripe.com/pay/cs_test_123"}
        return {"url": "https://billing.stripe.com/session/bps_test_123"}

    monkeypatch.setattr(billing_mod, "_stripe_post_form", _fake_stripe_post)
    co_resp = client.post("/v1/billing/checkout", headers=hdrs, json={"plan_id": "pro"})
    assert co_resp.status_code == 200
    assert co_resp.json()["checkout_url"].startswith("https://checkout.stripe.com/")

    po_resp = client.post("/v1/billing/portal", headers=hdrs)
    assert po_resp.status_code == 200
    assert po_resp.json()["portal_url"].startswith("https://billing.stripe.com/")


class _Snap:
    def __init__(self, ref: _DocRef, data: dict[str, Any] | None) -> None:
        self.reference = ref
        self._data = copy.deepcopy(data) if data is not None else None

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._data)


class _DocRef:
    def __init__(self, db: _FakeFirestoreClient, path: str) -> None:
        self._db = db
        self.path = path

    def collection(self, name: str) -> _ColRef:
        return _ColRef(self._db, f"{self.path}/{name}")

    def get(self) -> _Snap:
        return _Snap(self, self._db.docs.get(self.path))

    def set(self, data: dict[str, Any], *, merge: bool = False) -> None:
        if merge and self.path in self._db.docs:
            merged = copy.deepcopy(self._db.docs[self.path])
            merged.update(copy.deepcopy(data))
            self._db.docs[self.path] = merged
        else:
            self._db.docs[self.path] = copy.deepcopy(data)

    def delete(self) -> None:
        self._db.docs.pop(self.path, None)


class _Query:
    def __init__(self, col: _ColRef, field: str, op: str, val: Any) -> None:
        self._col, self._field, self._op, self._val = col, field, op, val

    def stream(self) -> list[_Snap]:
        return [
            s
            for s in self._col.stream()
            if self._op == "==" and (s.to_dict() or {}).get(self._field) == self._val
        ]


class _ColRef:
    def __init__(self, db: _FakeFirestoreClient, path: str) -> None:
        self._db = db
        self.path = path

    def document(self, doc_id: str) -> _DocRef:
        return _DocRef(self._db, f"{self.path}/{doc_id}")

    def where(self, field: str, op: str, val: Any) -> _Query:
        return _Query(self, field, op, val)

    def stream(self) -> list[_Snap]:
        prefix = f"{self.path}/"
        return [
            _Snap(_DocRef(self._db, k), v)
            for k, v in sorted(self._db.docs.items())
            if k.startswith(prefix) and "/" not in k[len(prefix) :]
        ]


class _Batch:
    def __init__(self) -> None:
        self._ops: list[tuple[str, _DocRef, dict[str, Any] | None, bool]] = []

    def set(self, ref: _DocRef, data: dict[str, Any], *, merge: bool = False) -> None:
        self._ops.append(("set", ref, data, merge))

    def delete(self, ref: _DocRef) -> None:
        self._ops.append(("delete", ref, None, False))

    def commit(self) -> None:
        for op, ref, data, merge in self._ops:
            if op == "delete":
                ref.delete()
            elif data is not None:
                ref.set(data, merge=merge)
        self._ops.clear()


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _ColRef:
        return _ColRef(self, name)

    def batch(self) -> _Batch:
        return _Batch()


class _InMemoryGzipBlobStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put_gzip_json(self, path: str, json_text: str) -> None:
        self.blobs[path] = gzip.compress(json_text.encode("utf-8"), compresslevel=6)

    def get_gzip_json(self, path: str) -> str | None:
        raw = self.blobs.get(path)
        return gzip.decompress(raw).decode("utf-8") if raw is not None else None

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.blobs if k.startswith(prefix)]
        for k in keys:
            del self.blobs[k]
        return len(keys)


# ----------------------------------------------------------------------
# 6. FirestoreWorkspaceStore Stage 3 Parity (Users, Quotas, Stripe, GDPR)
# ----------------------------------------------------------------------
def test_firestore_store_stage3_parity_and_gdpr_erasure() -> None:
    fake_db = _FakeFirestoreClient()
    fake_blobs = _InMemoryGzipBlobStore()
    fs = FirestoreWorkspaceStore(firestore_client=fake_db, blob_store=fake_blobs)

    fs.get_or_create_tenant("cy-firm-fs")
    fs.update_tenant_billing(
        "cy-firm-fs",
        plan="pro",
        subscription_status="active",
        stripe_customer_id="cus_fs_42",
        stripe_subscription_id="sub_fs_42",
    )
    assert fs.find_tenant_by_stripe_customer("cus_fs_42") == "cy-firm-fs"

    owner = fs.create_user(
        "cy-firm-fs",
        "owner@fsfirm.cy",
        "Owner User",
        role=Role.OWNER,
        password="Password!123",
    )
    eng = fs.create_user(
        "cy-firm-fs",
        "eng@fsfirm.cy",
        "Engineer User",
        role=Role.ENGINEER,
        password="Password!456",
    )
    assert fs.authenticate_user("owner@fsfirm.cy", "Password!123") is not None
    assert fs.authenticate_user("owner@fsfirm.cy", "WrongPass!999") is None
    assert len(fs.list_users("cy-firm-fs")) == 2

    assert fs.increment_monthly_solve_count("cy-firm-fs", "2026-11") == 1
    assert fs.increment_monthly_solve_count("cy-firm-fs", "2026-11") == 2
    qs = fs.get_quota_status("cy-firm-fs", "2026-11")
    assert qs.solves_used == 2
    assert qs.solves_limit == 500
    assert qs.exceeded is False

    # GDPR Art. 20 export includes firm members
    bundle = fs.export_tenant_data("cy-firm-fs")
    assert len(bundle.members) == 2
    assert {m.email for m in bundle.members} == {"owner@fsfirm.cy", "eng@fsfirm.cy"}

    # Update role & delete non-owner
    upd_eng = fs.update_user_role("cy-firm-fs", eng.user_id, Role.VIEWER)
    assert upd_eng is not None and upd_eng.role == Role.VIEWER
    assert fs.delete_user("cy-firm-fs", eng.user_id) is True
    assert fs.get_user_by_email("eng@fsfirm.cy") is None

    # GDPR Art. 17 cascade erasure deletes tenant, remaining user, email index, and quota docs
    assert fs.delete_tenant_cascade("cy-firm-fs") is True
    assert fs.get_user_by_email("owner@fsfirm.cy") is None
    assert fs.get_user("cy-firm-fs", owner.user_id) is None
    assert fs.get_monthly_solve_count("cy-firm-fs", "2026-11") == 0


# ----------------------------------------------------------------------
# 7. Budget Gate 'alert_only' Mode for Commercial GA
# ----------------------------------------------------------------------
def test_budget_gate_alert_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    paused_calls: list[str] = []

    def _fake_pause(proj: str, reg: str, svc: str) -> dict[str, Any]:
        paused_calls.append(svc)
        return {"paused": svc}

    monkeypatch.setenv("CYELEC_GCP_PROJECT", "cyelec-saas-261125")
    monkeypatch.setenv("CYELEC_BUDGET_GATE_MODE", "alert_only")
    monkeypatch.delenv("CYELEC_EXPECTED_SUBSCRIPTION", raising=False)

    gate_client = TestClient(create_budget_gate_app(pause_fn=_fake_pause))
    assert gate_client.get("/health").json()["mode"] == "alert_only"

    payload_b64 = base64.b64encode(
        json.dumps(
            {
                "budgetDisplayName": "CyElec Monthly",
                "costAmount": 12.50,
                "budgetAmount": 10.00,
                "currencyCode": "EUR",
                "alertThresholdExceeded": 1.0,
            }
        ).encode("utf-8")
    ).decode("ascii")
    resp = gate_client.post(
        "/pubsub/budget",
        json={"message": {"data": payload_b64, "messageId": "m-1"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "alert_only_logged"
    assert body["paused_services"] == []
    assert paused_calls == []


# ----------------------------------------------------------------------
# 8. Official Cyprus EAC Application Form + 4th Excel Sheet + ISO A3/A1 CAD
# ----------------------------------------------------------------------
def test_eac_application_form_excel_sheet_and_iso_paperspace_dxf(tmp_path: Path) -> None:
    # A) Residential 3-bed apartment (span <= 16m -> ISO A3 1:50 paper-space layout)
    apt_dxf = tmp_path / "apt.dxf"
    apt_bldg = generate_cyprus_3bed_dxf(out_dxf=apt_dxf)
    apt_res = run_phase2_pipeline(
        apt_bldg,
        overrides=DesignOverrides(
            branding=TenantBranding(
                firm_name="Cyprus MEP Consultants Ltd",
                engineer_name="Dr. Nikos Charalambous",
                etek_reg_no="ETEK-E-9021",
            )
        ),
    )
    apt_out = export_phase3_deliverables(apt_res, tmp_path / "apt_pack")

    # Official EAC Application Form HTML
    assert "eac_form_html" in apt_out
    form_html = apt_out["eac_form_html"].read_text(encoding="utf-8")
    assert "ΑΡΧΗ ΗΛΕΚΤΡΙΣΜΟΥ ΚΥΠΡΟΥ" in form_html.upper() or "ΑΗΚ" in form_html
    assert "ETEK-E-9021" in form_html
    assert "Statutory ETEK Declaration" in form_html
    assert len(build_eac_application_rows(apt_res)) >= 15
    assert render_eac_application_form_html(apt_res) == form_html

    # 4th Excel worksheet "EAC Application"
    wb = load_workbook(str(apt_out["xlsx"]), data_only=False)
    assert "EAC Application" in wb.sheetnames
    ws4 = wb["EAC Application"]
    sheet_text = " ".join(
        str(ws4.cell(row=r, column=c).value or "")
        for r in range(1, ws4.max_row + 1)
        for c in range(1, 5)
    )
    assert "ETEK-E-9021" in sheet_text
    assert "REQUIRED BEFORE EAC SUBMISSION" in sheet_text

    # AutoCAD ISO A3 (1:50) Paper-Space Layout Tab with real VIEWPORT & scale bar
    dxf_doc = ezdxf.readfile(str(apt_out["layout_dxf"]))
    assert "EAC-A3-1-50" in dxf_doc.layouts
    psp = dxf_doc.layouts.get("EAC-A3-1-50")
    viewports = list(psp.query("VIEWPORT"))
    assert len(viewports) >= 1
    psp_texts = " ".join(e.dxf.text for e in psp.query("TEXT"))
    assert "SCALE 1:50 (METRIC)" in psp_texts
    assert "ETEK-E-9021" in psp_texts

    # B) Commercial Office (span > 16m -> automatically selects ISO A1 1:100 layout)
    off_dxf = tmp_path / "office.dxf"
    off_bldg = generate_benchmark_dxf(BenchmarkTemplate.COMMERCIAL_OFFICE, out_dxf=off_dxf)
    off_res = run_phase2_pipeline(off_bldg)
    off_out = export_phase3_deliverables(off_res, tmp_path / "office_pack")

    off_doc = ezdxf.readfile(str(off_out["layout_dxf"]))
    assert "EAC-A1-1-100" in off_doc.layouts
    off_psp = off_doc.layouts.get("EAC-A1-1-100")
    assert len(list(off_psp.query("VIEWPORT"))) >= 1
    off_texts = " ".join(e.dxf.text for e in off_psp.query("TEXT"))
    assert "SCALE 1:100 (METRIC)" in off_texts
    assert "Commercial Office" in off_out["eac_form_html"].read_text(encoding="utf-8")

    # C) 2-storey villa renders Section F Sub-Distribution Boards table in EAC Application Form
    v2_bldg = generate_benchmark_dxf(BenchmarkTemplate.VILLA_2STOREY, out_dxf=tmp_path / "v2.dxf")
    v2_res = run_phase2_pipeline(v2_bldg)
    assert "F. Sub-Distribution Boards" in render_eac_application_form_html(v2_res)

    # Confirm no auth helper unused import warning
    assert auth_mod.role_allows(Role.OWNER, Role.ENGINEER) is True
    assert auth_mod.role_allows(Role.VIEWER, Role.ENGINEER) is False
