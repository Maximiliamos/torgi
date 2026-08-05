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
  await page.getByRole("button", { name: "Карта", exact: true }).click();
  await expect(page.locator('iframe[title="Яндекс.Карта лотов"]')).toBeVisible();
  await expect(page.frameLocator('iframe[title="Яндекс.Карта лотов"]').locator("#hint")).toBeHidden({ timeout: 30_000 });
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
