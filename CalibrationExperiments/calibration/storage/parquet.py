from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from calibration.schema import SCHEMA_VERSION
from calibration.storage.artifacts import ArtifactStore
from calibration.storage.sqlite import SqliteRunStore


@dataclass(frozen=True, slots=True)
class ParquetExport:
    run_id: str
    output_directory: Path
    files: dict[str, str]
    row_counts: dict[str, int]


EXPORT_SCHEMAS: dict[str, pa.Schema] = {
    "runs": pa.schema([
        ("schema_version", pa.string()), ("run_id", pa.string()),
        ("experiment_id", pa.string()), ("manifest_hash", pa.string()),
        ("resolved_manifest_hash", pa.string()), ("manifest_json", pa.string()),
        ("resolved_manifest_json", pa.string()), ("code_commit", pa.string()),
        ("status", pa.string()), ("started_utc", pa.string()),
        ("completed_utc", pa.string()), ("failure_message", pa.string()),
        ("heartbeat_utc", pa.string()), ("cancellation_requested", pa.int8()),
    ]),
    "attempts": pa.schema([
        ("schema_version", pa.string()), ("attempt_id", pa.string()),
        ("run_id", pa.string()), ("case_id", pa.string()),
        ("condition_id", pa.string()), ("model_id", pa.string()),
        ("model_version", pa.string()), ("provider", pa.string()),
        ("prompt_version", pa.string()), ("repeat_index", pa.int64()),
        ("parent_attempt_id", pa.string()), ("request_hash", pa.string()),
        ("raw_request_uri", pa.string()), ("raw_response_uri", pa.string()),
        ("latency_ms", pa.float64()), ("token_counts_json", pa.string()),
        ("tool_calls_json", pa.string()), ("provider_cost", pa.float64()),
        ("finish_reason", pa.string()), ("refusal", pa.int8()),
        ("response_id", pa.string()), ("from_cache", pa.int8()),
        ("created_utc", pa.string()), ("resolved_model", pa.string()),
        ("resolved_provider", pa.string()), ("endpoint", pa.string()),
        ("content_json", pa.string()), ("router_metadata_json", pa.string()),
        ("usage_json", pa.string()), ("calculated_cost", pa.float64()),
        ("cost_reconciliation_json", pa.string()),
    ]),
    "scores": pa.schema([
        ("schema_version", pa.string()), ("attempt_id", pa.string()),
        ("scorer_name", pa.string()), ("scorer_version", pa.string()),
        ("success", pa.int8()), ("good", pa.int8()), ("acceptable", pa.int8()),
        ("critical", pa.int8()), ("schema_valid", pa.int8()),
        ("semantic_score", pa.float64()), ("grounded_score", pa.float64()),
        ("tool_state_score", pa.float64()), ("failure_class", pa.string()),
        ("metric_json", pa.string()),
    ]),
    "case_features": pa.schema([
        ("schema_version", pa.string()), ("run_id", pa.string()),
        ("case_id", pa.string()), ("dataset_id", pa.string()),
        ("dataset_revision", pa.string()), ("split", pa.string()),
        ("category", pa.string()), ("base_difficulty_stratum", pa.string()),
        ("context_band", pa.string()), ("reasoning_depth", pa.string()),
        ("domain_band", pa.string()), ("tool_horizon", pa.string()),
        ("verifiability_band", pa.string()), ("output_band", pa.string()),
        ("criticality_band", pa.string()), ("feature_json", pa.string()),
    ]),
    "model_snapshots": pa.schema([
        ("schema_version", pa.string()), ("snapshot_id", pa.string()),
        ("run_id", pa.string()), ("catalog_id", pa.string()),
        ("provider", pa.string()), ("provider_model", pa.string()),
        ("aa_snapshot", pa.string()), ("aa_intelligence_index", pa.float64()),
        ("snapshot_hash", pa.string()), ("catalog_json", pa.string()),
    ]),
    "fitted_estimates": pa.schema([
        ("schema_version", pa.string()), ("estimate_id", pa.string()),
        ("run_id", pa.string()), ("parameter", pa.string()),
        ("value", pa.float64()), ("lower", pa.float64()), ("upper", pa.float64()),
        ("source_row_ids_json", pa.string()), ("source_export_hash", pa.string()),
        ("provenance_id", pa.string()), ("diagnostics_json", pa.string()),
    ]),
    "run_provenance": pa.schema([
        ("schema_version", pa.string()), ("provenance_id", pa.string()),
        ("run_id", pa.string()), ("provenance_json", pa.string()),
    ]),
    "transport_events": pa.schema([
        ("schema_version", pa.string()), ("event_id", pa.string()),
        ("run_id", pa.string()), ("request_hash", pa.string()),
        ("provider", pa.string()), ("transport_attempt", pa.int64()),
        ("event_type", pa.string()), ("status_code", pa.int64()),
        ("retry_after_seconds", pa.float64()), ("delay_seconds", pa.float64()),
        ("error_type", pa.string()), ("error_message", pa.string()),
        ("created_utc", pa.string()),
    ]),
}


def export_run_to_parquet(
    store: SqliteRunStore,
    run_id: str,
    output_directory: str | Path,
    *,
    artifacts: ArtifactStore | None = None,
) -> ParquetExport:
    """Snapshot normalized tables to immutable, deterministic Parquet files."""
    provenance_errors = store.audit_provenance(run_id)
    if provenance_errors:
        raise ValueError("Cannot export an incomplete lineage: " + "; ".join(provenance_errors))
    if artifacts:
        artifact_errors = artifacts.audit_integrity()
        if artifact_errors:
            raise ValueError("Cannot export corrupt artifacts: " + "; ".join(artifact_errors))

    attempts = store.rows_for_export("attempts", run_id)
    attempt_ids = {str(row["attempt_id"]) for row in attempts}
    score_attempt_ids = {
        str(row["attempt_id"]) for row in store.rows_for_export("scores", run_id)
    }
    if not score_attempt_ids.issubset(attempt_ids):
        raise ValueError("Score rows do not reconcile to exported attempt rows")

    destination = Path(output_directory) / run_id
    destination.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    counts: dict[str, int] = {}
    for table_name, schema in EXPORT_SCHEMAS.items():
        rows = [_normalize_row(row, schema) for row in store.rows_for_export(table_name, run_id)]
        table = pa.Table.from_pylist(rows, schema=schema)
        path = destination / f"{table_name}.parquet"
        _write_immutable_parquet(path, table)
        digest = _sha256(path)
        hashes[table_name] = digest
        counts[table_name] = table.num_rows
        store.record_export(
            run_id,
            table_name,
            path.relative_to(Path(output_directory)).as_posix(),
            digest,
            table.num_rows,
        )
    return ParquetExport(run_id, destination, hashes, counts)


class ParquetExporter:
    """Object-oriented facade retained for callers that schedule exports."""

    def __init__(self, output_directory: str | Path) -> None:
        self.output_directory = Path(output_directory)

    def export(
        self, store: SqliteRunStore, run_id: str, *, artifacts: ArtifactStore | None = None
    ) -> ParquetExport:
        return export_run_to_parquet(
            store, run_id, self.output_directory, artifacts=artifacts
        )


def _normalize_row(row: dict[str, Any], schema: pa.Schema) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field in schema:
        value = row.get(field.name)
        if field.name == "schema_version" and value is None:
            value = SCHEMA_VERSION
        if field.name in {
            "token_counts_json", "tool_calls_json", "metric_json", "feature_json",
            "catalog_json", "source_row_ids_json", "diagnostics_json",
        } and isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if field.type == pa.int8() and value is not None:
            value = int(value)
        normalized[field.name] = value
    return normalized


def _write_immutable_parquet(path: Path, table: pa.Table) -> None:
    if path.exists():
        existing = pq.read_table(path)
        if not existing.equals(table):
            raise ValueError(f"Immutable export already exists with different data: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        pq.write_table(
            table,
            temporary,
            compression="zstd",
            use_dictionary=False,
            write_statistics=True,
            version="2.6",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
