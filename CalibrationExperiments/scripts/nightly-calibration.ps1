param(
    [int]$MaxCases = 20
)

$ErrorActionPreference = "Stop"
if ($MaxCases -lt 1 -or $MaxCases -gt 50) {
    throw "Nightly subset must contain between 1 and 50 cases."
}

$runRoot = Join-Path (Get-Location) ".nightly-runs"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$manifest = "manifests/pr-smoke.yaml"
$mode = "offline-fake"

if ($env:CALIBRATION_NIGHTLY_LIVE -eq "true") {
    if (-not $env:OPENROUTER_API_KEY) {
        throw "Live nightly calibration requires OPENROUTER_API_KEY."
    }
    $manifest = "manifests/openrouter-smoke.yaml"
    $mode = "openrouter"
}

& uv run --locked python -m calibration run $manifest --output $runRoot --max-cases $MaxCases --workers 2 | Tee-Object -FilePath (Join-Path $runRoot "runner-summary.json")

$summary = Get-Content (Join-Path $runRoot "runner-summary.json") -Raw | ConvertFrom-Json
$runId = [string]$summary.run_id
if (-not $runId) {
    throw "Calibration runner did not return a run ID."
}

& uv run --locked python -c "import json, sys; from pathlib import Path; from calibration.pipeline import write_nightly_report; write_nightly_report(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6])" `
    $runRoot `
    $runId `
    (Join-Path $runRoot "nightly-report.json") `
    $MaxCases `
    $mode `
    (Join-Path $runRoot "baseline.json")

if (Get-ChildItem -Path $runRoot -Recurse -Filter "*profile*.json" -ErrorAction SilentlyContinue) {
    throw "Nightly calibration produced a profile artifact; promotion is forbidden."
}
