import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/api")>();
  return {
    ...original,
    fetchCurrentMapDataset: vi.fn(),
    fetchCurrentUser: vi.fn(),
    fetchMapLotDetail: vi.fn(),
    fetchMapLotsSWR: vi.fn(),
    fetchMapTile: vi.fn(),
    fetchOperationsProgress: vi.fn(),
    fetchRegions: vi.fn(),
    setReviewStatus: vi.fn(),
  };
});

import {
  fetchCurrentMapDataset,
  fetchCurrentUser,
  fetchMapLotDetail,
  fetchMapLotsSWR,
  fetchMapTile,
  fetchOperationsProgress,
  fetchRegions,
  setReviewStatus,
  type MapDataset,
  type MapLot,
  type MapTilePayload,
} from "../../lib/api";
import { applyMapTileReviewOverrides, fetchCachedMapTile, MapView } from "./MapView";

function sendViewport(
  frame: HTMLIFrameElement,
  bounds: [number, number, number, number] = [37.4, 55.6, 37.9, 55.9],
  zoom = 10,
) {
  const match = frame.srcdoc.match(/const channel=("[^"]+")/);
  if (!match) throw new Error("Map iframe channel was not found");
  window.dispatchEvent(new MessageEvent("message", {
    source: frame.contentWindow,
    data: {
      type: "bankrotai-viewport",
      channel: JSON.parse(match[1]) as string,
      bounds,
      zoom,
    },
  }));
}

const dataset = (version: string): MapDataset => ({
  version,
  point_count: 100,
  tile_count: 20,
  max_zoom: 14,
  point_zoom: 12,
  published_at: "2026-09-06T00:00:00",
});

const mapLot = (id: number, reviewStatus: string | null = null): MapLot => ({
  id,
  external_id: `lot-${id}`,
  title: `Лот ${id}`,
  description: "Описание",
  address: "Москва",
  cadastral_number: null,
  category: "real_estate",
  region: "Москва",
  status: "active",
  is_archived: false,
  review_status: reviewStatus,
  current_price: 1_000_000,
  lat: 55.6,
  lon: 37.4,
  geometry: null,
  confidence: "high",
  source: "test",
  source_name: "Test",
  source_url: null,
  gis_torgi_url: null,
  etp_url: null,
  torgi_russia_url: null,
  image_url: null,
  image_urls: [],
  procedure_number: null,
  application_deadline: null,
  auction_at: null,
  sources: [],
});

function sendMapMessage(frame: HTMLIFrameElement, type: string, payload: Record<string, unknown> = {}) {
  const match = frame.srcdoc.match(/const channel=("[^"]+")/);
  if (!match) throw new Error("Map iframe channel was not found");
  window.dispatchEvent(new MessageEvent("message", {
    source: frame.contentWindow,
    data: { type, channel: JSON.parse(match[1]) as string, ...payload },
  }));
}

function deferredTile() {
  let resolve!: (payload: MapTilePayload) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<MapTilePayload>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe("tile map startup", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchCurrentUser).mockResolvedValue({ id: 1, username: "reader", role: "reader" });
    vi.mocked(fetchRegions).mockResolvedValue([]);
    vi.mocked(fetchMapTile).mockResolvedValue({ features: [] });
    vi.mocked(fetchOperationsProgress).mockResolvedValue({
      sync: null,
      geocoding: { total: 100, geocoded: 25, remaining: 75, terminal_failures: 2, percent: 25, task: null },
    });
    vi.mocked(setReviewStatus).mockResolvedValue({ lot_id: 1, status: "approved" });
  });

  it("waits for dataset metadata and never starts the legacy lots request", async () => {
    let resolveDataset!: (dataset: MapDataset) => void;
    vi.mocked(fetchCurrentMapDataset).mockReturnValue(new Promise((resolve) => {
      resolveDataset = resolve;
    }));

    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    act(() => sendViewport(frame));

    expect(fetchCurrentMapDataset).toHaveBeenCalledTimes(1);
    expect(fetchMapLotsSWR).not.toHaveBeenCalled();
    expect(fetchMapTile).not.toHaveBeenCalled();

    await act(async () => resolveDataset(dataset("v-step-4")));

    await waitFor(() => expect(fetchMapTile).toHaveBeenCalled());
    expect(fetchMapLotsSWR).not.toHaveBeenCalled();
  });

  it("shows exact geocoding completion and queue counters", async () => {
    vi.mocked(fetchCurrentMapDataset).mockResolvedValue(dataset("v-progress"));
    vi.mocked(fetchOperationsProgress).mockResolvedValue({
      sync: {
        task_id: "sync-1", status: "running", sources: [{
          source_system: "torgi-russia.ru", status: "running", items_seen: 420,
          pages_scanned: 10, total_pages: 20, percent: 50, current_category: "Регион 4 из 8",
        }],
      },
      geocoding: {
        total: 1000, geocoded: 640, remaining: 360, terminal_failures: 7, percent: 64,
        task: { task_id: "geo-1", status: "running", progress: {
          queued: 250, processed: 125, geocoded: 110, failed: 15, percent: 90,
          unique_queries: 230, resolved_queries: 230, cache_hits: 20, phase: "saving",
        } },
      },
    });

    render(<MapView refreshToken={0} />);

    expect(await screen.findByText("Геокодирование — 64.0%")).toBeInTheDocument();
    expect(screen.getByText("640 из 1000 с координатами")).toBeInTheDocument();
    expect(screen.getByText(/В очереди: 360/)).toBeInTheDocument();
    expect(screen.getByText(/Текущий пакет: 125 из 250/)).toBeInTheDocument();
    expect(screen.getByText(/Запросы геокодера: 230 из 230 · из кеша 20/)).toBeInTheDocument();
    expect(screen.getByText(/torgi-russia.ru: 420 лотов/)).toBeInTheDocument();
  });

  it("does not silently fall back to legacy lots when current dataset is unavailable", async () => {
    vi.mocked(fetchCurrentMapDataset).mockRejectedValue(new Error("dataset unavailable"));

    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    act(() => sendViewport(frame));

    expect(await screen.findByText("Актуальный набор данных карты пока недоступен")).toBeInTheDocument();
    expect(fetchMapLotsSWR).not.toHaveBeenCalled();
    expect(fetchMapTile).not.toHaveBeenCalled();
  });

  it("ignores a stale viewport response that resolves after the current viewport", async () => {
    vi.mocked(fetchCurrentMapDataset).mockResolvedValue(dataset("v-race"));
    const first = deferredTile();
    const second = deferredTile();
    vi.mocked(fetchMapTile)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    await waitFor(() => expect(fetchCurrentMapDataset).toHaveBeenCalled());
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(1));
    act(() => sendViewport(frame, [131.8, 43.1, 131.81, 43.11], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(2));

    await act(async () => second.resolve({ features: [
      { kind: "lot", id: 2, lat: 43.1, lon: 131.8 },
      { kind: "lot", id: 3, lat: 43.11, lon: 131.81 },
    ] }));
    expect(await screen.findByText("На карте: 2 объектов слоя")).toBeInTheDocument();

    await act(async () => first.resolve({
      features: [{ kind: "lot", id: 1, lat: 55.6, lon: 37.4 }],
    }));
    expect(screen.getByText("На карте: 2 объектов слоя")).toBeInTheDocument();
  });

  it("never commits a late V1 tile after the current dataset switches to V2", async () => {
    vi.mocked(fetchCurrentMapDataset)
      .mockResolvedValueOnce(dataset("v1"))
      .mockResolvedValueOnce(dataset("v2"));
    const v1 = deferredTile();
    const v2 = deferredTile();
    vi.mocked(fetchMapTile)
      .mockReturnValueOnce(v1.promise)
      .mockReturnValueOnce(v2.promise);

    const view = render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => sendMapMessage(frame, "bankrotai-ready", { instanceId: "dataset-race" }));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledWith("v1", expect.anything(), expect.anything(), expect.anything()));

    view.rerender(<MapView refreshToken={1} />);
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledWith("v2", expect.anything(), expect.anything(), expect.anything()));
    await act(async () => v2.resolve({
      features: [{ kind: "lot", id: 2, lat: 55.6, lon: 37.4 }],
    }));
    await waitFor(() => expect(screen.getByText("На карте: 1 объектов слоя")).toBeInTheDocument());

    await act(async () => v1.resolve({
      features: [{ kind: "lot", id: 1, lat: 55.6, lon: 37.4 }],
    }));
    const nonEmptySyncs = postMessage.mock.calls
      .map(([message]) => message as { type?: string; entries?: Array<{ key: string }> })
      .filter((message) => message.type === "sync-tiles" && message.entries?.length);
    const latestSync = nonEmptySyncs[nonEmptySyncs.length - 1];
    expect(latestSync?.entries?.every((entry: { key: string }) => entry.key.startsWith("v2/"))).toBe(true);
    expect(fetchMapTile).toHaveBeenCalledTimes(2);
  });

  it("does not start a new load cycle for an identical normalized tile set", async () => {
    vi.mocked(fetchCurrentMapDataset).mockResolvedValue(dataset("v-identical"));
    vi.mocked(fetchMapTile).mockResolvedValue({ features: [] });

    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    await waitFor(() => expect(fetchCurrentMapDataset).toHaveBeenCalled());
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(1));
    act(() => sendViewport(frame, [37.405, 55.605, 37.415, 55.615], 10));
    await act(async () => Promise.resolve());

    expect(fetchMapTile).toHaveBeenCalledTimes(1);
  });

  it("reuses a completed tile after it leaves and re-enters the viewport", async () => {
    vi.mocked(fetchCurrentMapDataset).mockResolvedValue(dataset("v-pan-back"));
    vi.mocked(fetchMapTile).mockResolvedValue({ features: [] });

    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    await waitFor(() => expect(fetchCurrentMapDataset).toHaveBeenCalled());
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(1));
    act(() => sendViewport(frame, [131.8, 43.1, 131.81, 43.11], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(2));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await act(async () => Promise.resolve());

    expect(fetchMapTile).toHaveBeenCalledTimes(2);
  });
});

describe("tile marker review updates", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchCurrentUser).mockResolvedValue({ id: 1, username: "reader", role: "reader" });
    vi.mocked(fetchRegions).mockResolvedValue([]);
    vi.mocked(fetchCurrentMapDataset).mockResolvedValue(dataset("v-review"));
    vi.mocked(fetchMapLotDetail).mockImplementation(async (id) => mapLot(id));
    vi.mocked(setReviewStatus).mockImplementation(async (id, status) => ({ lot_id: id, status }));
  });

  it("targets only the affected visible marker without refetching its tile", async () => {
    vi.mocked(fetchMapTile).mockResolvedValue({ features: [
      { kind: "lot", id: 1, lat: 55.6, lon: 37.4, review_status: null },
      { kind: "lot", id: 2, lat: 55.61, lon: 37.41, review_status: "rejected" },
      { kind: "lot", id: 3, lat: 55.62, lon: 37.42, review_status: null },
    ] });

    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => sendMapMessage(frame, "bankrotai-ready", { instanceId: "review-test" }));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(1));
    act(() => sendMapMessage(frame, "bankrotai-select", { lotId: 1 }));
    await screen.findByText("Лот 1");
    expect(fetchMapLotDetail).toHaveBeenCalledWith(1);
    const tileCalls = vi.mocked(fetchMapTile).mock.calls.length;

    await act(async () => screen.getByRole("button", { name: "Интересен" }).click());
    await waitFor(() => expect(setReviewStatus).toHaveBeenCalledWith(1, "approved"));
    const reviewCommands = postMessage.mock.calls
      .map(([message]) => message as { type?: string; lotId?: number; status?: string })
      .filter((message) => message.type === "update-lot-review");

    expect(reviewCommands).toEqual([expect.objectContaining({
      type: "update-lot-review", lotId: 1, status: "approved", revision: 1,
    })]);
    expect(reviewCommands.some((message) => message.lotId === 2 || message.lotId === 3)).toBe(false);
    expect(fetchMapTile).toHaveBeenCalledTimes(tileCalls);
    expect(screen.getByText("Лот 1")).toBeInTheDocument();
    const updateSource = frame.srcdoc.slice(
      frame.srcdoc.indexOf("function updateTileReview"),
      frame.srcdoc.indexOf("function emitViewport"),
    );
    expect(updateSource).toContain("tileManager.objects.setObjectOptions(id,opts(updated))");
    expect(updateSource).not.toContain("removeAll");
    expect(updateSource).not.toContain("tileManager.add");
  });

  it("uses the latest status across repeated marker-only updates", async () => {
    vi.mocked(fetchMapTile).mockResolvedValue({
      features: [{ kind: "lot", id: 1, lat: 55.6, lon: 37.4, review_status: null }],
    });
    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => sendMapMessage(frame, "bankrotai-ready", { instanceId: "repeat-test" }));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalled());
    act(() => sendMapMessage(frame, "bankrotai-select", { lotId: 1 }));
    await screen.findByText("Лот 1");

    for (const [index, name] of ["Интересен", "Плохой", /Сомневаюсь/].entries()) {
      await act(async () => screen.getByRole("button", { name }).click());
      await waitFor(() => expect(setReviewStatus).toHaveBeenCalledTimes(index + 1));
    }
    await waitFor(() => expect(setReviewStatus).toHaveBeenCalledTimes(3));
    const updates = postMessage.mock.calls
      .map(([message]) => message as { type?: string; status?: string })
      .filter((message) => message.type === "update-lot-review");
    expect(updates.map((message) => message.status)).toEqual(["approved", "rejected", "maybe"]);
    expect(fetchMapTile).toHaveBeenCalledTimes(1);
  });

  it("applies an override when a previously invisible lot tile is loaded later", async () => {
    vi.mocked(fetchMapTile)
      .mockResolvedValueOnce({ features: [{ kind: "lot", id: 1, lat: 55.6, lon: 37.4 }] })
      .mockResolvedValueOnce({ features: [{ kind: "lot", id: 9, lat: 43.1, lon: 131.8, review_status: null }] });
    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => sendMapMessage(frame, "bankrotai-ready", { instanceId: "invisible-test" }));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(1));
    act(() => sendMapMessage(frame, "bankrotai-select", { lotId: 9 }));
    await screen.findByText("Лот 9");
    await act(async () => screen.getByRole("button", { name: "Интересен" }).click());
    await waitFor(() => expect(setReviewStatus).toHaveBeenCalledWith(9, "approved"));
    await waitFor(() => expect(postMessage.mock.calls.some(([message]) =>
      (message as { type?: string; lotId?: number }).type === "update-lot-review" &&
      (message as { lotId?: number }).lotId === 9,
    )).toBe(true));
    act(() => sendViewport(frame, [131.8, 43.1, 131.81, 43.11], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(2));

    await waitFor(() => {
      const syncCommands = postMessage.mock.calls
        .map(([message]) => message as { type?: string; entries?: Array<{ features: Array<{ id: number; review_status?: string }> }> })
        .filter((message) => message.type === "sync-tiles" && message.entries?.some((entry) => entry.features.some((feature) => feature.id === 9)));
      const latestSync = syncCommands[syncCommands.length - 1];
      const futureFeature = latestSync?.entries?.flatMap((entry) => entry.features).find((feature) => feature.id === 9);
      expect(futureFeature?.review_status).toBe("approved");
    });
  });
});

describe("tile and legacy rendering isolation", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchCurrentUser).mockResolvedValue({ id: 1, username: "reader", role: "reader" });
    vi.mocked(fetchRegions).mockResolvedValue([{ code: "77", name: "Москва" }]);
    vi.mocked(fetchCurrentMapDataset).mockResolvedValue(dataset("v-modes"));
    vi.mocked(fetchMapTile).mockResolvedValue({
      features: [{ kind: "cluster", id: "cluster-1", lat: 55.7, lon: 37.6, count: 12 }],
    });
    vi.mocked(fetchMapLotsSWR).mockResolvedValue({
      data: {
        items: [{
          id: 71, title: "Legacy lot", address: "Москва", current_price: 2_000_000,
          start_price: 1_500_000, region_code: "77", status: "active", is_archived: false,
          review_status: null, lat: 55.71, lon: 37.61,
        }],
        returned: 1, limit: 250, truncated: false, total: 1, mapped_total: 1,
        without_coordinates: 0, updated_at: "2026-09-06T00:00:00", statistics_exact: true,
        timings: { server_ms: 1 },
      },
      networkMs: 1,
      fromCache: false,
    });
  });

  it("keeps server clusters unclustered and restores tile mode after a legacy filter", async () => {
    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    expect(frame.srcdoc).toContain("legacyManager=new ymaps.ObjectManager({clusterize:true");
    expect(frame.srcdoc).toContain("tileManager=new ymaps.ObjectManager({clusterize:false})");
    expect(frame.srcdoc).toContain("activateManager(tileManager)");
    expect(frame.srcdoc).toContain("activateManager(legacyManager)");

    act(() => sendMapMessage(frame, "bankrotai-ready", { instanceId: "mode-test" }));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(postMessage.mock.calls.some(([message]) =>
      (message as { type?: string; entries?: unknown[] }).type === "sync-tiles" &&
      ((message as { entries?: unknown[] }).entries?.length ?? 0) > 0,
    )).toBe(true));

    const minPrice = screen.getByText("Стартовая цена от").parentElement?.querySelector("input");
    if (!minPrice) throw new Error("Minimum price input not found");
    fireEvent.change(minPrice, { target: { value: "1000000" } });
    await act(async () => screen.getByRole("button", { name: "Применить" }).click());
    await waitFor(() => expect(fetchMapLotsSWR).toHaveBeenCalled());
    await waitFor(() => expect(postMessage.mock.calls.some(([message]) =>
      (message as { type?: string; lots?: Array<{ id: number }> }).type === "replace-lots" &&
      (message as { lots?: Array<{ id: number }> }).lots?.[0]?.id === 71,
    )).toBe(true));
    const tileCallsBeforeReset = vi.mocked(fetchMapTile).mock.calls.length;

    await act(async () => screen.getByRole("button", { name: "Сбросить" }).click());
    await waitFor(() => {
      const nonEmptyTileSyncs = postMessage.mock.calls.filter(([message]) =>
        (message as { type?: string; entries?: unknown[] }).type === "sync-tiles" &&
        ((message as { entries?: unknown[] }).entries?.length ?? 0) > 0,
      );
      expect(nonEmptyTileSyncs.length).toBeGreaterThanOrEqual(2);
    });
    expect(fetchMapTile).toHaveBeenCalledTimes(tileCallsBeforeReset);
    expect(frame.srcdoc).toContain("lots=Array.isArray(data.lots)?data.lots:[];if(mode!=='tiles')renderLots()");
  });

  it("ignores a late legacy filter response after the filter is cleared", async () => {
    let resolveLegacy!: (value: Awaited<ReturnType<typeof fetchMapLotsSWR>>) => void;
    vi.mocked(fetchMapLotsSWR).mockReturnValue(new Promise((resolve) => { resolveLegacy = resolve; }));
    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => sendMapMessage(frame, "bankrotai-ready", { instanceId: "legacy-race" }));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalled());

    const minPrice = screen.getByText("Стартовая цена от").parentElement?.querySelector("input");
    if (!minPrice) throw new Error("Minimum price input not found");
    fireEvent.change(minPrice, { target: { value: "1000000" } });
    await act(async () => screen.getByRole("button", { name: "Применить" }).click());
    await waitFor(() => expect(fetchMapLotsSWR).toHaveBeenCalledTimes(1));
    await act(async () => screen.getByRole("button", { name: "Сбросить" }).click());

    await act(async () => resolveLegacy({
      data: {
        items: [{
          id: 999, title: "Late legacy", address: null, current_price: null,
          start_price: null, region_code: null, status: "active", is_archived: false,
          review_status: null, lat: 55.7, lon: 37.6,
        }],
        returned: 1, limit: 250, truncated: false, total: 1, mapped_total: 1,
        without_coordinates: 0, updated_at: "2026-09-06T00:00:00", statistics_exact: true,
        timings: { server_ms: 1 },
      },
      networkMs: 1,
      fromCache: false,
    }));
    const lateLegacy = postMessage.mock.calls
      .map(([message]) => message as { type?: string; lots?: Array<{ id: number }> })
      .filter((message) => message.type === "replace-lots" && message.lots?.some((lot) => lot.id === 999));
    expect(lateLegacy).toEqual([]);
    expect(screen.getByText(/На карте:/)).toHaveTextContent("объектов слоя");
  });

  it("preserves a review override through tile, legacy filter, and tile return", async () => {
    vi.mocked(fetchMapTile).mockResolvedValue({
      features: [{ kind: "lot", id: 71, lat: 55.71, lon: 37.61, review_status: null }],
    });
    vi.mocked(fetchMapLotDetail).mockResolvedValue(mapLot(71));
    vi.mocked(setReviewStatus).mockResolvedValue({ lot_id: 71, status: "approved" });
    render(<MapView refreshToken={0} />);
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => sendMapMessage(frame, "bankrotai-ready", { instanceId: "review-filter" }));
    act(() => sendViewport(frame, [37.4, 55.6, 37.41, 55.61], 10));
    await waitFor(() => expect(fetchMapTile).toHaveBeenCalledTimes(1));
    act(() => sendMapMessage(frame, "bankrotai-select", { lotId: 71 }));
    await screen.findByText("Лот 71");
    await act(async () => screen.getByRole("button", { name: "Интересен" }).click());
    await act(async () => screen.getByTitle("Закрыть").click());

    const minPrice = screen.getByText("Стартовая цена от").parentElement?.querySelector("input");
    if (!minPrice) throw new Error("Minimum price input not found");
    fireEvent.change(minPrice, { target: { value: "1000000" } });
    await act(async () => screen.getByRole("button", { name: "Применить" }).click());
    await waitFor(() => {
      const legacyCommand = postMessage.mock.calls
        .map(([message]) => message as { type?: string; lots?: Array<{ id: number; review_status?: string | null }> })
        .find((message) => message.type === "replace-lots" && message.lots?.some((lot) => lot.id === 71));
      expect(legacyCommand?.lots?.find((lot) => lot.id === 71)?.review_status).toBe("approved");
    });

    await act(async () => screen.getByRole("button", { name: "Сбросить" }).click());
    await waitFor(() => {
      const tileCommands = postMessage.mock.calls
        .map(([message]) => message as { type?: string; entries?: Array<{ features: Array<{ id: number; review_status?: string | null }> }> })
        .filter((message) => message.type === "sync-tiles" && message.entries?.length);
      const latest = tileCommands[tileCommands.length - 1];
      const lot = latest?.entries?.flatMap((entry) => entry.features).find((feature) => feature.id === 71);
      expect(lot?.review_status).toBe("approved");
    });
    expect(fetchMapTile).toHaveBeenCalledTimes(1);
  });
});

describe("tile request cache", () => {
  const tile = { z: 12, x: 2473, y: 1280 };
  let completed: Map<string, MapTilePayload>;
  let inflight: Map<string, Promise<MapTilePayload>>;

  beforeEach(() => {
    vi.clearAllMocks();
    completed = new Map();
    inflight = new Map();
  });

  it("uses a successfully completed tile without another HTTP request", async () => {
    vi.mocked(fetchMapTile).mockResolvedValue({ features: [] });
    await fetchCachedMapTile(completed, inflight, "v1", tile);
    await fetchCachedMapTile(completed, inflight, "v1", tile);
    expect(fetchMapTile).toHaveBeenCalledTimes(1);
  });

  it("deduplicates concurrent requests for the same tile", async () => {
    const pending = deferredTile();
    vi.mocked(fetchMapTile).mockReturnValue(pending.promise);
    const first = fetchCachedMapTile(completed, inflight, "v1", tile);
    const second = fetchCachedMapTile(completed, inflight, "v1", tile);
    expect(fetchMapTile).toHaveBeenCalledTimes(1);
    pending.resolve({ features: [] });
    await Promise.all([first, second]);
  });

  it("separates identical coordinates by dataset version", async () => {
    vi.mocked(fetchMapTile).mockResolvedValue({ features: [] });
    await fetchCachedMapTile(completed, inflight, "v1", tile);
    await fetchCachedMapTile(completed, inflight, "v2", tile);
    expect(fetchMapTile).toHaveBeenCalledTimes(2);
  });

  it("does not cache a failed tile request", async () => {
    vi.mocked(fetchMapTile)
      .mockRejectedValueOnce(new Error("503"))
      .mockResolvedValueOnce({ features: [] });
    await expect(fetchCachedMapTile(completed, inflight, "v1", tile)).rejects.toThrow("503");
    await fetchCachedMapTile(completed, inflight, "v1", tile);
    expect(fetchMapTile).toHaveBeenCalledTimes(2);
    expect(completed.size).toBe(1);
    expect(inflight.size).toBe(0);
  });

  it("evicts the least recently used completed tile and keeps a recent hit", async () => {
    vi.mocked(fetchMapTile).mockResolvedValue({ features: [] });
    const first = { z: 12, x: 1, y: 1 };
    const second = { z: 12, x: 2, y: 2 };
    const third = { z: 12, x: 3, y: 3 };
    await fetchCachedMapTile(completed, inflight, "v1", first, 2);
    await fetchCachedMapTile(completed, inflight, "v1", second, 2);
    await fetchCachedMapTile(completed, inflight, "v1", first, 2);
    await fetchCachedMapTile(completed, inflight, "v1", third, 2);

    expect([...completed.keys()]).toEqual(["v1:12:1:1", "v1:12:3:3"]);
    await fetchCachedMapTile(completed, inflight, "v1", second, 2);
    expect(fetchMapTile).toHaveBeenCalledTimes(4);
  });

  it("never evicts or duplicates an in-flight tile", async () => {
    const pending = deferredTile();
    vi.mocked(fetchMapTile).mockReturnValue(pending.promise);
    const first = fetchCachedMapTile(completed, inflight, "v1", tile, 1);
    const second = fetchCachedMapTile(completed, inflight, "v1", tile, 1);
    expect(inflight.size).toBe(1);
    expect(fetchMapTile).toHaveBeenCalledTimes(1);
    pending.resolve({ features: [] });
    await Promise.all([first, second]);
    expect(inflight.size).toBe(0);
    expect(completed.size).toBe(1);
  });

  it("reapplies current review state after a tile was evicted and fetched again", async () => {
    vi.mocked(fetchMapTile)
      .mockResolvedValueOnce({ features: [{ kind: "lot", id: 7, lat: 55.7, lon: 37.6, review_status: null }] })
      .mockResolvedValueOnce({ features: [] })
      .mockResolvedValueOnce({ features: [{ kind: "lot", id: 7, lat: 55.7, lon: 37.6, review_status: null }] });
    const reviewedTile = { z: 12, x: 7, y: 7 };
    const otherTile = { z: 12, x: 8, y: 8 };
    await fetchCachedMapTile(completed, inflight, "v1", reviewedTile, 1);
    await fetchCachedMapTile(completed, inflight, "v1", otherTile, 1);
    const reloaded = await fetchCachedMapTile(completed, inflight, "v1", reviewedTile, 1);
    const features = applyMapTileReviewOverrides(reloaded.features, new Map([[7, "approved"]]));
    expect(features[0].review_status).toBe("approved");
    expect(fetchMapTile).toHaveBeenCalledTimes(3);
  });
});
