$ErrorActionPreference = 'Continue'
Write-Host '=== containers ==='
$names = @(
    'bankrotai-home-postgres', 'bankrotai-home-redis', 'bankrotai-photon',
    'bankrotai-home-secondary', 'bankrotai-home-ingestion-worker'
)
foreach ($name in $names) {
    docker inspect --format 'container={{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}' $name 2>&1
}
docker stats --no-stream --format 'container={{.Name}} cpu={{.CPUPerc}} memory={{.MemUsage}} pids={{.PIDs}}' $names 2>&1

Write-Host '=== local health ==='
foreach ($path in @('/health/live', '/health/ready')) {
    $status = & curl.exe -sS --connect-timeout 2 --max-time 15 -o NUL -w '%{http_code} duration=%{time_total}' "http://127.0.0.1:18000$path" 2>$null
    Write-Host "path=$path status=$status curlExit=$LASTEXITCODE"
}

Write-Host '=== database ==='
$pgUser = docker exec bankrotai-home-postgres printenv POSTGRES_USER
$pgDatabase = docker exec bankrotai-home-postgres printenv POSTGRES_DB
if ($LASTEXITCODE -eq 0 -and $pgUser -and $pgDatabase) {
    docker exec bankrotai-home-postgres psql -U $pgUser.Trim() -d $pgDatabase.Trim() -Atc `
        "select 'db_activity total='||count(*)||' active='||count(*) filter (where state='active')||' waiting='||count(*) filter (where wait_event is not null) from pg_stat_activity;" 2>&1
}

Write-Host '=== relay process and watchdog ==='
Get-CimInstance Win32_Process -Filter "Name = 'wstunnel.exe'" | ForEach-Object {
    $argsText = [string]$_.CommandLine
    $mode = if ($argsText.Contains('tcp://0.0.0.0:18080:127.0.0.1:18000') -and $argsText.Contains('wss://relay.194-226-126-233.sslip.io')) { 'expected' } else { 'other' }
    Write-Host "wstunnel pid=$($_.ProcessId) mode=$mode"
}
foreach ($taskName in @('BankrotAI Home WSS Relay', 'BankrotAI Home Reverse Relay')) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        Write-Host "task=$taskName state=$($task.State) lastRun=$($info.LastRunTime.ToString('o')) result=$($info.LastTaskResult)"
    }
}
Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '194.226.126.233/32' -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "route destination=$($_.DestinationPrefix) interface=$($_.InterfaceIndex) nextHop=$($_.NextHop) metric=$($_.RouteMetric)"
}

Write-Host '=== relay logs ==='
$logRoot = 'C:\ProgramData\BankrotAI\relay'
foreach ($file in @('relay-wss.log', 'relay-wss.log.err')) {
    $path = Join-Path $logRoot $file
    if (Test-Path -LiteralPath $path) {
        Write-Host "file=$file size=$((Get-Item -LiteralPath $path).Length)"
        Get-Content -LiteralPath $path -Tail 100 | ForEach-Object {
            $_ -replace '(?i)(authorization:\s*bearer\s+)[^\s]+', '$1***'
        }
    }
}

Write-Host '=== public relay sample ==='
foreach ($attempt in 1..3) {
    $status = & curl.exe -sS --connect-timeout 3 --max-time 10 -o NUL -w '%{http_code} duration=%{time_total}' 'https://home-relay.194-226-126-233.sslip.io/health/live' 2>$null
    Write-Host "attempt=$attempt status=$status curlExit=$LASTEXITCODE"
}
