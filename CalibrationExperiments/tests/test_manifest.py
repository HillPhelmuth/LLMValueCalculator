from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from calibration.manifest import load_manifest


VALID_MANIFEST = """
experiment_id: curve-v1
dataset:
  adapter: jsonl
  revision: sha256:abc
  split: validation
  sample_seed: 1847
models:
  - catalog_id: model-1
    provider: fake
    provider_model: fake/model-1
    aa_snapshot: 2026-07-01
generation:
  temperature: 0
  max_output_tokens: 32
  repeats: 1
prompt_version: prompt-v1
conditions: [baseline]
scorers: [answer_exact_match]
"""


class ManifestTests(unittest.TestCase):
    def test_hash_is_stable_for_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text(VALID_MANIFEST, encoding="utf-8")

            first = load_manifest(path)
            second = load_manifest(path)

        self.assertEqual(first.manifest_hash, second.manifest_hash)
        self.assertEqual(64, len(first.manifest_hash))

    def test_unknown_fields_fail_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text(VALID_MANIFEST + "unknown: true\n", encoding="utf-8")

            with self.assertRaises(ValidationError):
                load_manifest(path)

    def test_duplicate_conditions_fail_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.yaml"
            path.write_text(
                VALID_MANIFEST.replace(
                    "conditions: [baseline]", "conditions: [baseline, baseline]"
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                load_manifest(path)


if __name__ == "__main__":
    unittest.main()

