/* global Request, Response */

import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "./worker.mjs";

describe("API origin failover proxy", () => {
  afterEach(() => vi.restoreAllMocks());

  it("replaces caller credentials with the service binding", async () => {
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
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("returns a bounded upstream error without leaking details", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("network down"));
    const response = await worker.fetch(
      new Request("https://api.dezster.ru/health/live"),
      { KOYEB_SERVICE_KEY: "bound-secret" },
    );
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "API upstream is temporarily unavailable" });
  });
});
