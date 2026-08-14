# dg — PowerShell wrapper.
#
# Does only what must happen on the host: confirm Docker is running, build the image the
# first time, and hand the arguments to the container. Every other decision lives in
# src/decision_graph/cli.py so it exists once instead of once per shell dialect.
#
#   .\dg.ps1 init
#   .\dg.ps1 query "send_file" --mode why

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Fail($message, $fix) {
    Write-Host "error: $message" -ForegroundColor Red
    if ($fix) { Write-Host "  -> $fix" -ForegroundColor Yellow }
    exit 1
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker is not installed." "Install Docker Desktop: https://www.docker.com/products/docker-desktop"
}

# `docker info` fails when the engine is not running, which is the single most common
# cause of every other error this tool could report.
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail "Docker is installed but not running." "Start Docker Desktop, wait for the whale icon to settle, then retry."
}

docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail "This Docker has no 'compose' subcommand." "Update Docker Desktop — Compose v2 is required."
}

# Build once. Docker caches layers, so a rebuild after a Dockerfile change is quick, but
# doing it on every command would add seconds to every query.
$image = docker compose images -q app 2>$null
if ([string]::IsNullOrWhiteSpace($image) -or $args[0] -eq 'rebuild') {
    Write-Host "Building the decision-graph image (first run only)..." -ForegroundColor Cyan
    docker compose build app
    if ($LASTEXITCODE -ne 0) { Fail "Image build failed." "Scroll up for the build error." }
    if ($args[0] -eq 'rebuild') { Write-Host "Rebuilt." -ForegroundColor Green; exit 0 }
}

docker compose run --rm app @args
exit $LASTEXITCODE
