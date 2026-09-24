/* global Request, Response, console */

import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "./worker.mjs";

async function makeMapToken(secret, version, { expiresIn = 120, sub = 1 } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    sub,
    v: version,
    iat: now,
    exp: now + expiresIn,
  };
  const base64Url = (bytes) => {
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  };
  const encoded = base64Url(new TextEncoder().encode(JSON.stringify(payload)));
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = new Uint8Array(await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(encoded),
  ));
  return `${encoded}.${base64Url(signature)}`;
}

function installEdgeCache({ hit = null } = {}) {
  const cache = {
    match: vi.fn().mockResolvedValue(hit),
    put: vi.fn().mockResolvedValue(undefined),
  };
  Object.defineProperty(globalThis, "caches", {
    configurable: true,
    value: { default: cache },
  });
  return cache;
}

describe("API origin failover proxy", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete globalThis.caches;
  });

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

  it("serves authenticated prepared tiles from the edge cache without origin access", async () => {
    const secret = "map-edge-secret-for-tests";
    const version = "dataset-edge";
    const token = await makeMapToken(secret, version);
    const edgeCache = installEdgeCache({
      hit: new Response('{"type":"FeatureCollection","features":[]}', {
        headers: { etag: '"edge-etag"', "content-type": "application/json" },
      }),
    });
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await worker.fetch(new Request(
      `https://api.sterdez.online/api/map/yandex-tiles/${version}/7/10/20`,
      { headers: { "x-map-token": token } },
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_TOKEN_SECRET: secret,
    }, { waitUntil: vi.fn() });

    expect(response.status).toBe(200);
    expect(response.headers.get("x-map-cache")).toBe("EDGE-HIT");
    expect(response.headers.get("cache-control")).toBe("private, max-age=86400, immutable");
    expect(edgeCache.match).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("serves authenticated prepared tiles from R2 and warms the local edge cache", async () => {
    const secret = "map-edge-secret-for-tests";
    const version = "dataset-r2";
    const token = await makeMapToken(secret, version);
    const edgeCache = installEdgeCache();
    const body = new TextEncoder().encode('{"type":"FeatureCollection","features":[]}');
    const object = {
      httpEtag: '"r2-etag"',
      arrayBuffer: vi.fn().mockResolvedValue(body.buffer),
      writeHttpMetadata: vi.fn(),
    };
    const r2 = {
      get: vi.fn().mockResolvedValue(object),
      put: vi.fn(),
    };
    const waitUntil = vi.fn();
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await worker.fetch(new Request(
      `https://api.sterdez.online/api/map/yandex-tiles/${version}/7/10/20`,
      { headers: { "x-map-token": token } },
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_TOKEN_SECRET: secret,
      MAP_ASSETS: r2,
    }, { waitUntil });

    expect(response.status).toBe(200);
    expect(response.headers.get("x-map-cache")).toBe("R2-HIT");
    expect(r2.get).toHaveBeenCalledWith(`tiles/${version}/7/10/20.json`);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(waitUntil).toHaveBeenCalledTimes(1);
    expect(edgeCache.put).toHaveBeenCalledTimes(1);
  });

  it("fills R2 and the edge cache after an authenticated origin miss", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const secret = "map-edge-secret-for-tests";
    const version = "dataset-origin";
    const token = await makeMapToken(secret, version);
    const edgeCache = installEdgeCache();
    const r2 = {
      get: vi.fn().mockResolvedValue(null),
      put: vi.fn().mockResolvedValue(undefined),
    };
    const waitUntil = vi.fn();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"type":"FeatureCollection","features":[]}', {
        status: 200,
        headers: { etag: '"origin-etag"', "content-type": "application/json" },
      }),
    );

    const response = await worker.fetch(new Request(
      `https://api.sterdez.online/api/map/yandex-tiles/${version}/7/10/20`,
      {
        headers: {
          "x-map-token": token,
          cookie: "bankrotai_session=signed",
        },
      },
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_TOKEN_SECRET: secret,
      MAP_ASSETS: r2,
      PRIMARY_API_ORIGIN: "https://home.example.test",
    }, { waitUntil });

    expect(response.status).toBe(200);
    expect(response.headers.get("x-map-cache")).toBe("ORIGIN-MISS");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const upstream = fetchMock.mock.calls[0][0];
    expect(upstream.url).toContain(
      `home.example.test/api/map/yandex-tiles/${version}/7/10/20`,
    );
    expect(upstream.headers.get("x-map-token")).toBeNull();
    expect(upstream.headers.get("cookie")).toBe("bankrotai_session=signed");
    expect(r2.put).toHaveBeenCalledTimes(1);
    expect(edgeCache.put).toHaveBeenCalledTimes(1);
    expect(waitUntil).toHaveBeenCalledTimes(1);
  });

  it("rejects an invalid prepared-tile token before consulting caches or origin", async () => {
    const secret = "map-edge-secret-for-tests";
    const version = "dataset-denied";
    const edgeCache = installEdgeCache();
    const r2 = { get: vi.fn(), put: vi.fn() };
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await worker.fetch(new Request(
      `https://api.sterdez.online/api/map/yandex-tiles/${version}/7/10/20`,
      { headers: { "x-map-token": "invalid.token" } },
    ), {
      KOYEB_SERVICE_KEY: "bound-secret",
      MAP_EDGE_TOKEN_SECRET: secret,
      MAP_ASSETS: r2,
    }, { waitUntil: vi.fn() });

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(edgeCache.match).not.toHaveBeenCalled();
    expect(r2.get).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps authentication and mutation responses non-cacheable", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ id: 1 }));

    const response = await worker.fetch(new Request(
      "https://api.sterdez.online/api/auth/me",
    ), { KOYEB_SERVICE_KEY: "bound-secret" });

    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});
