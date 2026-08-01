param(
    [Parameter(Mandatory = $false)]
    [string]$ExePath = "app\BankrotAI.exe",
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $ExePath).Path
$process = Start-Process -FilePath $resolved -ArgumentList "--smoke-test" -PassThru
if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Packaged desktop smoke test timed out after $TimeoutSeconds seconds"
}
if ($process.ExitCode -ne 0) {
    throw "Packaged desktop smoke test failed with exit code $($process.ExitCode)"
}
Write-Output "Packaged desktop smoke test passed: $resolved"
