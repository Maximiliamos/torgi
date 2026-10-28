[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$GitFlicUrl,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
if ($GitFlicUrl -notmatch '^https://gitflic\.ru/(?:[A-Za-z0-9_.-]+/){1,3}[A-Za-z0-9_.-]+(?:\.git)?) {
    throw 'GitFlicUrl must be an HTTPS project git URL on gitflic.ru'
}
$root = git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or -not $root) { throw 'Run from a Git working tree' }
$currentBranch = git rev-parse --verify main
if ($LASTEXITCODE -ne 0) { throw 'Local main branch is missing; no backup attempted' }

$existing = git remote get-url gitflic 2>$null
if ($LASTEXITCODE -eq 0 -and $existing -ne $GitFlicUrl) {
    throw "Existing gitflic remote points elsewhere; refusing to overwrite it"
}

if (-not $Apply) {
    [pscustomobject]@{
        mode = 'preview'
        repository = $root.Trim()
        target = $GitFlicUrl
        next_action = 'Create the GitFlic repository first, then rerun with -Apply'
        remote_exists = [bool]$existing
        touched_local_git = $false
        pushed = $false
    } | ConvertTo-Json
    exit 0
}

if (-not $existing) {
    git remote add gitflic $GitFlicUrl
    if ($LASTEXITCODE -ne 0) { throw 'Could not add gitflic remote' }
}
git push --dry-run gitflic main
if ($LASTEXITCODE -ne 0) { throw 'Dry-run failed: check GitFlic repository and credentials' }
git push gitflic main
if ($LASTEXITCODE -ne 0) { throw 'GitFlic main push failed' }
git push gitflic --tags
if ($LASTEXITCODE -ne 0) { throw 'Main was pushed, but tag backup failed' }

[pscustomobject]@{
    mode = 'applied'
    target = $GitFlicUrl
    main_pushed = $true
    tags_pushed = $true
    note = 'Only Git commits and tags are mirrored, not GitHub Issues/Actions/PRs'
} | ConvertTo-Json
) {
    throw 'GitFlicUrl must be an HTTPS project git URL on gitflic.ru'
}
$root = git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or -not $root) { throw 'Run from a Git working tree' }
$currentBranch = git rev-parse --verify main
if ($LASTEXITCODE -ne 0) { throw 'Local main branch is missing; no backup attempted' }

$existing = git remote get-url gitflic 2>$null
if ($LASTEXITCODE -eq 0 -and $existing -ne $GitFlicUrl) {
    throw "Existing gitflic remote points elsewhere; refusing to overwrite it"
}

if (-not $Apply) {
    [pscustomobject]@{
        mode = 'preview'
        repository = $root.Trim()
        target = $GitFlicUrl
        next_action = 'Create the GitFlic repository first, then rerun with -Apply'
        remote_exists = [bool]$existing
        touched_local_git = $false
        pushed = $false
    } | ConvertTo-Json
    exit 0
}

if (-not $existing) {
    git remote add gitflic $GitFlicUrl
    if ($LASTEXITCODE -ne 0) { throw 'Could not add gitflic remote' }
}
git push --dry-run gitflic main
if ($LASTEXITCODE -ne 0) { throw 'Dry-run failed: check GitFlic repository and credentials' }
git push gitflic main
if ($LASTEXITCODE -ne 0) { throw 'GitFlic main push failed' }
git push gitflic --tags
if ($LASTEXITCODE -ne 0) { throw 'Main was pushed, but tag backup failed' }

[pscustomobject]@{
    mode = 'applied'
    target = $GitFlicUrl
    main_pushed = $true
    tags_pushed = $true
    note = 'Only Git commits and tags are mirrored, not GitHub Issues/Actions/PRs'
} | ConvertTo-Json
