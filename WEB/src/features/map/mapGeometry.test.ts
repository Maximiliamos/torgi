import { describe, expect, it } from "vitest";

import {
  mapBoundsPrecision,
  mapLimitForZoom,
  mapTileCacheKey,
  visibleTileCoordinates,
  yandexMapsApiUrl,
} from "./mapGeometry";
import * as legacyMapView from "./MapView";

describe("BAT-308 extracted map geometry", () => {
  it("preserves existing MapView exports", () => {
    expect(legacyMapView.mapLimitForZoom).toBe(mapLimitForZoom);
    expect(legacyMapView.visibleTileCoordinates).toBe(visibleTileCoordinates);
    expect(legacyMapView.mapBoundsPrecision).toBe(mapBoundsPrecision);
    expect(legacyMapView.mapTileCacheKey).toBe(mapTileCacheKey);
    expect(legacyMapView.yandexMapsApiUrl).toBe(yandexMapsApiUrl);
  });

  it("keeps bounded zoom and cache identity", () => {
    expect(mapLimitForZoom(7)).toBe(250);
    expect(mapBoundsPrecision(16)).toBe(4);
    expect(mapTileCacheKey("v1", { z: 3, x: 2, y: 1 })).toBe("v1:3:2:1");
  });

  it("limits tiles to the viewport and handles crossing longitude 180", () => {
    const result = visibleTileCoordinates([179, -10, -179, 10], 5);
    expect(result.length).toBeGreaterThan(0);
    expect(new Set(result.map((tile) => tile.key)).size).toBe(result.length);
    expect(result.every((tile) => tile.x === 0 || tile.x === 31)).toBe(true);
  });
});
