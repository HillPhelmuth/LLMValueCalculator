from pathlib import Path

import json
import yaml

from calibration.manifest import load_manifest
from calibration.sandbox import SandboxImageLock, SandboxPolicy
from calibration.security import assert_secret_free, redact


ROOT = Path(__file__).resolve().parents[1]


def test_data_policy_and_openrouter_manifest_require_zdr() -> None:
    policy = yaml.safe_load((ROOT / "security/data-policy.yaml").read_text(encoding="utf-8"))
    manifest = load_manifest(ROOT / "manifests/openrouter-smoke.yaml")
    requirements = policy["routing_requirements"]["openrouter"]
    assert manifest.routing.data_collection == requirements["data_collection"]
    assert manifest.routing.zdr is requirements["zdr"]
    assert policy["full_run_gate"]["require_security_review_status"] == "approved"


def test_secret_redaction_and_free_artifacts() -> None:
    secret = "sk-phase6-test-secret"
    value = {"api_key": secret, "nested": f"Bearer {secret}"}
    redacted = redact(value)
    assert_secret_free(redacted, [secret])


def test_sandbox_policy_forbids_network_and_privilege() -> None:
    lock = json.loads((ROOT / "security/sandbox-image-lock.json").read_text(encoding="utf-8"))
    policy = SandboxPolicy(
        image=SandboxImageLock(
            image=lock["image"],
            digest=lock["digest"],
            scan_report_hash=lock["scan_report_hash"],
        )
    )
    policy.validate()
    command = policy.docker_command(("python", "-c", "print('ok')"))
    assert "--network=none" in command
    assert "--cap-drop=ALL" in command


def test_security_review_and_threat_model_are_complete() -> None:
    review = (ROOT / "security/REVIEW.md").read_text(encoding="utf-8")
    threat_model = (ROOT / "security/threat-model.md").read_text(encoding="utf-8")
    for phrase in ("approved", "license", "dependency", "secret", "retention"):
        assert phrase in review.lower() or phrase in threat_model.lower()
