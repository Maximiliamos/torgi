import { describe, expect, it } from "vitest";

import {
  formatMoscowDate,
  mapBoundsPrecision,
  mapLimitForZoom,
  mapObjectCountLabel,
  MAP_SELECTION_SCRIPT,
  visibleTileCoordinates,
} from "./MapView";

describe("map lot selection", () => {
  it("centers a selected favorite on its coordinates at a useful zoom", () => {
    expect(MAP_SELECTION_SCRIPT).toContain("map.setCenter([selected.lat,selected.lon]");
    expect(MAP_SELECTION_SCRIPT).toContain("Math.max(map.getZoom(),16)");
  });
});

describe("auction date display", () => {
  it("treats timezone-less API timestamps as UTC and displays Moscow time", () => {
    expect(formatMoscowDate("2026-08-23T19:30:00")).toContain("22:30");
  });
});

describe("wide viewport request budget", () => {
  it("labels deferred statistics as a viewport lower bound", () => {
    expect(mapObjectCountLabel(251, 250, false)).toBe("не менее 250 объектов в области");
    expect(mapObjectCountLabel(29_130, 250, true)).toBe("29130 объектов");
  });

  it("uses bounded marker limits until the user zooms in", () => {
    expect(mapLimitForZoom(5)).toBe(250);
    expect(mapLimitForZoom(7)).toBe(250);
    expect(mapLimitForZoom(8)).toBe(750);
    expect(mapLimitForZoom(10)).toBe(750);
    expect(mapLimitForZoom(11)).toBe(1500);
  });

  it("normalizes distant bounds onto a reusable cache grid", () => {
    expect(mapBoundsPrecision(5)).toBe(1);
    expect(mapBoundsPrecision(7)).toBe(1);
    expect(mapBoundsPrecision(9)).toBe(2);
    expect(mapBoundsPrecision(12)).toBe(3);
    expect(mapBoundsPrecision(16)).toBe(4);
  });
});

describe("precomputed map tiles", () => {
  it("requests only tiles intersecting the visible viewport", () => {
    const tiles = visibleTileCoordinates([37.4, 55.6, 37.9, 55.9], 10);
    expect(tiles.length).toBeGreaterThan(0);
    expect(tiles.length).toBeLessThanOrEqual(9);
    expect(new Set(tiles.map((tile) => tile.key)).size).toBe(tiles.length);
    expect(tiles.every((tile) => tile.z === 10)).toBe(true);
  });

  it("splits a viewport crossing the date line without scanning the world", () => {
    const tiles = visibleTileCoordinates([179, -10, -179, 10], 5);
    expect(tiles.length).toBeGreaterThan(0);
    expect(tiles.length).toBeLessThanOrEqual(8);
    expect(tiles.some((tile) => tile.x === 0)).toBe(true);
    expect(tiles.some((tile) => tile.x === 31)).toBe(true);
  });
});
