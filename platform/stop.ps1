<#
.SYNOPSIS
    Stops everything run.ps1 started, tracked via .run/state.json. Falls back to
    port-based discovery for anything the state file missed (e.g. a process that
    outlived a previous crashed run.ps1 invocation).
#>
$Root = $PSScriptRoot
$StateFile = Join-Path $Root ".run\state.json"

$KnownPorts = @(8000, 8001, 8002, 8003, 8004, 8005, 8007, 8008, 8009, 8080, 5433, 6380, 9092)

Write-Host "=== Stopping Glovatrix platform ===" -ForegroundColor Cyan

if (Test-Path $StateFile) {
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    foreach ($entry in $state.pids) {
        try {
            Stop-Process -Id $entry.pid -Force -ErrorAction Stop
            Write-Host "  stopped $($entry.label) (pid $($entry.pid))"
        } catch {
            # already gone -- fine
        }
    }
    Remove-Item $StateFile -Force
} else {
    Write-Host "  no state file found -- falling back to port-based cleanup"
}

# Port-based safety net: anything still listening on our ports that the state file
# didn't know about (e.g. started manually, or state.json was lost).
foreach ($port in $KnownPorts) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        try {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop
            Write-Host "  stopped stray process on port $port (pid $($c.OwningProcess))"
        } catch {}
    }
}

Write-Host "`nAll stopped. Postgres/Redis/Kafka data directories are left intact --"
Write-Host "re-run .\run.ps1 to bring everything back up with the same data, or"
Write-Host ".\run.ps1 -Fresh to reinitialize from a clean state."
