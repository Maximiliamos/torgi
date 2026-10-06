[CmdletBinding()]
param(
    [string]$RunnerRoot = 'C:\BankrotAI\actions-runner',
    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'

function Get-RouteSummary {
    $defaultRoutes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric, InterfaceMetric |
        Select-Object -First 5)
    return @($defaultRoutes | ForEach-Object {
        $adapter = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue
        [ordered]@{
            interface_index = $_.InterfaceIndex
            interface_alias = $_.InterfaceAlias
            adapter_description = if ($adapter) { $adapter.InterfaceDescription } else { $null }
            next_hop = $_.NextHop
            route_metric = $_.RouteMetric
            interface_metric = $_.InterfaceMetric
        }
    })
}

function Test-Endpoint {
    param([Parameter(Mandatory=$true)][string]$HostName)

    $dnsOk = $false
    $addresses = @()
    try {
        $addresses = @(Resolve-DnsName -Name $HostName -Type A -ErrorAction Stop |
            Where-Object IPAddress |
            Select-Object -ExpandProperty IPAddress -Unique)
        $dnsOk = $addresses.Count -gt 0
    } catch {}

    $tcp = $null
    try {
        $tcp = Test-NetConnection $HostName -Port 443 -WarningAction SilentlyContinue
    } catch {}

    $httpsOk = $false
    $httpsStatus = $null
    $httpsError = $null
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-WebRequest -Uri ("https://{0}/" -f $HostName) -Method Head -TimeoutSec 20 -UseBasicParsing
        $httpsStatus = [int]$response.StatusCode
        $httpsOk = $true
    } catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $httpsStatus = [int]$_.Exception.Response.StatusCode
            $httpsOk = $true
        } else {
            $httpsError = $_.Exception.Message
        }
    } finally {
        $watch.Stop()
    }

    [ordered]@{
        host = $HostName
        dns_ok = $dnsOk
        addresses = $addresses
        tcp_443_ok = [bool]($tcp -and $tcp.TcpTestSucceeded)
        interface_alias = if ($tcp) { $tcp.InterfaceAlias } else { $null }
        source_address = if ($tcp) { [string]$tcp.SourceAddress } else { $null }
        remote_address = if ($tcp) { [string]$tcp.RemoteAddress } else { $null }
        https_ok = $httpsOk
        https_status = $httpsStatus
        https_elapsed_ms = [int]$watch.ElapsedMilliseconds
        https_error = $httpsError
    }
}

$hosts = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
@(
    'github.com',
    'api.github.com',
    'codeload.github.com',
    'pipelines.actions.githubusercontent.com',
    'results-receiver.actions.githubusercontent.com',
    'objects.githubusercontent.com'
) | ForEach-Object { [void]$hosts.Add($_) }

$latestLog = Get-ChildItem (Join-Path $RunnerRoot '_diag\Runner_*.log') -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if ($latestLog) {
    $recent = Get-Content -LiteralPath $latestLog.FullName -Tail 500 -ErrorAction SilentlyContinue
    foreach ($line in $recent) {
        foreach ($match in [regex]::Matches($line, 'https://([A-Za-z0-9.-]+\.actions\.githubusercontent\.com)')) {
            [void]$hosts.Add($match.Groups[1].Value)
        }
    }
}

$service = Get-Service -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'actions.runner*' -or $_.DisplayName -like '*GitHub Actions*' } |
    Select-Object -First 1
$listener = Get-Process Runner.Listener -ErrorAction SilentlyContinue | Select-Object -First 1
$worker = Get-Process Runner.Worker -ErrorAction SilentlyContinue | Select-Object -First 1

$endpoints = @($hosts | Sort-Object | ForEach-Object { Test-Endpoint -HostName $_ })
$failed = @($endpoints | Where-Object { -not $_.dns_ok -or -not $_.tcp_443_ok -or -not $_.https_ok })

$vpnHints = @(
    Get-NetAdapter -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Status -eq 'Up' -and (
                $_.Name -match '(?i)(xray|vpn|tun|tap|wireguard|wintun|happ)' -or
                $_.InterfaceDescription -match '(?i)(xray|vpn|tun|tap|wireguard|wintun|happ)'
            )
        } |
        Select-Object Name, InterfaceDescription, InterfaceIndex, Status
)

$result = [ordered]@{
    checked_at = (Get-Date).ToUniversalTime().ToString('o')
    healthy = $failed.Count -eq 0
    runner = [ordered]@{
        service_name = if ($service) { $service.Name } else { $null }
        service_status = if ($service) { [string]$service.Status } else { 'missing' }
        listener_pid = if ($listener) { $listener.Id } else { $null }
        worker_pid = if ($worker) { $worker.Id } else { $null }
        latest_log = if ($latestLog) { $latestLog.FullName } else { $null }
    }
    route = Get-RouteSummary
    vpn_hints = $vpnHints
    endpoints = $endpoints
    failed_endpoint_count = $failed.Count
    likely_route_interference = [bool]($vpnHints.Count -gt 0 -and $failed.Count -gt 0)
}

$json = $result | ConvertTo-Json -Depth 10
$json

if ($OutputPath) {
    $directory = Split-Path -Parent $OutputPath
    if ($directory) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $json | Set-Content -LiteralPath $OutputPath -Encoding utf8
}

$persistCommand = "import json,sys; from bankrotai.db import SessionLocal; from bankrotai.services.operations_status import record_operations_snapshot; p=json.load(sys.stdin); s=SessionLocal(); record_operations_snapshot(s,'runner',p); s.commit(); s.close()"
$json | docker exec -i bankrotai-home-map-worker python -c $persistCommand *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Could not persist runner network snapshot for Operations UX.'
}

if ($failed.Count -gt 0) { exit 2 }
