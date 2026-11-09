from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from bankrotai.connectors.base import AuctionConnector, ConnectorHealth, ConnectorPage
from bankrotai.connectors.etp_sources.common import (
    EtpSearchFilters,
    EtpSourceProfile,
    HtmlEtpConnector,
    SourceAccessError,
    SourceSchemaError,
)
from bankrotai.connectors.etp_sources.source_specific import apply_detail
from bankrotai.connectors.transient_http import request_with_retries
from bankrotai.domain import NormalizedLot


DETAIL_CAPABILITIES = frozenset(
    {"search", "fetch_lot", "healthcheck", "detail_enrichment"}
)


def _page(filters: Any, cursor: str | None) -> int:
    if cursor is not None:
        return max(1, int(cursor))
    if isinstance(filters, EtpSearchFilters):
        return max(1, int(filters.page))
    if isinstance(filters, dict) and filters.get("page") is not None:
        return max(1, int(filters["page"]))
    return 1


def _profile_map(profiles: tuple[EtpSourceProfile, ...]) -> dict[str, EtpSourceProfile]:
    return {profile.source_id: profile for profile in profiles}


def _trusted(profile: EtpSourceProfile, value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in profile.allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise SourceAccessError(f"{profile.source_id}: refused unsafe provider URL")
    return value


def _merge_detail(listing: NormalizedLot, detail: NormalizedLot, *, version: int = 1) -> NormalizedLot:
    merged = apply_detail(listing, detail, version=version)
    for field in (
        "deposit_amount",
        "deposit_percent",
        "deposit_payment_details",
        "deposit_deadline",
        "application_start_at",
        "auction_timezone",
        "auction_step_amount",
        "auction_step_percent",
        "auction_type",
        "public_offer_schedule",
        "next_interval_price",
        "next_price_reduction_at",
        "bankruptcy_case_number",
    ):
        value = getattr(detail, field, None)
        if value is not None:
            setattr(merged, field, value)
    raw = dict(merged.raw_data or {})
    raw.update(detail.raw_data or {})
    raw["detail_enrichment_status"] = "success"
    raw["detail_identity_verified"] = True
    merged.raw_data = raw
    return merged


class AlfalotBlockedConnector(AuctionConnector):
    source_id = "alfalot.ru"
    safe_for_reconciliation = False
    capabilities = frozenset({"search", "healthcheck"})

    def __init__(
        self, *, session: requests.Session | None = None, timeout: float = 25.0
    ) -> None:
        self.session = session
        self.timeout = timeout

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        from bankrotai.connectors.etp_sources.alfalot_audit import (
            ALFALOT_BLOCKED_REASON,
        )
        raise SourceAccessError(ALFALOT_BLOCKED_REASON)

    async def healthcheck(self) -> ConnectorHealth:
        from bankrotai.connectors.etp_sources.alfalot_audit import (
            ALFALOT_BLOCKED_REASON,
        )
        return ConnectorHealth(
            "blocked",
            ALFALOT_BLOCKED_REASON,
            {
                "source": self.source_id,
                "production_enabled": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
        )


class CenterRConnector(HtmlEtpConnector):
    safe_for_reconciliation = False
    capabilities = DETAIL_CAPABILITIES

    def _request(self, page: int) -> requests.Response:
        from bankrotai.connectors.etp_sources.centerr_contract import (
            CenterRContractError,
            parse_next_page_postback,
        )

        if page <= 1:
            response = self._read_request(
                "get",
                _trusted(self.profile, self.profile.listing_url),
                timeout=self.timeout,
                allow_redirects=False,
            )
            self._last_response, self._last_page = response, 1
            return response

        previous = self._last_response if self._last_page == page - 1 else None
        if previous is None:
            if page != 2:
                raise SourceSchemaError(
                    f"{self.source_id}: sequential ASP.NET state required before page {page}"
                )
            previous = self._request(1)
        try:
            postback = parse_next_page_postback(previous.content, next_page=page)
        except CenterRContractError as exc:
            raise SourceSchemaError(str(exc)) from exc
        if postback is None:
            raise SourceSchemaError(
                f"{self.source_id}: page {page} is beyond the provider terminal page"
            )
        target, argument, hidden = postback
        soup = BeautifulSoup(previous.content, "html.parser")
        form = soup.find("form")
        action = urljoin(
            previous.url or self.profile.listing_url,
            str(form.get("action") or "") if isinstance(form, Tag) else "",
        )
        data = dict(hidden)
        data["__EVENTTARGET"] = target
        data["__EVENTARGUMENT"] = argument
        response = self._read_request(
            "post",
            _trusted(self.profile, action),
            data=data,
            timeout=self.timeout,
            allow_redirects=False,
        )
        self._last_response, self._last_page = response, page
        return response

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        from bankrotai.connectors.etp_sources.centerr_contract import (
            CenterRContractError,
            parse_listing,
            parse_next_page_postback,
        )

        page = _page(filters, cursor)
        response = await asyncio.to_thread(self._request, page)
        fingerprint = hashlib.sha256(response.content).hexdigest()
        if page > 1 and fingerprint in {
            value for number, value in self._page_fingerprints.items() if number < page
        }:
            raise SourceSchemaError(
                f"{self.source_id}: repeated catalogue page content at page {page}"
            )
        self._page_fingerprints[page] = fingerprint
        try:
            lots = parse_listing(response.content, page=page)
            has_next = parse_next_page_postback(
                response.content, next_page=page + 1
            ) is not None
        except CenterRContractError as exc:
            raise SourceSchemaError(str(exc)) from exc
        if not lots:
            raise SourceSchemaError(f"{self.source_id}: no verified catalogue lots")
        return ConnectorPage(
            items=lots,
            next_cursor=str(page + 1) if has_next else None,
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(lots),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "response_url": response.url,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        from bankrotai.connectors.etp_sources.centerr_contract import (
            CenterRContractError,
            parse_detail,
        )

        match = re.fullmatch(
            r"centerr\.ru:(auctions|contests|public-offers)-(\d+)",
            external_id,
        )
        if not match:
            raise SourceSchemaError("centerr.ru: invalid external lot identity")
        detail_url = _trusted(
            self.profile,
            f"https://bankrupt.centerr.ru/public/{match.group(1)}/lots/view/{match.group(2)}/",
        )
        response = await asyncio.to_thread(
            self._read_request,
            "get",
            detail_url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        try:
            return parse_detail(
                response.content,
                detail_url=detail_url,
                external_id=external_id,
            )
        except CenterRContractError as exc:
            raise SourceSchemaError(str(exc)) from exc

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        return _merge_detail(lot, await self.fetch_lot(lot.external_id))

    async def healthcheck(self) -> ConnectorHealth:
        try:
            page = await self.search({})
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
        return ConnectorHealth(
            "ok",
            f"matched {len(page.items)} verified individual lots",
            {"source": self.source_id, "safe_for_archival": False},
        )


class EtpuConnector(AuctionConnector):
    source_id = "etpu.ru"
    safe_for_reconciliation = False
    capabilities = DETAIL_CAPABILITIES

    def __init__(
        self, *, session: requests.Session | None = None, timeout: float = 30.0
    ) -> None:
        from bankrotai.connectors.etp_sources.etpu import EtpuClient
        self.client = EtpuClient(session=session, timeout=timeout)

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        page = _page(filters, cursor)
        result = await asyncio.to_thread(self.client.fetch_catalogue_page, page)
        if not result.items:
            raise SourceSchemaError("etpu.ru: no verified catalogue lots")
        return ConnectorPage(
            items=list(result.items),
            next_cursor=str(result.next_page) if result.next_page is not None else None,
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(result.items),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        return await asyncio.to_thread(self.client.fetch_detail, external_id)

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        return _merge_detail(lot, await self.fetch_lot(lot.external_id))

    async def healthcheck(self) -> ConnectorHealth:
        try:
            result = await asyncio.to_thread(self.client.fetch_catalogue_page, 1)
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
        return ConnectorHealth(
            "ok" if result.items else "degraded",
            f"matched {len(result.items)} verified catalogue lots",
            {"source": self.source_id, "safe_for_archival": False},
        )


class UtplConnector(HtmlEtpConnector):
    safe_for_reconciliation = False
    capabilities = DETAIL_CAPABILITIES

    def _request(self, page: int) -> requests.Response:
        from bankrotai.connectors.etp_sources.utpl_contract import (
            UtplLotSchemaError,
            utpl_next_postback,
            validate_sequential_page_state,
        )

        try:
            validate_sequential_page_state(
                requested_page=page,
                last_page=self._last_page if self._last_response is not None else None,
            )
        except UtplLotSchemaError as exc:
            raise SourceSchemaError(str(exc)) from exc
        if page <= 1:
            response = self._read_request(
                "get",
                _trusted(self.profile, self.profile.listing_url),
                timeout=self.timeout,
                allow_redirects=False,
            )
            self._last_response, self._last_page = response, 1
            return response
        previous = self._last_response if self._last_page == page - 1 else None
        if previous is None:
            if page != 2:
                raise SourceSchemaError(
                    f"{self.source_id}: sequential ASP.NET state required before page {page}"
                )
            previous = self._request(1)
        postback = utpl_next_postback(previous.content, page - 1)
        if postback is None:
            raise SourceSchemaError(
                f"{self.source_id}: page {page} is beyond the provider terminal page"
            )
        soup = BeautifulSoup(previous.content, "html.parser")
        form = soup.find("form")
        if not isinstance(form, Tag):
            raise SourceSchemaError("utpl.ru: pagination form disappeared")
        data = {
            str(node.get("name")): str(node.get("value") or "")
            for node in form.select("input[type=hidden][name]")
        }
        data["__EVENTTARGET"], data["__EVENTARGUMENT"] = postback
        action = _trusted(
            self.profile,
            urljoin(
                previous.url or self.profile.listing_url,
                str(form.get("action") or ""),
            ),
        )
        response = self._read_request(
            "post",
            action,
            data=data,
            timeout=self.timeout,
            allow_redirects=False,
        )
        self._last_response, self._last_page = response, page
        return response

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        from bankrotai.connectors.etp_sources.utpl_contract import (
            UtplLotSchemaError,
            is_terminal_page,
            normalize_utpl_status,
            parse_utpl_catalogue_page,
        )

        page = _page(filters, cursor)
        response = await asyncio.to_thread(self._request, page)
        fingerprint = hashlib.sha256(response.content).hexdigest()
        if page > 1 and fingerprint in {
            value for number, value in self._page_fingerprints.items() if number < page
        }:
            raise SourceSchemaError(
                f"{self.source_id}: repeated catalogue page content at page {page}"
            )
        self._page_fingerprints[page] = fingerprint
        try:
            rows = parse_utpl_catalogue_page(response.content, page=page)
        except UtplLotSchemaError as exc:
            raise SourceSchemaError(str(exc)) from exc
        if not rows:
            raise SourceSchemaError("utpl.ru: no verified individual lot rows")
        lots = [
            NormalizedLot(
                external_id=row["external_id"],
                source="utpl",
                source_system=self.source_id,
                title=row["title"][:500],
                description=row["title"][:5000],
                category="other",
                region_slug=None,
                region_name=None,
                address=None,
                cadastral_number=None,
                vin=None,
                area=None,
                start_price=row["initial_price"],
                current_price=row["current_price"],
                auction_status=normalize_utpl_status(row["source_status_text"]),
                lot_url=row["detail_url"],
                source_url=row["detail_url"],
                detail_level="list",
                raw_data={
                    "listing_page": page,
                    "procedure_number": row["procedure_number"],
                    "lot_number": row["lot_number"],
                    "individual_lot_identity_verified": True,
                    "property_classification_verified": False,
                    "complete_source_inventory": False,
                    "safe_for_archival": False,
                    "reconcile_missing": False,
                },
                platform_name=self.profile.platform_name,
                platform_code=self.profile.platform_code,
                procedure_number=row["procedure_number"],
            )
            for row in rows
        ]
        return ConnectorPage(
            items=lots,
            next_cursor=None if is_terminal_page(response.content, page) else str(page + 1),
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(lots),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        from bankrotai.connectors.etp_sources.utpl_contract import (
            UtplLotSchemaError,
            parse_utpl_detail,
        )

        match = re.fullmatch(
            r"utpl\.ru:(auctions|contests|public-offers)-(\d+)", external_id
        )
        if not match:
            raise SourceSchemaError("utpl.ru: invalid external lot identity")
        detail_url = _trusted(
            self.profile,
            f"https://bankrupt.utpl.ru/public/{match.group(1)}/lots/view/{match.group(2)}/",
        )
        response = await asyncio.to_thread(
            self._read_request,
            "get",
            detail_url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        try:
            fields = parse_utpl_detail(
                detail_url,
                response.content,
                external_id=external_id,
            )
        except UtplLotSchemaError as exc:
            raise SourceSchemaError(str(exc)) from exc
        return NormalizedLot(
            external_id=fields["external_id"],
            source="utpl",
            source_system=self.source_id,
            title=fields["title"][:500],
            description=fields["title"][:5000],
            category=fields["category"],
            region_slug=None,
            region_name=None,
            address=fields["address"],
            cadastral_number=fields["cadastral_number"],
            vin=fields["vin"],
            area=fields["area"],
            start_price=fields["initial_price"],
            current_price=fields["current_price"],
            auction_status=fields["status"],
            lot_url=detail_url,
            source_url=detail_url,
            detail_level="detail",
            raw_data={
                "utpl_classifier": fields["classifier"],
                "utpl_property_kinds": list(fields["property_kinds"]),
                "structured_classifier_verified": fields["structured_classifier_verified"],
                "property_classification_verified": fields["property_classification_verified"],
                "safe_for_archival": False,
                "reconcile_missing": False,
            },
            platform_name=self.profile.platform_name,
            platform_code=self.profile.platform_code,
            procedure_number=fields["procedure_number"],
            application_start_at=fields["application_start"],
            application_deadline=fields["application_deadline"],
            auction_at=fields["auction_at"],
        )

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        return _merge_detail(lot, await self.fetch_lot(lot.external_id))

    async def healthcheck(self) -> ConnectorHealth:
        try:
            page = await self.search({})
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
        return ConnectorHealth(
            "ok",
            f"matched {len(page.items)} verified individual lots",
            {"source": self.source_id, "safe_for_archival": False},
        )


class AukcionCenterConnector(HtmlEtpConnector):
    safe_for_reconciliation = False
    capabilities = DETAIL_CAPABILITIES

    def _request(self, page: int) -> requests.Response:
        from bankrotai.connectors.etp_sources.aukcioncenter import catalogue_url
        response = self._read_request(
            "get",
            _trusted(self.profile, catalogue_url(page)),
            timeout=self.timeout,
            allow_redirects=False,
        )
        self._last_response, self._last_page = response, page
        return response

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        from bankrotai.connectors.etp_sources.aukcioncenter import (
            has_next_page,
            parse_listing,
        )

        page = _page(filters, cursor)
        response = await asyncio.to_thread(self._request, page)
        lots = parse_listing(cast(Any, self.profile), response.content, page=page, active_filter=True)
        if not lots:
            raise SourceSchemaError("aukcioncenter.ru: no verified individual lots")
        return ConnectorPage(
            items=lots,
            next_cursor=str(page + 1) if has_next_page(response.content, page) else None,
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(lots),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "response_url": response.url,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        from bankrotai.connectors.etp_sources.aukcioncenter import (
            detail_request_url,
            parse_detail,
        )

        detail_url = _trusted(self.profile, detail_request_url(external_id))
        response = await asyncio.to_thread(
            self._read_request,
            "get",
            detail_url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return parse_detail(
            cast(Any, self.profile),
            response.content,
            detail_url_value=detail_url,
            external_id=external_id,
        )

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        return _merge_detail(lot, await self.fetch_lot(lot.external_id))

    async def healthcheck(self) -> ConnectorHealth:
        try:
            page = await self.search({})
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
        return ConnectorHealth(
            "ok",
            f"matched {len(page.items)} verified individual lots",
            {"source": self.source_id, "safe_for_archival": False},
        )


class SistematorgConnector(AuctionConnector):
    source_id = "sistematorg.com"
    safe_for_reconciliation = False
    capabilities = DETAIL_CAPABILITIES

    def __init__(
        self, *, session: requests.Session | None = None, timeout: float = 25.0
    ) -> None:
        from bankrotai.connectors.etp_sources.sistematorg_contract import (
            SistematorgReadonlyClient,
        )
        self.client = SistematorgReadonlyClient(session=session, timeout=timeout)

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        from bankrotai.connectors.etp_sources.sistematorg_contract import (
            parse_sistematorg_catalogue_page,
        )

        page = _page(filters, cursor)
        response = await asyncio.to_thread(self.client.fetch_catalogue_page, page)
        parsed = parse_sistematorg_catalogue_page(response.content, page=page)
        lots: list[NormalizedLot] = []
        for procedure in parsed.procedures:
            for item in procedure.lots:
                if not item.is_property:
                    continue
                lots.append(
                    NormalizedLot(
                        external_id=item.external_id,
                        source="sistematorg",
                        source_system=self.source_id,
                        title=item.title[:500],
                        description=item.title[:5000],
                        category=item.category,
                        region_slug=None,
                        region_name=None,
                        address=None,
                        cadastral_number=None,
                        vin=None,
                        area=None,
                        start_price=None,
                        current_price=None,
                        auction_status="unknown",
                        lot_url=procedure.detail_url,
                        source_url=procedure.detail_url,
                        detail_level="search",
                        raw_data={
                            "listing_page": page,
                            "procedure_id": item.trade_id,
                            "lot_number": item.lot_id,
                            "individual_lot_identity_verified": True,
                            "per_lot_detail_verified": False,
                            "complete_source_inventory": False,
                            "safe_for_archival": False,
                            "reconcile_missing": False,
                        },
                        platform_name="Систематорг",
                        platform_code="sistematorg",
                        procedure_number=procedure.procedure_number,
                    )
                )
        if not lots:
            raise SourceSchemaError("sistematorg.com: no verified property lots")
        has_next = self.client.has_next_page(response.content, page)
        return ConnectorPage(
            items=lots,
            next_cursor=str(page + 1) if has_next else None,
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(lots),
                "catalogue_rejects": dict(parsed.rejects),
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "response_url": response.url,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        from bankrotai.connectors.etp_sources.sistematorg_contract import (
            parse_sistematorg_detail_lots,
            parse_sistematorg_external_id,
        )

        try:
            trade_id, _lot_id = parse_sistematorg_external_id(external_id)
        except ValueError as exc:
            raise SourceSchemaError(str(exc)) from exc
        detail_url = f"https://sistematorg.com/trade_view.php?trade_nid={trade_id}"
        response = await asyncio.to_thread(self.client.fetch_detail, detail_url)
        try:
            details = parse_sistematorg_detail_lots(
                response.content,
                detail_url=detail_url,
            )
        except ValueError as exc:
            raise SourceSchemaError(str(exc)) from exc
        matching = [lot for lot in details if lot.external_id == external_id]
        if len(matching) != 1:
            raise SourceSchemaError(
                "sistematorg.com: requested individual lot was not uniquely proven"
            )
        return matching[0]

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        return _merge_detail(lot, await self.fetch_lot(lot.external_id))

    async def healthcheck(self) -> ConnectorHealth:
        try:
            page = await self.search({})
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
        return ConnectorHealth(
            "ok",
            f"matched {len(page.items)} verified property lots",
            {"source": self.source_id, "safe_for_archival": False},
        )


class SberbankAstConnector(HtmlEtpConnector):
    safe_for_reconciliation = False
    capabilities = DETAIL_CAPABILITIES

    async def search(self, filters: Any, cursor: str | None = None) -> ConnectorPage:
        from bankrotai.connectors.etp_sources.sberbank_ast_contract import (
            SberbankAstContractError,
            build_catalogue_request,
            parse_catalogue_page,
        )

        page = _page(filters, cursor)
        try:
            api_url, payload = build_catalogue_request(page)
        except SberbankAstContractError as exc:
            raise SourceSchemaError(str(exc)) from exc
        response = await asyncio.to_thread(
            self._read_request,
            "post",
            _trusted(self.profile, api_url),
            json=payload,
            timeout=self.timeout,
            allow_redirects=False,
        )
        try:
            parsed = parse_catalogue_page(response.json(), page=page)
        except SberbankAstContractError as exc:
            raise SourceSchemaError(str(exc)) from exc
        return ConnectorPage(
            items=parsed.lots,
            next_cursor=str(parsed.next_page) if parsed.next_page is not None else None,
            metadata={
                "source": self.source_id,
                "page": page,
                "items_on_page": len(parsed.lots),
                "api_total_reported": parsed.api_total_reported,
                "api_total_is_source_end": parsed.source_total_proven,
                "terminal_page": parsed.terminal_page,
                "terminal_page_proven": parsed.terminal_page_proven,
                "complete_source_inventory": False,
                "safe_for_archival": False,
                "reconcile_missing": False,
                "response_url": response.url,
            },
        )

    async def fetch_lot(self, external_id: str) -> NormalizedLot:
        from bankrotai.connectors.etp_sources.sberbank_ast_contract import (
            SberbankAstContractError,
            detail_url_from_external_id,
            parse_detail,
        )

        try:
            detail_url = detail_url_from_external_id(external_id)
        except SberbankAstContractError as exc:
            raise SourceSchemaError(str(exc)) from exc
        response = await asyncio.to_thread(
            self._read_request,
            "get",
            _trusted(self.profile, detail_url),
            timeout=self.timeout,
            allow_redirects=False,
        )
        try:
            return parse_detail(
                response.content,
                detail_url=detail_url,
                external_id=external_id,
            )
        except SberbankAstContractError as exc:
            raise SourceSchemaError(str(exc)) from exc

    async def enrich_lot(self, lot: NormalizedLot) -> NormalizedLot:
        return _merge_detail(lot, await self.fetch_lot(lot.external_id))

    async def healthcheck(self) -> ConnectorHealth:
        try:
            page = await self.search({})
        except Exception as exc:
            return ConnectorHealth("degraded", str(exc), {"source": self.source_id})
        source_end = bool(page.metadata.get("api_total_is_source_end"))
        return ConnectorHealth(
            "ok" if source_end and page.items else "degraded",
            (
                f"verified bankruptcy catalogue; matched {len(page.items)} property lots"
                if source_end
                else "bankruptcy catalogue verified; source end is not proven beyond display cap"
            ),
            {"source": self.source_id, **page.metadata},
        )


def build_source_overrides(
    profiles: tuple[EtpSourceProfile, ...],
) -> dict[str, type[AuctionConnector]]:
    from bankrotai.connectors.etp_sources.arbbitlot import ArbbitlotConnector
    from bankrotai.connectors.etp_sources.fabrikant import FabrikantConnector
    from bankrotai.connectors.etp_sources.vertrades import connector_type as vertrades_type
    from bankrotai.connectors.etp_sources.zakazrf import ZakazRfConnector

    by_id = _profile_map(profiles)

    CenterRConnector.profile = by_id["centerr.ru"]
    UtplConnector.profile = by_id["utpl.ru"]
    AukcionCenterConnector.profile = by_id["aukcioncenter.ru"]
    SberbankAstConnector.profile = by_id["sberbank-ast.ru"]

    class IntegratedFabrikantConnector(FabrikantConnector):
        safe_for_reconciliation = False
        capabilities = DETAIL_CAPABILITIES

    IntegratedFabrikantConnector.profile = by_id["fabrikant.ru"]
    IntegratedFabrikantConnector.__name__ = "FabrikantConnector"
    IntegratedFabrikantConnector.__qualname__ = "FabrikantConnector"

    return {
        "alfalot.ru": AlfalotBlockedConnector,
        "centerr.ru": CenterRConnector,
        "arbbitlot.ru": ArbbitlotConnector,
        "etpu.ru": EtpuConnector,
        "zakazrf.ru": ZakazRfConnector,
        "utpl.ru": UtplConnector,
        "aukcioncenter.ru": AukcionCenterConnector,
        "fabrikant.ru": IntegratedFabrikantConnector,
        "vertrades.ru": vertrades_type(by_id["vertrades.ru"]),
        "sistematorg.com": SistematorgConnector,
        "sberbank-ast.ru": SberbankAstConnector,
    }
