param([string]$EvidenceDirectory, [string]$Workload, [switch]$Dashboard, [string]$DashboardReference, [string]$ReferenceBundle)

$ErrorActionPreference = "Stop"
$env:DURAI_CH_ADMIN_PASSWORD = [guid]::NewGuid().ToString("N")
$env:DURAI_CH_PUBLISHER_PASSWORD = [guid]::NewGuid().ToString("N")
$env:DURAI_CH_OIL_PASSWORD = [guid]::NewGuid().ToString("N")
$env:DURAI_CH_GAS_PASSWORD = [guid]::NewGuid().ToString("N")
$env:DURAI_CH_INTEGRATION = "1"
$env:DURAI_CH_HTTP = "http://127.0.0.1:58123"
$env:PYTHONPATH = "..;."
$project = "durai-reporting-ch-probe-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$composeFile = Join-Path $PSScriptRoot "compose.yaml"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$fixture = Join-Path $repo "design/reporting/energy_futures_close.json"
if (-not $Workload) { $Workload = Join-Path $repo "design/reporting/workload_v1.json" }
if ($Dashboard) {
    if (-not $DashboardReference) { $DashboardReference = Join-Path $repo "deploy/reporting/postgres" }
    if (-not $ReferenceBundle) { $ReferenceBundle = Join-Path $repo "design/reporting/superset_energy_futures_close_v1.zip" }
    $env:DURAI_CH_POLICY_FILE = (Resolve-Path (Join-Path $DashboardReference "superset_policy.py")).Path
}
$runDirectory = $null

function Invoke-Compose {
    docker compose -p $project -f $composeFile @args
    if ($LASTEXITCODE -ne 0) { throw "A Docker Compose command failed." }
}

function Wait-Endpoint([string]$Uri) {
    for ($step = 0; $step -lt 45; $step++) {
        try {
            $check = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 1
            if ($check.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "The local service did not become ready."
}

Push-Location (Join-Path $repo "tests")
try {
    Invoke-Compose up -d clickhouse
    Wait-Endpoint "$env:DURAI_CH_HTTP/ping"
    python -m unittest -v test_reporting_clickhouse
    if ($LASTEXITCODE -ne 0) { throw "The ClickHouse tests failed." }
    if ($EvidenceDirectory) {
        New-Item -ItemType Directory -Force -Path $EvidenceDirectory | Out-Null
        $probe = python -m deploy.reporting.clickhouse.benchmark $Workload $fixture
        if ($LASTEXITCODE -ne 0) { throw "The ClickHouse benchmark failed." }
        $probe | Set-Content -Encoding ascii (Join-Path $EvidenceDirectory "clickhouse-workload.json")
        $exportLock = Join-Path $EvidenceDirectory "$project.lock"
        try {
            $publications = python -m deploy.reporting.clickhouse.close load $fixture --lock $exportLock
            if ($LASTEXITCODE -ne 0) { throw "The ClickHouse replay receipt failed." }
            $publications | Set-Content -Encoding ascii (Join-Path $EvidenceDirectory "clickhouse-publications.json")
        } finally {
            if (Test-Path -LiteralPath $exportLock) { Remove-Item -LiteralPath $exportLock }
        }
        foreach ($desk in @("oil", "gas")) {
            $rows = python -m deploy.reporting.clickhouse.close dump $desk --business-date 2026-11-03
            if ($LASTEXITCODE -ne 0) { throw "The ClickHouse export failed." }
            $rows | Set-Content -Encoding ascii (Join-Path $EvidenceDirectory "clickhouse-$desk.json")
        }
    }
    if ($Dashboard) {
        $runDirectory = Join-Path ([System.IO.Path]::GetTempPath()) $project
        New-Item -ItemType Directory -Path $runDirectory | Out-Null
        $clickhouseBundle = Join-Path $runDirectory "clickhouse.zip"
        python -m deploy.reporting.clickhouse.rebind $ReferenceBundle $clickhouseBundle
        if ($LASTEXITCODE -ne 0) { throw "The ClickHouse dashboard rebind failed." }
        $env:DURAI_CH_SUPERSET_SECRET_KEY = [guid]::NewGuid().ToString("N")
        $env:DURAI_CH_METADATA_ADMIN_PASSWORD = [guid]::NewGuid().ToString("N")
        $env:DURAI_CH_METADATA_PASSWORD = [guid]::NewGuid().ToString("N")
        $env:SUPERSET_ADMIN_PASSWORD = [guid]::NewGuid().ToString("N")
        $env:SUPERSET_TEST_PASSWORD = [guid]::NewGuid().ToString("N")
        Invoke-Compose --profile dashboard up -d --build superset
        Invoke-Compose --profile dashboard exec -T superset superset db upgrade
        Invoke-Compose --profile dashboard exec -T superset superset init
        Invoke-Compose --profile dashboard cp (Join-Path $DashboardReference "dashboard_bundle.py") "superset:/tmp/dashboard_bundle.py"
        Invoke-Compose --profile dashboard cp (Join-Path $DashboardReference "configure_superset.py") "superset:/tmp/configure_superset.py"
        Invoke-Compose --profile dashboard cp (Join-Path $PSScriptRoot "configure_dashboard.py") "superset:/tmp/configure_dashboard.py"
        Invoke-Compose --profile dashboard cp $clickhouseBundle "superset:/tmp/clickhouse.zip"
        Invoke-Compose --profile dashboard exec -T -e DURAI_CH_OIL_PASSWORD -e DURAI_CH_GAS_PASSWORD -e SUPERSET_ADMIN_PASSWORD -e SUPERSET_TEST_PASSWORD superset /app/.venv/bin/python /tmp/configure_dashboard.py /tmp/clickhouse.zip
        Wait-Endpoint "http://127.0.0.1:58061/health"
        $env:DURAI_CH_DASHBOARD = "1"
        python -m unittest -v test_reporting_clickhouse_dashboard
        if ($LASTEXITCODE -ne 0) { throw "The ClickHouse dashboard tests failed." }
        Invoke-Compose --profile dashboard exec -T superset /app/.venv/bin/python /tmp/configure_superset.py --oil-reader-role unscoped
        $env:DURAI_CH_ROLE_REMOVED = "1"
        python -m unittest -v test_reporting_clickhouse_dashboard.ClickHouseDashboardTests.test_removed_desk_role_refuses_prior_chart_and_export
        if ($LASTEXITCODE -ne 0) { throw "The ClickHouse dashboard role removal test failed." }
    }
} finally {
    Pop-Location
    if ($runDirectory) {
        $archive = Join-Path $runDirectory "clickhouse.zip"
        if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive }
        Remove-Item -LiteralPath $runDirectory
    }
    Invoke-Compose --profile dashboard down -v
}
