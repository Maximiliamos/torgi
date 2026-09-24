/* global Request, Response, console, TextEncoder */

import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "./worker.mjs";


function memoryCache() {
  const store = new Map();
  return {
    store,
    match: vi.fn(async (request) => {
      const response = store.get(request.url);
      return response ? response.clone() : undefined;
    }),
    put: vi.fn(async (request, response) => {
      store.set(request.url, response.clone());
    }),
  };
}

function executionContext() {
  const pending = [];
  return {
    waitUntil(promise) {
      pending.push(Promise.resolve(promise));
    },
    async flush() {
      const items = pending.splice(0);
      await Promise.all(items);
    },
  };
}

function memoryR2(initial = new Map()) {
  const store = new Map(initial);
  return {
    store,
    get: vi.fn(async (key) => {
      const item = store.get(key);
      if (!item) return null;
      return {
        body: new Response(item.body).body,
        etag: item.etag || "r2-etag",
        httpEtag: item.httpEtag || '"r2-etag"',
        customMetadata: item.customMetadata || {},
        writeHttpMetadata(headers) {
          const metadata = item.httpMetadata || {};
          if (metadata.contentType) headers.set("content-type", metadata.contentType);
          if (metadata.cacheControl) headers.set("cache-control", metadata.cacheControl);
        },
      };
    }),
    put: vi.fn(async (key, value, options = {}) => {
      let body;
      if (value instanceof ArrayBuffer) {
        body = value.slice(0);
      } else if (ArrayBuffer.isView(value)) {
        body = value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
      } else if (typeof value === "string") {
        body = new TextEncoder().encode(value).buffer;
      } else {
        body = await new Response(value).arrayBuffer();
      }
      store.set(key, {
        body,
        customMetadata: options.customMetadata || {},
        httpMetadata: options.httpMetadata || {},
      });
    }),
  };
}

async function authorizeMapEdge({ cache, r2, fetchMock, serviceKey = "bound-secret" }) {
  fetchMock.mockResolvedValueOnce(new Response(
    JSON.stringify({ version: "dataset-v1", bootstrap_tiles: [] }),
    {
      status: 200,
      headers: {
        "content-type": "application/json",
        etag: '"dataset-dataset-v1"',
      },
    },
  ));
  const ctx = executionContext();
  const response = await worker.fetch(new Request(
    "https://api.sterdez.online/api/map/datasets/current",
    { headers: { cookie: "bankrotai_session=origin-signed" } },
  ), {
    KOYEB_SERVICE_KEY: serviceKey,
    MAP_EDGE_CACHE: cache,
    MAP_ASSETS: r2,
  }, ctx);
  await ctx.flush();
  expect(response.status).toBe(200);
  expect(response.headers.get("x-map-cache")).toBe("ORIGIN-AUTH");
  const setCookie = response.headers.get("set-cookie");
  expect(setCookie).toContain("bankrotai_map_edge=");
  expect(setCookie).toContain("HttpOnly");
  expect(setCookie).toContain("Secure");
  expect(setCookie).toContain("SameSite=Strict");
  expect(setCookie).toContain("Max-Age=120");
  return setCookie.split(";")[0];
}

describe("API origin failover proxy", () => {
  afterEach(() => vi.restoreAllMocks());

  it("proxies only allowlisted public GIS GET paths", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ content: [] }),
    );
    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/__public-source/torgi/new/api/public/lotcards/search?page=0&size=1",
    ), {});
    expect(fetchMock.mock.calls[0][0].url)
      .toBe("https://torgi.gov.ru/new/api/public/lotcards/search?page=0&size=1");
    expect(response.status).toBe(200);

    const denied = await worker.fetch(new Request(
      "https://api.sterdez.online/__public-source/torgi/admin/private",
    ), {});
    expect(denied.status).toBe(404);
  });

  it("replaces caller credentials with the service binding", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ status: "ok" }),
    );

    const response = await worker.fetch(new Request("https://api.sterdez.online/api/auth/me", {
      headers: { authorization: "caller", "x-api-key": "caller-key", cookie: "session=signed" },
    }), { KOYEB_SERVICE_KEY: "bound-secret" });

    const upstream = fetchMock.mock.calls[0][0];
    expect(upstream.url).toBe("https://home-relay.194-226-126-233.sslip.io/api/auth/me");
    expect(upstream.headers.get("authorization")).toBeNull();
    expect(upstream.headers.get("x-api-key")).toBe("bound-secret");
    expect(upstream.headers.get("cookie")).toBe("session=signed");
    expect(upstream.headers.get("x-forwarded-host")).toBe("api.sterdez.online");
    expect(upstream.headers.get("x-request-id")).toBeTruthy();
    expect(response.status).toBe(200);
    expect(response.headers.get("x-request-id")).toBeTruthy();
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("uses a configured primary origin for every method", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ status: "ok" }),
    );

    const response = await worker.fetch(new Request("https://api.sterdez.online/api/auth/login", {
      method: "POST",
      body: "{}",
    }), {
      KOYEB_SERVICE_KEY: "bound-secret",
      PRIMARY_API_ORIGIN: "https://home.example.test",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0].url).toBe("https://home.example.test/api/auth/login");
    expect(response.status).toBe(200);
  });

  it("returns a bounded upstream error without leaking details", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("network down"));
    const response = await worker.fetch(
      new Request("https://api.sterdez.online/health/live"),
      { KOYEB_SERVICE_KEY: "bound-secret" },
    );
    expect(response.status).toBe(502);
    expect(response.headers.get("x-request-id")).toBeTruthy();
    expect(await response.json()).toEqual({ detail: "API upstream is temporarily unavailable" });
  });

  it("preserves a bounded caller request ID for end-to-end correlation", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("ok"));

    const response = await worker.fetch(new Request("https://api.sterdez.online/health/live", {
      headers: { "x-request-id": "availability-sample-42" },
    }), { KOYEB_SERVICE_KEY: "bound-secret" });

    const upstream = fetchMock.mock.calls[0][0];
    expect(upstream.headers.get("x-request-id")).toBe("availability-sample-42");
    expect(response.headers.get("x-request-id")).toBe("availability-sample-42");
  });

  it("fails a safe read over to the secondary after primary transport failure", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("primary reset"))
      .mockRejectedValueOnce(new TypeError("primary reset again"))
      .mockResolvedValueOnce(Response.json({ status: "alive" }));

    const response = await worker.fetch(new Request("https://api.sterdez.online/health/live"), {
      KOYEB_SERVICE_KEY: "bound-secret",
      SECONDARY_API_ORIGIN: "https://secondary.example.test",
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0].url).toBe("https://home-relay.194-226-126-233.sslip.io/health/live");
    expect(fetchMock.mock.calls[2][0].url).toBe("https://secondary.example.test/health/live");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "alive" });
  });

  it("checks the current secondary when a stale primary does not know a safe route", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("Not Found", { status: 404 }))
      .mockResolvedValueOnce(Response.json({ synchronized: true }));

    const response = await worker.fetch(new Request("https://api.sterdez.online/api/time"), {
      KOYEB_SERVICE_KEY: "bound-secret",
      SECONDARY_API_ORIGIN: "https://secondary.example.test",
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0].url).toBe("https://secondary.example.test/api/time");
    expect(response.status).toBe(200);
  });

  it("preserves a real not-found response without consulting the secondary", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("Not Found", { status: 404 }));

    const response = await worker.fetch(new Request("https://api.sterdez.online/api/lots/999999999"), {
      KOYEB_SERVICE_KEY: "bound-secret",
      SECONDARY_API_ORIGIN: "https://secondary.example.test",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(404);
  });

  it("retries a map read once on the primary before using the secondary", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("home tunnel response reset"))
      .mockResolvedValueOnce(Response.json({ items: [{ id: 1 }] }));

    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/map/lots?west=33&south=55&east=46&north=60",
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      PRIMARY_API_ORIGIN: "https://home.example.test",
      SECONDARY_API_ORIGIN: "https://secondary.example.test",
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0].url).toContain("home.example.test/api/map/lots");
    expect(fetchMock.mock.calls[1][0].url).toContain("home.example.test/api/map/lots");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ items: [{ id: 1 }] });
  });

  it.each(["POST", "PUT", "PATCH", "DELETE"])("never retries %s mutations", async (method) => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("primary reset"));
    const response = await worker.fetch(new Request("https://api.sterdez.online/api/auth/login", {
      method,
      body: "{}",
    }), {
      KOYEB_SERVICE_KEY: "bound-secret",
      SECONDARY_API_ORIGIN: "https://secondary.example.test",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(502);
  });

  it("falls back when successful primary response headers are followed by a body disconnect", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const broken = new Response(new globalThis.ReadableStream({
      pull(controller) { controller.error(new TypeError("body reset")); },
    }), { status: 200 });
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(broken)
      .mockResolvedValueOnce(new Response("complete", { status: 200 }));

    const response = await worker.fetch(new Request("https://api.sterdez.online/api/lots"), {
      KOYEB_SERVICE_KEY: "bound-secret",
      SECONDARY_API_ORIGIN: "https://secondary.example.test",
    });
    expect(await response.text()).toBe("complete");
  });

  it("preserves a bodyless upstream 304 without activating fallback", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 304, headers: { etag: '"map-v1"' } }),
    );

    const response = await worker.fetch(new Request("https://api.sterdez.online/api/map/lots", {
      headers: { "if-none-match": '"map-v1"' },
    }), { KOYEB_SERVICE_KEY: "bound-secret" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0].headers.get("if-none-match")).toBe('"map-v1"');
    expect(response.status).toBe(304);
    expect(response.headers.get("etag")).toBe('"map-v1"');
  });

  it("preserves private browser caching for authenticated immutable map tiles", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('{"features":[]}', {
      headers: { "cache-control": "private, max-age=86400, immutable" },
    }));

    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/map/tiles/version-1/12/2345/1234",
      { headers: { cookie: "bankrotai_session=signed" } },
    ), { KOYEB_SERVICE_KEY: "bound-secret" });

    expect(response.headers.get("cache-control"))
      .toBe("private, max-age=86400, immutable");
  });

  it("keeps authentication and mutation responses non-cacheable", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ id: 1 }));

    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/auth/me",
    ), { KOYEB_SERVICE_KEY: "bound-secret" });

    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("requires an authenticated map edge session before reading shared tile caches", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const r2 = memoryR2(new Map([
      ["yandex-tiles/dataset-v1/7/77/38.json", {
        body: new TextEncoder().encode('{"type":"FeatureCollection","features":[]}').buffer,
      }],
    ]));

    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/map/yandex-tiles/dataset-v1/7/77/38",
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_ASSETS: r2,
      MAP_EDGE_CACHE: memoryCache(),
    });

    expect(response.status).toBe(401);
    expect(response.headers.get("x-map-auth")).toBe("refresh");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(r2.get).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("serves the current dataset from edge only after origin authenticated the session", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const cache = memoryCache();
    const r2 = memoryR2();

    const edgeCookie = await authorizeMapEdge({ cache, r2, fetchMock });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fetchMock.mockClear();
    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/map/datasets/current",
      { headers: { cookie: edgeCookie } },
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_CACHE: cache,
      MAP_ASSETS: r2,
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("x-map-cache")).toBe("EDGE-HIT");
    expect(response.headers.get("cache-control"))
      .toBe("private, max-age=5, stale-while-revalidate=30");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("serves an authenticated immutable Yandex tile from L1 without R2 or origin", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const cache = memoryCache();
    const r2 = memoryR2();
    const edgeCookie = await authorizeMapEdge({ cache, r2, fetchMock });

    const tileUrl = "https://api.sterdez.online/api/map/yandex-tiles/dataset-v1/7/77/38";
    await cache.put(new Request(tileUrl), new Response(
      '{"type":"FeatureCollection","features":[]}',
      {
        headers: {
          "content-type": "application/json",
          etag: '"tile-v1"',
          "x-map-dataset": "dataset-v1",
          "cache-control": "public, max-age=31536000, immutable",
        },
      },
    ));
    fetchMock.mockClear();
    r2.get.mockClear();

    const response = await worker.fetch(new Request(tileUrl, {
      headers: { cookie: edgeCookie },
    }), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_CACHE: cache,
      MAP_ASSETS: r2,
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("x-map-cache")).toBe("EDGE-HIT");
    expect(response.headers.get("cache-control"))
      .toBe("private, max-age=31536000, immutable");
    expect(r2.get).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses R2 as shared L2 and fills the local edge cache", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const cache = memoryCache();
    const r2 = memoryR2(new Map([
      ["yandex-tiles/dataset-v1/7/77/38.json", {
        body: new TextEncoder().encode(
          '{"type":"FeatureCollection","features":[{"id":1}]}'
        ).buffer,
        customMetadata: { originEtag: '"origin-tile-etag"' },
        httpMetadata: {
          contentType: "application/json; charset=utf-8",
          cacheControl: "public, max-age=31536000, immutable",
        },
      }],
    ]));
    const edgeCookie = await authorizeMapEdge({ cache, r2, fetchMock });

    fetchMock.mockClear();
    const ctx = executionContext();
    const tileUrl = "https://api.sterdez.online/api/map/yandex-tiles/dataset-v1/7/77/38";
    const response = await worker.fetch(new Request(tileUrl, {
      headers: { cookie: edgeCookie },
    }), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_CACHE: cache,
      MAP_ASSETS: r2,
    }, ctx);
    await ctx.flush();

    expect(response.status).toBe(200);
    expect(response.headers.get("x-map-cache")).toBe("R2-HIT");
    expect(response.headers.get("etag")).toBe('"origin-tile-etag"');
    expect(response.headers.get("cache-control"))
      .toBe("private, max-age=31536000, immutable");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cache.put).toHaveBeenCalled();
  });

  it("fills R2 and L1 only after a successful authenticated origin tile response", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const cache = memoryCache();
    const r2 = memoryR2();
    const edgeCookie = await authorizeMapEdge({ cache, r2, fetchMock });

    fetchMock.mockResolvedValueOnce(new Response(
      '{"type":"FeatureCollection","features":[{"id":2}]}',
      {
        status: 200,
        headers: {
          "content-type": "application/json; charset=utf-8",
          etag: '"origin-v2"',
          "x-map-dataset": "dataset-v1",
        },
      },
    ));
    const ctx = executionContext();
    const tileUrl = "https://api.sterdez.online/api/map/yandex-tiles/dataset-v1/7/77/38";
    const response = await worker.fetch(new Request(tileUrl, {
      headers: { cookie: edgeCookie, "if-none-match": '"origin-v2"' },
    }), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_CACHE: cache,
      MAP_ASSETS: r2,
    }, ctx);
    await ctx.flush();

    expect(response.status).toBe(304);
    expect(response.headers.get("x-map-cache")).toBe("ORIGIN-MISS");
    expect(r2.put).toHaveBeenCalledTimes(1);
    expect(cache.put).toHaveBeenCalled();
    const tileOriginRequest = fetchMock.mock.calls.at(-1)[0];
    expect(tileOriginRequest.headers.get("if-none-match")).toBeNull();
  });

  it("does not cache failed origin tile responses", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const cache = memoryCache();
    const r2 = memoryR2();
    const edgeCookie = await authorizeMapEdge({ cache, r2, fetchMock });
    cache.put.mockClear();

    fetchMock.mockResolvedValueOnce(new Response(
      '{"detail":"upstream failed"}',
      { status: 500, headers: { "content-type": "application/json" } },
    ));
    const ctx = executionContext();
    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/map/yandex-tiles/dataset-v1/7/77/38",
      { headers: { cookie: edgeCookie } },
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_CACHE: cache,
      MAP_ASSETS: r2,
    }, ctx);
    await ctx.flush();

    expect(response.status).toBe(500);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(r2.put).not.toHaveBeenCalled();
    expect(cache.put).not.toHaveBeenCalled();
  });

  it("rejects malformed immutable tile coordinates before cache or origin access", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const r2 = memoryR2();
    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/map/yandex-tiles/dataset-v1/15/0/0",
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_ASSETS: r2,
      MAP_EDGE_CACHE: memoryCache(),
    });

    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(r2.get).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects mutation methods on immutable Yandex tiles", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/map/yandex-tiles/dataset-v1/7/77/38",
      { method: "POST", body: "{}" },
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_CACHE: memoryCache(),
      MAP_ASSETS: memoryR2(),
    });

    expect(response.status).toBe(405);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
