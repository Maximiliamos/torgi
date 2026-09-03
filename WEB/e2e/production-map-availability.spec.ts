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
    const response = await page.context().request.get(
      `/api/map/lots?limit=250&west=${west}&south=${south}&east=${east}&north=${north}`,
      { headers: { "Cache-Control": "no-cache" }, timeout: 30_000 },
    );
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
