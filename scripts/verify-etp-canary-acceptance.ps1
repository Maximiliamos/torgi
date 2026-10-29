# Fail closed unless a manual ETP canary runs on the exact currently accepted main SHA.
# This is deliberately read-only; it neither changes PostgreSQL nor triggers deploys.
param(
    [Parameter(Mandatory=$true)]
    [string]$ExpectedSha
)
$ErrorActionPreference = 'Stop'
if ($ExpectedSha -cnotmatch '^[0-9a-f]{40}$') {
    throw 'ETP canary refused: expected SHA is not a full commit identifier'
}
if ($env:GITHUB_REF -ne 'refs/heads/main' -or $env:GITHUB_SHA -ne $ExpectedSha) {
    throw 'ETP canary refused: workflow must use its exact main-branch commit'
}
if (-not $env:GITHUB_REPOSITORY -or -not $env:GH_TOKEN) {
    throw 'ETP canary refused: authenticated GitHub acceptance evidence unavailable'
}
$headers = @{
    Authorization = "Bearer $env:GH_TOKEN"
    Accept = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
}
$api = "https://api.github.com/repos/$env:GITHUB_REPOSITORY"
$head = Invoke-RestMethod -Uri "$api/branches/main" -Headers $headers -TimeoutSec 20
if ([string]$head.commit.sha -ne $ExpectedSha) {
    throw 'ETP canary refused: GitHub main has advanced since dispatch'
}
$runs = Invoke-RestMethod -Uri "$api/actions/runs?head_sha=$ExpectedSha&per_page=100" -Headers $headers -TimeoutSec 20
$required = @(
    'Production reliability',
    'Deploy home secondary origin',
    'Deploy WEB and API to REG.RU',
    'Deploy Cloudflare edge proxy',
    'P1 production data quality',
    'P11 production acceptance',
    'Public WEB smoke',
    'Production functional reliability'
)
foreach ($name in $required) {
    $latest = @($runs.workflow_runs | Where-Object {
        $_.name -eq $name -and $_.head_sha -eq $ExpectedSha
    } | Sort-Object created_at -Descending | Select-Object -First 1)
    if ($latest.Count -ne 1 -or $latest[0].status -ne 'completed' -or
        $latest[0].conclusion -ne 'success') {
        throw "ETP canary refused: $name has no successful latest exact-SHA run"
    }
}
$worker = 'bankrotai-home-ingestion-worker'
$image = (docker inspect --format '{{.Config.Image}}' $worker | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or -not $image -or
    ([string]$image).Trim() -cne "bankrotai-home-api:$ExpectedSha") {
    throw 'ETP canary refused: ingestion-worker image is not the accepted SHA'
}
$running = (docker inspect --format '{{.State.Running}}' $worker | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or ([string]$running).Trim() -ne 'true') {
    throw 'ETP canary refused: accepted ingestion-worker is not running'
}
Write-Output "ETP canary accepted exact deployment revision $ExpectedSha; no DB writes performed"
