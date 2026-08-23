import { describe, expect, it } from "vitest";

import { MAP_SELECTION_SCRIPT } from "./MapView";

describe("map lot selection", () => {
  it("centers a selected favorite on its coordinates at a useful zoom", () => {
    expect(MAP_SELECTION_SCRIPT).toContain("map.setCenter([selected.lat,selected.lon]");
    expect(MAP_SELECTION_SCRIPT).toContain("Math.max(map.getZoom(),16)");
  });
});
