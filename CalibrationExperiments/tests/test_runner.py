from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from calibration.manifest import load_manifest
from calibration.providers.fake import FakeProvider
from calibration.runner.runner import CalibrationRunner
from calibration.storage.artifacts import ArtifactStore
from calibration.storage.sqlite import SqliteRunStore


class RunnerTests(unittest.TestCase):
    def test_end_to_end_run_cache_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = root / "cases.jsonl"
            dataset_path.write_text(
                '{"case_id":"1","prompt":"one","expected":"one"}\n'
                '{"case_id":"2","prompt":"two","expected":"two"}\n',
                encoding="utf-8",
            )
            revision = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                f"""
experiment_id: runner-test-v1
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
  repeats: 2
prompt_version: test-v1
conditions: [baseline, variant]
scorers: [answer_exact_match, answer_token_f1]
""",
                encoding="utf-8",
            )
            manifest = load_manifest(manifest_path)
            provider = FakeProvider()
            artifacts = ArtifactStore(root / "objects")

            with SqliteRunStore(root / "runs.sqlite3") as store:
                first = asyncio.run(
                    CalibrationRunner(
                        manifest,
                        manifest_path,
                        store,
                        artifacts,
                        providers={"fake": provider},
                    ).run(code_commit="test")
                )
                self.assertEqual(8, first["attempts"])
                self.assertEqual(16, first["scores"])
                self.assertEqual(8, provider.call_count)

                second = asyncio.run(
                    CalibrationRunner(
                        manifest,
                        manifest_path,
                        store,
                        artifacts,
                        providers={"fake": provider},
                    ).run(code_commit="test")
                )
                self.assertEqual(8, second["attempts"])
                self.assertEqual(8, provider.call_count)

                resumed = asyncio.run(
                    CalibrationRunner(
                        manifest,
                        manifest_path,
                        store,
                        artifacts,
                        providers={"fake": provider},
                    ).run(resume_run_id=str(first["run_id"]), code_commit="test")
                )
                self.assertEqual(8, resumed["attempts"])
                self.assertEqual(8, provider.call_count)


if __name__ == "__main__":
    unittest.main()

