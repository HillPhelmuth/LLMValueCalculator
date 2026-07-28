from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from calibration.manifest import ExperimentManifest, ModelConfig
from calibration.models import (
    CaseFeatures,
    ProviderRequest,
    ProviderResponse,
    ScoreResult,
    utc_now_iso,
)
from calibration.provenance import RunProvenance, build_run_provenance, canonical_hash
from calibration.schema import SCHEMA_VERSION, validate_record
from calibration.security import redact_text


SCHEMA_MIGRATION_VERSION = 5
RUN_STATES = {"created", "running", "pausing", "completed", "failed", "cancelled"}


class SqliteRunStore:
    """Durable queue, result, and provenance store for a calibration run."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._initialize_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SqliteRunStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_utc TEXT NOT NULL)"
        )
        current = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        if current == 0 and self._table_exists("runs"):
            self._migrate_legacy_database()
            current = 0
        if current < 1:
            self._apply_initial_schema()
            self._record_migration(1)
            current = 1
        if current < 2:
            self._apply_lease_and_lineage_schema()
            self._record_migration(2)
            current = 2
        if current < 3:
            self._apply_export_schema()
            self._record_migration(3)
            current = 3
        if current < 4:
            self._apply_provider_response_schema()
            self._record_migration(4)
            current = 4
        if current < 5:
            self._apply_monitoring_schema()
            self._record_migration(5)
        self._connection.commit()

    def _apply_initial_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                run_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                resolved_manifest_hash TEXT NOT NULL,
                resolved_manifest_json TEXT NOT NULL,
                code_commit TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('created', 'running', 'pausing', 'completed', 'failed', 'cancelled')),
                started_utc TEXT NOT NULL,
                completed_utc TEXT,
                failure_message TEXT,
                heartbeat_utc TEXT,
                cancellation_requested INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS attempts (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                case_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_version TEXT NOT NULL,
                provider TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                repeat_index INTEGER NOT NULL,
                parent_attempt_id TEXT REFERENCES attempts(attempt_id),
                request_hash TEXT NOT NULL,
                raw_request_uri TEXT NOT NULL,
                raw_response_uri TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                token_counts_json TEXT NOT NULL,
                tool_calls_json TEXT NOT NULL,
                provider_cost REAL,
                finish_reason TEXT NOT NULL,
                refusal INTEGER NOT NULL,
                response_id TEXT NOT NULL,
                from_cache INTEGER NOT NULL,
                created_utc TEXT NOT NULL,
                resolved_model TEXT,
                resolved_provider TEXT,
                endpoint TEXT,
                content_json TEXT,
                router_metadata_json TEXT NOT NULL DEFAULT '{}',
                usage_json TEXT NOT NULL DEFAULT '{}',
                calculated_cost REAL,
                cost_reconciliation_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(run_id, request_hash)
            );

            CREATE TABLE IF NOT EXISTS scores (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                scorer_name TEXT NOT NULL,
                scorer_version TEXT NOT NULL,
                success INTEGER,
                good INTEGER,
                acceptable INTEGER,
                critical INTEGER,
                schema_valid INTEGER,
                semantic_score REAL,
                grounded_score REAL,
                tool_state_score REAL,
                failure_class TEXT,
                metric_json TEXT NOT NULL,
                PRIMARY KEY(attempt_id, scorer_name, scorer_version)
            );

            CREATE TABLE IF NOT EXISTS case_features (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                case_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                dataset_revision TEXT NOT NULL,
                split TEXT NOT NULL,
                category TEXT,
                base_difficulty_stratum TEXT,
                context_band TEXT,
                reasoning_depth TEXT,
                domain_band TEXT,
                tool_horizon TEXT,
                verifiability_band TEXT,
                output_band TEXT,
                criticality_band TEXT,
                feature_json TEXT NOT NULL,
                PRIMARY KEY(run_id, case_id)
            );

            CREATE TABLE IF NOT EXISTS response_cache (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                request_hash TEXT PRIMARY KEY,
                response_uri TEXT NOT NULL,
                provider TEXT NOT NULL,
                model_version TEXT NOT NULL,
                created_utc TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_attempts_run_id ON attempts(run_id);
            CREATE INDEX IF NOT EXISTS ix_scores_attempt_id ON scores(attempt_id);
            """
        )

    def _apply_lease_and_lineage_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS run_provenance (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                provenance_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
                provenance_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_snapshots (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                catalog_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_model TEXT NOT NULL,
                aa_snapshot TEXT NOT NULL,
                aa_intelligence_index REAL,
                snapshot_hash TEXT NOT NULL,
                catalog_json TEXT NOT NULL,
                UNIQUE(run_id, catalog_id)
            );

            CREATE TABLE IF NOT EXISTS work_items (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                work_item_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'leased', 'completed', 'failed')),
                lease_owner TEXT,
                lease_expires_utc TEXT,
                heartbeat_utc TEXT,
                created_utc TEXT NOT NULL,
                completed_utc TEXT,
                UNIQUE(run_id, request_hash)
            );

            CREATE TABLE IF NOT EXISTS fitted_estimates (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                estimate_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                parameter TEXT NOT NULL,
                value REAL NOT NULL,
                lower REAL,
                upper REAL,
                source_row_ids_json TEXT NOT NULL,
                source_export_hash TEXT,
                provenance_id TEXT NOT NULL REFERENCES run_provenance(provenance_id),
                diagnostics_json TEXT NOT NULL
            );
            """
        )

    def _apply_export_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS run_exports (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                export_name TEXT NOT NULL,
                uri TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                created_utc TEXT NOT NULL,
                PRIMARY KEY(run_id, export_name),
                UNIQUE(uri)
            );
            """
        )

    def _apply_provider_response_schema(self) -> None:
        attempt_columns = {
            "resolved_model": "TEXT",
            "resolved_provider": "TEXT",
            "endpoint": "TEXT",
            "content_json": "TEXT",
            "router_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "usage_json": "TEXT NOT NULL DEFAULT '{}'",
            "calculated_cost": "REAL",
            "cost_reconciliation_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        current_columns = {
            row[1] for row in self._connection.execute("PRAGMA table_info(attempts)")
        }
        for column, definition in attempt_columns.items():
            if column not in current_columns:
                self._connection.execute(
                    f"ALTER TABLE attempts ADD COLUMN {column} {definition}"
                )
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS transport_events (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                request_hash TEXT NOT NULL,
                provider TEXT NOT NULL,
                transport_attempt INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                status_code INTEGER,
                retry_after_seconds REAL,
                delay_seconds REAL,
                error_type TEXT,
                error_message TEXT,
                created_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_transport_events_run_id ON transport_events(run_id);
            """
        )

    def _apply_monitoring_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS budget_events (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                experiment_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                amount_usd REAL NOT NULL,
                estimated_usd REAL NOT NULL DEFAULT 0,
                token_count INTEGER NOT NULL,
                request_count INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('reserved', 'settled', 'released', 'over_budget')),
                created_utc TEXT NOT NULL,
                settled_utc TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_budget_events_run_id ON budget_events(run_id);
            CREATE INDEX IF NOT EXISTS ix_budget_events_created_utc ON budget_events(created_utc);
            CREATE TABLE IF NOT EXISTS monitoring_events (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_monitoring_events_run_id ON monitoring_events(run_id);
            """
        )
        columns = {
            str(row[1])
            for row in self._connection.execute(
                "PRAGMA table_info(budget_events)"
            ).fetchall()
        }
        if "estimated_usd" not in columns:
            self._connection.execute(
                "ALTER TABLE budget_events ADD COLUMN estimated_usd REAL NOT NULL DEFAULT 0"
            )

    def _migrate_legacy_database(self) -> None:
        """Upgrade the original pre-migration schema without discarding results."""
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "schema_version" not in columns:
            self._connection.execute(
                "ALTER TABLE runs ADD COLUMN schema_version TEXT NOT NULL DEFAULT '1.0'"
            )
        if "resolved_manifest_hash" not in columns:
            self._connection.execute(
                "ALTER TABLE runs ADD COLUMN resolved_manifest_hash TEXT NOT NULL DEFAULT ''"
            )
            self._connection.execute(
                "ALTER TABLE runs ADD COLUMN resolved_manifest_json TEXT NOT NULL DEFAULT '{}'"
            )
            self._connection.execute("ALTER TABLE runs ADD COLUMN heartbeat_utc TEXT")
            self._connection.execute(
                "ALTER TABLE runs ADD COLUMN cancellation_requested INTEGER NOT NULL DEFAULT 0"
            )
            self._connection.execute(
                "UPDATE runs SET resolved_manifest_hash = manifest_hash, resolved_manifest_json = manifest_json"
            )
        for table, column in (
            ("attempts", "schema_version"),
            ("scores", "schema_version"),
            ("case_features", "schema_version"),
            ("response_cache", "schema_version"),
        ):
            if not self._table_exists(table):
                continue
            current_columns = {
                row[1]
                for row in self._connection.execute(f"PRAGMA table_info({table})")
            }
            if column not in current_columns:
                self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '1.0'"
                )
        definition = self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()[0]
        if definition and "cancelled" not in definition:
            self._rebuild_runs_with_lifecycle_states()

    def _rebuild_runs_with_lifecycle_states(self) -> None:
        self._connection.execute("PRAGMA foreign_keys = OFF")
        self._connection.execute(
            """
            CREATE TABLE runs_new (
                schema_version TEXT NOT NULL DEFAULT '1.0',
                run_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                resolved_manifest_hash TEXT NOT NULL,
                resolved_manifest_json TEXT NOT NULL,
                code_commit TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('created', 'running', 'pausing', 'completed', 'failed', 'cancelled')),
                started_utc TEXT NOT NULL,
                completed_utc TEXT,
                failure_message TEXT,
                heartbeat_utc TEXT,
                cancellation_requested INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._connection.execute(
            """
            INSERT INTO runs_new (
                schema_version, run_id, experiment_id, manifest_hash, manifest_json,
                resolved_manifest_hash, resolved_manifest_json, code_commit, status,
                started_utc, completed_utc, failure_message, heartbeat_utc,
                cancellation_requested
            )
            SELECT schema_version, run_id, experiment_id, manifest_hash, manifest_json,
                   resolved_manifest_hash, resolved_manifest_json, code_commit, status,
                   started_utc, completed_utc, failure_message, heartbeat_utc,
                   cancellation_requested
            FROM runs
            """
        )
        self._connection.execute("DROP TABLE runs")
        self._connection.execute("ALTER TABLE runs_new RENAME TO runs")
        self._connection.execute("PRAGMA foreign_keys = ON")

    def _table_exists(self, table: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )

    def _record_migration(self, version: int) -> None:
        self._connection.execute(
            "INSERT INTO schema_migrations(version, applied_utc) VALUES (?, ?)",
            (version, utc_now_iso()),
        )

    def create_run(
        self,
        manifest: ExperimentManifest,
        code_commit: str,
        run_id: str | None = None,
        *,
        resolved_manifest: dict[str, Any] | None = None,
        provenance: RunProvenance | None = None,
        dependency_lock: str | Path | None = None,
        catalog_snapshot_hash: str | None = None,
    ) -> str:
        identifier = run_id or str(uuid.uuid4())
        resolved = resolved_manifest or manifest.resolved(
            tuple(manifest.dataset.sample_ids)
        )
        resolved_hash = str(resolved["resolved_manifest_hash"])
        provenance = provenance or build_run_provenance(
            manifest,
            code_commit,
            dependency_lock=dependency_lock,
            catalog_snapshot_hash=catalog_snapshot_hash,
        )
        provenance = replace(
            provenance,
            provenance_id=canonical_hash(
                {"run_id": identifier, "provenance": provenance.to_json()}
            ),
        )
        validate_record(
            "run",
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": identifier,
                "experiment_id": manifest.experiment_id,
                "manifest_hash": manifest.manifest_hash,
                "resolved_manifest_hash": resolved_hash,
                "status": "running",
                "started_utc": utc_now_iso(),
                "completed_utc": None,
                "failure_message": None,
                "code_commit": code_commit,
            },
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO runs (
                    schema_version, run_id, experiment_id, manifest_hash, manifest_json,
                    resolved_manifest_hash, resolved_manifest_json, code_commit, status,
                    started_utc, heartbeat_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    SCHEMA_VERSION,
                    identifier,
                    manifest.experiment_id,
                    manifest.manifest_hash,
                    manifest.canonical_json,
                    resolved_hash,
                    _json(resolved),
                    code_commit,
                    utc_now_iso(),
                    utc_now_iso(),
                ),
            )
            self._insert_provenance(identifier, provenance)
            self._insert_model_snapshots(identifier, manifest)
        return identifier

    def _insert_provenance(self, run_id: str, provenance: RunProvenance) -> None:
        self._connection.execute(
            "INSERT INTO run_provenance(schema_version, provenance_id, run_id, provenance_json) VALUES (?, ?, ?, ?)",
            (
                SCHEMA_VERSION,
                provenance.provenance_id,
                run_id,
                _json(provenance.to_json()),
            ),
        )

    def _insert_model_snapshots(
        self, run_id: str, manifest: ExperimentManifest
    ) -> None:
        for model in manifest.models:
            value = model.model_dump(mode="json")
            snapshot_hash = canonical_hash(value)
            snapshot_id = f"{run_id}:{model.catalog_id}"
            validate_record(
                "model_snapshot",
                {
                    "schema_version": SCHEMA_VERSION,
                    "snapshot_id": snapshot_id,
                    "run_id": run_id,
                    "catalog_id": model.catalog_id,
                    "provider": model.provider,
                    "provider_model": model.provider_model,
                    "aa_snapshot": model.aa_snapshot,
                    "aa_intelligence_index": model.aa_intelligence_index,
                    "snapshot_hash": snapshot_hash,
                    "catalog_json": value,
                },
            )
            self._connection.execute(
                """
                INSERT INTO model_snapshots (
                    schema_version, snapshot_id, run_id, catalog_id, provider,
                    provider_model, aa_snapshot, aa_intelligence_index, snapshot_hash,
                    catalog_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SCHEMA_VERSION,
                    snapshot_id,
                    run_id,
                    model.catalog_id,
                    model.provider,
                    model.provider_model,
                    model.aa_snapshot,
                    model.aa_intelligence_index,
                    snapshot_hash,
                    _json(value),
                ),
            )

    def resume_run(
        self,
        run_id: str,
        manifest: ExperimentManifest,
        resolved_manifest: dict[str, Any] | None = None,
    ) -> None:
        row = self._connection.execute(
            "SELECT manifest_hash, resolved_manifest_hash FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown run ID: {run_id}")
        if row["manifest_hash"] != manifest.manifest_hash:
            raise ValueError("Cannot resume a run with a different manifest hash")
        if (
            resolved_manifest
            and row["resolved_manifest_hash"]
            != resolved_manifest["resolved_manifest_hash"]
        ):
            raise ValueError("Cannot resume a run with a different resolved manifest")
        with self._connection:
            self._connection.execute(
                "UPDATE runs SET status='running', cancellation_requested=0, failure_message=NULL WHERE run_id=?",
                (run_id,),
            )
            self._connection.execute(
                """
                UPDATE work_items
                SET status='pending', lease_owner=NULL, lease_expires_utc=NULL, heartbeat_utc=NULL
                WHERE run_id=? AND status IN ('leased', 'failed')
                """,
                (run_id,),
            )

    def resolved_manifest(self, run_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT resolved_manifest_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown run ID: {run_id}")
        return json.loads(row["resolved_manifest_json"])

    def completed_request_hashes(self, run_id: str) -> set[str]:
        rows = self._connection.execute(
            "SELECT request_hash FROM attempts WHERE run_id = ?", (run_id,)
        ).fetchall()
        return {str(row["request_hash"]) for row in rows}

    def put_case_features(self, run_id: str, features: CaseFeatures) -> None:
        validate_record("case_features", _case_features_json(features))
        self._connection.execute(
            """
            INSERT INTO case_features (
                schema_version, run_id, case_id, dataset_id, dataset_revision, split,
                category, base_difficulty_stratum, context_band, reasoning_depth,
                domain_band, tool_horizon, verifiability_band, output_band,
                criticality_band, feature_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, case_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                dataset_id=excluded.dataset_id,
                dataset_revision=excluded.dataset_revision,
                split=excluded.split,
                category=excluded.category,
                base_difficulty_stratum=excluded.base_difficulty_stratum,
                context_band=excluded.context_band,
                reasoning_depth=excluded.reasoning_depth,
                domain_band=excluded.domain_band,
                tool_horizon=excluded.tool_horizon,
                verifiability_band=excluded.verifiability_band,
                output_band=excluded.output_band,
                criticality_band=excluded.criticality_band,
                feature_json=excluded.feature_json
            """,
            (
                SCHEMA_VERSION,
                run_id,
                features.case_id,
                features.dataset_id,
                features.dataset_revision,
                features.split,
                features.category,
                features.base_difficulty_stratum,
                features.context_band,
                features.reasoning_depth,
                features.domain_band,
                features.tool_horizon,
                features.verifiability_band,
                features.output_band,
                features.criticality_band,
                _json(features.feature_json),
            ),
        )
        self._connection.commit()

    def cached_response_uri(self, request_hash: str) -> str | None:
        row = self._connection.execute(
            "SELECT response_uri FROM response_cache WHERE request_hash = ?",
            (request_hash,),
        ).fetchone()
        return None if row is None else str(row["response_uri"])

    def cache_response(
        self,
        request: ProviderRequest,
        response_uri: str,
        response: ProviderResponse,
    ) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO response_cache (
                schema_version, request_hash, response_uri, provider, model_version, created_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                request.request_hash,
                response_uri,
                request.provider,
                request.dated_model_version,
                response.created_utc,
            ),
        )
        self._connection.commit()

    def record_transport_event(
        self,
        run_id: str,
        request_hash: str,
        provider: str,
        transport_attempt: int,
        event_type: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        delay_seconds: float | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        event_record = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "run_id": run_id,
            "request_hash": request_hash,
            "provider": provider,
            "transport_attempt": transport_attempt,
            "event_type": event_type,
            "status_code": status_code,
            "retry_after_seconds": retry_after_seconds,
            "delay_seconds": delay_seconds,
            "error_type": error_type,
            "error_message": error_message,
            "created_utc": utc_now_iso(),
        }
        validate_record("transport_event", event_record)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO transport_events (
                    schema_version, event_id, run_id, request_hash, provider,
                    transport_attempt, event_type, status_code, retry_after_seconds,
                    delay_seconds, error_type, error_message, created_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SCHEMA_VERSION,
                    event_id,
                    run_id,
                    request_hash,
                    provider,
                    transport_attempt,
                    event_type,
                    status_code,
                    retry_after_seconds,
                    delay_seconds,
                    error_type,
                    redact_text(error_message or "") or None,
                    event_record["created_utc"],
                ),
            )
        return event_id

    def budget_totals(
        self,
        *,
        run_id: str,
        experiment_id: str | None = None,
        model_id: str | None = None,
        day: str | None = None,
        exclude_event_id: str | None = None,
    ) -> dict[str, float]:
        clauses = ["status <> 'released'"]
        parameters: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if experiment_id is not None:
            clauses.append("experiment_id = ?")
            parameters.append(experiment_id)
        if model_id is not None:
            clauses.append("model_id = ?")
            parameters.append(model_id)
        if day is not None:
            clauses.append("substr(created_utc, 1, 10) = ?")
            parameters.append(day)
        if exclude_event_id is not None:
            clauses.append("event_id <> ?")
            parameters.append(exclude_event_id)
        row = self._connection.execute(
            "SELECT COALESCE(SUM(amount_usd), 0), COALESCE(SUM(token_count), 0), COALESCE(SUM(request_count), 0) "
            "FROM budget_events WHERE " + " AND ".join(clauses),
            parameters,
        ).fetchone()
        return {
            "usd": float(row[0]),
            "tokens": float(row[1]),
            "requests": float(row[2]),
        }

    def record_budget_event(
        self,
        *,
        run_id: str,
        experiment_id: str,
        model_id: str,
        provider: str,
        request_hash: str,
        amount_usd: float,
        estimated_usd: float | None = None,
        token_count: int,
        request_count: int = 1,
        status: str = "reserved",
    ) -> str:
        if status not in {"reserved", "settled", "released", "over_budget"}:
            raise ValueError(f"Unsupported budget event status: {status}")
        event_id = str(uuid.uuid4())
        with self._connection:
            self._connection.execute(
                "INSERT INTO budget_events (schema_version, event_id, run_id, experiment_id, model_id, provider, request_hash, amount_usd, estimated_usd, token_count, request_count, status, created_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    SCHEMA_VERSION,
                    event_id,
                    run_id,
                    experiment_id,
                    model_id,
                    provider,
                    request_hash,
                    float(amount_usd),
                    float(amount_usd if estimated_usd is None else estimated_usd),
                    int(token_count),
                    int(request_count),
                    status,
                    utc_now_iso(),
                ),
            )
        return event_id

    def settle_budget_event(
        self, event_id: str, amount_usd: float, *, over_budget: bool = False
    ) -> None:
        status = "over_budget" if over_budget else "settled"
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE budget_events SET amount_usd=?, status=?, settled_utc=? WHERE event_id=? AND status='reserved'",
                (float(amount_usd), status, utc_now_iso(), event_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Budget event is not reservable: {event_id}")

    def record_monitoring_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> str:
        event_id = str(uuid.uuid4())
        with self._connection:
            self._connection.execute(
                "INSERT INTO monitoring_events (schema_version, event_id, run_id, event_type, payload_json, created_utc) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    SCHEMA_VERSION,
                    event_id,
                    run_id,
                    event_type,
                    _json(payload),
                    utc_now_iso(),
                ),
            )
        return event_id

    def monitoring_rows(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM monitoring_events WHERE run_id=? ORDER BY created_utc, event_id",
                (run_id,),
            ).fetchall()
        ]

    def budget_rows(self, run_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM budget_events WHERE run_id=? ORDER BY created_utc, event_id",
                (run_id,),
            ).fetchall()
        ]

    def queue_counts(self, run_id: str) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT status, COUNT(*) FROM work_items WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
        values = {str(row[0]): int(row[1]) for row in rows}
        return {
            key: values.get(key, 0)
            for key in ("pending", "leased", "completed", "failed")
        }

    def cancellation_requested(self, run_id: str) -> bool:
        row = self._connection.execute(
            "SELECT cancellation_requested FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown run ID: {run_id}")
        return bool(row[0])

    def create_work_item(self, run_id: str, request_hash: str) -> str:
        work_item_id = str(uuid.uuid4())
        self._connection.execute(
            """
            INSERT OR IGNORE INTO work_items (
                schema_version, work_item_id, run_id, request_hash, status, created_utc
            ) VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (SCHEMA_VERSION, work_item_id, run_id, request_hash, utc_now_iso()),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT work_item_id FROM work_items WHERE run_id = ? AND request_hash = ?",
            (run_id, request_hash),
        ).fetchone()
        if row is None:
            raise RuntimeError("Work item was not persisted")
        return str(row["work_item_id"])

    def claim_work_item(
        self,
        run_id: str,
        owner: str,
        lease_seconds: int = 300,
        *,
        work_item_id: str | None = None,
    ) -> str | None:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        now_text = now.isoformat()
        with self._connection:
            if work_item_id is None:
                row = self._connection.execute(
                    """
                    SELECT work_item_id FROM work_items
                    WHERE run_id = ? AND (
                        status = 'pending' OR
                        (status = 'leased' AND lease_expires_utc < ?)
                    )
                    ORDER BY created_utc, work_item_id LIMIT 1
                    """,
                    (run_id, now_text),
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT work_item_id FROM work_items
                    WHERE run_id = ? AND work_item_id = ? AND (
                        status = 'pending' OR
                        (status = 'leased' AND lease_expires_utc < ?)
                    )
                    """,
                    (run_id, work_item_id, now_text),
                ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                """
                UPDATE work_items
                SET status='leased', lease_owner=?, lease_expires_utc=?, heartbeat_utc=?
                WHERE work_item_id=?
                """,
                (owner, expires, now_text, row["work_item_id"]),
            )
        return str(row["work_item_id"])

    def heartbeat_work_item(
        self, work_item_id: str, owner: str, lease_seconds: int = 300
    ) -> None:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE work_items SET lease_expires_utc=?, heartbeat_utc=?
                WHERE work_item_id=? AND status='leased' AND lease_owner=?
                """,
                (expires, now.isoformat(), work_item_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work item lease is not owned by this worker")

    def complete_work_item(self, work_item_id: str, owner: str) -> None:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE work_items
                SET status='completed', completed_utc=?, lease_owner=NULL, lease_expires_utc=NULL
                WHERE work_item_id=? AND status='leased' AND lease_owner=?
                """,
                (utc_now_iso(), work_item_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work item lease is not owned by this worker")

    def reconcile_completed_work_item(
        self, work_item_id: str, run_id: str, request_hash: str
    ) -> bool:
        """Close a stale queue row only when its immutable attempt already exists.

        A long-running provider call can outlive its lease even though its response
        was durably recorded.  Reconciliation is deliberately conditioned on the
        attempt's run and request hash, so it cannot turn an unexecuted cell into a
        completed one.
        """
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE work_items
                SET status='completed', completed_utc=?, lease_owner=NULL,
                    lease_expires_utc=NULL, heartbeat_utc=NULL
                WHERE work_item_id=? AND run_id=? AND status <> 'completed'
                  AND EXISTS (
                      SELECT 1 FROM attempts
                      WHERE attempts.run_id=? AND attempts.request_hash=?
                  )
                """,
                (utc_now_iso(), work_item_id, run_id, run_id, request_hash),
            )
        return cursor.rowcount == 1

    def reconcile_recorded_work_items(self, run_id: str) -> int:
        """Close every stale queue row for a run that already has an attempt."""
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE work_items
                SET status='completed', completed_utc=?, lease_owner=NULL,
                    lease_expires_utc=NULL, heartbeat_utc=NULL
                WHERE run_id=? AND status <> 'completed'
                  AND EXISTS (
                      SELECT 1 FROM attempts
                      WHERE attempts.run_id=work_items.run_id
                        AND attempts.request_hash=work_items.request_hash
                  )
                """,
                (utc_now_iso(), run_id),
            )
        return cursor.rowcount

    def has_recorded_attempt(self, run_id: str, request_hash: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM attempts WHERE run_id=? AND request_hash=? LIMIT 1",
                (run_id, request_hash),
            ).fetchone()
            is not None
        )

    def fail_work_item(self, work_item_id: str, owner: str) -> None:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE work_items
                SET status='failed', completed_utc=?, lease_owner=NULL,
                    lease_expires_utc=NULL
                WHERE work_item_id=? AND status='leased' AND lease_owner=?
                """,
                (utc_now_iso(), work_item_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("Work item lease is not owned by this worker")

    def recover_expired_leases(self) -> int:
        now = utc_now_iso()
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE work_items SET status='pending', lease_owner=NULL,
                    lease_expires_utc=NULL, heartbeat_utc=NULL
                WHERE status='leased' AND lease_expires_utc < ?
                """,
                (now,),
            )
        return cursor.rowcount

    def record_attempt_with_scores(
        self,
        run_id: str,
        case_id: str,
        model: ModelConfig,
        request: ProviderRequest,
        response: ProviderResponse,
        raw_request_uri: str,
        raw_response_uri: str,
        scores: tuple[ScoreResult, ...],
        from_cache: bool,
        parent_attempt_id: str | None = None,
    ) -> str:
        attempt_id = str(uuid.uuid4())
        token_counts = {
            "input": response.input_tokens,
            "cached": response.cached_tokens,
            "output": response.output_tokens,
            "reasoning": response.reasoning_tokens,
        }
        attempt_record = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "case_id": case_id,
            "condition_id": request.condition_id,
            "model_id": model.catalog_id,
            "model_version": request.dated_model_version,
            "provider": request.provider,
            "prompt_version": request.prompt_version,
            "repeat_index": request.repeat_index,
            "parent_attempt_id": parent_attempt_id,
            "request_hash": request.request_hash,
            "raw_request_uri": raw_request_uri,
            "raw_response_uri": raw_response_uri,
            "latency_ms": response.latency_ms,
            "token_counts": token_counts,
            "tool_calls": list(response.tool_calls),
            "provider_cost": response.provider_cost,
            "finish_reason": response.finish_reason,
            "refusal": response.refusal,
            "response_id": response.response_id,
            "from_cache": from_cache,
            "created_utc": response.created_utc,
            "resolved_model": response.resolved_model,
            "resolved_provider": response.resolved_provider,
            "endpoint": response.endpoint,
            "content": response.content,
            "router_metadata": response.router_metadata,
            "usage": response.usage,
            "calculated_cost": response.calculated_cost,
            "cost_reconciliation": response.cost_reconciliation,
        }
        validate_record("attempt", attempt_record)
        for score in scores:
            validate_record(
                "score",
                {
                    "schema_version": SCHEMA_VERSION,
                    "attempt_id": attempt_id,
                    "scorer_name": score.scorer_name,
                    "scorer_version": score.scorer_version,
                    "success": score.success,
                    "good": score.good,
                    "acceptable": score.acceptable,
                    "critical": score.critical,
                    "schema_valid": score.schema_valid,
                    "semantic_score": score.semantic_score,
                    "grounded_score": score.grounded_score,
                    "tool_state_score": score.tool_state_score,
                    "failure_class": score.failure_class,
                    "metrics": score.metrics,
                },
            )
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO attempts (
                    schema_version, attempt_id, run_id, case_id, condition_id, model_id,
                    model_version, provider, prompt_version, repeat_index, parent_attempt_id,
                    request_hash, raw_request_uri, raw_response_uri, latency_ms,
                    token_counts_json, tool_calls_json, provider_cost, finish_reason,
                    refusal, response_id, from_cache, created_utc, resolved_model,
                    resolved_provider, endpoint, content_json, router_metadata_json,
                    usage_json, calculated_cost, cost_reconciliation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SCHEMA_VERSION,
                    attempt_id,
                    run_id,
                    case_id,
                    request.condition_id,
                    model.catalog_id,
                    request.dated_model_version,
                    request.provider,
                    request.prompt_version,
                    request.repeat_index,
                    parent_attempt_id,
                    request.request_hash,
                    raw_request_uri,
                    raw_response_uri,
                    response.latency_ms,
                    _json(token_counts),
                    _json(response.tool_calls),
                    _money_float(response.provider_cost),
                    response.finish_reason,
                    int(response.refusal),
                    response.response_id,
                    int(from_cache),
                    response.created_utc,
                    response.resolved_model,
                    response.resolved_provider,
                    response.endpoint,
                    _json(response.content),
                    _json(response.router_metadata),
                    _json(response.usage),
                    _money_float(response.calculated_cost),
                    _json(response.cost_reconciliation),
                ),
            )
            if cursor.rowcount == 0:
                existing = self._connection.execute(
                    "SELECT attempt_id FROM attempts WHERE run_id=? AND request_hash=?",
                    (run_id, request.request_hash),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("Attempt conflict could not be reconciled")
                return str(existing["attempt_id"])
            self._connection.executemany(
                """
                INSERT INTO scores (
                    schema_version, attempt_id, scorer_name, scorer_version, success,
                    good, acceptable, critical, schema_valid, semantic_score,
                    grounded_score, tool_state_score, failure_class, metric_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        SCHEMA_VERSION,
                        attempt_id,
                        score.scorer_name,
                        score.scorer_version,
                        _bool(score.success),
                        _bool(score.good),
                        _bool(score.acceptable),
                        _bool(score.critical),
                        _bool(score.schema_valid),
                        score.semantic_score,
                        score.grounded_score,
                        score.tool_state_score,
                        score.failure_class,
                        _json(score.metrics),
                    )
                    for score in scores
                ],
            )
        return attempt_id

    def record_estimate(
        self,
        run_id: str,
        parameter: str,
        value: float,
        source_row_ids: tuple[str, ...],
        *,
        lower: float | None = None,
        upper: float | None = None,
        source_export_hash: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> str:
        if not source_row_ids:
            raise ValueError("A fitted estimate must reference source rows")
        source_ids = set(source_row_ids)
        known_source_ids = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT attempt_id FROM attempts WHERE run_id=?", (run_id,)
            ).fetchall()
        }
        known_source_ids.update(
            str(row[0])
            for row in self._connection.execute(
                "SELECT attempt_id || ':' || scorer_name || ':' || scorer_version "
                "FROM scores WHERE attempt_id IN (SELECT attempt_id FROM attempts WHERE run_id=?)",
                (run_id,),
            ).fetchall()
        )
        if not source_ids.issubset(known_source_ids):
            missing = sorted(source_ids - known_source_ids)
            raise ValueError(f"Estimate source rows are not in the run: {missing}")
        provenance_row = self._connection.execute(
            "SELECT provenance_id FROM run_provenance WHERE run_id = ?", (run_id,)
        ).fetchone()
        if provenance_row is None:
            raise ValueError("Cannot record an estimate without run provenance")
        estimate_id = str(uuid.uuid4())
        record = {
            "schema_version": SCHEMA_VERSION,
            "estimate_id": estimate_id,
            "run_id": run_id,
            "parameter": parameter,
            "value": value,
            "lower": lower,
            "upper": upper,
            "source_row_ids": list(source_row_ids),
            "source_export_hash": source_export_hash,
            "provenance_id": provenance_row["provenance_id"],
            "diagnostics": diagnostics or {},
        }
        validate_record("fitted_estimate", record)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO fitted_estimates (
                    schema_version, estimate_id, run_id, parameter, value, lower, upper,
                    source_row_ids_json, source_export_hash, provenance_id, diagnostics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SCHEMA_VERSION,
                    estimate_id,
                    run_id,
                    parameter,
                    value,
                    lower,
                    upper,
                    _json(source_row_ids),
                    source_export_hash,
                    provenance_row["provenance_id"],
                    _json(diagnostics or {}),
                ),
            )
        return estimate_id

    def set_run_status(self, run_id: str, status: str) -> None:
        if status not in RUN_STATES:
            raise ValueError(f"Unsupported run status: {status}")
        with self._connection:
            self._connection.execute(
                "UPDATE runs SET status=?, heartbeat_utc=? WHERE run_id=?",
                (status, utc_now_iso(), run_id),
            )

    def pause_run(self, run_id: str) -> None:
        self.set_run_status(run_id, "pausing")

    def cancel_run(self, run_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE runs SET cancellation_requested=1, status='cancelled', completed_utc=? WHERE run_id=?",
                (utc_now_iso(), run_id),
            )

    def heartbeat_run(self, run_id: str) -> None:
        self._connection.execute(
            "UPDATE runs SET heartbeat_utc=? WHERE run_id=?", (utc_now_iso(), run_id)
        )
        self._connection.commit()

    def mark_completed(self, run_id: str) -> None:
        self.set_run_status(run_id, "completed")
        self._connection.execute(
            "UPDATE runs SET completed_utc=? WHERE run_id=?", (utc_now_iso(), run_id)
        )
        self._connection.commit()

    def mark_failed(self, run_id: str, message: str) -> None:
        self._connection.execute(
            """
            UPDATE runs SET status='failed', completed_utc=?, failure_message=? WHERE run_id=?
            """,
            (utc_now_iso(), redact_text(message), run_id),
        )
        self._connection.commit()

    def rows_for_export(self, table: str, run_id: str) -> list[dict[str, Any]]:
        allowed = {
            "runs": "SELECT * FROM runs WHERE run_id = ?",
            "attempts": "SELECT * FROM attempts WHERE run_id = ? ORDER BY attempt_id",
            "scores": "SELECT s.* FROM scores s JOIN attempts a ON a.attempt_id=s.attempt_id WHERE a.run_id=? ORDER BY s.attempt_id, s.scorer_name, s.scorer_version",
            "case_features": "SELECT * FROM case_features WHERE run_id=? ORDER BY case_id",
            "model_snapshots": "SELECT * FROM model_snapshots WHERE run_id=? ORDER BY catalog_id",
            "fitted_estimates": "SELECT * FROM fitted_estimates WHERE run_id=? ORDER BY estimate_id",
            "run_provenance": "SELECT schema_version, provenance_id, run_id, provenance_json FROM run_provenance WHERE run_id=?",
            "transport_events": "SELECT * FROM transport_events WHERE run_id=? ORDER BY created_utc, event_id",
        }
        query = allowed.get(table)
        if query is None:
            raise ValueError(f"Unsupported export table: {table}")
        return [
            dict(row) for row in self._connection.execute(query, (run_id,)).fetchall()
        ]

    def record_export(
        self, run_id: str, export_name: str, uri: str, sha256: str, row_count: int
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO run_exports (
                    schema_version, run_id, export_name, uri, sha256, row_count, created_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, export_name) DO NOTHING
                """,
                (
                    SCHEMA_VERSION,
                    run_id,
                    export_name,
                    uri,
                    sha256,
                    row_count,
                    utc_now_iso(),
                ),
            )

    def audit_provenance(self, run_id: str) -> list[str]:
        errors: list[str] = []
        run = self._connection.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            return [f"Unknown run ID: {run_id}"]
        if (
            self._connection.execute(
                "SELECT 1 FROM run_provenance WHERE run_id=?", (run_id,)
            ).fetchone()
            is None
        ):
            errors.append("run has no provenance record")
        if (
            self._connection.execute(
                "SELECT 1 FROM model_snapshots WHERE run_id=?", (run_id,)
            ).fetchone()
            is None
        ):
            errors.append("run has no model snapshot records")
        orphan_scores = self._connection.execute(
            "SELECT COUNT(*) FROM scores s LEFT JOIN attempts a ON a.attempt_id=s.attempt_id WHERE a.attempt_id IS NULL"
        ).fetchone()[0]
        if orphan_scores:
            errors.append(f"{orphan_scores} orphaned scores")
        attempts = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT attempt_id FROM attempts WHERE run_id=?", (run_id,)
            ).fetchall()
        }
        score_rows = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT attempt_id || ':' || scorer_name || ':' || scorer_version "
                "FROM scores WHERE attempt_id IN (SELECT attempt_id FROM attempts WHERE run_id=?)",
                (run_id,),
            ).fetchall()
        }
        for estimate in self._connection.execute(
            "SELECT estimate_id, source_row_ids_json FROM fitted_estimates WHERE run_id=?",
            (run_id,),
        ).fetchall():
            source_ids = set(json.loads(estimate["source_row_ids_json"]))
            if not source_ids or not source_ids.issubset(attempts | score_rows):
                errors.append(
                    f"estimate {estimate['estimate_id']} has incomplete source lineage"
                )
        return errors

    def run_summary(self, run_id: str) -> dict[str, object]:
        row = self._connection.execute(
            """
            SELECT r.run_id, r.experiment_id, r.manifest_hash, r.manifest_json, r.resolved_manifest_hash,
                   r.status, r.started_utc, r.completed_utc, r.heartbeat_utc,
                   (SELECT COUNT(*) FROM attempts a WHERE a.run_id = r.run_id) AS attempts,
                   (SELECT COUNT(*) FROM scores s JOIN attempts a ON a.attempt_id = s.attempt_id WHERE a.run_id = r.run_id) AS scores,
                   (SELECT COALESCE(SUM(a.provider_cost), 0) FROM attempts a WHERE a.run_id = r.run_id) AS provider_cost,
                   (SELECT COUNT(*) FROM run_exports e WHERE e.run_id = r.run_id) AS exports
            FROM runs r WHERE r.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown run ID: {run_id}")
        summary = dict(row)
        summary["export_hashes"] = {
            str(export["export_name"]): str(export["sha256"])
            for export in self._connection.execute(
                "SELECT export_name, sha256 FROM run_exports WHERE run_id=? ORDER BY export_name",
                (run_id,),
            ).fetchall()
        }
        provenance = self._connection.execute(
            "SELECT provenance_json FROM run_provenance WHERE run_id=?", (run_id,)
        ).fetchone()
        summary["provenance"] = (
            None if provenance is None else json.loads(provenance["provenance_json"])
        )
        return summary

    def latest_run_id(self) -> str:
        row = self._connection.execute(
            "SELECT run_id FROM runs ORDER BY started_utc DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise ValueError("No calibration run exists in the database")
        return str(row["run_id"])


def _case_features_json(features: CaseFeatures) -> dict[str, Any]:
    return {
        "schema_version": features.schema_version,
        "case_id": features.case_id,
        "dataset_id": features.dataset_id,
        "dataset_revision": features.dataset_revision,
        "split": features.split,
        "category": features.category,
        "base_difficulty_stratum": features.base_difficulty_stratum,
        "context_band": features.context_band,
        "reasoning_depth": features.reasoning_depth,
        "domain_band": features.domain_band,
        "tool_horizon": features.tool_horizon,
        "verifiability_band": features.verifiability_band,
        "output_band": features.output_band,
        "criticality_band": features.criticality_band,
        "feature_json": features.feature_json,
    }


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _bool(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _money_float(value: Any) -> float | None:
    return None if value is None else float(value)
