# Calibration security and privacy threat model

Status: approved for the synthetic smoke and recorded-fixture rehearsal. External
full runs remain blocked until the dataset license and provider data-policy review
for that exact manifest is approved.

## Data flow

1. A locked dataset revision is acquired into a revision-addressed cache.
2. Canonical prompts are rendered in the runner and sent only to the provider named
   by the manifest routing lock.
3. Provider responses are normalized, redacted before logs, content-addressed in the
   artifact store, scored, exported, and linked to run provenance.
4. Profiles are written under immutable hash-addressed paths. Application artifacts
   include the profile hash and cannot overwrite a different generated artifact.

Raw secrets are never written to SQLite, Parquet, reports, or generated profiles.
Raw provider payload retention is disabled by policy for live OpenRouter runs; the
stored response representation must be redacted or approved as synthetic.

## Threats and controls

| Threat | Control | Disposition |
| --- | --- | --- |
| Provider/API secret leakage | environment/secret-store input, recursive secret scan, redacted transport errors | resolved |
| Prompt or response exposure | data-class policy, OpenRouter `data_collection: deny` and `zdr: true`, retention limit | resolved for approved manifests |
| Malicious dataset content | immutable revision/hash, schema validation, no executable dataset path | resolved |
| Generated-code escape | generated profile is data-only; application generation is allow-listed and review-gated | resolved |
| Dependency compromise | `uv.lock`, dependency audit, license inventory, reproducible environment | resolved when audit is green |
| Artifact tamper | content-addressed artifact audit and immutable profile paths | resolved |
| Budget abuse or runaway work | pre-reservation ceilings, settlement, heartbeats, alerts, cancellation | resolved |

## Review checklist

- [x] Manifest routing is reviewed against `security/data-policy.yaml`.
- [x] Dataset revision, license, terms, and permitted use are recorded before acquisition.
- [x] Secret scan and dependency audit are required before full runs.
- [x] Sandbox policy rejects network and privileged execution for generated code.
- [x] Retention and export paths are documented and auditable.
- [x] Findings are either resolved above or explicitly accepted by the run approver.

Reviewer acceptance is bound to the manifest hash, model snapshot hash, dependency
lock hash, and code commit. A changed input set requires a new review.
