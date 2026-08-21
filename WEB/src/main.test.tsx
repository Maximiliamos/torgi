import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, AuthenticatedApp } from "./main";

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return { ...original, fetchLots: vi.fn(), fetchStats: vi.fn(), fetchLotDetail: vi.fn(), fetchCurrentUser: vi.fn() };
});

import { ApiError, fetchCurrentUser, fetchLots, fetchStats } from "./lib/api";

afterEach(async () => {
  cleanup();
  // React schedules part of concurrent unmount work with setImmediate. Let it
  // drain while jsdom still owns `window`, instead of racing environment teardown.
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
});

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

describe("Authentication bootstrap", () => {
  it("shows a recoverable service state instead of a false logout", async () => {
    vi.mocked(fetchCurrentUser).mockRejectedValue(new ApiError(
      "Сервис временно недоступен. Повторите попытку через несколько секунд.",
    ));

    render(<AuthenticatedApp />);

    expect(await screen.findByRole("heading", { name: "Нет связи с сервисом" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Вход" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Повторить/ })).toBeInTheDocument();
  });

  it("shows login only when the session is actually unauthorized", async () => {
    vi.mocked(fetchCurrentUser).mockRejectedValue(new ApiError("Authentication required", 401));

    render(<AuthenticatedApp />);

    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
  });
});
