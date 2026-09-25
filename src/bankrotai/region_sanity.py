from __future__ import annotations

from dataclasses import dataclass

from bankrotai.regions import REGION_DIRECTORY, normalize_region_code


@dataclass(frozen=True, slots=True)
class RegionSanityEnvelope:
    south: float
    north: float
    longitude_ranges: tuple[tuple[float, float], ...]

    def contains(self, lat: float, lon: float) -> bool:
        latitude = float(lat)
        longitude = float(lon)
        return (
            self.south <= latitude <= self.north
            and any(west <= longitude <= east for west, east in self.longitude_ranges)
        )


def _box(south: float, north: float, west: float, east: float) -> RegionSanityEnvelope:
    return RegionSanityEnvelope(south, north, ((west, east),))


# Deliberately generous subject envelopes. These are gross-outlier guards, not
# administrative polygons: the margins are intentionally wider than real
# borders so a valid border settlement is retained while coordinates hundreds
# or thousands of kilometres away are rejected.
REGION_SANITY_ENVELOPES: dict[str, RegionSanityEnvelope] = {
    "01": _box(43.5, 45.6, 38.2, 41.3),
    "02": _box(50.5, 58.0, 52.0, 61.5),
    "03": _box(48.0, 58.5, 95.0, 118.5),
    "04": _box(48.5, 54.0, 82.0, 91.0),
    "05": _box(40.0, 45.5, 44.5, 50.0),
    "06": _box(42.5, 44.5, 43.5, 46.0),
    "07": _box(42.4, 44.6, 41.5, 45.2),
    "08": _box(43.8, 49.0, 40.0, 49.0),
    "09": _box(42.8, 45.0, 39.5, 43.5),
    "10": _box(59.0, 68.0, 28.0, 39.0),
    "11": _box(57.0, 70.0, 43.0, 68.0),
    "12": _box(54.5, 58.0, 44.0, 51.0),
    "13": _box(53.0, 56.0, 40.0, 48.0),
    "14": _box(54.0, 79.0, 99.0, 171.0),
    "15": _box(42.2, 44.8, 42.5, 46.0),
    "16": _box(52.5, 57.5, 46.0, 56.0),
    "17": _box(48.5, 55.0, 87.0, 101.0),
    "18": _box(54.5, 59.5, 50.0, 56.0),
    "19": _box(50.5, 56.5, 86.0, 93.0),
    "20": _box(42.0, 45.0, 43.5, 48.0),
    "21": _box(53.5, 57.5, 44.0, 50.0),
    "22": _box(49.0, 56.0, 76.0, 89.0),
    "23": _box(42.5, 48.0, 35.0, 43.0),
    "24": _box(50.0, 82.5, 74.0, 116.0),
    "25": _box(41.5, 50.5, 129.0, 141.0),
    "26": _box(42.5, 47.0, 39.0, 47.0),
    "27": _box(45.0, 64.5, 129.0, 150.0),
    "28": _box(47.0, 59.0, 118.0, 137.0),
    "29": _box(59.0, 82.5, 33.0, 71.0),
    "30": _box(44.5, 50.0, 43.0, 51.0),
    "31": _box(49.0, 53.0, 34.0, 41.0),
    "32": _box(51.0, 55.5, 30.0, 36.5),
    "33": _box(54.0, 58.0, 37.0, 44.5),
    "34": _box(46.0, 53.5, 40.0, 49.0),
    "35": _box(56.0, 63.0, 33.0, 49.0),
    "36": _box(48.5, 53.0, 37.0, 44.0),
    "37": _box(54.5, 58.5, 38.0, 45.0),
    "38": _box(49.0, 66.0, 94.0, 121.0),
    "39": _box(53.5, 56.5, 18.5, 23.5),
    "40": _box(52.5, 56.5, 32.5, 39.5),
    "41": _box(49.0, 67.0, 154.0, 180.0),
    "42": _box(51.0, 58.0, 83.0, 91.0),
    "43": _box(54.0, 63.0, 44.0, 56.0),
    "44": _box(55.0, 61.0, 37.5, 49.0),
    "45": _box(53.0, 58.5, 59.0, 70.0),
    "46": _box(49.5, 54.0, 33.0, 40.0),
    "47": _box(56.5, 63.0, 25.5, 38.0),
    "48": _box(50.0, 55.0, 36.0, 43.0),
    "49": _box(57.0, 68.0, 143.0, 166.0),
    "50": _box(53.5, 59.5, 33.0, 43.0),
    "51": _box(65.0, 71.5, 26.0, 43.0),
    "52": _box(52.0, 59.5, 39.0, 50.0),
    "53": _box(55.0, 61.0, 27.0, 39.0),
    "54": _box(52.0, 58.0, 73.0, 87.0),
    "55": _box(51.5, 60.0, 67.0, 79.0),
    "56": _box(48.0, 56.0, 49.0, 64.0),
    "57": _box(50.5, 55.0, 32.0, 41.0),
    "58": _box(50.0, 57.0, 40.0, 49.0),
    "59": _box(55.0, 63.5, 49.0, 62.0),
    "60": _box(54.5, 60.5, 25.0, 34.0),
    "61": _box(44.0, 52.0, 35.0, 47.0),
    "62": _box(51.5, 57.0, 36.0, 45.0),
    "63": _box(49.5, 57.0, 45.0, 55.0),
    "64": _box(48.0, 56.0, 39.0, 53.0),
    "65": _box(42.0, 56.5, 138.0, 159.0),
    "66": _box(53.5, 64.0, 55.0, 70.0),
    "67": _box(52.0, 58.5, 28.0, 38.0),
    "68": _box(50.0, 56.0, 36.0, 46.0),
    "69": _box(54.0, 61.0, 28.0, 42.0),
    "70": _box(53.0, 64.0, 72.0, 92.0),
    "71": _box(51.0, 57.0, 32.0, 42.0),
    "72": _box(53.0, 62.0, 62.0, 79.0),
    "73": _box(50.5, 57.0, 42.0, 53.0),
    "74": _box(49.0, 59.0, 54.0, 66.0),
    "75": _box(47.0, 61.0, 104.0, 124.0),
    "76": _box(54.0, 61.0, 34.0, 44.0),
    "77": _box(54.5, 57.0, 35.0, 40.0),
    "78": _box(58.5, 61.0, 28.0, 33.0),
    "79": _box(46.0, 52.0, 128.0, 137.0),
    "80": _box(46.0, 50.5, 35.0, 41.0),
    "81": _box(46.0, 51.5, 36.0, 42.0),
    "82": _box(43.5, 47.0, 31.0, 39.0),
    "83": _box(65.0, 72.5, 40.0, 69.0),
    "84": _box(44.5, 50.0, 29.0, 39.0),
    "85": _box(44.5, 50.5, 31.0, 41.0),
    "86": _box(56.0, 68.0, 56.0, 90.0),
    "87": RegionSanityEnvelope(59.0, 73.5, ((154.0, 180.0), (-180.0, -163.0))),
    "89": _box(60.0, 76.0, 58.0, 92.0),
    "92": _box(43.5, 46.0, 31.0, 36.0),
}


def coordinate_matches_region_sanity(
    lat: float,
    lon: float,
    region_code: str | None,
) -> bool:
    code = normalize_region_code(region_code)
    if code is None:
        return True
    envelope = REGION_SANITY_ENVELOPES.get(code)
    if envelope is None:
        return True
    return envelope.contains(lat, lon)


def uncovered_canonical_region_codes() -> set[str]:
    return {region.code for region in REGION_DIRECTORY} - set(REGION_SANITY_ENVELOPES)
