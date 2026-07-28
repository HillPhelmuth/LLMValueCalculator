param(
    [switch]$LicensesOnly
)

$ErrorActionPreference = "Stop"
if (-not $LicensesOnly) {
    uv run --locked pip-audit
}
uv run --locked pip-licenses --format=markdown --with-urls --with-authors
