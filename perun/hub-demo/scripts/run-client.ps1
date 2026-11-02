param(
    [Parameter(Mandatory = $true)][string]$Node,
    [int]$Payments = 0,
    [string]$Config = "config\cluster.local.json"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& ".\scripts\preflight.ps1" -Node $Node -Config $Config

New-Item -ItemType Directory -Force -Path "results\logs" | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = "results\logs\$timestamp-$Node.log"

& ".\bin\perun-node.exe" -mode client -node $Node -config $Config -payments $Payments 2>&1 |
    Tee-Object -FilePath $log
$status = $LASTEXITCODE
Write-Host "node=$Node exit_code=$status log=$log"
exit $status
