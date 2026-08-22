import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("production security headers", () => {
  it("allows the Cloudflare Web Analytics resources injected at the edge", () => {
    const headers = readFileSync(join(process.cwd(), "public", "_headers"), "utf8");

    expect(headers).toContain("script-src 'self'");
    expect(headers).toContain("https://static.cloudflareinsights.com");
    expect(headers).toContain("connect-src 'self' https://api.dezster.ru");
    expect(headers).toContain("https://cloudflareinsights.com");
  });
});
