import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const mapDatasetVersionSource = readFileSync(
  new URL("../../src/bankrotai/services/map_dataset_version.py", import.meta.url),
  "utf8",
);
const MAP_DATASET_REVISION = mapDatasetVersionSource.match(
  /^MAP_DATASET_REVISION\s*=\s*"([^"]+)"$/m,
)?.[1];

if (!MAP_DATASET_REVISION) {
  throw new Error("MAP_DATASET_REVISION is missing from map_dataset_version.py");
}

test.skip(
  process.env.E2E_PRODUCTION_AUDIT !== "1",
  "The production map gate runs only in the dedicated reliability job.",
);

test("direct map serves five spatial shards without the bulk viewport API", async ({ page }, testInfo) => {
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

  const bulkRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/map/lots") bulkRequests.push(request.url());
  });

  const datasetResponse = await page.context().request.get("/api/map/datasets/current", {
    timeout: 30_000,
  });
  expect(datasetResponse.status()).toBe(200);
  const dataset = await datasetResponse.json() as {
    version: string;
    object_store_layout?: string;
    bundle_root_url?: string | null;
    bundle_manifest_url?: string | null;
  };
  expect(dataset.object_store_layout).toBe("regional-bundles-v1");
  expect(dataset.version).toMatch(new RegExp(`-${MAP_DATASET_REVISION}-bundle-s3$`));
  expect(dataset.bundle_root_url).toBe("https://s3.regru.cloud/sterdez-map");
  expect(dataset.bundle_manifest_url).toBeTruthy();

  const manifestResponse = await page.context().request.get(String(dataset.bundle_manifest_url), {
    headers: { Origin: "https://sterdez.online", "Cache-Control": "no-cache" },
    timeout: 30_000,
  });
  expect(manifestResponse.status()).toBe(200);
  const manifest = await manifestResponse.json() as {
    pipeline_revision?: string;
    index_shards?: Record<string, string>;
  };
  expect(manifest.pipeline_revision).toBe(MAP_DATASET_REVISION);
  const sampleShards = Object.entries(manifest.index_shards || {})
    .filter(([name]) => name.startsWith("detail/8/"))
    .slice(0, 5);
  expect(sampleShards.length).toBe(5);

  const timings: Array<{
    shard: string;
    indexMs: number;
    bundleMs: number;
    indexStatus: number;
    bundleStatus: number;
  }> = [];

  for (const [shard, indexKey] of sampleShards) {
    const indexUrl = new URL(
      indexKey,
      `${String(dataset.bundle_root_url).replace(/\/$/, "")}/`,
    ).toString();
    const indexStarted = Date.now();
    const indexResponse = await page.context().request.get(indexUrl, {
      headers: { Origin: "https://sterdez.online" },
      timeout: 30_000,
    });
    const indexMs = Date.now() - indexStarted;
    expect(indexResponse.status(), `index shard ${shard}`).toBe(200);
    const indexPayload = await indexResponse.json() as {
      tiles?: Record<string, { bundle?: string }>;
    };
    const bundleKey = Object.values(indexPayload.tiles || {})
      .map((entry) => entry.bundle)
      .find((value): value is string => Boolean(value));
    expect(bundleKey, `bundle for shard ${shard}`).toBeTruthy();

    const bundleUrl = new URL(
      bundleKey!,
      `${String(dataset.bundle_root_url).replace(/\/$/, "")}/`,
    ).toString();
    const bundleStarted = Date.now();
    const bundleResponse = await page.context().request.get(bundleUrl, {
      headers: { Origin: "https://sterdez.online" },
      timeout: 30_000,
    });
    const bundleMs = Date.now() - bundleStarted;
    expect(bundleResponse.status(), `bundle for shard ${shard}`).toBe(200);
    const payload = await bundleResponse.json() as { tiles?: Record<string, unknown> };
    expect(Object.keys(payload.tiles || {}).length).toBeGreaterThan(0);

    timings.push({
      shard,
      indexMs,
      bundleMs,
      indexStatus: indexResponse.status(),
      bundleStatus: bundleResponse.status(),
    });
  }

  await page.getByRole("button", { name: "Карта", exact: true }).click();
  await expect(page.locator('iframe[title="Яндекс.Карта лотов"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Сервис временно недоступен", { exact: false })).toHaveCount(0);
  expect(bulkRequests).toEqual([]);

  await testInfo.attach("direct-spatial-shard-timings.json", {
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
    bundle_manifest_url?: string | null;
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
    expect(dataset.version).toMatch(new RegExp(`-${MAP_DATASET_REVISION}-bundle-s3$`));
    expect(dataset.bundle_root_url).toBe("https://s3.regru.cloud/sterdez-map");
    expect(dataset.bundle_manifest_url)
      .toMatch(/^https:\/\/s3\.regru\.cloud\/sterdez-map\/datasets\/.*\/manifest\.json$/);
    expect(dataset.priority_regions).toContain("76");

    const manifestResponse = await page.context().request.get(String(dataset.bundle_manifest_url), {
      headers: { Origin: "https://sterdez.online", "Cache-Control": "no-cache" },
      timeout: 30_000,
    });
    expect(manifestResponse.status()).toBe(200);
    const manifestHeaders = manifestResponse.headers();
    expect(["*", "https://sterdez.online"]).toContain(manifestHeaders["access-control-allow-origin"]);
    const manifest = await manifestResponse.json() as {
      version?: string;
      pipeline_revision?: string;
      layout?: string;
      detail_parent_zoom?: number;
      overview_parent_zoom?: number;
      index_shards?: Record<string, string>;
    };
    expect(manifest.version).toBe(dataset.version);
    expect(manifest.pipeline_revision).toBe(MAP_DATASET_REVISION);
    expect(manifest.layout).toBe("regional-bundles-v1");
    expect(manifest.detail_parent_zoom).toBe(8);
    expect(manifest.overview_parent_zoom).toBe(6);

    for (const tile of dataset.bootstrap_tiles || []) {
      const overviewZoom = Number(manifest.overview_parent_zoom);
      const detailZoom = Number(manifest.detail_parent_zoom);
      const shard = tile.z <= overviewZoom
        ? "overview/root"
        : tile.z < 12
          ? `overview/${overviewZoom}/${tile.x >> (tile.z - overviewZoom)}/${tile.y >> (tile.z - overviewZoom)}`
          : `detail/${detailZoom}/${tile.x >> (tile.z - detailZoom)}/${tile.y >> (tile.z - detailZoom)}`;
      const indexKey = manifest.index_shards?.[shard];
      if (!indexKey) continue;
      const indexUrl = new URL(
        indexKey,
        `${String(dataset.bundle_root_url).replace(/\/$/, "")}/`,
      ).toString();
      const indexResponse = await page.context().request.get(indexUrl, {
        headers: { Origin: "https://sterdez.online", "Cache-Control": "no-cache" },
        timeout: 30_000,
      });
      if (indexResponse.status() !== 200) continue;
      const indexPayload = await indexResponse.json() as {
        shard?: string;
        tiles?: Record<string, { bundle?: string; region?: string }>;
      };
      expect(indexPayload.shard).toBe(shard);
      const entry = indexPayload.tiles?.[`${tile.z}/${tile.x}/${tile.y}`];
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
      directHeaders = bundleResponse.headers();
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
        directHeaders = response.headers();
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
      bundleManifestUrl: dataset.bundle_manifest_url || null,
      directObjectUrl,
      tileHost: new URL(directObjectUrl).host,
      directReadMs,
      yandexReady,
      yandexHint: hintText,
    }, null, 2)),
    contentType: "application/json",
  });
});



test("filtered map stays responsive for four concurrent requests", async ({ page }, testInfo) => {
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
    bootstrap_tiles?: Array<{ z: number; x: number; y: number }>;
  };
  const tile = (dataset.bootstrap_tiles || [])[4] || (dataset.bootstrap_tiles || [])[0];
  expect(tile).toBeTruthy();

  const path =
    `/api/map/filtered-tiles/${encodeURIComponent(dataset.version)}/${tile.z}/${tile.x}/${tile.y}`;
  const query = "?region_code=76&min_start_price=1";

  const warmStarted = Date.now();
  const warm = await page.context().request.get(`${path}${query}`, { timeout: 30_000 });
  const warmMs = Date.now() - warmStarted;
  expect(warm.status()).toBe(200);

  const parallel = await Promise.all(Array.from({ length: 4 }, async (_, index) => {
    const started = Date.now();
    const response = await page.context().request.get(`${path}${query}`, { timeout: 30_000 });
    const durationMs = Date.now() - started;
    const body = await response.json() as { type?: string };
    return {
      index,
      status: response.status(),
      durationMs,
      cache: response.headers()["x-map-filter-cache"] || "",
      serverTiming: response.headers()["server-timing"] || "",
      type: body.type,
    };
  }));

  for (const result of parallel) {
    expect(result.status).toBe(200);
    expect(result.type).toBe("FeatureCollection");
    expect(result.cache).toBe("HIT");
    expect(result.durationMs).toBeLessThan(5_000);
  }

  await testInfo.attach("filtered-four-request-timings.json", {
    body: Buffer.from(JSON.stringify({
      dataset: dataset.version,
      tile,
      warm: {
        durationMs: warmMs,
        cache: warm.headers()["x-map-filter-cache"] || "",
        serverTiming: warm.headers()["server-timing"] || "",
      },
      parallel,
    }, null, 2)),
    contentType: "application/json",
  });
});
