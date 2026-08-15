import { beforeEach, describe, expect, it, vi } from "vitest";
import { onRequest } from "../../functions/api/[[path]]";

const env = {
  KOYEB_API_ORIGIN: "https://api.example.test",
  KOYEB_SERVICE_KEY: "server-side-service-key",
  TUNNEL_REGISTRY: { get: vi.fn().mockResolvedValue(null) },
};

function context(path: string, init?: RequestInit) {
  return {
    request: new Request(`https://dezster.test${path}`, init),
    env,
  };
}

describe("Cloudflare Pages API boundary", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("retries temporary upstream failures only for GET", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("temporary", { status: 503 }))
      .mockResolvedValueOnce(Response.json({ ok: true }));

    const response = await onRequest(context("/api/lots"));

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry POST and forwards its body once", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("temporary", { status: 503 }));

    const response = await onRequest(context("/api/saved-searches", {
      method: "POST",
      body: JSON.stringify({ name: "audit" }),
      headers: { "content-type": "application/json" },
    }));

    expect(response.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not duplicate a timed-out external catalogue read", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("source unavailable", { status: 503 }));

    const response = await onRequest(context("/api/search/lot-online?region=76"));

    expect(response.status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("replaces client-supplied credentials at the trust boundary", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json({ ok: true }));

    await onRequest(context("/api/lots", {
      headers: {
        authorization: "Bearer attacker",
        "x-api-key": "attacker",
        "x-forwarded-host": "attacker.invalid",
        "x-forwarded-proto": "http",
      },
    }));

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("authorization")).toBeNull();
    expect(headers.get("x-api-key")).toBe(env.KOYEB_SERVICE_KEY);
    expect(headers.get("x-forwarded-host")).toBe("dezster.test");
    expect(headers.get("x-forwarded-proto")).toBe("https");
  });

  it("fails closed when the configured origin is not HTTPS", async () => {
    const response = await onRequest({
      request: new Request("https://dezster.test/api/lots"),
      env: { ...env, KOYEB_API_ORIGIN: "http://origin.invalid" },
    });

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "Upstream API configuration is invalid" });
  });
});
