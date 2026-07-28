from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Mapping

from calibration.security import redact_text


class ConfigurationError(RuntimeError):
    """Raised when runtime configuration cannot safely be used."""


@dataclass(frozen=True, slots=True)
class CalibrationSettings:
    environment: str = "local"
    openrouter_api_key: str | None = None
    openrouter_http_referer: str | None = None
    openrouter_title: str | None = None
    database_url: str = "sqlite:///.calibration-runs/runs.sqlite3"
    artifact_root: str = ".calibration-runs/objects"
    dependency_lock: str = "uv.lock"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "CalibrationSettings":
        values = os.environ if environ is None else environ
        return cls(
            environment=values.get("CALIBRATION_ENV", "local"),
            openrouter_api_key=values.get("OPENROUTER_API_KEY"),
            openrouter_http_referer=values.get("OPENROUTER_HTTP_REFERER"),
            openrouter_title=values.get("X_OPENROUTER_TITLE"),
            database_url=values.get(
                "CALIBRATION_DATABASE_URL",
                "sqlite:///.calibration-runs/runs.sqlite3",
            ),
            artifact_root=values.get(
                "CALIBRATION_ARTIFACT_ROOT", ".calibration-runs/objects"
            ),
            dependency_lock=values.get("CALIBRATION_DEPENDENCY_LOCK", "uv.lock"),
        )

    def require_openrouter(self) -> str:
        if not self.openrouter_api_key:
            raise ConfigurationError(
                "OPENROUTER_API_KEY is required for OpenRouter runs; set it in the "
                "environment or an approved secret store"
            )
        return self.openrouter_api_key

    def safe_dict(self) -> dict[str, str | None]:
        return {
            "environment": self.environment,
            "openrouter_http_referer": self.openrouter_http_referer,
            "openrouter_title": self.openrouter_title,
            "database_url": self.database_url,
            "artifact_root": self.artifact_root,
            "dependency_lock": self.dependency_lock,
            "python_version": sys.version.split()[0],
        }

    def safe_error(self, error: BaseException) -> str:
        return redact_text(f"{type(error).__name__}: {error}")
