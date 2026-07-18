param(
    [switch]$SkipDependencyAudit,
    [switch]$SkipSecretScan
)

$ErrorActionPreference = "Stop"

.venv\Scripts\python.exe -m pytest -q tests\test_phase6_security.py

if (-not $SkipDependencyAudit) {
    .\scripts\dependency-audit.ps1
}

if (-not $SkipSecretScan) {
    $gitleaks = Get-Command gitleaks -ErrorAction SilentlyContinue
    if ($null -eq $gitleaks) {
        if ($env:CI -eq "true") {
            throw "gitleaks is required in CI before a full run."
        }
        Write-Warning "gitleaks is not installed locally; CI must provide the secret scanner."
    } else {
        gitleaks detect --no-banner --redact --source .
    }
}

Write-Output "Security and privacy review checks passed."
