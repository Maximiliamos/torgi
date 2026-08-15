import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchLots, makeUrl, requestJson, type LotQuery } from "./api";

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
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("service unavailable", { status: 400 }));
    await expect(requestJson("/api/lots")).rejects.toThrow("service unavailable");
  });

  it("retries a transient GET once and then succeeds", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("temporary", { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    await expect(requestJson<{ ok: boolean }>("/api/lots")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never retries a mutation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("temporary", { status: 503 }));

    await expect(requestJson("/api/saved-searches", undefined, {
      method: "POST",
      body: JSON.stringify({ name: "audit" }),
    })).rejects.toMatchObject({
      message: "Сервис временно недоступен. Повторите попытку через несколько секунд.",
      status: 503,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not expose Failed to fetch to the user", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError("Failed to fetch"));

    const error = await requestJson("/api/lots").catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as Error).message).toBe(
      "Сервис временно недоступен. Повторите попытку через несколько секунд.",
    );
    expect((error as Error).message).not.toContain("Failed to fetch");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry an aborted request", async () => {
    const controller = new AbortController();
    const abortError = new DOMException("aborted", "AbortError");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(abortError);
    controller.abort();

    await expect(requestJson("/api/lots", undefined, { signal: controller.signal }))
      .rejects.toBe(abortError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
