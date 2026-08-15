import { expect, test, type Page } from "@playwright/test";

async function ensureAuthenticated(page: Page) {
  const loginField = page.locator('input[autocomplete="username"]');
  const passwordField = page.locator('input[autocomplete="current-password"]');
  const workspace = page.locator(".workspace");

  for (let attempt = 0; attempt < 3; attempt += 1) {
    await expect(loginField.or(workspace)).toBeVisible({ timeout: 30_000 });
    if (await workspace.isVisible()) {
      return;
    }

    await loginField.fill(process.env.E2E_USERNAME || "reader");
    await passwordField.fill(process.env.E2E_PASSWORD || "");
    const loginResponse = page
      .waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname === "/api/auth/login",
        { timeout: 20_000 },
      )
      .catch(() => null);
    await page.getByRole("button", { name: "Войти" }).click();
    const response = await loginResponse;
    if (await passwordField.isVisible()) {
      await passwordField.fill("");
    }
    if (response?.status() === 401) {
      throw new Error("Public smoke credentials were rejected by the API");
    }

    try {
      await expect(workspace).toBeVisible({ timeout: 25_000 });
      return;
    } catch (error) {
      if (attempt === 2) {
        throw error;
      }
      await page.waitForTimeout(1_000);
      await page.reload({ waitUntil: "domcontentloaded" });
    }
  }

  throw new Error("Public smoke could not establish an authenticated session");
}

test("authenticated list search detail and API failure smoke", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await expect(page.locator("main")).toBeVisible();
  await ensureAuthenticated(page);
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
  const searchView = page.getByRole("region", { name: "Онлайн-поиск" });
  const region = searchView.getByLabel("Регион онлайн-поиска");
  await expect(region).toHaveAttribute("list", "auction-regions");
  await region.fill("76");
  await expect(searchView.getByLabel("Категория")).toHaveValue("Вся недвижимость");
  await expect(searchView.getByLabel("Категория")).toHaveAttribute("readonly", "");
  const previewImage =
    "data:image/svg+xml;charset=UTF-8," +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#292823"/><stop offset="1" stop-color="#898277"/></linearGradient></defs><rect width="800" height="500" fill="url(#g)"/><path d="M0 350h800M80 90v260m210-280v280m300-270v270" stroke="#b9b1a5" stroke-width="12" opacity=".55"/><text x="400" y="260" text-anchor="middle" fill="white" font-family="Arial" font-size="34">Фотография лота</text></svg>',
    );
  const mapLot = (
    id: number,
    source: string,
    title: string,
    price: number,
  ) => ({
    id,
    external_id: `map-e2e-${id}`,
    title,
    description:
      "Нежилое помещение, расположенное по адресу: Ярославская область, Ярославский район, посёлок Карачиха.",
    address:
      "Ярославская область, Ярославский район, п. Карачиха, ул. Садовая, д. 26",
    cadastral_number: "76:17:010101:20019",
    category: "commercial_room",
    region: "Ярославская область",
    status: "active",
    is_archived: false,
    review_status: null,
    current_price: price,
    lat: 57.6261,
    lon: 39.8845,
    geometry: null,
    confidence: "high",
    source,
    source_name: source.toUpperCase(),
    source_url: "https://example.test/source",
    gis_torgi_url: "https://example.test/gis",
    etp_url: null,
    torgi_russia_url: "https://example.test/russia",
    image_url: previewImage,
    image_urls: [previewImage],
    procedure_number: `PROC-${id}`,
    application_deadline: "20.08.2026 12:00",
    auction_at: "25.08.2026 10:00",
    sources: [
      {
        processed_lot_id: id,
        source_system: source,
        external_id: `map-e2e-${id}`,
        title,
        price,
        url: "https://example.test/source",
        is_primary: true,
      },
    ],
  });
  const coincidentLots = [
    mapLot(
      7001,
      "tbankrot.ru",
      "Нежилое помещение площадью 65,4 кв.м с кадастровым номером 76:17:010101:20019",
      693109,
    ),
    mapLot(
      7002,
      "torgi.gov.ru",
      "Земельный участок в Ярославском районе",
      820000,
    ),
    mapLot(7003, "lot-online.ru", "Здание с земельным участком", 1250000),
  ];
  await page.route(/\/api\/map\/lots(?:\?.*)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { ETag: '"map-e2e"' },
      body: JSON.stringify({
        returned: 3,
        limit: 3,
        truncated: true,
        total: 10,
        mapped_total: 3,
        without_coordinates: 7,
        updated_at: new Date().toISOString(),
        timings: { server_ms: 12 },
        items: coincidentLots.map((lot) => ({
          id: lot.id,
          title: lot.title,
          address: lot.address,
          current_price: lot.current_price,
          status: lot.status,
          is_archived: lot.is_archived,
          review_status: lot.review_status,
          lat: lot.lat,
          lon: lot.lon,
        })),
      }),
    }),
  );
  await page.route(/\/api\/map\/lots\/(\d+)$/, async (route) => {
    const id = Number(new URL(route.request().url()).pathname.split("/").at(-1));
    const lot = coincidentLots.find((item) => item.id === id);
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    return lot
      ? route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(lot),
        })
      : route.fulfill({ status: 404, body: "not found" });
  });
  await page.route("**/api/lots/7001/review-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ lot_id: 7001, status: "approved" }),
    }),
  );
  // The application preloads map totals during its initial bootstrap. Reload
  // after installing the map fixtures so cached production totals cannot race
  // with the deterministic smoke responses.
  await page.reload();
  await ensureAuthenticated(page);
  await page.getByRole("button", { name: "Карта", exact: true }).click();
  const mapFrameElement = page.locator('iframe[title="Яндекс.Карта лотов"]');
  await expect(mapFrameElement).toBeVisible();
  await expect(mapFrameElement).toHaveAttribute("sandbox", "allow-scripts");
  await expect(
    page.getByRole("heading", { name: "Кадастровый поиск" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Поиск всех лотов РФ" }),
  ).toBeVisible();
  await expect(page.getByLabel("Состояние карты")).toContainText(
    "10 объектов · 3 на карте · 7 без координат",
  );
  await expect(page.getByLabel("Состояние карты")).toContainText(
    "Система готова",
  );
  await expect(page.locator(".mapViewportWarning")).toContainText(
    "Показаны первые 3. Приблизьте карту",
  );
  await expect(
    page.frameLocator('iframe[title="Яндекс.Карта лотов"]').locator("#hint"),
  ).toBeHidden({ timeout: 30_000 });
  await expect(
    page
      .frameLocator('iframe[title="Яндекс.Карта лотов"]')
      .locator("ymaps")
      .first(),
  ).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(2_000);
  await page.screenshot({
    path: "test-results/map-desktop-controls.png",
    fullPage: true,
  });
  let mapFrame = page
    .frames()
    .find(
      (frame) => frame !== page.mainFrame() && frame.url() === "about:srcdoc",
    );
  if (mapFrame) {
    await mapFrame.evaluate(() =>
      (
        window as unknown as {
          bankrotaiDebug: {
            setViewport: (center: number[], zoom: number) => void;
          };
        }
      ).bankrotaiDebug.setViewport([58.1, 40.2], 12),
    );
    const viewportBefore = await mapFrame.evaluate(() =>
      (
        window as unknown as {
          bankrotaiDebug: {
            getViewport: () => {
              center: number[];
              zoom: number;
              instanceId: string;
            };
          };
        }
      ).bankrotaiDebug.getViewport(),
    );
    await page.evaluate(() => {
      (
        window as unknown as { mapContentWindow?: Window | null }
      ).mapContentWindow = document.querySelector<HTMLIFrameElement>(
        'iframe[title="Яндекс.Карта лотов"]',
      )?.contentWindow;
    });
    mapFrame = page
      .frames()
      .find(
        (frame) => frame !== page.mainFrame() && frame.url() === "about:srcdoc",
      );
    await mapFrame.evaluate(() =>
      (
        window as unknown as {
          bankrotaiDebug: { clickCoincident: (ids: number[]) => void };
        }
      ).bankrotaiDebug.clickCoincident([7001, 7002, 7003]),
    );
    const coincidentPanel = page.getByLabel("Лоты в выбранной точке");
    await expect(coincidentPanel).toBeVisible();
    for (const [id, title] of [
      [7002, "Земельный участок в Ярославском районе"],
      [7003, "Здание с земельным участком"],
      [7001, "Нежилое помещение площадью 65,4 кв.м"],
    ] as const) {
      await coincidentPanel.locator(`[data-lot-id="${id}"]`).click();
      await expect(page.getByLabel("Карточка выбранного лота")).toContainText(title, { timeout: 1_000 });
    }
    try {
      await mapFrame.evaluate(() =>
        (
          window as unknown as {
            bankrotaiDebug: { clickObject: (id: number) => void };
          }
        ).bankrotaiDebug.clickObject(7001),
      );
    } catch {
      // Yandex can recreate its frame immediately after dispatching the click.
    }
    await expect(page.getByLabel("Карточка выбранного лота")).toBeVisible({
      timeout: 1_000,
    });
    await expect(page.getByText("Загрузка полной карточки…")).toBeVisible();
    mapFrame = page
      .frames()
      .find(
        (frame) => frame !== page.mainFrame() && frame.url() === "about:srcdoc",
      );
    expect(mapFrame).toBeTruthy();
    await expect(page.getByText("Оценка лота")).toBeVisible();
    await expect(page.getByRole("button", { name: "Источник" })).toBeVisible();
    await expect(page.getByRole("button", { name: "ГИС Торги" })).toBeVisible();
    await expect(page.getByRole("button", { name: "ЭТП" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Торги РФ" })).toBeVisible();
    const viewportAfterSelect = await mapFrame.evaluate(() =>
      (
        window as unknown as {
          bankrotaiDebug: {
            getViewport: () => {
              center: number[];
              zoom: number;
              instanceId: string;
            };
          };
        }
      ).bankrotaiDebug.getViewport(),
    );
    expect(viewportAfterSelect).toEqual(viewportBefore);
    expect(
      await page.evaluate(
        () =>
          (window as unknown as { mapContentWindow?: Window | null })
            .mapContentWindow ===
          document.querySelector<HTMLIFrameElement>(
            'iframe[title="Яндекс.Карта лотов"]',
          )?.contentWindow,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Интересен", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Интересен", exact: true }),
    ).toHaveClass(/active/);
    await expect
      .poll(() =>
        mapFrame.evaluate(() =>
          (
            window as unknown as {
              bankrotaiDebug: { getLotReview: (id: number) => string | null };
            }
          ).bankrotaiDebug.getLotReview(7001),
        ),
      )
      .toBe("approved");
    const viewportAfterReview = await mapFrame.evaluate(() =>
      (
        window as unknown as {
          bankrotaiDebug: {
            getViewport: () => {
              center: number[];
              zoom: number;
              instanceId: string;
            };
          };
        }
      ).bankrotaiDebug.getViewport(),
    );
    expect(viewportAfterReview).toEqual(viewportBefore);
    expect(
      await page.evaluate(
        () =>
          (window as unknown as { mapContentWindow?: Window | null })
            .mapContentWindow ===
          document.querySelector<HTMLIFrameElement>(
            'iframe[title="Яндекс.Карта лотов"]',
          )?.contentWindow,
      ),
    ).toBe(true);
  }
  await page.screenshot({
    path: "test-results/map-desktop-preview.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Сделка", exact: true }).click();
  await expect(page.getByText("Работа со сделкой")).toBeVisible();
  await page.getByRole("button", { name: "Надёжность", exact: true }).click();
  await expect(page.getByText("Состояние источников")).toBeVisible();
  await page.getByRole("button", { name: "Реестр", exact: true }).click();
  await expect(page.locator(".workspace")).toBeVisible();
  await page.route("**/api/lots**", (route) => route.abort());
  await page.locator(".pageHeader .primaryButton").click();
  await expect(page.locator(".errorBox")).toBeVisible();
});
