# Operator-controlled full-cycle launcher; never run from CI or a PR merge.
# Dry-run by default. Requires an exact deployed main SHA and manual opt-in.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ExpectedSha,
    [switch]$Execute,
    [switch]$Watch,
    [ValidateRange(0,60)][int]$P11SoakMinutes = 30,
    [ValidateRange(10,120)][int]$PollSeconds = 20,
    [ValidateRange(60,300)][int]$AcceptanceTimeoutMinutes = 180
)

$ErrorActionPreference = 'Stop'
$repo = 'Maximiliamos/torgi'
$fullWorkflow = 'phase3-lite-full-reconcile.yml'
$acceptedWorkflows = @('p1-data-quality.yml', 'p11-production-acceptance.yml')

function Invoke-GhText {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)
    $lines = @(& gh @Arguments 2>&1)
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "GitHub CLI command failed (exit $code). Check authentication and workflow permissions."
    }
    return ($lines -join "`n").Trim()
}

function Get-CurrentMain {
    return (Invoke-GhText @('api', "repos/$repo/branches/main", '--jq', '.commit.sha'))
}

function Assert-ExactMain {
    $current = Get-CurrentMain
    if ($current -cne $ExpectedSha) {
        throw "Expected SHA does not match current main: expected=$ExpectedSha actual=$current"
    }
}

function Get-WorkflowRuns {
    param([Parameter(Mandatory=$true)][string]$Workflow)
    $body = Invoke-GhText @(
        'run', 'list', '--repo', $repo,
        '--workflow', $Workflow, '--limit', '50',
        '--json', 'databaseId,headSha,status,conclusion,createdAt,event'
    )
    return @($body | ConvertFrom-Json)
}

function Assert-NoActiveRun {
    param([string]$Workflow)
    $busy = @(Get-WorkflowRuns $Workflow | Where-Object {
        $_.status -in @('queued', 'in_progress', 'waiting', 'pending', 'requested')
    })
    if ($busy.Count -gt 0) {
        throw "Refusing overlapping GitHub workflow $Workflow; active run count=$($busy.Count)"
    }
}

function Start-TrackedWorkflow {
    param([string]$Workflow, [string[]]$ExtraArgs = @())
    Assert-ExactMain
    Assert-NoActiveRun $Workflow
    $priorIds = @(Get-WorkflowRuns $Workflow | ForEach-Object { [string]$_.databaseId })
    Write-Host "Dispatching $Workflow on exact main $ExpectedSha"
    $arguments = @('workflow', 'run', $Workflow, '--repo', $repo, '--ref', 'main') + $ExtraArgs
    [void](Invoke-GhText $arguments)

    # GitHub dispatch is asynchronous. Only accept a *new* uniquely identified
    # workflow_dispatch run on the original exact SHA.
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(4)
    do {
        $new = @(Get-WorkflowRuns $Workflow | Where-Object {
            $_.headSha -eq $ExpectedSha -and
            $_.event -eq 'workflow_dispatch' -and
            [string]$_.databaseId -notin $priorIds
        })
        if ($new.Count -gt 1) {
            throw "Ambiguous concurrent dispatches for $Workflow; no run adopted"
        }
        if ($new.Count -eq 1) { return [long]$new[0].databaseId }
        Start-Sleep -Seconds 5
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Could not identify a new exact-SHA dispatch for $Workflow"
}

function Wait-ForAcceptance {
    param([long]$RunId, [string]$Workflow)
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($AcceptanceTimeoutMinutes)
    do {
        $run = Invoke-GhText @(
            'run', 'view', [string]$RunId, '--repo', $repo,
            '--json', 'headSha,status,conclusion'
        ) | ConvertFrom-Json
        if ($run.headSha -cne $ExpectedSha) {
            throw "Acceptance SHA mismatch for $Workflow run $RunId"
        }
        if ($run.status -eq 'completed') {
            if ($run.conclusion -ne 'success') {
                throw "Acceptance $Workflow run $RunId ended $($run.conclusion); refusing full ingestion"
            }
            Write-Host "ACCEPTED: $Workflow run $RunId"
            return
        }
        Start-Sleep -Seconds $PollSeconds
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Acceptance $Workflow run $RunId timed out; refusing full ingestion"
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required; authenticate with gh auth login.'
}
Assert-ExactMain
Write-Host "Current main SHA verified: $ExpectedSha"
if (-not $Execute) {
    Write-Host 'DRY RUN ONLY; no workflows were dispatched.'
    Write-Host 'After independent review and deployment, pass -Execute to run P1, P11, then Phase 3.'
    exit 0
}

# P1/P11 must be newly and independently run on this exact deployed SHA.
# A previously skipped, cancelled, or stale run is never accepted.
$p1Id = Start-TrackedWorkflow -Workflow $acceptedWorkflows[0]
Wait-ForAcceptance -RunId $p1Id -Workflow $acceptedWorkflows[0]
$p11Id = Start-TrackedWorkflow -Workflow $acceptedWorkflows[1] -ExtraArgs @(
    '--field', "soak_minutes=$P11SoakMinutes", '--field', 'restart_drill=false'
)
Wait-ForAcceptance -RunId $p11Id -Workflow $acceptedWorkflows[1]

Assert-ExactMain
$fullId = Start-TrackedWorkflow -Workflow $fullWorkflow
Write-Host "Full seven-source run dispatched on $ExpectedSha"
Write-Host "Track: https://github.com/$repo/actions/runs/$fullId"
Write-Host 'The production workflow must still pass independent backup/restore, source, lease and S3 capacity gates.'
if ($Watch) {
    & gh run watch $fullId --repo $repo --exit-status
    if ($LASTEXITCODE -ne 0) { throw "Full reconciliation GitHub run $fullId failed" }
}
