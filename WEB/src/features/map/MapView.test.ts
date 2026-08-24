import { describe, expect, it } from "vitest";

import { formatMoscowDate, MAP_SELECTION_SCRIPT } from "./MapView";

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
