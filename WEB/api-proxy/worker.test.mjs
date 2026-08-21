/* global Request, Response, console */

import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "./worker.mjs";

describe("API origin failover proxy", () => {
  afterEach(() => vi.restoreAllMocks());

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
});
