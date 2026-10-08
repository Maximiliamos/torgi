import { describe, expect, it } from "vitest";
import { OperationProgressCard, operationsSourceSummaryLabel } from "./OperationProgressCard";
import { operationsSourceSummaryLabel as legacySummaryLabel } from "./MapView";
import type { OperationsProgress } from "../../lib/api";

describe("BAT-308 extracted operation progress panel", () => {
  it("preserves the published MapView helper export identity", () => {
    expect(legacySummaryLabel).toBe(operationsSourceSummaryLabel);
  });

  it("distinguishes no audited sources from ready and unavailable sources", () => {
    expect(operationsSourceSummaryLabel()).toBe("Источники ещё не проверены");
    const summary = (ready: number, total: number) => ({
      sources: { ready, total },
    }) as OperationsProgress["summary"];
    expect(operationsSourceSummaryLabel(summary(0, 0))).toBe("Источники ещё не проверены");
    expect(operationsSourceSummaryLabel(summary(2, 3))).toBe("2/3 источника готовы");
    expect(operationsSourceSummaryLabel(summary(3, 3))).toBe("3/3 источника готовы");
  });

  it("keeps the component independently exportable", () => {
    expect(typeof OperationProgressCard).toBe("function");
  });
});
