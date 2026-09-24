import { expect, test } from "@playwright/test";

test.skip(
  process.env.E2E_PRODUCTION_AUDIT !== "1",
  "The production map gate runs only in the dedicated reliability job.",
);

test("authenticated map survives five wide viewport movements", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  const username = process.env.E2E_USERNAME || "reader";
  const password = process.env.E2E_PASSWORD;
  if (!password) throw new Error("E2E_PASSWORD is required for the production map gate");

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Логин").fill(username);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: new RegExp(`Выйти: ${username}`) }))
    .toBeVisible({ timeout: 40_000 });

  await page.getByRole("button", { name: "Карта", exact: true }).click();
  await expect(page.locator('iframe[title="Яндекс.Карта лотов"]')).toBeVisible({ timeout: 30_000 });

  const samples = [
    [20, 45, 60, 70],
    [21, 45, 61, 70],
    [22, 45, 62, 70],
    [23, 45, 63, 70],
    [24, 45, 64, 70],
  ];
  const timings: Array<{ bounds: number[]; status: number; durationMs: number; returned: number }> = [];
  for (const [west, south, east, north] of samples) {
    const startedAt = Date.now();
    let response = await page.context().request.get(
      `/api/map/lots?limit=250&west=${west}&south=${south}&east=${east}&north=${north}`,
      { headers: { "Cache-Control": "no-cache", "X-Production-Retry-Probe": "1" }, timeout: 30_000 },
    );
    for (let attempt = 1; attempt < 3 && [502, 503, 504].includes(response.status()); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      response = await page.context().request.get(
        `/api/map/lots?limit=250&west=${west}&south=${south}&east=${east}&north=${north}`,
        { headers: { "Cache-Control": "no-cache", "X-Production-Retry-Probe": "1" }, timeout: 30_000 },
      );
    }
    const durationMs = Date.now() - startedAt;
    expect(response.status(), `wide viewport ${west},${south},${east},${north}`).toBe(200);
    const payload = await response.json() as { items: unknown[]; limit: number };
    expect(payload.limit).toBe(250);
    expect(Array.isArray(payload.items)).toBe(true);
    expect(payload.items.length).toBeLessThanOrEqual(250);
    timings.push({
      bounds: [west, south, east, north],
      status: response.status(),
      durationMs,
      returned: payload.items.length,
    });
  }

  await expect(page.getByText("Сервис временно недоступен", { exact: false })).toHaveCount(0);
  await testInfo.attach("wide-viewport-timings.json", {
    body: Buffer.from(JSON.stringify(timings, null, 2)),
    contentType: "application/json",
  });
});


test("direct prepared tiles are published and readable from REG.RU S3", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  const username = process.env.E2E_USERNAME || "reader";
  const password = process.env.E2E_PASSWORD;
  if (!password) throw new Error("E2E_PASSWORD is required for the production map gate");

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Логин").fill(username);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("button", { name: new RegExp(`Выйти: ${username}`) }))
    .toBeVisible({ timeout: 40_000 });

  const datasetResponse = await page.context().request.get("/api/map/datasets/current", {
    timeout: 30_000,
  });
  expect(datasetResponse.status()).toBe(200);
  const dataset = await datasetResponse.json() as {
    version: string;
    point_count: number;
    tile_count: number;
    bootstrap_tiles?: Array<{
      z: number;
      x: number;
      y: number;
      payload?: { type?: string; features?: unknown[] };
    }>;
    tile_source?: string;
    tile_base_url?: string | null;
    object_store_layout?: string;
    bundle_root_url?: string | null;
    bundle_index_base_url?: string | null;
    priority_regions?: string[];
  };
  expect(dataset.version).toMatch(/-s3$/);
  expect(dataset.point_count).toBeGreaterThan(0);
  expect(dataset.tile_count).toBeGreaterThan(0);
  expect(dataset.bootstrap_tiles?.length).toBe(9);
  expect(dataset.tile_source).toBe("regru-s3");

  const bundled = dataset.object_store_layout === "regional-bundles-v1";
  let directObjectUrl = "";
  let firstPayload: {
    type?: string;
    features?: Array<{ properties?: Record<string, unknown> }>;
  } | null = null;
  let directHeaders: Record<string, string> = {};

  if (bundled) {
    expect(dataset.bundle_root_url).toBe("https://s3.regru.cloud/sterdez-map");
    expect(dataset.bundle_index_base_url)
      .toMatch(/^https:\/\/s3\.regru\.cloud\/sterdez-map\/datasets\/.*\/indexes$/);
    expect(dataset.priority_regions).toContain("76");

    for (const tile of dataset.bootstrap_tiles || []) {
      const indexUrl = `${String(dataset.bundle_index_base_url).replace(/\/$/, "")}/${tile.z}.json`;
      const indexResponse = await page.context().request.get(indexUrl, {
        headers: { Origin: "https://sterdez.online", "Cache-Control": "no-cache" },
        timeout: 30_000,
      });
      if (indexResponse.status() !== 200) continue;
      const indexPayload = await indexResponse.json() as {
        tiles?: Record<string, { bundle?: string; region?: string }>;
      };
      const entry = indexPayload.tiles?.[`${tile.x}/${tile.y}`];
      if (!entry?.bundle) continue;
      const bundleUrl = new URL(
        entry.bundle,
        `${String(dataset.bundle_root_url).replace(/\/$/, "")}/`,
      ).toString();
      const bundleResponse = await page.context().request.get(bundleUrl, {
        headers: { Origin: "https://sterdez.online", "Cache-Control": "no-cache" },
        timeout: 30_000,
      });
      if (bundleResponse.status() !== 200) continue;
      const bundlePayload = await bundleResponse.json() as {
        tiles?: Record<string, {
          type?: string;
          features?: Array<{ properties?: Record<string, unknown> }>;
        }>;
      };
      const logicalTile = bundlePayload.tiles?.[`${tile.z}/${tile.x}/${tile.y}`];
      if (!logicalTile) continue;
      directObjectUrl = bundleUrl;
      directHeaders = await bundleResponse.allHeaders();
      firstPayload = logicalTile;
      break;
    }
  } else {
    expect(dataset.tile_base_url).toMatch(/^https:\/\/s3\.regru\.cloud\/sterdez-map\/datasets\//);
    const tileBaseUrl = String(dataset.tile_base_url).replace(/\/$/, "");
    for (const tile of dataset.bootstrap_tiles || []) {
      const candidateUrl = `${tileBaseUrl}/${tile.z}/${tile.x}/${tile.y}.json`;
      const response = await page.context().request.get(candidateUrl, {
        headers: { Origin: "https://sterdez.online", "Cache-Control": "no-cache" },
        timeout: 30_000,
      });
      if (response.status() === 200) {
        directObjectUrl = candidateUrl;
        directHeaders = await response.allHeaders();
        firstPayload = await response.json() as {
          type?: string;
          features?: Array<{ properties?: Record<string, unknown> }>;
        };
        break;
      }
    }
  }

  expect(firstPayload, "at least one bootstrap tile must be readable from REG.RU S3").not.toBeNull();
  expect(directHeaders["cache-control"]).toContain("public");
  expect(directHeaders["cache-control"]).toContain("immutable");
  expect(["*", "https://sterdez.online"]).toContain(directHeaders["access-control-allow-origin"]);
  expect(firstPayload!.type).toBe("FeatureCollection");
  expect(Array.isArray(firstPayload!.features)).toBe(true);
  for (const feature of firstPayload!.features || []) {
    expect(feature.properties).not.toHaveProperty("review_status");
  }

  const started = Date.now();
  const directRead = await page.context().request.get(directObjectUrl, {
    headers: { Origin: "https://sterdez.online" },
    timeout: 30_000,
  });
  const directReadMs = Date.now() - started;
  expect(directRead.status()).toBe(200);

  await page.getByRole("button", { name: "Карта", exact: true }).click();
  const frameElement = page.locator('iframe[title="Яндекс.Карта лотов"]');
  await expect(frameElement).toBeVisible({ timeout: 30_000 });
  const frame = page.frameLocator('iframe[title="Яндекс.Карта лотов"]');
  const yandexReady = await frame.locator("#hint").isHidden({ timeout: 15_000 }).catch(() => false);
  const hintText = yandexReady ? "" : await frame.locator("#hint").textContent().catch(() => null);

  await expect(page.getByText("Сервис временно недоступен", { exact: false })).toHaveCount(0);
  await testInfo.attach("direct-map-regru-s3-evidence.json", {
    body: Buffer.from(JSON.stringify({
      dataset: dataset.version,
      pointCount: dataset.point_count,
      tileCount: dataset.tile_count,
      tileSource: dataset.tile_source,
      objectStoreLayout: dataset.object_store_layout || "tiles",
      tileBaseUrl: dataset.tile_base_url || null,
      bundleRootUrl: dataset.bundle_root_url || null,
      directObjectUrl,
      tileHost: new URL(directObjectUrl).host,
      directReadMs,
      yandexReady,
      yandexHint: hintText,
    }, null, 2)),
    contentType: "application/json",
  });
});

