<#
.SYNOPSIS
    Entry point called by Windows Task Scheduler every morning.

.DESCRIPTION
    Runs the reconciliation for the default cut-off (23:59:59 of the previous day) using the
    repository's virtual environment. Database URLs come from the .env file in the repo root
    or from machine/user environment variables (RECON_ECC_URL, RECON_S4_URL).

    The process exit code is passed through so Task Scheduler's "Last Run Result" is meaningful:
        0 = finished   1 = failed   2 = differences found (only with -FailOnMismatch)
        3 = another run still in progress

.PARAMETER Cutoff
    Optional data cut-off (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS). Default: end of yesterday.

.PARAMETER FailOnMismatch
    Return exit code 2 when any row differs, so the task shows as failed in Task Scheduler.
#>
param(
    [string]$Cutoff = "",
    [switch]$FailOnMismatch
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Error "Virtual environment not found at $Python. See docs/scheduling_windows.md."
    exit 1
}

$RunArgs = @("-m", "pricing_reconciler", "run")
if ($Cutoff) { $RunArgs += @("--cutoff", $Cutoff) }
if ($FailOnMismatch) { $RunArgs += "--fail-on-mismatch" }

& $Python @RunArgs
exit $LASTEXITCODE
