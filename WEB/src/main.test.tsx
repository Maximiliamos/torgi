import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, AuthenticatedApp, registryActiveFilterCount } from "./main";

vi.mock("./lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./lib/api")>();
  return { ...original, fetchLots: vi.fn(), fetchStats: vi.fn(), fetchLotDetail: vi.fn(), fetchCurrentUser: vi.fn(), fetchRegions: vi.fn(), searchOnline: vi.fn() };
});

import { ApiError, fetchCurrentUser, fetchLots, fetchRegions, fetchStats, searchOnline } from "./lib/api";

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
    vi.mocked(fetchRegions).mockResolvedValue([{ code: "76", name: "Ярославская область" }]);
    vi.mocked(searchOnline).mockResolvedValue({ source: "torgi-gov", items: [], meta: { total: 0, total_pages: 0 } });
  });

  it("renders an empty result without crashing", async () => {
    vi.mocked(fetchLots).mockResolvedValue({ items: [], total: 0 });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Реестр" }));
    await waitFor(() => expect(fetchLots).toHaveBeenCalled());
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
    expect(screen.getByText("По заданным фильтрам лотов нет")).toBeInTheDocument();
  });

  it("keeps loaded registry rows visible while a refresh is pending", async () => {
    const lot = {
      id: 42, external_id: "42", title: "Лот без визуального мигания", description: null,
      category: "apartment", region_slug: "yaroslavl", address: "Ярославль",
      current_price: 1_000_000, market_price: 1_400_000, discount_percent: 28.6,
      risk_score: 2, rating: 8, auction_status: "active", lot_url: null,
      building_area: 50, land_area: null, land_area_sotki: null, area: 50,
      last_update: null, needs_human_review: false,
    };
    vi.mocked(fetchLots).mockResolvedValueOnce({ items: [lot], total: 1 });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Реестр" }));
    expect(await screen.findByText("Лот без визуального мигания")).toBeInTheDocument();

    vi.mocked(fetchLots).mockReturnValueOnce(new Promise(() => undefined));
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));

    expect(await screen.findByText("Обновляем")).toBeInTheDocument();
    expect(screen.getByText("Лот без визуального мигания")).toBeInTheDocument();
  });

  it("submits online search with Enter and shows an explicit empty state", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Поиск" }));
    const field = screen.getByPlaceholderText("Название, адрес, кадастровый номер");
    fireEvent.change(field, { target: { value: "квартира" } });
    fireEvent.keyDown(field, { key: "Enter", code: "Enter", charCode: 13 });
    const form = field.closest("form");
    if (!form) throw new Error("Search form not found");
    fireEvent.submit(form);

    await waitFor(() => expect(searchOnline).toHaveBeenCalled());
    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("counts only non-default registry filters", () => {
    expect(registryActiveFilterCount({
      city_slug: "yaroslavl", page: 1, per_page: 18, search: "", categories: [],
      statuses: ["active", "scheduled"], min_discount: 0, max_discount: 100, sort: "recommended",
    })).toBe(0);
    expect(registryActiveFilterCount({
      city_slug: "yaroslavl", page: 1, per_page: 18, search: "дом", categories: ["house"],
      statuses: ["active"], min_discount: 0, max_discount: 100, sort: "discount",
    }, true)).toBe(5);
  });

  it("renders a network error", async () => {
    vi.mocked(fetchLots).mockRejectedValue(new Error("API offline"));
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Реестр" }));
    expect(await screen.findByText("API offline")).toBeInTheDocument();
  });

  it("opens the nationwide map as the primary workspace", () => {
    render(<App />);
    expect(screen.getByRole("button", { name: "Карта" })).toHaveClass("active");
    expect(screen.getByText("Лоты недвижимости")).toBeInTheDocument();
    expect(screen.getByLabelText("Текущее московское время").closest(".mapBottomStatus")).not.toBeNull();
    const mapFrame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    expect(mapFrame.srcdoc).toContain("legacyManager=new ymaps.ObjectManager({clusterize:true");
    expect(mapFrame.srcdoc).toContain("tileManager=new ymaps.ObjectManager({clusterize:false");
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
