"""Google Cloud Firestore Native + GCS gzipped-blob WorkspaceStoreProtocol implementation.

Designed for 100% GCP EU (europe-west1) scale-to-zero Cloud Run deployments:
- €0.00/month idle cost (no always-on Cloud SQL or VPC Serverless Connector)
- Native GCP IAM / Workload Identity authentication (zero DB passwords)
- Strict hierarchical tenant isolation:
    tenants/{tenant_id}
    tenants/{tenant_id}/projects/{project_id}
    tenants/{tenant_id}/projects/{project_id}/revisions/{revision_number:06d}
    tenants/{tenant_id}/usage_events/{event_id}
- Large Phase2DesignResult payloads gzipped in EU Google Cloud Storage (avoids Firestore's
  1 MiB document size cap) and cascade-deleted on project delete & GDPR Art. 17 erasure.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

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
from cyelec.saas.store import (
    ProjectSummary,
    RevisionRecord,
    RevisionSummary,
    TenantExportBundle,
    TenantProfile,
    UsageSummary,
    UserRecord,
    WorkspaceStore,
    WorkspaceStoreProtocol,
)


def _utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@runtime_checkable
class BlobStoreProtocol(Protocol):
    """Minimal object storage contract for gzipped revision JSON snapshots."""

    def put_gzip_json(self, path: str, json_text: str) -> None: ...
    def get_gzip_json(self, path: str) -> str | None: ...
    def delete_blob(self, path: str) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...


class GcsBlobStore:
    """Google Cloud Storage implementation of BlobStoreProtocol (Apache-2.0 SDK)."""

    def __init__(self, bucket_name: str, *, project_id: str | None = None) -> None:
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise ImportError(
                "GCS blob store requires optional 'gcp' extra: pip install 'cyelec-pipeline[gcp]'"
            ) from exc
        self._client = storage.Client(project=project_id) if project_id else storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def put_gzip_json(self, path: str, json_text: str) -> None:
        blob = self._bucket.blob(path)
        payload = gzip.compress(json_text.encode("utf-8"), compresslevel=6)
        blob.content_encoding = "gzip"
        blob.upload_from_string(payload, content_type="application/json")

    def get_gzip_json(self, path: str) -> str | None:
        blob = self._bucket.blob(path)
        if not blob.exists():
            return None
        raw: bytes = blob.download_as_bytes()
        # GCS may transparently decompress gzip if content_encoding=gzip; handle both safely
        if raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw).decode("utf-8")
        return raw.decode("utf-8")

    def delete_blob(self, path: str) -> None:
        blob = self._bucket.blob(path)
        if blob.exists():
            blob.delete()

    def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        for blob in self._client.list_blobs(self._bucket, prefix=prefix):
            blob.delete()
            deleted += 1
        return deleted


class FirestoreWorkspaceStore:
    """Strictly tenant-isolated GCP Firestore Native + GCS implementation of WorkspaceStoreProtocol.

    Supports both live GCP ADC credentials and dependency-injected clients for deterministic
    offline testing without live GCP credentials.
    """

    def __init__(
        self,
        *,
        gcs_bucket: str | None = None,
        project_id: str | None = None,
        database: str = "(default)",
        firestore_client: Any | None = None,
        blob_store: BlobStoreProtocol | None = None,
    ) -> None:
        if firestore_client is not None:
            self._db: Any = firestore_client
        else:
            try:
                from google.cloud import firestore
            except ImportError as exc:
                raise ImportError(
                    "FirestoreWorkspaceStore requires optional 'gcp' extra: "
                    "pip install 'cyelec-pipeline[gcp]'"
                ) from exc
            kwargs: dict[str, Any] = {"database": database}
            if project_id:
                kwargs["project"] = project_id
            self._db = firestore.Client(**kwargs)

        if blob_store is not None:
            self._blobs: BlobStoreProtocol = blob_store
        else:
            if not gcs_bucket:
                raise ValueError(
                    "CYELEC_GCS_BUCKET (gcs_bucket) is required when using FirestoreWorkspaceStore."
                )
            self._blobs = GcsBlobStore(gcs_bucket, project_id=project_id)

        self.database = database

    def close(self) -> None:
        close_fn = getattr(self._db, "close", None)
        if callable(close_fn):
            close_fn()

    # ------------------------------------------------------------------
    # Internal Document Reference Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_id(value: str, label: str) -> str:
        clean = value.strip()
        if (
            not clean
            or len(clean) > 128
            or "/" in clean
            or "\\" in clean
            or ".." in clean
            or "\x00" in clean
        ):
            raise ValueError(f"Invalid {label}: {value!r}")
        return clean

    def _tenant_ref(self, tenant_id: str) -> Any:
        return self._db.collection("tenants").document(self._safe_id(tenant_id, "tenant_id"))

    def _users_col(self, tenant_id: str) -> Any:
        return self._tenant_ref(tenant_id).collection("users")

    def _email_index_ref(self, norm_email: str) -> Any:
        ehash = hashlib.sha256(norm_email.encode("utf-8")).hexdigest()[:32]
        return self._db.collection("user_emails").document(ehash)

    def _quota_ref(self, tenant_id: str, month_key: str) -> Any:
        safe_mk = self._safe_id(month_key, "month_key")
        return self._tenant_ref(tenant_id).collection("meta").document(f"quota_{safe_mk}")

    def _projects_col(self, tenant_id: str) -> Any:
        return self._tenant_ref(tenant_id).collection("projects")

    def _project_ref(self, tenant_id: str, project_id: str) -> Any:
        return self._projects_col(tenant_id).document(self._safe_id(project_id, "project_id"))

    def _revisions_col(self, tenant_id: str, project_id: str) -> Any:
        return self._project_ref(tenant_id, project_id).collection("revisions")

    def _usage_col(self, tenant_id: str) -> Any:
        return self._tenant_ref(tenant_id).collection("usage_events")

    def _doc_to_tenant(self, data: dict[str, Any]) -> TenantProfile:
        branding = (
            TenantBranding.model_validate_json(str(data["branding_json"]))
            if data.get("branding_json")
            else None
        )
        boq_rates = (
            BoqRateOverrides.model_validate_json(str(data["boq_rate_overrides_json"]))
            if data.get("boq_rate_overrides_json")
            else None
        )
        raw_qo = data.get("monthly_quota_override")
        return TenantProfile(
            tenant_id=str(data["tenant_id"]),
            display_name=str(data["display_name"]),
            plan=str(data.get("plan", "pro_cyprus")),
            subscription_status=str(data.get("subscription_status", "active")),
            stripe_customer_id=(
                str(data["stripe_customer_id"]) if data.get("stripe_customer_id") else None
            ),
            stripe_subscription_id=(
                str(data["stripe_subscription_id"])
                if data.get("stripe_subscription_id")
                else None
            ),
            monthly_quota_override=int(raw_qo) if raw_qo is not None else None,
            report_language=ReportLanguage(str(data.get("report_language", "en"))),
            branding=branding,
            boq_rate_overrides=boq_rates,
            has_api_key=bool(data.get("api_key_hash")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )

    @staticmethod
    def _doc_to_user(data: dict[str, Any]) -> UserRecord:
        return UserRecord(
            user_id=str(data["user_id"]),
            tenant_id=str(data["tenant_id"]),
            email=str(data["email"]),
            display_name=str(data["display_name"]),
            role=Role(str(data.get("role", "engineer"))),
            auth_provider=str(data.get("auth_provider", "password")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )

    def _doc_to_project(self, data: dict[str, Any]) -> ProjectSummary:
        return ProjectSummary(
            project_id=str(data["project_id"]),
            tenant_id=str(data["tenant_id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            revision_count=int(data.get("revision_count", 0)),
            latest_supply_tier=(
                str(data["latest_supply_tier"])
                if data.get("latest_supply_tier") is not None
                else None
            ),
            latest_boq_eur=(
                float(data["latest_boq_eur"])
                if data.get("latest_boq_eur") is not None
                else None
            ),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )

    def _doc_to_rev_summary(self, data: dict[str, Any]) -> RevisionSummary:
        return RevisionSummary(
            revision_id=str(data["revision_id"]),
            project_id=str(data["project_id"]),
            tenant_id=str(data["tenant_id"]),
            revision_number=int(data["revision_number"]),
            notes=str(data.get("notes", "")),
            input_sha256=str(data["input_sha256"]),
            supply_tier=str(data["supply_tier"]),
            diversified_kva=float(data["diversified_kva"]),
            boq_grand_total_eur=float(data["boq_grand_total_eur"]),
            has_blockers=bool(data.get("has_blockers", False)),
            created_at=str(data["created_at"]),
        )

    # ------------------------------------------------------------------
    # Tenant Profile, Branding, Custom BOQ Rates & O(1) Prefix API Keys
    # ------------------------------------------------------------------
    def get_or_create_tenant(self, tenant_id: str) -> TenantProfile:
        ref = self._tenant_ref(tenant_id)
        snap = ref.get()
        if snap.exists:
            return self._doc_to_tenant(snap.to_dict() or {})
        now = _utc_now()
        doc: dict[str, Any] = {
            "tenant_id": tenant_id,
            "display_name": tenant_id,
            "plan": "pro_cyprus",
            "subscription_status": "active",
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "monthly_quota_override": None,
            "report_language": ReportLanguage.EN.value,
            "branding_json": None,
            "boq_rate_overrides_json": None,
            "api_key_prefix": None,
            "api_key_hash": None,
            "api_key_salt": None,
            "created_at": now,
            "updated_at": now,
        }
        ref.set(doc)
        return self._doc_to_tenant(doc)

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
        now = _utc_now()
        self._tenant_ref(tenant_id).set(
            {
                "display_name": new_name,
                "report_language": new_lang.value,
                "branding_json": new_branding.model_dump_json() if new_branding else None,
                "boq_rate_overrides_json": new_boq.model_dump_json() if new_boq else None,
                "updated_at": now,
            },
            merge=True,
        )
        return self.get_or_create_tenant(tenant_id)

    def issue_tenant_api_key(self, tenant_id: str) -> str:
        """Issue or rotate a prefix-indexed per-tenant API key (salted PBKDF2 stored)."""
        self.get_or_create_tenant(tenant_id)
        raw_key = generate_api_key()
        prefix = extract_key_prefix(raw_key)
        digest, salt = hash_api_key(raw_key)
        now = _utc_now()
        self._tenant_ref(tenant_id).set(
            {
                "api_key_prefix": prefix,
                "api_key_hash": digest,
                "api_key_salt": salt,
                "updated_at": now,
            },
            merge=True,
        )
        return raw_key

    def resolve_tenant_by_api_key(self, api_key: str) -> str | None:
        """O(1) single-field Firestore indexed lookup on api_key_prefix + PBKDF2 verification."""
        if not api_key:
            return None
        prefix = extract_key_prefix(api_key)
        if prefix is None:
            return None
        query = self._db.collection("tenants").where("api_key_prefix", "==", prefix)
        for snap in query.stream():
            data = snap.to_dict() or {}
            digest = data.get("api_key_hash")
            salt = data.get("api_key_salt")
            if digest and salt and verify_api_key(api_key, str(digest), str(salt)):
                return str(data["tenant_id"])
        return None

    # ------------------------------------------------------------------
    # Projects (Strictly Scoped under tenants/{tenant_id}/projects)
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
        doc: dict[str, Any] = {
            "project_id": project_id,
            "tenant_id": tenant_id,
            "name": name.strip() or "Untitled Project",
            "description": description,
            "revision_count": 0,
            "latest_supply_tier": None,
            "latest_boq_eur": None,
            "created_at": now,
            "updated_at": now,
        }
        self._project_ref(tenant_id, project_id).set(doc)
        return self._doc_to_project(doc)

    def get_project(self, tenant_id: str, project_id: str) -> ProjectSummary | None:
        snap = self._project_ref(tenant_id, project_id).get()
        if not snap.exists:
            return None
        return self._doc_to_project(snap.to_dict() or {})

    def list_projects(self, tenant_id: str) -> list[ProjectSummary]:
        items = [
            self._doc_to_project(snap.to_dict() or {})
            for snap in self._projects_col(tenant_id).stream()
        ]
        items.sort(key=lambda p: p.updated_at, reverse=True)
        return items

    def _usage_summary_ref(self, tenant_id: str) -> Any:
        return self._tenant_ref(tenant_id).collection("meta").document("usage_summary")

    def _batch_delete_refs(self, refs: list[Any], *, chunk_size: int = 400) -> None:
        if not refs:
            return
        for i in range(0, len(refs), chunk_size):
            chunk = refs[i : i + chunk_size]
            batch = self._db.batch()
            if hasattr(batch, "delete"):
                for ref in chunk:
                    batch.delete(ref)
                batch.commit()
            else:
                for ref in chunk:
                    ref.delete()

    def delete_project(self, tenant_id: str, project_id: str) -> bool:
        """Delete a tenant-owned project, all Firestore revision docs, and GCS gzip blobs."""
        proj_ref = self._project_ref(tenant_id, project_id)
        if not proj_ref.get().exists:
            return False
        rev_refs: list[Any] = []
        for rev_snap in self._revisions_col(tenant_id, project_id).stream():
            rev_data = rev_snap.to_dict() or {}
            blob_path = rev_data.get("result_blob_path")
            if blob_path:
                self._blobs.delete_blob(str(blob_path))
            rev_refs.append(rev_snap.reference)
        self._batch_delete_refs(rev_refs)
        self._blobs.delete_prefix(f"tenants/{tenant_id}/projects/{project_id}/")
        proj_ref.delete()
        return True

    # ------------------------------------------------------------------
    # Revisions (Metadata in Firestore, Gzipped Phase2DesignResult in GCS)
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
        proj_ref = self._project_ref(tenant_id, project_id)
        rev_id = f"rev_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        d = result.design
        boq_total = d.boq.grand_total_eur if d.boq is not None else 0.0
        blob_path = (
            f"tenants/{tenant_id}/projects/{project_id}/"
            f"revisions/{rev_id}.json.gz"
        )

        # Verify project exists before writing GCS blob
        proj_snap = proj_ref.get()
        if not proj_snap.exists:
            raise KeyError(f"Project '{project_id}' not found for tenant '{tenant_id}'.")

        # Upload immutable rev_id-keyed gzipped snapshot first so metadata never dangles
        self._blobs.put_gzip_json(blob_path, result.model_dump_json())

        def _build_rev_doc(rev_num: int) -> dict[str, Any]:
            return {
                "revision_id": rev_id,
                "project_id": project_id,
                "tenant_id": tenant_id,
                "revision_number": rev_num,
                "notes": notes,
                "input_sha256": d.provenance.input_sha256,
                "overrides_json": overrides.model_dump_json() if overrides is not None else None,
                "result_blob_path": blob_path,
                "supply_tier": d.eac_compliance.recommended_supply_tier,
                "diversified_kva": d.eac_compliance.total_diversified_kva,
                "boq_grand_total_eur": boq_total,
                "has_blockers": d.has_blockers,
                "created_at": now,
            }

        # Use a real Firestore transaction when available to prevent concurrent revision races
        if hasattr(self._db, "transaction"):
            from google.cloud import firestore

            tx = self._db.transaction()

            def _commit_in_tx(transaction: Any) -> dict[str, Any]:
                snap = proj_ref.get(transaction=transaction)
                if not snap.exists:
                    raise KeyError(f"Project '{project_id}' not found for tenant '{tenant_id}'.")
                cur_data = snap.to_dict() or {}
                next_num = int(cur_data.get("revision_count", 0)) + 1
                doc = _build_rev_doc(next_num)
                r_ref = self._revisions_col(tenant_id, project_id).document(f"{next_num:06d}")
                transaction.set(r_ref, doc)
                transaction.set(
                    proj_ref,
                    {
                        "revision_count": next_num,
                        "latest_supply_tier": d.eac_compliance.recommended_supply_tier,
                        "latest_boq_eur": boq_total,
                        "updated_at": now,
                    },
                    merge=True,
                )
                return doc

            tx_runner = firestore.transactional(_commit_in_tx)
            rev_doc: dict[str, Any] = tx_runner(tx)
        else:
            proj_data = proj_snap.to_dict() or {}
            rev_num = int(proj_data.get("revision_count", 0)) + 1
            rev_doc = _build_rev_doc(rev_num)
            rev_ref = self._revisions_col(tenant_id, project_id).document(f"{rev_num:06d}")
            batch = self._db.batch()
            batch.set(rev_ref, rev_doc)
            batch.set(
                proj_ref,
                {
                    "revision_count": rev_num,
                    "latest_supply_tier": d.eac_compliance.recommended_supply_tier,
                    "latest_boq_eur": boq_total,
                    "updated_at": now,
                },
                merge=True,
            )
            batch.commit()

        return RevisionRecord(
            summary=self._doc_to_rev_summary(rev_doc),
            overrides=overrides,
            result=result,
        )

    def list_revisions(self, tenant_id: str, project_id: str) -> list[RevisionSummary]:
        items = [
            self._doc_to_rev_summary(snap.to_dict() or {})
            for snap in self._revisions_col(tenant_id, project_id).stream()
        ]
        items.sort(key=lambda r: r.revision_number)
        return items

    def get_revision(
        self,
        tenant_id: str,
        project_id: str,
        revision_number: int,
    ) -> RevisionRecord | None:
        snap = self._revisions_col(tenant_id, project_id).document(f"{revision_number:06d}").get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        blob_path = str(data["result_blob_path"])
        raw_json = self._blobs.get_gzip_json(blob_path)
        if raw_json is None:
            raise RuntimeError(
                f"Missing GCS revision snapshot '{blob_path}' for tenant '{tenant_id}'."
            )
        summary = self._doc_to_rev_summary(data)
        overrides = (
            DesignOverrides.model_validate_json(str(data["overrides_json"]))
            if data.get("overrides_json")
            else None
        )
        result = Phase2DesignResult.model_validate_json(raw_json)
        return RevisionRecord(summary=summary, overrides=overrides, result=result)

    def get_latest_revision(self, tenant_id: str, project_id: str) -> RevisionRecord | None:
        revs = self.list_revisions(tenant_id, project_id)
        if not revs:
            return None
        return self.get_revision(tenant_id, project_id, revs[-1].revision_number)

    # ------------------------------------------------------------------
    # Usage Metering (Strictly Tenant-Scoped, O(1) Atomic Rollup Counter)
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
        evt_id = f"evt_{uuid.uuid4().hex[:12]}"
        clean_ms = round(max(0.0, float(duration_ms)), 2)
        clean_bytes = max(0, int(payload_bytes))
        now = _utc_now()
        evt_ref = self._usage_col(tenant_id).document(evt_id)
        summary_ref = self._usage_summary_ref(tenant_id)
        safe_et = self._safe_id(event_type, "event_type")

        evt_doc: dict[str, Any] = {
            "event_id": evt_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "event_type": safe_et,
            "duration_ms": clean_ms,
            "payload_bytes": clean_bytes,
            "created_at": now,
        }

        if hasattr(self._db, "transaction"):
            from google.cloud import firestore

            batch = self._db.batch()
            batch.set(evt_ref, evt_doc)
            batch.set(
                summary_ref,
                {
                    "total_events": firestore.Increment(1),
                    "total_duration_ms": firestore.Increment(clean_ms),
                    "total_payload_bytes": firestore.Increment(clean_bytes),
                    "counts_by_event_type": {safe_et: firestore.Increment(1)},
                    "updated_at": now,
                },
                merge=True,
            )
            batch.commit()
        else:
            evt_ref.set(evt_doc)
            cur_snap = summary_ref.get()
            cur = cur_snap.to_dict() if cur_snap.exists else {}
            cur_map = dict((cur or {}).get("counts_by_event_type") or {})
            cur_map[safe_et] = int(cur_map.get(safe_et, 0)) + 1
            summary_ref.set(
                {
                    "total_events": int((cur or {}).get("total_events", 0)) + 1,
                    "total_duration_ms": round(
                        float((cur or {}).get("total_duration_ms", 0.0)) + clean_ms, 2
                    ),
                    "total_payload_bytes": int((cur or {}).get("total_payload_bytes", 0))
                    + clean_bytes,
                    "counts_by_event_type": cur_map,
                    "updated_at": now,
                },
                merge=True,
            )

    def get_usage_summary(self, tenant_id: str) -> UsageSummary:
        """O(1) rollup read for usage metering (falls back to stream for legacy tenants)."""
        summary_snap = self._usage_summary_ref(tenant_id).get()
        if summary_snap.exists:
            data = summary_snap.to_dict() or {}
            raw_counts = data.get("counts_by_event_type") or {}
            counts = {str(k): int(v) for k, v in raw_counts.items()}
            return UsageSummary(
                tenant_id=tenant_id,
                total_events=int(data.get("total_events", 0)),
                total_duration_ms=round(float(data.get("total_duration_ms", 0.0)), 2),
                total_payload_bytes=int(data.get("total_payload_bytes", 0)),
                counts_by_event_type=counts,
            )

        counts_legacy: dict[str, int] = {}
        total_events = 0
        total_ms = 0.0
        total_bytes = 0
        for snap in self._usage_col(tenant_id).stream():
            data = snap.to_dict() or {}
            et = str(data.get("event_type", "unknown"))
            counts_legacy[et] = counts_legacy.get(et, 0) + 1
            total_events += 1
            total_ms += float(data.get("duration_ms", 0.0))
            total_bytes += int(data.get("payload_bytes", 0))
        return UsageSummary(
            tenant_id=tenant_id,
            total_events=total_events,
            total_duration_ms=round(total_ms, 2),
            total_payload_bytes=total_bytes,
            counts_by_event_type=counts_legacy,
        )

    # ------------------------------------------------------------------
    # Stage 3: Billing, Team Users/RBAC & Atomic Monthly Solve Quotas
    # ------------------------------------------------------------------
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
        now = _utc_now()
        updates: dict[str, Any] = {
            "plan": plan if plan is not None else current.plan,
            "subscription_status": (
                subscription_status
                if subscription_status is not None
                else current.subscription_status
            ),
            "stripe_customer_id": (
                stripe_customer_id
                if stripe_customer_id is not None
                else current.stripe_customer_id
            ),
            "stripe_subscription_id": (
                stripe_subscription_id
                if stripe_subscription_id is not None
                else current.stripe_subscription_id
            ),
            "monthly_quota_override": (
                None
                if clear_quota_override
                else (
                    monthly_quota_override
                    if monthly_quota_override is not None
                    else current.monthly_quota_override
                )
            ),
            "updated_at": now,
        }
        self._tenant_ref(tenant_id).set(updates, merge=True)
        return self.get_or_create_tenant(tenant_id)

    def find_tenant_by_stripe_customer(self, stripe_customer_id: str) -> str | None:
        if not stripe_customer_id:
            return None
        query = self._db.collection("tenants").where(
            "stripe_customer_id", "==", stripe_customer_id.strip()
        )
        for snap in query.stream():
            data = snap.to_dict() or {}
            tid = str(data.get("tenant_id", "")).strip()
            if tid:
                return tid
        return None

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
        if len(self.list_users(tenant_id)) >= plan_spec.max_seats:
            raise ValueError(
                f"Seat limit reached ({plan_spec.max_seats} seats on plan '{plan_spec.plan_id}'). "
                "Upgrade plan to add more firm seats."
            )

        idx_ref = self._email_index_ref(norm_email)
        if idx_ref.get().exists:
            raise ValueError("An account with this email already exists.")

        pw_hash: str | None = None
        pw_salt: str | None = None
        if password is not None:
            pw_hash, pw_salt = hash_password(password)
        elif auth_provider == "password":
            raise ValueError("Password is required for password-based accounts.")

        uid = f"usr_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        user_doc: dict[str, Any] = {
            "user_id": uid,
            "tenant_id": tenant_id,
            "email": norm_email,
            "display_name": display_name.strip() or norm_email.split("@", 1)[0],
            "role": role.value,
            "auth_provider": auth_provider,
            "password_hash": pw_hash,
            "password_salt": pw_salt,
            "created_at": now,
            "updated_at": now,
        }
        batch = self._db.batch()
        batch.set(self._users_col(tenant_id).document(uid), user_doc)
        batch.set(
            idx_ref,
            {"email": norm_email, "tenant_id": tenant_id, "user_id": uid, "created_at": now},
        )
        batch.commit()
        return self._doc_to_user(user_doc)

    def authenticate_user(self, email: str, password: str) -> UserRecord | None:
        norm_email = email.strip().lower()
        if not norm_email or not password:
            return None
        idx_snap = self._email_index_ref(norm_email).get()
        if not idx_snap.exists:
            return None
        idx = idx_snap.to_dict() or {}
        tid = str(idx.get("tenant_id", "")).strip()
        uid = str(idx.get("user_id", "")).strip()
        if not tid or not uid:
            return None
        u_snap = self._users_col(tid).document(uid).get()
        if not u_snap.exists:
            return None
        data = u_snap.to_dict() or {}
        pw_hash = str(data.get("password_hash") or "")
        pw_salt = str(data.get("password_salt") or "")
        if not pw_hash or not pw_salt:
            return None
        if not verify_password(password, pw_hash, pw_salt):
            return None
        return self._doc_to_user(data)

    def get_user_by_email(self, email: str) -> UserRecord | None:
        norm_email = email.strip().lower()
        if not norm_email:
            return None
        idx_snap = self._email_index_ref(norm_email).get()
        if not idx_snap.exists:
            return None
        idx = idx_snap.to_dict() or {}
        tid = str(idx.get("tenant_id", "")).strip()
        uid = str(idx.get("user_id", "")).strip()
        if not tid or not uid:
            return None
        return self.get_user(tid, uid)

    def get_user(self, tenant_id: str, user_id: str) -> UserRecord | None:
        snap = self._users_col(tenant_id).document(self._safe_id(user_id, "user_id")).get()
        if not snap.exists:
            return None
        return self._doc_to_user(snap.to_dict() or {})

    def list_users(self, tenant_id: str) -> list[UserRecord]:
        users = [
            self._doc_to_user(snap.to_dict() or {})
            for snap in self._users_col(tenant_id).stream()
        ]
        users.sort(key=lambda u: u.created_at)
        return users

    def update_user_role(
        self, tenant_id: str, user_id: str, role: Role
    ) -> UserRecord | None:
        ref = self._users_col(tenant_id).document(self._safe_id(user_id, "user_id"))
        snap = ref.get()
        if not snap.exists:
            return None
        cur = self._doc_to_user(snap.to_dict() or {})
        if cur.role == Role.OWNER and role != Role.OWNER:
            owners = [u for u in self.list_users(tenant_id) if u.role == Role.OWNER]
            if len(owners) <= 1:
                raise ValueError("Cannot demote the last remaining owner of a tenant.")
        ref.set({"role": role.value, "updated_at": _utc_now()}, merge=True)
        return self.get_user(tenant_id, user_id)

    def delete_user(self, tenant_id: str, user_id: str) -> bool:
        ref = self._users_col(tenant_id).document(self._safe_id(user_id, "user_id"))
        snap = ref.get()
        if not snap.exists:
            return False
        cur = self._doc_to_user(snap.to_dict() or {})
        if cur.role == Role.OWNER:
            owners = [u for u in self.list_users(tenant_id) if u.role == Role.OWNER]
            if len(owners) <= 1:
                raise ValueError("Cannot remove the last remaining owner of a tenant.")
        self._batch_delete_refs([ref, self._email_index_ref(cur.email)])
        return True

    def get_monthly_solve_count(
        self, tenant_id: str, month_key: str | None = None
    ) -> int:
        mk = month_key or current_month_key()
        snap = self._quota_ref(tenant_id, mk).get()
        if not snap.exists:
            return 0
        data = snap.to_dict() or {}
        return int(data.get("solve_count", 0))

    def increment_monthly_solve_count(
        self, tenant_id: str, month_key: str | None = None
    ) -> int:
        self.get_or_create_tenant(tenant_id)
        mk = month_key or current_month_key()
        q_ref = self._quota_ref(tenant_id, mk)
        now = _utc_now()
        if hasattr(self._db, "transaction"):
            from google.cloud import firestore

            q_ref.set(
                {
                    "month_key": mk,
                    "solve_count": firestore.Increment(1),
                    "updated_at": now,
                },
                merge=True,
            )
            return self.get_monthly_solve_count(tenant_id, mk)
        cur = self.get_monthly_solve_count(tenant_id, mk)
        nxt = cur + 1
        q_ref.set({"month_key": mk, "solve_count": nxt, "updated_at": now}, merge=True)
        return nxt

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

    # ------------------------------------------------------------------
    # EU GDPR Articles 17 & 20: Portability Export & Full Cascade Erasure
    # ------------------------------------------------------------------
    def export_tenant_data(self, tenant_id: str) -> TenantExportBundle:
        """GDPR Article 20 portable JSON export for a tenant (excludes secrets & hashes)."""
        tenant = self.get_or_create_tenant(tenant_id)
        members = self.list_users(tenant_id)
        projects = self.list_projects(tenant_id)
        revisions_by_project: dict[str, list[RevisionRecord]] = {}
        for proj in projects:
            recs: list[RevisionRecord] = []
            for rs in self.list_revisions(tenant_id, proj.project_id):
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
        """GDPR Article 17 right to erasure: batch-delete Firestore docs + GCS blobs for tenant."""
        t_ref = self._tenant_ref(tenant_id)
        existed = bool(t_ref.get().exists)

        for proj in self.list_projects(tenant_id):
            self.delete_project(tenant_id, proj.project_id)

        # Belt-and-suspenders sweep of any remaining GCS blobs under this tenant's prefix
        self._blobs.delete_prefix(f"tenants/{tenant_id}/")

        user_refs: list[Any] = []
        for u_snap in self._users_col(tenant_id).stream():
            u_data = u_snap.to_dict() or {}
            email = str(u_data.get("email", "")).strip().lower()
            if email:
                user_refs.append(self._email_index_ref(email))
            user_refs.append(u_snap.reference)
        self._batch_delete_refs(user_refs)

        meta_refs = [m_snap.reference for m_snap in t_ref.collection("meta").stream()]
        self._batch_delete_refs(meta_refs)

        usage_refs = [evt_snap.reference for evt_snap in self._usage_col(tenant_id).stream()]
        self._batch_delete_refs(usage_refs)

        if existed:
            t_ref.delete()
        return existed


def build_workspace_store_from_env(
    db_path: Path | None = None,
) -> tuple[WorkspaceStoreProtocol, str]:
    """Build SQLite (local/CLI default) or GCP Firestore+GCS store from environment variables."""
    backend = os.environ.get("CYELEC_STORE_BACKEND", "sqlite").strip().lower() or "sqlite"
    if backend == "firestore":
        gcs_bucket = os.environ.get("CYELEC_GCS_BUCKET", "").strip()
        gcp_project = os.environ.get("CYELEC_GCP_PROJECT", "").strip() or None
        database = os.environ.get("CYELEC_FIRESTORE_DATABASE", "(default)").strip() or "(default)"
        return (
            FirestoreWorkspaceStore(
                gcs_bucket=gcs_bucket,
                project_id=gcp_project,
                database=database,
            ),
            "firestore",
        )
    if backend != "sqlite":
        raise ValueError(
            f"Unsupported CYELEC_STORE_BACKEND '{backend}'. Expected 'sqlite' or 'firestore'."
        )
    return WorkspaceStore(db_path=db_path), "sqlite"
