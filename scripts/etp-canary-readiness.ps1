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
    ready_for_source_scoped_canary = $false
    eligible_source_ids = @()
    blocked_source_ids = @()
}
$failures = @()
try {
    $report.restore_verified_backup_available = Test-RecentRestoreVerifiedBackup
    if ($report.restore_verified_backup_available -ne $true) {
        $failures += 'no_recent_restore_verified_backup'
    } else {
        # These calls use etp_manual_import WITHOUT --apply. The module reads
        # at most two verified details per source and runs rollback-only SELECTs
        # to catch existing ID/cadastral collisions before operator dispatch.
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
                $output = @(& docker.exe exec bankrotai-home-ingestion-worker python -m bankrotai.services.etp_manual_import --source $source --limit 2 --audit-collisions 2>$null)
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
                $review = $candidate.preimport_collision_review
                if ($null -eq $review -or
                    $review.audit -ne 'etp_preimport_collision_review' -or
                    $review.source -ne $source -or
                    $review.read_only -ne $true -or
                    $review.database_writes -ne 0 -or
                    $review.map_publication_verified -ne $false -or
                    $review.race_free_persistence_guaranteed -ne $false -or
                    $review.revalidate_in_atomic_import -ne $true -or
                    $review.details_reviewed -ne $candidate.verified_detail_count -or
                    $review.blocked_candidate_count -ne 0 -or
                    $review.new_unconflicted_candidate_count -ne $candidate.verified_detail_count -or
                    $review.ready_for_separate_operator_canary -ne $true) {
                    throw 'Verified ETP details have existing ID/cadastral conflicts or incomplete SQL evidence'
                }
                $entry.status = 'details_and_collisions_prechecked_manual_import_still_required'
                $entry.verified_detail_count = [int]$candidate.verified_detail_count
                $entry.existing_cadastral_collision_checked = $true
            } catch {
                $entry.status = 'blocked_source_details_unverified'
                $failures += ('source_details_not_verified:' + $source)
            }
            $report.source_details += $entry
        }
    }
} finally {
    # Each source remains independently eligible. A blocked CDTRF detail
    # must not suppress a verified M-ETS canary (or vice versa).
    $eligible = @($report.source_details | Where-Object {
        $_.status -eq 'details_and_collisions_prechecked_manual_import_still_required' -and
        $_.verified_detail_count -ge 1 -and $_.verified_detail_count -le 2 -and
        $_.existing_cadastral_collision_checked -eq $true
    } | ForEach-Object { [string]$_.source })
    $blocked = @($report.source_details | Where-Object {
        $_.status -ne 'details_and_collisions_prechecked_manual_import_still_required'
    } | ForEach-Object { [string]$_.source })
    $report['eligible_source_ids'] = $eligible
    $report['blocked_source_ids'] = $blocked
    $report['ready_for_source_scoped_canary'] = (
        $report.restore_verified_backup_available -eq $true -and
        $eligible.Count -ge 1 -and $report.source_details.Count -eq 2
    )
    $report['blocking_reasons'] = @($failures)
    $report['all_source_details_preflight_passed'] = (
        $report.source_details.Count -eq 2 -and
        $blocked.Count -eq 0 -and $failures.Count -eq 0
    )
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputFile -Encoding utf8
}

if ($report.ready_for_source_scoped_canary -ne $true) {
    throw ('ETP S5 has no individually verified source: ' + ($failures -join ', '))
}
if ($blocked.Count -gt 0) {
    Write-Warning ('ETP S5 source-scoped only; blocked sources remain disallowed: ' + ($blocked -join ', '))
}
Write-Output ('ETP S5 read-only verified source(s): ' + ($eligible -join ', ') + '; source-wide sync remains disabled')
