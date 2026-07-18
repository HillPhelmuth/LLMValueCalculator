param(
    [switch]$RunLiveOpenRouter
)

$ErrorActionPreference = "Stop"
$runRoot = Join-Path (Get-Location) ".ci-runs"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

uv run --locked ruff check calibration tests
uv run --locked pyright calibration/pipeline.py calibration/storage/sqlite.py tests/test_phase6_quality.py
uv run --locked python -m compileall -q calibration tests
uv run --locked python -m unittest discover -s tests -v
uv run --locked pytest -q
uv run --locked python -m calibration check-adapter manifests/pr-smoke.yaml
uv run --locked coverage run --source=calibration -m unittest discover -s tests -v
uv run --locked coverage xml -o (Join-Path $runRoot "coverage.xml")
uv run --locked coverage report
uv run --locked python -m calibration run manifests/pr-smoke.yaml --output (Join-Path $runRoot "fake") --workers 4

if ($RunLiveOpenRouter) {
    if (-not $env:OPENROUTER_API_KEY) {
        throw "Live smoke was requested but OPENROUTER_API_KEY is not configured."
    }
    if ($env:CALIBRATION_LIVE_APPROVED -ne "true") {
        throw "Live smoke requires the protected CALIBRATION_LIVE_APPROVED=true gate."
    }
    uv run --locked python -m calibration run manifests/openrouter-smoke.yaml --output (Join-Path $runRoot "openrouter") --workers 2
}
