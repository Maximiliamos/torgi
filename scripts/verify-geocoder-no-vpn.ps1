param(
    [string]$CadastralNumber = "76:23:050309:1108",
    [string]$Address = "г. Ярославль, ул. Свердлова, д. 5а/17"
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = (Resolve-Path "$PSScriptRoot\..\src").Path

@'
import json
import os
from bankrotai.geo import CADASTRAL_GEOCODER, IK12_GEOCODER, resolve_lot_geo

cadastre = os.environ["GEOCODER_TEST_CADASTRE"]
address = os.environ["GEOCODER_TEST_ADDRESS"]
ik12 = IK12_GEOCODER.search_by_cadastral_number(cadastre)
nspd = CADASTRAL_GEOCODER._search_nspd_geoportal(cadastre)
resolved = resolve_lot_geo(cadastre, address, region_name="Ярославская область")

def safe(value):
    if value is None:
        return None
    return {
        "source": value.source,
        "status": value.status,
        "confidence": value.confidence,
        "lat": value.lat,
        "lon": value.lon,
        "cadastral_number": value.cadastral_number,
        "address": value.address,
        "error": value.error,
        "attempts": value.attempts,
    }

print(json.dumps({"ik12": safe(ik12), "nspd": safe(nspd), "resolved": safe(resolved)}, ensure_ascii=False, indent=2))
'@ | ForEach-Object {
    $env:GEOCODER_TEST_CADASTRE = $CadastralNumber
    $env:GEOCODER_TEST_ADDRESS = $Address
    $_ | python -
}
