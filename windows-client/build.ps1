$ErrorActionPreference = 'Stop'
$env:GOMODCACHE = Join-Path $PSScriptRoot '.nsgo-cache'
Push-Location $PSScriptRoot
try {
    go run github.com/tc-hib/go-winres@v0.3.3 simply --arch amd64 --icon logo.png --manifest none
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    go build -trimpath -ldflags '-s -w -H=windowsgui' -o nexus-client.exe .
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
