from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from calibration.datasets.base import DatasetAdapter
from calibration.manifest import DatasetConfig
from calibration.models import CanonicalCase, CaseFeatures, Message


class JsonlDatasetAdapter(DatasetAdapter):
    """Small deterministic adapter used by smoke runs and local datasets."""

    def __init__(self, config: DatasetConfig, manifest_directory: Path) -> None:
        self._config = config
        raw_path = config.options.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("The jsonl dataset adapter requires dataset.options.path")
        self._path = (manifest_directory / raw_path).resolve()

    def prepare(self) -> None:
        if not self._path.is_file():
            raise FileNotFoundError(f"Dataset file does not exist: {self._path}")

        expected = self._config.revision.removeprefix("sha256:")
        actual = hashlib.sha256(self._path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(
                f"Dataset revision mismatch for {self._path}: expected {expected}, got {actual}"
            )

    def cases(self, split: str) -> Iterable[CanonicalCase]:
        with self._path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("split", self._config.split) != split:
                    continue
                try:
                    case_id = str(row["case_id"])
                    prompt = str(row["prompt"])
                    expected = row["expected"]
                except KeyError as error:
                    raise ValueError(
                        f"Missing {error.args[0]!r} on line {line_number} of {self._path}"
                    ) from error

                metadata = row.get("metadata", {})
                if not isinstance(metadata, dict):
                    raise ValueError(f"metadata must be an object on line {line_number}")

                yield CanonicalCase(
                    case_id=case_id,
                    input={"prompt": prompt},
                    expected=expected,
                    metadata=metadata,
                )

    def render(self, case: CanonicalCase, condition: str) -> tuple[Message, ...]:
        conditions = case.metadata.get("condition_prompts", {})
        prompt = conditions.get(condition, case.input["prompt"])
        return (Message(role="user", content=str(prompt)),)

    def metadata(self, case: CanonicalCase) -> CaseFeatures:
        known = {
            "category",
            "base_difficulty_stratum",
            "context_band",
            "reasoning_depth",
            "domain_band",
            "tool_horizon",
            "verifiability_band",
            "output_band",
            "criticality_band",
        }
        extra = {key: value for key, value in case.metadata.items() if key not in known}
        return CaseFeatures(
            case_id=case.case_id,
            dataset_id=self._config.adapter,
            dataset_revision=self._config.revision,
            split=self._config.split,
            category=_string_or_none(case.metadata.get("category")),
            base_difficulty_stratum=_string_or_none(
                case.metadata.get("base_difficulty_stratum")
            ),
            context_band=_string_or_none(case.metadata.get("context_band")),
            reasoning_depth=_string_or_none(case.metadata.get("reasoning_depth")),
            domain_band=_string_or_none(case.metadata.get("domain_band")),
            tool_horizon=_string_or_none(case.metadata.get("tool_horizon")),
            verifiability_band=_string_or_none(
                case.metadata.get("verifiability_band")
            ),
            output_band=_string_or_none(case.metadata.get("output_band")),
            criticality_band=_string_or_none(case.metadata.get("criticality_band")),
            feature_json=extra,
        )


def _string_or_none(value: Any) -> str | None:
    return None if value is None else str(value)

