param([string]$BaseUrl = "http://127.0.0.1:2322")

$ErrorActionPreference = "Stop"
$query = [uri]::EscapeDataString("г. Ярославль, ул. Свердлова, д. 5а/17")
$response = Invoke-RestMethod -TimeoutSec 15 -Uri "$BaseUrl/api?q=$query&limit=5&lang=ru&countrycode=RU"
if (-not $response.features -or $response.features.Count -lt 1) {
    throw "Photon returned no results for the regression address"
}
$coordinates = $response.features[0].geometry.coordinates
if ($coordinates.Count -lt 2) {
    throw "Photon result has no coordinates"
}
[pscustomobject]@{
    Status = "PASS"
    Results = $response.features.Count
    Longitude = $coordinates[0]
    Latitude = $coordinates[1]
}
