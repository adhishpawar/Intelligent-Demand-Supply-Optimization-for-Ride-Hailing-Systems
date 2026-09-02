#Requires -Version 5.1
<#
.SYNOPSIS
    One-command bring-up for the Glovatrix ride-hailing platform (PLAN §5.2 / AM-20:
    "a single run.ps1, written at C1, extended per service" — this is that script,
    kept current through every checkpoint tonight).

.DESCRIPTION
    From a clean clone, on a machine with no infra pre-installed, this:
      1. Ensures a Python venv with dependencies installed.
      2. Ensures native Postgres/Redis/Kafka binaries are present, downloading and
         initializing them on first run if not (same portable-binary approach used
         throughout this session — see PROGRESS.md for why native, not Docker:
         WSL2/Hyper-V is unavailable in the environment this was built in).
      3. Starts Postgres (5433), Redis (6380), Kafka (9092) if not already running.
      4. Runs migrations and (unless -SkipSeed) the deterministic seed data.
      5. Starts all 9 backend services + the API gateway as background processes.
      6. Ensures a portable Node.js is present, installs web-react's npm deps if
         needed, and starts the Vite dev server.
      7. Prints every URL and the seeded demo credentials.

    Everything started here is tracked in .run/state.json; use stop.ps1 to tear it
    all down cleanly. Safe to re-run: already-running components are left alone.

.PARAMETER SkipSeed
    Skip the deterministic seed step (use when you already have real data you don't
    want overwritten — seed.py is an upsert, so this is a convenience, not a safety
    requirement).

.PARAMETER Fresh
    Wipe Postgres/Kafka data directories and reinitialize from scratch before
    starting. Use this to prove the "cold docker compose down -v equivalent" claim
    from PLAN §5's C10 DONE-criteria.

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Fresh
#>
param(
    [switch]$SkipSeed,
    [switch]$Fresh
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Native = Join-Path $Root "infra\native"
$RunDir = Join-Path $Root ".run"
$LogDir = Join-Path $RunDir "logs"
$StateFile = Join-Path $RunDir "state.json"
$KafkaHome = "C:\kafka-rh"           # short path deliberately (see infra note below) --
                                     # matches the install this session actually made
$NodeHome = "C:\node-portable\node"  # ditto -- matches this session's actual install

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
$State = @{ pids = @() }

function Save-State { $State | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $StateFile }
function Track-Pid($ProcessId, $Label) { $State.pids += @{ pid = $ProcessId; label = $Label }; Save-State }

function Test-PortOpen([int]$Port) {
    # Checks actual listening sockets rather than attempting a TCP connect --
    # found the hard way that Vite's dev server binds to the IPv6 loopback ([::1])
    # by default, which a `TcpClient.Connect("127.0.0.1", ...)` probe never sees,
    # producing a false "not running" that then fails to start a second instance
    # on an already-occupied port. Get-NetTCPConnection sees any listener,
    # IPv4 or IPv6, which is what "is something already serving this port" means.
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $conns
}

function Wait-Port([int]$Port, [string]$Label, [int]$TimeoutSec = 60) {
    Write-Host "  waiting for $Label on port $Port..." -NoNewline
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen $Port) { Write-Host " ready" -ForegroundColor Green; return }
        Start-Sleep -Milliseconds 500
    }
    Write-Host " TIMEOUT" -ForegroundColor Red
    throw "$Label did not come up on port $Port within ${TimeoutSec}s -- check $LogDir"
}

function Start-Tracked([string]$FilePath, [string]$ArgumentList, [string]$Label, [string]$WorkDir = $Root) {
    $out = Join-Path $LogDir "$Label.log"
    $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkDir `
        -RedirectStandardOutput $out -RedirectStandardError "$out.err" -WindowStyle Hidden -PassThru
    Track-Pid $p.Id $Label
    Write-Host "  started $Label (pid $($p.Id))"
    return $p
}

Write-Host "=== Glovatrix platform bring-up ===" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. Python venv
Write-Host "`n[1/7] Python environment"
$VenvPython = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "  creating venv..."
    python -m venv (Join-Path $Root "venv")
}
$reqHash = (Get-FileHash (Join-Path $Root "requirements.txt")).Hash
$reqHashFile = Join-Path $RunDir "requirements.sha256"
if (-not (Test-Path $reqHashFile) -or (Get-Content $reqHashFile) -ne $reqHash) {
    Write-Host "  installing/updating dependencies..."
    & (Join-Path $Root "venv\Scripts\pip.exe") install -q -r (Join-Path $Root "requirements.txt")
    Set-Content $reqHashFile $reqHash
} else {
    Write-Host "  dependencies up to date"
}

# ---------------------------------------------------------------- 2. Postgres
Write-Host "`n[2/7] PostgreSQL (native, port 5433)"
$PgDir = Join-Path $Native "pg"
$PgData = Join-Path $Native "pgdata"
$PgLogs = Join-Path $Native "pglogs"
if (-not (Test-Path $PgDir)) {
    Write-Host "  downloading portable PostgreSQL 16..."
    $zip = Join-Path $Native "postgres.zip"
    New-Item -ItemType Directory -Force -Path $Native | Out-Null
    Invoke-WebRequest -Uri "https://get.enterprisedb.com/postgresql/postgresql-16.4-1-windows-x64-binaries.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Native "pg_extract") -Force
    Move-Item (Join-Path $Native "pg_extract\pgsql") $PgDir
    Remove-Item (Join-Path $Native "pg_extract") -Recurse -Force
    Remove-Item $zip -Force
}
if ($Fresh -and (Test-Path $PgData)) { Remove-Item $PgData -Recurse -Force }
if (-not (Test-Path $PgData)) {
    Write-Host "  initializing data directory..."
    $pwFile = Join-Path $Native "pgpass.tmp"
    Set-Content $pwFile "ridehail_dev_pw" -NoNewline
    & (Join-Path $PgDir "bin\initdb.exe") -D $PgData -U ridehail --pwfile=$pwFile -A md5 -E UTF8 | Out-Null
    Remove-Item $pwFile
}
if (-not (Test-PortOpen 5433)) {
    New-Item -ItemType Directory -Force -Path $PgLogs | Out-Null
    & (Join-Path $PgDir "bin\pg_ctl.exe") -D $PgData -l (Join-Path $PgLogs "pg.log") -o "-p 5433" start | Out-Null
    Wait-Port 5433 "PostgreSQL"
    $env:PGPASSWORD = "ridehail_dev_pw"
    $dbExists = & (Join-Path $PgDir "bin\psql.exe") -h localhost -p 5433 -U ridehail -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='ridehail'"
    if (-not $dbExists) {
        & (Join-Path $PgDir "bin\psql.exe") -h localhost -p 5433 -U ridehail -d postgres -c "CREATE DATABASE ridehail OWNER ridehail;" | Out-Null
    }
} else {
    Write-Host "  already running"
}

# ---------------------------------------------------------------- 3. Redis
Write-Host "`n[3/7] Redis (native, port 6380)"
$RedisDir = Join-Path $Native "redis"
if (-not (Test-Path (Join-Path $RedisDir "redis-server.exe"))) {
    Write-Host "  downloading portable Redis 5.0.14 (tporadowski/redis)..."
    $zip = Join-Path $Native "redis.zip"
    New-Item -ItemType Directory -Force -Path $RedisDir | Out-Null
    Invoke-WebRequest -Uri "https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $RedisDir -Force
    Remove-Item $zip -Force
}
$RedisConf = Join-Path $RedisDir "redis-ridehail.conf"
if (-not (Test-Path $RedisConf)) {
    @"
port 6380
bind 127.0.0.1
protected-mode no
daemonize no
save ""
appendonly no
"@ | Set-Content $RedisConf
}
if (-not (Test-PortOpen 6380)) {
    Start-Tracked (Join-Path $RedisDir "redis-server.exe") "`"$RedisConf`"" "redis" $RedisDir | Out-Null
    Wait-Port 6380 "Redis"
} else {
    Write-Host "  already running"
}

# ---------------------------------------------------------------- 4. Kafka
Write-Host "`n[4/7] Kafka (native, KRaft, port 9092)"
Write-Host "  NOTE: installed to $KafkaHome (a short path, not under the repo) --"
Write-Host "  Kafka's launcher builds a classpath from every jar in its lib dir, and"
Write-Host "  the resulting command line overflows Windows' length limit under a long,"
Write-Host "  space-containing repo path. Confirmed the hard way this session."
if (-not (Test-Path $KafkaHome)) {
    Write-Host "  downloading + installing Kafka 3.8.0..."
    $tgz = Join-Path $env:TEMP "kafka.tgz"
    Invoke-WebRequest -Uri "https://archive.apache.org/dist/kafka/3.8.0/kafka_2.13-3.8.0.tgz" -OutFile $tgz -UseBasicParsing
    tar -xzf $tgz -C $env:TEMP
    Move-Item (Join-Path $env:TEMP "kafka_2.13-3.8.0") $KafkaHome
    Remove-Item $tgz -Force

    Push-Location $KafkaHome
    $clusterUuid = & ".\bin\windows\kafka-storage.bat" random-uuid
    Copy-Item "config\kraft\server.properties" "config\kraft\server-ridehail.properties" -Force
    (Get-Content "config\kraft\server-ridehail.properties") -replace '^log\.dirs=.*', "log.dirs=$($KafkaHome -replace '\\','/')/kraft-logs" |
        Set-Content "config\kraft\server-ridehail.properties"
    & ".\bin\windows\kafka-storage.bat" format -t $clusterUuid -c "config\kraft\server-ridehail.properties" | Out-Null
    Pop-Location
}
if ($Fresh) {
    $krlogs = Join-Path $KafkaHome "kraft-logs"
    if (Test-Path $krlogs) {
        Remove-Item $krlogs -Recurse -Force
        Push-Location $KafkaHome
        $clusterUuid = & ".\bin\windows\kafka-storage.bat" random-uuid
        & ".\bin\windows\kafka-storage.bat" format -t $clusterUuid -c "config\kraft\server-ridehail.properties" | Out-Null
        Pop-Location
    }
}
if (-not (Test-PortOpen 9092)) {
    Start-Tracked (Join-Path $KafkaHome "bin\windows\kafka-server-start.bat") "config\kraft\server-ridehail.properties" "kafka" $KafkaHome | Out-Null
    Wait-Port 9092 "Kafka" 90
} else {
    Write-Host "  already running"
}

# ---------------------------------------------------------------- 5. Migrate + seed
Write-Host "`n[5/7] Database migrations + seed data"
& $VenvPython -m tools.migrate
if (-not $SkipSeed) {
    & $VenvPython -m tools.seed
} else {
    Write-Host "  (skipped: -SkipSeed)"
}

# ---------------------------------------------------------------- 6. Backend services
Write-Host "`n[6/7] Backend services"
$Services = @(
    @{ name = "identity";     port = 8001 },
    @{ name = "location";     port = 8002 },
    @{ name = "matching";     port = 8003 },
    @{ name = "trip";         port = 8004 },
    @{ name = "pricing";      port = 8005 },
    @{ name = "payment";      port = 8007 },
    @{ name = "notification"; port = 8008 },
    @{ name = "ratings";      port = 8009 },
    @{ name = "gateway";      port = 8000 }
)
foreach ($svc in $Services) {
    if (Test-PortOpen $svc.port) {
        Write-Host "  $($svc.name) already running on $($svc.port)"
        continue
    }
    $module = "services.$($svc.name).main:app"
    Start-Tracked $VenvPython "-m uvicorn $module --port $($svc.port)" $svc.name | Out-Null
}
foreach ($svc in $Services) { Wait-Port $svc.port $svc.name }

# ---------------------------------------------------------------- 7. Frontend
Write-Host "`n[7/7] Frontend (Vite + React, port 8080)"
if (-not (Get-Command node -ErrorAction SilentlyContinue) -and -not (Test-Path "$NodeHome\node.exe")) {
    Write-Host "  downloading portable Node.js 22..."
    $zip = Join-Path $env:TEMP "node.zip"
    Invoke-WebRequest -Uri "https://nodejs.org/dist/v22.11.0/node-v22.11.0-win-x64.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $env:TEMP -Force
    Move-Item (Join-Path $env:TEMP "node-v22.11.0-win-x64") $NodeHome
    Remove-Item $zip -Force
}
$NodeBin = if (Get-Command node -ErrorAction SilentlyContinue) { (Get-Command node).Source | Split-Path } else { $NodeHome }
$env:Path = "$NodeBin;$env:Path"

$WebReact = Join-Path $Root "web-react"
if (-not (Test-Path (Join-Path $WebReact "node_modules"))) {
    Write-Host "  installing npm dependencies (first run, this takes a minute)..."
    Push-Location $WebReact
    & "$NodeBin\npm.cmd" install --no-fund --no-audit *>&1 | Out-Null
    Pop-Location
}
if (-not (Test-PortOpen 8080)) {
    $out = Join-Path $LogDir "web-react.log"
    $p = Start-Process -FilePath "$NodeBin\npm.cmd" -ArgumentList "run dev" -WorkingDirectory $WebReact `
        -RedirectStandardOutput $out -RedirectStandardError "$out.err" -WindowStyle Hidden -PassThru
    Track-Pid $p.Id "web-react"
    Wait-Port 8080 "Vite dev server"
} else {
    Write-Host "  already running"
}

# ---------------------------------------------------------------- Done
Write-Host "`n=== Glovatrix is up ===" -ForegroundColor Green
Write-Host ""
Write-Host "  App:      http://localhost:8080/login"
Write-Host "  Gateway:  http://localhost:8000"
Write-Host ""
Write-Host "  Seeded accounts (phone + OTP; dev mode returns the code directly):"
Write-Host "    Admin:   +910000000001"
Write-Host "    Riders:  +919000000001 .. +919000000005"
Write-Host "    Drivers: +918000000001 .. +918000000012"
Write-Host ""
Write-Host "  Headless end-to-end proof:  .\venv\Scripts\python.exe -m tools.demo"
Write-Host "  Test suite:                 .\venv\Scripts\python.exe -m pytest"
Write-Host "  Stop everything:            .\stop.ps1"
Write-Host ""
