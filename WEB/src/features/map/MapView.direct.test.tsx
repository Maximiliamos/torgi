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
    fetchYandexMapTile: vi.fn(),
    fetchFilteredYandexMapTile: vi.fn(),
    fetchOperationsProgress: vi.fn(),
    fetchRegions: vi.fn(),
    setReviewStatus: vi.fn(),
  };
});

import {
  fetchCurrentMapDataset,
  fetchCurrentUser,
  fetchMapLotsSWR,
  fetchMapTile,
  fetchYandexMapTile,
  fetchFilteredYandexMapTile,
  fetchOperationsProgress,
  fetchRegions,
  type MapDataset,
  type YandexMapTilePayload,
} from "../../lib/api";

function channelFrom(frame: HTMLIFrameElement) {
  const match = frame.srcdoc.match(/const channel=("[^"]+")/);
  if (!match) throw new Error("Map iframe channel was not found");
  return JSON.parse(match[1]) as string;
}

function send(frame: HTMLIFrameElement, type: string, payload: Record<string, unknown> = {}) {
  window.dispatchEvent(new MessageEvent("message", {
    source: frame.contentWindow,
    data: { type, channel: channelFrom(frame), ...payload },
  }));
}

const emptyPayload: YandexMapTilePayload = {
  type: "FeatureCollection",
  features: [],
};

const dataset: MapDataset = {
  version: "direct-v1",
  point_count: 123,
  tile_count: 456,
  max_zoom: 14,
  point_zoom: 12,
  published_at: "2026-09-24T16:00:00",
  bootstrap_zoom: 7,
  bootstrap_center: [57.6261, 39.8845],
  bootstrap_tiles: [{
    z: 7,
    x: 78,
    y: 39,
    etag: "bootstrap",
    payload: emptyPayload,
  }],
};

describe("direct Yandex tile transport", () => {
  beforeEach(async () => {
    vi.stubEnv("VITE_DIRECT_MAP_TILES", "true");
    vi.clearAllMocks();
    vi.mocked(fetchCurrentMapDataset).mockResolvedValue(dataset);
    vi.mocked(fetchCurrentUser).mockResolvedValue({
      id: 1,
      username: "reader",
      role: "reader",
    });
    vi.mocked(fetchRegions).mockResolvedValue([{ code: "76", name: "Ярославская область" }]);
    vi.mocked(fetchOperationsProgress).mockResolvedValue({
      sync: null,
      geocoding: {
        total: 100,
        geocoded: 100,
        remaining: 0,
        terminal_failures: 0,
        percent: 100,
        task: null,
      },
    });
    vi.mocked(fetchYandexMapTile).mockResolvedValue({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: 77,
        geometry: { type: "Point", coordinates: [55.7, 37.6] },
        properties: {
          kind: "lot",
          lotId: 77,
          title: "Direct lot",
          current_price: 1_000_000,
          review_status: null,
        },
        options: { preset: "islands#grayDotIcon" },
      }],
    });
    vi.mocked(fetchFilteredYandexMapTile).mockResolvedValue({
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        id: 88,
        geometry: { type: "Point", coordinates: [57.62, 39.88] },
        properties: {
          kind: "lot",
          lotId: 88,
          title: "Filtered lot",
          start_price: 2_000_000,
          region_code: "76",
        },
        options: { preset: "islands#grayDotIcon" },
      }],
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("keeps credentials in the parent and sends ready FeatureCollections to the sandbox", async () => {
    const { MapView } = await import("./MapView");
    render(<MapView refreshToken={0} />);

    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.srcdoc).toContain("bankrotai-request-tiles");
    expect(frame.srcdoc).toContain("tileManager.add(payload)");
    expect(frame.srcdoc).not.toContain("allow-same-origin");

    await waitFor(() => expect(fetchCurrentMapDataset).toHaveBeenCalled());
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");

    act(() => send(frame, "bankrotai-ready"));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "set-direct-dataset",
        enabled: true,
        dataset: expect.objectContaining({ version: "direct-v1" }),
      }),
      "*",
    ));

    act(() => send(frame, "bankrotai-request-tiles", {
      version: "direct-v1",
      generation: 4,
      visible: [{ z: 12, x: 2475, y: 1280 }],
      prefetch: [],
    }));

    await waitFor(() => expect(fetchYandexMapTile).toHaveBeenCalledWith(
      "direct-v1",
      12,
      2475,
      1280,
    ));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "install-direct-tile",
        generation: 4,
        key: "direct-v1/12/2475/1280",
        payload: expect.objectContaining({ type: "FeatureCollection" }),
      }),
      "*",
    ));

    expect(fetchMapTile).not.toHaveBeenCalled();
    expect(fetchMapLotsSWR).not.toHaveBeenCalled();
  });

  it("keeps region and price filters on runtime-filtered direct tiles", async () => {
    const { MapView } = await import("./MapView");
    render(<MapView refreshToken={0} />);

    await waitFor(() => expect(fetchRegions).toHaveBeenCalled());
    const frame = screen.getByTitle("Яндекс.Карта лотов") as HTMLIFrameElement;
    const postMessage = vi.spyOn(frame.contentWindow!, "postMessage");

    const regionLabel = screen.getByText("Субъект РФ").closest("label");
    const regionSelect = regionLabel?.querySelector("select");
    const minLabel = screen.getByText("Стартовая цена от").closest("label");
    const minInput = minLabel?.querySelector("input");
    expect(regionSelect).not.toBeNull();
    expect(minInput).not.toBeNull();

    fireEvent.change(regionSelect!, { target: { value: "76" } });
    fireEvent.change(minInput!, { target: { value: "1500000" } });
    fireEvent.click(screen.getByRole("button", { name: "Применить" }));

    act(() => send(frame, "bankrotai-ready"));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "set-direct-dataset",
        enabled: true,
        filterKey: "76|1500000|",
      }),
      "*",
    ));

    act(() => send(frame, "bankrotai-request-tiles", {
      version: "direct-v1",
      filterKey: "76|1500000|",
      generation: 9,
      visible: [{ z: 12, x: 2501, y: 1301 }],
      prefetch: [],
    }));

    await waitFor(() => expect(fetchFilteredYandexMapTile).toHaveBeenCalledWith(
      "direct-v1",
      12,
      2501,
      1301,
      {
        region_code: "76",
        min_start_price: 1_500_000,
        max_start_price: undefined,
      },
    ));
    expect(fetchMapLotsSWR).not.toHaveBeenCalled();
    expect(fetchMapTile).not.toHaveBeenCalled();
  });

});
