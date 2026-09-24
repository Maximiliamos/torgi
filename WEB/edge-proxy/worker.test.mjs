/* global Request, Response */

import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "./worker.mjs";

describe("canonical Cloudflare edge proxy", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("redirects www to the canonical HTTPS host without a loop", async () => {
    const response = await worker.fetch(new Request("https://www.sterdez.online/path?q=1"));

    expect(response.status).toBe(308);
    expect(response.headers.get("location")).toBe("https://sterdez.online/path?q=1");
    expect(response.headers.get("strict-transport-security")).toContain("max-age=31536000");
  });

  it("bypasses stale cache for deployment provenance", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ commit: "a".repeat(40) }),
    );

    const response = await worker.fetch(new Request("https://sterdez.online/deployment.json"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]).toEqual({ cache: "no-store" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("caches content-addressed assets immutably at the edge", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("export default 1", { headers: { "cache-control": "max-age=0" } }),
    );

    const response = await worker.fetch(new Request(
      "https://sterdez.online/assets/index-D0C3FuAH.js",
    ));

    expect(fetchMock.mock.calls[0][1]).toEqual({
      cf: { cacheEverything: true, cacheTtl: 31_536_000 },
    });
    expect(response.headers.get("cache-control"))
      .toBe("public, max-age=31536000, immutable");
  });

  it("does not give unhashed frontend paths an immutable lifetime", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("html", { headers: { "cache-control": "max-age=0" } }),
    );

    const response = await worker.fetch(new Request("https://sterdez.online/"));

    expect(response.headers.get("cache-control")).toBe("max-age=0");
  });

  it("routes API requests directly to the dedicated API Worker", async () => {
    const pagesFetch = vi.spyOn(globalThis, "fetch");
    const apiFetch = vi.fn().mockResolvedValue(
      Response.json({ detail: "Not authenticated" }, { status: 401 }),
    );

    const response = await worker.fetch(new Request("https://sterdez.online/api/auth/me", {
      headers: { cookie: "session=signed" },
    }), { API_PROXY: { fetch: apiFetch } });

    const upstream = apiFetch.mock.calls[0][0];
    expect(upstream.url).toBe("https://api.sterdez.online/api/auth/me");
    expect(upstream.headers.get("cookie")).toBe("session=signed");
    expect(response.status).toBe(401);
    expect(pagesFetch).not.toHaveBeenCalled();
  });

  it("returns a controlled error when Pages is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("network down"));

    const response = await worker.fetch(new Request("https://sterdez.online/"));

    expect(response.status).toBe(503);
    expect(response.headers.get("retry-after")).toBe("5");
    expect(await response.text()).toContain("Сервис временно недоступен");
  });
});
