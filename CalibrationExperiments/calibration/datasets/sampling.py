from __future__ import annotations

import hashlib
import json
import random
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

from calibration.models import CanonicalCase


class SamplingError(ValueError):
    """Raised when a sample or holdout lock would be invalid."""


@dataclass(frozen=True, slots=True)
class SampleLock:
    dataset_id: str
    dataset_revision: str
    split: str
    seed: int
    fit_case_ids: tuple[str, ...]
    holdout_case_ids: tuple[str, ...]
    holdout_dataset_ids: tuple[str, ...] = ()
    strata_counts: dict[str, int] | None = None
    membership_hash: str = ""

    def __post_init__(self) -> None:
        if not self.fit_case_ids:
            raise SamplingError("Fit sample must contain at least one case")
        if set(self.fit_case_ids) & set(self.holdout_case_ids):
            raise SamplingError("Fit and holdout case IDs must be disjoint")
        if not self.membership_hash:
            material = {
                "dataset_id": self.dataset_id,
                "dataset_revision": self.dataset_revision,
                "split": self.split,
                "fit_case_ids": list(self.fit_case_ids),
                "holdout_case_ids": list(self.holdout_case_ids),
                "holdout_dataset_ids": list(self.holdout_dataset_ids),
            }
            digest = hashlib.sha256(
                json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            object.__setattr__(self, "membership_hash", digest)

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {
            "fit_case_ids": list(self.fit_case_ids),
            "holdout_case_ids": list(self.holdout_case_ids),
            "holdout_dataset_ids": list(self.holdout_dataset_ids),
        }

    def write(self, directory: str | Path) -> tuple[Path, Path, Path]:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / "sample-lock.json"
        fit_path = root / "fit-case-ids.txt"
        holdout_path = root / "holdout-case-ids.txt"
        lock_path.write_text(
            json.dumps(self.to_json(), sort_keys=True, indent=2), encoding="utf-8"
        )
        fit_path.write_text("\n".join(self.fit_case_ids) + "\n", encoding="utf-8")
        holdout_path.write_text(
            "\n".join(self.holdout_case_ids) + ("\n" if self.holdout_case_ids else ""),
            encoding="utf-8",
        )
        return lock_path, fit_path, holdout_path


def stratified_sample(
    cases: Iterable[CanonicalCase],
    sample_size: int,
    *,
    seed: int,
    stratum: Callable[[CanonicalCase], str] | None = None,
) -> tuple[CanonicalCase, ...]:
    """Select an explicit, deterministic sample with round-robin strata coverage."""
    rows = list(cases)
    if sample_size < 1 or sample_size > len(rows):
        raise SamplingError("sample_size must be within the available case count")
    if len({case.case_id for case in rows}) != len(rows):
        raise SamplingError("Cannot sample duplicate case IDs")
    key = stratum or (lambda case: str(case.metadata.get("category", "unstratified")))
    groups: dict[str, list[CanonicalCase]] = defaultdict(list)
    for case in rows:
        groups[key(case)].append(case)
    rng = random.Random(seed)
    for group in groups.values():
        group.sort(key=lambda case: case.case_id)
        rng.shuffle(group)
    selected: list[CanonicalCase] = []
    while len(selected) < sample_size:
        progressed = False
        for group_name in sorted(groups):
            group = groups[group_name]
            if group and len(selected) < sample_size:
                selected.append(group.pop())
                progressed = True
        if not progressed:
            break
    selected.sort(key=lambda case: case.case_id)
    return tuple(selected)


def freeze_sample(
    cases: Iterable[CanonicalCase],
    *,
    dataset_id: str,
    dataset_revision: str,
    split: str,
    sample_size: int,
    seed: int,
    output_directory: str | Path,
    holdout_fraction: float = 0.25,
    holdout_case_ids: Iterable[str] = (),
    holdout_dataset_ids: Iterable[str] = (),
) -> SampleLock:
    rows = list(cases)
    if not 0 <= holdout_fraction < 1:
        raise SamplingError("holdout_fraction must be between 0 and 1")
    selected = stratified_sample(rows, sample_size, seed=seed)
    selected_ids = {case.case_id for case in selected}
    explicit_holdout = set(holdout_case_ids)
    if not explicit_holdout.issubset(selected_ids):
        missing = sorted(explicit_holdout - selected_ids)
        raise SamplingError(f"Holdout IDs are not in the sample: {missing}")
    count = max(len(explicit_holdout), int(round(len(selected) * holdout_fraction)))
    shuffled = list(selected)
    random.Random(seed + 1).shuffle(shuffled)
    holdout = explicit_holdout | {case.case_id for case in shuffled[:count]}
    fit_ids = tuple(sorted(selected_ids - holdout))
    holdout_ids = tuple(sorted(holdout))
    if not fit_ids:
        raise SamplingError("Holdout selection consumed the entire fit sample")
    strata_counts: dict[str, int] = defaultdict(int)
    for case in selected:
        strata_counts[str(case.metadata.get("category", "unstratified"))] += 1
    lock = SampleLock(
        dataset_id=dataset_id,
        dataset_revision=dataset_revision,
        split=split,
        seed=seed,
        fit_case_ids=fit_ids,
        holdout_case_ids=holdout_ids,
        holdout_dataset_ids=tuple(sorted(set(holdout_dataset_ids))),
        strata_counts=dict(sorted(strata_counts.items())),
    )
    lock.write(output_directory)
    return lock


def assert_no_leakage(
    fit_cases: Iterable[CanonicalCase],
    holdout_cases: Iterable[CanonicalCase],
    *,
    similarity_threshold: float = 0.98,
) -> None:
    fit = list(fit_cases)
    holdout = list(holdout_cases)
    fit_ids = {case.case_id for case in fit}
    holdout_ids = {case.case_id for case in holdout}
    if fit_ids & holdout_ids:
        raise SamplingError("Case IDs overlap between fit and holdout")
    normalized_fit = {
        case.case_id: _normalize_text(case.input) for case in fit
    }
    for case in holdout:
        candidate = _normalize_text(case.input)
        for fit_id, fit_text in normalized_fit.items():
            if candidate == fit_text or SequenceMatcher(None, candidate, fit_text).ratio() >= similarity_threshold:
                raise SamplingError(
                    f"Potential prompt leakage between fit {fit_id} and holdout {case.case_id}"
                )


def hide_holdout_labels(case: CanonicalCase) -> CanonicalCase:
    """Return a fitting-safe view that never carries a holdout answer."""
    metadata = dict(case.metadata)
    metadata["label_available"] = False
    metadata["holdout_label_hidden"] = True
    return CanonicalCase(case.case_id, dict(case.input), None, metadata)


def _normalize_text(value: object) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return re.sub(r"\s+", " ", text).strip().casefold()
