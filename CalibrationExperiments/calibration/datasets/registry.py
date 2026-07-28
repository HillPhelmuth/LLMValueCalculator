from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen

import yaml


class DatasetRegistryError(ValueError):
    """Raised when a dataset registry entry or prepared artifact is invalid."""


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """A reviewable, immutable description of one public dataset revision."""

    dataset_id: str
    adapter: str
    adapter_version: str
    source_url: str
    license: str
    revision: str
    file_name: str
    splits: tuple[str, ...]
    labels_available: bool = True
    terms_url: str | None = None
    citation: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        value = self.revision.removeprefix("sha256:")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
            raise DatasetRegistryError(
                f"Dataset {self.dataset_id} revision must be a sha256 digest"
            )
        return value.lower()

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"splits": list(self.splits), "sha256": self.sha256}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "DatasetSpec":
        required = (
            "dataset_id",
            "adapter",
            "adapter_version",
            "source_url",
            "license",
            "revision",
            "file_name",
            "splits",
        )
        missing = [key for key in required if not value.get(key)]
        if missing:
            raise DatasetRegistryError(f"Dataset entry is missing: {', '.join(missing)}")
        splits = tuple(str(split) for split in value["splits"])
        if not splits or len(splits) != len(set(splits)):
            raise DatasetRegistryError("Dataset splits must be non-empty and unique")
        spec = cls(
            dataset_id=str(value["dataset_id"]),
            adapter=str(value["adapter"]),
            adapter_version=str(value["adapter_version"]),
            source_url=str(value["source_url"]),
            license=str(value["license"]),
            revision=str(value["revision"]),
            file_name=str(value["file_name"]),
            splits=splits,
            labels_available=bool(value.get("labels_available", True)),
            terms_url=None if value.get("terms_url") is None else str(value["terms_url"]),
            citation=None if value.get("citation") is None else str(value["citation"]),
            notes=None if value.get("notes") is None else str(value["notes"]),
            metadata=dict(value.get("metadata", {})),
        )
        spec.sha256
        return spec


class DatasetRegistry:
    def __init__(self, entries: Mapping[str, DatasetSpec]) -> None:
        self._entries = dict(entries)
        if len(self._entries) != len(set(self._entries)):
            raise DatasetRegistryError("Dataset IDs must be unique")

    @classmethod
    def from_file(cls, path: str | Path) -> "DatasetRegistry":
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("datasets"), list):
            raise DatasetRegistryError("Registry must contain a datasets list")
        entries = [DatasetSpec.from_json(item) for item in document["datasets"]]
        ids = [entry.dataset_id for entry in entries]
        if len(ids) != len(set(ids)):
            raise DatasetRegistryError("Dataset IDs must be unique")
        return cls({entry.dataset_id: entry for entry in entries})

    def get(self, dataset_id: str) -> DatasetSpec:
        try:
            return self._entries[dataset_id]
        except KeyError as error:
            raise DatasetRegistryError(f"Unknown dataset ID: {dataset_id}") from error

    def entries(self) -> tuple[DatasetSpec, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))


@dataclass(frozen=True, slots=True)
class PreparedDataset:
    dataset_id: str
    revision: str
    path: str
    sha256: str
    lock_path: str
    license_notice: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


Downloader = Callable[[str], bytes]


class DatasetAcquirer:
    """Download once into a revision-addressed cache and support offline reuse."""

    def __init__(self, cache_root: str | Path) -> None:
        self.cache_root = Path(cache_root)

    def prepare(
        self,
        spec: DatasetSpec,
        *,
        offline: bool = False,
        downloader: Downloader | None = None,
    ) -> PreparedDataset:
        target_root = self.cache_root / spec.dataset_id / spec.sha256
        target = target_root / spec.file_name
        lock_path = target_root / "dataset.lock.json"
        notice_path = target_root / "LICENSE.txt"
        if target.is_file():
            self._verify(target, spec.sha256)
        elif offline:
            raise DatasetRegistryError(
                f"Offline preparation requested but {spec.dataset_id} is not cached"
            )
        else:
            target_root.mkdir(parents=True, exist_ok=True)
            content = (downloader or _download)(spec.source_url)
            actual = hashlib.sha256(content).hexdigest()
            if actual != spec.sha256:
                raise DatasetRegistryError(
                    f"Hash mismatch for {spec.dataset_id}: expected {spec.sha256}, got {actual}"
                )
            _atomic_write(target, content)
        notice = _license_notice(spec)
        _atomic_write(notice_path, notice.encode("utf-8"))
        lock = {
            "dataset": spec.to_json(),
            "prepared_path": str(target),
            "content_sha256": spec.sha256,
            "offline_reusable": True,
        }
        _atomic_write(
            lock_path,
            json.dumps(lock, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            ),
        )
        return PreparedDataset(
            dataset_id=spec.dataset_id,
            revision=spec.revision,
            path=str(target),
            sha256=spec.sha256,
            lock_path=str(lock_path),
            license_notice=str(notice_path),
        )

    @staticmethod
    def _verify(path: Path, expected: str) -> None:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise DatasetRegistryError(
                f"Cached dataset hash mismatch for {path}: expected {expected}, got {actual}"
            )


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "llm-value-calibration/0.1"})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - registry controls URLs
        return response.read()


def _license_notice(spec: DatasetSpec) -> str:
    terms = f"\nTerms: {spec.terms_url}" if spec.terms_url else ""
    citation = f"\nCitation: {spec.citation}" if spec.citation else ""
    notes = f"\nNotes: {spec.notes}" if spec.notes else ""
    return (
        f"Dataset: {spec.dataset_id}\nRevision: {spec.revision}\n"
        f"License: {spec.license}{terms}{citation}{notes}\n"
    )


def _atomic_write(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
