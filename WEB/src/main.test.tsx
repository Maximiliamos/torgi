import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./main";

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return { ...original, fetchLots: vi.fn(), fetchStats: vi.fn(), fetchLotDetail: vi.fn() };
});

import { fetchLots, fetchStats } from "./lib/api";

describe("App states", () => {
  beforeEach(() => {
    vi.mocked(fetchStats).mockResolvedValue({
      total_lots: 0, active_lots: 0, appraised_lots: 0, average_discount: null, region: "yaroslavl"
    });
  });

  it("renders an empty result without crashing", async () => {
    vi.mocked(fetchLots).mockResolvedValue({ items: [], total: 0 });
    render(<App />);
    await waitFor(() => expect(fetchLots).toHaveBeenCalled());
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
  });

  it("renders a network error", async () => {
    vi.mocked(fetchLots).mockRejectedValue(new Error("API offline"));
    render(<App />);
    expect(await screen.findByText("API offline")).toBeInTheDocument();
  });
});
