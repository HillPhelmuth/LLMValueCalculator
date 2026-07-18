param(
    [string]$Output = ".rehearsal-runs"
)

$ErrorActionPreference = "Stop"
uv run --locked python -m calibration rehearse --output $Output
