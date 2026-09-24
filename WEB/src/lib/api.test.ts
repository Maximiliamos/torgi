import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchLots, fetchMapLotsSWR, fetchMapTile, fetchYandexMapTile, fetchPublicYandexMapTile, fetchPublicYandexMapBundleTile, fetchMapReviewStatuses, makeUrl, requestJson, type LotQuery } from "./api";

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
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("X-Production-Retry-Probe")).toBe("1");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).has("X-Production-Retry-Probe")).toBe(false);
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

  it("lets the map replace an expensive request with a reduced-limit fallback", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("temporary", { status: 502 }));

    await expect(fetchMapLotsSWR({ west: 20, south: 45, east: 60, north: 70, limit: 1000 }, undefined, undefined, 1))
      .rejects.toMatchObject({ status: 502 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(new URL(String(fetchMock.mock.calls[0][0])).searchParams.get("limit")).toBe("1000");
  });

  it.each([
    { features: null },
    { features: [{}] },
    { features: [{ kind: "lot", id: 1, lat: "55.7", lon: 37.6 }] },
    { features: [{ kind: "lot", id: 1, lat: 95, lon: 37.6 }] },
    { features: [{ kind: "cluster", id: "c", lat: 55.7, lon: 37.6, count: 0, bounds: [] }] },
  ])("rejects a malformed map tile payload %#", async (payload) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    );
    await expect(fetchMapTile("v1", 10, 1, 1)).rejects.toThrow(/тайла карты/);
  });

  it("accepts valid tile features and ignores unknown extra fields", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ features: [
      { kind: "lot", id: 1, lat: 55.7, lon: 37.6, title: "Лот", future_field: true },
      { kind: "cluster", id: "c:1", lat: 55.8, lon: 37.7, count: 2, bounds: [37, 55, 38, 56] },
    ] }), { status: 200 }));
    await expect(fetchMapTile("v1", 10, 1, 1)).resolves.toMatchObject({ features: [{ id: 1 }, { id: "c:1" }] });
  });
  it.each([
    null,
    { type: "FeatureCollection", features: null },
    { type: "FeatureCollection", features: [{}] },
    {
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: 1,
        geometry: { type: "Point", coordinates: ["55.7", 37.6] },
        properties: { kind: "lot" },
      }],
    },
    {
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: "c:1",
        geometry: { type: "Point", coordinates: [55.7, 37.6] },
        properties: { kind: "cluster", count: 0, bounds: [] },
      }],
    },
  ])("rejects malformed Yandex-ready tile payload %#", async (payload) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    );
    await expect(fetchYandexMapTile("v1", 10, 1, 1)).rejects.toThrow(/Yandex-тайла/);
  });

  it("loads immutable public tiles directly from REG.RU-compatible HTTPS storage", async () => {
    const payload = {
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: 42,
        geometry: { type: "Point", coordinates: [55.7, 37.6] },
        properties: { kind: "lot", lotId: 42, title: "Public lot" },
        options: { preset: "islands#grayDotIcon" },
      }],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    );

    await expect(fetchPublicYandexMapTile(
      "https://storage.example.test/public/datasets/v1/tiles",
      12,
      2345,
      1234,
    )).resolves.toEqual(payload);

    expect(String(fetchMock.mock.calls[0][0]))
      .toBe("https://storage.example.test/public/datasets/v1/tiles/12/2345/1234.json");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: "GET",
      mode: "cors",
      credentials: "omit",
      cache: "force-cache",
    });
  });

  it("rejects insecure or missing public object-store tiles so the map can use API fallback", async () => {
    await expect(fetchPublicYandexMapTile(
      "http://storage.example.test/public/datasets/v1/tiles",
      12,
      1,
      1,
    )).rejects.toThrow(/HTTPS/);

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("missing", { status: 404 }),
    );
    await expect(fetchPublicYandexMapTile(
      "https://storage.example.test/public/datasets/v1/tiles",
      12,
      1,
      1,
    )).resolves.toEqual({ type: "FeatureCollection", features: [] });
  });

  it("keeps review marker state behind the authenticated API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ items: [{ id: 1, review_status: "approved" }] }),
    );
    await expect(fetchMapReviewStatuses([1, 2])).resolves.toEqual({
      items: [{ id: 1, review_status: "approved" }],
    });
    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.pathname).toBe("/api/map/review-statuses");
    expect(url.searchParams.get("ids")).toBe("1,2");
  });

  it("accepts a server-ready Yandex FeatureCollection without client conversion", async () => {
    const payload = {
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: 42,
        geometry: { type: "Point", coordinates: [55.7, 37.6] },
        properties: {
          kind: "lot",
          lotId: 42,
          title: "Готовый лот",
          review_status: "approved",
        },
        options: { preset: "islands#greenDotIcon" },
      }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(payload), { status: 200 }),
    );
    await expect(fetchYandexMapTile("dataset-v1", 12, 1, 1)).resolves.toEqual(payload);
  });


  it("loads multiple logical tiles from one cached regional S3 bundle", async () => {
    const tileA = {
      type: "FeatureCollection" as const,
      features: [{
        type: "Feature" as const,
        id: 76,
        geometry: { type: "Point" as const, coordinates: [57.6, 39.8] },
        properties: { kind: "lot" as const, lotId: 76, region_code: "76" },
        options: { preset: "islands#grayDotIcon" },
      }],
    };
    const tileB = {
      type: "FeatureCollection" as const,
      features: [{
        type: "Feature" as const,
        id: 77,
        geometry: { type: "Point" as const, coordinates: [57.7, 39.9] },
        properties: { kind: "lot" as const, lotId: 77, region_code: "76" },
        options: { preset: "islands#grayDotIcon" },
      }],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/datasets/bundle-test/indexes/12.json")) {
        return new Response(JSON.stringify({
          layout: "regional-bundles-v1",
          version: "bundle-test",
          zoom: 12,
          tiles: {
            "2500/1200": { bundle: "bundles/v1/aa/hash.json", region: "76" },
            "2501/1201": { bundle: "bundles/v1/aa/hash.json", region: "76" },
          },
        }), { status: 200 });
      }
      if (url.endsWith("/bundles/v1/aa/hash.json")) {
        return new Response(JSON.stringify({
          layout: "regional-bundles-v1",
          region: "76",
          bucket: "76/p9/312/150",
          tiles: {
            "12/2500/1200": tileA,
            "12/2501/1201": tileB,
          },
        }), { status: 200 });
      }
      return new Response("not found", { status: 404 });
    });

    const source = {
      layout: "regional-bundles-v1",
      rootUrl: "https://storage-bundle.example.test/sterdez-map",
      indexBaseUrl: "https://storage-bundle.example.test/sterdez-map/datasets/bundle-test/indexes",
    };
    await expect(fetchPublicYandexMapBundleTile(source, "bundle-test", 12, 2500, 1200))
      .resolves.toEqual(tileA);
    await expect(fetchPublicYandexMapBundleTile(source, "bundle-test", 12, 2501, 1201))
      .resolves.toEqual(tileB);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/indexes/12.json");
    expect(String(fetchMock.mock.calls[1][0])).toContain("/bundles/v1/aa/hash.json");
  });

  it("rejects a regional bundle index that tries to escape the configured S3 origin", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      layout: "regional-bundles-v1",
      version: "bundle-escape",
      zoom: 12,
      tiles: {
        "1/1": { bundle: "https://evil.example.test/bundle.json", region: "76" },
      },
    }), { status: 200 }));

    await expect(fetchPublicYandexMapBundleTile({
      layout: "regional-bundles-v1",
      rootUrl: "https://storage-escape.example.test/sterdez-map",
      indexBaseUrl: "https://storage-escape.example.test/sterdez-map/datasets/bundle-escape/indexes",
    }, "bundle-escape", 12, 1, 1)).rejects.toThrow(/путь bundle/);
  });

});
