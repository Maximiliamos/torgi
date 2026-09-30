import { describe, expect, it } from "vitest";

import { tbankrotAuthStatusLabel } from "./TBankrotView";

const base = {
  message: "",
  checked_at: "2026-09-30T01:00:00Z",
  cookie_count: 0,
  viewport: { width: 1280, height: 760 },
};

describe("TBankrot Auth Center UX", () => {
  it("shows a clear re-authentication state", () => {
    expect(tbankrotAuthStatusLabel({
      ...base,
      state: "requires_auth",
      browser_active: false,
      authenticated: false,
      requires_auth: true,
    })).toBe("Требуется авторизация");
  });

  it("shows authenticated state without exposing credentials", () => {
    expect(tbankrotAuthStatusLabel({
      ...base,
      state: "authenticated",
      browser_active: false,
      authenticated: true,
      requires_auth: false,
      cookie_count: 3,
    })).toBe("Авторизация активна");
  });
});
