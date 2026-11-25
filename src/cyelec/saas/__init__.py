"""Multi-tenant EU B2B SaaS workspace persistence, auth, revision diffing & usage metering."""

from cyelec.saas.auth import generate_api_key, hash_api_key, verify_api_key
from cyelec.saas.revisions import (
    CircuitDelta,
    RevisionDiff,
    RoomDelta,
    carry_forward_overrides,
    compute_revision_diff,
)
from cyelec.saas.store import (
    ProjectSummary,
    RevisionRecord,
    RevisionSummary,
    TenantProfile,
    UsageSummary,
    WorkspaceStore,
)

__all__ = [
    "CircuitDelta",
    "ProjectSummary",
    "RevisionDiff",
    "RevisionRecord",
    "RevisionSummary",
    "RoomDelta",
    "TenantProfile",
    "UsageSummary",
    "WorkspaceStore",
    "carry_forward_overrides",
    "compute_revision_diff",
    "generate_api_key",
    "hash_api_key",
    "verify_api_key",
]
