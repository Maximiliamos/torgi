# Read-only 8 Nov post-run forensic inspection from a pinned Git commit.
# Usage: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-readonly-postrun-audit-home.ps1
[CmdletBinding()]
param(
    [string]$AuditCommit = 'aedc1e26ad3e1e1be1d052bf1badfcceadc7fa8b',
    [string]$RunId = 'f572bdff-3db1-41bf-94a4-1f75ab690232',
    [string]$Container = 'bankrotai-home-ingestion-worker',
    [switch]$SkipPublicS3
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($AuditCommit -notmatch '^[a-f0-9]{40}$') { throw 'AuditCommit must be exact 40-char Git SHA' }
if ($RunId -notmatch '^[a-f0-9-]{36}$') { throw 'Invalid run id' }
$image = @(& docker.exe inspect --format '{{.Config.Image}}' $Container)
if ($LASTEXITCODE -ne 0 -or !$image.Count) { throw 'Cannot inspect Home ingestion worker' }
$running = @(& docker.exe inspect --format '{{.State.Running}}' $Container)
if ($LASTEXITCODE -ne 0 -or !$running.Count -or $running[-1].Trim() -ne 'true') {
    throw 'Home ingestion worker is not running'
}
Write-Host ('Read-only audit against running worker image: ' + $image[-1]) -ForegroundColor Cyan
$dir = 'D:\BankrotAI\etp-operator-logs'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$source = Join-Path $dir 'audit-20261108-source.py'
$destination = Join-Path $dir ('audit-20261108-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
$uri = "https://raw.githubusercontent.com/Maximiliamos/torgi/$AuditCommit/scripts/audit_20261108_nationwide_readonly.py"
Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $source
if (-not (Test-Path -LiteralPath $source) -or (Get-Item -LiteralPath $source).Length -lt 1024) {
    throw 'Audit script not retrieved from pinned commit'
}
$argsPy = @('exec', '-i', $Container, 'python', '-', '--run-id', $RunId)
if (-not $SkipPublicS3) { $argsPy += '--check-public-s3' }
Write-Host 'Executing SELECT-only DB audit and optional public S3 GET' -ForegroundColor Cyan
$output = @(Get-Content -LiteralPath $source -Raw -Encoding UTF8 | & docker.exe @argsPy 2>&1)
$exit = $LASTEXITCODE
if ($exit -ne 0) {
    $output | ForEach-Object { Write-Host $_ }
    throw "Read-only audit failed with code $exit; no writes performed"
}
$json = $output -join [Environment]::NewLine
try {
    $report = $json | ConvertFrom-Json
} catch {
    throw 'Read-only report was not valid JSON, no production write was attempted'
}
if ($report.run.id -ne $RunId -or @($report.source_runs).Count -ne 7) {
    throw 'Unexpected run identity or source coverage'
}
[System.IO.File]::WriteAllText($destination, $json, [System.Text.UTF8Encoding]::new($false))
Write-Host "Report saved: $destination" -ForegroundColor Green
Write-Host ('Status: ' + $report.run.status + '; map: ' + $report.map_dataset_current.version)
Write-Host ('Public S3 manifest: ' + $report.public_s3_manifest.status)
Write-Host ('Archive totals (source counters): ' + $report.totals.items_archived)
Write-Host ('Source window archives and bounded samples:')
$report.archive_time_window | Select-Object source,run_reported_archived,currently_archived_in_run_time_window,window_matches_run_counter | Format-Table -AutoSize
Write-Output $destination
