param(
    [string[]]$Peers = @()
)

$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path "results\environment" | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$output = "results\environment\$timestamp-$env:COMPUTERNAME.txt"

& {
    "timestamp_utc=$timestamp"
    "hostname=$env:COMPUTERNAME"
    "commit=$(git rev-parse HEAD)"
    go version
    Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,OsArchitecture,CsProcessors
    Get-NetIPConfiguration
    Get-NetConnectionProfile
    netsh wlan show interfaces
    powercfg /getactivescheme
    foreach ($peer in $Peers) {
        "ping_peer=$peer"
        ping -n 50 $peer
    }
} 2>&1 | Tee-Object -FilePath $output

Write-Host "environment_log=$output"
