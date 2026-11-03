# Runs the exact-SHA production import for every reviewed ETP, then GEO and map.
# Run only after fully disabling the VPN/proxy that blocks Russian providers.
[CmdletBinding()]
param(
    [string]$ExpectedSha,
    [string]$LogDirectory = 'D:\BankrotAI\etp-operator-logs',
    [switch]$ResumeAfterImport
)

$ErrorActionPreference = 'Stop'
$repository = 'Maximiliamos/torgi'
$p1Workflow = 'p1-data-quality.yml'
$campaignWorkflow = 'etp-all-new-sources-import-geo-map.yml'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$logPath = Join-Path $LogDirectory "all-etp-no-vpn-$stamp.log"
Start-Transcript -LiteralPath $logPath -Force | Out-Null

function Invoke-GhJson {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)
    $lines = @(& gh.exe @Arguments)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -lt 1) {
        throw "GitHub CLI failed: gh $($Arguments -join ' ')"
    }
    return ($lines -join "`n" | ConvertFrom-Json)
}

function Find-NewRun {
    param(
        [Parameter(Mandatory=$true)][string]$Workflow,
        [Parameter(Mandatory=$true)][datetime]$StartedUtc,
        [Parameter(Mandatory=$true)][string]$Sha
    )
    for ($attempt = 1; $attempt -le 24; $attempt++) {
        $runs = @(Invoke-GhJson -Arguments @(
            'run','list','--repo',$repository,'--workflow',$Workflow,
            '--event','workflow_dispatch','--limit','20',
            '--json','databaseId,status,conclusion,headSha,createdAt,url'
        ))
        $run = @($runs | Where-Object {
            $_.headSha -eq $Sha -and
            ([datetime]$_.createdAt).ToUniversalTime() -ge $StartedUtc.AddSeconds(-5)
        } | Sort-Object { [datetime]$_.createdAt } -Descending | Select-Object -First 1)
        if ($run.Count -eq 1) { return $run[0] }
        Write-Host "[  1%] Waiting for GitHub to register $Workflow ($attempt/24)"
        Start-Sleep -Seconds 5
    }
    throw "Timed out waiting for $Workflow on $Sha"
}

function Get-Run {
    param([Parameter(Mandatory=$true)][long]$RunId)
    return Invoke-GhJson -Arguments @(
        'run','view',[string]$RunId,'--repo',$repository,
        '--json','status,conclusion,url,jobs'
    )
}

function Wait-P1 {
    param([Parameter(Mandatory=$true)][long]$RunId)
    $last = -1
    while ($true) {
        $run = Get-Run -RunId $RunId
        $steps = @($run.jobs | ForEach-Object { @($_.steps) })
        $done = @($steps | Where-Object status -eq 'completed').Count
        $fraction = if ($steps.Count) { $done / $steps.Count } else { 0 }
        $percent = [math]::Min(15, [math]::Floor(2 + 13 * $fraction))
        if ($percent -ne $last) {
            $active = @($steps | Where-Object status -eq 'in_progress' | Select-Object -First 1)
            $detail = if ($active.Count) { $active[0].name } else { $run.status }
            Write-Host ("[{0,3}%] P1: {1}" -f $percent,$detail)
            $last = $percent
        }
        if ($run.status -eq 'completed') {
            if ($run.conclusion -ne 'success') { throw "P1 failed: $($run.url)" }
            return
        }
        Start-Sleep -Seconds 10
    }
}

function Get-LocalImportProgress {
    $line = @(& docker.exe exec bankrotai-home-ingestion-worker `
        python -m bankrotai.services.etp_all_sources_campaign progress 2>$null)[-1]
    if ($LASTEXITCODE -ne 0 -or -not $line) { return $null }
    try { return ($line | ConvertFrom-Json) } catch { return $null }
}

function Wait-Campaign {
    param(
        [Parameter(Mandatory=$true)][long]$RunId,
        [string]$BaselineImportRunId
    )
    $lastText = ''
    $lastHeartbeat = [datetime]::MinValue
    while ($true) {
        $run = Get-Run -RunId $RunId
        $steps = @($run.jobs | ForEach-Object { @($_.steps) })
        $active = @($steps | Where-Object status -eq 'in_progress' | Select-Object -First 1)
        $activeName = if ($active.Count) { [string]$active[0].name } else { [string]$run.status }
        $percent = 15
        $detail = $activeName

        if ($activeName -like 'Sequential non-reconciling import*') {
            $progress = Get-LocalImportProgress
            if ($null -ne $progress -and [string]$progress.run_id -ne $BaselineImportRunId) {
                $sourcePercent = [int]$progress.percent
                $percent = 15 + [math]::Floor($sourcePercent * 0.65)
                $detail = "import $sourcePercent%; sources $($progress.completed)/$($progress.total); current $($progress.current_source) $($progress.current_source_percent)%"
            } else {
                $detail = 'import starting; waiting for first durable checkpoint'
            }
        } elseif ($activeName -like 'Geocode every*') {
            $percent = 85
        } elseif ($activeName -like 'Publish map*') {
            $percent = 95
        } elseif ($activeName -like 'Preserve aggregate*') {
            $percent = 99
        }

        if ($run.status -eq 'completed' -and $run.conclusion -eq 'success') {
            $percent = 100
            $detail = 'all imports, GEO and map publication completed'
        }
        $text = "[{0,3}%] {1}" -f $percent,$detail
        if ($text -ne $lastText -or ([datetime]::UtcNow - $lastHeartbeat).TotalSeconds -ge 30) {
            Write-Host $text
            $lastText = $text
            $lastHeartbeat = [datetime]::UtcNow
        }
        if ($run.status -eq 'completed') {
            if ($run.conclusion -ne 'success') { throw "All-ETP campaign failed: $($run.url)" }
            return
        }
        Start-Sleep -Seconds 10
    }
}

try {
    if (-not (Get-Command gh.exe -ErrorAction SilentlyContinue)) { throw 'gh.exe is required' }
    if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) { throw 'docker.exe is required' }
    & gh.exe auth status *> $null
    if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI is not authenticated' }

    $main = Invoke-GhJson -Arguments @('api',"repos/$repository/branches/main")
    $mainSha = [string]$main.commit.sha
    if (-not $ExpectedSha) { $ExpectedSha = $mainSha }
    if ($ExpectedSha -cnotmatch '^[0-9a-f]{40}$' -or $ExpectedSha -cne $mainSha) {
        throw "Expected exact current main SHA $mainSha, received $ExpectedSha"
    }

    foreach ($hostName in @('m-ets.ru','torgi.cdtrf.ru','etp-profit.ru','ausib.ru','nspd.gov.ru')) {
        $addresses = @(Resolve-DnsName -Name $hostName -Type A -ErrorAction Stop |
            Where-Object IPAddress | ForEach-Object { [string]$_.IPAddress })
        if (-not $addresses.Count) { throw "DNS returned no IPv4 for $hostName" }
        if (@($addresses | Where-Object { $_ -match '^198\.(18|19)\.' }).Count) {
            throw "VPN/Fake-IP DNS is active for $hostName. Fully disable the VPN and retry."
        }
    }

    foreach ($worker in @(
        'bankrotai-home-ingestion-worker','bankrotai-home-geocoding-worker','bankrotai-home-map-worker'
    )) {
        $image = @(& docker.exe inspect --format '{{.Config.Image}}' $worker)[-1].Trim()
        $running = @(& docker.exe inspect --format '{{.State.Running}}' $worker)[-1].Trim()
        if ($LASTEXITCODE -ne 0 -or $running -ne 'true' -or $image -ne "bankrotai-home-api:$ExpectedSha") {
            throw "Worker $worker is not running exact image bankrotai-home-api:$ExpectedSha"
        }
    }
    Write-Host "[  0%] Exact production SHA and no-VPN DNS confirmed: $ExpectedSha"
    Write-Host "Log: $logPath"

    $p1Started = [datetime]::UtcNow
    & gh.exe workflow run $p1Workflow --repo $repository --ref main
    if ($LASTEXITCODE -ne 0) { throw 'Could not dispatch P1' }
    $p1 = Find-NewRun -Workflow $p1Workflow -StartedUtc $p1Started -Sha $ExpectedSha
    Write-Host "P1: $($p1.url)"
    Wait-P1 -RunId ([long]$p1.databaseId)

    $baseline = if ($ResumeAfterImport) { $null } else { Get-LocalImportProgress }
    $baselineId = if ($null -ne $baseline) { [string]$baseline.run_id } else { '' }
    $campaignStarted = [datetime]::UtcNow
    $campaignArguments = @(
        'workflow','run',$campaignWorkflow,'--repo',$repository,'--ref','main',
        '-f',"accepted_sha=$ExpectedSha"
    )
    if ($ResumeAfterImport) {
        $campaignArguments += @('-f','resume_after_import=true')
        Write-Host '[ 15%] Reusing the persisted all-ETP import; starting GEO and map publication.'
    }
    & gh.exe @campaignArguments
    if ($LASTEXITCODE -ne 0) { throw 'Could not dispatch all-ETP campaign' }
    $campaign = Find-NewRun -Workflow $campaignWorkflow -StartedUtc $campaignStarted -Sha $ExpectedSha
    Write-Host "All-ETP campaign: $($campaign.url)"
    Wait-Campaign -RunId ([long]$campaign.databaseId) -BaselineImportRunId $baselineId

    $successText = if ($ResumeAfterImport) {
        'persisted all-ETP import geocoded and published'
    } else {
        'all reviewed ETPs imported, geocoded and published'
    }
    Write-Host "[100%] SUCCESS: $successText." -ForegroundColor Green
    Write-Host 'Map: https://sterdez.online/map'
    Write-Host "Full log: $logPath"
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Full log: $logPath" -ForegroundColor Yellow
    throw
} finally {
    try { Stop-Transcript | Out-Null } catch { }
}
