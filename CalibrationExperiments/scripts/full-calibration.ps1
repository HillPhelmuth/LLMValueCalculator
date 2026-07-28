param(
    [string[]]$Manifests = @("manifests/smoke.yaml"),
    [int]$MaxCases = 5000,
    [string]$FittingData = $env:CALIBRATION_FITTING_DATA
)

$ErrorActionPreference = "Stop"
if ($env:CALIBRATION_FULL_APPROVED -ne "true") {
    throw "Full calibration requires CALIBRATION_FULL_APPROVED=true from the protected environment."
}
$manifestList = @($Manifests | ForEach-Object { $_ -split "," } | Where-Object { $_ })
if (-not $manifestList) {
    throw "A full run requires at least one manifest."
}
if (-not $FittingData -or -not (Test-Path $FittingData -PathType Leaf)) {
    throw "A full run requires a frozen JSONL fitting-data input before profile generation."
}
foreach ($required in @("CALIBRATION_APPROVED_BY", "CALIBRATION_APPROVED_UTC", "CALIBRATION_MODEL_SNAPSHOT_HASH", "CALIBRATION_CODE_COMMIT", "CALIBRATION_MANIFEST_SET_HASH")) {
    if (-not (Get-Item "Env:$required" -ErrorAction SilentlyContinue)) {
        throw "Missing required full-run approval value: $required"
    }
}

$runRoot = Join-Path (Get-Location) ".full-runs"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$manifestArguments = $manifestList -join ","

& uv run --locked python -c "import json, os, sys; from calibration.pipeline import FullRunApproval, PipelineBudget, manifest_set_hash; paths=tuple(sys.argv[1].split(',')); actual=manifest_set_hash(paths); expected=os.environ['CALIBRATION_MANIFEST_SET_HASH']; assert actual == expected, f'manifest set hash mismatch: {actual} != {expected}'; approval=FullRunApproval(os.environ['CALIBRATION_APPROVED_BY'], os.environ['CALIBRATION_APPROVED_UTC'], actual, os.environ['CALIBRATION_MODEL_SNAPSHOT_HASH'], os.environ['CALIBRATION_CODE_COMMIT'], PipelineBudget(int(os.environ.get('CALIBRATION_MAX_REQUESTS','100000')), int(os.environ.get('CALIBRATION_MAX_TOKENS','10000000')), float(os.environ.get('CALIBRATION_MAX_USD','1000')), int(os.environ.get('CALIBRATION_TIMEOUT_MINUTES','240')))); approval.validate(); print(json.dumps(approval.to_json() if hasattr(approval, 'to_json') else {'approved_by': approval.approved_by, 'manifest_set_hash': approval.manifest_set_hash}, sort_keys=True))" $manifestArguments | Set-Content (Join-Path $runRoot "approval.json")

foreach ($manifest in $manifestList) {
    $name = [IO.Path]::GetFileNameWithoutExtension($manifest)
    $output = Join-Path $runRoot $name
    $runArguments = @("run", $manifest, "--output", $output, "--max-cases", $MaxCases, "--workers", "8")
    $database = Join-Path $output "runs.sqlite3"
    if (Test-Path $database) {
        $resumeRunId = & uv run --locked python -c "import sys; from calibration.storage.sqlite import SqliteRunStore; store=SqliteRunStore(sys.argv[1]); print(store.latest_run_id()); store.close()" $database
        if ($resumeRunId) {
            $runArguments += @("--resume-run-id", ([string]$resumeRunId).Trim())
        }
    }
    & uv run --locked python -m calibration @runArguments | Tee-Object -FilePath (Join-Path $output "runner-summary.json")
    $summaryPath = Join-Path $output "runner-summary.json"
    if (-not (Test-Path $summaryPath)) {
        throw "Run summary was not produced for $manifest."
    }
    $summary = Get-Content $summaryPath -Raw | ConvertFrom-Json
    & uv run --locked python -m calibration audit $summary.run_id --database (Join-Path $output "runs.sqlite3") --artifacts (Join-Path $output "objects")
    & uv run --locked python -m calibration export $summary.run_id --database (Join-Path $output "runs.sqlite3") --output (Join-Path $output "exports") --artifacts (Join-Path $output "objects")
    & uv run --locked python -c "import sys; from calibration.pipeline import validate_fitting_gate; print(validate_fitting_gate(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])))" (Join-Path $output "runs.sqlite3") (Join-Path $output "objects") $manifest $MaxCases
}

$firstOutput = Join-Path $runRoot ([IO.Path]::GetFileNameWithoutExtension($manifestList[0]))
& uv run --locked python -c "import json, os, sys; from calibration.pipeline import manifest_set_hash, validate_fitting_gate, write_candidate_profile; manifests=tuple(sys.argv[1].split(',')); actual=manifest_set_hash(manifests); gate=validate_fitting_gate(sys.argv[2], sys.argv[3], manifests[0], int(sys.argv[4])); output=write_candidate_profile(sys.argv[5], sys.argv[6], manifest_hashes=tuple(manifest_set_hash((item,)) for item in manifests), aa_snapshot=os.environ['CALIBRATION_MODEL_SNAPSHOT_HASH'], profile_version=os.environ.get('CALIBRATION_PROFILE_VERSION','candidate-1.0.0')); print(json.dumps({'candidate_profile': str(output), 'manifest_set_hash': actual, 'gate': gate}, sort_keys=True))" `
    $manifestArguments `
    (Join-Path $firstOutput "runs.sqlite3") `
    (Join-Path $firstOutput "objects") `
    $MaxCases `
    $FittingData `
    (Join-Path $runRoot "candidate-profile.json") | Tee-Object -FilePath (Join-Path $runRoot "full-run-report.json")

if (Get-ChildItem -Path $runRoot -Recurse -Filter "production-profile*.json" -ErrorAction SilentlyContinue) {
    throw "Full calibration produced a production profile; automatic promotion is forbidden."
}
