# Calibration operations runbook

All commands below run from `CalibrationExperiments` and use the locked environment.

## Contributor checks

```powershell
uv sync --locked
pwsh ./scripts/ci-smoke.ps1
pwsh ./scripts/rehearsal.ps1
```

The contributor path uses the fake provider and fixed smoke cases. A live OpenRouter
canary is opt-in and requires both a secret and the protected approval flag:

```powershell
$env:CALIBRATION_LIVE_APPROVED = "true"
$env:OPENROUTER_API_KEY = "(retrieve from the secret store)"
pwsh ./scripts/ci-smoke.ps1 -RunLiveOpenRouter
```

Never paste a key into a manifest, command line captured by CI, report, or issue.

## Operator workflow

1. Review `security/REVIEW.md`, the dataset registry/license notice, and the frozen
   OpenRouter catalog snapshot.
2. Prepare a dataset offline or from its reviewed registry entry:

   ```powershell
   uv run --locked python -m calibration prepare-dataset calibration/data/dataset_registry.yaml smoke-jsonl --offline
   ```

3. Validate the adapter and run the locked manifest:

   ```powershell
   uv run --locked python -m calibration check-adapter manifests/pr-smoke.yaml
   uv run --locked python -m calibration run manifests/pr-smoke.yaml --output .full-runs/pr-smoke
   ```

4. Monitor, cancel, and resume without replaying completed request hashes:

   ```powershell
   uv run --locked python -m calibration status --database .full-runs/pr-smoke/runs.sqlite3 --expected-cells 20
   uv run --locked python -m calibration cancel RUN_ID --database .full-runs/pr-smoke/runs.sqlite3
   uv run --locked python -m calibration run manifests/pr-smoke.yaml --output .full-runs/pr-smoke --resume-run-id RUN_ID
   ```

5. Audit and export before fitting:

   ```powershell
   uv run --locked python -m calibration audit RUN_ID --database .full-runs/pr-smoke/runs.sqlite3 --artifacts .full-runs/pr-smoke/objects
   uv run --locked python -m calibration export RUN_ID --database .full-runs/pr-smoke/runs.sqlite3 --output .full-runs/pr-smoke/exports --artifacts .full-runs/pr-smoke/objects
   uv run --locked python -m calibration fit-candidate .full-runs/pr-smoke/fitting-data.jsonl --manifest-hash HASH --aa-snapshot SNAPSHOT
   uv run --locked python -m calibration render-report .full-runs/pr-smoke/reports/card.json --output .full-runs/pr-smoke/reports
   ```

6. Candidate review is explicit. Review the calibration card, scenario diff,
   provenance IDs, cost, and limitations. Then run `promotion-check` and, only after
   approval, `promote-candidate`. Use `rollback-profile HASH` to point the active
   profile at an earlier immutable hash; the append-only history is never rewritten.

## Incident procedures

- Credential rotation: revoke the old provider key, update the secret store, rerun
  `scripts/security-review.ps1`, and start a new run. Never edit historical artifacts.
- Model disappearance or catalog drift: stop the run, retain the frozen snapshot,
  refresh the catalog in a new review, and do not substitute a model silently.
- Budget exhaustion: inspect `status` and `budget_events`; cancel/resume only after a
  new approved ceiling is bound to a new run or manifest revision.
- Corrupt artifact: stop fitting, run `audit`, quarantine the run output, and rerun
  from the last immutable dataset/catalog snapshot.
- Failed migration or locked database: preserve the SQLite file, release stale
  processes, copy it for diagnosis, and resume only after integrity checks pass.
- Stalled work: inspect heartbeat age and queue depth in `status`; cancellation is
  resumable and leases recover after expiry.

## Compliance and retention

The policy in `security/data-policy.yaml` is the source of truth for provider data
collection, ZDR, dataset licenses, secret handling, retention, and full-run gates.
Full runs remain candidate-only and cannot automatically promote an application
profile.
