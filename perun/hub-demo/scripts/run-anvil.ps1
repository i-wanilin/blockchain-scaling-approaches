param(
    [int]$BlockTime = 1
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$command = Get-Command anvil -ErrorAction SilentlyContinue
if ($command) {
    $anvil = $command.Source
} else {
    $fallback = Join-Path $env:LOCALAPPDATA "Foundry\v1.7.1\bin\anvil.exe"
    if (-not (Test-Path -LiteralPath $fallback)) {
        throw "Anvil was not found on PATH or at $fallback"
    }
    $anvil = $fallback
}

$listener = Get-NetTCPConnection -LocalPort 8545 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "TCP 8545 is already occupied. Stop the existing chain before starting Anvil."
}

New-Item -ItemType Directory -Force -Path "results\logs" | Out-Null
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = "results\logs\$timestamp-anvil.log"

Write-Host "Starting private-LAN Anvil; stop it only after Ingrid exits."
& $anvil --host 0.0.0.0 --chain-id 31337 --block-time $BlockTime 2>&1 |
    Tee-Object -FilePath $log
$status = $LASTEXITCODE
Write-Host "anvil exit_code=$status log=$log"
exit $status
