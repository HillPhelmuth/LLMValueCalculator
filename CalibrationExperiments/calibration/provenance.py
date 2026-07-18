from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from calibration.manifest import ExperimentManifest
from calibration.schema import SCHEMA_VERSION, validate_record, with_schema_version


@dataclass(frozen=True, slots=True)
class RunProvenance:
    provenance_id: str
    code_commit: str
    manifest_hash: str
    dependency_lock_hash: str
    dataset_revisions: tuple[str, ...]
    prompt_hashes: tuple[str, ...]
    model_snapshot_hash: str
    scorer_versions: dict[str, str]
    container_digests: tuple[str, ...]
    environment: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return with_schema_version(asdict(self))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_run_provenance(
    manifest: ExperimentManifest,
    code_commit: str,
    *,
    dependency_lock: str | Path | None = None,
    environment: dict[str, Any] | None = None,
) -> RunProvenance:
    lock_hash = "unavailable"
    if dependency_lock and Path(dependency_lock).is_file():
        lock_hash = file_sha256(dependency_lock)
    model_snapshot_hash = canonical_hash(
        [model.model_dump(mode="json") for model in manifest.models]
    )
    prompt_hashes = tuple(
        manifest.prompt_hashes
        or (hashlib.sha256(manifest.prompt_version.encode("utf-8")).hexdigest(),)
    )
    scorer_versions = dict(manifest.scorer_versions) or {
        scorer.name: scorer.version for scorer in manifest.scorer_locks
    }
    if not scorer_versions:
        scorer_versions = {name: "registry" for name in manifest.scorers}
    record = {
        "code_commit": code_commit,
        "manifest_hash": manifest.manifest_hash,
        "dependency_lock_hash": lock_hash,
        "dataset_revisions": (manifest.dataset.revision,),
        "prompt_hashes": prompt_hashes,
        "model_snapshot_hash": model_snapshot_hash,
        "scorer_versions": scorer_versions,
        "container_digests": tuple(manifest.containers.digests),
    }
    provenance_id = canonical_hash(record)
    provenance = RunProvenance(
        provenance_id=provenance_id,
        environment=environment
        or {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "implementation": platform.python_implementation(),
        },
        **record,
    )
    validate_record("run_provenance", provenance.to_json())
    return provenance
