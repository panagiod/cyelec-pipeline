"""Zero-dependency per-tenant API key generation and constant-time PBKDF2-SHA256 verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 100_000


def generate_api_key() -> str:
    """Generate a high-entropy tenant API key suitable for X-API-Key authentication."""
    return f"cyelec_live_{secrets.token_urlsafe(32)}"


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
