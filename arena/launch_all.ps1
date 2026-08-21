# Launch all 3 Model Arena lanes + the Arena backend as background jobs.
#
# Prereqs: glc_v5 already running on :8111 (see glc_v5's own README), and
# S17_CONTROL_TOKEN generated once and set below (same value every lane and
# the Arena backend must agree on).
#
# Usage: powershell -File arena/launch_all.ps1
# Stop everything: Get-Job | Stop-Job; Get-Job | Remove-Job

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $env:S17_CONTROL_TOKEN) {
    Write-Host "S17_CONTROL_TOKEN is not set in this shell. Generate one and export it first:" -ForegroundColor Yellow
    Write-Host '  $env:S17_CONTROL_TOKEN = (python -c "import secrets; print(secrets.token_urlsafe(32))")'
    exit 1
}

$lanes = Get-Content "$PSScriptRoot\lanes.json" | ConvertFrom-Json

foreach ($lane in $lanes.lanes) {
    $workspace = (Resolve-Path (Join-Path $PSScriptRoot $lane.workspace)).Path
    $dataDir = Join-Path $PSScriptRoot "data\$($lane.name)"
    $sandboxDir = Join-Path $PSScriptRoot "sandbox\$($lane.name)"
    New-Item -ItemType Directory -Force -Path $dataDir, $sandboxDir | Out-Null

    Start-Job -Name "arena-lane-$($lane.name)" -ScriptBlock {
        param($repoRoot, $port, $provider, $workspace, $dataDir, $sandboxDir, $token)
        Set-Location $repoRoot
        $env:S17_PORT = $port
        $env:S17_GATEWAY_PROVIDER = $provider
        $env:S17_WORKSPACE = $workspace
        $env:S17_DATA_DIR = $dataDir
        $env:S17_SANDBOX_ROOT = $sandboxDir
        $env:S17_A2A_GRPC_ENABLED = "0"
        $env:S17_SKILLS_DIR = Join-Path $repoRoot "skills"
        $env:S17_CONTROL_TOKEN = $token
        $env:GLC_BASE_URL = "http://127.0.0.1:8111"
        uv run s17code serve
    } -ArgumentList $repoRoot, $lane.port, $lane.provider, $workspace, $dataDir, $sandboxDir, $env:S17_CONTROL_TOKEN | Out-Null

    Write-Host "Launched lane '$($lane.name)' -> port $($lane.port), provider $($lane.provider)"
}

Start-Job -Name "arena-backend" -ScriptBlock {
    param($repoRoot, $token)
    Set-Location $repoRoot
    $env:S17_CONTROL_TOKEN = $token
    uv run python -m arena.server
} -ArgumentList $repoRoot, $env:S17_CONTROL_TOKEN | Out-Null

Write-Host "Launched Arena backend -> http://127.0.0.1:8090"
Write-Host "Jobs: $(Get-Job | Select-Object -ExpandProperty Name)"
