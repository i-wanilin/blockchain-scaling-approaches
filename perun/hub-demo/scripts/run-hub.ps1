param(
    [string]$Config = "config\cluster.local.json"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& ".\scripts\preflight.ps1" -Node "ingrid" -Config $Config

New-Item -ItemType Directory -Force -Path "results\logs" | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = "results\logs\$timestamp-ingrid.log"

& ".\bin\perun-node.exe" -mode hub -config $Config 2>&1 |
    Tee-Object -FilePath $log
$status = $LASTEXITCODE
Write-Host "node=ingrid exit_code=$status log=$log"
exit $status
