param(
    [Parameter(Mandatory = $true)][string]$Node,
    [string]$Config = "config\cluster.local.json"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$binary = "bin\perun-node.exe"
if (-not (Test-Path -LiteralPath $binary)) {
    throw "No Windows perun-node binary found; run .\scripts\build.ps1 first."
}

& ".\$binary" -mode preflight -node $Node -config $Config
if ($LASTEXITCODE -ne 0) {
    throw "Preflight failed with exit code $LASTEXITCODE"
}
