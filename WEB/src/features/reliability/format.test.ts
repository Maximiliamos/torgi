import { describe, expect, it } from "vitest";
import { moscowTimestamp, parseReliabilityTimestamp, relativeTimestamp, timestampEpoch } from "./format";

describe("Reliability timestamps", () => {
  it("interprets naive database datetimes as UTC and formats Moscow time", () => {
    const naive = "2026-11-19T02:00:00";
    expect(parseReliabilityTimestamp(naive)?.toISOString()).toBe("2026-11-19T02:00:00.000Z");
    expect(moscowTimestamp(naive)).toContain("05:00");
    expect(moscowTimestamp(naive)).toContain("19.11.2026");
  });

  it("preserves explicit UTC offsets and orders attempts consistently", () => {
    expect(timestampEpoch("2026-11-19T02:00:00"))
      .toBe(timestampEpoch("2026-11-19T05:00:00+03:00"));
    expect(timestampEpoch("2026-11-19T02:00:00Z"))
      .toBe(timestampEpoch("2026-11-19T02:00:00"));
    expect(relativeTimestamp("2026-11-19T02:00:00", Date.UTC(2026, 10, 19, 3)))
      .toBe("1 ч назад");
  });

  it("does not invent times for missing or invalid timestamps", () => {
    expect(moscowTimestamp(null)).toBe("Нет данных");
    expect(relativeTimestamp(undefined)).toBe("Нет проверки");
    expect(timestampEpoch("wrong")).toBe(0);
    expect(parseReliabilityTimestamp("invalid")).toBe(null);
  });
});
