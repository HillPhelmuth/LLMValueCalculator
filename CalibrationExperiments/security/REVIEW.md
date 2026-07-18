# Security review record

Review ID: calibration-phase6-2026-07-18

Decision: approved for synthetic smoke, fake-provider CI, and recorded-fixture
rehearsal; conditional for live OpenRouter canaries; not an automatic approval for
production promotion.

Scope: `security/data-policy.yaml`, `security/threat-model.md`, locked Python
dependencies, dataset registry metadata, sandbox controls, secret redaction, and
content-addressed artifacts.

Required evidence before a full live run:

- `scripts/security-review.ps1` succeeds, including tests, secret scan, dependency
  audit, and license inventory.
- The exact manifest has `routing.data_collection: deny` and `routing.zdr: true`.
- Every external dataset has a reviewed license, terms URL, revision hash, and
  permitted-use note.
- An approver records the manifest, catalog, code, lockfile, and budget hashes.

Open findings: none for the approved synthetic scope. External operational data,
provider-specific retention exceptions, and unreviewed dataset terms are rejected
by policy rather than accepted implicitly.
