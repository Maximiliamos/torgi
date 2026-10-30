# Runs the guarded ETP-to-map campaign after the operator disables a VPN that
# prevents the Home production runner from reaching Russian ETP/GEO services.
# The script never writes to PostgreSQL itself: all mutations remain inside the
# exact-SHA, backup-verified, collision-checked GitHub Actions campaign.
[CmdletBinding()]
param(
    [string]$ExpectedSha,
    [ValidateRange(1, 10)][int]$P11Attempts = 5,
    [ValidateRange(30, 600)][int]$RetryDelaySeconds = 90,
    [ValidateRange(5, 180)][int]$ChainTimeoutMinutes = 45,
    [string]$LogDirectory = 'D:\BankrotAI\etp-operator-logs'
)

$ErrorActionPreference = 'Stop'
$runStamp = [datetime]::Now.ToString('yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$logPath = Join-Path $LogDirectory "etp-off-vpn-map-$runStamp.log"
Start-Transcript -LiteralPath $logPath -Force | Out-Null
Write-Host "Локальный журнал: $logPath"
trap {
    Write-Host ''
    Write-Host ("ОШИБКА: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host "Полный локальный журнал сохранён: $logPath" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch { }
    throw
}

$repository = 'Maximiliamos/torgi'
$allSourceQaWorkflow = 'etp-manual-qa-all-sources.yml'
$p11Workflow = 'p11-production-acceptance.yml'
$s5Workflow = 'etp-s5-canary-readiness.yml'
$campaignWorkflow = 'etp-verified-canary-geo-map.yml'

function Invoke-GhJson {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)
    $output = @(& gh.exe @Arguments)
    if ($LASTEXITCODE -ne 0 -or $output.Count -lt 1) {
        throw "GitHub CLI command failed: gh $($Arguments -join ' ')"
    }
    return ($output -join "`n" | ConvertFrom-Json)
}

function Get-WorkflowRuns {
    param([Parameter(Mandatory=$true)][string]$Workflow)
    return @(Invoke-GhJson -Arguments @(
        'run', 'list', '--repo', $repository, '--workflow', $Workflow,
        '--limit', '20', '--json', 'databaseId,status,conclusion,headSha,createdAt,url,event'
    ))
}

function Wait-ForRunCreatedAfter {
    param(
        [Parameter(Mandatory=$true)][string]$Workflow,
        [Parameter(Mandatory=$true)][datetime]$NotBeforeUtc,
        [Parameter(Mandatory=$true)][string]$Sha,
        [Parameter(Mandatory=$true)][datetime]$DeadlineUtc
    )
    while ([datetime]::UtcNow -lt $DeadlineUtc) {
        $run = @(Get-WorkflowRuns -Workflow $Workflow | Where-Object {
            $_.headSha -eq $Sha -and
            ([datetime]$_.createdAt).ToUniversalTime() -ge $NotBeforeUtc.AddSeconds(-5)
        } | Sort-Object { [datetime]$_.createdAt } -Descending | Select-Object -First 1)
        if ($run.Count -eq 1) { return $run[0] }
        Start-Sleep -Seconds 10
    }
    throw "Timed out waiting for workflow $Workflow on exact SHA $Sha"
}

function Watch-RunProgress {
    param(
        [Parameter(Mandatory=$true)][long]$RunId,
        [Parameter(Mandatory=$true)][string]$Label,
        [ValidateRange(0, 100)][int]$BasePercent,
        [ValidateRange(1, 100)][int]$SpanPercent
    )
    $started = [datetime]::UtcNow
    $lastShownPercent = -1
    $lastHeartbeat = [datetime]::MinValue
    while ($true) {
        $run = Invoke-GhJson -Arguments @(
            'run', 'view', [string]$RunId, '--repo', $repository,
            '--json', 'status,conclusion,url,jobs'
        )
        $steps = @($run.jobs | ForEach-Object { @($_.steps) })
        $totalSteps = $steps.Count
        $completedSteps = @($steps | Where-Object { $_.status -eq 'completed' }).Count
        $fraction = if ($totalSteps -gt 0) { $completedSteps / $totalSteps } else { 0.0 }
        if ($run.status -eq 'completed' -and $run.conclusion -eq 'success') { $fraction = 1.0 }
        $percent = [math]::Min(100, [math]::Floor($BasePercent + $fraction * $SpanPercent))
        $active = @($steps | Where-Object { $_.status -eq 'in_progress' } |
            Select-Object -First 1 -ExpandProperty name)
        $detail = if ($active.Count -eq 1) { [string]$active[0] } elseif ($run.status -eq 'queued') {
            'ожидание свободного runner'
        } else { 'проверка состояния' }
        $elapsed = [math]::Floor(([datetime]::UtcNow - $started).TotalSeconds)
        if ($percent -ne $lastShownPercent -or
            ([datetime]::UtcNow - $lastHeartbeat).TotalSeconds -ge 30 -or
            $run.status -eq 'completed') {
            Write-Host ("[{0,3}%] {1}: {2}; прошло {3} сек." -f $percent,$Label,$detail,$elapsed)
            $lastShownPercent = $percent
            $lastHeartbeat = [datetime]::UtcNow
        }
        if ($run.status -eq 'completed') {
            if ($run.conclusion -eq 'success') { return 0 }
            Write-Warning "$Label завершён со статусом $($run.conclusion): $($run.url)"
            return 1
        }
        Start-Sleep -Seconds 10
    }
}

if (-not (Get-Command gh.exe -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh.exe) is required'
}
if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI (docker.exe) is required'
}

& gh.exe auth status *> $null
if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI is not authenticated' }

$main = Invoke-GhJson -Arguments @('api', "repos/$repository/branches/main")
$mainSha = [string]$main.commit.sha
if (-not $ExpectedSha) { $ExpectedSha = $mainSha }
if ($ExpectedSha -cnotmatch '^[0-9a-f]{40}$') {
    throw 'ExpectedSha must be a full lowercase commit SHA'
}
if ($mainSha -cne $ExpectedSha) {
    throw "Refusing stale campaign: main is $mainSha, requested $ExpectedSha"
}

$requiredWorkers = @(
    'bankrotai-home-ingestion-worker',
    'bankrotai-home-geocoding-worker',
    'bankrotai-home-map-worker'
)
foreach ($worker in $requiredWorkers) {
    $running = (& docker.exe inspect --format '{{.State.Running}}' $worker | Select-Object -Last 1).Trim()
    $image = (& docker.exe inspect --format '{{.Config.Image}}' $worker | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or $running -ne 'true') {
        throw "Required production worker is not running: $worker"
    }
    if ($image -cne "bankrotai-home-api:$ExpectedSha") {
        throw "Worker $worker is not running exact main image $ExpectedSha (found $image)"
    }
}

Write-Host "Exact production revision confirmed: $ExpectedSha"
Write-Host 'Running all 21 new ETP parsers in isolated read-only QA...'
$qaDispatchUtc = [datetime]::UtcNow
& gh.exe workflow run $allSourceQaWorkflow --repo $repository --ref main
if ($LASTEXITCODE -ne 0) { throw 'Unable to dispatch all-source ETP QA' }
$qa = Wait-ForRunCreatedAfter -Workflow $allSourceQaWorkflow -NotBeforeUtc $qaDispatchUtc `
    -Sha $ExpectedSha -DeadlineUtc ([datetime]::UtcNow.AddMinutes(3))
Write-Host "All-source parser run: $($qa.url)"
if ((Watch-RunProgress -RunId ([long]$qa.databaseId) -Label 'Парсинг 21 ЭТП' `
    -BasePercent 0 -SpanPercent 35) -ne 0) {
    throw 'The isolated 21-source parser run failed; no production import was attempted.'
}

Write-Host 'Waiting for production health to recover without VPN...'

$successfulP11 = $null
for ($attempt = 1; $attempt -le $P11Attempts; $attempt++) {
    $dispatchUtc = [datetime]::UtcNow
    $dispatchOutput = @(& gh.exe workflow run $p11Workflow --repo $repository --ref main `
        -f soak_minutes=0 -f restart_drill=false)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to dispatch P11 production acceptance' }

    $p11 = Wait-ForRunCreatedAfter -Workflow $p11Workflow -NotBeforeUtc $dispatchUtc `
        -Sha $ExpectedSha -DeadlineUtc ([datetime]::UtcNow.AddMinutes(3))
    Write-Host "P11 attempt $attempt/$P11Attempts`: $($p11.url)"
    $exitCode = Watch-RunProgress -RunId ([long]$p11.databaseId) `
        -Label "Production health, попытка $attempt" -BasePercent 35 -SpanPercent 20
    if ($exitCode -eq 0) {
        $successfulP11 = $p11
        break
    }
    if ($attempt -lt $P11Attempts) {
        Write-Warning "P11 is still degraded; retrying in $RetryDelaySeconds seconds"
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}
if ($null -eq $successfulP11) {
    throw 'P11 did not recover. Leave VPN disabled and inspect the last linked run before retrying.'
}

$deadline = [datetime]::UtcNow.AddMinutes($ChainTimeoutMinutes)
$p11Created = ([datetime]$successfulP11.createdAt).ToUniversalTime()
$s5 = Wait-ForRunCreatedAfter -Workflow $s5Workflow -NotBeforeUtc $p11Created `
    -Sha $ExpectedSha -DeadlineUtc $deadline
Write-Host "S5 exact-SHA readiness: $($s5.url)"
if ((Watch-RunProgress -RunId ([long]$s5.databaseId) -Label 'Проверка кандидатов и конфликтов' `
    -BasePercent 55 -SpanPercent 15) -ne 0) {
    throw 'S5 read-only source/collision readiness failed; no production import was authorized.'
}

$s5Created = ([datetime]$s5.createdAt).ToUniversalTime()
$campaign = Wait-ForRunCreatedAfter -Workflow $campaignWorkflow -NotBeforeUtc $s5Created `
    -Sha $ExpectedSha -DeadlineUtc $deadline
Write-Host "Verified import/GEO/map campaign: $($campaign.url)"
if ((Watch-RunProgress -RunId ([long]$campaign.databaseId) `
    -Label 'Импорт, геокодирование и публикация карты' -BasePercent 70 -SpanPercent 30) -ne 0) {
    throw 'Verified campaign failed closed. Inspect the linked matrix job; do not rerun with bypasses.'
}

Write-Host ''
Write-Host 'SUCCESS: verified ETP lots were imported, geocoded and published.' -ForegroundColor Green
Write-Host 'Open the map: https://sterdez.online/map'
Write-Host 'Public source checks:'
Write-Host '  https://api.sterdez.online/api/map/lots?sources=m-ets.ru&limit=10'
Write-Host '  https://api.sterdez.online/api/map/lots?sources=cdtrf.ru&limit=10'
Write-Host "Полный локальный журнал: $logPath"
Stop-Transcript | Out-Null
