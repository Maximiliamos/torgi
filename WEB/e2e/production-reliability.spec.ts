import { expect, test, type APIRequestContext, type APIResponse, type Page, type TestInfo } from "@playwright/test";
import { writeFile } from "node:fs/promises";

test.skip(process.env.E2E_PRODUCTION_AUDIT !== "1",
  "Real production acceptance is only enabled in the dedicated post-deploy workflow.");

async function maskSessionCookies(request: APIRequestContext): Promise<void> {
  if (process.env.GITHUB_ACTIONS !== "true") return;
  for (const cookie of (await request.storageState()).cookies) {
    if (cookie.value) console.log("::add-mask::" + cookie.value);
  }
}

async function authenticatedGet(request: APIRequestContext, path: string): Promise<APIResponse> {
  try {
    return await request.get(path, { headers: { "Cache-Control": "no-store" }, timeout: 30_000 });
  } catch {
    // Playwright's raw error includes the Cookie header. Never serialize it.
    throw new Error("Authenticated production GET failed or timed out: " + path);
  }
}

async function logIn(page: Page, testInfo: TestInfo): Promise<void> {
  // Preserve pre-login evidence before a test-wide timeout prevents artifacts.
  // Paths only: query strings, headers, cookies and bodies may contain secrets.
  const navigation: { event: string; origin: string; path: string; status?: number; error?: string }[] = [];
  const relevant = (kind: string) => ["document", "script", "stylesheet"].includes(kind);
  const requestListener = (request: import("@playwright/test").Request) => {
    if (!relevant(request.resourceType())) return;
    const url = new URL(request.url());
    navigation.push({ event: "request", origin: url.origin, path: url.pathname });
  };
  const responseListener = (response: import("@playwright/test").Response) => {
    if (!relevant(response.request().resourceType())) return;
    const url = new URL(response.url());
    navigation.push({ event: "response", origin: url.origin, path: url.pathname, status: response.status() });
  };
  const failureListener = (request: import("@playwright/test").Request) => {
    if (!relevant(request.resourceType())) return;
    const url = new URL(request.url());
    navigation.push({ event: "failed", origin: url.origin, path: url.pathname, error: request.failure()?.errorText });
  };
  page.on("request", requestListener);
  page.on("response", responseListener);
  page.on("requestfailed", failureListener);
  try {
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 45_000 });
  } catch (error) {
    const evidencePath = testInfo.outputPath("pre-login-navigation.json");
    await writeFile(evidencePath, JSON.stringify(navigation, null, 2));
    console.log("Pre-login navigation: " + JSON.stringify(navigation));
    await testInfo.attach("pre-login-navigation.json", {
      path: evidencePath, contentType: "application/json",
    });
    await page.screenshot({ path: testInfo.outputPath("pre-login-screen.png"), timeout: 5_000 }).catch(() => undefined);
    throw error;
  } finally {
    page.off("request", requestListener);
    page.off("response", responseListener);
    page.off("requestfailed", failureListener);
  }
  const navigate = page.getByRole("button", { name: "Надёжность", exact: true });
  const login = page.getByRole("heading", { name: "Вход", exact: true });
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (await navigate.isVisible().catch(() => false)) {
      await maskSessionCookies(page.context().request);
      return;
    }
    if (await login.isVisible().catch(() => false)) break;
    await page.waitForTimeout(500);
  }
  if (!await login.isVisible()) throw new Error("Production login form did not appear");
  const password = process.env.E2E_PASSWORD;
  if (!password) throw new Error("E2E_PASSWORD is required for production acceptance");
  await page.getByLabel("Логин").fill(process.env.E2E_USERNAME || "reader");
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(navigate).toBeVisible({ timeout: 40_000 });
  await maskSessionCookies(page.context().request);
}

type ReliabilitySource = {
  canonical_id: string; configured: boolean; overall_status: string; current_error: string | null;
};
type ReliabilitySummary = {
  schema_version: number; generated_at: string;
  system: { status: string; generated_at: string; reasons: string[] };
  sources: {
    generated_at: string; configured_total: number; healthy: number;
    degraded: number; failed: number; paused_total: number; not_checked: number;
  };
  geo: { total: number; geocoded: number; percent: number };
  data_quality: { population: number; percentages: Record<string, number> };
};

function percentile95(samples: number[]): number {
  const sorted = samples.slice().sort((a, b) => a - b);
  return sorted[Math.ceil(sorted.length * 0.95) - 1];
}

test("Reliability data and layout: desktop, tablet and mobile", async ({ page }, testInfo) => {
  test.setTimeout(150_000);
  await logIn(page, testInfo);
  await page.getByRole("button", { name: "Надёжность", exact: true }).click();
  const panel = page.locator(".rel-dashboard");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await expect(panel.getByRole("heading", { name: /Источники данных/ })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "GEO · координаты" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Карта: доставка и целостность" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Экспорт отчёта" })).toBeEnabled();
  const summaryResponse = await authenticatedGet(page.context().request, "/api/reliability/summary");
  const sourcesResponse = await authenticatedGet(page.context().request, "/api/reliability/sources");
  expect(summaryResponse.status()).toBe(200);
  expect(sourcesResponse.status()).toBe(200);
  const summary = await summaryResponse.json() as ReliabilitySummary;
  const sources = await sourcesResponse.json() as { items: ReliabilitySource[] };
  expect(summary.schema_version).toBe(1);
  expect(["ok", "degraded", "incident"]).toContain(summary.system.status);
  expect(summary.generated_at).toBe(summary.system.generated_at);
  expect(summary.generated_at).toBe(summary.sources.generated_at);
  expect(summary.sources.configured_total).toBe(sources.items.filter(x => x.configured).length);
  const classified = summary.sources.healthy + summary.sources.degraded
    + summary.sources.failed + summary.sources.paused_total + summary.sources.not_checked;
  expect(classified).toBe(summary.sources.configured_total);
  expect(new Set(sources.items.map(x => x.canonical_id)).size).toBe(sources.items.length);
  expect(sources.items.filter(x => x.overall_status === "healthy" && x.current_error !== null)).toHaveLength(0);
  expect(summary.geo.geocoded).toBeLessThanOrEqual(summary.geo.total);
  expect(summary.geo.percent).toBeGreaterThanOrEqual(0);
  expect(summary.geo.percent).toBeLessThanOrEqual(100);
  expect(summary.data_quality.population).toBeGreaterThanOrEqual(0);
  expect(Object.values(summary.data_quality.percentages).every(x => x >= 0 && x <= 100)).toBe(true);

  for (const viewport of [
    { label: "desktop", width: 1600, height: 1000 },
    { label: "tablet", width: 1024, height: 768 },
    { label: "mobile", width: 390, height: 844 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await expect(panel).toBeVisible();
    await expect(panel.locator(".rel-hero")).toBeVisible();
    await expect(panel.locator("#rel-sources")).toBeAttached();
    await expect(panel.locator("#rel-quality")).toBeAttached();
    const layout = await page.evaluate(() => ({
      width: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      dashboardWidth: Math.round(document.querySelector(".rel-dashboard")!.getBoundingClientRect().width),
      navigationWidth: Math.round(document.querySelector(".appRail")!.getBoundingClientRect().width),
      geoTop: document.querySelector("#rel-geo")!.getBoundingClientRect().top,
      mapTop: document.querySelector("#rel-map")!.getBoundingClientRect().top,
      sourcesTop: document.querySelector("#rel-sources")!.getBoundingClientRect().top,
    }));
    expect(layout.scrollWidth - layout.width, viewport.label + " must not overflow horizontally").toBeLessThanOrEqual(2);
    if (viewport.width >= 1024) expect(layout.navigationWidth).toBeGreaterThanOrEqual(180);
    else {
      expect(layout.navigationWidth).toBeLessThanOrEqual(70);
      expect(layout.geoTop).toBeLessThan(layout.sourcesTop);
      expect(layout.mapTop).toBeLessThan(layout.sourcesTop);
    }
    expect(layout.dashboardWidth).toBeGreaterThan(200);
    const screenshotPath = testInfo.outputPath("reliability-" + viewport.label + ".png");
    await page.screenshot({ path: screenshotPath, fullPage: true, animations: "disabled" });
    await testInfo.attach("reliability-" + viewport.label + ".png", {
      path: screenshotPath, contentType: "image/png",
    });
  }
  await panel.locator(".rel-tab--failed").click();
  await expect(panel.locator(".rel-tab--failed")).toHaveAttribute("aria-pressed", "true");
  await panel.locator(".rel-tab--all").click();
  await expect(panel.locator(".rel-tab--all")).toHaveAttribute("aria-pressed", "true");
});

test("Reliability summary server-side warm p95 <= 500ms", async ({ request }, testInfo) => {
  test.setTimeout(120_000);
  // Measure the authenticated API independently of browser navigation. The
  // preceding test covers the real browser login and all responsive layouts.
  const password = process.env.E2E_PASSWORD;
  if (!password) throw new Error("E2E_PASSWORD is required for production acceptance");
  const login = await request.post("/api/auth/login", {
    data: { username: process.env.E2E_USERNAME || "reader", password }, timeout: 30_000,
  });
  expect(login.status()).toBe(200);
  await maskSessionCookies(request);
  const timings: number[] = [];
  const networkTimes: number[] = [];
  let firstServerMs: number | null = null;
  for (let i = 0; i < 22; i += 1) {
    const start = performance.now();
    let response: APIResponse;
    try {
      response = await authenticatedGet(request, "/api/reliability/summary");
    } catch {
      await writeFile(testInfo.outputPath("reliability-p95-incomplete.json"), JSON.stringify({
        failedSampleIndex: i, firstServerMs, completedWarmSamples: timings,
        completedNetworkSamples: networkTimes, acceptancePassed: false,
      }, null, 2));
      throw new Error("Reliability public request timed out or failed at sample " + i);
    }
    const networkMs = performance.now() - start;
    expect(response.status()).toBe(200);
    const header = response.headers()["server-timing"] || "";
    const match = header.match(/(?:^|,)\s*reliability;dur=([\d.]+)/);
    expect(match, "Production response must expose backend handler latency").not.toBeNull();
    const serverMs = Number(match![1]);
    expect(Number.isFinite(serverMs)).toBe(true);
    console.log("Reliability sample " + i + ": server=" + serverMs + "ms; network=" + Math.round(networkMs) + "ms");
    if (i === 0) firstServerMs = serverMs;
    else {
      timings.push(serverMs);
      networkTimes.push(networkMs);
    }
  }
  const sorted = timings.slice().sort((a, b) => a - b);
  const result = {
    sampleCount: timings.length,
    firstServerMs,
    warmP50ServerMs: sorted[Math.floor((sorted.length - 1) / 2)],
    warmP95ServerMs: percentile95(timings),
    overallP95ServerMs: percentile95([firstServerMs!, ...timings]),
    warmMaxServerMs: Math.max(...timings),
    warmP95NetworkMs: percentile95(networkTimes),
    sloThresholdServerMs: 500,
    note: "Server-Timing includes production handler and database. Warm samples exclude the first request; network RTT is separate.",
  };
  console.log("Reliability production warm p95 (server ms): " + result.warmP95ServerMs
    + "; cold server ms: " + result.firstServerMs
    + "; warm network p95 ms: " + result.warmP95NetworkMs);
  const metricsPath = testInfo.outputPath("reliability-p95-production.json");
  await writeFile(metricsPath, JSON.stringify(result, null, 2));
  await testInfo.attach("reliability-p95-production.json", { path: metricsPath, contentType: "application/json" });
  expect(result.warmP95ServerMs).toBeLessThanOrEqual(500);
  expect(result.overallP95ServerMs).toBeLessThanOrEqual(500);
});
