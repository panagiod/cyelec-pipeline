"""Strictly tenant-isolated SQLite workspace store for projects, revisions, branding & usage."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from cyelec.models.electrical import (
    BoqRateOverrides,
    DesignOverrides,
    ReportLanguage,
    TenantBranding,
)
from cyelec.models.spatial import Phase2DesignResult
from cyelec.saas.auth import (
    Role,
    extract_key_prefix,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_api_key,
    verify_password,
)
from cyelec.saas.billing import QuotaStatus, current_month_key, resolve_plan_spec
from cyelec.saas.billing import utc_now as _utc_now


class TenantProfile(BaseModel):
    """Per-tenant consulting practice profile, branding defaults, and billing/plan metadata."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    display_name: str
    plan: str = "pro_cyprus"
    subscription_status: str = "active"
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    monthly_quota_override: int | None = None
    report_language: ReportLanguage = ReportLanguage.EN
    branding: TenantBranding | None = None
    boq_rate_overrides: BoqRateOverrides | None = None
    has_api_key: bool = False
    created_at: str
    updated_at: str


class UserRecord(BaseModel):
    """Tenant-scoped firm member account (never includes password hashes or salts)."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    tenant_id: str
    email: str
    display_name: str
    role: Role
    auth_provider: str = "password"
    created_at: str
    updated_at: str


class ProjectSummary(BaseModel):
    """Tenant-scoped electrical engineering project metadata."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    tenant_id: str
    name: str
    description: str = ""
    revision_count: int = 0
    latest_supply_tier: str | None = None
    latest_boq_eur: float | None = None
    created_at: str
    updated_at: str


class RevisionSummary(BaseModel):
    """Lightweight summary row for a saved project revision."""

    model_config = ConfigDict(extra="forbid")

    revision_id: str
    project_id: str
    tenant_id: str
    revision_number: int
    notes: str = ""
    input_sha256: str
    supply_tier: str
    diversified_kva: float
    boq_grand_total_eur: float
    has_blockers: bool
    created_at: str


class RevisionRecord(BaseModel):
    """Complete persisted project revision including DesignOverrides and Phase2DesignResult."""

    model_config = ConfigDict(extra="forbid")

    summary: RevisionSummary
    overrides: DesignOverrides | None = None
    result: Phase2DesignResult


class UsageSummary(BaseModel):
    """Per-tenant metered SaaS API usage breakdown."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    total_events: int
    total_duration_ms: float
    total_payload_bytes: int
    counts_by_event_type: dict[str, int] = Field(default_factory=dict)


class TenantExportBundle(BaseModel):
    """EU GDPR Article 20 portable data export for a tenant (excludes secrets & hashes)."""

    model_config = ConfigDict(extra="forbid")

    exported_at_utc: str
    tenant: TenantProfile
    members: list[UserRecord] = Field(default_factory=list)
    projects: list[ProjectSummary] = Field(default_factory=list)
    revisions_by_project: dict[str, list[RevisionRecord]] = Field(default_factory=dict)
    usage: UsageSummary


@runtime_checkable
class WorkspaceStoreProtocol(Protocol):
    """Backend-agnostic multi-tenant store contract (SQLite default; Firestore Native ready)."""

    def close(self) -> None: ...
    def get_or_create_tenant(self, tenant_id: str) -> TenantProfile: ...
    def update_tenant_profile(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        report_language: ReportLanguage | None = None,
        branding: TenantBranding | None = None,
        boq_rate_overrides: BoqRateOverrides | None = None,
    ) -> TenantProfile: ...
    def update_tenant_billing(
        self,
        tenant_id: str,
        *,
        plan: str | None = None,
        subscription_status: str | None = None,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        monthly_quota_override: int | None = None,
        clear_quota_override: bool = False,
    ) -> TenantProfile: ...
    def find_tenant_by_stripe_customer(self, stripe_customer_id: str) -> str | None: ...
    def issue_tenant_api_key(self, tenant_id: str) -> str: ...
    def resolve_tenant_by_api_key(self, api_key: str) -> str | None: ...
    def create_user(
        self,
        tenant_id: str,
        email: str,
        display_name: str,
        *,
        role: Role = Role.ENGINEER,
        password: str | None = None,
        auth_provider: str = "password",
    ) -> UserRecord: ...
    def authenticate_user(self, email: str, password: str) -> UserRecord | None: ...
    def get_user_by_email(self, email: str) -> UserRecord | None: ...
    def get_user(self, tenant_id: str, user_id: str) -> UserRecord | None: ...
    def list_users(self, tenant_id: str) -> list[UserRecord]: ...
    def update_user_role(
        self, tenant_id: str, user_id: str, role: Role
    ) -> UserRecord | None: ...
    def update_user_password(
        self, tenant_id: str, user_id: str, new_password: str
    ) -> UserRecord | None: ...
    def delete_user(self, tenant_id: str, user_id: str) -> bool: ...
    def get_monthly_solve_count(
        self, tenant_id: str, month_key: str | None = None
    ) -> int: ...
    def increment_monthly_solve_count(
        self, tenant_id: str, month_key: str | None = None
    ) -> int: ...
    def get_quota_status(
        self, tenant_id: str, month_key: str | None = None
    ) -> QuotaStatus: ...
    def create_project(
        self, tenant_id: str, name: str, description: str = ""
    ) -> ProjectSummary: ...
    def get_project(self, tenant_id: str, project_id: str) -> ProjectSummary | None: ...
    def list_projects(self, tenant_id: str) -> list[ProjectSummary]: ...
    def delete_project(self, tenant_id: str, project_id: str) -> bool: ...
    def save_revision(
        self,
        tenant_id: str,
        project_id: str,
        result: Phase2DesignResult,
        *,
        overrides: DesignOverrides | None = None,
        notes: str = "",
    ) -> RevisionRecord: ...
    def list_revisions(self, tenant_id: str, project_id: str) -> list[RevisionSummary]: ...
    def get_revision(
        self, tenant_id: str, project_id: str, revision_number: int
    ) -> RevisionRecord | None: ...
    def get_latest_revision(self, tenant_id: str, project_id: str) -> RevisionRecord | None: ...
    def record_usage(
        self,
        tenant_id: str,
        event_type: str,
        duration_ms: float,
        payload_bytes: int,
        *,
        project_id: str | None = None,
    ) -> None: ...
    def get_usage_summary(self, tenant_id: str) -> UsageSummary: ...
    def export_tenant_data(self, tenant_id: str) -> TenantExportBundle: ...
    def delete_tenant_cascade(self, tenant_id: str) -> bool: ...


class WorkspaceStore:
    """Thread-safe SQLite repository enforcing strict per-tenant isolation on every query."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            env_path = os.environ.get("CYELEC_DB_PATH", "").strip()
            if env_path:
                db_path = Path(env_path)
            else:
                db_path = Path(tempfile.gettempdir()) / "cyelec_workspace.db"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            for attempt in range(10):
                try:
                    self._init_schema_once()
                    return
                except sqlite3.OperationalError as exc:
                    msg = str(exc).lower()
                    if ("locked" in msg or "busy" in msg) and attempt < 9:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    raise

    def _init_schema_once(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                PRAGMA busy_timeout=30000;
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    plan TEXT NOT NULL DEFAULT 'pro_cyprus',
                    subscription_status TEXT NOT NULL DEFAULT 'active',
                    stripe_customer_id TEXT,
                    stripe_subscription_id TEXT,
                    monthly_quota_override INTEGER,
                    report_language TEXT NOT NULL DEFAULT 'en',
                    branding_json TEXT,
                    boq_rate_overrides_json TEXT,
                    api_key_prefix TEXT,
                    api_key_hash TEXT,
                    api_key_salt TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'engineer',
                    auth_provider TEXT NOT NULL DEFAULT 'password',
                    password_hash TEXT,
                    password_salt TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
                );
                CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);

                CREATE TABLE IF NOT EXISTS tenant_monthly_quotas (
                    tenant_id TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    solve_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, month_key),
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
                );

                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
                );
                CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects(tenant_id);

                CREATE TABLE IF NOT EXISTS revisions (
                    revision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    input_sha256 TEXT NOT NULL,
                    overrides_json TEXT,
                    result_json TEXT NOT NULL,
                    supply_tier TEXT NOT NULL,
                    diversified_kva REAL NOT NULL,
                    boq_grand_total_eur REAL NOT NULL,
                    has_blockers INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, revision_number),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_revisions_tenant_proj
                    ON revisions(tenant_id, project_id, revision_number);

                CREATE TABLE IF NOT EXISTS usage_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT,
                    event_type TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    payload_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_usage_tenant ON usage_events(tenant_id);
                """
            )
            # Safe idempotent migrations for pre-existing SQLite databases
            cols = {
                str(r["name"])
                for r in self._conn.execute("PRAGMA table_info(tenants)").fetchall()
            }
            if "api_key_prefix" not in cols:
                self._conn.execute("ALTER TABLE tenants ADD COLUMN api_key_prefix TEXT")
            if "boq_rate_overrides_json" not in cols:
                self._conn.execute("ALTER TABLE tenants ADD COLUMN boq_rate_overrides_json TEXT")
            if "subscription_status" not in cols:
                self._conn.execute(
                    "ALTER TABLE tenants ADD COLUMN subscription_status "
                    "TEXT NOT NULL DEFAULT 'active'"
                )
            if "stripe_customer_id" not in cols:
                self._conn.execute("ALTER TABLE tenants ADD COLUMN stripe_customer_id TEXT")
            if "stripe_subscription_id" not in cols:
                self._conn.execute("ALTER TABLE tenants ADD COLUMN stripe_subscription_id TEXT")
            if "monthly_quota_override" not in cols:
                self._conn.execute(
                    "ALTER TABLE tenants ADD COLUMN monthly_quota_override INTEGER"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tenants_key_prefix ON tenants(api_key_prefix)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tenants_stripe_cust "
                "ON tenants(stripe_customer_id)"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Tenant Profile, Branding & API Keys
    # ------------------------------------------------------------------
    def get_or_create_tenant(self, tenant_id: str) -> TenantProfile:
        now = _utc_now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO tenants (
                        tenant_id, display_name, plan, report_language, created_at, updated_at
                    ) VALUES (?, ?, 'pro_cyprus', 'en', ?, ?)
                    """,
                    (tenant_id, tenant_id, now, now),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)
                ).fetchone()
            if row is None:
                raise RuntimeError(f"Tenant '{tenant_id}' missing immediately after insert.")
            return self._row_to_tenant(row)

    def _row_to_tenant(self, row: sqlite3.Row) -> TenantProfile:
        branding = (
            TenantBranding.model_validate_json(str(row["branding_json"]))
            if row["branding_json"]
            else None
        )
        boq_rates = (
            BoqRateOverrides.model_validate_json(str(row["boq_rate_overrides_json"]))
            if row["boq_rate_overrides_json"]
            else None
        )
        raw_qo = row["monthly_quota_override"]
        return TenantProfile(
            tenant_id=str(row["tenant_id"]),
            display_name=str(row["display_name"]),
            plan=str(row["plan"]),
            subscription_status=str(row["subscription_status"] or "active"),
            stripe_customer_id=(
                str(row["stripe_customer_id"]) if row["stripe_customer_id"] else None
            ),
            stripe_subscription_id=(
                str(row["stripe_subscription_id"]) if row["stripe_subscription_id"] else None
            ),
            monthly_quota_override=int(raw_qo) if raw_qo is not None else None,
            report_language=ReportLanguage(str(row["report_language"])),
            branding=branding,
            boq_rate_overrides=boq_rates,
            has_api_key=bool(row["api_key_hash"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            user_id=str(row["user_id"]),
            tenant_id=str(row["tenant_id"]),
            email=str(row["email"]),
            display_name=str(row["display_name"]),
            role=Role(str(row["role"])),
            auth_provider=str(row["auth_provider"] or "password"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def update_tenant_profile(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        report_language: ReportLanguage | None = None,
        branding: TenantBranding | None = None,
        boq_rate_overrides: BoqRateOverrides | None = None,
    ) -> TenantProfile:
        current = self.get_or_create_tenant(tenant_id)
        new_name = display_name if display_name is not None else current.display_name
        new_lang = report_language if report_language is not None else current.report_language
        new_branding = branding if branding is not None else current.branding
        new_boq = (
            boq_rate_overrides if boq_rate_overrides is not None else current.boq_rate_overrides
        )
        b_json = new_branding.model_dump_json() if new_branding is not None else None
        boq_json = new_boq.model_dump_json() if new_boq is not None else None
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                UPDATE tenants
                SET display_name = ?, report_language = ?, branding_json = ?,
                    boq_rate_overrides_json = ?, updated_at = ?
                WHERE tenant_id = ?
                """,
                (new_name, new_lang.value, b_json, boq_json, now, tenant_id),
            )
            self._conn.commit()
        return self.get_or_create_tenant(tenant_id)

    def update_tenant_billing(
        self,
        tenant_id: str,
        *,
        plan: str | None = None,
        subscription_status: str | None = None,
        stripe_customer_id: str | None = None,
        stripe_subscription_id: str | None = None,
        monthly_quota_override: int | None = None,
        clear_quota_override: bool = False,
    ) -> TenantProfile:
        current = self.get_or_create_tenant(tenant_id)
        new_plan = plan if plan is not None else current.plan
        new_status = (
            subscription_status if subscription_status is not None else current.subscription_status
        )
        new_cust = (
            stripe_customer_id if stripe_customer_id is not None else current.stripe_customer_id
        )
        new_sub = (
            stripe_subscription_id
            if stripe_subscription_id is not None
            else current.stripe_subscription_id
        )
        new_qo = (
            None
            if clear_quota_override
            else (
                monthly_quota_override
                if monthly_quota_override is not None
                else current.monthly_quota_override
            )
        )
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                UPDATE tenants
                SET plan = ?, subscription_status = ?, stripe_customer_id = ?,
                    stripe_subscription_id = ?, monthly_quota_override = ?, updated_at = ?
                WHERE tenant_id = ?
                """,
                (new_plan, new_status, new_cust, new_sub, new_qo, now, tenant_id),
            )
            self._conn.commit()
        return self.get_or_create_tenant(tenant_id)

    def find_tenant_by_stripe_customer(self, stripe_customer_id: str) -> str | None:
        if not stripe_customer_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT tenant_id FROM tenants WHERE stripe_customer_id = ?",
                (stripe_customer_id.strip(),),
            ).fetchone()
        return str(row["tenant_id"]) if row is not None else None

    # ------------------------------------------------------------------
    # Team Users, RBAC & Monthly Solve Quotas
    # ------------------------------------------------------------------
    def create_user(
        self,
        tenant_id: str,
        email: str,
        display_name: str,
        *,
        role: Role = Role.ENGINEER,
        password: str | None = None,
        auth_provider: str = "password",
    ) -> UserRecord:
        norm_email = email.strip().lower()
        if not norm_email or "@" not in norm_email:
            raise ValueError("A valid email address is required.")
        tenant = self.get_or_create_tenant(tenant_id)
        plan_spec = resolve_plan_spec(tenant.plan)
        existing_members = self.list_users(tenant_id)
        if len(existing_members) >= plan_spec.max_seats:
            raise ValueError(
                f"Seat limit reached ({plan_spec.max_seats} seats on plan '{plan_spec.plan_id}'). "
                "Upgrade plan to add more firm seats."
            )

        pw_hash: str | None = None
        pw_salt: str | None = None
        if password is not None:
            pw_hash, pw_salt = hash_password(password)
        elif auth_provider == "password":
            raise ValueError("Password is required for password-based accounts.")

        uid = f"usr_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        with self._lock:
            dup = self._conn.execute(
                "SELECT user_id FROM users WHERE email = ?", (norm_email,)
            ).fetchone()
            if dup is not None:
                raise ValueError("An account with this email already exists.")
            self._conn.execute(
                """
                INSERT INTO users (
                    user_id, tenant_id, email, display_name, role, auth_provider,
                    password_hash, password_salt, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    tenant_id,
                    norm_email,
                    display_name.strip() or norm_email.split("@", 1)[0],
                    role.value,
                    auth_provider,
                    pw_hash,
                    pw_salt,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (uid,)
            ).fetchone()
        if row is None:
            raise RuntimeError(f"User '{uid}' missing immediately after insert.")
        return self._row_to_user(row)

    def authenticate_user(self, email: str, password: str) -> UserRecord | None:
        norm_email = email.strip().lower()
        if not norm_email or not password:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (norm_email,)
            ).fetchone()
        if row is None or not row["password_hash"] or not row["password_salt"]:
            return None
        if not verify_password(password, str(row["password_hash"]), str(row["password_salt"])):
            return None
        return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> UserRecord | None:
        norm_email = email.strip().lower()
        if not norm_email:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (norm_email,)
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def get_user(self, tenant_id: str, user_id: str) -> UserRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ? AND tenant_id = ?",
                (user_id, tenant_id),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def list_users(self, tenant_id: str) -> list[UserRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users WHERE tenant_id = ? ORDER BY created_at ASC",
                (tenant_id,),
            ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def update_user_role(
        self, tenant_id: str, user_id: str, role: Role
    ) -> UserRecord | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ? AND tenant_id = ?",
                (user_id, tenant_id),
            ).fetchone()
            if cur is None:
                return None
            if str(cur["role"]) == Role.OWNER.value and role != Role.OWNER:
                owners = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM users WHERE tenant_id = ? AND role = ?",
                    (tenant_id, Role.OWNER.value),
                ).fetchone()
                if owners is not None and int(owners["cnt"]) <= 1:
                    raise ValueError("Cannot demote the last remaining owner of a tenant.")
            self._conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE user_id = ? AND tenant_id = ?",
                (role.value, _utc_now(), user_id, tenant_id),
            )
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ? AND tenant_id = ?",
                (user_id, tenant_id),
            ).fetchone()
        return self._row_to_user(updated) if updated is not None else None

    def update_user_password(
        self, tenant_id: str, user_id: str, new_password: str
    ) -> UserRecord | None:
        pw_hash, pw_salt = hash_password(new_password)
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ? AND tenant_id = ?",
                (user_id, tenant_id),
            ).fetchone()
            if cur is None:
                return None
            self._conn.execute(
                """
                UPDATE users
                SET password_hash = ?, password_salt = ?, auth_provider = 'password', updated_at = ?
                WHERE user_id = ? AND tenant_id = ?
                """,
                (pw_hash, pw_salt, _utc_now(), user_id, tenant_id),
            )
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ? AND tenant_id = ?",
                (user_id, tenant_id),
            ).fetchone()
        return self._row_to_user(updated) if updated is not None else None

    def delete_user(self, tenant_id: str, user_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ? AND tenant_id = ?",
                (user_id, tenant_id),
            ).fetchone()
            if cur is None:
                return False
            if str(cur["role"]) == Role.OWNER.value:
                owners = self._conn.execute(
                    "SELECT COUNT(*) AS cnt FROM users WHERE tenant_id = ? AND role = ?",
                    (tenant_id, Role.OWNER.value),
                ).fetchone()
                if owners is not None and int(owners["cnt"]) <= 1:
                    raise ValueError("Cannot remove the last remaining owner of a tenant.")
            self._conn.execute(
                "DELETE FROM users WHERE user_id = ? AND tenant_id = ?",
                (user_id, tenant_id),
            )
            self._conn.commit()
            return True

    def get_monthly_solve_count(
        self, tenant_id: str, month_key: str | None = None
    ) -> int:
        mk = month_key or current_month_key()
        with self._lock:
            row = self._conn.execute(
                "SELECT solve_count FROM tenant_monthly_quotas "
                "WHERE tenant_id = ? AND month_key = ?",
                (tenant_id, mk),
            ).fetchone()
        return int(row["solve_count"]) if row is not None else 0

    def increment_monthly_solve_count(
        self, tenant_id: str, month_key: str | None = None
    ) -> int:
        self.get_or_create_tenant(tenant_id)
        mk = month_key or current_month_key()
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tenant_monthly_quotas (tenant_id, month_key, solve_count, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(tenant_id, month_key)
                DO UPDATE SET solve_count = solve_count + 1, updated_at = excluded.updated_at
                """,
                (tenant_id, mk, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT solve_count FROM tenant_monthly_quotas "
                "WHERE tenant_id = ? AND month_key = ?",
                (tenant_id, mk),
            ).fetchone()
        return int(row["solve_count"]) if row is not None else 1

    def get_quota_status(
        self, tenant_id: str, month_key: str | None = None
    ) -> QuotaStatus:
        mk = month_key or current_month_key()
        tenant = self.get_or_create_tenant(tenant_id)
        plan_spec = resolve_plan_spec(tenant.plan)
        limit = (
            tenant.monthly_quota_override
            if tenant.monthly_quota_override is not None
            else plan_spec.monthly_solve_quota
        )
        used = self.get_monthly_solve_count(tenant_id, mk)
        return QuotaStatus(
            tenant_id=tenant_id,
            plan_id=plan_spec.plan_id,
            month_key=mk,
            solves_used=used,
            solves_limit=limit,
            solves_remaining=max(0, limit - used),
            exceeded=used >= limit,
        )

    def issue_tenant_api_key(self, tenant_id: str) -> str:
        """Issue or rotate a prefix-indexed per-tenant API key (salted PBKDF2 stored)."""
        self.get_or_create_tenant(tenant_id)
        raw_key = generate_api_key()
        prefix = extract_key_prefix(raw_key)
        digest, salt = hash_api_key(raw_key)
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                UPDATE tenants
                SET api_key_prefix = ?, api_key_hash = ?, api_key_salt = ?, updated_at = ?
                WHERE tenant_id = ?
                """,
                (prefix, digest, salt, now, tenant_id),
            )
            self._conn.commit()
        return raw_key

    def resolve_tenant_by_api_key(self, api_key: str) -> str | None:
        """O(1) key-prefix indexed lookup + constant-time PBKDF2 verification."""
        if not api_key:
            return None
        prefix = extract_key_prefix(api_key)
        with self._lock:
            if prefix is not None:
                rows = self._conn.execute(
                    "SELECT tenant_id, api_key_hash, api_key_salt FROM tenants "
                    "WHERE api_key_prefix = ? AND api_key_hash IS NOT NULL "
                    "AND api_key_salt IS NOT NULL",
                    (prefix,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT tenant_id, api_key_hash, api_key_salt FROM tenants "
                    "WHERE api_key_prefix IS NULL AND api_key_hash IS NOT NULL "
                    "AND api_key_salt IS NOT NULL"
                ).fetchall()
        for row in rows:
            if verify_api_key(api_key, str(row["api_key_hash"]), str(row["api_key_salt"])):
                return str(row["tenant_id"])
        return None

    # ------------------------------------------------------------------
    # Projects (Strictly Tenant-Scoped)
    # ------------------------------------------------------------------
    def create_project(
        self,
        tenant_id: str,
        name: str,
        description: str = "",
    ) -> ProjectSummary:
        self.get_or_create_tenant(tenant_id)
        project_id = f"prj_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO projects (
                    project_id, tenant_id, name, description, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (project_id, tenant_id, name.strip() or "Untitled Project", description, now, now),
            )
            self._conn.commit()
        proj = self.get_project(tenant_id, project_id)
        if proj is None:
            raise RuntimeError(f"Project '{project_id}' missing immediately after insert.")
        return proj

    def _hydrate_project(self, row: sqlite3.Row) -> ProjectSummary:
        pid = str(row["project_id"])
        tid = str(row["tenant_id"])
        rev_meta = self._conn.execute(
            """
            SELECT COUNT(*) AS cnt,
                   (SELECT supply_tier FROM revisions
                    WHERE project_id = ? AND tenant_id = ?
                    ORDER BY revision_number DESC LIMIT 1) AS latest_tier,
                   (SELECT boq_grand_total_eur FROM revisions
                    WHERE project_id = ? AND tenant_id = ?
                    ORDER BY revision_number DESC LIMIT 1) AS latest_boq
            FROM revisions
            WHERE project_id = ? AND tenant_id = ?
            """,
            (pid, tid, pid, tid, pid, tid),
        ).fetchone()
        cnt = int(rev_meta["cnt"]) if rev_meta is not None else 0
        latest_tier = (
            str(rev_meta["latest_tier"])
            if (rev_meta is not None and rev_meta["latest_tier"] is not None)
            else None
        )
        latest_boq = (
            float(rev_meta["latest_boq"])
            if (rev_meta is not None and rev_meta["latest_boq"] is not None)
            else None
        )
        return ProjectSummary(
            project_id=pid,
            tenant_id=tid,
            name=str(row["name"]),
            description=str(row["description"]),
            revision_count=cnt,
            latest_supply_tier=latest_tier,
            latest_boq_eur=latest_boq,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def get_project(self, tenant_id: str, project_id: str) -> ProjectSummary | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE project_id = ? AND tenant_id = ?",
                (project_id, tenant_id),
            ).fetchone()
            return self._hydrate_project(row) if row is not None else None

    def list_projects(self, tenant_id: str) -> list[ProjectSummary]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects WHERE tenant_id = ? ORDER BY updated_at DESC",
                (tenant_id,),
            ).fetchall()
            return [self._hydrate_project(r) for r in rows]

    # ------------------------------------------------------------------
    # Revisions (Strictly Tenant-Scoped)
    # ------------------------------------------------------------------
    def save_revision(
        self,
        tenant_id: str,
        project_id: str,
        result: Phase2DesignResult,
        *,
        overrides: DesignOverrides | None = None,
        notes: str = "",
    ) -> RevisionRecord:
        with self._lock:
            proj = self._conn.execute(
                "SELECT project_id FROM projects WHERE project_id = ? AND tenant_id = ?",
                (project_id, tenant_id),
            ).fetchone()
            if proj is None:
                raise KeyError(f"Project '{project_id}' not found for tenant '{tenant_id}'.")

            max_row = self._conn.execute(
                "SELECT COALESCE(MAX(revision_number), 0) AS mx "
                "FROM revisions WHERE project_id = ? AND tenant_id = ?",
                (project_id, tenant_id),
            ).fetchone()
            rev_num = int(max_row["mx"]) + 1
            rev_id = f"rev_{uuid.uuid4().hex[:12]}"
            now = _utc_now()

            d = result.design
            boq_total = d.boq.grand_total_eur if d.boq is not None else 0.0
            ov_json = overrides.model_dump_json() if overrides is not None else None

            self._conn.execute(
                """
                INSERT INTO revisions (
                    revision_id, project_id, tenant_id, revision_number, notes,
                    input_sha256, overrides_json, result_json, supply_tier,
                    diversified_kva, boq_grand_total_eur, has_blockers, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rev_id,
                    project_id,
                    tenant_id,
                    rev_num,
                    notes,
                    d.provenance.input_sha256,
                    ov_json,
                    result.model_dump_json(),
                    d.eac_compliance.recommended_supply_tier,
                    d.eac_compliance.total_diversified_kva,
                    boq_total,
                    1 if d.has_blockers else 0,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE projects SET updated_at = ? WHERE project_id = ? AND tenant_id = ?",
                (now, project_id, tenant_id),
            )
            self._conn.commit()

        rec = self.get_revision(tenant_id, project_id, rev_num)
        if rec is None:
            raise RuntimeError(
                f"Revision {rev_num} for project '{project_id}' missing after insert."
            )
        return rec

    def _row_to_rev_summary(self, row: sqlite3.Row) -> RevisionSummary:
        return RevisionSummary(
            revision_id=str(row["revision_id"]),
            project_id=str(row["project_id"]),
            tenant_id=str(row["tenant_id"]),
            revision_number=int(row["revision_number"]),
            notes=str(row["notes"]),
            input_sha256=str(row["input_sha256"]),
            supply_tier=str(row["supply_tier"]),
            diversified_kva=float(row["diversified_kva"]),
            boq_grand_total_eur=float(row["boq_grand_total_eur"]),
            has_blockers=bool(row["has_blockers"]),
            created_at=str(row["created_at"]),
        )

    def list_revisions(self, tenant_id: str, project_id: str) -> list[RevisionSummary]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT revision_id, project_id, tenant_id, revision_number, notes,
                       input_sha256, supply_tier, diversified_kva, boq_grand_total_eur,
                       has_blockers, created_at
                FROM revisions
                WHERE project_id = ? AND tenant_id = ?
                ORDER BY revision_number ASC
                """,
                (project_id, tenant_id),
            ).fetchall()
            return [self._row_to_rev_summary(r) for r in rows]

    def get_revision(
        self,
        tenant_id: str,
        project_id: str,
        revision_number: int,
    ) -> RevisionRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM revisions
                WHERE project_id = ? AND tenant_id = ? AND revision_number = ?
                """,
                (project_id, tenant_id, revision_number),
            ).fetchone()
            if row is None:
                return None
            summary = self._row_to_rev_summary(row)
            overrides = (
                DesignOverrides.model_validate_json(str(row["overrides_json"]))
                if row["overrides_json"]
                else None
            )
            result = Phase2DesignResult.model_validate_json(str(row["result_json"]))
            return RevisionRecord(summary=summary, overrides=overrides, result=result)

    def get_latest_revision(self, tenant_id: str, project_id: str) -> RevisionRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT revision_number FROM revisions
                WHERE project_id = ? AND tenant_id = ?
                ORDER BY revision_number DESC LIMIT 1
                """,
                (project_id, tenant_id),
            ).fetchone()
            if row is None:
                return None
            return self.get_revision(tenant_id, project_id, int(row["revision_number"]))

    # ------------------------------------------------------------------
    # Usage Metering (Strictly Tenant-Scoped)
    # ------------------------------------------------------------------
    def record_usage(
        self,
        tenant_id: str,
        event_type: str,
        duration_ms: float,
        payload_bytes: int,
        *,
        project_id: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO usage_events (
                    event_id, tenant_id, project_id, event_type,
                    duration_ms, payload_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"evt_{uuid.uuid4().hex[:12]}",
                    tenant_id,
                    project_id,
                    event_type,
                    round(duration_ms, 2),
                    max(0, int(payload_bytes)),
                    _utc_now(),
                ),
            )
            self._conn.commit()

    def get_usage_summary(self, tenant_id: str) -> UsageSummary:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_type, COUNT(*) AS cnt,
                       SUM(duration_ms) AS dur, SUM(payload_bytes) AS bytes
                FROM usage_events
                WHERE tenant_id = ?
                GROUP BY event_type
                """,
                (tenant_id,),
            ).fetchall()
        counts: dict[str, int] = {}
        total_events = 0
        total_ms = 0.0
        total_bytes = 0
        for r in rows:
            cnt = int(r["cnt"])
            counts[str(r["event_type"])] = cnt
            total_events += cnt
            total_ms += float(r["dur"] or 0.0)
            total_bytes += int(r["bytes"] or 0)
        return UsageSummary(
            tenant_id=tenant_id,
            total_events=total_events,
            total_duration_ms=round(total_ms, 2),
            total_payload_bytes=total_bytes,
            counts_by_event_type=counts,
        )

    # ------------------------------------------------------------------
    # EU GDPR Lifecycle: Project Deletion, Art. 20 Export & Art. 17 Erasure
    # ------------------------------------------------------------------
    def delete_project(self, tenant_id: str, project_id: str) -> bool:
        """Atomically delete a tenant-owned project and all its revisions."""
        with self._lock:
            row = self._conn.execute(
                "SELECT project_id FROM projects WHERE project_id = ? AND tenant_id = ?",
                (project_id, tenant_id),
            ).fetchone()
            if row is None:
                return False
            self._conn.execute(
                "DELETE FROM revisions WHERE project_id = ? AND tenant_id = ?",
                (project_id, tenant_id),
            )
            self._conn.execute(
                "DELETE FROM projects WHERE project_id = ? AND tenant_id = ?",
                (project_id, tenant_id),
            )
            self._conn.commit()
            return True

    def export_tenant_data(self, tenant_id: str) -> TenantExportBundle:
        """GDPR Article 20 portable JSON export for a tenant (excludes secrets & hashes)."""
        with self._lock:
            tenant = self.get_or_create_tenant(tenant_id)
            members = self.list_users(tenant_id)
            projects = self.list_projects(tenant_id)
            revisions_by_project: dict[str, list[RevisionRecord]] = {}
            for proj in projects:
                rev_summaries = self.list_revisions(tenant_id, proj.project_id)
                recs: list[RevisionRecord] = []
                for rs in rev_summaries:
                    rec = self.get_revision(tenant_id, proj.project_id, rs.revision_number)
                    if rec is not None:
                        recs.append(rec)
                revisions_by_project[proj.project_id] = recs
            usage = self.get_usage_summary(tenant_id)
        return TenantExportBundle(
            exported_at_utc=_utc_now(),
            tenant=tenant,
            members=members,
            projects=projects,
            revisions_by_project=revisions_by_project,
            usage=usage,
        )

    def delete_tenant_cascade(self, tenant_id: str) -> bool:
        """GDPR Article 17 right to erasure: atomically erase all data for a tenant."""
        with self._lock:
            row = self._conn.execute(
                "SELECT tenant_id FROM tenants WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            self._conn.execute("DELETE FROM usage_events WHERE tenant_id = ?", (tenant_id,))
            self._conn.execute(
                "DELETE FROM tenant_monthly_quotas WHERE tenant_id = ?", (tenant_id,)
            )
            self._conn.execute("DELETE FROM users WHERE tenant_id = ?", (tenant_id,))
            self._conn.execute("DELETE FROM revisions WHERE tenant_id = ?", (tenant_id,))
            self._conn.execute("DELETE FROM projects WHERE tenant_id = ?", (tenant_id,))
            self._conn.execute("DELETE FROM tenants WHERE tenant_id = ?", (tenant_id,))
            self._conn.commit()
            return row is not None

