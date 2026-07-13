import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchLots, makeUrl, requestJson, type LotQuery } from "./api";

describe("API client", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("encodes search and omits absent risk filters", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })
    );
    const query: LotQuery = {
      city_slug: "yaroslavl", page: 1, per_page: 18, search: "  склад  ", categories: [],
      statuses: ["active"], sort: "recommended"
    };
    await fetchLots(query);
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.searchParams.get("search")).toBe("  склад  ");
    expect(url.searchParams.has("min_risk")).toBe(false);
    expect(url.searchParams.get("statuses")).toBe("active");
  });

  it("includes explicit risk range", () => {
    const url = new URL(makeUrl("/api/lots", { min_risk: 2, max_risk: 7 }));
    expect(url.searchParams.get("min_risk")).toBe("2");
    expect(url.searchParams.get("max_risk")).toBe("7");
  });

  it("surfaces API errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("service unavailable", { status: 503 }));
    await expect(requestJson("/api/lots")).rejects.toThrow("service unavailable");
  });
});
