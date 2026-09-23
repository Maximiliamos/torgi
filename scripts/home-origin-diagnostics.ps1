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
    docker exec bankrotai-home-postgres psql -U $pgUser.Trim() -d $pgDatabase.Trim() -P pager=off -c @'
select id, version, status, is_current, point_count, tile_count, published_at, created_at
from map_datasets order by created_at desc limit 8;
'@ 2>&1
    docker exec bankrotai-home-postgres psql -U $pgUser.Trim() -d $pgDatabase.Trim() -P pager=off -c @'
select d.version, d.tile_count as declared_tiles, count(t.id) as actual_tiles,
       coalesce(sum(octet_length(t.payload_json::text)),0) as payload_bytes,
       coalesce(max(octet_length(t.payload_json::text)),0) as largest_tile_bytes
from map_datasets d left join map_tiles t on t.dataset_id=d.id
where d.is_current group by d.id, d.version, d.tile_count;
'@ 2>&1
    docker exec bankrotai-home-postgres psql -U $pgUser.Trim() -d $pgDatabase.Trim() -P pager=off -c @'
select task_id, status, created_at, started_at, finished_at,
       left(coalesce(progress_json::text,''),500) as progress,
       left(coalesce(error_message,''),300) as error
from background_task_states where task_type='geocoding'
order by created_at desc, id desc limit 12;
'@ 2>&1
    Write-Host '=== map marker detail sample ==='
    $sampleLotId = docker exec bankrotai-home-postgres psql -U $pgUser.Trim() -d $pgDatabase.Trim() -Atc @'
select feature->>'id'
from map_datasets d
join map_tiles t on t.dataset_id=d.id
cross join lateral jsonb_array_elements(t.payload_json->'features') feature
where d.is_current and feature->>'kind'='lot'
limit 1;
'@
    $apiKey = docker exec bankrotai-home-secondary printenv BANKROTAI_API_KEY
    if ($LASTEXITCODE -eq 0 -and $sampleLotId -and $apiKey) {
        Write-Output "::add-mask::$($apiKey.Trim())"
        Write-Host "sample_lot_id=$($sampleLotId.Trim())"
        & curl.exe -sS --connect-timeout 3 --max-time 30 -o NUL `
            -w 'detail_status=%{http_code} bytes=%{size_download} duration=%{time_total}' `
            -H "x-api-key: $($apiKey.Trim())" `
            "http://127.0.0.1:18000/api/map/lots/$($sampleLotId.Trim())"
        Write-Host " curlExit=$LASTEXITCODE"
    }
}

Write-Host '=== queue and workers ==='
$redisPasswordPath = 'C:\ProgramData\BankrotAI\redis-password.txt'
if (Test-Path -LiteralPath $redisPasswordPath) {
    $redisPassword = [IO.File]::ReadAllText($redisPasswordPath).Trim()
    Write-Output "::add-mask::$redisPassword"
    docker exec --env "REDISCLI_AUTH=$redisPassword" bankrotai-home-redis redis-cli LLEN celery 2>&1 |
        ForEach-Object { Write-Host "celery_queue_length=$_" }
}
docker exec bankrotai-home-ingestion-worker celery -A bankrotai.tasks:celery_app inspect ping --timeout 10 2>&1
docker exec bankrotai-home-ingestion-worker celery -A bankrotai.tasks:celery_app inspect active --timeout 10 2>&1
docker exec bankrotai-home-ingestion-worker celery -A bankrotai.tasks:celery_app inspect scheduled --timeout 10 2>&1

Write-Host '=== recent application logs ==='
docker logs --timestamps --since 2h bankrotai-home-ingestion-worker 2>&1 | Select-Object -Last 500
docker logs --timestamps --since 30m bankrotai-home-secondary 2>&1 | Select-Object -Last 300

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
