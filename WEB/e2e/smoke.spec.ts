import { expect, test } from "@playwright/test";

test("authenticated list search detail and API failure smoke", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("main")).toBeVisible();
  const loginField = page.locator('input[autocomplete="username"]');
  const workspace = page.locator(".workspace");
  await expect(loginField.or(workspace)).toBeVisible({ timeout: 30_000 });
  if (await loginField.isVisible()) {
    await loginField.fill(process.env.E2E_USERNAME || "reader");
    await page.locator('input[autocomplete="current-password"]').fill(process.env.E2E_PASSWORD || "");
    await page.getByRole("button", { name: "Войти" }).click();
    await expect(workspace).toBeVisible({ timeout: 30_000 });
  }
  const search = page.locator(".searchBox input");
  await search.fill("земля");
  await page.waitForTimeout(350);
  const firstRow = page.locator(".lotRow").first();
  if (await firstRow.count()) {
    await firstRow.click();
    await expect(page.locator(".detailPanel")).toBeVisible();
    await page.locator(".detailPanel .iconButton").click();
  }
  await page.getByRole("button", { name: "Поиск", exact: true }).click();
  await expect(page.getByRole("button", { name: "ГИС Торги" })).toBeVisible();
  const region = page.getByLabel("Регион");
  await expect(region).toHaveAttribute("list", "auction-regions");
  await region.fill("76");
  await expect(page.getByLabel("Категория")).toHaveValue("Вся недвижимость");
  await expect(page.getByLabel("Категория")).toHaveAttribute("readonly", "");
  const previewImage = "data:image/svg+xml;charset=UTF-8," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#292823"/><stop offset="1" stop-color="#898277"/></linearGradient></defs><rect width="800" height="500" fill="url(#g)"/><path d="M0 350h800M80 90v260m210-280v280m300-270v270" stroke="#b9b1a5" stroke-width="12" opacity=".55"/><text x="400" y="260" text-anchor="middle" fill="white" font-family="Arial" font-size="34">Фотография лота</text></svg>');
  await page.route("**/api/map/lots**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total: 1, items: [{
    id: 7001, external_id: "map-e2e", title: "Нежилое помещение площадью 65,4 кв.м с кадастровым номером 76:17:010101:20019",
    description: "Нежилое помещение, расположенное по адресу: Ярославская область, Ярославский район, посёлок Карачиха.",
    address: "Ярославская область, Ярославский район, п. Карачиха, ул. Садовая, д. 26", cadastral_number: "76:17:010101:20019",
    category: "commercial_room", region: "Ярославская область", status: "active", is_archived: false, review_status: null,
    current_price: 693109, lat: 57.6261, lon: 39.8845, geometry: null, confidence: "high", source: "tbankrot.ru", source_name: "TBANKROT.RU",
    source_url: "https://example.test/source", gis_torgi_url: "https://example.test/gis", etp_url: null, torgi_russia_url: "https://example.test/russia",
    image_url: previewImage, image_urls: [previewImage], procedure_number: "PROC-7001", application_deadline: "20.08.2026 12:00", auction_at: "25.08.2026 10:00"
  }] }) }));
  await page.getByRole("button", { name: "Карта", exact: true }).click();
  const mapFrameElement = page.locator('iframe[title="Яндекс.Карта лотов"]');
  await expect(mapFrameElement).toBeVisible();
  await expect(mapFrameElement).toHaveAttribute("sandbox", "allow-scripts");
  await expect(page.getByRole("heading", { name: "Кадастровый поиск" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Поиск всех лотов РФ" })).toBeVisible();
  await expect(page.frameLocator('iframe[title="Яндекс.Карта лотов"]').locator("#hint")).toBeHidden({ timeout: 30_000 });
  await expect(page.frameLocator('iframe[title="Яндекс.Карта лотов"]').locator("ymaps").first()).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(2_000);
  await page.screenshot({ path: "test-results/map-desktop-controls.png", fullPage: true });
  const mapFrame = page.frames().find((frame) => frame !== page.mainFrame() && frame.url() === "about:srcdoc");
  const lotIds = mapFrame ? await mapFrame.evaluate(() => (window as unknown as { bankrotaiLotIds?: number[] }).bankrotaiLotIds || []) : [];
  if (mapFrame && lotIds.length) {
    await mapFrame.evaluate((lotId) => (window as unknown as { bankrotaiSelectLot: (id: number) => void }).bankrotaiSelectLot(lotId), lotIds[0]);
    await expect(page.getByLabel("Карточка выбранного лота")).toBeVisible();
    await expect(page.getByText("Оценка лота")).toBeVisible();
    await expect(page.frameLocator('iframe[title="Яндекс.Карта лотов"]').locator("#hint")).toBeHidden({ timeout: 30_000 });
    await page.waitForTimeout(2_000);
  }
  await page.screenshot({ path: "test-results/map-desktop-preview.png", fullPage: true });
  await page.getByRole("button", { name: "Сделка", exact: true }).click();
  await expect(page.getByText("Работа со сделкой")).toBeVisible();
  await page.getByRole("button", { name: "Надёжность", exact: true }).click();
  await expect(page.getByText("Состояние источников")).toBeVisible();
  await page.getByRole("button", { name: "Реестр", exact: true }).click();
  await expect(page.locator(".workspace")).toBeVisible();
  await page.route("**/api/lots**", route => route.abort());
  await page.locator(".topBar .primaryButton").click();
  await expect(page.locator(".errorBox")).toBeVisible();
});
