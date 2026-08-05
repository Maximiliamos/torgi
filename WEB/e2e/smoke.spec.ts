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
  await page.route("**/api/lots**", route => route.abort());
  await page.locator(".topBar .primaryButton").click();
  await expect(page.locator(".errorBox")).toBeVisible();
});
