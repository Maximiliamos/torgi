import { expect, test, type Page, type TestInfo } from "@playwright/test";

test.skip(
  process.env.E2E_PRODUCTION_AUDIT !== "1",
  "The read-only production suite runs only in the dedicated reliability job.",
);

type BrowserFailure = { kind: string; value: string };

function observeBrowserFailures(page: Page, failures: BrowserFailure[]) {
  page.on("console", (message) => {
    if (message.type() === "error") failures.push({ kind: "console.error", value: message.text() });
  });
  page.on("pageerror", (error) => failures.push({ kind: "pageerror", value: error.message }));
  page.on("requestfailed", (request) => {
    const reason = request.failure()?.errorText || "unknown";
    if (!reason.includes("ERR_ABORTED") && !reason.includes("NS_BINDING_ABORTED")) {
      failures.push({ kind: "requestfailed", value: `${request.method()} ${request.url()} ${reason}` });
    }
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      failures.push({ kind: `http-${response.status()}`, value: response.url() });
    }
  });
}

async function login(page: Page) {
  const username = process.env.E2E_USERNAME || "reader";
  const password = process.env.E2E_PASSWORD;
  if (!password) throw new Error("E2E_PASSWORD is required for the production audit");

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible({ timeout: 30_000 });

  await page.getByLabel("Логин").fill(username);
  await page.getByLabel("Пароль").fill(`wrong-${Date.now()}`);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.locator(".errorBox")).toContainText(/Invalid username or password/i);

  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
  try {
    await expect(page.locator(".workspace")).toBeVisible({ timeout: 40_000 });
  } finally {
    const passwordField = page.locator('input[autocomplete="current-password"]');
    if (await passwordField.isVisible()) {
      await passwordField.fill("");
    }
  }
}

async function browserJson<T>(page: Page, path: string): Promise<{ status: number; body: T }> {
  return page.evaluate(async (url) => {
    const response = await fetch(url, { credentials: "same-origin" });
    return { status: response.status, body: await response.json() as T };
  }, path);
}

async function attachFailures(testInfo: TestInfo, failures: BrowserFailure[]) {
  if (!failures.length) return;
  await testInfo.attach("browser-failures.json", {
    body: Buffer.from(JSON.stringify(failures, null, 2)),
    contentType: "application/json",
  });
}

test("real production auth, registry, sources, GEO, images and source links", async ({ page, context }, testInfo) => {
  test.setTimeout(360_000);
  const failures: BrowserFailure[] = [];
  observeBrowserFailures(page, failures);

  await login(page);

  const session = (await context.cookies()).find((cookie) => cookie.name === "bankrotai_session");
  expect(session).toMatchObject({ httpOnly: true, secure: true, sameSite: "Strict" });
  const me = await browserJson<{ username: string; role: string }>(page, "/api/auth/me");
  expect(me.status).toBe(200);
  expect(me.body.username).toBe(process.env.E2E_USERNAME || "reader");

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: /Выйти:/ })).toBeVisible({ timeout: 40_000 });
  const secondTab = await context.newPage();
  await secondTab.goto("/", { waitUntil: "domcontentloaded" });
  await expect(secondTab.getByRole("button", { name: /Выйти:/ })).toBeVisible({ timeout: 40_000 });
  const secondTabMe = await browserJson<{ username: string; role: string }>(secondTab, "/api/auth/me");
  expect(secondTabMe.status).toBe(200);
  expect(secondTabMe.body.username).toBe(process.env.E2E_USERNAME || "reader");
  await secondTab.close();

  const firstRow = page.locator(".lotRow").first();
  await expect(firstRow).toBeVisible({ timeout: 40_000 });
  await firstRow.click();
  await expect(page.locator(".detailPanel")).toBeVisible();
  const registrySource = page.locator(".detailPanel a", { hasText: "Источник" });
  if (await registrySource.count()) {
    expect(new URL(await registrySource.getAttribute("href") || "").protocol).toMatch(/^https?:$/);
  }
  await page.locator(".detailPanel .iconButton").click();

  const search = page.locator(".searchBox input");
  const missingQuery = `no-result-${Date.now()}`;
  const emptySearchResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/lots" && url.searchParams.get("search") === missingQuery;
  });
  await search.fill(missingQuery);
  expect((await emptySearchResponse).status()).toBe(200);
  await expect(page.getByText("Загрузка лотов", { exact: true })).toBeHidden({ timeout: 30_000 });
  await expect(page.locator(".lotRow")).toHaveCount(0, { timeout: 30_000 });
  await expect(page.getByText("0 найдено", { exact: true })).toBeVisible();
  await search.fill("");
  await page.getByLabel("Сортировка").selectOption("price_asc");
  const categoryRequest = page.waitForResponse((response) =>
    response.url().includes("/api/lots?") && response.url().includes("categories=land"),
  );
  await page.getByRole("button", { name: "Земля", exact: true }).click();
  expect((await categoryRequest).status()).toBe(200);
  await page.getByRole("button", { name: "Земля", exact: true }).click();
  const regionRequest = page.waitForResponse((response) =>
    response.url().includes("/api/lots?") && response.url().includes("city_slug=76"),
  );
  await page.getByLabel("Регион реестра").selectOption("76");
  expect((await regionRequest).status()).toBe(200);

  const invalidLot = await browserJson<{ detail: string }>(page, "/api/lots/2147483647");
  expect(invalidLot.status).toBe(404);

  await page.getByRole("button", { name: "Поиск", exact: true }).click();
  const searchView = page.getByRole("region", { name: "Онлайн-поиск" });
  await searchView.getByLabel("Регион онлайн-поиска").fill("Ярославская область");
  for (const source of ["ГИС Торги", "Т‑Банкрот", "РАД / ЛОТ‑ОНЛАЙН"]) {
    await searchView.getByRole("button", { name: source, exact: true }).click();
    await searchView.getByRole("button", { name: "Найти онлайн" }).click();
    const card = searchView.locator(".onlineCard").first();
    await expect(card, `${source} must return actual visible lots`).toBeVisible({ timeout: 90_000 });
    const link = card.getByRole("link", { name: /Источник/ });
    await expect(link).toBeVisible();
    expect(new URL(await link.getAttribute("href") || "").protocol).toMatch(/^https?:$/);
  }

  const mapResponse = await browserJson<{
    items: Array<{ id: number; lat: number; lon: number }>;
    truncated: boolean;
  }>(page, "/api/map/lots?limit=100");
  expect(mapResponse.status).toBe(200);
  expect(mapResponse.body.items.length).toBeGreaterThan(0);
  expect(mapResponse.body.items.every((item) => Number.isFinite(item.lat) && Number.isFinite(item.lon))).toBe(true);

  const detailCandidates: Array<{ status: number; body: {
      id: number;
      source_url: string | null;
      gis_torgi_url: string | null;
      etp_url: string | null;
      image_url: string | null;
      image_urls: string[];
  } }> = [];
  for (const item of mapResponse.body.items.slice(0, 20)) {
    detailCandidates.push(await browserJson<{
      id: number;
      source_url: string | null;
      gis_torgi_url: string | null;
      etp_url: string | null;
      image_url: string | null;
      image_urls: string[];
    }>(page, `/api/map/lots/${item.id}`));
  }
  expect(detailCandidates.some(({ body }) => [body.source_url, body.gis_torgi_url, body.etp_url]
    .some((url) => url && /^https?:\/\//.test(url)))).toBe(true);
  expect(detailCandidates.some(({ body }) => body.image_url || body.image_urls.length > 0),
    "At least one sampled production map lot must expose a parsed image").toBe(true);

  const etagResult = await page.evaluate(async () => {
    const first = await fetch("/api/map/lots?limit=25", { credentials: "same-origin" });
    const etag = first.headers.get("etag");
    await first.arrayBuffer();
    const second = await fetch("/api/map/lots?limit=25", {
      credentials: "same-origin",
      headers: etag ? { "If-None-Match": etag } : {},
    });
    return { etag, status: second.status };
  });
  expect(etagResult.etag).toBeTruthy();
  expect(etagResult.status).toBe(304);

  const cadastre = await browserJson<Record<string, unknown>>(
    page,
    "/api/cadastre/search?query=76%3A17%3A010101%3A20019",
  );
  expect(cadastre.status).toBe(200);
  expect(cadastre.body).toHaveProperty("cadastral_number");

  await page.getByRole("button", { name: "Карта", exact: true }).click();
  const mapFrame = page.locator('iframe[title="Яндекс.Карта лотов"]');
  await expect(mapFrame).toBeVisible({ timeout: 40_000 });
  await expect(page.getByLabel("Состояние карты")).toContainText("Система готова", { timeout: 60_000 });
  await page.evaluate(() => {
    (window as unknown as { auditMapContentWindow?: Window | null }).auditMapContentWindow =
      document.querySelector<HTMLIFrameElement>('iframe[title="Яндекс.Карта лотов"]')?.contentWindow;
  });
  await page.getByRole("button", { name: "Реестр", exact: true }).click();
  await page.getByRole("button", { name: "Карта", exact: true }).click();
  expect(await page.evaluate(() =>
    (window as unknown as { auditMapContentWindow?: Window | null }).auditMapContentWindow ===
      document.querySelector<HTMLIFrameElement>('iframe[title="Яндекс.Карта лотов"]')?.contentWindow,
  )).toBe(true);

  await page.getByRole("button", { name: "Сделка", exact: true }).click();
  await expect(page.getByText("Работа со сделкой")).toBeVisible();
  await page.getByRole("button", { name: "Надёжность", exact: true }).click();
  await expect(page.getByText("Состояние источников")).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: new RegExp(`Выйти: ${process.env.E2E_USERNAME || "reader"}`) }).click();
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible();
  await context.addCookies([{
    name: "bankrotai_session",
    value: "invalid.audit.session",
    domain: "dezster.ru",
    path: "/",
    secure: true,
    httpOnly: true,
    sameSite: "Strict",
  }]);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Вход" })).toBeVisible();

  await login(page);
  const repeatedMe = await browserJson<{ username: string; role: string }>(page, "/api/auth/me");
  expect(repeatedMe.status).toBe(200);
  expect(repeatedMe.body.username).toBe(process.env.E2E_USERNAME || "reader");

  await attachFailures(testInfo, failures);
  expect(failures, "No unknown browser/runtime/network/5xx failures are allowed").toEqual([]);
});
