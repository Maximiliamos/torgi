/* global Request, Response, console */

import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "./worker.mjs";

describe("API origin failover proxy", () => {
  afterEach(() => vi.restoreAllMocks());

  it("proxies only allowlisted public GIS GET paths", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ content: [] }),
    );
    const response = await worker.fetch(new Request(
      "https://api.dezster.ru/__public-source/torgi/new/api/public/lotcards/search?page=0&size=1",
    ), {});
    expect(fetchMock.mock.calls[0][0].url)
      .toBe("https://torgi.gov.ru/new/api/public/lotcards/search?page=0&size=1");
    expect(response.status).toBe(200);

    const denied = await worker.fetch(new Request(
      "https://api.dezster.ru/__public-source/torgi/admin/private",
    ), {});
    expect(denied.status).toBe(404);
  });

  it("replaces caller credentials with the service binding", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ status: "ok" }),
    );

    const response = await worker.fetch(new Request("https://api.dezster.ru/api/auth/me", {
      headers: { authorization: "caller", "x-api-key": "caller-key", cookie: "session=signed" },
    }), { KOYEB_SERVICE_KEY: "bound-secret" });

    const upstream = fetchMock.mock.calls[0][0];
    expect(upstream.url).toBe("https://194-226-126-233.sslip.io/api/auth/me");
    expect(upstream.headers.get("authorization")).toBeNull();
    expect(upstream.headers.get("x-api-key")).toBe("bound-secret");
    expect(upstream.headers.get("cookie")).toBe("session=signed");
    expect(upstream.headers.get("x-forwarded-host")).toBe("api.dezster.ru");
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

    const response = await worker.fetch(new Request("https://api.dezster.ru/api/auth/login", {
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
      new Request("https://api.dezster.ru/health/live"),
      { KOYEB_SERVICE_KEY: "bound-secret" },
    );
    expect(response.status).toBe(502);
    expect(response.headers.get("x-request-id")).toBeTruthy();
    expect(await response.json()).toEqual({ detail: "API upstream is temporarily unavailable" });
  });

  it("preserves a bounded caller request ID for end-to-end correlation", async () => {
    vi.spyOn(console, "log").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("ok"));

    const response = await worker.fetch(new Request("https://api.dezster.ru/health/live", {
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
      .mockResolvedValueOnce(Response.json({ status: "alive" }));

    const response = await worker.fetch(new Request("https://api.dezster.ru/health/live"), {
      KOYEB_SERVICE_KEY: "bound-secret",
      SECONDARY_API_ORIGIN: "https://secondary.example.test",
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0].url).toBe("https://secondary.example.test/health/live");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "alive" });
  });

  it.each(["POST", "PUT", "PATCH", "DELETE"])("never retries %s mutations", async (method) => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("primary reset"));
    const response = await worker.fetch(new Request("https://api.dezster.ru/api/auth/login", {
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

    const response = await worker.fetch(new Request("https://api.dezster.ru/api/lots"), {
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

    const response = await worker.fetch(new Request("https://api.dezster.ru/api/map/lots", {
      headers: { "if-none-match": '"map-v1"' },
    }), { KOYEB_SERVICE_KEY: "bound-secret" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0].headers.get("if-none-match")).toBe('"map-v1"');
    expect(response.status).toBe(304);
    expect(response.headers.get("etag")).toBe('"map-v1"');
  });
});
