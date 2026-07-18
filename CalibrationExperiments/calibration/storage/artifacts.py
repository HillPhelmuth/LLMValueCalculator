from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from calibration.schema import SCHEMA_VERSION, validate_record, with_schema_version
from calibration.security import redact


class ArtifactIntegrityError(ValueError):
    """Raised when an object or its metadata is missing or has changed."""


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    sha256: str
    media_type: str
    byte_length: int
    compression: str
    created_utc: str
    uri: str
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return with_schema_version({
            "sha256": self.sha256,
            "media_type": self.media_type,
            "byte_length": self.byte_length,
            "compression": self.compression,
            "created_utc": self.created_utc,
            "uri": self.uri,
            "schema_version": self.schema_version,
        })


class ArtifactStore:
    """Atomic, content-addressed storage with read-time integrity checking."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_json(
        self,
        value: Any,
        *,
        media_type: str = "application/json",
        compression: str = "none",
    ) -> str:
        safe_value = redact(value)
        content = json.dumps(
            safe_value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return self.put_bytes(
            content,
            media_type=media_type,
            compression=compression,
            extension="json",
        )

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
        compression: str = "none",
        extension: str = "bin",
    ) -> str:
        if compression not in {"none", "gzip"}:
            raise ValueError("compression must be 'none' or 'gzip'")
        digest = hashlib.sha256(content).hexdigest()
        relative = f"{digest[:2]}/{digest}.{extension}"
        destination = self.root / relative
        metadata_path = self._metadata_path(relative)
        encoded = gzip.compress(content, mtime=0) if compression == "gzip" else content

        if destination.exists() or metadata_path.exists():
            if not destination.is_file() or not metadata_path.is_file():
                raise ArtifactIntegrityError(f"Incomplete artifact: {relative}")
            existing = self._read_metadata(relative)
            if existing.sha256 != digest:
                raise ArtifactIntegrityError(f"Artifact hash collision: {relative}")
            self._read_and_verify(relative, existing)
            return relative

        destination.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(destination, encoded)
        metadata = ArtifactMetadata(
            sha256=digest,
            media_type=media_type,
            byte_length=len(content),
            compression=compression,
            created_utc=datetime.now(timezone.utc).isoformat(),
            uri=relative,
        )
        validate_record("artifact_metadata", metadata.to_json())
        self._atomic_write(
            metadata_path,
            json.dumps(
                metadata.to_json(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        self._read_and_verify(relative, metadata)
        return relative

    def get_json(self, uri: str) -> Any:
        content = self.get_bytes(uri)
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ArtifactIntegrityError(f"Artifact is not valid JSON: {uri}") from error

    def get_bytes(self, uri: str) -> bytes:
        metadata = self._read_metadata(uri)
        return self._read_and_verify(uri, metadata)

    def metadata(self, uri: str) -> ArtifactMetadata:
        return self._read_metadata(uri)

    def audit_integrity(self) -> list[str]:
        errors: list[str] = []
        data_files = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and not path.name.endswith(".meta.json")
        }
        for metadata_path in self.root.rglob("*.meta.json"):
            relative = metadata_path.relative_to(self.root).as_posix()
            uri = relative.removesuffix(".meta.json")
            try:
                metadata = self._read_metadata(uri)
                self._read_and_verify(uri, metadata)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"{uri}: {error}")
        metadata_files = {
            path.relative_to(self.root).as_posix().removesuffix(".meta.json")
            for path in self.root.rglob("*.meta.json")
            if path.is_file()
        }
        for uri in sorted(data_files - metadata_files):
            errors.append(f"{uri}: missing artifact metadata")
        return errors

    def _read_metadata(self, uri: str) -> ArtifactMetadata:
        metadata_path = self._metadata_path(uri)
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            validate_record("artifact_metadata", value)
            return ArtifactMetadata(**value)
        except FileNotFoundError as error:
            raise ArtifactIntegrityError(f"Missing artifact metadata: {uri}") from error
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ArtifactIntegrityError(f"Invalid artifact metadata: {uri}") from error

    def _read_and_verify(self, uri: str, metadata: ArtifactMetadata) -> bytes:
        path = self.root / uri
        try:
            encoded = path.read_bytes()
        except FileNotFoundError as error:
            raise ArtifactIntegrityError(f"Missing artifact: {uri}") from error
        content = gzip.decompress(encoded) if metadata.compression == "gzip" else encoded
        actual = hashlib.sha256(content).hexdigest()
        if actual != metadata.sha256 or len(content) != metadata.byte_length:
            raise ArtifactIntegrityError(f"Artifact integrity check failed: {uri}")
        return content

    def _metadata_path(self, uri: str) -> Path:
        return self.root / Path(uri).with_name(Path(uri).name + ".meta.json")

    @staticmethod
    def _atomic_write(destination: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
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
