from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bankrotai.connectors.base import AuctionConnector, ConnectorPage
from bankrotai.db import Base
from bankrotai.domain import NormalizedLot
from bankrotai.services.ingestion import NationwideIngestionService, SourceSyncSpec


class ReplayConnector(AuctionConnector):
    source_id = "torgi.gov.ru"

    def __init__(self, lots: list[NormalizedLot]) -> None:
        self.lots = lots

    async def search(self, filters, cursor: str | None = None) -> ConnectorPage:
        return ConnectorPage(
            items=self.lots,
            metadata={
                "requested_category_group": "903",
                "timings": {
                    "http_requests": 0,
                    "request_ms": 0,
                    "json_decode_ms": 0,
                    "normalize_ms": 0,
                    "response_bytes": 0,
                },
            },
        )


def replay_lots(count: int) -> list[NormalizedLot]:
    return [
        NormalizedLot(
            external_id=f"torgi_gov:profile-{index}",
            source="torgi_gov",
            source_system="torgi.gov.ru",
            title=f"Земельный участок со зданием {index}",
            description="Профиль производительности nationwide ingestion",
            category="commercial_building_with_land",
            region_slug="76",
            region_name="Ярославская область",
            address=f"Ярославская область, тестовый адрес {index}",
            cadastral_number=f"76:23:{index // 10000:07d}:{index + 1}",
            vin=None,
            area=100.0 + index,
            start_price=500_000.0 + index,
            current_price=500_000.0 + index,
            auction_status="active",
            lot_url=f"https://torgi.gov.ru/new/public/lots/lot/profile-{index}",
            source_url=f"https://torgi.gov.ru/new/public/lots/lot/profile-{index}",
            detail_level="search",
            raw_data={"region_code": "76", "category_code": "903"},
        )
        for index in range(count)
    ]


def run_profile(rows: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="bankrotai-gis-profile-") as temp_dir:
        database_path = Path(temp_dir) / "profile.sqlite3"
        engine = create_engine(f"sqlite:///{database_path}")
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        connector = ReplayConnector(replay_lots(rows))
        service = NationwideIngestionService(
            sessions,
            connector_factory=lambda _source: connector,
            profile_timings=True,
        )
        reports = []
        for mode in ("insert", "update"):
            run_id = service.create_run(triggered_by="local-profile", trigger_type="benchmark", total_sources=1)
            result = asyncio.run(service.run(run_id, (SourceSyncSpec("torgi.gov.ru", {}),)))
            reports.append({"mode": mode, **result["sources"][0], **result["profile"]})
        engine.dispose()
    return {"rows": rows, "transport": "offline replay", "reports": reports}


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile the existing GIS per-lot persistence path safely.")
    parser.add_argument("--rows", type=int, default=100)
    args = parser.parse_args()
    if args.rows < 1:
        parser.error("--rows must be positive")
    print(json.dumps(run_profile(args.rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
