"""Shared application state container for CyElec FastAPI routers and middleware."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import anyio
from fastapi import Request

from cyelec.api.workers import validate_safe_id
from cyelec.models.electrical import DesignOverrides, ReportLanguage
from cyelec.saas.store import WorkspaceStoreProtocol

_AUTH_CACHE_TTL_S = 60.0
_MAX_AUTH_CACHE = 512


@dataclass
class AppContext:
    """Explicit runtime dependencies shared across routers and middleware."""

    store: WorkspaceStoreProtocol
    store_backend: str
    disable_openapi: bool
    rate_buckets: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    # Bounded 60s TTL cache mapping SHA-256(api_key) -> (tenant_id, expires_monotonic)
    auth_cache: dict[str, tuple[str, float]] = field(default_factory=dict)

    def effective_tenant(self, request: Request, header_tenant: str) -> str:
        """Resolve authenticated tenant ID or validate fallback X-Tenant-ID header."""
        auth_tid = getattr(request.state, "auth_tenant_id", None)
        if isinstance(auth_tid, str) and auth_tid:
            return auth_tid
        return validate_safe_id(header_tenant or "anonymous", "X-Tenant-ID")

    def merge_tenant_defaults(
        self,
        tenant_id: str,
        overrides: DesignOverrides | None,
    ) -> DesignOverrides | None:
        """Apply saved tenant branding, language, and BOQ rate defaults unless overridden."""
        profile = self.store.get_or_create_tenant(tenant_id)
        if (
            profile.branding is None
            and profile.report_language == ReportLanguage.EN
            and profile.boq_rate_overrides is None
        ):
            return overrides
        if overrides is None:
            return DesignOverrides(
                report_language=profile.report_language,
                branding=profile.branding,
                boq_rate_overrides=profile.boq_rate_overrides,
            )
        updates: dict[str, object] = {}
        if overrides.branding is None and profile.branding is not None:
            updates["branding"] = profile.branding
        if (
            overrides.report_language == ReportLanguage.EN
            and profile.report_language != ReportLanguage.EN
        ):
            updates["report_language"] = profile.report_language
        if overrides.boq_rate_overrides is None and profile.boq_rate_overrides is not None:
            updates["boq_rate_overrides"] = profile.boq_rate_overrides
        return overrides.model_copy(update=updates) if updates else overrides

    def evict_auth_cache(self, tenant_id: str) -> None:
        """Invalidate cached API-key lookups for a tenant after key rotation or erasure."""
        stale_keys = [k for k, (tid, _) in self.auth_cache.items() if tid == tenant_id]
        for k in stale_keys:
            self.auth_cache.pop(k, None)

    async def resolve_api_key_nonblocking(self, supplied_key: str) -> str | None:
        """Verify an API key with bounded 60s caching and offloaded PBKDF2 hashing."""
        now_m = time.monotonic()
        key_sha = hashlib.sha256(supplied_key.encode("utf-8")).hexdigest()
        cached = self.auth_cache.get(key_sha)
        if cached is not None:
            tid, exp = cached
            if now_m < exp:
                return tid
            self.auth_cache.pop(key_sha, None)

        resolved = await anyio.to_thread.run_sync(
            self.store.resolve_tenant_by_api_key, supplied_key
        )
        if resolved is not None:
            if len(self.auth_cache) >= _MAX_AUTH_CACHE:
                expired = [k for k, (_, exp) in self.auth_cache.items() if now_m >= exp]
                for k in expired:
                    self.auth_cache.pop(k, None)
                if len(self.auth_cache) >= _MAX_AUTH_CACHE:
                    oldest_k = min(self.auth_cache, key=lambda k: self.auth_cache[k][1])
                    self.auth_cache.pop(oldest_k, None)
            self.auth_cache[key_sha] = (resolved, now_m + _AUTH_CACHE_TTL_S)
        return resolved
