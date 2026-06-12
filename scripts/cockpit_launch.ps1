# cockpit_launch.ps1 — one-click desktop launch for the HFT3 Cockpit.
#
# Loopback-only (127.0.0.1): no token needed, no remote exposure. Builds the
# SPA on first run, starts the backend (which serves the SPA at /), opens the
# browser once the port is live, and is idempotent if already running.
#
#   scripts\cockpit_launch.ps1                 # build-if-needed, launch, open browser
#   scripts\cockpit_launch.ps1 -Rebuild        # force a fresh SPA build first
#   scripts\cockpit_launch.ps1 -NoBrowser      # don't auto-open the browser
param(
    [int]$Port = 8080,
    [switch]$NoBrowser,
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "C:/Users/MSI/.claude/shims;$repo;$repo/packages"
$url = "http://127.0.0.1:$Port/"

function Test-Port([int]$p) {
    try { $c = [System.Net.Sockets.TcpClient]::new('127.0.0.1', $p); $c.Close(); return $true }
    catch { return $false }
}

# Already running? Just open the browser and exit (double-click is idempotent).
if (Test-Port $Port) {
    Write-Host "HFT3 Cockpit already running -> $url"
    if (-not $NoBrowser) { Start-Process $url }
    return
}

# Build the SPA if the bundle is missing, forced, OR stale (any front-end source
# newer than the built bundle) — so new panels/changes are always reflected.
$frontend = Join-Path $repo "apps/cockpit/frontend"
$dist = Join-Path $frontend "dist"
$distIndex = Join-Path $dist "index.html"

$needBuild = $Rebuild -or -not (Test-Path $distIndex)
if (-not $needBuild) {
    $distTime = (Get-Item $distIndex).LastWriteTime
    $srcRoots = @(
        (Join-Path $frontend "src"),
        (Join-Path $frontend "index.html"),
        (Join-Path $frontend "package.json"),
        (Join-Path $frontend "vite.config.ts")
    ) | Where-Object { Test-Path $_ }
    $newest = Get-ChildItem -Recurse -File $srcRoots -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($newest -and $newest.LastWriteTime -gt $distTime) {
        Write-Host "Front-end source changed since last build -> rebuilding..."
        $needBuild = $true
    }
}
if ($needBuild) {
    Write-Host "Building cockpit frontend..."
    Push-Location $frontend
    try {
        if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
            npm install --no-fund --no-audit
        }
        npm run build
    } finally { Pop-Location }
}

# Open the browser once the server accepts connections (runs alongside uvicorn).
if (-not $NoBrowser) {
    Start-Job -Name CockpitOpen -ScriptBlock {
        param($u, $p)
        for ($i = 0; $i -lt 80; $i++) {
            try { $c = [System.Net.Sockets.TcpClient]::new('127.0.0.1', $p); $c.Close(); Start-Process $u; break }
            catch { Start-Sleep -Milliseconds 400 }
        }
    } -ArgumentList $url, $Port | Out-Null
}

Write-Host "HFT3 Cockpit -> $url   (close this window or Ctrl+C to stop)"
python -m uvicorn apps.cockpit.backend.main:app --host 127.0.0.1 --port $Port
