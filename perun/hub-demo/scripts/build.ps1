# Build a native Windows client binary from the cloned repository.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
New-Item -ItemType Directory -Force -Path bin | Out-Null
go build -o bin/perun-node.exe .
if ($LASTEXITCODE -ne 0) {
    throw "go build failed with exit code $LASTEXITCODE"
}
Write-Host 'Built bin\perun-node.exe'
