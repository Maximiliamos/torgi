# Read-only, exact-SHA preflight for an eventual one-source M-ETS/CDTRF canary.
# Never imports, archives, reconciles or updates Postgres; saves aggregate proof.
param(
    [Parameter(Mandatory=$true)][string]$ExpectedSha,
    [Parameter(Mandatory=$true)][string]$OutputFile
)
$ErrorActionPreference = 'Stop'
if ($ExpectedSha -cnotmatch '^[0-9a-f]{40}$') {
    throw 'ETP canary preflight requires a full exact SHA'
}
if (-not $OutputFile) { throw 'ETP canary preflight requires an output file' }

# The existing production acceptance verifier independently checks main,
# exact worker image and all eight P11/WEB/REG.RU/Cloudflare production gates.
# This command does not write any data.
& (Join-Path $PSScriptRoot 'verify-etp-canary-acceptance.ps1') -ExpectedSha $ExpectedSha
if ($LASTEXITCODE -ne 0) { throw 'ETP canary preflight acceptance failed' }

function Test-RecentRestoreVerifiedBackup {
    $root = 'D:\BankrotAI\dr-backups'
    if (-not (Test-Path -LiteralPath $root)) { return $false }
    $files = @(Get-ChildItem -LiteralPath $root -File -Filter 'bankrotai-*.json' |
        Sort-Object LastWriteTimeUtc -Descending)
    foreach ($file in $files) {
        try {
            $data = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
            if ($data.restore_verification -ne 'passed' -or
                $data.source_schema_revision -ne $data.restored_schema_revision) { continue }
            if (-not $data.backup_file -or -not (Test-Path -LiteralPath ([string]$data.backup_file))) {
                continue
            }
            $hours = ((Get-Date).ToUniversalTime() - $file.LastWriteTimeUtc).TotalHours
            if ($hours -lt 0 -or $hours -gt 60) { continue }
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath ([string]$data.backup_file)).Hash.ToLowerInvariant()
            if ($hash -ne ([string]$data.sha256).ToLowerInvariant()) { continue }
            return $true
        } catch {
            # A corrupt or stale manifest is not evidence of safe recovery.
            continue
        }
    }
    return $false
}

$report = [ordered]@{
    report = 'etp_exact_sha_manual_canary_readiness'
    evaluated_sha = $ExpectedSha
    observed_at_utc = [DateTime]::UtcNow.ToString('o')
    read_only = $true
    production_database_writes = 0
    archive_missing = $false
    reconcile_missing = $false
    import_executed = $false
    map_publication_verified = $false
    restore_verified_backup_available = $false
    source_details = @()
    manual_source_import_is_separate_action = $true
    ready_for_automatic_sync = $false
}
$failures = @()
try {
    $report.restore_verified_backup_available = Test-RecentRestoreVerifiedBackup
    if ($report.restore_verified_backup_available -ne $true) {
        $failures += 'no_recent_restore_verified_backup'
    } else {
        # These calls use etp_manual_import WITHOUT --apply. The module reads
        # at most two verified details per approved source, with no DB session.
        # Network 401/403/429 and invalid cards remain blockers; no bypass.
        foreach ($source in @('m-ets.ru', 'cdtrf.ru')) {
            $entry = [ordered]@{
                source = $source
                verified_detail_count = 0
                status = 'blocked'
                existing_cadastral_collision_checked = $false
                persisted_count = 0
            }
            try {
                $output = @(& docker.exe exec bankrotai-home-ingestion-worker python -m bankrotai.services.etp_manual_import --source $source --limit 2 2>$null)
                if ($LASTEXITCODE -ne 0 -or $output.Count -lt 1) {
                    throw 'Read-only detail collection failed'
                }
                $candidate = ($output | Select-Object -Last 1 | ConvertFrom-Json)
                if ($candidate.source -ne $source -or
                    $candidate.write_authorized -ne $false -or
                    $candidate.missing_lot_archival -ne $false -or
                    $candidate.complete_source_inventory -ne $false -or
                    $candidate.persisted_count -ne 0 -or
                    @($candidate.persisted_external_ids).Count -ne 0 -or
                    $candidate.verified_detail_count -lt 1 -or
                    $candidate.verified_detail_count -gt 2) {
                    throw 'Canary preflight evidence missing or unsafe'
                }
                $entry.status = 'details_verified_manual_import_still_required'
                $entry.verified_detail_count = [int]$candidate.verified_detail_count
            } catch {
                $entry.status = 'blocked_source_details_unverified'
                $failures += ('source_details_not_verified:' + $source)
            }
            $report.source_details += $entry
        }
    }
} finally {
    $report['blocking_reasons'] = @($failures)
    $report['all_source_details_preflight_passed'] = ($report.source_details.Count -eq 2 -and $failures.Count -eq 0)
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputFile -Encoding utf8
}

if ($failures.Count -gt 0) {
    throw ('ETP canary preflight not accepted: ' + ($failures -join ', '))
}
Write-Output 'Read-only ETP detail preflight passed; manual backup-gated import remains required'
