from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


class FittingDatasetError(ValueError):
    """Raised when exported observations cannot be safely used for fitting."""


@dataclass(frozen=True, slots=True)
class FittingDatasetRules:
    version: str = "fitting-rules-1.0.0"
    included_splits: tuple[str, ...] = ("fit", "validation")
    excluded_failure_classes: tuple[str, ...] = ()
    require_public_label: bool = True
    minimum_cell_count: int = 1

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FittingRow:
    model_id: str
    dataset_id: str
    prompt_id: str
    case_id: str
    condition_id: str
    repeat_index: int
    success: bool | None
    split: str
    features: dict[str, Any]
    source_row_ids: tuple[str, ...]
    derived: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.model_id,
            self.dataset_id,
            self.prompt_id,
            self.case_id,
            self.condition_id,
            self.repeat_index,
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"source_row_ids": list(self.source_row_ids)}


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    rules_version: str
    input_rows: int
    included_rows: int
    exclusions: dict[str, int]
    cell_counts: dict[str, int]
    missing_fields: dict[str, int]
    holdout_rows_seen: int
    fitting_data_hash: str

    @property
    def passed(self) -> bool:
        return not self.missing_fields and not any(count < 1 for count in self.cell_counts.values())

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CanonicalFittingDataset:
    rows: tuple[FittingRow, ...]
    rules: FittingDatasetRules
    quality: DataQualityReport
    fitting_data_hash: str

    def to_json(self) -> dict[str, Any]:
        return {
            "rules": self.rules.to_json(),
            "fitting_data_hash": self.fitting_data_hash,
            "quality": self.quality.to_json(),
            "rows": [row.to_json() for row in self.rows],
        }

    def write(self, directory: str | Path) -> tuple[Path, Path]:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        rows_path = root / "fitting-data.jsonl"
        lock_path = root / "fitting-data.lock.json"
        rows_path.write_text(
            "".join(json.dumps(row.to_json(), sort_keys=True, separators=(",", ":")) + "\n" for row in self.rows),
            encoding="utf-8",
        )
        lock_path.write_text(
            json.dumps(
                {
                    "rules": self.rules.to_json(),
                    "quality": self.quality.to_json(),
                    "fitting_data_hash": self.fitting_data_hash,
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return rows_path, lock_path


def build_fitting_dataset(
    attempts: Iterable[Mapping[str, Any]],
    scores: Iterable[Mapping[str, Any]],
    case_features: Iterable[Mapping[str, Any]],
    *,
    rules: FittingDatasetRules | None = None,
) -> CanonicalFittingDataset:
    rules = rules or FittingDatasetRules()
    attempts_by_id = {str(row["attempt_id"]): row for row in attempts}
    features_by_case = {str(row["case_id"]): row for row in case_features}
    output: list[FittingRow] = []
    exclusions: dict[str, int] = {}
    missing_fields: dict[str, int] = {}
    holdout_seen = 0
    score_rows = tuple(scores)
    for score in score_rows:
        score_id = str(score.get("attempt_id", ""))
        attempt = attempts_by_id.get(score_id)
        if attempt is None:
            _increment(exclusions, "orphan_score")
            continue
        case_id = str(attempt.get("case_id", ""))
        features = features_by_case.get(case_id, {})
        split = str(features.get("split", attempt.get("split", "fit")))
        if split not in rules.included_splits:
            if "holdout" in split:
                holdout_seen += 1
                _increment(exclusions, "holdout")
            else:
                _increment(exclusions, "split")
            continue
        if rules.require_public_label and features.get("label_available", True) is False:
            _increment(exclusions, "label_unavailable")
            continue
        failure_class = score.get("failure_class")
        if failure_class in rules.excluded_failure_classes:
            _increment(exclusions, "excluded_failure_class")
            continue
        required = {
            "model_id": attempt.get("model_id"),
            "dataset_id": features.get("dataset_id"),
            "prompt_id": attempt.get("prompt_version"),
            "case_id": case_id,
            "condition_id": attempt.get("condition_id"),
            "repeat_index": attempt.get("repeat_index"),
        }
        absent = [key for key, value in required.items() if value is None or value == ""]
        if absent:
            for key in absent:
                _increment(missing_fields, key)
            continue
        source_ids = (score_id, f"{score_id}:{score.get('scorer_name', '')}:{score.get('scorer_version', '')}")
        output.append(
            FittingRow(
                model_id=str(required["model_id"]),
                dataset_id=str(required["dataset_id"]),
                prompt_id=str(required["prompt_id"]),
                case_id=case_id,
                condition_id=str(required["condition_id"]),
                repeat_index=int(required["repeat_index"]),
                success=None if score.get("success") is None else bool(score["success"]),
                split=split,
                features={key: value for key, value in features.items() if key not in {"case_id", "dataset_id", "split"}},
                source_row_ids=source_ids,
                derived=_derive_features(attempt, score, features),
            )
        )
    keys = [row.key() for row in output]
    if len(keys) != len(set(keys)):
        raise FittingDatasetError("Fitting transformation produced duplicate observation keys")
    cell_counts: dict[str, int] = {}
    for row in output:
        cell = f"{row.model_id}|{row.dataset_id}|{row.condition_id}"
        cell_counts[cell] = cell_counts.get(cell, 0) + 1
    if any(count < rules.minimum_cell_count for count in cell_counts.values()):
        raise FittingDatasetError("A fitting cell has fewer rows than the configured minimum")
    data_hash = _hash_rows(output)
    quality = DataQualityReport(
        rules_version=rules.version,
        input_rows=len(score_rows),
        included_rows=len(output),
        exclusions=dict(sorted(exclusions.items())),
        cell_counts=dict(sorted(cell_counts.items())),
        missing_fields=dict(sorted(missing_fields.items())),
        holdout_rows_seen=holdout_seen,
        fitting_data_hash=data_hash,
    )
    if missing_fields:
        raise FittingDatasetError(f"Required fitting fields are missing: {missing_fields}")
    return CanonicalFittingDataset(tuple(output), rules, quality, data_hash)


def read_parquet_rows(path: str | Path) -> tuple[dict[str, Any], ...]:
    import pyarrow.parquet as parquet

    return tuple(dict(row) for row in parquet.read_table(path).to_pylist())


def _derive_features(
    attempt: Mapping[str, Any], score: Mapping[str, Any], features: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "has_cost": attempt.get("provider_cost") is not None,
        "has_latency": attempt.get("latency_ms") is not None,
        "score_name": score.get("scorer_name"),
        "category": features.get("category"),
        "context_band": features.get("context_band"),
        "reasoning_depth": features.get("reasoning_depth"),
        "domain_band": features.get("domain_band"),
    }


def _hash_rows(rows: Iterable[FittingRow]) -> str:
    encoded = "\n".join(
        json.dumps(row.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for row in rows
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _increment(values: dict[str, int], key: str) -> None:
    values[key] = values.get(key, 0) + 1
