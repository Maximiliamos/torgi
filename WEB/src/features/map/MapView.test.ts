import { describe, expect, it } from "vitest";

import {
  formatMoscowDate,
  mapBoundsPrecision,
  mapLimitForZoom,
  MAP_SELECTION_SCRIPT,
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
  it("uses bounded marker limits until the user zooms in", () => {
    expect(mapLimitForZoom(5)).toBe(1000);
    expect(mapLimitForZoom(7)).toBe(1000);
    expect(mapLimitForZoom(8)).toBe(1500);
    expect(mapLimitForZoom(10)).toBe(1500);
    expect(mapLimitForZoom(11)).toBe(3000);
  });

  it("normalizes distant bounds onto a reusable cache grid", () => {
    expect(mapBoundsPrecision(5)).toBe(1);
    expect(mapBoundsPrecision(7)).toBe(1);
    expect(mapBoundsPrecision(9)).toBe(2);
    expect(mapBoundsPrecision(12)).toBe(3);
    expect(mapBoundsPrecision(16)).toBe(4);
  });
});
