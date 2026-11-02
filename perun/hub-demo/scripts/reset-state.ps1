param(
    [switch]$ClearResults
)

# Remove generated channel/identity state while preserving benchmark evidence.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
Remove-Item -Force -Recurse -ErrorAction SilentlyContinue .state
if ($ClearResults) {
    Remove-Item -Force -Recurse -ErrorAction SilentlyContinue results
}
Write-Host "Removed local distributed-benchmark state. Results cleared: $ClearResults"
