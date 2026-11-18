"""Use the current GEO projection; snapshots are historical evidence."""
from bankrotai.db import LotGeoSnapshot


def current_map_geo(lot, snapshot: LotGeoSnapshot | None) -> LotGeoSnapshot | None:
    if lot.current_geo_lat is None or lot.current_geo_lon is None:
        return None
    if (snapshot is not None
            and snapshot.centroid_lat == lot.current_geo_lat
            and snapshot.centroid_lon == lot.current_geo_lon
            and (lot.current_geo_observed_at is None or snapshot.observed_at == lot.current_geo_observed_at)):
        return snapshot
    # A current point may exist without a matching historical geometry. Never
    # attach an old shape or old metadata to that point. This object is a view,
    # is not attached to a session and is never inserted into the database.
    return LotGeoSnapshot(lot_id=lot.id, centroid_lat=lot.current_geo_lat,
                          centroid_lon=lot.current_geo_lon, geo_source=lot.current_geo_source or "unknown",
                          geo_confidence=lot.current_geo_confidence or "unknown",
                          observed_at=lot.current_geo_observed_at)
