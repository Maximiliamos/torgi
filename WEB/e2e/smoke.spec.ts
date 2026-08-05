import { expect, test } from "@playwright/test";

test("authenticated list search detail and API failure smoke", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await expect(page.locator("main")).toBeVisible();
  const loginField = page.locator('input[autocomplete="username"]');
  const workspace = page.locator(".workspace");
  await expect(loginField.or(workspace)).toBeVisible({ timeout: 30_000 });
  if (await loginField.isVisible()) {
    await loginField.fill(process.env.E2E_USERNAME || "reader");
    await page
      .locator('input[autocomplete="current-password"]')
      .fill(process.env.E2E_PASSWORD || "");
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
  await page.route("**/api/map/lots**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (/\/api\/map\/lots\/\d+$/.test(pathname)) {
      const id = Number(pathname.split("/").at(-1));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(coincidentLots.find((lot) => lot.id === id)),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { ETag: '"map-e2e"' },
      body: JSON.stringify({
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
    });
  });
  await page.route("**/api/lots/7001/review-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ lot_id: 7001, status: "approved" }),
    }),
  );
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
  const mapFrame = page
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
    await mapFrame.evaluate(() =>
      (
        window as unknown as {
          bankrotaiDebug: { selectLot: (id: number) => void };
        }
      ).bankrotaiDebug.selectLot(7001),
    );
    await expect(page.getByLabel("Карточка выбранного лота")).toBeVisible();
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
