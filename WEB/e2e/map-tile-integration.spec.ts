import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";

test("versioned tile map integration, cache, detail, review and legacy filter", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  const tilePaths: string[] = [];
  let servedPointTile = false;
  let bulkCalls = 0;
  const tileBody = JSON.stringify({ features: [{
    kind: "lot", id: 7001, lat: 57.6261, lon: 39.8845, title: "Тайловый лот",
    current_price: 693109, status: "active", review_status: null,
  }] });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, headers: Record<string, string> = {}) => route.fulfill({
      status: 200, contentType: "application/json", headers, body: JSON.stringify(body),
    });
    if (path === "/api/auth/me") return json({ id: 1, username: "reader", role: "reader" }, { Date: new Date().toUTCString() });
    if (path === "/api/regions") return json([{ code: "76", name: "Ярославская область" }]);
    if (path === "/api/map/datasets/current") return json({
      version: "e2e-v1", point_count: 1, tile_count: 15, max_zoom: 14,
      point_zoom: 12, published_at: new Date().toISOString(),
    });
    if (path.startsWith("/api/map/tiles/e2e-v1/")) {
      tilePaths.push(path);
      const containsPoint = !servedPointTile && path.includes("/12/");
      if (containsPoint) servedPointTile = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: containsPoint ? tileBody : JSON.stringify({ features: [] }),
      });
    }
    if (path === "/api/map/lots/7001" && request.method() === "GET") return json({
      id: 7001, external_id: "e2e-7001", title: "Тайловый лот", description: "Полная карточка",
      address: "Ярославль", cadastral_number: null, category: "land", region: "Ярославская область",
      status: "active", is_archived: false, review_status: null, current_price: 693109,
      lat: 57.6261, lon: 39.8845, geometry: null, confidence: "high", source: "test",
      source_name: "Test", source_url: null, gis_torgi_url: null, etp_url: null,
      torgi_russia_url: null, image_url: null, image_urls: [], procedure_number: null,
      application_deadline: null, auction_at: null, sources: [],
    });
    if (path === "/api/lots/7001/review-status" && request.method() === "PUT") {
      return json({ lot_id: 7001, status: "approved" });
    }
    if (path === "/api/map/lots") {
      bulkCalls += 1;
      return json({
        items: [{ id: 7001, title: "Тайловый лот", address: "Ярославль", current_price: 693109,
          start_price: 700000, region_code: "76", status: "active", is_archived: false,
          review_status: null, lat: 57.6261, lon: 39.8845 }],
        returned: 1, limit: 250, truncated: false, total: 1, mapped_total: 1,
        without_coordinates: 0, updated_at: new Date().toISOString(), statistics_exact: true,
        timings: { server_ms: 1 },
      });
    }
    if (path === "/api/lots") return json({ items: [], total: 0 });
    if (path === "/api/stats") return json({ total_lots: 0, active_lots: 0, appraised_lots: 0, average_discount: null, region: "all" });
    return json({});
  });

  const started = performance.now();
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".appShell")).toBeVisible();
  await page.getByRole("button", { name: "Карта", exact: true }).click();
  const frameElement = page.locator('iframe[title="Яндекс.Карта лотов"]');
  await expect(frameElement).toBeVisible();
  const currentMapFrame = () => page.frames().find(
    (candidate) => candidate !== page.mainFrame() && candidate.url() === "about:srcdoc",
  );
  expect(currentMapFrame()).toBeTruthy();
  await expect(page.frameLocator('iframe[title="Яндекс.Карта лотов"]').locator("#hint")).toBeHidden({ timeout: 30_000 });
  await expect.poll(() => currentMapFrame()!.evaluate(() => Boolean(
    (window as unknown as { bankrotaiDebug?: unknown }).bankrotaiDebug,
  )), { timeout: 30_000 }).toBe(true);
  const setViewport = (center: number[], zoom: number) => currentMapFrame()!.evaluate(({ center, zoom }) =>
    (window as unknown as { bankrotaiDebug: { setViewport: (value: number[], level: number) => void } })
      .bankrotaiDebug.setViewport(center, zoom), { center, zoom });

  const beforeZoom12 = tilePaths.length;
  await setViewport([57.6261, 39.8845], 12);
  await expect.poll(() => tilePaths.length).toBeGreaterThan(beforeZoom12);
  // Yandex emits more than one boundschange while settling a programmatic
  // viewport. Capture the baseline only after its debounced events finish.
  await page.waitForTimeout(500);
  await expect(page.getByLabel("Состояние карты")).toContainText("1 на карте");
  const firstRenderMs = Math.round((performance.now() - started) * 10) / 10;
  expect(bulkCalls).toBe(0);
  const zoom12Requests = tilePaths.length;
  await setViewport([57.6261, 39.8845], 14);
  await expect.poll(() => tilePaths.length).toBeGreaterThan(zoom12Requests);
  await page.waitForTimeout(500);
  const zoom14Requests = tilePaths.length;
  await setViewport([57.6261, 39.8845], 12);
  await page.waitForTimeout(400);
  expect(tilePaths.length).toBe(zoom14Requests);
  const zoomBackRequests = tilePaths.length - zoom14Requests;
  const initialRequests = tilePaths.length;
  await setViewport([43.15, 131.9], 12);
  await expect.poll(() => tilePaths.length).toBeGreaterThan(initialRequests);
  const afterPan = tilePaths.length;
  await setViewport([57.6261, 39.8845], 12);
  await page.waitForTimeout(400);
  expect(tilePaths.length).toBe(afterPan);
  const panBackRequests = tilePaths.length - afterPan;

  await currentMapFrame()!.evaluate(() => parent.postMessage({
    type: "bankrotai-select", channel: "bankrotai-map-v1", lotId: 7001,
  }, "*"));
  await expect(page.getByLabel("Карточка выбранного лота")).toContainText("Полная карточка");
  await page.getByRole("button", { name: "Интересен", exact: true }).click();
  await expect(page.getByRole("button", { name: "Интересен", exact: true })).toHaveClass(/active/);
  await page.getByTitle("Закрыть").click();

  await page.getByText("Стартовая цена от").locator("..").locator("input").fill("100000");
  await page.getByRole("button", { name: "Применить" }).click();
  await expect.poll(() => bulkCalls).toBe(1);
  await page.getByRole("button", { name: "Сбросить" }).click();
  await expect(page.getByLabel("Состояние карты")).toContainText("Система готова");
  expect(tilePaths.length).toBe(afterPan);
  expect(new Set(tilePaths).size).toBe(tilePaths.length);
  expect(errors).toEqual([]);

  await page.screenshot({ path: testInfo.outputPath("map-tile-integration.png"), fullPage: true });
  const performanceSummary = {
      firstRenderMs, tilePayloadBytes: Buffer.byteLength(tileBody), tileRequests: tilePaths.length,
      uniqueTileRequests: new Set(tilePaths).size, initialBulkMapLotsCalls: 0,
      dynamicFilterBulkMapLotsCalls: bulkCalls,
      ordinaryStartupTileRequests: beforeZoom12,
      controlledViewportRequests: zoom12Requests - beforeZoom12,
      zoomRequests: zoom14Requests - zoom12Requests,
      zoomBackRequests,
      panRequests: afterPan - initialRequests,
      panBackRequests,
  };
  const performancePath = testInfo.outputPath("map-performance.json");
  await writeFile(performancePath, JSON.stringify(performanceSummary, null, 2), "utf8");
  await testInfo.attach("map-performance.json", {
    path: performancePath,
    contentType: "application/json",
  });
});
