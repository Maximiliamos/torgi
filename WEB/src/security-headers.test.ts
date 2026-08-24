import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("production security headers", () => {
  it("allows the Cloudflare Web Analytics resources injected at the edge", () => {
    const headers = readFileSync(join(process.cwd(), "public", "_headers"), "utf8");

    expect(headers).toContain("script-src 'self'");
    expect(headers).toContain("'sha256-Puf8QeJoXBahFDJL2moTJGL+2XkjzInTBH9UK8uAGuE='");
    expect(headers).toContain("https://static.cloudflareinsights.com");
    expect(headers).toContain("connect-src 'self' https://cloudflareinsights.com");
    expect(headers).toMatch(
      /script-src[^;]*https:\/\/core-renderer-tiles\.maps\.yandex\.net[^;]*;/,
    );
    expect(headers).not.toMatch(/script-src[^;]*https:\/\/\*\.maps\.yandex\.net/);
  });
});
