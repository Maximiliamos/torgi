/* global Request, Response */

import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "./worker.mjs";

describe("canonical Cloudflare edge proxy", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("redirects www to the canonical HTTPS host without a loop", async () => {
    const response = await worker.fetch(new Request("https://www.dezster.ru/path?q=1"));

    expect(response.status).toBe(308);
    expect(response.headers.get("location")).toBe("https://dezster.ru/path?q=1");
    expect(response.headers.get("strict-transport-security")).toContain("max-age=31536000");
  });

  it("bypasses stale cache for deployment provenance", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ commit: "a".repeat(40) }),
    );

    const response = await worker.fetch(new Request("https://dezster.ru/deployment.json"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]).toEqual({ cache: "no-store" });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("returns a controlled error when Pages is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("network down"));

    const response = await worker.fetch(new Request("https://dezster.ru/"));

    expect(response.status).toBe(503);
    expect(response.headers.get("retry-after")).toBe("5");
    expect(await response.text()).toContain("Сервис временно недоступен");
  });
});
