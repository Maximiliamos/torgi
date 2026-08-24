from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import logging
import math
import re
import time
import threading

import requests
from requests.exceptions import SSLError
from sqlalchemy.orm import Session

from bankrotai.db import LotGeoSnapshot, ProcessedLot, distance_km
from bankrotai.core import get_settings

logger = logging.getLogger(__name__)

CADASTRAL_RE = re.compile(r"^\d{2}:\d{2}:\d{6,7}:\d+$")
REQUEST_TIMEOUT = (get_settings().external_connect_timeout, get_settings().external_read_timeout)
# Cadastre providers are optional read dependencies behind a 10-second edge
# deadline. Keep each provider attempt bounded so an unavailable PKK followed
# by an unavailable NSPD still returns a controlled result before that deadline.
CADASTRAL_REQUEST_TIMEOUT = (
    min(get_settings().external_connect_timeout, 2.0),
    min(get_settings().external_read_timeout, 3.0),
)
NSPD_REFERER = "https://nspd.gov.ru/map?thematic=PKK"
NOMINATIM_MIN_REQUEST_INTERVAL = 1.05
CADASTRAL_MIN_REQUEST_INTERVAL = 0.35
CADASTRAL_CIRCUIT_BREAK_SECONDS = 300.0


class NSPDTLSVerificationError(RuntimeError):
    pass


def nspd_tls_verify() -> bool | str:
    settings = get_settings()
    if settings.app_env == "production":
        return settings.nspd_ca_bundle or True
    if settings.nspd_allow_insecure_debug:
        logger.warning("NSPD TLS verification is disabled by explicit local debug configuration")
        return False
    return settings.nspd_ca_bundle or True


@dataclass
class CadastralObjectResult:
    query: str
    cadastral_number: str | None = None
    object_type: str | None = None
    title: str | None = None
    address: str | None = None

    lat: float | None = None
    lon: float | None = None

    geometry_json: dict | None = None
    has_boundary: bool = False

    source: str = "unknown"
    confidence: str = "low"

    info: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class CadastralGeocoder:
    """
    Кадастровый поиск и геокодинг.
    Всё держим в geo.py, без отдельного cadastre.py.
    """

    FEATURE_TYPES = {
        "land_plot": 1,
        "building": 5,
    }

    def __init__(self):
        self.base_url = "https://pkk.rosreestr.ru/api/features"
        self.nspd_search_url = "https://nspd.gov.ru/api/geoportal/v2/search/geoportal"
        self.last_request_time = 0.0
        self._rate_lock = threading.Lock()
        self._pkk_request_lock = threading.Lock()
        self._nspd_request_lock = threading.Lock()
        self._pkk_disabled_until = 0.0
        self._nspd_disabled_until = 0.0

    def _circuit_available(self, service: str) -> bool:
        return time.monotonic() >= getattr(self, f"_{service}_disabled_until")

    def _open_circuit(self, service: str) -> None:
        setattr(
            self,
            f"_{service}_disabled_until",
            time.monotonic() + CADASTRAL_CIRCUIT_BREAK_SECONDS,
        )

    def _rate_limit(self):
        with self._rate_lock:
            elapsed = time.monotonic() - self.last_request_time
            if elapsed < CADASTRAL_MIN_REQUEST_INTERVAL:
                time.sleep(CADASTRAL_MIN_REQUEST_INTERVAL - elapsed)
            self.last_request_time = time.monotonic()

    def search(self, query: str) -> CadastralObjectResult:
        q = (query or "").strip()

        if not q:
            return CadastralObjectResult(
                query=q,
                error="Пустой запрос",
                confidence="none",
            )

        if CADASTRAL_RE.match(q):
            return self.search_by_cadastral_number(q)

        return self.search_by_address(q)

    def search_by_cadastral_number(self, cadastral_number: str) -> CadastralObjectResult:
        for kind, feature_type in self.FEATURE_TYPES.items():
            result = self._search_pkk_feature(cadastral_number, feature_type, kind)
            if result and result.lat and result.lon:
                return result

        try:
            nspd_result = self._search_nspd_geoportal(cadastral_number)
        except NSPDTLSVerificationError:
            logger.warning(
                "NSPD TLS verification failed for %s; leaving the lot for manual GEO review",
                cadastral_number,
            )
            return CadastralObjectResult(
                query=cadastral_number,
                cadastral_number=cadastral_number,
                source="nspd",
                confidence="none",
                error=(
                    "Не удалось установить защищённое соединение с НСПД. "
                    "Проверка сертификата не отключалась; объект оставлен для ручной проверки."
                ),
            )
        if nspd_result and nspd_result.lat and nspd_result.lon:
            return nspd_result

        return CadastralObjectResult(
            query=cadastral_number,
            cadastral_number=cadastral_number,
            source="pkk/nspd",
            confidence="none",
            error="Объект не найден в кадастровом API или API недоступен. Старый PKK часто отключен, НСПД может быть недоступен из текущей сети.",
        )

    def search_by_address(self, address: str) -> CadastralObjectResult:
        result = NOMINATIM_GEOCODER.geocode(address)

        if not result:
            return CadastralObjectResult(
                query=address,
                address=address,
                source="nominatim",
                confidence="none",
                error="Адрес не найден",
            )

        return CadastralObjectResult(
            query=address,
            title="Адрес найден",
            address=address,
            lat=result.get("centroid_lat"),
            lon=result.get("centroid_lon"),
            source="nominatim",
            confidence=result.get("geo_confidence", "medium"),
            info={
                "Адрес": address,
                "Источник": "Nominatim / OpenStreetMap",
                "Примечание": result.get("trace_reason", ""),
            },
        )

    def _search_pkk_feature(
        self,
        cadastral_number: str,
        feature_type: int,
        kind: str,
    ) -> CadastralObjectResult | None:
        url = f"{self.base_url}/{feature_type}"
        params = {"cadastralNumber": cadastral_number}

        if not self._circuit_available("pkk"):
            return None
        with self._pkk_request_lock:
            if not self._circuit_available("pkk"):
                return None
            self._rate_limit()
            try:
                resp = requests.get(url, params=params, timeout=CADASTRAL_REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                self._open_circuit("pkk")
                logger.warning("PKK request failed for %s; pausing PKK requests: %s", cadastral_number, e)
                return None
            except Exception as e:
                logger.warning("PKK response failed for %s: %s", cadastral_number, e)
                return None

        return self._parse_pkk_feature(data, cadastral_number, kind)

    def _parse_pkk_feature(
        self,
        data: dict,
        cadastral_number: str,
        kind: str,
    ) -> CadastralObjectResult | None:
        features = data.get("features") or []
        if not features:
            return None

        feature = features[0]
        attrs = feature.get("attrs") or {}
        center = feature.get("center") or {}
        geometry = feature.get("geometry")

        lat = lon = None

        if "x" in center and "y" in center:
            lon = float(center["x"])
            lat = float(center["y"])
        elif geometry:
            lat, lon = centroid_from_geometry(geometry)

        info = normalize_pkk_attrs(attrs, cadastral_number, kind)

        if lat is None or lon is None:
            return CadastralObjectResult(
                query=cadastral_number,
                cadastral_number=cadastral_number,
                object_type=info.get("Вид объекта недвижимости"),
                source="pkk",
                confidence="low",
                info=info,
                raw=feature,
                error="Объект найден, но координаты не получены",
            )

        geometry_json = to_geojson_geometry(geometry)

        return CadastralObjectResult(
            query=cadastral_number,
            cadastral_number=cadastral_number,
            object_type=info.get("Вид объекта недвижимости") or kind,
            title=info.get("Наименование") or info.get("Назначение") or kind,
            address=info.get("Адрес"),
            lat=lat,
            lon=lon,
            geometry_json=geometry_json,
            has_boundary=bool(geometry_json),
            source="pkk",
            confidence="high",
            info=info,
            raw=feature,
        )

    def _search_nspd_geoportal(self, cadastral_number: str) -> CadastralObjectResult | None:
        headers = {
            "Referer": NSPD_REFERER,
            "User-Agent": "Mozilla/5.0 BankrotAI/1.0",
            "Accept": "application/json,text/plain,*/*",
        }
        params: dict[str, str | int] = {
            "thematicSearchId": 1,
            "query": cadastral_number,
        }

        if not self._circuit_available("nspd"):
            return None
        with self._nspd_request_lock:
            if not self._circuit_available("nspd"):
                return None
            try:
                resp = requests.get(
                    self.nspd_search_url,
                    params=params,
                    headers=headers,
                    timeout=CADASTRAL_REQUEST_TIMEOUT,
                    verify=nspd_tls_verify(),
                )
                resp.raise_for_status()
                data = resp.json()
            except SSLError as e:
                self._open_circuit("nspd")
                logger.error("NSPD TLS verification failed for %s: %s", cadastral_number, e)
                raise NSPDTLSVerificationError("NSPD TLS certificate verification failed") from e
            except requests.RequestException as e:
                self._open_circuit("nspd")
                logger.warning("NSPD request failed for %s; pausing NSPD requests: %s", cadastral_number, e)
                return None
            except Exception as e:
                logger.warning("NSPD response failed for %s: %s", cadastral_number, e)
                return None

        features = ((data.get("data") or {}).get("features") or data.get("features") or [])
        if not features:
            return None

        feature = self._pick_nspd_feature(features, cadastral_number)
        props = feature.get("properties") or {}
        geometry = geometry_to_wgs84(feature.get("geometry"))
        lat, lon = centroid_from_geometry(geometry)

        if lat is None or lon is None:
            return CadastralObjectResult(
                query=cadastral_number,
                cadastral_number=cadastral_number,
                source="nspd",
                confidence="low",
                info=normalize_nspd_props(props, cadastral_number),
                raw=feature,
                error="Объект найден в НСПД, но координаты не получены",
            )

        info = normalize_nspd_props(props, cadastral_number)
        geometry_json = geometry if geometry and geometry.get("type") != "Point" else None

        return CadastralObjectResult(
            query=cadastral_number,
            cadastral_number=info.get("Кадастровый номер") or cadastral_number,
            object_type=info.get("Вид объекта недвижимости"),
            title=info.get("Наименование") or info.get("Назначение") or info.get("Вид объекта недвижимости"),
            address=info.get("Адрес"),
            lat=lat,
            lon=lon,
            geometry_json=geometry_json,
            has_boundary=bool(geometry_json),
            source="nspd",
            confidence="high",
            info=info,
            raw=feature,
        )

    def _pick_nspd_feature(self, features: list[dict], cadastral_number: str) -> dict:
        for feature in features:
            text = json_like_text(feature)
            if cadastral_number in text:
                return feature
        return features[0]


    def geocode(self, cadastral_number: str) -> dict | None:
        result = self.search_by_cadastral_number(cadastral_number)
        if not result or not result.lat or not result.lon:
            return None

        return {
            "centroid_lat": result.lat,
            "centroid_lon": result.lon,
            "geo_confidence": result.confidence,
        }


def build_geocoding_address_candidates(
    address: str | None,
    *,
    title: str | None = None,
    description: str | None = None,
    region_name: str | None = None,
) -> list[str]:
    """Build conservative Nominatim queries from Russian auction-card addresses."""
    value = (address or "").strip()
    if not value and (title or description):
        # Keep this fallback aligned with extractors.extract_address so that
        # already imported shallow LOT-ONLINE cards can be geocoded without a
        # full re-import.
        from bankrotai.extractors import extract_address

        value = extract_address(" ".join(part for part in (title, description) if part)) or ""
    if not value:
        return []

    value = re.sub(r"\.{3,}$", "", value)
    value = re.sub(
        r",?\s*(?:с|вместе\s+с)\s+земельн(?:ым|ого)\s+участк(?:ом|а).*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r",\s*с\.\s*п\.\s*[^,]+", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"(?:м\.\s*)?р-н\s+([^,]+)",
        lambda match: f"{match.group(1).strip()} муниципальный округ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"([^,]+?)\s+(?:р-н|район)(?=,|$)",
        lambda match: f"{match.group(1).strip()} муниципальный округ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\bПереславский муниципальный округ\b",
        "Переславль-Залесский муниципальный округ",
        value,
        flags=re.IGNORECASE,
    )
    replacements = (
        (r"\bобл\.(?=\s|,|$)", "область"),
        (r"\bг\.(?=\s)", "город "),
        (r"\bп\.(?=\s)", "поселок "),
        (r"\bс\.(?=\s)", "село "),
        (r"\bд\.(?=\s)", "дом "),
        (r"\bул\.(?=\s)", "улица "),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" ,.;")
    if region_name and region_name.casefold() not in value.casefold():
        value = f"{value}, {region_name}"

    parts = [part.strip(" ,.;") for part in value.split(",") if part.strip(" ,.;")]
    region = next((part for part in parts if "област" in part.casefold()), region_name or "")
    district = next(
        (part for part in parts if "муниципальн" in part.casefold() or "район" in part.casefold()),
        "",
    )
    locality = next(
        (
            part for part in parts
            if re.search(r"\b(?:город|село|поселок|деревня|рабочий поселок)\b", part, re.IGNORECASE)
        ),
        "",
    )
    locality_query = re.sub(
        r"^(?:город|село|поселок|деревня|рабочий поселок)\s+",
        "",
        locality,
        flags=re.IGNORECASE,
    )
    street = next((part for part in parts if re.search(r"\b(?:улица|проспект|шоссе|переулок)\b", part, re.I)), "")
    house = next((part for part in parts if re.search(r"\bдом\s*\d", part, re.IGNORECASE)), "")
    house_number = re.sub(r"^дом\s*", "", house, flags=re.IGNORECASE) if house else ""

    candidates = [value]
    if street and locality_query:
        candidates.append(", ".join(part for part in (house_number, street, locality_query, district, region) if part))
    if locality_query and district:
        candidates.append(", ".join(part for part in (locality_query, district, region) if part))
    if locality_query and region:
        candidates.append(f"{locality_query}, {region}")

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = " ".join(candidate.split()).strip(" ,")
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


class NominatimGeocoder:
    def __init__(self):
        self.base_url = "https://nominatim.openstreetmap.org/search"
        self.last_request_time = 0.0
        self._lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._cache: dict[tuple[str, str], dict | None] = {}
        self._inflight: dict[tuple[str, str], threading.Event] = {}

    def geocode(self, address: str) -> dict | None:
        if not address or len(address.strip()) < 5:
            return None

        normalized_address = " ".join(address.casefold().split())
        cache_key = (normalized_address, "nominatim")
        with self._lock:
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                return dict(cached) if cached else None
            wait_event = self._inflight.get(cache_key)
            if wait_event is None:
                wait_event = threading.Event()
                self._inflight[cache_key] = wait_event
                owns_request = True
            else:
                owns_request = False

        if not owns_request:
            wait_event.wait(timeout=120)
            with self._lock:
                cached = self._cache.get(cache_key)
            return dict(cached) if cached else None

        ignored_tokens = {
            "россия", "область", "области", "муниципальный", "округ", "район",
            "город", "село", "поселок", "деревня", "улица", "проспект", "дом",
        }

        def match_tokens(text: str) -> set[str]:
            return {
                token[:7]
                for token in re.findall(r"[а-яёa-z-]{5,}", text.casefold())
                if token not in ignored_tokens and not token.startswith("ярославск")
            }

        expected_tokens = match_tokens(address)
        value = None
        for index, candidate in enumerate(build_geocoding_address_candidates(address)):
            try:
                with self._request_lock:
                    elapsed = time.monotonic() - self.last_request_time
                    if elapsed < NOMINATIM_MIN_REQUEST_INTERVAL:
                        time.sleep(NOMINATIM_MIN_REQUEST_INTERVAL - elapsed)
                    self.last_request_time = time.monotonic()
                headers = {
                    "User-Agent": "BankrotAI/1.0 (contact: local-user)",
                    "Referer": "https://local.bankrotai/",
                }
                params: dict[str, str | int] = {
                    "q": candidate,
                    "format": "jsonv2",
                    "limit": 20,
                    "addressdetails": 1,
                    "countrycodes": "ru",
                }
                resp = requests.get(self.base_url, params=params, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    continue
                scored = [
                    (len(expected_tokens & match_tokens(str(item.get("display_name") or ""))), item)
                    for item in data
                ]
                scored.sort(key=lambda item: (item[0], float(item[1].get("importance", 0))), reverse=True)
                best_score, result = scored[0]
                if result.get("display_name") and expected_tokens and best_score < min(2, len(expected_tokens)):
                    continue
                lat = float(result["lat"])
                lon = float(result["lon"])
                importance = float(result.get("importance", 0))
                confidence = "high" if index == 0 and importance > 0.5 else "medium"
                value = {
                    "centroid_lat": lat,
                    "centroid_lon": lon,
                    "geo_confidence": confidence,
                    "trace_reason": f"OSM Nominatim: {candidate}",
                }
                break
            except Exception as e:
                logger.warning("Geocoding failed for '%s': %s", candidate, e)

        with self._lock:
            self._cache[cache_key] = value
            if len(self._cache) > 10_000:
                self._cache.pop(next(iter(self._cache)))
            completed_event = self._inflight.pop(cache_key, None)
            if completed_event:
                completed_event.set()
        return dict(value) if value else None


NOMINATIM_GEOCODER = NominatimGeocoder()
CADASTRAL_GEOCODER = CadastralGeocoder()


def centroid_from_geometry(geom: dict | None) -> tuple[float | None, float | None]:
    if not geom:
        return None, None

    coords = geom.get("coordinates")
    if not coords:
        return None, None

    points = []

    def collect(obj):
        if isinstance(obj, list):
            if len(obj) >= 2 and all(isinstance(x, (int, float)) for x in obj[:2]):
                points.append(obj)
            else:
                for item in obj:
                    collect(item)

    collect(coords)

    if not points:
        return None, None

    lon = sum(p[0] for p in points) / len(points)
    lat = sum(p[1] for p in points) / len(points)

    return lat, lon


def to_geojson_geometry(geom: dict | None) -> dict | None:
    if not geom:
        return None

    if geom.get("type") and geom.get("coordinates"):
        return {
            "type": geom["type"],
            "coordinates": geom["coordinates"],
        }

    return None


def json_like_text(value: Any) -> str:
    return str(value)


def web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    radius = 6378137.0
    lon = (x / radius) * 180.0 / math.pi
    lat = math.degrees(math.atan(math.sinh(y / radius)))
    return lon, lat


def geometry_to_wgs84(geom: dict | None) -> dict | None:
    if not geom:
        return None

    coords = geom.get("coordinates")
    if not coords:
        return to_geojson_geometry(geom)

    def convert(obj):
        if isinstance(obj, list):
            if len(obj) >= 2 and all(isinstance(x, (int, float)) for x in obj[:2]):
                x, y = float(obj[0]), float(obj[1])
                if abs(x) > 180 or abs(y) > 90:
                    return list(web_mercator_to_wgs84(x, y))
                return [x, y]
            return [convert(item) for item in obj]
        return obj

    return {
        "type": geom.get("type"),
        "coordinates": convert(coords),
    }


def normalize_nspd_props(props: dict, cadastral_number: str) -> dict[str, Any]:
    options = props.get("options") if isinstance(props.get("options"), dict) else {}

    def pick(*keys):
        for source in (props, options):
            for key in keys:
                val = source.get(key) if isinstance(source, dict) else None
                if val not in (None, ""):
                    return val
        return None

    return {
        "Вид объекта недвижимости": pick("categoryName", "category_name", "objectType", "typeName", "type"),
        "Кадастровый номер": pick("cad_num", "cadNum", "cadastralNumber", "cn", "label") or cadastral_number,
        "Адрес": pick("address", "readableAddress", "location", "addr"),
        "Наименование": pick("name", "label", "descr"),
        "Назначение": pick("purpose", "util_by_doc", "assignation"),
        "Площадь общая": pick("area", "area_value", "readableArea"),
        "Статус": pick("status", "state", "readableStatus"),
        "Кадастровая стоимость": pick("cad_cost", "cost", "readableCadCost"),
        "Категория НСПД": pick("category", "categoryId"),
    }


def normalize_pkk_attrs(attrs: dict, cadastral_number: str, kind: str) -> dict[str, Any]:
    def pick(*keys):
        for key in keys:
            val = attrs.get(key)
            if val not in (None, ""):
                return val
        return None

    object_type = "Здание" if kind == "building" else "Земельный участок"

    return {
        "Вид объекта недвижимости": pick("type_name", "type", "type_value", "obj_type") or object_type,
        "Дата присвоения": pick("date_create", "assign_date", "cad_record_date"),
        "Кадастровый номер": pick("cn", "cadnum", "cadastral_number") or cadastral_number,
        "Кадастровый квартал": pick("kvartal", "cad_quarter", "quarter"),
        "Адрес": pick("address", "addr", "address_note", "location"),
        "Наименование": pick("name", "object_name"),
        "Назначение": pick("util_by_doc", "purpose", "assignation"),
        "Площадь общая": pick("area_value", "area", "s"),
        "Единица площади": pick("area_unit", "area_type"),
        "Статус": pick("cad_record_status", "statecd", "state", "status"),
        "Форма собственности": pick("fp", "ownership", "right_type"),
        "Кадастровая стоимость": pick("cad_cost", "cad_cost_value", "cad_price"),
        "Удельный показатель кадастровой стоимости": pick("ud_cost", "unit_cost"),
        "Количество этажей": pick("floors", "floor_count"),
        "Количество подземных этажей": pick("underground_floors", "underground_floor_count"),
        "Завершение строительства": pick("year_built", "build_year"),
        "Ввод в эксплуатацию": pick("year_commissioning", "year_commisioning", "commissioning_year"),
        "ОКН": pick("cultural_heritage", "heritage", "oks_flag"),
    }


def resolve_lot_geo(
    cadastral_number: str | None = None,
    address: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
    region_name: str | None = None,
) -> CadastralObjectResult | None:
    final_result = None

    if cadastral_number:
        cad_result = CADASTRAL_GEOCODER.search_by_cadastral_number(cadastral_number)
        if cad_result and cad_result.lat and cad_result.lon:
            final_result = cad_result

    address_candidates = build_geocoding_address_candidates(
        address,
        title=title,
        description=description,
        region_name=region_name,
    )
    if final_result is None and address_candidates:
        addr_result = CADASTRAL_GEOCODER.search_by_address(address_candidates[0])
        if addr_result and addr_result.lat and addr_result.lon:
            final_result = addr_result

    return final_result


def apply_lot_geo_result(session: Session, lot: ProcessedLot, final_result: CadastralObjectResult | None) -> bool:
    if not final_result or not final_result.lat or not final_result.lon:
        lot.needs_geo_check = True
        return False

    snapshot = LotGeoSnapshot(
        lot_id=lot.id,
        geo_source=final_result.source,
        geo_method="cadastral" if final_result.cadastral_number else "address",
        geo_confidence=final_result.confidence,
        centroid_lat=final_result.lat,
        centroid_lon=final_result.lon,
        geometry_json=final_result.geometry_json,
        metadata_json={
            "query": final_result.query,
            "cadastral_number": final_result.cadastral_number,
            "object_type": final_result.object_type,
            "title": final_result.title,
            "address": final_result.address,
            "has_boundary": final_result.has_boundary,
            "info": final_result.info,
            "source": final_result.source,
            "error": final_result.error,
        },
        trace_reason=(
            f"{final_result.source}: "
            f"{'границы получены' if final_result.has_boundary else 'без границ'}"
        ),
    )

    session.add(snapshot)

    if final_result.address and (not lot.address or len(lot.address) < 15):
        lot.address = final_result.address

    lot.needs_geo_check = final_result.confidence not in {"high", "medium"}

    return True


def enrich_lot_geo(session: Session, lot: ProcessedLot) -> bool:
    final_result = resolve_lot_geo(
        lot.cadastral_number,
        lot.address,
        title=lot.title,
        description=lot.description,
        region_name=lot.region_name,
    )
    return apply_lot_geo_result(session, lot, final_result)


def distance_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return distance_km(lat1, lon1, lat2, lon2)
