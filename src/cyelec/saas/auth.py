"""Zero-dependency API key hashing, PBKDF2 passwords, HS256 JWT sessions, RBAC & Google OIDC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import secrets
import time
import urllib.parse
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

_PBKDF2_ITERATIONS = 100_000
_PASSWORD_PBKDF2_ITERATIONS = 200_000
_KEY_PREFIX_TAG = "cyelec_live_"
_JWT_ISSUER = "cyelec"
_PROCESS_FALLBACK_SECRET: bytes = secrets.token_bytes(32)


class Role(StrEnum):
    """Tenant RBAC roles ordered by ascending privilege."""

    VIEWER = "viewer"
    ENGINEER = "engineer"
    OWNER = "owner"


_ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 1,
    Role.ENGINEER: 2,
    Role.OWNER: 3,
}


def role_allows(actual: Role, required: Role) -> bool:
    """Return True if `actual` role meets or exceeds `required` RBAC privilege."""
    return _ROLE_RANK[actual] >= _ROLE_RANK[required]


class AuthPrincipal(BaseModel):
    """Authenticated caller identity resolved by HTTP middleware."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    role: Role = Role.OWNER
    user_id: str | None = None
    email: str | None = None
    auth_method: Literal["jwt", "api_key", "global_key", "demo"] = "demo"


# ----------------------------------------------------------------------
# API Key Generation & Prefix-Indexed PBKDF2 Verification
# ----------------------------------------------------------------------
def generate_api_key() -> str:
    """Generate a prefix-indexed high-entropy tenant API key (cyelec_live_<8hex>_<secret>)."""
    key_id = secrets.token_hex(4)  # 8 hex chars for O(1) DB index lookup
    secret_part = secrets.token_urlsafe(32)
    return f"{_KEY_PREFIX_TAG}{key_id}_{secret_part}"


def extract_key_prefix(api_key: str) -> str | None:
    """Extract the 8-hex key_id prefix from a formatted API key for O(1) indexed lookup."""
    if not api_key.startswith(_KEY_PREFIX_TAG):
        return None
    rest = api_key[len(_KEY_PREFIX_TAG) :]
    if "_" not in rest:
        return None
    prefix, _ = rest.split("_", 1)
    return prefix if len(prefix) == 8 else None


def hash_api_key(api_key: str, salt: str | None = None) -> tuple[str, str]:
    """Derive a salted PBKDF2-HMAC-SHA256 digest (hex_hash, hex_salt) for storage."""
    resolved_salt = salt if salt is not None else secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        api_key.encode("utf-8"),
        resolved_salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    ).hex()
    return digest, resolved_salt


def verify_api_key(api_key: str, stored_hash: str, salt: str) -> bool:
    """Constant-time verification of a candidate API key against its stored salted digest."""
    if not api_key or not stored_hash or not salt:
        return False
    candidate_hash, _ = hash_api_key(api_key, salt=salt)
    return hmac.compare_digest(candidate_hash, stored_hash)


# ----------------------------------------------------------------------
# User Password Hashing (PBKDF2-HMAC-SHA256, 200k iterations)
# ----------------------------------------------------------------------
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a user password with PBKDF2-HMAC-SHA256 (200,000 iterations, random 128-bit salt)."""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if len(password) > 128:
        raise ValueError("Password must be at most 128 characters long.")
    resolved_salt = salt if salt is not None else secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt.encode("utf-8"),
        _PASSWORD_PBKDF2_ITERATIONS,
    ).hex()
    return digest, resolved_salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Constant-time verification of a plaintext password against its stored PBKDF2 digest."""
    if not password or not stored_hash or not salt or len(password) > 128:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PASSWORD_PBKDF2_ITERATIONS,
    ).hex()
    return hmac.compare_digest(digest, stored_hash)


# ----------------------------------------------------------------------
# Zero-Dependency RFC 7519 HS256 JWT Session Tokens
# ----------------------------------------------------------------------
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode((segment + pad).encode("ascii"))


def get_jwt_secret() -> bytes:
    """Resolve the JWT HS256 signing key.

    Priority:
      1. Explicit CYELEC_JWT_SECRET environment variable / Secret Manager secret.
      2. Domain-separated HMAC derivation from CYELEC_ADMIN_KEY (stable across Cloud Run replicas).
      3. Process-ephemeral random 256-bit key (local dev/tests).
    """
    explicit = os.environ.get("CYELEC_JWT_SECRET", "").strip()
    if explicit:
        return explicit.encode("utf-8")
    admin_key = os.environ.get("CYELEC_ADMIN_KEY", "").strip()
    if admin_key:
        return hmac.new(
            admin_key.encode("utf-8"),
            b"cyelec-jwt-signing-v1",
            hashlib.sha256,
        ).digest()
    return _PROCESS_FALLBACK_SECRET


def create_access_token(
    *,
    user_id: str,
    tenant_id: str,
    email: str,
    role: Role,
    ttl_seconds: int = 86_400,
    secret: bytes | None = None,
) -> str:
    """Issue a signed HS256 JWT session token bound to user_id, tenant_id, email, and RBAC role."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": _JWT_ISSUER,
        "sub": user_id,
        "tid": tenant_id,
        "email": email,
        "role": role.value,
        "iat": now,
        "exp": now + max(60, ttl_seconds),
    }
    h_seg = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p_seg = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_seg}.{p_seg}".encode("ascii")
    key = secret if secret is not None else get_jwt_secret()
    sig = hmac.new(key, signing_input, hashlib.sha256).digest()
    return f"{h_seg}.{p_seg}.{_b64url_encode(sig)}"


def verify_access_token(
    token: str,
    *,
    secret: bytes | None = None,
    now_ts: int | None = None,
) -> AuthPrincipal | None:
    """Verify an HS256 JWT and return an AuthPrincipal, or None if invalid/expired."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    h_seg, p_seg, s_seg = parts
    try:
        header = json.loads(_b64url_decode(h_seg).decode("utf-8"))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            return None
        key = secret if secret is not None else get_jwt_secret()
        expected_sig = hmac.new(
            key,
            f"{h_seg}.{p_seg}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        supplied_sig = _b64url_decode(s_seg)
        if not hmac.compare_digest(expected_sig, supplied_sig):
            return None

        payload = json.loads(_b64url_decode(p_seg).decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("iss") != _JWT_ISSUER:
            return None
        now = int(time.time()) if now_ts is None else now_ts
        exp = int(payload.get("exp", 0))
        if exp <= now:
            return None

        sub = str(payload.get("sub", "")).strip()
        tid = str(payload.get("tid", "")).strip()
        email = str(payload.get("email", "")).strip()
        role = Role(str(payload.get("role", "viewer")))
        if not sub or not tid:
            return None
        return AuthPrincipal(
            tenant_id=tid,
            role=role,
            user_id=sub,
            email=email or None,
            auth_method="jwt",
        )
    except Exception:
        return None


# ----------------------------------------------------------------------
# Optional Google OIDC ID-Token Verification (Stdlib HTTPS, Zero Extra Deps)
# ----------------------------------------------------------------------
def verify_google_id_token(
    id_token: str,
    expected_client_id: str,
    *,
    timeout_s: float = 10.0,
) -> dict[str, str]:
    """Verify a Google OIDC ID token against oauth2.googleapis.com/tokeninfo."""
    clean_tok = id_token.strip()
    if not clean_tok:
        raise ValueError("Missing Google ID token.")
    if not expected_client_id.strip():
        raise ValueError("Google OIDC SSO is not configured (CYELEC_GOOGLE_CLIENT_ID not set).")

    query = urllib.parse.urlencode({"id_token": clean_tok})
    conn = http.client.HTTPSConnection("oauth2.googleapis.com", timeout=timeout_s)
    try:
        conn.request("GET", f"/tokeninfo?{query}")
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        if resp.status != 200:
            raise ValueError("Google ID token validation failed.")
        data = json.loads(raw)
    finally:
        conn.close()

    if not isinstance(data, dict):
        raise ValueError("Unexpected Google tokeninfo payload.")
    if str(data.get("aud", "")) != expected_client_id.strip():
        raise ValueError("Google ID token audience mismatch.")
    iss = str(data.get("iss", ""))
    if iss not in ("https://accounts.google.com", "accounts.google.com"):
        raise ValueError("Unexpected Google ID token issuer.")
    if str(data.get("email_verified", "")).lower() != "true":
        raise ValueError("Google account email is not verified.")
    email = str(data.get("email", "")).strip().lower()
    sub = str(data.get("sub", "")).strip()
    if not email or not sub:
        raise ValueError("Google token missing email or subject.")
    return {
        "sub": sub,
        "email": email,
        "name": str(data.get("name", "")).strip() or email.split("@", 1)[0],
    }
