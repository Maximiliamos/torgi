from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any
import concurrent.futures
import hashlib
import logging
import math
import os
import re
import time
import threading

import requests
from requests.exceptions import SSLError
from sqlalchemy.orm import Session

from bankrotai.db import LotGeoSnapshot, ProcessedLot, distance_km
from bankrotai.core import get_settings, utc_now
from bankrotai.region_sanity import coordinate_region_sanity_rejection_reason
from bankrotai.services.geo_resilience import (
    GeoProviderUnavailable,
    classify_transport_exception,
    record_provider_failure,
    record_provider_success,
    require_provider,
)

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
INTERACTIVE_CADASTRAL_CACHE_SECONDS = 21_600.0
INTERACTIVE_CADASTRAL_CACHE_MAX_ENTRIES = 2_048
INTERACTIVE_CADASTRAL_HARD_DEADLINE_SECONDS = 7.5
INTERACTIVE_NSPD_BUDGET_SECONDS = 3.2
INTERACTIVE_NSPD_HEDGE_DELAY_SECONDS = 1.1
INTERACTIVE_NSPD_CONNECT_TIMEOUT = 0.65
INTERACTIVE_NSPD_READ_TIMEOUT = 1.55
INTERACTIVE_NSPD_RETRY_READ_TIMEOUT = 0.9
INTERACTIVE_IK12_BUDGET_SECONDS = 4.8
INTERACTIVE_IK12_CONNECT_TIMEOUT = 0.7
INTERACTIVE_IK12_READ_TIMEOUT = 1.2
INTERACTIVE_IK12_POW_BUDGET_SECONDS = 2.2
NSPD_WMS_LAYERS = (("building", "36049"), ("land_plot", "36048"))


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
    status: str = "GEOCODED"
    attempts: list[dict[str, Any]] = field(default_factory=list)


IK12_API_BASE = "https://api.roscadastres.com/pkk_files"
IK12_MAX_NONCE = 5_000_000
CITY_SANITY_ANCHORS = {
    "ярославл": (57.6261, 39.8845, 45.0),
    "москв": (55.7558, 37.6176, 90.0),
    "санкт-петербург": (59.9343, 30.3351, 75.0),
}

class IK12Geocoder:
    """Minimal HTTP client for the public IK12 cadastral map challenge API."""

    def __init__(self) -> None:
        self.session = requests.Session()

    @staticmethod
    def _solve_pow(timestamp: int, query: str, threshold: int) -> int:
        prefix = f"{timestamp}{query}".encode()
        for nonce in range(IK12_MAX_NONCE):
            digest = hashlib.sha256(prefix + str(nonce).encode()).digest()
            if int.from_bytes(digest[:4], "big") < threshold:
                return nonce
        raise RuntimeError("IK12 proof-of-work limit exceeded")

    def search_by_cadastral_number(self, cadastral_number: str) -> CadastralObjectResult | None:
        require_provider("ik12", external=True)
        started = time.monotonic()
        try:
            token_response = self.session.get(
                f"{IK12_API_BASE}/token.php",
                params={"query": cadastral_number, "action": "search"},
                timeout=CADASTRAL_REQUEST_TIMEOUT,
            )
            token_response.raise_for_status()
            token = token_response.json()
            nonce = self._solve_pow(int(token["timestamp"]), cadastral_number, int(token["threshold"]))
            elapsed = max(1, round((time.monotonic() - started) * 1000))
            response = self.session.get(
                f"{IK12_API_BASE}/search3.php",
                params={
                    "query": cadastral_number,
                    "action": "search",
                    "type": 1,
                    "timestamp": token["timestamp"],
                    "hash": token["hash"],
                    "threshold": token["threshold"],
                    "version": token["version"],
                    "nonce": nonce,
                    "elapsed": elapsed,
                },
                timeout=CADASTRAL_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            record_provider_success("ik12", latency_ms=(time.monotonic() - started) * 1000)
        except requests.RequestException as exc:
            category = classify_transport_exception(exc)
            record_provider_failure("ik12", category, latency_ms=(time.monotonic() - started) * 1000)
            logger.warning("IK12 request failed for %s: %s", cadastral_number, exc)
            raise GeoProviderUnavailable("ik12", category, str(exc)) from exc
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            record_provider_failure("ik12", "provider_protocol", latency_ms=(time.monotonic() - started) * 1000)
            logger.warning("IK12 response failed for %s: %s", cadastral_number, exc)
            raise GeoProviderUnavailable("ik12", "provider_protocol", str(exc)) from exc

        features = (payload.get("object_data") or {}).get("features") or []
        expected = cadastral_number.replace(" ", "")
        feature = next(
            (item for item in features if str((item.get("attrs") or {}).get("cn") or "").replace(" ", "") == expected),
            None,
        )
        if feature is None:
            return None

        attrs = feature.get("attrs") or {}
        raw_type = str(
            payload.get("object_type")
            or attrs.get("type_name")
            or attrs.get("type")
            or attrs.get("obj_type")
            or ""
        ).casefold()
        kind = "building" if raw_type == "5" or "здан" in raw_type or "building" in raw_type else "land_plot"
        info = normalize_pkk_attrs(attrs, expected, kind)

        center = feature.get("center") or {}
        geometry = geometry_to_wgs84(feature.get("geometry"))
        lat, lon = centroid_from_geometry(geometry)
        if "x" in center and "y" in center:
            lon, lat = web_mercator_to_wgs84(float(center["x"]), float(center["y"]))
        if lat is None or lon is None:
            return CadastralObjectResult(
                query=cadastral_number,
                cadastral_number=expected,
                object_type=info.get("Вид объекта недвижимости"),
                title=info.get("Наименование") or info.get("Назначение"),
                address=info.get("Адрес"),
                source="ik12_cadastral",
                confidence="low",
                raw=feature,
                info=info,
                error="Объект найден в резервном кадастровом источнике, но координаты не получены",
            )

        geometry_json = geometry if geometry and geometry.get("type") != "Point" else None
        return CadastralObjectResult(
            query=cadastral_number,
            cadastral_number=expected,
            object_type=info.get("Вид объекта недвижимости"),
            title=info.get("Наименование") or info.get("Назначение") or info.get("Вид объекта недвижимости"),
            address=info.get("Адрес"),
            lat=lat,
            lon=lon,
            geometry_json=geometry_json,
            has_boundary=bool(geometry_json),
            source="ik12_cadastral",
            confidence="high",
            raw=feature,
            info=info,
        )


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
        self._nspd_request_lock = threading.BoundedSemaphore(get_settings().geo_nspd_concurrency)
        self._pkk_disabled_until = 0.0
        self._nspd_disabled_until = 0.0
        self._interactive_cache: dict[str, tuple[float, CadastralObjectResult]] = {}
        self._interactive_cache_lock = threading.Lock()

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

    @staticmethod
    def _cache_key(prefix: str, value: str) -> str:
        return f"{prefix}:{' '.join((value or '').casefold().split())}"

    def _cache_get(self, key: str) -> CadastralObjectResult | None:
        now = time.monotonic()
        with self._interactive_cache_lock:
            item = self._interactive_cache.get(key)
            if item is None:
                return None
            stored_at, value = item
            if now - stored_at > INTERACTIVE_CADASTRAL_CACHE_SECONDS:
                self._interactive_cache.pop(key, None)
                return None
            return deepcopy(value)

    def _cache_put(self, key: str, value: CadastralObjectResult) -> None:
        if not value.cadastral_number or value.lat is None or value.lon is None:
            return
        with self._interactive_cache_lock:
            self._interactive_cache[key] = (time.monotonic(), deepcopy(value))
            while len(self._interactive_cache) > INTERACTIVE_CADASTRAL_CACHE_MAX_ENTRIES:
                self._interactive_cache.pop(next(iter(self._interactive_cache)))

    def _fill_result_address(
        self,
        result: CadastralObjectResult,
        *,
        fallback_address: str | None = None,
    ) -> CadastralObjectResult:
        if result.address:
            return result
        address = (fallback_address or "").strip() or None
        if address is None and result.lat is not None and result.lon is not None:
            try:
                address = PHOTON_GEOCODER.reverse_address(result.lat, result.lon)
            except GeoProviderUnavailable:
                address = None
        if address:
            result.address = address
            if not result.info.get("Адрес"):
                result.info["Адрес"] = address
        return result

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
        """Interactive cadastral-number lookup: memory cache -> NSPD Search -> IK12."""
        normalized = cadastral_number.replace(" ", "")
        cache_key = self._cache_key("cad", normalized)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        nspd_error: Exception | None = None
        try:
            nspd_result = self._search_nspd_geoportal(normalized, interactive=True)
        except (NSPDTLSVerificationError, GeoProviderUnavailable) as exc:
            nspd_error = exc
            nspd_result = None
            logger.warning(
                "NSPD unavailable for interactive cadastral search %s; trying bounded IK12 fallback: %s",
                normalized,
                exc,
            )

        if nspd_result and nspd_result.cadastral_number:
            nspd_result = self._fill_result_address(nspd_result)
            self._cache_put(cache_key, nspd_result)
            return nspd_result

        try:
            ik12_result = IK12_GEOCODER.search_by_cadastral_number(normalized)
        except GeoProviderUnavailable as exc:
            logger.warning("IK12 fallback failed for %s: %s", normalized, exc)
            ik12_result = None
        if ik12_result and ik12_result.cadastral_number:
            ik12_result = self._fill_result_address(ik12_result)
            self._cache_put(cache_key, ik12_result)
            return ik12_result

        if nspd_error is not None:
            error = (
                "НСПД временно недоступна через защищённое соединение, "
                "а резервный кадастровый источник не вернул объект."
            )
        else:
            error = "Объект не найден в доступных кадастровых источниках."

        return CadastralObjectResult(
            query=normalized,
            cadastral_number=normalized,
            source="nspd/ik12",
            confidence="none",
            error=error,
        )

    def search_by_address(self, address: str, *, allow_nominatim: bool = True) -> CadastralObjectResult:
        photon_error: GeoProviderUnavailable | None = None
        try:
            result = PHOTON_GEOCODER.geocode(address)
        except GeoProviderUnavailable as exc:
            photon_error = exc
            result = None
        source = "photon" if result else "nominatim"
        if not result and allow_nominatim:
            try:
                result = NOMINATIM_GEOCODER.geocode(address)
            except GeoProviderUnavailable:
                if photon_error is not None:
                    raise photon_error
                raise
        elif not result and photon_error is not None:
            raise photon_error

        if not result:
            return CadastralObjectResult(
                query=address,
                address=address,
                source=source,
                confidence="none",
                error="Адрес не найден",
            )

        return CadastralObjectResult(
            query=address,
            title="Адрес найден",
            address=result.get("matched_address") or address,
            lat=result.get("centroid_lat"),
            lon=result.get("centroid_lon"),
            source=source,
            confidence=result.get("geo_confidence", "medium"),
            info={
                "Адрес запроса": address,
                "Найденный адрес": result.get("matched_address") or address,
                "Источник": "Local Photon / OpenStreetMap" if source == "photon" else "Nominatim / OpenStreetMap",
                "Примечание": result.get("trace_reason", ""),
            },
        )

    def search_selected_address(self, address: str) -> CadastralObjectResult:
        """Compatibility wrapper for callers that still expect one selected-address result."""
        point = self.search_by_address(address, allow_nominatim=False)
        if point.lat is None or point.lon is None:
            return point
        try:
            objects = self.search_objects_by_point(
                point.lat,
                point.lon,
                fallback_address=point.address or address,
            )
        except (NSPDTLSVerificationError, GeoProviderUnavailable) as exc:
            logger.warning("NSPD point lookup failed for selected address '%s': %s", address, exc)
            return point
        return objects[0] if objects else point

    def search_objects_by_point(
        self,
        lat: float,
        lon: float,
        *,
        fallback_address: str | None = None,
    ) -> list[CadastralObjectResult]:
        """Return all cadastral buildings/land plots at a Photon-selected point via NSPD WMS."""
        require_provider("nspd", external=True)
        radius = 100.0
        mercator_radius = 6_378_137.0
        safe_lat = max(-85.05112878, min(85.05112878, float(lat)))
        x = mercator_radius * math.radians(float(lon))
        y = mercator_radius * math.log(
            math.tan(math.pi / 4.0 + math.radians(safe_lat) / 2.0)
        )
        bbox = f"{x-radius},{y-radius},{x+radius},{y+radius}"

        results: list[CadastralObjectResult] = []
        seen: set[str] = set()
        successful_layers = 0
        provider_errors: list[GeoProviderUnavailable] = []

        for kind, layer_id in NSPD_WMS_LAYERS:
            started = time.monotonic()
            headers = {
                "Referer": f"https://nspd.gov.ru/map?thematic=PKK&active_layers={layer_id}",
                "Origin": "https://nspd.gov.ru",
                "User-Agent": "Mozilla/5.0 BankrotAI/1.0",
                "Accept": "application/json,text/plain,*/*",
            }
            try:
                response = requests.get(
                    f"https://nspd.gov.ru/api/aeggis/v3/{layer_id}/wms",
                    params={
                        "REQUEST": "GetFeatureInfo",
                        "QUERY_LAYERS": layer_id,
                        "SERVICE": "WMS",
                        "VERSION": "1.3.0",
                        "FORMAT": "image/png",
                        "STYLES": "",
                        "LAYERS": layer_id,
                        "INFO_FORMAT": "application/json",
                        "FEATURE_COUNT": 25,
                        "I": 400,
                        "J": 400,
                        "WIDTH": 800,
                        "HEIGHT": 800,
                        "CRS": "EPSG:3857",
                        "BBOX": bbox,
                    },
                    headers=headers,
                    timeout=(1.5, 2.5),
                    verify=nspd_tls_verify(),
                )
                response.raise_for_status()
                payload = response.json()
                successful_layers += 1
                record_provider_success("nspd", latency_ms=(time.monotonic() - started) * 1000)
            except SSLError as exc:
                record_provider_failure("nspd", "tls_error", latency_ms=(time.monotonic() - started) * 1000)
                raise NSPDTLSVerificationError("NSPD WMS TLS certificate verification failed") from exc
            except requests.RequestException as exc:
                category = classify_transport_exception(exc)
                record_provider_failure("nspd", category, latency_ms=(time.monotonic() - started) * 1000)
                provider_errors.append(GeoProviderUnavailable("nspd", category, str(exc)))
                continue
            except (ValueError, AttributeError) as exc:
                record_provider_failure("nspd", "provider_protocol", latency_ms=(time.monotonic() - started) * 1000)
                provider_errors.append(GeoProviderUnavailable("nspd", "provider_protocol", str(exc)))
                continue

            for feature in payload.get("features") or []:
                props = feature.get("properties") or {}
                info = normalize_nspd_props(props, "")
                number = str(info.get("Кадастровый номер") or "").replace(" ", "")
                if not CADASTRAL_RE.match(number) or number in seen:
                    continue

                geometry = geometry_to_wgs84(feature.get("geometry"))
                geometry_json = geometry if geometry and geometry.get("type") != "Point" else None
                feature_lat, feature_lon = centroid_from_geometry(geometry)
                if kind == "building" and not info.get("Вид объекта недвижимости"):
                    info["Вид объекта недвижимости"] = "Здание"
                elif kind == "land_plot" and not info.get("Вид объекта недвижимости"):
                    info["Вид объекта недвижимости"] = "Земельный участок"
                if fallback_address and not info.get("Адрес"):
                    info["Адрес"] = fallback_address

                seen.add(number)
                results.append(
                    CadastralObjectResult(
                        query=fallback_address or number,
                        cadastral_number=number,
                        object_type=info.get("Вид объекта недвижимости"),
                        title=info.get("Наименование") or info.get("Назначение") or info.get("Вид объекта недвижимости"),
                        address=info.get("Адрес") or fallback_address,
                        lat=feature_lat if feature_lat is not None else float(lat),
                        lon=feature_lon if feature_lon is not None else float(lon),
                        geometry_json=geometry_json,
                        has_boundary=bool(geometry_json),
                        source="nspd_wms",
                        confidence="high",
                        info=info,
                        raw=feature,
                    )
                )

        if results:
            return results
        if successful_layers == 0 and provider_errors:
            raise provider_errors[0]
        return []

    def _search_nspd_by_point(
        self,
        lat: float,
        lon: float,
        *,
        fallback_address: str | None = None,
    ) -> CadastralObjectResult | None:
        """Backward-compatible single-result NSPD WMS lookup."""
        results = self.search_objects_by_point(
            lat,
            lon,
            fallback_address=fallback_address,
        )
        return results[0] if results else None

    def _search_pkk_by_point(
        self,
        lat: float,
        lon: float,
        *,
        fallback_address: str | None = None,
    ) -> CadastralObjectResult | None:
        """Bounded legacy PKK fallback for a user-selected address point."""
        if not self._circuit_available("pkk"):
            return None
        with self._pkk_request_lock:
            if not self._circuit_available("pkk"):
                return None
            for kind, feature_type in (("building", 5), ("land_plot", 1)):
                try:
                    response = requests.get(
                        f"{self.base_url}/{feature_type}",
                        params={
                            "text": f"{float(lat)} {float(lon)}",
                            "limit": 10,
                            "tolerance": 2,
                        },
                        timeout=(0.75, 1.25),
                    )
                    response.raise_for_status()
                    data = response.json()
                except requests.RequestException as exc:
                    self._open_circuit("pkk")
                    logger.warning(
                        "PKK point lookup failed for %.6f, %.6f: %s",
                        lat,
                        lon,
                        exc,
                    )
                    return None
                except (ValueError, AttributeError) as exc:
                    logger.warning(
                        "PKK point response failed for %.6f, %.6f: %s",
                        lat,
                        lon,
                        exc,
                    )
                    return None

                features = data.get("features") or []
                for feature in features:
                    attrs = feature.get("attrs") or {}
                    number = str(
                        attrs.get("cn")
                        or attrs.get("cad_num")
                        or attrs.get("cadastralNumber")
                        or ""
                    ).replace(" ", "")
                    if not CADASTRAL_RE.match(number):
                        continue
                    info = normalize_pkk_attrs(attrs, number, kind)
                    if fallback_address and not info.get("Адрес"):
                        info["Адрес"] = fallback_address
                    geometry = geometry_to_wgs84(feature.get("geometry"))
                    geometry_json = geometry if geometry and geometry.get("type") != "Point" else None
                    return CadastralObjectResult(
                        query=fallback_address or number,
                        cadastral_number=number,
                        object_type=info.get("Вид объекта недвижимости"),
                        title=info.get("Наименование") or info.get("Назначение") or info.get("Вид объекта недвижимости"),
                        address=info.get("Адрес") or fallback_address,
                        lat=float(lat),
                        lon=float(lon),
                        geometry_json=geometry_json,
                        has_boundary=bool(geometry_json),
                        source="pkk_point",
                        confidence="high",
                        info=info,
                        raw=feature,
                    )
        return None

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

    def _search_nspd_geoportal(self, query: str, *, interactive: bool = False) -> CadastralObjectResult | None:
        require_provider("nspd", external=True)
        started = time.monotonic()
        headers = {
            "Referer": NSPD_REFERER,
            "User-Agent": "Mozilla/5.0 BankrotAI/1.0",
            "Accept": "application/json,text/plain,*/*",
        }
        params: dict[str, str | int] = {
            "thematicSearchId": 1,
            "query": query,
        }

        if not interactive and not self._circuit_available("nspd"):
            return None
        with self._nspd_request_lock:
            if not interactive and not self._circuit_available("nspd"):
                return None
            attempts = 2 if interactive else 1
            request_timeout = (1.0, 1.75) if interactive else CADASTRAL_REQUEST_TIMEOUT
            data = None
            for attempt in range(attempts):
                started = time.monotonic()
                try:
                    resp = requests.get(
                        self.nspd_search_url,
                        params=params,
                        headers=headers,
                        timeout=request_timeout,
                        verify=nspd_tls_verify(),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    self._nspd_disabled_until = 0.0
                    record_provider_success("nspd", latency_ms=(time.monotonic() - started) * 1000)
                    break
                except SSLError as e:
                    record_provider_failure("nspd", "tls_error", latency_ms=(time.monotonic() - started) * 1000)
                    if interactive and attempt + 1 < attempts:
                        time.sleep(0.1)
                        continue
                    self._open_circuit("nspd")
                    logger.error("NSPD TLS verification failed for %s: %s", query, e)
                    raise NSPDTLSVerificationError("NSPD TLS certificate verification failed") from e
                except requests.RequestException as e:
                    category = classify_transport_exception(e)
                    record_provider_failure("nspd", category, latency_ms=(time.monotonic() - started) * 1000)
                    if interactive and attempt + 1 < attempts:
                        time.sleep(0.1)
                        continue
                    self._open_circuit("nspd")
                    logger.warning("NSPD request failed for %s; pausing batch NSPD requests: %s", query, e)
                    raise GeoProviderUnavailable("nspd", category, str(e)) from e
                except Exception as e:
                    record_provider_failure("nspd", "provider_protocol", latency_ms=(time.monotonic() - started) * 1000)
                    logger.warning("NSPD response failed for %s: %s", query, e)
                    raise GeoProviderUnavailable("nspd", "provider_protocol", str(e)) from e
            if data is None:
                return None

        features = (data.get("data") or {}).get("features") or data.get("features") or []
        if not features:
            return None

        feature = self._pick_nspd_feature(features, query)
        props = feature.get("properties") or {}
        expected_cadastral = query if CADASTRAL_RE.match(query.replace(" ", "")) else ""
        info = normalize_nspd_props(props, expected_cadastral)
        observed_text = str(info.get("Кадастровый номер") or "").replace(" ", "")
        observed_cadastral: str | None = (
            observed_text
            if CADASTRAL_RE.match(observed_text)
            else (expected_cadastral or None)
        )
        info["Кадастровый номер"] = observed_cadastral
        geometry = geometry_to_wgs84(feature.get("geometry"))
        lat, lon = centroid_from_geometry(geometry)

        if lat is None or lon is None:
            return CadastralObjectResult(
                query=query,
                cadastral_number=observed_cadastral,
                object_type=info.get("Вид объекта недвижимости"),
                title=info.get("Наименование") or info.get("Назначение") or info.get("Вид объекта недвижимости"),
                address=info.get("Адрес"),
                source="nspd",
                confidence="low",
                info=info,
                raw=feature,
                error="Объект найден в НСПД, но координаты не получены",
            )

        geometry_json = geometry if geometry and geometry.get("type") != "Point" else None
        return CadastralObjectResult(
            query=query,
            cadastral_number=observed_cadastral,
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
    address_is_incomplete = is_incomplete_address(value)
    if (not value or address_is_incomplete) and (title or description):
        # Keep this fallback aligned with extractors.extract_address so that
        # already imported shallow LOT-ONLINE cards can be geocoded without a
        # full re-import.
        from bankrotai.extractors import extract_address

        source_text = " ".join(part for part in (title, description) if part)
        extracted = extract_best_numbered_address(source_text) or extract_address(source_text) or ""
        if extracted:
            value = extracted
    if not value:
        return []

    value = re.sub(r"\.{3,}$", "", value)
    value = re.sub(
        r",?\s*(?:с|вместе\s+с)\s+земельн(?:ым|ого)\s+участк(?:ом|а).*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    # Source cards frequently append legal prose to the address. Large queries
    # make every Photon miss consume the full timeout and reduce match quality.
    value = re.split(
        r"(?:[,.;]\s*|—\s*)(?:зарегистрированные\s+обременения|"
        r"разрешенный\s+вид\s+использования|вид\s+разрешенного\s+использования|"
        r"категория\s+земель|общей\s+площадью|территориальная\s+зона|"
        r"отправив\s+предварительно\s+запрос|направив\s+предварительно\s+запрос|"
        r"осмотр\s+имущества|порядок\s+ознакомления)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    # Photon receives the query in the URL. Even malformed source records must
    # never be able to produce a 414 response or multi-kilobyte log entry.
    value = value[:512]
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
    value = re.sub(
        r"\b\u0434\.\s*(?=[\u0410-\u042f\u0401A-Z])",
        "\u0434\u0435\u0440\u0435\u0432\u043d\u044f ", value, flags=re.IGNORECASE,
    )
    replacements = (
        (r"\bобл\.(?=\s|,|$)", "область"),
        (r"\bг\.(?=\s)", "город "),
        (r"\bп\.(?=\s)", "поселок "),
        (r"\bс\.(?=\s)", "село "),
        (r"\bд\.(?=\s)", "дом "),
        (r"\bул\.(?=\s)", "улица "),
        (r"\bкорп\.(?=\s*\w)", "корпус "),
        (r"\bстр\.(?=\s*\w)", "строение "),
        (r"\bвл\.(?=\s*\w)", "владение "),
        (r"\bлит\.(?=\s*\w)", "литера "),
        (r"\bпр-т\b", "проспект"),
        (r"\bпер\.(?=\s)", "переулок "),
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
    localities = [
        part
        for part in parts
        if re.search(
            r"\b(?:город|село|поселок|деревня|рабочий поселок)\b",
            part,
            re.IGNORECASE,
        )
    ]
    locality = localities[-1] if localities else ""
    locality_query = re.sub(
        r"^(?:город|село|поселок|деревня|рабочий поселок)\s+",
        "",
        locality,
        flags=re.IGNORECASE,
    )
    street = next(
        (
            part
            for part in parts
            if re.search(
                r"\b(?:улица|проспект|шоссе|переулок|проезд|набережная|площадь|тракт)\b",
                part,
                re.I,
            )
        ),
        "",
    )
    house = next(
        (
            part
            for part in parts
            if re.search(r"\b(?:дом|владение)\s*\d", part, re.IGNORECASE)
        ),
        "",
    )
    house_number = re.sub(r"^(?:дом|владение)\s*", "", house, flags=re.IGNORECASE) if house else ""

    candidates = [value]
    unit_stripped = re.sub(
        r",?\s*(?:помещение|пом\.|квартира|кв\.|комната|офис)\s*[№#]?\s*[\w/-]+.*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip(" ,.;")
    if unit_stripped and unit_stripped.casefold() != value.casefold():
        candidates.append(unit_stripped)
    if street and locality_query:
        candidates.append(", ".join(part for part in (house_number, street, locality_query, district, region) if part))
    if locality_query and district:
        candidates.append(", ".join(part for part in (locality_query, district, region) if part))
    if locality_query and region:
        candidates.append(f"{locality_query}, {region}")

    # Auction cards frequently keep a vague address in the structured field
    # while the exact street/house appears only in title or description.
    # Preserve the structured address as the first choice, but surface one
    # exact numbered supplemental candidate for the local Photon fallback.
    source_text = " ".join(part for part in (title, description) if part)
    supplemental = extract_best_numbered_address(source_text) if source_text else None
    if supplemental:
        supplemental = supplemental.strip(" ,.;")
        if region_name and region_name.casefold() not in supplemental.casefold():
            supplemental = f"{supplemental}, {region_name}"
        candidates.append(supplemental)

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = " ".join(candidate.split()).strip(" ,")
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def extract_best_numbered_address(text: str | None) -> str | None:
    """Prefer an address that contains a real house number over an earlier placeholder."""
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(?:г\.|город)\s*[^,.;]+,\s*"
        r"(?:ул\.|улица|проспект|пр-т|пер\.)\s*[^,.;]+,\s*"
        r"(?:д\.|дом)\s*[0-9][^,.;]*",
        re.IGNORECASE,
    )
    matches = [match.group(0).strip(" ,.;") for match in pattern.finditer(compact)]
    return max(matches, key=len) if matches else None


def is_incomplete_address(value: str | None) -> bool:
    return bool(
        value
        and re.search(
            r"\b(?:д|дом)\.?\s*(?:помещени[ея]|объект[а-я]*|имуще(?:ство|ства))\b",
            value,
            re.IGNORECASE,
        )
    )


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
        require_provider("nominatim", external=True)
        transport_successes = 0
        operational_failures = 0
        last_operational_category = "connection_error"

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
            "россия",
            "область",
            "области",
            "муниципальный",
            "округ",
            "район",
            "город",
            "село",
            "поселок",
            "деревня",
            "улица",
            "проспект",
            "дом",
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
            started = time.monotonic()
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
                transport_successes += 1
                record_provider_success("nominatim", latency_ms=(time.monotonic() - started) * 1000)
                if not data:
                    continue
                scored = [
                    (len(expected_tokens & match_tokens(str(item.get("display_name") or ""))), item) for item in data
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
            except requests.RequestException as e:
                operational_failures += 1
                last_operational_category = classify_transport_exception(e)
                record_provider_failure("nominatim", last_operational_category, latency_ms=(time.monotonic() - started) * 1000)
                logger.warning("Nominatim geocoding failed for '%s': %s", candidate, e)
            except (ValueError, TypeError, KeyError) as e:
                operational_failures += 1
                last_operational_category = "provider_protocol"
                record_provider_failure("nominatim", last_operational_category, latency_ms=(time.monotonic() - started) * 1000)
                logger.warning("Nominatim response failed for '%s': %s", candidate, e)

        if value is None and operational_failures and transport_successes == 0:
            with self._lock:
                completed_event = self._inflight.pop(cache_key, None)
                if completed_event:
                    completed_event.set()
            raise GeoProviderUnavailable("nominatim", last_operational_category)

        with self._lock:
            self._cache[cache_key] = value
            if len(self._cache) > 10_000:
                self._cache.pop(next(iter(self._cache)))
            completed_event = self._inflight.pop(cache_key, None)
            if completed_event:
                completed_event.set()
        return dict(value) if value else None


NOMINATIM_GEOCODER = NominatimGeocoder()


def expected_locality_name(address: str | None) -> str:
    if not address:
        return ""
    matches = list(re.finditer(
        r"(?:^|[,;]\s*)(?:\u0433\.|\u0433\u043e\u0440\u043e\u0434|\u0434\u0435\u0440\u0435\u0432\u043d\u044f|\u0441\u0435\u043b\u043e|\u043f\u043e\u0441\u0435\u043b\u043e\u043a)\s*([^,;]+)",
        address, re.IGNORECASE,
    ))
    abbreviated = list(re.finditer(
        r"(?:^|[,;]\s*)\u0434\.\s*([\u0410-\u042f\u0401A-Z][^,;]*)",
        address, re.IGNORECASE,
    ))
    candidates = [*matches, *abbreviated]
    if not candidates:
        return ""
    match = max(candidates, key=lambda item: item.start())
    return match.group(1).strip().casefold()


class PhotonGeocoder:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url if base_url is not None else os.getenv("PHOTON_BASE_URL", "")).rstrip("/")

    @staticmethod
    def _feature_address_label(feature: dict[str, Any]) -> str:
        props = feature.get("properties") or {}
        locality = (
            props.get("city")
            or props.get("town")
            or props.get("village")
            or props.get("hamlet")
            or props.get("locality")
            or props.get("district")
            or props.get("municipality")
        )
        street = props.get("street")
        house = props.get("housenumber")
        name = props.get("name")
        state = props.get("state")
        parts: list[str] = []
        if locality:
            parts.append(str(locality))
        if street:
            parts.append(str(street))
        elif name and str(name) != str(locality or ""):
            parts.append(str(name))
        if house:
            parts.append(f"д {house}")
        if state and str(state) not in parts:
            parts.append(str(state))
        return ", ".join(part for part in parts if part).strip()

    def suggest_addresses(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Return deterministic address candidates for the interactive cadastre search."""
        value = (query or "").strip()
        if not self.base_url or len(value) < 3:
            return []
        require_provider("photon", external=False)
        started = time.monotonic()
        try:
            response = requests.get(
                f"{self.base_url}/api",
                params={"q": value, "limit": max(1, min(int(limit), 20)), "lang": "ru", "countrycode": "RU"},
                timeout=(2, 5),
            )
            response.raise_for_status()
            features = response.json().get("features") or []
            record_provider_success("photon", latency_ms=(time.monotonic() - started) * 1000)
        except requests.RequestException as exc:
            category = classify_transport_exception(exc)
            record_provider_failure("photon", category, latency_ms=(time.monotonic() - started) * 1000)
            raise GeoProviderUnavailable("photon", category, str(exc)) from exc
        except (ValueError, AttributeError) as exc:
            record_provider_failure("photon", "provider_protocol", latency_ms=(time.monotonic() - started) * 1000)
            raise GeoProviderUnavailable("photon", "provider_protocol", str(exc)) from exc

        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for feature in features:
            label = self._feature_address_label(feature)
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if not label or len(coordinates) < 2:
                continue
            key = " ".join(label.casefold().split())
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "label": label,
                    "lat": float(coordinates[1]),
                    "lon": float(coordinates[0]),
                }
            )
            if len(result) >= limit:
                break
        return result

    def reverse_address(self, lat: float, lon: float) -> str | None:
        """Return a local Photon address label for already known coordinates."""
        if not self.base_url:
            return None
        require_provider("photon", external=False)
        started = time.monotonic()
        try:
            response = requests.get(
                f"{self.base_url}/reverse",
                params={"lat": float(lat), "lon": float(lon), "lang": "ru"},
                timeout=(1, 3),
            )
            response.raise_for_status()
            features = response.json().get("features") or []
            record_provider_success("photon", latency_ms=(time.monotonic() - started) * 1000)
        except requests.RequestException as exc:
            category = classify_transport_exception(exc)
            record_provider_failure("photon", category, latency_ms=(time.monotonic() - started) * 1000)
            logger.warning("Local Photon reverse geocoding failed for %.6f, %.6f: %s", lat, lon, exc)
            return None
        except (ValueError, AttributeError) as exc:
            record_provider_failure("photon", "provider_protocol", latency_ms=(time.monotonic() - started) * 1000)
            logger.warning("Local Photon reverse response failed for %.6f, %.6f: %s", lat, lon, exc)
            return None

        for feature in features:
            label = self._feature_address_label(feature)
            if label:
                return label
        return None

    def geocode(self, address: str) -> dict[str, Any] | None:
        if not self.base_url or len(address.strip()) < 5:
            return None
        require_provider("photon", external=False)
        operational_failures = 0
        transport_successes = 0
        last_operational_category = "connection_error"
        expected = {token[:7] for token in re.findall(r"[а-яёa-z-]{5,}", address.casefold())}
        street_match = re.search(
            r"(?:^|[,;]\s*)(?:ул\.|улица|проспект|пр-т|переулок|пер\.|шоссе|проезд|набережная|площадь|тракт)\s*([^,;]+)",
            address,
            re.IGNORECASE,
        )
        expected_street = street_match.group(1).strip().casefold() if street_match else ""
        house_match = re.search(r"(?:д\.|дом|вл\.|владение)\s*([0-9]+[а-яa-z]?)", address, re.IGNORECASE)

        def normalize_house(value: str) -> str:
            confusables = str.maketrans(
                {
                    "а": "a",
                    "в": "b",
                    "с": "c",
                    "е": "e",
                    "х": "x",
                    "к": "k",
                    "м": "m",
                    "н": "h",
                    "о": "o",
                    "р": "p",
                    "т": "t",
                }
            )
            return re.sub(r"\s+", "", value.casefold()).translate(confusables).split("/", 1)[0]

        expected_house = normalize_house(house_match.group(1)) if house_match else ""
        from bankrotai.regions import normalize_region_code, region_code_from_text

        expected_region = region_code_from_text(address) or normalize_region_code(address)
        expected_locality = expected_locality_name(address)
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for query_index, candidate in enumerate(build_geocoding_address_candidates(address)):
            exact_candidate_found = False
            started = time.monotonic()
            try:
                response = requests.get(
                    f"{self.base_url}/api",
                    params={"q": candidate, "limit": 10, "lang": "ru", "countrycode": "RU"},
                    timeout=(2, 5),
                )
                response.raise_for_status()
                features = response.json().get("features") or []
                transport_successes += 1
                record_provider_success("photon", latency_ms=(time.monotonic() - started) * 1000)
            except requests.RequestException as exc:
                operational_failures += 1
                last_operational_category = classify_transport_exception(exc)
                record_provider_failure("photon", last_operational_category, latency_ms=(time.monotonic() - started) * 1000)
                logger.warning("Local Photon geocoding failed for '%s': %s", candidate, exc)
                continue
            except (ValueError, AttributeError) as exc:
                operational_failures += 1
                last_operational_category = "provider_protocol"
                record_provider_failure("photon", last_operational_category, latency_ms=(time.monotonic() - started) * 1000)
                logger.warning("Local Photon response failed for '%s': %s", candidate, exc)
                continue
            for feature in features:
                props = feature.get("properties") or {}
                place_name = props.get("name") if props.get("osm_key") == "place" else None
                locality_values = [
                    props.get("city"),
                    props.get("town"),
                    props.get("village"),
                    props.get("hamlet"),
                    props.get("locality"),
                    props.get("district"),
                    props.get("county"),
                    props.get("municipality"),
                    place_name,
                ]
                locality_text = " ".join(str(value) for value in locality_values if value).casefold()
                city = str(
                    props.get("city")
                    or props.get("town")
                    or props.get("village")
                    or props.get("hamlet")
                    or props.get("locality")
                    or props.get("district")
                    or place_name
                    or ""
                )
                state = str(props.get("state") or "")
                street = str(props.get("street") or "")
                house = normalize_house(str(props.get("housenumber") or ""))
                if expected_locality and expected_locality not in locality_text:
                    continue
                if expected_street and street and expected_street not in street.casefold():
                    continue
                if expected_region and state and normalize_region_code(state) != expected_region:
                    continue
                if expected_house and house and house != expected_house:
                    continue
                haystack = " ".join(str(value) for value in props.values()).casefold()
                score = sum(token in haystack for token in expected)
                score += 4 if expected_locality and expected_locality in locality_text else 0
                score += 3 if expected_street and expected_street in street.casefold() else 0
                score += 2 if expected_region and normalize_region_code(state) == expected_region else 0
                score += 2 if expected_house and house == expected_house else 0
                scored.append((score, -query_index, feature))
                exact_candidate_found = bool(
                    expected_locality
                    and expected_locality in locality_text
                    and (not expected_street or expected_street in street.casefold())
                    and (not expected_region or normalize_region_code(state) == expected_region)
                    and (not expected_house or house == expected_house)
                )
            if exact_candidate_found:
                break
        if not scored:
            if operational_failures and transport_successes == 0:
                raise GeoProviderUnavailable("photon", last_operational_category)
            return None
        score, _query_rank, feature = max(scored, key=lambda item: (item[0], item[1]))
        if expected and score < min(2, len(expected)):
            return None
        coordinates = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coordinates) < 2:
            return None
        props = feature.get("properties") or {}
        matched_address = ", ".join(
            str(value)
            for value in (
                props.get("city")
                or props.get("town")
                or props.get("village")
                or props.get("hamlet")
                or props.get("locality")
                or props.get("district")
                or props.get("name"),
                props.get("street"),
                props.get("housenumber"),
                props.get("state"),
            )
            if value
        )
        return {
            "centroid_lat": float(coordinates[1]),
            "centroid_lon": float(coordinates[0]),
            "geo_confidence": "high" if score >= 3 else "medium",
            "trace_reason": "Local Photon / OpenStreetMap",
            "matched_address": matched_address or None,
        }


PHOTON_GEOCODER = PhotonGeocoder()
CADASTRAL_GEOCODER = CadastralGeocoder()
IK12_GEOCODER = IK12Geocoder()


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
        for source in (options, props):
            for key in keys:
                val = source.get(key) if isinstance(source, dict) else None
                if val not in (None, ""):
                    return val
        return None

    observed_number = pick(
        "cad_num",
        "cadNum",
        "cadastralNumber",
        "cadastral_number",
        "cn",
        "label",
        "descr",
    )
    observed_number = str(observed_number or "").replace(" ", "")
    if not CADASTRAL_RE.match(observed_number):
        observed_number = cadastral_number or None

    object_name = pick("name", "objectName", "object_name")
    if object_name is not None and observed_number and str(object_name).replace(" ", "") == observed_number:
        object_name = None

    object_type = pick(
        "categoryName",
        "category_name",
        "objectType",
        "typeName",
        "type",
        "land_record_type",
        "build_record_type",
        "construction_record_type",
    )

    return {
        "Вид объекта недвижимости": object_type,
        "Дата присвоения": pick(
            "date_create",
            "assignDate",
            "assign_date",
            "cadRecordDate",
            "cad_record_date",
            "registration_date",
        ),
        "Кадастровый номер": observed_number,
        "Кадастровый квартал": pick(
            "quarter",
            "cadQuarter",
            "cad_quarter",
            "kvartal",
            "quarter_cad_number",
        ),
        "Адрес": pick(
            "readable_address",
            "readableAddress",
            "address",
            "object_address",
            "address_note",
            "location",
            "addr",
        ),
        "Наименование": object_name,
        "Назначение": pick(
            "purpose",
            "util_by_doc",
            "assignation",
            "building_purpose",
            "purpose_name",
        ),
        "Площадь общая": pick(
            "specified_area",
            "declared_area",
            "area",
            "area_value",
            "readableArea",
            "readable_area",
        ),
        "Статус": pick(
            "status",
            "state",
            "readableStatus",
            "readable_status",
            "cadRecordStatus",
            "cad_record_status",
        ),
        "Форма собственности": pick("ownership", "ownershipType", "ownership_type", "right_type", "fp"),
        "Кадастровая стоимость": pick(
            "cost_value",
            "cad_cost",
            "cadCost",
            "cost",
            "readableCadCost",
            "readable_cad_cost",
        ),
        "Удельный показатель кадастровой стоимости": pick(
            "cost_value_per_square_meter",
            "ud_cost",
            "unitCost",
            "unit_cost",
        ),
        "Количество этажей": pick("floors", "floorCount", "floor_count"),
        "Количество подземных этажей": pick(
            "undergroundFloors",
            "underground_floors",
            "underground_floor_count",
        ),
        "Материал стен": pick("wallMaterial", "wall_material"),
        "Завершение строительства": pick("yearBuilt", "year_built", "buildYear", "build_year"),
        "Ввод в эксплуатацию": pick(
            "commissioningYear",
            "commissioning_year",
            "year_commissioning",
        ),
        "ОКН": pick("culturalHeritage", "cultural_heritage", "heritage", "oks_flag"),
        "Без координат границ": pick("no_coords"),
        "Категория НСПД": pick("category", "categoryId", "category_id"),
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
    cadastral_numbers: list[str] | None = None,
    title: str | None = None,
    description: str | None = None,
    region_name: str | None = None,
    region_code: str | None = None,
    bulk: bool = False,
) -> CadastralObjectResult | None:
    attempts: list[dict[str, Any]] = []
    cadastral_candidates: list[str] = []
    seen_cadastral: set[str] = set()
    for raw in [cadastral_number, *(cadastral_numbers or [])]:
        normalized = re.sub(r"\s+", "", str(raw or ""))
        if not normalized or normalized in seen_cadastral:
            continue
        if raw != cadastral_number and not CADASTRAL_RE.match(normalized):
            continue
        seen_cadastral.add(normalized)
        cadastral_candidates.append(normalized)
        if len(cadastral_candidates) >= 5:
            break

    address_candidates = build_geocoding_address_candidates(
        address,
        title=title,
        description=description,
        region_name=region_name,
    )
    provider_address_fallbacks: list[str] = []

    def remember_provider_address(
        result: CadastralObjectResult | None,
        expected_cadastral_number: str | None,
    ) -> None:
        if result is None or not result.address:
            return
        expected = re.sub(r"\s+", "", str(expected_cadastral_number or ""))
        observed = re.sub(r"\s+", "", str(result.cadastral_number or ""))
        if expected and observed and expected != observed:
            return
        for candidate in build_geocoding_address_candidates(
            result.address,
            region_name=region_name,
        )[:2]:
            key = candidate.casefold()
            if key not in {item.casefold() for item in provider_address_fallbacks}:
                provider_address_fallbacks.append(candidate)

    def note_operational(
        exc: GeoProviderUnavailable,
        provider: str,
        *,
        candidate_index: int | None = None,
    ) -> None:
        attempt: dict[str, Any] = {
            "source": provider,
            "valid": False,
            "reason": exc.category,
            "operational": True,
        }
        if candidate_index is not None:
            attempt["candidate_index"] = candidate_index
        attempts.append(attempt)

    def accept(
        result: CadastralObjectResult | None,
        provider: str,
        *,
        expected_cadastral_number: str | None = None,
        candidate_index: int | None = None,
        validation_address: str | None = None,
    ) -> CadastralObjectResult | None:
        valid, reason = validate_geocoding_result(
            result,
            cadastral_number=expected_cadastral_number or cadastral_number,
            address=validation_address if validation_address is not None else address,
            region_name=region_name,
            region_code=region_code,
        )
        attempt: dict[str, Any] = {"source": provider, "valid": valid, "reason": reason}
        if candidate_index is not None:
            attempt["candidate_index"] = candidate_index
        attempts.append(attempt)
        if result is not None:
            result.attempts = list(attempts)
            if valid and not result.address and address_candidates:
                result.address = address_candidates[0]
        return result if valid else None

    for candidate_index, candidate in enumerate(cadastral_candidates):
        nspd_source = "nspd_cadastral" if candidate_index == 0 else "nspd_cadastral_alt"
        try:
            nspd_candidate = CADASTRAL_GEOCODER._search_nspd_geoportal(candidate)
        except GeoProviderUnavailable as exc:
            note_operational(exc, nspd_source, candidate_index=candidate_index)
            nspd_candidate = None
            nspd_result = None
        except NSPDTLSVerificationError as exc:
            note_operational(
                GeoProviderUnavailable("nspd", "tls_error", str(exc)),
                nspd_source,
                candidate_index=candidate_index,
            )
            nspd_candidate = None
            nspd_result = None
        else:
            nspd_result = accept(
                nspd_candidate,
                nspd_source,
                expected_cadastral_number=candidate,
                candidate_index=candidate_index,
            )
            if not nspd_result:
                remember_provider_address(nspd_candidate, candidate)
        if nspd_result:
            return nspd_result
        if not bulk or get_settings().geo_bulk_ik12_fallback:
            ik12_source = "ik12_cadastral" if candidate_index == 0 else "ik12_cadastral_alt"
            try:
                ik12_candidate = IK12_GEOCODER.search_by_cadastral_number(candidate)
            except GeoProviderUnavailable as exc:
                note_operational(exc, ik12_source, candidate_index=candidate_index)
                ik12_result = None
            else:
                ik12_result = accept(
                    ik12_candidate,
                    ik12_source,
                    expected_cadastral_number=candidate,
                    candidate_index=candidate_index,
                )
                if not ik12_result:
                    remember_provider_address(ik12_candidate, candidate)
            if ik12_result:
                return ik12_result

    # Local Photon is the cheap bulk address provider. The candidate builder
    # already produces progressively simpler/structured variants, but the bulk
    # resolver historically discarded all except the first one. Try a small,
    # bounded set so malformed auction-card prose does not strand an otherwise
    # geocodable lot. Nominatim remains disabled in bulk unless explicitly
    # configured, so this adds no public-provider fan-out.
    address_attempt_limit = 3 if bulk else 1
    address_attempts: list[str] = []
    for candidate in [*provider_address_fallbacks, *address_candidates]:
        if candidate.casefold() in {item.casefold() for item in address_attempts}:
            continue
        address_attempts.append(candidate)
        if len(address_attempts) >= address_attempt_limit:
            break
    provider_address_keys = {item.casefold() for item in provider_address_fallbacks}
    source_text = " ".join(part for part in (title, description) if part)
    supplemental = extract_best_numbered_address(source_text) if source_text else None
    if supplemental:
        supplemental = supplemental.strip(" ,.;")
        if region_name and region_name.casefold() not in supplemental.casefold():
            supplemental = f"{supplemental}, {region_name}"
        if supplemental.casefold() not in {item.casefold() for item in address_attempts}:
            if len(address_attempts) < address_attempt_limit:
                address_attempts.append(supplemental)
            elif bulk and address_attempts:
                # In bulk mode a numbered address extracted from the card is
                # more specific than the least-preferred generic candidate.
                address_attempts[-1] = supplemental

    for candidate_index, candidate in enumerate(address_attempts):
        if candidate.casefold() in provider_address_keys:
            address_source = "address_geocoder_cadastral_hint"
        else:
            address_source = "address_geocoder" if candidate_index == 0 else "address_geocoder_alt"
        try:
            addr_result = CADASTRAL_GEOCODER.search_by_address(
                candidate,
                allow_nominatim=not bulk or get_settings().geo_bulk_nominatim_fallback,
            )
        except GeoProviderUnavailable as exc:
            note_operational(exc, address_source, candidate_index=candidate_index)
            accepted = None
        else:
            accepted = accept(
                addr_result,
                address_source,
                expected_cadastral_number=cadastral_candidates[0] if cadastral_candidates else cadastral_number,
                candidate_index=candidate_index,
                validation_address=candidate,
            )
        if accepted:
            return accepted

    operational = any(bool(attempt.get("operational")) for attempt in attempts)
    return CadastralObjectResult(
        query=(cadastral_candidates[0] if cadastral_candidates else cadastral_number)
        or (address_candidates[0] if address_candidates else ""),
        cadastral_number=cadastral_candidates[0] if cadastral_candidates else cadastral_number,
        source="geocoding_chain",
        confidence="none",
        status="GEOCODING_DEGRADED" if operational else "GEOCODING_FAILED",
        attempts=attempts,
        error="Operational geocoding dependency degraded" if operational else "No validated geocoding result",
    )


def validate_geocoding_result(
    result: CadastralObjectResult | None,
    *,
    cadastral_number: str | None,
    address: str | None,
    region_name: str | None,
    region_code: str | None = None,
) -> tuple[bool, str]:
    if result is None or result.lat is None or result.lon is None:
        return False, "no_coordinates"
    if not (-90 <= result.lat <= 90 and -180 <= result.lon <= 180):
        return False, "coordinates_out_of_range"
    if result.confidence in {"none", "low", "unknown"}:
        return False, "low_confidence"
    expected_cad = (cadastral_number or "").replace(" ", "")
    observed_cad = (result.cadastral_number or "").replace(" ", "")
    if expected_cad and observed_cad and expected_cad != observed_cad:
        return False, "cadastral_number_mismatch"

    expected_region = expected_cad.split(":", 1)[0].zfill(2) if ":" in expected_cad else None
    if expected_region and address:
        from bankrotai.regions import region_code_from_text

        address_region = region_code_from_text(address)
        if address_region and expected_region != address_region:
            return False, "address_cadastral_region_mismatch"
    if expected_region and result.address:
        from bankrotai.regions import region_code_from_text

        observed_region = region_code_from_text(result.address)
        if observed_region and expected_region != observed_region:
            return False, "result_cadastral_region_mismatch"
    from bankrotai.regions import normalize_region_code

    if region_code and normalize_region_code(region_code) is None:
        return False, "unsupported_region_code"
    canonical_region = normalize_region_code(region_code)
    named_region = None
    if region_name:
        from bankrotai.regions import region_code_from_text

        named_region = normalize_region_code(region_name)
        if expected_region and named_region and expected_region != named_region:
            return False, "region_cadastral_mismatch"
        if named_region and result.address:
            result_region = region_code_from_text(result.address)
            if result_region and result_region != named_region:
                return False, "result_region_mismatch"
    if expected_region and canonical_region and expected_region != canonical_region:
        return False, "region_cadastral_mismatch"
    if named_region and canonical_region and named_region != canonical_region:
        return False, "region_code_name_mismatch"

    expected_text = " ".join(part for part in (address, region_name) if part).casefold()
    observed_text = (result.address or "").casefold()
    expected_locality = expected_locality_name(address)
    if expected_locality and observed_text and expected_locality not in observed_text:
        return False, "locality_name_mismatch"
    for city_key, (city_lat, city_lon, max_distance) in CITY_SANITY_ANCHORS.items():
        if city_key in expected_text:
            if distance_between(result.lat, result.lon, city_lat, city_lon) > max_distance:
                return False, "city_distance_mismatch"
            if observed_text and city_key not in observed_text:
                return False, "city_name_mismatch"
            break

    # Keep the explicit raw code as a final fallback. Unsupported numeric/source
    # codes must fail closed instead of becoming indistinguishable from a lot
    # that genuinely has no regional claim.
    sanity_region = expected_region or canonical_region or named_region
    spatial_rejection = coordinate_region_sanity_rejection_reason(
        result.lat,
        result.lon,
        sanity_region,
    )
    if spatial_rejection:
        return False, spatial_rejection
    return True, "validated"


def geocoding_result_quality_score(
    result: CadastralObjectResult | None,
    *,
    cadastral_number: str | None,
    address: str | None,
    region_name: str | None,
    region_code: str | None = None,
) -> int:
    """Score an accepted coordinate for audit/ranking without weakening validation."""
    if result is None or result.lat is None or result.lon is None:
        return 0

    confidence_scores = {
        "high": 45,
        "medium": 35,
        "low": 15,
        "none": 0,
        "unknown": 0,
    }
    score = confidence_scores.get(str(result.confidence or "").casefold(), 20)
    source = str(result.source or "").casefold()
    if source.startswith("nspd") or source.startswith("ik12"):
        score += 20
    elif source in {"photon", "nominatim", "address_geocoder"}:
        score += 10

    expected_cad = re.sub(r"\s+", "", str(cadastral_number or ""))
    observed_cad = re.sub(r"\s+", "", str(result.cadastral_number or ""))
    if expected_cad and observed_cad:
        score += 15 if expected_cad == observed_cad else -30

    expected_locality = expected_locality_name(address)
    observed_address = str(result.address or "").casefold()
    if expected_locality and observed_address:
        score += 10 if expected_locality in observed_address else -15

    from bankrotai.regions import normalize_region_code, region_code_from_text

    expected_region = normalize_region_code(region_code)
    if expected_region is None and region_name:
        expected_region = normalize_region_code(region_name)
    if expected_region and observed_address:
        observed_region = region_code_from_text(result.address)
        if observed_region:
            score += 10 if observed_region == expected_region else -20

    valid, _reason = validate_geocoding_result(
        result,
        cadastral_number=cadastral_number,
        address=address,
        region_name=region_name,
        region_code=region_code,
    )
    if valid:
        score += 15
    else:
        score = min(score, 49)
    return max(0, min(100, int(score)))


def apply_lot_geo_result(session: Session, lot: ProcessedLot, final_result: CadastralObjectResult | None) -> bool:
    if not final_result or not final_result.lat or not final_result.lon:
        lot.needs_geo_check = True
        return False

    observed_at = utc_now()
    snapshot = LotGeoSnapshot(
        lot_id=lot.id,
        geo_source=final_result.source,
        geo_method="cadastral" if final_result.cadastral_number else "address",
        geo_confidence=final_result.confidence,
        centroid_lat=final_result.lat,
        centroid_lon=final_result.lon,
        observed_at=observed_at,
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
            "status": final_result.status,
            "attempts": final_result.attempts,
            "quality_score": geocoding_result_quality_score(
                final_result,
                cadastral_number=lot.cadastral_number,
                address=lot.address,
                region_name=lot.region_name,
                region_code=lot.region_code,
            ),
        },
        trace_reason=(f"{final_result.source}: {'границы получены' if final_result.has_boundary else 'без границ'}"),
    )

    session.add(snapshot)
    lot.current_geo_lat = final_result.lat
    lot.current_geo_lon = final_result.lon
    lot.current_geo_source = final_result.source
    lot.current_geo_confidence = final_result.confidence
    lot.current_geo_observed_at = observed_at

    if final_result.address and (not lot.address or len(lot.address) < 15 or is_incomplete_address(lot.address)):
        lot.address = final_result.address

    lot.needs_geo_check = final_result.confidence not in {"high", "medium"}

    return True


def enrich_lot_geo(session: Session, lot: ProcessedLot) -> bool:
    final_result = resolve_lot_geo(
        lot.cadastral_number,
        lot.address,
        cadastral_numbers=lot.cadastral_numbers,
        title=lot.title,
        description=lot.description,
        region_name=lot.region_name,
        region_code=lot.region_code,
    )
    return apply_lot_geo_result(session, lot, final_result)


def distance_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return distance_km(lat1, lon1, lat2, lon2)
