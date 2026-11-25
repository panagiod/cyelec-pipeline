"""Zero-dependency per-tenant API key generation and O(1) prefix-indexed PBKDF2 verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 100_000
_KEY_PREFIX_TAG = "cyelec_live_"


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
