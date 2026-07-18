from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


class SandboxPolicyError(ValueError):
    """Raised when execution would violate the untrusted-code policy."""


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    cpu_seconds: int = 30
    memory_mb: int = 512
    process_count: int = 64
    disk_mb: int = 128
    wall_seconds: int = 45

    def validate(self) -> None:
        if min(self.cpu_seconds, self.memory_mb, self.process_count, self.disk_mb, self.wall_seconds) < 1:
            raise SandboxPolicyError("Sandbox limits must be positive")


@dataclass(frozen=True, slots=True)
class SandboxImageLock:
    image: str
    digest: str
    scan_report_hash: str
    critical_vulnerabilities: int = 0
    high_vulnerabilities: int = 0

    def validate(self) -> None:
        if not self.image or not self.digest.startswith("sha256:"):
            raise SandboxPolicyError("Sandbox images require an immutable sha256 digest")
        if len(self.digest) != len("sha256:") + 64:
            raise SandboxPolicyError("Sandbox image digest is malformed")
        if not self.scan_report_hash:
            raise SandboxPolicyError("Sandbox image requires a vulnerability scan lock")
        if self.critical_vulnerabilities or self.high_vulnerabilities:
            raise SandboxPolicyError("Sandbox image has unapproved critical/high vulnerabilities")

    @property
    def reference(self) -> str:
        self.validate()
        return f"{self.image}@{self.digest}"


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    image: SandboxImageLock
    limits: SandboxLimits = SandboxLimits()
    network_disabled: bool = True
    privileged: bool = False
    read_only_root: bool = True
    host_mounts: tuple[str, ...] = ()
    scratch_mount: str = "/tmp"

    def validate(self) -> None:
        self.image.validate()
        self.limits.validate()
        if not self.network_disabled or self.privileged or not self.read_only_root:
            raise SandboxPolicyError("Sandbox must be network-disabled, unprivileged, and read-only")
        if self.host_mounts:
            raise SandboxPolicyError("Host mounts are forbidden for untrusted execution")

    def docker_command(self, argv: Sequence[str]) -> list[str]:
        self.validate()
        if not argv:
            raise SandboxPolicyError("Sandbox command cannot be empty")
        return [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={self.limits.process_count}",
            f"--memory={self.limits.memory_mb}m",
            f"--cpus={max(0.1, self.limits.cpu_seconds / self.limits.wall_seconds):.2f}",
            "--tmpfs",
            f"{self.scratch_mount}:rw,noexec,nosuid,size={self.limits.disk_mb}m",
            self.image.reference,
            *argv,
        ]


@dataclass(frozen=True, slots=True)
class SandboxResult:
    return_code: int | None
    stdout: str
    stderr: str
    outcome: str
    timed_out: bool = False
    resource_usage: dict[str, float] | None = None

    def to_json(self) -> dict[str, object]:
        return asdict(self)


class DockerSandboxRunner:
    """Run a command under the locked Docker policy without shell interpolation."""

    def __init__(self, policy: SandboxPolicy) -> None:
        policy.validate()
        self.policy = policy

    def run(self, argv: Sequence[str]) -> SandboxResult:
        command = self.policy.docker_command(argv)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.policy.limits.wall_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return SandboxResult(
                return_code=None,
                stdout=_text(error.stdout),
                stderr=_text(error.stderr),
                outcome="timeout",
                timed_out=True,
            )
        except OSError as error:
            return SandboxResult(
                return_code=None,
                stdout="",
                stderr=str(error),
                outcome="infrastructure_failure",
            )
        outcome = "passed" if completed.returncode == 0 else "failed"
        if completed.returncode in {137, 143}:
            outcome = "resource_exhaustion"
        return SandboxResult(
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            outcome=outcome,
        )


def scan_lock_hash(report: dict[str, object]) -> str:
    """Hash an external scanner report for provenance without storing scanner secrets."""
    return hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_image_lock(path: str | Path) -> SandboxImageLock:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    lock = SandboxImageLock(**value)
    lock.validate()
    return lock


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
