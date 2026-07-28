from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from calibration.manifest import load_manifest
from calibration.schema import SchemaValidationError, validate_record
from calibration.security import redact
from calibration.storage.artifacts import ArtifactIntegrityError, ArtifactStore
from calibration.storage.parquet import export_run_to_parquet
from calibration.storage.sqlite import SqliteRunStore


class Phase1FoundationTests(unittest.TestCase):
    def test_previous_sqlite_schema_migrates_to_lifecycle_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previous.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL, manifest_json TEXT NOT NULL,
                    code_commit TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
                    started_utc TEXT NOT NULL, completed_utc TEXT, failure_message TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO runs VALUES ('old-run', 'old', 'h', '{}', 'c', 'running', 'now', NULL, NULL)"
            )
            connection.commit()
            connection.close()
            with SqliteRunStore(path) as store:
                store.cancel_run("old-run")
                self.assertEqual("cancelled", store.run_summary("old-run")["status"])

    def test_schema_rejects_unknown_and_missing_fields(self) -> None:
        fixture_directory = Path(__file__).parent / "fixtures" / "schemas"
        validate_record(
            "score",
            json.loads((fixture_directory / "score.valid.json").read_text()),
        )
        with self.assertRaises(SchemaValidationError):
            validate_record(
                "score",
                json.loads((fixture_directory / "score.invalid.json").read_text()),
            )
        with self.assertRaises(SchemaValidationError):
            validate_record("score", {"schema_version": "1.0"})
        with self.assertRaises(SchemaValidationError):
            validate_record(
                "score",
                {
                    "schema_version": "1.0",
                    "attempt_id": "a",
                    "scorer_name": "s",
                    "scorer_version": "1",
                    "metrics": {},
                    "secret": "must-not-be-accepted",
                },
            )

    def test_secret_redaction_is_recursive(self) -> None:
        value = redact(
            {
                "headers": {"Authorization": "Bearer sk-abcdefghijklmnop"},
                "nested": [{"api_key": "secret-value"}],
                "url": "https://example.test/?token=sk-abcdefghijklmnop",
            }
        )
        rendered = json.dumps(value)
        self.assertNotIn("sk-abcdefghijklmnop", rendered)
        self.assertNotIn("secret-value", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_artifact_readback_detects_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(directory)
            uri = store.put_json({"answer": "ok"})
            self.assertEqual({"answer": "ok"}, store.get_json(uri))
            (Path(directory) / uri).write_bytes(b"tampered")
            with self.assertRaises(ArtifactIntegrityError):
                store.get_json(uri)

    def test_leases_recover_and_provenance_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "cases.jsonl"
            dataset.write_text(
                '{"case_id":"1","prompt":"one","expected":"one"}\n',
                encoding="utf-8",
            )
            revision = hashlib.sha256(dataset.read_bytes()).hexdigest()
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                f"""
experiment_id: lease-test-v1
dataset:
  adapter: jsonl
  revision: sha256:{revision}
  split: validation
  sample_seed: 42
  options:
    path: cases.jsonl
models:
  - catalog_id: fake-1
    provider: fake
    provider_model: fake/echo-v1
    aa_snapshot: test
generation:
  temperature: 0
  max_output_tokens: 16
conditions: [baseline]
prompt_version: test-v1
scorers: [answer_exact_match]
""",
                encoding="utf-8",
            )
            manifest = load_manifest(manifest_path)
            with SqliteRunStore(root / "runs.sqlite3") as store:
                run_id = store.create_run(
                    manifest, "test", resolved_manifest=manifest.resolved(("1",))
                )
                self.assertEqual([], store.audit_provenance(run_id))
                self.assertIsNotNone(store.run_summary(run_id)["provenance"])
                work_id = store.create_work_item(run_id, "a" * 64)
                self.assertEqual(work_id, store.claim_work_item(run_id, "worker-a"))
                store._connection.execute(
                    "UPDATE work_items SET lease_expires_utc='2000-01-01T00:00:00+00:00' WHERE work_item_id=?",
                    (work_id,),
                )
                store._connection.commit()
                self.assertEqual(1, store.recover_expired_leases())
                self.assertEqual(work_id, store.claim_work_item(run_id, "worker-b"))

    def test_parquet_export_is_immutable_and_hashes_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "cases.jsonl"
            dataset.write_text(
                '{"case_id":"1","prompt":"one","expected":"one"}\n',
                encoding="utf-8",
            )
            revision = hashlib.sha256(dataset.read_bytes()).hexdigest()
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                f"""
experiment_id: export-test-v1
dataset:
  adapter: jsonl
  revision: sha256:{revision}
  split: validation
  sample_seed: 42
  options:
    path: cases.jsonl
models:
  - catalog_id: fake-1
    provider: fake
    provider_model: fake/echo-v1
    aa_snapshot: test
generation:
  temperature: 0
  max_output_tokens: 16
conditions: [baseline]
prompt_version: test-v1
scorers: [answer_exact_match]
""",
                encoding="utf-8",
            )
            manifest = load_manifest(manifest_path)
            from calibration.providers.fake import FakeProvider
            from calibration.runner.runner import CalibrationRunner

            with SqliteRunStore(root / "runs.sqlite3") as store:
                run_id = asyncio.run(
                    CalibrationRunner(
                        manifest,
                        manifest_path,
                        store,
                        ArtifactStore(root / "objects"),
                        providers={"fake": FakeProvider()},
                    ).run(code_commit="test")
                )["run_id"]
                first = export_run_to_parquet(store, str(run_id), root / "exports")
                second = export_run_to_parquet(store, str(run_id), root / "exports")
                self.assertEqual(first.files, second.files)
                self.assertEqual(first.files, store.run_summary(str(run_id))["export_hashes"])


if __name__ == "__main__":
    unittest.main()
