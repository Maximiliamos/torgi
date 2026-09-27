[CmdletBinding()]
param(
    [ValidateRange(1, 50)]
    [int]$MinimumFreePercent = 10,
    [ValidateRange(1, 30)]
    [int]$DrRetainDays = 7,
    [ValidateRange(1, 10)]
    [int]$PreMigrationRetainCount = 2,
    [string]$DrBackupDirectory = 'C:\ProgramData\BankrotAI\dr-backups',
    [string]$PreMigrationDirectory = 'C:\ProgramData\BankrotAI\db-backups'
)

$ErrorActionPreference = 'Stop'

function Get-FreePercent {
    $disk = Get-PSDrive -Name C
    $total = [double]$disk.Used + [double]$disk.Free
    if ($total -le 0) { return 0.0 }
    return [math]::Round(([double]$disk.Free / $total) * 100, 1)
}

function Remove-ExpiredDrBackups {
    param([string]$Directory, [int]$RetainDays)
    if (-not (Test-Path -LiteralPath $Directory)) { return 0 }
    $cutoff = (Get-Date).AddDays(-$RetainDays)
    $removed = 0
    Get-ChildItem -LiteralPath $Directory -File -Filter 'bankrotai-*' |
        Where-Object LastWriteTime -lt $cutoff |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force
            $removed++
        }
    return $removed
}

function Trim-PreMigrationBackups {
    param([string]$Directory, [int]$RetainCount)
    if (-not (Test-Path -LiteralPath $Directory)) { return 0 }
    $removed = 0
    Get-ChildItem -LiteralPath $Directory -File -Filter 'pre-migration-*.dump' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $RetainCount |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force
            $removed++
        }
    return $removed
}

$before = Get-FreePercent
Write-Output "Home disk cleanup: C: free before=$before%"
docker system df

# Build cache and images not referenced by any container are reproducible.
# Never prune volumes: PostgreSQL/Redis persistent data live in named volumes.
docker builder prune --all --force
if ($LASTEXITCODE -ne 0) { throw 'Docker builder cache cleanup failed' }

docker image prune --all --force
if ($LASTEXITCODE -ne 0) { throw 'Unused Docker image cleanup failed' }

$removedPreMigration = Trim-PreMigrationBackups -Directory $PreMigrationDirectory -RetainCount $PreMigrationRetainCount
$removedDr = Remove-ExpiredDrBackups -Directory $DrBackupDirectory -RetainDays $DrRetainDays

$after = Get-FreePercent
Write-Output "Removed pre-migration dumps: $removedPreMigration"
Write-Output "Removed expired DR backup files: $removedDr"
Write-Output "Home disk cleanup: C: free after=$after%"
docker system df

if ($after -lt $MinimumFreePercent) {
    throw "C: remains below safe deployment threshold after bounded cleanup: $after% < $MinimumFreePercent%. Volumes and recent backups were intentionally preserved."
}
